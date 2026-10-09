import asyncio
import hashlib
import json
import math
import time
import uuid
from pathlib import Path

import httpx

from preflight.vision import VisionError, observe_jpeg, proposal_boxes

from .models import VisionObservation, VisionStageRecord
from .sweeps import StoreError, SweepStore
from .tracking import commit_observation

SHARED_POSTWORK_DEADLINE_S = 240.0


def load_contract(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def vision_cache_version(contract: dict) -> str:
    version = str(contract.get("version", ""))
    if contract.get("provider") != "groq":
        return version
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "provider": "groq",
                "model": contract["model"],
                "instructions": contract["instructions"],
                "response_format": contract["response_format"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return f"groq:{version}:{fingerprint}"


def _failure_status(exc: VisionError) -> int:
    message = str(exc)
    if "rate limit" in message:
        return 429
    if "unavailable" in message:
        return 503
    return 502


async def analyze_sweep_frame(
    store: SweepStore,
    sweep_id: str,
    frame_id: str,
    *,
    client: httpx.AsyncClient,
    api_key: str | None,
    contract: dict,
    sha_cache: dict,
    tracking_contract: dict | None = None,
    clock=time.monotonic,
    observer=observe_jpeg,
) -> dict:
    path = store.frame_path(sweep_id, frame_id)
    jpeg = path.read_bytes()
    sha = hashlib.sha256(jpeg).hexdigest()
    provider = contract.get("provider", "openai")
    cache_key = f"{vision_cache_version(contract)}:{sha}"
    cached = sha_cache.get(cache_key)
    started = clock()
    if cached is not None:
        elapsed_ms = (clock() - started) * 1000.0
        reply = dict(cached["reply"])
        reply["frame_id"] = frame_id
        reply["source_frame_id"] = cached["source_frame_id"]
        reply["stage_ref"] = cached["stage_ref"]
        reply["model"] = cached["model"]
        reply["cost_usd_estimate"] = cached["cost_usd_estimate"]
        reply["elapsed_ms"] = elapsed_ms
        reply["cached"] = True
        store.record_observation(
            sweep_id,
            frame_id,
            {
                "stage": VisionStageRecord(
                    frame_id=frame_id,
                    status="cached",
                    provider=provider,
                    persisted_ref=cached["stage_ref"],
                    model=cached["model"],
                    cost_usd_estimate="0",
                    elapsed_ms=elapsed_ms,
                    error=None,
                ),
                "observation": VisionObservation(**reply),
            },
        )
        if tracking_contract is not None:
            commit_observation(store, sweep_id, frame_id, reply, jpeg, tracking_contract)
        return reply
    if not api_key:
        raise StoreError(503, "OPENAI_API_KEY not configured")
    try:
        result = await observer(client, jpeg, api_key, contract)
    except VisionError as exc:
        elapsed_ms = (clock() - started) * 1000.0
        reason = str(exc)
        ref = store.persist_stage(
            sweep_id,
            f"vision_{frame_id}_{uuid.uuid4().hex}.json",
            json.dumps(
                {
                    "frame_id": frame_id,
                    "status": "failed",
                    "provider": provider,
                    "error": reason,
                    "elapsed_ms": elapsed_ms,
                }
            ).encode(),
        )
        store.record_observation(
            sweep_id,
            frame_id,
            {
                "stage": VisionStageRecord(
                    frame_id=frame_id,
                    status="failed",
                    provider=provider,
                    persisted_ref=ref,
                    model=None,
                    cost_usd_estimate=None,
                    elapsed_ms=elapsed_ms,
                    error=reason,
                ),
                "observation": None,
            },
        )
        raise StoreError(_failure_status(exc), reason) from exc
    elapsed_ms = (clock() - started) * 1000.0
    scene = result["scene"]
    coordinate_system = result.get("coordinate_system", "normalized_1000")
    mapping = proposal_boxes(
        scene,
        result["image_width"],
        result["image_height"],
        confidence_min=contract["admission"]["book_existence_confidence_min"],
        coordinate_system=coordinate_system,
        sent_width=result["sent_image_width"],
        sent_height=result["sent_image_height"],
    )
    ref = store.persist_stage(
        sweep_id,
        f"vision_{frame_id}_{uuid.uuid4().hex}.json",
        json.dumps(
            {
                "frame_id": frame_id,
                "status": "completed",
                "provider": provider,
                "scene": scene,
                "raw_response": result["raw_response"],
                "usage": result["usage"],
                "model": result["model"],
                "response_id": result["response_id"],
                "cost_usd_estimate": result["cost_usd_estimate"],
                "elapsed_ms": elapsed_ms,
                "image_width": result["image_width"],
                "image_height": result["image_height"],
                "sent_image_width": result["sent_image_width"],
                "sent_image_height": result["sent_image_height"],
                "coordinate_system": coordinate_system,
            }
        ).encode(),
    )
    reply = {
        "frame_id": frame_id,
        "source_frame_id": frame_id,
        "observation": scene,
        "box_mapping": mapping,
        "stage_ref": ref,
        "model": result["model"],
        "cost_usd_estimate": result["cost_usd_estimate"],
        "elapsed_ms": elapsed_ms,
        "usage": result["usage"],
        "image_width": result["image_width"],
        "image_height": result["image_height"],
        "sent_image_width": result["sent_image_width"],
        "sent_image_height": result["sent_image_height"],
        "coordinate_system": coordinate_system,
        "cached": False,
    }
    store.record_observation(
        sweep_id,
        frame_id,
        {
            "stage": VisionStageRecord(
                frame_id=frame_id,
                status="completed",
                provider=provider,
                persisted_ref=ref,
                model=result["model"],
                cost_usd_estimate=result["cost_usd_estimate"],
                elapsed_ms=elapsed_ms,
                error=None,
            ),
            "observation": VisionObservation(**reply),
        },
    )
    sha_cache[cache_key] = {
        "source_frame_id": frame_id,
        "stage_ref": ref,
        "model": result["model"],
        "cost_usd_estimate": result["cost_usd_estimate"],
        "reply": {
            key: reply[key]
            for key in (
                "observation",
                "box_mapping",
                "usage",
                "image_width",
                "image_height",
                "sent_image_width",
                "sent_image_height",
                "coordinate_system",
            )
        },
    }
    if tracking_contract is not None:
        commit_observation(store, sweep_id, frame_id, reply, jpeg, tracking_contract)
    return reply


class VisionManager:
    def __init__(
        self,
        analyze,
        contract: dict,
        *,
        enabled: bool = False,
        record_skip=None,
        client_factory=None,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        sha_for=None,
    ):
        limits = contract["runtime_limits"]
        self._version = vision_cache_version(contract)
        self._min_interval_s = float(limits["minimum_live_call_interval_s"])
        self._max_calls = int(limits["maximum_calls_per_sweep"])
        self._max_post_calls = int(limits["maximum_post_sweep_calls"])
        self._post_budget_s = float(limits["post_sweep_time_budget_s"])
        self.downstream_reserve_s = float(limits["postwork_downstream_reserve_s"])
        self._semaphore = asyncio.Semaphore(int(limits["maximum_concurrent_vision_calls"]))
        self._analyze = analyze
        self._record_skip = record_skip or (lambda *args: None)
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=30.0))
        self.enabled = enabled
        self._clock = clock
        self._sleep = sleep
        self._sha_for = sha_for
        self._jobs: dict[str, dict] = {}
        self._caches: dict[str, dict] = {}

    def sha_cache(self, sweep_id: str) -> dict:
        return self._caches.setdefault(sweep_id, {})

    def _job(self, sweep_id: str) -> dict:
        return self._jobs.setdefault(
            sweep_id,
            {
                "latest": None,
                "last_sha": None,
                "calls": 0,
                "post_calls": 0,
                "post_seconds": 0.0,
                "ended": False,
                "ended_at": None,
                "last_call_at": None,
                "postwork_deadline": None,
                "task": None,
            },
        )

    def _gate(self, job: dict):
        if job["calls"] >= self._max_calls:
            return "vision call budget exhausted"
        if job["ended"]:
            cutoff = job.get("postwork_deadline")
            if cutoff is not None and self._clock() >= cutoff:
                return "post-sweep vision cutoff reached"
            if job["post_calls"] >= self._max_post_calls:
                return "post-sweep call budget exhausted"
            if job["post_seconds"] >= self._post_budget_s:
                return "post-sweep time budget exhausted"
            if self._clock() - job["ended_at"] > SHARED_POSTWORK_DEADLINE_S:
                return "post-sweep deadline exceeded"
        return None

    def _remaining(self, job: dict) -> float | None:
        if not job["ended"]:
            return None
        time_left = self._post_budget_s - job["post_seconds"]
        deadline_left = SHARED_POSTWORK_DEADLINE_S - (self._clock() - job["ended_at"])
        cutoff = job.get("postwork_deadline")
        if cutoff is not None:
            deadline_left = min(deadline_left, cutoff - self._clock())
        return max(0.0, min(time_left, deadline_left))

    async def _call(self, job: dict, sweep_id: str, frame_id: str, client, sha=None) -> dict:
        async with self._semaphore:
            if sha is None and self._sha_for is not None:
                sha = self._sha_for(sweep_id, frame_id)
            cache = self._caches.get(sweep_id) or {}
            if sha is not None and f"{self._version}:{sha}" in cache:
                return await self._analyze(sweep_id, frame_id, client)
            if job["last_call_at"] is not None:
                wait = self._min_interval_s - (self._clock() - job["last_call_at"])
                if wait > 0:
                    await self._sleep(wait)
            blocked = self._gate(job)
            timeout = self._remaining(job)
            if not blocked and timeout is not None and timeout <= 0:
                blocked = "post-sweep time budget exhausted"
            if blocked:
                self._record_skip(sweep_id, frame_id, blocked)
                raise StoreError(429, blocked)
            job["calls"] += 1
            if job["ended"]:
                job["post_calls"] += 1
            started = self._clock()
            job["last_call_at"] = started
            try:
                result = await asyncio.wait_for(
                    self._analyze(sweep_id, frame_id, client), timeout=timeout
                )
            except asyncio.TimeoutError:
                if job["ended"]:
                    job["post_seconds"] += self._clock() - started
                self._record_skip(sweep_id, frame_id, "post-sweep time budget exhausted")
                raise StoreError(503, "vision call exceeded remaining budget")
            except Exception:
                if job["ended"]:
                    job["post_seconds"] += self._clock() - started
                raise
            if result.get("cached"):
                job["calls"] -= 1
                if job["ended"]:
                    job["post_calls"] -= 1
            elif job["ended"]:
                job["post_seconds"] += self._clock() - started
            return result

    async def analyze_now(self, sweep_id: str, frame_id: str, *, client=None) -> dict:
        job = self._job(sweep_id)
        if client is None:
            async with self._client_factory() as client:
                return await self._call(job, sweep_id, frame_id, client)
        return await self._call(job, sweep_id, frame_id, client)

    def notify_frame(self, sweep_id: str, frame_id: str, sha: str | None = None) -> None:
        if not self.enabled:
            return
        job = self._job(sweep_id)
        job["latest"] = (frame_id, sha)
        task = job["task"]
        if task is None or task.done():
            job["task"] = asyncio.ensure_future(self._worker(sweep_id))

    def remaining_budget(self, sweep_id: str) -> dict:
        job = self._jobs.get(sweep_id)
        if job is None:
            return {
                "calls_left": self._max_calls,
                "post_calls_left": self._max_post_calls,
                "seconds_left": self._post_budget_s,
            }
        seconds = (
            self._remaining(job) if job["ended"] else self._post_budget_s
        )
        return {
            "calls_left": max(0, self._max_calls - job["calls"]),
            "post_calls_left": max(0, self._max_post_calls - job["post_calls"]),
            "seconds_left": max(0.0, seconds),
        }

    def set_postwork_deadline(self, sweep_id: str, deadline_mono: float) -> None:
        if (
            isinstance(deadline_mono, bool)
            or not isinstance(deadline_mono, (int, float))
            or not math.isfinite(deadline_mono)
        ):
            raise ValueError("postwork deadline must be finite")
        job = self._job(sweep_id)
        current = job.get("postwork_deadline")
        job["postwork_deadline"] = (
            float(deadline_mono)
            if current is None
            else min(current, float(deadline_mono))
        )

    def notify_ended(self, sweep_id: str) -> None:
        job = self._job(sweep_id)
        if not job["ended"]:
            job["ended"] = True
            job["ended_at"] = self._clock()

    async def _worker(self, sweep_id: str) -> None:
        job = self._jobs[sweep_id]
        try:
            while job["latest"] is not None:
                frame_id, sha = job["latest"]
                job["latest"] = None
                if sha is not None and sha == job["last_sha"]:
                    self._record_skip(sweep_id, frame_id, "identical image bytes already analyzed")
                    continue
                try:
                    async with self._client_factory() as client:
                        await self._call(job, sweep_id, frame_id, client, sha=sha)
                    if sha is not None:
                        job["last_sha"] = sha
                except StoreError:
                    pass
                except Exception as exc:
                    self._record_skip(sweep_id, frame_id, str(getattr(exc, "detail", exc))[:200])
        finally:
            job["task"] = None

    async def drain_sweep(self, sweep_id: str) -> None:
        job = self._jobs.get(sweep_id)
        if job is None:
            return
        task = job["task"]
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def drain(self) -> None:
        tasks = [job["task"] for job in self._jobs.values() if job["task"] is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        for job in self._jobs.values():
            task = job["task"]
            if task is not None and not task.done():
                task.cancel()
        await self.drain()
