import asyncio
import time
from datetime import datetime, timezone

from .coverage import select_keyframes
from .models import CoverageDecision, CoverageManifest, ReviewItem
from .scene_reconciliation import plan_scene_books
from .sweeps import StoreError, SweepStore
from .vision import SHARED_POSTWORK_DEADLINE_S


def _detail(exc) -> str:
    detail = getattr(exc, "detail", None)
    if detail is None:
        detail = str(exc)
    return str(detail)[:200]


class PostSweepProcessor:
    def __init__(
        self,
        *,
        store: SweepStore,
        vision,
        reading,
        pricing,
        reader,
        pricer,
        resolver=None,
        api_key=None,
        client_factory=None,
        pricing_client_factory=None,
        reader_provider=None,
        pricing_provider=None,
        tracking_contract=None,
        clock=time.monotonic,
        wallclock=None,
        deadline_s: float = SHARED_POSTWORK_DEADLINE_S,
    ):
        self.store = store
        self.vision = vision
        self.reading = reading
        self.pricing = pricing
        self.reader = reader or (lambda: None)
        self.pricer = pricer
        self.resolver = resolver or (lambda: None)
        self.api_key = api_key or (lambda: None)
        self.client_factory = client_factory
        self.pricing_client_factory = pricing_client_factory or client_factory
        self.reader_provider = reader_provider
        self.pricing_provider = pricing_provider
        self.tracking_contract = tracking_contract
        self._clock = clock
        self._wallclock = wallclock or (lambda: datetime.now(timezone.utc))
        self._deadline_s = float(deadline_s)
        self._tasks: dict = {}

    def start(self, sweep_id: str) -> bool:
        task = self._tasks.get(sweep_id)
        if task is not None and not task.done():
            return False
        try:
            state = self.store.get(sweep_id)
        except StoreError:
            return False
        if (
            state.ended_at is None
            or state.status == "interrupted"
            or state.postwork_status != "not_started"
        ):
            return False
        self._tasks[sweep_id] = asyncio.ensure_future(self._run(sweep_id))
        return True

    async def drain(self, sweep_id: str) -> None:
        task = self._tasks.get(sweep_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def cancel(self, sweep_id: str) -> None:
        task = self._tasks.get(sweep_id)
        if task is not None and not task.done():
            task.cancel()

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            if task is not None and not task.done():
                task.cancel()
        pending = [t for t in self._tasks.values() if t is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _review(self, sweep_id: str, reason: str) -> None:
        try:
            self.store.append_reviews(
                sweep_id, [ReviewItem(ref_id="post_sweep", reason=reason)]
            )
        except StoreError:
            pass

    async def _bounded(self, sweep_id: str, deadline_mono: float, coro):
        remaining = deadline_mono - self._clock()
        for manager in (self.reading, self.pricing):
            if manager is None:
                continue
            try:
                remaining = min(
                    remaining, manager.remaining_seconds(self.store, sweep_id)
                )
            except StoreError:
                remaining = 0.0
        return await asyncio.wait_for(coro, timeout=max(0.0, remaining))

    def _stage_error(self, sweep_id: str, stage: str, exc) -> bool:
        if isinstance(exc, StoreError) and exc.status_code in (409, 429):
            self._review(
                sweep_id, f"post-sweep {stage} skipped: {_detail(exc)}"
            )
            return False
        self._review(sweep_id, f"post-sweep {stage} failed: {_detail(exc)}")
        return True

    async def _run(self, sweep_id: str) -> None:
        timings = {}
        outcome = "completed"
        work_started = self._clock()
        try:
            if not self.store.postwork_begin(sweep_id):
                return
            elapsed = 0.0
            state = self.store.get(sweep_id)
            if state.ended_at:
                try:
                    ended = datetime.fromisoformat(state.ended_at)
                    if ended.tzinfo is None:
                        ended = ended.replace(tzinfo=timezone.utc)
                    elapsed = max(
                        0.0, (self._wallclock() - ended).total_seconds()
                    )
                except (TypeError, ValueError):
                    elapsed = 0.0
            deadline_mono = work_started - elapsed + self._deadline_s
            try:
                for manager in (self.reading, self.pricing):
                    if manager is not None:
                        try:
                            await manager.deadline(self.store, sweep_id)
                        except StoreError:
                            pass
                outcome = await self._pipeline(sweep_id, deadline_mono, timings)
            except asyncio.CancelledError:
                outcome = "interrupted"
                raise
            except asyncio.TimeoutError:
                outcome = "timed_out"
            except Exception as exc:
                outcome = "failed"
                self._review(
                    sweep_id,
                    "post-sweep processing failed: " + _detail(exc),
                )
            timings["post_sweep_work_ms"] = round(
                (self._clock() - work_started) * 1000.0, 3
            )
            try:
                self.store.postwork_finish(sweep_id, outcome, timings)
            except StoreError:
                return
            if outcome != "interrupted":
                try:
                    self.store.publish_packet(sweep_id)
                except Exception as exc:
                    self._review(
                        sweep_id,
                        "post-sweep packet publication failed: " + _detail(exc),
                    )
                    try:
                        self.store.postwork_finish(sweep_id, "failed")
                    except StoreError:
                        pass
        finally:
            if outcome == "interrupted":
                timings.setdefault(
                    "post_sweep_work_ms",
                    round((self._clock() - work_started) * 1000.0, 3),
                )
                try:
                    self.store.postwork_finish(sweep_id, "interrupted", timings)
                except StoreError:
                    pass
            self._tasks.pop(sweep_id, None)

    def _build_manifest(self, state, call_budget: int):
        history: dict = {}
        for stage in state.vision_stages:
            history.setdefault(stage.frame_id, []).append(stage)
        unprocessed = [
            frame for frame in state.frames if frame.id not in history
        ]
        unprocessed_ids = {frame.id for frame in unprocessed}
        quality = {
            frame.id: frame.sharpness_score
            for frame in state.frames
            if frame.id in unprocessed_ids
        }
        selection = select_keyframes(
            saved_frames=list(unprocessed),
            quality=quality,
            observed_rows=[],
            budget=call_budget,
        )
        chosen = {entry.frame_id: entry for entry in selection.entries}
        entries = []
        for frame in state.frames:
            stages = history.get(frame.id)
            if stages is None:
                entries.append(chosen[frame.id])
                continue
            refs = [s.persisted_ref for s in stages if s.persisted_ref]
            success = next(
                (
                    s
                    for s in stages
                    if s.status in ("completed", "cached")
                ),
                None,
            )
            if success is not None:
                entries.append(
                    CoverageDecision(
                        frame_id=frame.id,
                        disposition="selected",
                        reason="prior analysis satisfied",
                        processing_status=success.status,
                        stage_refs=refs,
                    )
                )
                continue
            failed_stage = next(
                (s for s in reversed(stages) if s.status == "failed"),
                None,
            )
            stage = failed_stage if failed_stage is not None else stages[-1]
            if stage.status == "failed":
                entries.append(
                    CoverageDecision(
                        frame_id=frame.id,
                        disposition="unresolved",
                        reason=stage.error or "vision analysis failed",
                        processing_status="failed",
                        stage_refs=refs,
                    )
                )
            else:
                entries.append(
                    CoverageDecision(
                        frame_id=frame.id,
                        disposition="deferred",
                        reason=stage.error or "vision analysis skipped",
                        processing_status="skipped",
                        stage_refs=refs,
                    )
                )
        return CoverageManifest(
            entries=entries,
            gaps=list(selection.gaps),
            spatial_coverage_status="unresolved",
        )

    def _reconcile_entry(
        self, manifest, index: int, sweep_id: str, error
    ) -> None:
        entry = manifest.entries[index]
        stages = []
        try:
            state = self.store.get(sweep_id)
        except StoreError:
            state = None
        if state is not None:
            stages = [
                record
                for record in state.vision_stages
                if record.frame_id == entry.frame_id
            ]
        refs = list(entry.stage_refs)
        for record in stages:
            if record.persisted_ref and record.persisted_ref not in refs:
                refs.append(record.persisted_ref)
        update = {"stage_refs": refs}
        success = next(
            (s for s in stages if s.status in ("completed", "cached")), None
        )
        stage = stages[-1] if stages else None
        if success is not None:
            update["processing_status"] = success.status
        elif stage is not None and stage.status == "skipped":
            update["processing_status"] = "skipped"
            update["disposition"] = "deferred"
            update["reason"] = stage.error or "vision analysis skipped"
        elif stage is not None and stage.status == "failed":
            update["processing_status"] = "failed"
            update["disposition"] = "unresolved"
            update["reason"] = stage.error or "vision analysis failed"
        elif error is None:
            update["processing_status"] = "failed"
            update["disposition"] = "unresolved"
            update["reason"] = (
                "analysis returned without persisted stage evidence"
            )
        else:
            update["processing_status"] = "failed"
            update["disposition"] = "unresolved"
            update["reason"] = _detail(error)
        manifest.entries[index] = entry.model_copy(update=update)

    def _defer_selected(self, manifest, start: int, reason: str) -> None:
        for index in range(start, len(manifest.entries)):
            entry = manifest.entries[index]
            if entry.disposition == "selected" and entry.processing_status in (
                "pending",
                "running",
            ):
                manifest.entries[index] = entry.model_copy(
                    update={
                        "disposition": "deferred",
                        "reason": reason,
                        "processing_status": "skipped",
                    }
                )

    def _settle_manifest(self, sweep_id: str, manifest) -> None:
        if manifest is None:
            return
        entries = [
            entry.model_copy(update={"processing_status": "interrupted"})
            if entry.processing_status in ("pending", "running")
            else entry
            for entry in manifest.entries
        ]
        try:
            self.store.record_coverage(
                sweep_id, manifest.model_copy(update={"entries": entries})
            )
        except StoreError:
            pass

    async def _vision_stage(self, sweep_id: str, deadline_mono: float):
        reserve = float(
            getattr(self.vision, "downstream_reserve_s", 0.0) or 0.0
        )
        budget = self.vision.remaining_budget(sweep_id)
        vision_cutoff = min(
            deadline_mono - reserve,
            self._clock() + float(budget["seconds_left"]),
        )
        self.vision.set_postwork_deadline(sweep_id, vision_cutoff)
        try:
            await self._bounded(
                sweep_id, vision_cutoff, self.vision.drain_sweep(sweep_id)
            )
        except asyncio.TimeoutError:
            if self._clock() >= deadline_mono:
                raise
        budget = self.vision.remaining_budget(sweep_id)
        vision_cutoff = min(
            vision_cutoff, self._clock() + float(budget["seconds_left"])
        )
        self.vision.set_postwork_deadline(sweep_id, vision_cutoff)
        state = self.store.get(sweep_id)
        if not state.frames:
            self._review(
                sweep_id, "post-sweep vision skipped: no saved frames"
            )
            return None
        manifest = self._build_manifest(
            state,
            int(min(budget["calls_left"], budget["post_calls_left"])),
        )
        self.store.record_coverage(sweep_id, manifest)
        try:
            async with self.client_factory() as client:
                for index in range(len(manifest.entries)):
                    state_now = self.store.get(sweep_id)
                    if state_now.postwork_status != "running":
                        raise asyncio.CancelledError()
                    entry = manifest.entries[index]
                    if (
                        entry.disposition != "selected"
                        or entry.processing_status != "pending"
                    ):
                        continue
                    manifest.entries[index] = entry.model_copy(
                        update={"processing_status": "running"}
                    )
                    self.store.record_coverage(sweep_id, manifest)
                    error = None
                    try:
                        await self._bounded(
                            sweep_id,
                            vision_cutoff,
                            self.vision.analyze_now(
                                sweep_id, entry.frame_id, client=client
                            ),
                        )
                    except asyncio.TimeoutError:
                        if self._clock() >= deadline_mono:
                            raise
                        self._defer_selected(
                            manifest, index, "post-sweep vision cutoff reached"
                        )
                        self.store.record_coverage(sweep_id, manifest)
                        break
                    except asyncio.CancelledError:
                        raise
                    except StoreError as exc:
                        if exc.status_code == 429:
                            self._reconcile_entry(
                                manifest, index, sweep_id, exc
                            )
                            self._defer_selected(
                                manifest, index + 1, _detail(exc)
                            )
                            self.store.record_coverage(sweep_id, manifest)
                            break
                        error = exc
                    except Exception as exc:
                        error = exc
                    self._reconcile_entry(manifest, index, sweep_id, error)
                    self.store.record_coverage(sweep_id, manifest)
        except BaseException:
            self._settle_manifest(sweep_id, manifest)
            raise
        return manifest

    async def _pipeline(self, sweep_id: str, deadline_mono: float, timings: dict) -> str:
        failed = False
        started = self._clock()
        try:
            manifest = await self._vision_stage(sweep_id, deadline_mono)
            if manifest is not None:
                try:
                    manifest = (
                        self.store.get(sweep_id).coverage_manifest or manifest
                    )
                except StoreError:
                    pass
                self._review(
                    sweep_id,
                    "post-sweep coverage spatial extent unresolved",
                )
                for gap in manifest.gaps:
                    self._review(
                        sweep_id,
                        f"post-sweep coverage gap: {gap.reason} "
                        f"({len(gap.frame_ids)} frames)",
                    )
                for entry in manifest.entries:
                    if entry.processing_status == "failed":
                        self._review(
                            sweep_id,
                            f"post-sweep vision frame {entry.frame_id} failed: {entry.reason}",
                        )
                    elif entry.disposition in ("deferred", "unresolved"):
                        self._review(
                            sweep_id,
                            f"post-sweep vision frame {entry.frame_id} {entry.disposition}: {entry.reason}",
                        )
                    elif entry.processing_status == "interrupted":
                        self._review(
                            sweep_id,
                            f"post-sweep vision frame {entry.frame_id} interrupted",
                        )
            if self.tracking_contract and self.store.get(sweep_id).books:
                scene_plan = await self._bounded(
                    sweep_id, deadline_mono,
                    asyncio.to_thread(plan_scene_books, self.store, sweep_id, self.tracking_contract),
                )
                if self.store.get(sweep_id).postwork_status != "running":
                    raise asyncio.CancelledError()
                if scene_plan["supported_scenes"] == 1:
                    scene = scene_plan["scenes"][0]
                    try:
                        self.store.reconcile_book_tracks(
                            sweep_id, scene["book_ids"], scene["frame_id"],
                            scene["frame_ids"], scene_plan["unresolved_frames"],
                        )
                    except StoreError as exc:
                        failed = self._stage_error(sweep_id, "scene reconciliation", exc) or failed
                else:
                    self._review(sweep_id, "scene reconciliation unsupported; physical book count unverified")
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._review(
                sweep_id, "post-sweep vision exceeded the shared deadline"
            )
            raise
        except Exception as exc:
            failed = self._stage_error(sweep_id, "vision", exc) or failed
        finally:
            timings["post_sweep_vision_ms"] = round(
                (self._clock() - started) * 1000.0, 3
            )
        if not self.store.postwork_advance(sweep_id, "reading"):
            raise asyncio.CancelledError()
        state = self.store.get(sweep_id)
        if not state.books:
            self._review(
                sweep_id, "post-sweep reading skipped: no recorded book tracks"
            )
        else:
            impl = self.reader()
            if impl is None:
                self._review(
                    sweep_id, "post-sweep reading skipped: no reader configured"
                )
            else:
                started = self._clock()
                try:
                    async with self.client_factory() as client:
                        result = await self._bounded(
                            sweep_id,
                            deadline_mono,
                            impl(
                                self.store,
                                sweep_id,
                                client=client,
                                api_key=self.api_key(),
                                contract=self.reading.contract,
                                ref_ids=None,
                                manager=self.reading,
                                provider=self.reader_provider,
                            ),
                        )
                    if isinstance(result, dict) and result.get("skipped"):
                        self._review(
                            sweep_id,
                            "post-sweep reading skipped: no eligible book tracks",
                        )
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    self._review(
                        sweep_id,
                        "post-sweep reading exceeded the shared deadline",
                    )
                    raise
                except Exception as exc:
                    failed = (
                        self._stage_error(sweep_id, "reading", exc) or failed
                    )
                finally:
                    timings["book_identification_ms"] = round(
                        (self._clock() - started) * 1000.0, 3
                    )
        if not self.store.postwork_advance(sweep_id, "pricing"):
            raise asyncio.CancelledError()
        state = self.store.get(sweep_id)
        if not state.books:
            self._review(
                sweep_id, "post-sweep pricing skipped: no recorded book tracks"
            )
        else:
            started = self._clock()
            try:
                async with self.pricing_client_factory() as client:
                    await self._bounded(
                        sweep_id,
                        deadline_mono,
                        self.pricer(
                            self.store,
                            sweep_id,
                            client=client,
                            api_key=self.api_key(),
                            contract=self.pricing.contract,
                            book_ids=None,
                            manager=self.pricing,
                            provider=self.pricing_provider,
                            resolver=self.resolver(),
                        ),
                    )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                self._review(
                    sweep_id, "post-sweep pricing exceeded the shared deadline"
                )
                raise
            except Exception as exc:
                failed = self._stage_error(sweep_id, "pricing", exc) or failed
            finally:
                timings["pricing_ms"] = round(
                    (self._clock() - started) * 1000.0, 3
                )
        self.store.postwork_advance(sweep_id, "packet")
        return "failed" if failed else "completed"
