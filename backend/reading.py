import asyncio
import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import httpx
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from preflight.vision import usage_cost

from .models import ReadingStageRecord, ReviewItem
from .sweeps import StoreError, SweepStore


def load_reading_contract(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def usage_cost_adapter(contract: dict) -> dict:
    rates = contract["cost_evidence"]
    return {
        "model": contract["model"],
        "cost_evidence": {
            "standard_input_usd_per_million": rates["input_usd_per_million"],
            "cached_input_usd_per_million": rates["cached_input_usd_per_million"],
            "output_usd_per_million": rates["output_usd_per_million"],
        },
    }


def extract_crop_views(image: np.ndarray, bbox, selection: dict) -> dict:
    img_h, img_w = image.shape[:2]
    pad_fraction = float(selection["crop_padding_fraction_each_axis"])
    pad_x = bbox["w"] * pad_fraction
    pad_y = bbox["h"] * pad_fraction
    x0 = max(0, int(np.floor(bbox["x"] - pad_x)))
    y0 = max(0, int(np.floor(bbox["y"] - pad_y)))
    x1 = min(img_w, int(np.ceil(bbox["x"] + bbox["w"] + pad_x)))
    y1 = min(img_h, int(np.ceil(bbox["y"] + bbox["h"] + pad_y)))
    if x1 <= x0 or y1 <= y0:
        raise StoreError(422, "crop region is empty after clipping")
    crop = image[y0:y1, x0:x1]
    max_edge = int(selection["maximum_crop_long_edge_px"])
    long_edge = max(crop.shape[:2])
    if long_edge > max_edge:
        scale = max_edge / long_edge
        crop = cv2.resize(
            crop,
            (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    views = {}
    for view in selection["views_per_spine"]:
        if view == "original":
            rotated = crop
        elif view == "rotate_90_clockwise":
            rotated = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
        elif view == "rotate_90_counterclockwise":
            rotated = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
        else:
            continue
        ok, buf = cv2.imencode(".jpg", rotated)
        if not ok:
            raise StoreError(500, "crop encoding failed")
        views[view] = buf.tobytes()
    return views


def build_reading_payload(contract: dict, batch: list[dict]) -> dict:
    config = contract["request_configuration"]
    content = [
        {
            "type": "input_text",
            "text": "Read the supplied labelled spine crops. ref_ids: "
            + json.dumps([entry["ref_id"] for entry in batch]),
        }
    ]
    for entry in batch:
        for view in contract["selection"]["views_per_spine"]:
            encoded = base64.b64encode(entry["views"][view]).decode()
            content.append(
                {
                    "type": "input_text",
                    "text": f"{entry['ref_id']} {view}",
                }
            )
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{encoded}",
                    "detail": config["image_detail"],
                }
            )
    return {
        "model": contract["model"],
        "instructions": contract["instructions"],
        "input": [{"role": "user", "content": content}],
        "reasoning": config["reasoning"],
        "max_output_tokens": config["max_output_tokens"],
        "store": config["store"],
        "text": {"format": contract["response_format"]},
    }


def parse_reading_response(response: dict) -> dict:
    if not isinstance(response, dict):
        raise StoreError(502, "crop reading response malformed")
    if response.get("status") != "completed":
        raise StoreError(502, "crop reading response not completed")
    outputs = response.get("output")
    if not isinstance(outputs, list):
        raise StoreError(502, "crop reading response malformed")
    texts = []
    for message in outputs:
        if not isinstance(message, dict) or message.get("type") != "message":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            raise StoreError(502, "crop reading response malformed")
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise StoreError(502, "crop reading provider refused")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if len(texts) != 1:
        raise StoreError(502, "crop reading response malformed")
    try:
        parsed = json.loads(texts[0])
    except ValueError:
        raise StoreError(502, "crop reading response malformed") from None
    if not isinstance(parsed, dict):
        raise StoreError(502, "crop reading response malformed")
    return parsed


class _WireReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_id: str = Field(min_length=1)
    title: str
    author: str
    publisher: str
    edition: str
    isbn: str
    title_legible: bool
    author_legible: bool
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    visible_text: list[str]

    @field_validator("title_legible", "author_legible", mode="before")
    @classmethod
    def _strict_bool(cls, value):
        if not isinstance(value, bool):
            raise ValueError("must be a boolean")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _strict_number(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        return value


class _WireResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    readings: list[_WireReading]


def validate_readings(parsed: dict, ref_ids: list[str]) -> list[dict]:
    if not isinstance(parsed, dict):
        raise StoreError(502, "crop reading response malformed")
    try:
        wire = _WireResponse.model_validate(parsed)
    except ValidationError:
        raise StoreError(502, "crop reading response malformed") from None
    seen = set()
    cleaned = []
    for entry in wire.readings:
        if entry.ref_id not in ref_ids:
            raise StoreError(502, "crop reading returned unknown ref_id")
        if entry.ref_id in seen:
            raise StoreError(502, "crop reading returned duplicate ref_id")
        seen.add(entry.ref_id)
        cleaned.append(entry.model_dump())
    missing = [ref_id for ref_id in ref_ids if ref_id not in seen]
    if missing:
        raise StoreError(502, "crop reading omitted supplied ref_ids")
    return cleaned


def _best_evidence(book):
    scored = [e for e in book.evidence if e.sharpness is not None]
    if scored:
        return max(scored, key=lambda e: e.sharpness)
    return book.evidence[0]


class ReadingManager:
    def __init__(self, contract: dict, clock=time.monotonic, wallclock=None):
        self.contract = contract
        self._clock = clock
        self._wallclock = wallclock or (lambda: datetime.now(timezone.utc))
        selection = contract["selection"]
        self._max_batches = int(selection["maximum_batches_per_sweep"])
        self._deadline_s = float(selection["shared_postwork_deadline_s"])
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(int(selection["maximum_concurrent_calls"]))
        self._deadlines: dict[str, float] = {}
        self._busy: dict[str, set] = {}

    def _persisted_remaining(self, iso) -> float:
        try:
            wall_deadline = datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            return 0.0
        if wall_deadline.tzinfo is None or wall_deadline.utcoffset() is None:
            return 0.0
        return (wall_deadline - self._wallclock()).total_seconds()

    def remaining_seconds(self, store: SweepStore, sweep_id: str) -> float:
        state = store.get(sweep_id)
        return self._persisted_remaining(state.reading_deadline_at)

    async def deadline(self, store: SweepStore, sweep_id: str, state=None) -> float:
        async with self._lock:
            existing = self._deadlines.get(sweep_id)
            if existing is not None:
                return existing
            state = store.get(sweep_id)
            iso = state.reading_deadline_at
            if iso is None:
                candidate = self._wallclock() + timedelta(seconds=self._deadline_s)
                if state.ended_at:
                    try:
                        ended = datetime.fromisoformat(state.ended_at)
                        if ended.tzinfo is None:
                            ended = ended.replace(tzinfo=timezone.utc)
                        candidate = min(
                            candidate,
                            ended + timedelta(seconds=self._deadline_s),
                        )
                    except (TypeError, ValueError):
                        pass
                iso = store.init_reading_deadline(sweep_id, candidate.isoformat())
            remaining = max(0.0, self._persisted_remaining(iso))
            value = self._clock() + remaining
            self._deadlines[sweep_id] = value
            return value

    def _used_batches(self, state) -> int:
        return sum(
            1
            for stage in state.reading_stages
            if stage.status in ("completed", "failed", "interrupted", "running")
        )

    async def plan(self, store: SweepStore, sweep_id: str, batches: list) -> tuple:
        wanted = {book.id for batch in batches for book in batch}
        async with self._lock:
            state = store.get(sweep_id)
            used = self._used_batches(state)
            allowed = max(0, self._max_batches - used)
            busy = self._busy.setdefault(sweep_id, set())
            if busy & wanted:
                raise StoreError(409, "spine reading already in progress")
            busy.update(wanted)
            planned = batches[:allowed]
            skipped = [b.id for batch in batches[allowed:] for b in batch]
            reservations = []
            for batch in planned:
                index = store.record_reading_stage(
                    sweep_id,
                    ReadingStageRecord(
                        ref_ids=[b.id for b in batch],
                        status="running",
                    ),
                )
                reservations.append(index)
            if skipped:
                store.append_reviews(
                    sweep_id,
                    [
                        ReviewItem(
                            ref_id=ref_id,
                            reason="not read: crop reading batch budget exhausted",
                        )
                        for ref_id in skipped
                    ],
                )
            return planned, reservations, skipped, wanted

    async def release(self, sweep_id: str, wanted: set) -> None:
        async with self._lock:
            busy = self._busy.get(sweep_id)
            if busy is not None:
                busy.difference_update(wanted)
                if not busy:
                    del self._busy[sweep_id]


async def _default_provider(payload: dict, client: httpx.AsyncClient, api_key: str, contract: dict) -> dict:
    timeout = float(contract["request_configuration"]["timeout_s"])
    response = await client.post(
        contract["endpoint"],
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    if not 200 <= response.status_code < 300:
        raise StoreError(502, "crop reading provider rejected request")
    return response.json()


async def read_spines(
    store: SweepStore,
    sweep_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    contract: dict,
    ref_ids: list[str] | None = None,
    provider=None,
    manager: ReadingManager | None = None,
    clock=time.monotonic,
) -> dict:
    mgr = manager or ReadingManager(contract, clock=clock)
    clock = mgr._clock
    selection = contract["selection"]
    state = store.get(sweep_id)
    books = [b for b in state.books if not b.excluded]
    if ref_ids is not None:
        wanted = set(ref_ids)
        known = {b.id for b in state.books}
        unknown = sorted(wanted - known)
        if unknown:
            raise StoreError(404, "unknown reading target")
        books = [b for b in books if b.id in wanted]
    else:
        if not state.books:
            raise StoreError(
                409,
                "no recorded book tracks; analyze a saved frame before reading spines",
            )
        books = [b for b in books if b.status != "identified"]
    if not books:
        return {
            "readings": [],
            "ref_ids": [],
            "batches": 0,
            "skipped": True,
            "skipped_ref_ids": [],
        }
    if not api_key and provider is None:
        raise StoreError(503, "OPENAI_API_KEY not configured")
    call_provider = provider or _default_provider
    deadline = await mgr.deadline(store, sweep_id, state)
    timeout_s = float(contract["request_configuration"]["timeout_s"])
    per_batch = int(selection["maximum_crops_per_batch"])
    batches = [books[i : i + per_batch] for i in range(0, len(books), per_batch)]
    planned, reservations, skipped_refs, wanted = await mgr.plan(
        store, sweep_id, batches
    )
    try:
        if not planned:
            raise StoreError(429, "crop reading batch budget exhausted")
        all_readings: list[dict] = []
        stage_refs: list[tuple[list[str], str]] = []

        async def run_batch(batch, stage_index):
            ref_ids_batch = [b.id for b in batch]
            crops = []
            sources = {}
            raw = None
            started = clock()
            try:
                for book in batch:
                    ev = _best_evidence(book)
                    jpeg = store.frame_path(sweep_id, ev.frame_id).read_bytes()
                    image = cv2.imdecode(
                        np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if image is None:
                        raise StoreError(422, "undecodable frame evidence")
                    views = extract_crop_views(
                        image,
                        {"x": ev.bbox.x, "y": ev.bbox.y, "w": ev.bbox.w, "h": ev.bbox.h},
                        selection,
                    )
                    crop_refs = []
                    for view, payload in views.items():
                        name = f"crop_{book.id}_{view}_{uuid.uuid4().hex}.jpg"
                        crop_refs.append(store.persist_crop(sweep_id, name, payload))
                    crops.append({"ref_id": book.id, "views": views})
                    sources[book.id] = {
                        "frame_id": ev.frame_id,
                        "bbox": {
                            "x": ev.bbox.x,
                            "y": ev.bbox.y,
                            "w": ev.bbox.w,
                            "h": ev.bbox.h,
                        },
                        "crop_refs": crop_refs,
                    }
                    store.register_crops(sweep_id, {book.id: crop_refs})
                payload = build_reading_payload(contract, crops)
                async with mgr._semaphore:
                    remaining = max(
                        0.0,
                        min(
                            deadline - clock(),
                            mgr.remaining_seconds(store, sweep_id),
                        ),
                    )
                    if remaining <= 0:
                        raise StoreError(429, "crop reading shared deadline exceeded")
                    raw = await asyncio.wait_for(
                        call_provider(payload, client, api_key, contract)
                        if provider is None
                        else call_provider(payload),
                        timeout=min(timeout_s, remaining),
                    )
                parsed = parse_reading_response(raw) if provider is None else raw
                readings = validate_readings(parsed, ref_ids_batch)
            except StoreError as exc:
                elapsed_ms = (clock() - started) * 1000.0
                cost = None
                stage_model = None
                usage = None
                if isinstance(raw, dict):
                    stage_model = raw.get("model")
                    usage = raw.get("usage")
                    estimate = usage_cost(raw, usage_cost_adapter(contract))
                    if estimate is not None:
                        cost = format(estimate, "f")
                ref = store.persist_stage(
                    sweep_id,
                    f"reading_{uuid.uuid4().hex}_{uuid.uuid4().hex}.json",
                    json.dumps(
                        {
                            "status": "failed",
                            "ref_ids": ref_ids_batch,
                            "error": exc.detail,
                            "elapsed_ms": elapsed_ms,
                            "raw_response": raw,
                            "usage": usage,
                            "model": stage_model,
                            "cost_usd_estimate": cost,
                            "cost_evidence": contract["cost_evidence"],
                            "sources": sources,
                        },
                        default=str,
                    ).encode(),
                )
                store.update_reading_stage(
                    sweep_id,
                    stage_index,
                    ReadingStageRecord(
                        ref_ids=ref_ids_batch,
                        status="failed",
                        persisted_ref=ref,
                        model=stage_model,
                        cost_usd_estimate=cost,
                        elapsed_ms=elapsed_ms,
                        error=exc.detail,
                    ),
                )
                raise
            except Exception as exc:
                elapsed_ms = (clock() - started) * 1000.0
                ref = store.persist_stage(
                    sweep_id,
                    f"reading_{uuid.uuid4().hex}_{uuid.uuid4().hex}.json",
                    json.dumps(
                        {
                            "status": "failed",
                            "ref_ids": ref_ids_batch,
                            "error": "crop reading provider unavailable",
                            "elapsed_ms": elapsed_ms,
                            "raw_response": raw,
                            "sources": sources,
                        },
                        default=str,
                    ).encode(),
                )
                store.update_reading_stage(
                    sweep_id,
                    stage_index,
                    ReadingStageRecord(
                        ref_ids=ref_ids_batch,
                        status="failed",
                        persisted_ref=ref,
                        model=None,
                        cost_usd_estimate=None,
                        elapsed_ms=elapsed_ms,
                        error="crop reading provider unavailable",
                    ),
                )
                raise StoreError(502, "crop reading provider unavailable") from exc
            elapsed_ms = (clock() - started) * 1000.0
            cost = None
            if isinstance(raw, dict):
                estimate = usage_cost(raw, usage_cost_adapter(contract))
                if estimate is not None:
                    cost = format(estimate, "f")
            ref = store.persist_stage(
                sweep_id,
                f"reading_{uuid.uuid4().hex}_{uuid.uuid4().hex}.json",
                json.dumps(
                    {
                        "status": "completed",
                        "ref_ids": ref_ids_batch,
                        "readings": readings,
                        "raw_response": raw,
                        "usage": raw.get("usage") if isinstance(raw, dict) else None,
                        "model": contract["model"],
                        "cost_usd_estimate": cost,
                        "cost_evidence": contract["cost_evidence"],
                        "elapsed_ms": elapsed_ms,
                        "sources": sources,
                    },
                    default=str,
                ).encode(),
            )
            store.update_reading_stage(
                sweep_id,
                stage_index,
                ReadingStageRecord(
                    ref_ids=ref_ids_batch,
                    status="completed",
                    persisted_ref=ref,
                    model=contract["model"],
                    cost_usd_estimate=cost,
                    elapsed_ms=elapsed_ms,
                    error=None,
                ),
            )
            stage_refs.append((ref_ids_batch, ref))
            return readings

        results = await asyncio.gather(
            *(run_batch(batch, index) for batch, index in zip(planned, reservations)),
            return_exceptions=True,
        )
        stage_by_ref: dict[str, str] = {}
        first_error = None
        for batch, group in zip(planned, results):
            if isinstance(group, Exception):
                first_error = group
                continue
            all_readings.extend(group)
        for batch_refs, ref in stage_refs:
            for ref_id in batch_refs:
                stage_by_ref[ref_id] = ref
        final = store.apply_readings(sweep_id, stage_by_ref, all_readings)
        if first_error is not None:
            raise first_error
        return {
            "readings": all_readings,
            "ref_ids": [r["ref_id"] for r in all_readings],
            "batches": len(planned),
            "stage_refs": [ref for _, ref in stage_refs],
            "skipped": False,
            "skipped_ref_ids": skipped_refs,
            "revision": final.revision,
        }
    finally:
        await mgr.release(sweep_id, wanted)
