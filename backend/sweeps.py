import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from .models import (
    AssertionRecord,
    BBoxXYWH,
    BookTrack,
    CorrectionIntent,
    CorrectionResult,
    CoverageGap,
    CoverageManifest,
    FrameRecord,
    ItemTrack,
    ObservationEvidence,
    PricingStageRecord,
    ReadingStageRecord,
    RegionRecord,
    ReviewItem,
    SpineReading,
    SweepState,
    UserTurnRecord,
    normalize_country,
    normalize_currency,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SWEEP_ID_RE = re.compile(r"^[0-9a-f]{32}$")
FRAME_ID_RE = re.compile(r"^frame_[0-9a-f]{32}$")
BOOK_ID_RE = re.compile(r"^book_[0-9a-f]{32}$")
ITEM_ID_RE = re.compile(r"^item_[0-9a-f]{32}$")
CROP_NAME_RE = re.compile(r"^crop_[0-9a-z_]+_[0-9a-f]{32}\.jpg$")
STAGE_NAME_RE = re.compile(r"^[a-z_]+_[0-9a-f]{32}_[0-9a-f]{32}\.json$")


def _coverage_gaps(entries) -> list:
    grouped: dict = {}
    order: list = []
    for entry in entries:
        if entry.processing_status in (
            "completed",
            "cached",
            "pending",
            "running",
        ):
            continue
        reason = entry.reason
        if entry.processing_status == "interrupted":
            reason = "analysis interrupted"
        key = (entry.row_id, reason)
        if key not in grouped:
            grouped[key] = CoverageGap(
                frame_ids=[], row_id=entry.row_id, reason=reason
            )
            order.append(key)
        grouped[key].frame_ids.append(entry.frame_id)
    return [grouped[key] for key in order]


ART_CATEGORIES = ("art", "portrait", "framed_print")
MAX_FRAME_BYTES = 5 * 1024 * 1024
MAX_FRAME_PIXELS = 12_000_000
TURN_SOURCES = ("typed_user", "provider_user_transcript")
SOF_MARKERS = frozenset(
    (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
)


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    offset = 2
    while offset < len(data):
        if data[offset] != 0xFF:
            return None
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None
        marker = data[offset]
        offset += 1
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0xD9, 0xDA):
            return None
        if offset + 2 > len(data):
            return None
        seg_len = int.from_bytes(data[offset:offset + 2], "big")
        if seg_len < 2 or offset + seg_len > len(data):
            return None
        if marker in SOF_MARKERS:
            if seg_len < 7:
                return None
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            if width <= 0 or height <= 0:
                return None
            return width, height
        offset += seg_len
    return None


def _frames_intact(state: SweepState) -> bool:
    return all(
        FRAME_ID_RE.match(frame.id) and frame.frame_ref == f"frames/{frame.id}.jpg"
        for frame in state.frames
    )


class StoreError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_text(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def _append_review(state: SweepState, ref_id: str, reason: str) -> None:
    if not any(
        r.ref_id == ref_id and r.reason == reason for r in state.review_queue
    ):
        state.review_queue.append(ReviewItem(ref_id=ref_id, reason=reason))


_NEGATED = ("not ", "no ", "don't", "dont", "isn't", "isnt", "aren't", "arent", "unsure", "unknown", "maybe")


def _unclear_assertion(value) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, str):
        lowered = value.strip().lower()
        if not lowered:
            return True
        return any(lowered.startswith(t) or f" {t}" in lowered for t in _NEGATED)
    return False


def _affirmed_print(value) -> bool:
    if _unclear_assertion(value):
        return False
    if value is True:
        return True
    if isinstance(value, str):
        lowered = value.strip().lower()
        if "print" in lowered or "reproduction" in lowered or "poster" in lowered:
            return True
        if any(t in lowered for t in ("original", "painting", "canvas")):
            return False
        return False
    return False


_APPRAISAL_TERMS = (
    "rare",
    "first edition",
    "signed",
    "antiquarian",
    "collector",
)


def _appraisal_assertion(value) -> bool:
    if _unclear_assertion(value) or not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return any(term in lowered for term in _APPRAISAL_TERMS)


def _catalogue_ok(record, evidence) -> bool:
    if evidence is None or not evidence.url or not evidence.retrieved_at:
        return False
    if not record.title or evidence.matched_title != record.title:
        return False
    if not evidence.matched_author:
        return False
    if (
        record.author_legible
        and record.author
        and evidence.matched_author != record.author
    ):
        return False
    return True


def _pending_stage_reviews() -> list[ReviewItem]:
    return [
        ReviewItem(ref_id="extraction", reason="pending: frames not yet processed"),
        ReviewItem(ref_id="measurement", reason="pending: dimensions not measured"),
        ReviewItem(ref_id="pricing", reason="pending: prices not retrieved"),
    ]


class SweepStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._states: dict[str, SweepState] = {}
        self._corrupted: set[str] = set()
        for child in sorted(self.root.iterdir()):
            if not SWEEP_ID_RE.match(child.name):
                continue
            if child.is_symlink() or not child.is_dir():
                self._corrupted.add(child.name)
                continue
            state_path = child / "state.json"
            if state_path.is_symlink():
                self._corrupted.add(child.name)
                continue
            if not state_path.is_file():
                continue
            try:
                state = SweepState.model_validate_json(state_path.read_bytes())
            except (ValueError, OSError):
                self._corrupted.add(child.name)
                continue
            if state.id != child.name or not _frames_intact(state):
                self._corrupted.add(child.name)
                continue
            if any(stage.status == "running" for stage in state.reading_stages):
                state = state.model_copy(
                    update={
                        "reading_stages": [
                            stage.model_copy(update={"status": "interrupted"})
                            if stage.status == "running"
                            else stage
                            for stage in state.reading_stages
                        ]
                    }
                )
                self._save(state)
            if any(stage.status == "running" for stage in state.price_stages):
                state = state.model_copy(
                    update={
                        "price_stages": [
                            stage.model_copy(update={"status": "interrupted"})
                            if stage.status == "running"
                            else stage
                            for stage in state.price_stages
                        ]
                    }
                )
                self._save(state)
            if state.status in ("capturing", "processing"):
                state = state.model_copy(update={"status": "interrupted"})
                self._save(state)
            if state.postwork_status == "running":
                state = state.model_copy(
                    update={
                        "postwork_status": "interrupted",
                        "postwork_stage": None,
                    }
                )
                self._save(state)
            self._states[state.id] = state

    def _dir(self, sweep_id: str) -> Path:
        return self.root / sweep_id

    def _save(self, state: SweepState) -> None:
        sweep_dir = self._dir(state.id)
        sweep_dir.mkdir(parents=True, exist_ok=True)
        tmp = sweep_dir / "state.json.tmp"
        tmp.write_bytes(state.model_dump_json().encode())
        os.replace(tmp, sweep_dir / "state.json")

    def _loaded(self, sweep_id: str) -> SweepState:
        if not SWEEP_ID_RE.match(sweep_id):
            raise StoreError(404, "unknown sweep")
        if sweep_id in self._corrupted:
            raise StoreError(500, "stored sweep state is unreadable")
        state = self._states.get(sweep_id)
        if state is None:
            raise StoreError(404, "unknown sweep")
        return state

    def start(self, country: str, currency: str, device: str) -> SweepState:
        try:
            country_code = normalize_country(country)
            currency_code = normalize_currency(currency)
        except ValueError as exc:
            raise StoreError(422, str(exc)) from exc
        with self._lock:
            state = SweepState(
                id=uuid.uuid4().hex,
                revision=0,
                status="capturing",
                captured_at=_utcnow(),
                ended_at=None,
                country=country_code,
                currency=currency_code,
                device=device,
            )
            (self._dir(state.id) / "frames").mkdir(parents=True)
            self._save(state)
            self._states[state.id] = state
            return state.model_copy(deep=True)

    def get(self, sweep_id: str) -> SweepState:
        with self._lock:
            return self._loaded(sweep_id).model_copy(deep=True)

    def frame_path(self, sweep_id: str, frame_id: str) -> Path:
        with self._lock:
            state = self._loaded(sweep_id)
            if not FRAME_ID_RE.match(frame_id) or not any(
                record.id == frame_id for record in state.frames
            ):
                raise StoreError(404, "unknown frame")
            sweep_dir = self._dir(sweep_id)
            frames_dir = sweep_dir / "frames"
            target = frames_dir / f"{frame_id}.jpg"
            if (
                sweep_dir.is_symlink()
                or frames_dir.is_symlink()
                or target.is_symlink()
                or not target.is_file()
            ):
                raise StoreError(404, "unknown frame")
            try:
                expected = self.root.resolve() / sweep_id / "frames" / f"{frame_id}.jpg"
                if target.resolve() != expected:
                    raise StoreError(404, "unknown frame")
            except OSError:
                raise StoreError(404, "unknown frame")
            return target

    def persist_stage(self, sweep_id: str, name: str, payload: bytes) -> str:
        with self._lock:
            self._loaded(sweep_id)
            if not STAGE_NAME_RE.match(name):
                raise StoreError(500, "invalid stage output name")
            stage_dir = self._dir(sweep_id) / "stage_outputs"
            if stage_dir.is_symlink():
                raise StoreError(500, "stage storage unavailable")
            stage_dir.mkdir(exist_ok=True)
            target = stage_dir / name
            with open(target, "xb") as handle:
                handle.write(payload)
            return f"stage_outputs/{name}"

    def persist_crop(self, sweep_id: str, name: str, payload: bytes) -> str:
        with self._lock:
            self._loaded(sweep_id)
            if not CROP_NAME_RE.match(name):
                raise StoreError(500, "invalid crop name")
            crop_dir = self._dir(sweep_id) / "crops"
            if crop_dir.is_symlink():
                raise StoreError(500, "crop storage unavailable")
            crop_dir.mkdir(exist_ok=True)
            target = crop_dir / name
            with open(target, "xb") as handle:
                handle.write(payload)
            return f"crops/{name}"

    def stage_path(self, sweep_id: str, ref: str) -> Path:
        with self._lock:
            self._loaded(sweep_id)
            if (
                not isinstance(ref, str)
                or not ref.startswith("stage_outputs/")
                or "/" in ref[len("stage_outputs/") :]
                or not STAGE_NAME_RE.match(ref[len("stage_outputs/") :])
            ):
                raise StoreError(409, "invalid stage reference")
            name = ref[len("stage_outputs/") :]
            sweep_dir = self._dir(sweep_id)
            stage_dir = sweep_dir / "stage_outputs"
            target = stage_dir / name
            if (
                sweep_dir.is_symlink()
                or stage_dir.is_symlink()
                or target.is_symlink()
                or not target.is_file()
            ):
                raise StoreError(409, "stage reference does not resolve")
            try:
                expected = (
                    self.root.resolve() / sweep_id / "stage_outputs" / name
                )
                if target.resolve() != expected:
                    raise StoreError(409, "stage reference does not resolve")
            except OSError:
                raise StoreError(409, "stage reference does not resolve")
            return target

    def _packet_target(self, run_dir: Path, name: str) -> Path:
        expected = self.root.resolve() / run_dir.name / name
        target = run_dir / name
        if (
            target.is_symlink()
            or (target.exists() and not target.is_file())
            or (target.exists() and target.resolve() != expected)
        ):
            raise StoreError(500, "packet artifact path unsafe")
        return target

    def publish_packet(self, sweep_id: str) -> dict:
        import tempfile
        import time

        from .packet import build_packet, render_report_html

        with self._lock:
            state = self._loaded(sweep_id)
            if state.status == "capturing":
                raise StoreError(409, "sweep is actively capturing")
            packet = build_packet(state, self)
            render_started = time.monotonic()
            report = render_report_html(packet)
            rendering_ms = round((time.monotonic() - render_started) * 1000.0, 3)
            run_dir = self._dir(sweep_id)
            if run_dir.is_symlink() or not run_dir.is_dir():
                raise StoreError(500, "packet artifact path unsafe")
            targets = {
                name: self._packet_target(run_dir, name)
                for name in ("claim_packet.json", "report.html")
            }
            payloads = {
                "claim_packet.json": json.dumps(
                    packet, ensure_ascii=False, indent=2
                ).encode("utf-8"),
                "report.html": report.encode("utf-8"),
            }
            temps = []
            try:
                for name, data in payloads.items():
                    fd, tmp_name = tempfile.mkstemp(
                        dir=run_dir, prefix="." + name + ".", suffix=".tmp"
                    )
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(data)
                    temps.append((tmp_name, targets[name]))
                for tmp_name, target in temps:
                    os.replace(tmp_name, target)
                temps = []
            finally:
                for tmp_name, _ in temps:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
            ready_ms = None
            if state.ended_at:
                try:
                    ended = datetime.fromisoformat(state.ended_at)
                    if ended.tzinfo is not None:
                        ready_ms = round(
                            (datetime.now(timezone.utc) - ended).total_seconds()
                            * 1000.0,
                            3,
                        )
                except (TypeError, ValueError):
                    ready_ms = None
            state.packet_ref = "claim_packet.json"
            state.report_ref = "report.html"
            self._save(state)
            return {
                "packet_ref": state.packet_ref,
                "report_ref": state.report_ref,
                "summary": packet["summary"],
                "revision": state.revision,
                "status": packet["status"],
                "rendering_elapsed_ms": rendering_ms,
                "packet_ready_elapsed_ms": ready_ms,
            }

    def read_packet_artifact(self, sweep_id: str, name: str) -> tuple:
        with self._lock:
            state = self._loaded(sweep_id)
            if name == "claim_packet.json":
                registered = state.packet_ref == name
            elif name == "report.html":
                registered = state.report_ref == name
            else:
                raise StoreError(404, "packet artifact not generated")
            if not registered:
                raise StoreError(404, "packet artifact not generated")
            run_dir = self._dir(sweep_id)
            expected_root = self.root.resolve() / sweep_id

            def safe_file(artifact):
                target = run_dir / artifact
                if (
                    run_dir.is_symlink()
                    or target.is_symlink()
                    or not target.is_file()
                    or target.resolve() != expected_root / artifact
                ):
                    raise StoreError(404, "packet artifact not generated")
                return target

            packet_bytes = safe_file("claim_packet.json").read_bytes()
            try:
                packet_revision = json.loads(packet_bytes)["sweep"]["revision"]
            except (KeyError, TypeError, ValueError):
                raise StoreError(404, "packet artifact not generated") from None
            if name == "claim_packet.json":
                content = packet_bytes
            else:
                content = safe_file("report.html").read_bytes()
            return content, packet_revision, packet_revision != state.revision

    def crop_path(self, sweep_id: str, crop_file: str) -> Path:
        with self._lock:
            self._loaded(sweep_id)
            if not CROP_NAME_RE.match(crop_file):
                raise StoreError(404, "unknown crop")
            sweep_dir = self._dir(sweep_id)
            crop_dir = sweep_dir / "crops"
            target = crop_dir / crop_file
            if (
                sweep_dir.is_symlink()
                or crop_dir.is_symlink()
                or target.is_symlink()
                or not target.is_file()
            ):
                raise StoreError(404, "unknown crop")
            try:
                expected = self.root.resolve() / sweep_id / "crops" / crop_file
                if target.resolve() != expected:
                    raise StoreError(404, "unknown crop")
            except OSError:
                raise StoreError(404, "unknown crop")
            return target

    def record_observation(self, sweep_id: str, frame_id: str, result: dict) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            if state.status == "interrupted":
                raise StoreError(409, "sweep interrupted")
            if not FRAME_ID_RE.match(frame_id) or not any(
                record.id == frame_id for record in state.frames
            ):
                raise StoreError(404, "unknown frame")
            stage = result["stage"]
            observation = result.get("observation")
            state.vision_stages.append(stage)
            if observation is not None:
                state.latest_observation = observation
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)

    def record_coverage(self, sweep_id: str, manifest) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            if not isinstance(manifest, CoverageManifest):
                raise StoreError(422, "invalid coverage manifest")
            if manifest.spatial_coverage_status != "unresolved":
                raise StoreError(
                    422, "spatial coverage cannot be marked supported"
                )
            frame_ids = [record.id for record in state.frames]
            entry_ids = [entry.frame_id for entry in manifest.entries]
            if entry_ids != frame_ids:
                raise StoreError(
                    422,
                    "coverage manifest must contain one decision per saved frame, in order",
                )
            if state.status == "interrupted":
                if state.coverage_manifest is None:
                    raise StoreError(
                        409,
                        "interrupted sweep accepts no new coverage manifest",
                    )
                prior = {
                    entry.frame_id: entry
                    for entry in state.coverage_manifest.entries
                }
                for entry in manifest.entries:
                    previous = prior[entry.frame_id]
                    if entry.processing_status in (
                        "completed",
                        "cached",
                    ) and previous.processing_status not in (
                        "completed",
                        "cached",
                    ):
                        raise StoreError(
                            409,
                            "interrupted sweep does not accept completed coverage results",
                        )
                manifest = state.coverage_manifest.model_copy(
                    update={
                        "entries": [
                            entry.model_copy(
                                update={"processing_status": "interrupted"}
                            )
                            if entry.processing_status in ("pending", "running")
                            else entry
                            for entry in state.coverage_manifest.entries
                        ]
                    }
                )
            stages_by_frame: dict = {}
            for stage in state.vision_stages:
                stages_by_frame.setdefault(stage.frame_id, []).append(stage)
            for entry in manifest.entries:
                frame_stages = stages_by_frame.get(entry.frame_id, [])
                frame_refs = {
                    stage.persisted_ref
                    for stage in frame_stages
                    if stage.persisted_ref
                }
                for ref in entry.stage_refs:
                    self.stage_path(sweep_id, ref)
                    if ref not in frame_refs:
                        raise StoreError(
                            422,
                            "stage reference does not belong to the frame",
                        )
                if entry.processing_status in ("completed", "cached"):
                    if not any(
                        stage.status in ("completed", "cached")
                        and stage.persisted_ref in entry.stage_refs
                        for stage in frame_stages
                    ):
                        raise StoreError(
                            422,
                            "successful coverage decision requires persisted stage evidence",
                        )
            known = set(frame_ids)
            for gap in manifest.gaps:
                for frame_id in gap.frame_ids:
                    if frame_id not in known:
                        raise StoreError(
                            422, "coverage gap references unknown frame"
                        )
            manifest = manifest.model_copy(
                update={"gaps": _coverage_gaps(manifest.entries)}
            )
            if state.coverage_manifest == manifest:
                return state.model_copy(deep=True)
            state.coverage_manifest = manifest.model_copy(deep=True)
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)

    def add_frame(self, sweep_id: str, jpeg: bytes, timestamp_s: float) -> FrameRecord:
        with self._lock:
            state = self._loaded(sweep_id)
            if state.status != "capturing":
                raise StoreError(409, "sweep is not capturing")
            if not isinstance(timestamp_s, (int, float)) or not np.isfinite(timestamp_s) or timestamp_s < 0:
                raise StoreError(422, "invalid timestamp")
            if jpeg is None or len(jpeg) == 0:
                raise StoreError(422, "empty frame")
            if len(jpeg) > MAX_FRAME_BYTES:
                raise StoreError(413, "frame too large")
            declared = _jpeg_dimensions(jpeg)
            if declared is None:
                raise StoreError(422, "not a decodable JPEG image")
            if declared[0] * declared[1] > MAX_FRAME_PIXELS:
                raise StoreError(422, "frame dimensions out of bounds")
            decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if decoded is None:
                raise StoreError(422, "not a decodable JPEG image")
            height, width = decoded.shape[:2]
            if width <= 0 or height <= 0 or width * height > MAX_FRAME_PIXELS:
                raise StoreError(422, "frame dimensions out of bounds")
            if state.frames and timestamp_s <= state.frames[-1].timestamp_s:
                raise StoreError(409, "timestamp must advance past prior frame")
            frames_dir = self._dir(state.id) / "frames"
            if frames_dir.is_symlink() or not frames_dir.is_dir():
                raise StoreError(500, "frame storage unavailable")
            gray = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
            long_edge = max(width, height)
            if long_edge > 256:
                scale = 256.0 / long_edge
                gray = cv2.resize(
                    gray,
                    (
                        max(1, int(round(width * scale))),
                        max(1, int(round(height * scale))),
                    ),
                    interpolation=cv2.INTER_AREA,
                )
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            frame = FrameRecord(
                id="frame_" + uuid.uuid4().hex,
                frame_ref="",
                timestamp_s=float(timestamp_s),
                width_px=int(width),
                height_px=int(height),
                sha256=hashlib.sha256(jpeg).hexdigest(),
                sharpness_score=sharpness,
            )
            frame = frame.model_copy(update={"frame_ref": f"frames/{frame.id}.jpg"})
            target = frames_dir / f"{frame.id}.jpg"
            with open(target, "xb") as handle:
                handle.write(jpeg)
            state.frames.append(frame)
            state.revision += 1
            self._save(state)
            return frame.model_copy()

    def add_turn(self, sweep_id: str, turn_id: str, text: str, source: str) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            try:
                turn = UserTurnRecord(turn_id=turn_id, text=text, source=source)
            except ValueError as exc:
                raise StoreError(422, str(exc)) from exc
            if any(existing.turn_id == turn_id for existing in state.user_turns):
                return state.model_copy(deep=True)
            state.user_turns.append(turn)
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)

    def _region_excluded(self, state: SweepState, region_id: str) -> bool:
        return any(r.id == region_id and r.excluded for r in state.regions)

    def _shelving_in_frame(self, state: SweepState, frame_id: str) -> dict:
        shelves = {}
        for item in state.items:
            if item.category != "shelving":
                continue
            ev = next(
                (e for e in item.evidence if e.frame_id == frame_id),
                None,
            )
            if ev is not None:
                shelves[item.id] = ev.bbox
        return shelves

    def _shelf_for_box(self, shelves: dict, bbox) -> str | None:
        cx = bbox.x + bbox.w / 2
        cy = bbox.y + bbox.h / 2
        inside = [
            (item_id, box)
            for item_id, box in shelves.items()
            if box.x <= cx <= box.x + box.w and box.y <= cy <= box.y + box.h
        ]
        if not inside:
            return None
        return min(inside, key=lambda pair: pair[1].w * pair[1].h)[0]

    def commit_tracks(self, sweep_id: str, frame_id: str, result: dict) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            if state.status == "interrupted":
                raise StoreError(409, "sweep interrupted")
            if not FRAME_ID_RE.match(frame_id) or not any(
                record.id == frame_id for record in state.frames
            ):
                raise StoreError(404, "unknown frame")
            by_book = {b.id: b for b in state.books}
            by_item = {i.id: i for i in state.items}
            observed_books: list[tuple] = []
            for assignment in result.get("assignments", []):
                kind = assignment["kind"]
                pool = state.books if kind == "book" else state.items
                track = next((t for t in pool if t.id == assignment["track_id"]), None)
                if track is None:
                    continue
                proposal = next(
                    (p for p in result.get("applied_props", [])
                     if p["index"] == assignment["proposal_index"] and p["kind"] == kind),
                    None,
                )
                if proposal is None:
                    continue
                ev = proposal["evidence"]
                frame_box = BBoxXYWH(**ev["bbox"])
                if not any(
                    e.frame_id == ev["frame_id"]
                    and (e.bbox.x, e.bbox.y, e.bbox.w, e.bbox.h)
                    == (frame_box.x, frame_box.y, frame_box.w, frame_box.h)
                    for e in track.evidence
                ):
                    track.evidence.append(ObservationEvidence(**ev))
                track.bbox = track.bbox.model_copy(update=ev["bbox"])
                track.image_width = ev["image_width"]
                track.image_height = ev["image_height"]
                track.frame_ref = ev["frame_ref"]
                if proposal.get("hist") is not None:
                    track.track_histogram = proposal["hist"]
                if kind == "book":
                    for frag in proposal.get("fragments", []):
                        if frag not in track.observed_fragments:
                            track.observed_fragments.append(frag)
                    if proposal.get("flags"):
                        track.status = "needs_appraisal"
                        track.replacement_cost = None
                        track.used_value = None
                    observed_books.append((track, frame_box))
            new_book_ids = set()
            for payload in result.get("new_books", []):
                track = BookTrack(id="book_" + uuid.uuid4().hex, **payload)
                state.books.append(track)
                new_book_ids.add(track.id)
                observed_books.append((track, track.bbox))
            for payload in result.get("new_items", []):
                track = ItemTrack(id="item_" + uuid.uuid4().hex, **payload)
                state.items.append(track)
            frame_shelves = self._shelving_in_frame(state, frame_id)
            for track, frame_box in observed_books:
                shelf_id = self._shelf_for_box(frame_shelves, frame_box)
                if shelf_id is not None:
                    track.shelf = shelf_id
                    track.shelf_reason = None
                elif track.id in new_book_ids or frame_shelves or not track.shelf:
                    track.shelf = None
                    track.shelf_reason = "no observed shelving region"
            for shelf_id in {t.shelf for t, _ in observed_books if t.shelf}:
                members = [
                    track for track, _ in observed_books if track.shelf == shelf_id
                ]
                all_members = [b for b in state.books if b.shelf == shelf_id]
                if len(all_members) == len(members):
                    members.sort(key=lambda t: (t.bbox.x, t.bbox.y))
                    for position, member in enumerate(members, start=1):
                        member.position = position
                else:
                    for member in members:
                        member.position = None
                        if not any(
                            r.ref_id == member.id
                            and r.reason
                            == "shelf position unresolved: incomplete same-frame view"
                            for r in state.review_queue
                        ):
                            state.review_queue.append(
                                ReviewItem(
                                    ref_id=member.id,
                                    reason="shelf position unresolved: incomplete same-frame view",
                                )
                            )
            for track in state.books:
                if track.shelf is None:
                    track.position = None
            item_map = {i.id: i for i in state.items}
            room_excluded = self._region_excluded(state, "room")
            for track in state.books:
                shelf_item = item_map.get(track.shelf)
                track.excluded = track.excluded or room_excluded or (
                    shelf_item is not None
                    and (shelf_item.excluded or self._region_excluded(state, shelf_item.id))
                )
            for track in state.items:
                track.excluded = track.excluded or room_excluded or self._region_excluded(state, track.id)
            for track_id in result.get("flag_tracks", []):
                track = by_book.get(track_id) or by_item.get(track_id)
                if track is not None:
                    track.potential_duplicate = True
            for item in result.get("review_items", []):
                if not any(
                    r.ref_id == item["ref_id"] and r.reason == item["reason"]
                    for r in state.review_queue
                ):
                    state.review_queue.append(ReviewItem(**item))
            state.last_analyzed_frame_id = frame_id
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)

    def reconcile_book_tracks(
        self, sweep_id: str, selected_ids: list[str], representative_frame_id: str,
        supporting_frame_ids: list[str], unresolved_frame_ids: list[str],
    ) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            if state.ended_at is None:
                raise StoreError(409, "end the sweep before reconciling book tracks")
            if state.scene_reconciliation is not None:
                return state.model_copy(deep=True)
            known_ids = {book.id for book in state.books}
            frames = {frame.id for frame in state.frames}
            if (
                not selected_ids or len(selected_ids) != len(set(selected_ids))
                or not set(selected_ids) <= known_ids
                or representative_frame_id not in frames
                or not set(supporting_frame_ids + unresolved_frame_ids) <= frames
            ):
                raise StoreError(422, "invalid scene reconciliation evidence")
            if (
                state.corrections or state.reading_stages or state.price_stages
                or any(
                    book.assertions or book.identification_evidence or book.crop_refs
                    or book.replacement_cost or book.used_value
                    for book in state.books
                )
            ):
                raise StoreError(409, "existing book claims require manual scene reconciliation")
            chosen = set(selected_ids)
            archived = [book for book in state.books if book.id not in chosen]
            state.books = [book for book in state.books if book.id in chosen]
            state.unresolved_books.extend(archived)
            state.scene_reconciliation = {
                "representative_frame_id": representative_frame_id,
                "supporting_frame_ids": supporting_frame_ids,
                "unresolved_frame_ids": unresolved_frame_ids,
                "selected_book_ids": selected_ids,
                "count_status": "provisional; physical room count not verified",
            }
            state.review_queue.extend(
                ReviewItem(ref_id=book.id, reason="unresolved book proposal; cannot verify as distinct; evidence retained outside counted books")
                for book in archived
            )
            state.review_queue.extend(
                ReviewItem(ref_id=frame_id, reason="scene not sufficiently corroborated; physical count may be incomplete")
                for frame_id in dict.fromkeys(unresolved_frame_ids)
            )
            state.inventory_complete = False
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)

    def record_reading_stage(self, sweep_id: str, record: ReadingStageRecord) -> int:
        with self._lock:
            state = self._loaded(sweep_id)
            state.reading_stages.append(record)
            state.revision += 1
            self._save(state)
            return len(state.reading_stages) - 1

    def update_reading_stage(
        self, sweep_id: str, index: int, record: ReadingStageRecord
    ) -> None:
        with self._lock:
            state = self._loaded(sweep_id)
            if not 0 <= index < len(state.reading_stages):
                raise StoreError(404, "unknown reading stage")
            state.reading_stages[index] = record
            state.revision += 1
            self._save(state)

    @staticmethod
    def _deadline_dt(value) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed

    @classmethod
    def _earlier_deadline(cls, existing: str, new: str) -> str:
        existing_dt = cls._deadline_dt(existing)
        new_dt = cls._deadline_dt(new)
        if existing_dt is None or new_dt is None:
            return existing
        return new if new_dt < existing_dt else existing

    def init_reading_deadline(self, sweep_id: str, deadline_at: str) -> str:
        with self._lock:
            state = self._loaded(sweep_id)
            if state.reading_deadline_at is None:
                if state.pricing_deadline_at is not None:
                    deadline_at = self._earlier_deadline(
                        state.pricing_deadline_at, deadline_at
                    )
                state.reading_deadline_at = deadline_at
                state.revision += 1
                self._save(state)
            return state.reading_deadline_at

    def init_pricing_deadline(self, sweep_id: str, deadline_at: str) -> str:
        with self._lock:
            state = self._loaded(sweep_id)
            if state.pricing_deadline_at is None:
                if state.reading_deadline_at is not None:
                    deadline_at = self._earlier_deadline(
                        state.reading_deadline_at, deadline_at
                    )
                state.pricing_deadline_at = deadline_at
                state.revision += 1
                self._save(state)
            return state.pricing_deadline_at

    def record_price_stage(self, sweep_id: str, record: PricingStageRecord) -> int:
        with self._lock:
            state = self._loaded(sweep_id)
            state.price_stages.append(record)
            state.revision += 1
            self._save(state)
            return len(state.price_stages) - 1

    def update_price_stage(
        self, sweep_id: str, index: int, record: PricingStageRecord
    ) -> None:
        with self._lock:
            state = self._loaded(sweep_id)
            if not 0 <= index < len(state.price_stages):
                raise StoreError(404, "unknown pricing stage")
            state.price_stages[index] = record
            state.revision += 1
            self._save(state)

    def commit_prices(
        self, sweep_id: str, identity_snapshot: dict, valuations: dict, stage_ref
    ) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            for book_id, valuation in valuations.items():
                track = next((b for b in state.books if b.id == book_id), None)
                snapshot = identity_snapshot.get(book_id)
                if track is None or snapshot is None:
                    continue
                reading_ok = any(
                    r.status == "accepted"
                    and r.confidence >= 0.9
                    and _norm_text(r.title) == _norm_text(track.title)
                    and _norm_text(r.author) == _norm_text(track.author)
                    for r in track.identification_evidence
                )
                assertions_now = [
                    a.model_dump(mode="json") for a in track.assertions
                ]
                accepted_now = [
                    (r.title, r.author)
                    for r in track.identification_evidence
                    if r.status == "accepted"
                ]
                changed = (
                    track.excluded
                    or track.potential_duplicate
                    or track.status != snapshot["status"]
                    or track.title != snapshot["title"]
                    or track.author != snapshot["author"]
                    or track.edition != snapshot["edition"]
                    or track.isbn != snapshot["isbn"]
                    or assertions_now != snapshot["assertions"]
                    or accepted_now != snapshot["accepted"]
                    or any(
                        r.status == "contradicted"
                        for r in track.identification_evidence
                    )
                    or not reading_ok
                )
                if changed:
                    _append_review(
                        state,
                        book_id,
                        "book price suppressed: book state changed during pricing",
                    )
                    continue
                if valuation.get("needs_appraisal"):
                    track.status = "needs_appraisal"
                    track.replacement_cost = None
                    track.used_value = None
                else:
                    if valuation.get("replacement") is not None:
                        track.replacement_cost = valuation[
                            "replacement"
                        ].model_copy(update={"valuation_stage_ref": stage_ref})
                    if valuation.get("used") is not None:
                        track.used_value = valuation["used"].model_copy(
                            update={"valuation_stage_ref": stage_ref}
                        )
                for reason in valuation.get("reviews", []):
                    _append_review(state, book_id, reason)
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)

    def append_reviews(self, sweep_id: str, items: list) -> None:
        with self._lock:
            state = self._loaded(sweep_id)
            for item in items:
                if not any(
                    r.ref_id == item.ref_id and r.reason == item.reason
                    for r in state.review_queue
                ):
                    state.review_queue.append(item)
            state.revision += 1
            self._save(state)

    def register_crops(self, sweep_id: str, crop_refs: dict) -> None:
        with self._lock:
            state = self._loaded(sweep_id)
            for book_id, refs in crop_refs.items():
                book = next((b for b in state.books if b.id == book_id), None)
                if book is None:
                    continue
                for ref in refs:
                    if ref not in book.crop_refs:
                        book.crop_refs.append(ref)
            state.revision += 1
            self._save(state)

    def apply_readings(
        self,
        sweep_id: str,
        stage_refs: dict,
        readings: list,
        catalogue: dict | None = None,
    ) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            catalogue = catalogue or {}

            def add_review(ref_id: str, reason: str) -> None:
                if not any(
                    r.ref_id == ref_id and r.reason == reason
                    for r in state.review_queue
                ):
                    state.review_queue.append(ReviewItem(ref_id=ref_id, reason=reason))

            for raw in readings:
                book = next((b for b in state.books if b.id == raw["ref_id"]), None)
                if book is None:
                    continue
                evidence = catalogue.get(book.id)
                record = SpineReading(
                    stage_ref=stage_refs.get(book.id), catalogue=evidence, **raw
                )
                corroborated = _catalogue_ok(record, record.catalogue)
                accepted = (
                    record.confidence >= 0.90
                    and record.title_legible
                    and bool(record.title)
                    and (
                        (record.author_legible and bool(record.author))
                        or corroborated
                    )
                )
                if accepted:
                    prior = next(
                        (
                            r
                            for r in reversed(book.identification_evidence)
                            if r.status == "accepted" and r.title
                        ),
                        None,
                    )
                    prior_title = book.title or (prior.title if prior else "")
                    prior_author = book.author or (prior.author if prior else "")
                    scene_title = next(
                        (f["text"] for f in reversed(book.observed_fragments)
                         if f.get("field") == "observed_title"),
                        "",
                    )
                    title_key = _norm_text(record.title)
                    author_key = _norm_text(record.author) if record.author_legible else ""
                    scene_key = _norm_text(scene_title)
                    scene_agrees = scene_key == title_key or bool(
                        author_key and scene_key in (
                            f"{author_key} {title_key}", f"{title_key} {author_key}"
                        )
                    )
                    if (
                        (prior_title and prior_title != record.title)
                        or (
                            prior_title
                            and prior_title == record.title
                            and prior_author
                            and record.author_legible
                            and record.author
                            and prior_author != record.author
                        )
                        or (
                            scene_title
                            and not scene_agrees
                            and not corroborated
                        )
                    ):
                        record = record.model_copy(update={"status": "contradicted"})
                        book.identification_evidence.append(record)
                        book.title = ""
                        book.author = ""
                        book.publisher = ""
                        book.edition = ""
                        book.isbn = ""
                        book.id_confidence = None
                        book.replacement_cost = None
                        book.used_value = None
                        if book.status != "needs_appraisal":
                            book.status = "unidentified"
                        add_review(
                            book.id,
                            "crop reading contradicts earlier accepted identity "
                            "or scene proposal; work identity unsupported",
                        )
                        continue
                    record = record.model_copy(update={"status": "accepted"})
                    book.title = record.title
                    book.author = record.author if record.author_legible else ""
                    book.publisher = record.publisher or ""
                    book.edition = record.edition or ""
                    book.isbn = record.isbn or ""
                    book.id_confidence = record.confidence
                    if book.status != "needs_appraisal":
                        book.status = "identified"
                else:
                    if record.confidence >= 0.90 and record.title and not record.title_legible:
                        record = record.model_copy(update={"status": "low_confidence"})
                    elif not record.title_legible or not record.title:
                        record = record.model_copy(update={"status": "unreadable"})
                    else:
                        record = record.model_copy(update={"status": "low_confidence"})
                    add_review(
                        book.id,
                        "spine reading unresolved ("
                        + record.status
                        + "); identity unsupported",
                    )
                book.identification_evidence.append(record)
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)

    def correct(self, sweep_id: str, intent: CorrectionIntent) -> CorrectionResult:
        with self._lock:
            state = self._loaded(sweep_id)
            if not any(turn.turn_id == intent.source_turn_id for turn in state.user_turns):
                raise StoreError(409, "unknown source turn")
            if intent.source_frame_id is not None and (
                not FRAME_ID_RE.match(intent.source_frame_id)
                or not any(f.id == intent.source_frame_id for f in state.frames)
            ):
                raise StoreError(409, "unknown source frame")

            def frame_evidence(target) -> None:
                if intent.source_frame_id is None:
                    return
                if not any(
                    e.frame_id == intent.source_frame_id for e in target.evidence
                ):
                    raise StoreError(
                        409, "correction target has no evidence in the source frame"
                    )

            review_items: list[ReviewItem] = []
            if intent.type == "exclude_region":
                region = next((r for r in state.regions if r.id == intent.target_id), None)
                shelf_item = next(
                    (i for i in state.items
                     if i.id == intent.target_id and i.category == "shelving"),
                    None,
                )
                if region is None and shelf_item is None:
                    raise StoreError(409, "unknown correction target")
                if shelf_item is not None:
                    frame_evidence(shelf_item)
                if region is None:
                    region = RegionRecord(id=intent.target_id, excluded=False)
                    state.regions.append(region)
                if region.excluded:
                    return CorrectionResult(
                        applied=False, revision=state.revision, review_items=[]
                    )
                region.excluded = True
                if intent.target_id == "room":
                    for track in state.books:
                        track.excluded = True
                    for track in state.items:
                        track.excluded = True
                if shelf_item is not None:
                    shelf_item.excluded = True
                for track in state.books:
                    if track.shelf == intent.target_id:
                        track.excluded = True
                for track in state.items:
                    if track.id == intent.target_id:
                        track.excluded = True
            elif intent.type == "exclude_item":
                target = next(
                    (t for t in state.books if t.id == intent.target_id),
                    None,
                ) or next((t for t in state.items if t.id == intent.target_id), None)
                if target is None:
                    raise StoreError(409, "unknown correction target")
                frame_evidence(target)
                if target.excluded:
                    return CorrectionResult(
                        applied=False, revision=state.revision, review_items=[]
                    )
                target.excluded = True
            elif intent.type == "assert_edition":
                book = next((b for b in state.books if b.id == intent.target_id), None)
                if book is None:
                    raise StoreError(409, "unknown correction target")
                frame_evidence(book)
                contested = _unclear_assertion(intent.value)
                book.assertions.append(
                    AssertionRecord(
                        type="assert_edition",
                        value=intent.value,
                        source_turn_id=intent.source_turn_id,
                        recorded_at=_utcnow(),
                        needs_review=contested,
                    )
                )
                if _appraisal_assertion(intent.value):
                    book.status = "needs_appraisal"
                    book.replacement_cost = None
                    book.used_value = None
                if contested:
                    review_items.append(ReviewItem(
                        ref_id=book.id,
                        reason="edition assertion unclear or negated; requires review",
                    ))
            elif intent.type == "assert_art_print":
                item = next(
                    (
                        i
                        for i in state.items
                        if i.id == intent.target_id
                        and i.category in ART_CATEGORIES
                    ),
                    None,
                )
                if item is None:
                    raise StoreError(409, "unknown correction target")
                frame_evidence(item)
                affirmed = _affirmed_print(intent.value)
                contested = not affirmed
                item.assertions.append(
                    AssertionRecord(
                        type="assert_art_print",
                        value=intent.value,
                        source_turn_id=intent.source_turn_id,
                        recorded_at=_utcnow(),
                        needs_review=contested,
                    )
                )
                if affirmed:
                    if item.status == "needs_appraisal":
                        item.status = "range"
                        item.valuation_state = "unpriced"
                else:
                    if item.status != "needs_appraisal":
                        item.status = "needs_appraisal"
                        item.replacement_cost = None
                        item.valuation_state = "unpriced"
                    review_items.append(ReviewItem(
                        ref_id=item.id,
                        reason="art print assertion unclear or negated; remains for appraisal",
                    ))
            else:
                raise StoreError(409, "unknown correction target")
            state.corrections.append(intent)
            state.review_queue.extend(review_items)
            state.revision += 1
            self._save(state)
            return CorrectionResult(applied=True, revision=state.revision, review_items=review_items)

    def end(self, sweep_id: str, duration_s: float) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            if not isinstance(duration_s, (int, float)) or not np.isfinite(duration_s) or duration_s < 0:
                raise StoreError(422, "invalid duration")
            if state.ended_at is not None or state.status in ("partial", "complete", "interrupted", "failed"):
                return state.model_copy(deep=True)
            state.ended_at = _utcnow()
            state.duration_s = float(duration_s)
            state.status = "partial"
            state.inventory_complete = False
            state.processing_complete = False
            for item in _pending_stage_reviews():
                if not any(
                    r.ref_id == item.ref_id and r.reason == item.reason
                    for r in state.review_queue
                ):
                    state.review_queue.append(item)
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)

    def interrupt(self, sweep_id: str) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            changed = False
            if state.status in ("capturing", "processing"):
                state.status = "interrupted"
                changed = True
            if state.postwork_status == "running":
                state.postwork_status = "interrupted"
                state.postwork_stage = None
                state.status = "interrupted"
                changed = True
            elif (
                state.postwork_status == "not_started"
                and state.ended_at is not None
                and state.status != "interrupted"
            ):
                state.postwork_status = "interrupted"
                state.status = "interrupted"
                changed = True
            if changed:
                state.revision += 1
                self._save(state)
            return state.model_copy(deep=True)

    def postwork_begin(self, sweep_id: str) -> bool:
        with self._lock:
            state = self._loaded(sweep_id)
            if (
                state.ended_at is None
                or state.status == "interrupted"
                or state.postwork_status != "not_started"
            ):
                return False
            state.status = "processing"
            state.postwork_status = "running"
            state.postwork_stage = "vision"
            state.postwork_started_at = _utcnow()
            state.postwork_finished_at = None
            state.revision += 1
            self._save(state)
            return True

    def postwork_advance(self, sweep_id: str, stage: str) -> bool:
        with self._lock:
            state = self._loaded(sweep_id)
            if stage not in ("vision", "reading", "pricing", "packet"):
                raise StoreError(422, "invalid postwork stage")
            if state.postwork_status != "running":
                return False
            state.postwork_stage = stage
            state.revision += 1
            self._save(state)
            return True

    def postwork_finish(
        self, sweep_id: str, status: str, timings: dict | None = None
    ) -> SweepState:
        with self._lock:
            state = self._loaded(sweep_id)
            if status not in ("completed", "failed", "timed_out", "interrupted"):
                raise StoreError(422, "invalid postwork status")
            if state.postwork_status == status:
                if state.postwork_finished_at is None:
                    state.postwork_finished_at = _utcnow()
                    if timings is not None:
                        state.processing_timings_ms = dict(timings)
                    state.revision += 1
                    self._save(state)
                return state.model_copy(deep=True)
            downgrade = (
                state.postwork_status == "completed" and status == "failed"
            )
            if state.postwork_status != "running" and not downgrade:
                raise StoreError(409, "postwork is not running")
            state.postwork_status = status
            state.postwork_stage = None
            state.postwork_finished_at = _utcnow()
            if timings is not None:
                state.processing_timings_ms = dict(timings)
            if status == "interrupted":
                state.status = "interrupted"
            else:
                state.status = "partial"
                state.processing_complete = True
            state.revision += 1
            self._save(state)
            return state.model_copy(deep=True)
