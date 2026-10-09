import asyncio
import json
import time
import uuid

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import server
from backend.models import ReviewItem, VisionStageRecord
from backend.pricing import PricingManager
from backend.processing import PostSweepProcessor
from backend.reading import ReadingManager
from backend.sweeps import StoreError, SweepStore

ORIGIN = "http://localhost:8001"
TERMINAL = {"completed", "failed", "timed_out", "interrupted"}


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def scene_image(seed=7):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 255, (360, 640, 3), dtype=np.uint8)


def _frame(store, sweep_id, ts=1.0):
    return store.add_frame(sweep_id, jpeg(scene_image()), ts)


def _book_payload(frame_id, x=40):
    return {
        "frame_ref": f"frames/{frame_id}.jpg",
        "status": "unidentified",
        "bbox": {"x": x, "y": 40, "w": 60, "h": 200},
        "image_width": 640,
        "image_height": 360,
        "observed_fragments": [],
        "evidence": [
            {
                "frame_id": frame_id,
                "frame_ref": f"frames/{frame_id}.jpg",
                "bbox": {"x": x, "y": 40, "w": 60, "h": 200},
                "image_width": 640,
                "image_height": 360,
                "sharpness": 10.0,
            }
        ],
        "potential_duplicate": False,
        "track_histogram": None,
    }


def _item_payload(frame_id):
    return {
        "category": "shelving",
        "description": "wooden shelf",
        "material": "wood",
        "frame_ref": f"frames/{frame_id}.jpg",
        "status": "range",
        "valuation_state": "unpriced",
        "bbox": {"x": 0, "y": 0, "w": 640, "h": 360},
        "image_width": 640,
        "image_height": 360,
        "evidence": [
            {
                "frame_id": frame_id,
                "frame_ref": f"frames/{frame_id}.jpg",
                "bbox": {"x": 0, "y": 0, "w": 640, "h": 360},
                "image_width": 640,
                "image_height": 360,
                "sharpness": 9.0,
            }
        ],
        "track_histogram": None,
    }


def _tracks(store, sweep_id, frame_id, books=0, items=0):
    return store.commit_tracks(
        sweep_id,
        frame_id,
        {
            "frame_id": frame_id,
            "alignment": "none",
            "assignments": [],
            "applied_props": [],
            "new_books": [
                _book_payload(frame_id, x=40 + 80 * i) for i in range(books)
            ],
            "new_items": [_item_payload(frame_id) for _ in range(items)],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 0,
        },
    )


def _vision_stage_record(store, sweep_id, frame_id, status, error=None):
    ref = store.persist_stage(
        sweep_id,
        f"vision_{frame_id[6:]}_{uuid.uuid4().hex}.json",
        json.dumps(
            {"frame_id": frame_id, "status": status, "error": error}
        ).encode(),
    )
    store.record_observation(
        sweep_id,
        frame_id,
        {
            "stage": VisionStageRecord(
                frame_id=frame_id,
                status=status,
                persisted_ref=ref,
                model="fake-model",
                cost_usd_estimate="0",
                elapsed_ms=1.0,
                error=error,
            ),
            "observation": None,
        },
    )
    return ref


def _completed_vision_stage(store, sweep_id, frame_id):
    ref = store.persist_stage(
        sweep_id,
        f"vision_{frame_id[6:]}_{uuid.uuid4().hex}.json",
        json.dumps(
            {"frame_id": frame_id, "status": "completed"}
        ).encode(),
    )
    store.record_observation(
        sweep_id,
        frame_id,
        {
            "stage": VisionStageRecord(
                frame_id=frame_id,
                status="completed",
                persisted_ref=ref,
                model="test-model",
                cost_usd_estimate="0.001",
                elapsed_ms=12.0,
                error=None,
            ),
            "observation": None,
        },
    )


def null_client_factory():
    def factory():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, text="stub transport")
            )
        )

    return factory


class FakeVision:
    downstream_reserve_s = 60.0

    def __init__(
        self,
        error=None,
        delay=0.0,
        store=None,
        max_calls=24,
        max_post_calls=6,
        post_budget_s=180.0,
        fail_frames=None,
        blocking=None,
    ):
        self.drained = []
        self.analyzed = []
        self.error = error
        self.delay = delay
        self.store = store
        self.max_calls = max_calls
        self.max_post_calls = max_post_calls
        self.post_budget_s = post_budget_s
        self.fail_frames = dict(fail_frames or {})
        self.blocking = blocking
        self.calls = 0
        self.post_calls = 0
        self.post_seconds = 0.0
        self.deadlines = {}

    async def drain_sweep(self, sweep_id):
        self.drained.append(sweep_id)

    def remaining_budget(self, sweep_id):
        return {
            "calls_left": max(0, self.max_calls - self.calls),
            "post_calls_left": max(0, self.max_post_calls - self.post_calls),
            "seconds_left": max(0.0, self.post_budget_s - self.post_seconds),
        }

    def set_postwork_deadline(self, sweep_id, deadline_mono):
        current = self.deadlines.get(sweep_id)
        self.deadlines[sweep_id] = (
            deadline_mono if current is None else min(current, deadline_mono)
        )

    def _record_stage(self, sweep_id, frame_id, status, error=None):
        if self.store is None:
            return
        ref = self.store.persist_stage(
            sweep_id,
            f"vision_{frame_id[6:]}_{uuid.uuid4().hex}.json",
            json.dumps(
                {"frame_id": frame_id, "status": status, "error": error}
            ).encode(),
        )
        self.store.record_observation(
            sweep_id,
            frame_id,
            {
                "stage": VisionStageRecord(
                    frame_id=frame_id,
                    status=status,
                    persisted_ref=ref,
                    model="fake-model",
                    cost_usd_estimate="0",
                    elapsed_ms=1.0,
                    error=error,
                ),
                "observation": None,
            },
        )

    async def analyze_now(self, sweep_id, frame_id, *, client=None):
        deadline = self.deadlines.get(sweep_id)
        if deadline is not None and time.monotonic() >= deadline:
            self._record_stage(
                sweep_id, frame_id, "skipped", "post-sweep vision cutoff reached"
            )
            raise StoreError(429, "post-sweep vision cutoff reached")
        if self.calls >= self.max_calls:
            self._record_stage(
                sweep_id, frame_id, "skipped", "vision call budget exhausted"
            )
            raise StoreError(429, "vision call budget exhausted")
        if self.post_calls >= self.max_post_calls:
            self._record_stage(
                sweep_id, frame_id, "skipped", "post-sweep call budget exhausted"
            )
            raise StoreError(429, "post-sweep call budget exhausted")
        self.analyzed.append((sweep_id, frame_id))
        self.calls += 1
        self.post_calls += 1
        if self.blocking is not None:
            await self.blocking.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
            self.post_seconds += self.delay
        injected = self.fail_frames.get(frame_id)
        if isinstance(injected, Exception):
            self._record_stage(sweep_id, frame_id, "failed", str(injected))
            raise injected
        reason = injected
        if reason is None and self.error is not None:
            reason = str(getattr(self.error, "detail", self.error))
        if reason is not None:
            self._record_stage(sweep_id, frame_id, "failed", reason)
            raise StoreError(502, reason)
        self._record_stage(sweep_id, frame_id, "completed")
        return {"status": "completed"}


async def _skipped_reader(*args, **kwargs):
    return {"skipped": True}


async def _noop_pricer(*args, **kwargs):
    return {"valuations": {}}


def make_processor(store, *, vision=None, reader=None, pricer=None, deadline_s=240.0):
    return PostSweepProcessor(
        store=store,
        vision=vision or FakeVision(),
        reading=ReadingManager(server.READING_CONTRACT),
        pricing=PricingManager(server.PRICING_CONTRACT),
        reader=lambda: (reader or _skipped_reader),
        pricer=pricer or _noop_pricer,
        resolver=lambda: None,
        api_key=lambda: None,
        client_factory=null_client_factory(),
        pricing_client_factory=null_client_factory(),
        deadline_s=deadline_s,
    )


def run(coro):
    return asyncio.run(coro)


def review_reasons(store, sweep_id):
    return [r.reason for r in store.get(sweep_id).review_queue]


def post(client, path, origin=ORIGIN, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if origin is not None:
        headers["origin"] = origin
    return client.post(path, headers=headers, **kwargs)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as c:
        yield c


def api_sweep(client):
    resp = post(
        client,
        "/api/sweeps",
        json={"country": "AE", "currency": "USD", "device": "processing-test"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def wait_postwork(client, sweep_id, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = client.get(f"/api/sweeps/{sweep_id}").json()
        if state["postwork_status"] in TERMINAL:
            return state
        time.sleep(0.02)
    raise AssertionError(
        "postwork did not settle: " + state["postwork_status"]
    )


def test_end_launches_postwork_and_publishes_settled_packet(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = store.add_frame(sweep_id, jpeg(scene_image()), 1.0)
    vision = FakeVision()
    client.app.state.postwork.vision = vision

    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "partial"
    assert body["ended_at"]
    assert body["duration_s"] == 2.0
    assert body["postwork_status"] == "not_started"
    assert body["packet"] is None

    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert settled["status"] == "partial"
    assert settled["processing_complete"] is True
    assert settled["postwork_started_at"]
    assert settled["postwork_finished_at"]
    assert settled["postwork_stage"] is None
    timings = settled["processing_timings_ms"]
    assert isinstance(timings["post_sweep_vision_ms"], (int, float))
    assert isinstance(timings["post_sweep_work_ms"], (int, float))
    assert vision.drained == [sweep_id]
    assert vision.analyzed == [(sweep_id, frame.id)]

    assert settled["packet_ref"] == "claim_packet.json"
    packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
    assert packet.status_code == 200
    assert packet.headers["x-packet-stale"] == "false"
    metrics = packet.json()["metrics"]
    assert metrics["post_sweep_vision_ms"] == timings["post_sweep_vision_ms"]
    assert metrics["post_sweep_work_ms"] == timings["post_sweep_work_ms"]
    assert metrics["book_identification_ms"] is None
    assert metrics["pricing_ms"] is None
    assert metrics["post_sweep_total_ms"] is None
    assert metrics["report_generation_ms"] is None
    report = client.get(f"/api/sweeps/{sweep_id}/report.html")
    assert report.status_code == 200


def test_postwork_reconciles_repeated_scene_before_reading_and_packet(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    first = _frame(store, sweep_id, ts=1.0)
    _tracks(store, sweep_id, first.id, books=5)
    second = _frame(store, sweep_id, ts=2.0)
    _tracks(store, sweep_id, second.id, books=5)
    assert len(store.get(sweep_id).books) == 10
    client.app.state.postwork.vision = FakeVision()
    observed = []

    async def reader(store_, sid, **kwargs):
        observed.append(len(store_.get(sid).books))
        return {"skipped": True}

    client.app.state.postwork.reader = lambda: reader
    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    assert resp.status_code == 200
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert observed == [5]
    assert len(settled["books"]) == 5
    assert len({book["id"] for book in settled["books"]}) == 5
    assert len(settled["unresolved_books"]) == 5
    packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
    assert packet.status_code == 200
    assert packet.json()["totals"]["book_count"] == 5
    assert len(packet.json()["unresolved_book_observations"]) == 5
    assert all("cannot verify as distinct" in entry["review_reason"] for entry in packet.json()["unresolved_book_observations"])
    report = client.get(f"/api/sweeps/{sweep_id}/report.html")
    assert report.status_code == 200
    unresolved_section = report.text.split("Unresolved book observations", 1)[1].split("<h2>Items", 1)[0]
    assert packet.json()["unresolved_book_observations"][0]["id"] in unresolved_section
    assert 'href="frames/' in unresolved_section


def test_distinct_supported_views_are_not_silently_combined(tmp_path):
    from backend.scene_reconciliation import reconcile_scene_books

    store = SweepStore(tmp_path / "runs")
    sweep = store.start("AE", "AED", "separate views")
    for seed, start in ((17, 1.0), (23, 3.0)):
        picture = jpeg(scene_image(seed))
        for offset in (0.0, 1.0):
            frame = store.add_frame(sweep.id, picture, start + offset)
            _tracks(store, sweep.id, frame.id, books=2)
    store.end(sweep.id, 4.0)
    before = store.get(sweep.id)
    result = reconcile_scene_books(store, sweep.id, server.TRACKING_CONTRACT)
    after = store.get(sweep.id)
    assert result["supported_scenes"] == 2
    assert len(after.books) == len(before.books) == 8
    assert after.unresolved_books == []
    assert after.scene_reconciliation is None


def test_manual_model_routes_reject_concurrent_automatic_postwork(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = _frame(store, sweep_id)
    _tracks(store, sweep_id, frame.id, books=1)
    client.app.state.postwork.vision = FakeVision()
    release = asyncio.Event()

    async def blocked_reader(*args, **kwargs):
        await release.wait()
        return {"skipped": True}

    client.app.state.postwork.reader = lambda: blocked_reader
    assert post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0}).status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.get(f"/api/sweeps/{sweep_id}").json()
        if state["postwork_status"] == "running" and state["postwork_stage"] == "reading":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("reading stage did not start")
    paths = (
        ("analyze_frame", {"frame_id": frame.id}),
        ("read_spines", {"ref_ids": None}),
        ("price_books", {"book_ids": None}),
    )
    try:
        for path, payload in paths:
            resp = post(client, f"/api/sweeps/{sweep_id}/{path}", json=payload)
            assert resp.status_code == 409, (path, resp.status_code)
    finally:
        release.set()
        wait_postwork(client, sweep_id)


def test_end_unknown_and_duplicate_runs_one_job(client):
    assert (
        post(client, "/api/sweeps/" + "0" * 32 + "/end", json={"duration_s": 1.0}).status_code
        == 404
    )
    sweep_id = api_sweep(client)
    vision = FakeVision()
    client.app.state.postwork.vision = vision
    first = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    assert first.status_code == 200
    settled = wait_postwork(client, sweep_id)
    again = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 9.0})
    assert again.status_code == 200
    state = client.get(f"/api/sweeps/{sweep_id}").json()
    assert state["postwork_finished_at"] == settled["postwork_finished_at"]
    assert vision.drained == [sweep_id]
    assert state["postwork_status"] == "completed"


def test_empty_sweep_skips_stages_with_review_reasons(client):
    sweep_id = api_sweep(client)
    vision = FakeVision()
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    reasons = review_reasons(client.app.state.store, sweep_id)
    assert any("post-sweep vision skipped: no saved frames" in r for r in reasons)
    assert any("post-sweep reading skipped" in r for r in reasons)
    assert any("post-sweep pricing skipped" in r for r in reasons)
    assert vision.drained == [sweep_id]
    assert vision.analyzed == []
    assert client.get(f"/api/sweeps/{sweep_id}/claim_packet.json").status_code == 200


def test_item_only_sweep_skips_reading_without_spurious_409(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = store.add_frame(sweep_id, jpeg(scene_image()), 1.0)
    _tracks(store, sweep_id, frame.id, books=0, items=1)
    client.app.state.postwork.vision = FakeVision()

    calls = []

    async def reader(*args, **kwargs):
        calls.append((args, kwargs))
        return {"skipped": True}

    client.app.state.postwork.reader = lambda: reader
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert calls == []
    reasons = review_reasons(store, sweep_id)
    assert any("post-sweep reading skipped" in r for r in reasons)
    assert not any("no recorded book tracks" in r and "skipped:" not in r for r in reasons)
    assert client.get(f"/api/sweeps/{sweep_id}/claim_packet.json").status_code == 200


def test_stage_failure_still_publishes_partial_packet(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = store.add_frame(sweep_id, jpeg(scene_image()), 1.0)
    _tracks(store, sweep_id, frame.id, books=1)
    client.app.state.postwork.vision = FakeVision()

    async def failing_reader(*args, **kwargs):
        raise StoreError(502, "reading provider unreachable")

    client.app.state.postwork.reader = lambda: failing_reader
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "failed"
    assert settled["status"] == "partial"
    reasons = review_reasons(store, sweep_id)
    assert any("post-sweep reading failed: reading provider unreachable" in r for r in reasons)
    packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
    assert packet.status_code == 200
    assert packet.json()["status"] == "partial"


def test_429_stage_busy_records_skip_not_failure(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = store.add_frame(sweep_id, jpeg(scene_image()), 1.0)
    _tracks(store, sweep_id, frame.id, books=1)
    client.app.state.postwork.vision = FakeVision()

    async def busy_reader(*args, **kwargs):
        raise StoreError(429, "post-sweep time budget exhausted")

    client.app.state.postwork.reader = lambda: busy_reader
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    reasons = review_reasons(store, sweep_id)
    assert any("post-sweep reading skipped: post-sweep time budget exhausted" in r for r in reasons)
    assert client.get(f"/api/sweeps/{sweep_id}/claim_packet.json").status_code == 200


def test_completed_vision_stage_skips_fresh_analyze(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = store.add_frame(sweep_id, jpeg(scene_image()), 1.0)
    _completed_vision_stage(store, sweep_id, frame.id)
    vision = FakeVision()
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert vision.drained == [sweep_id]
    assert vision.analyzed == []
    manifest = settled["coverage_manifest"]
    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert entry["frame_id"] == frame.id
    assert entry["processing_status"] == "completed"
    assert entry["stage_refs"]


def test_interrupt_cancels_running_postwork_without_packet(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = store.add_frame(sweep_id, jpeg(scene_image()), 1.0)
    _tracks(store, sweep_id, frame.id, books=1)
    client.app.state.postwork.vision = FakeVision()
    release = asyncio.Event()

    async def blocked_reader(*args, **kwargs):
        await release.wait()
        return {"skipped": True}

    client.app.state.postwork.reader = lambda: blocked_reader
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = client.get(f"/api/sweeps/{sweep_id}").json()
        if state["postwork_status"] == "running":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("postwork never started")
    resp = post(client, f"/api/sweeps/{sweep_id}/interrupt")
    assert resp.status_code == 200
    settled = wait_postwork(client, sweep_id)
    release.set()
    assert settled["status"] == "interrupted"
    assert settled["postwork_status"] == "interrupted"
    assert settled["postwork_finished_at"]
    assert settled["packet_ref"] is None
    assert client.get(f"/api/sweeps/{sweep_id}/claim_packet.json").status_code == 404
    assert client.get(f"/api/sweeps/{sweep_id}/report.html").status_code == 404


def test_shared_monotonic_deadline_times_out_stage(tmp_path):
    async def scenario():
        store = SweepStore(tmp_path / "runs")
        state = store.start("AE", "USD", "deadline-test")
        frame = _frame(store, state.id)
        _tracks(store, state.id, frame.id, books=1)
        store.end(state.id, 1.0)

        async def slow_reader(*args, **kwargs):
            await asyncio.sleep(0.3)
            return {"skipped": True}

        proc = make_processor(
            store, reader=slow_reader, deadline_s=0.05
        )
        assert proc.start(state.id) is True
        await proc.drain(state.id)
        settled = store.get(state.id)
        assert settled.postwork_status == "timed_out"
        assert any(
            "post-sweep reading exceeded the shared deadline"
            in r.reason
            for r in settled.review_queue
        )
        assert settled.packet_ref == "claim_packet.json"

    run(scenario())


def test_deadline_shared_not_reset_per_stage(tmp_path):
    async def scenario():
        store = SweepStore(tmp_path / "runs")
        state = store.start("AE", "USD", "deadline-share-test")
        frame = _frame(store, state.id)
        _tracks(store, state.id, frame.id, books=1)
        store.end(state.id, 1.0)
        fake_now = [0.0]

        async def reader(*args, **kwargs):
            fake_now[0] += 0.10
            await asyncio.sleep(0.005)
            return {"skipped": True}

        async def pricer(*args, **kwargs):
            await asyncio.sleep(0.12)
            return {"valuations": {}}

        proc = PostSweepProcessor(
            store=store,
            vision=FakeVision(),
            reading=ReadingManager(server.READING_CONTRACT),
            pricing=PricingManager(server.PRICING_CONTRACT),
            reader=lambda: reader,
            pricer=pricer,
            resolver=None,
            api_key=lambda: None,
            client_factory=null_client_factory(),
            pricing_client_factory=null_client_factory(),
            clock=lambda: fake_now[0],
            deadline_s=0.15,
        )
        assert proc.start(state.id) is True
        await proc.drain(state.id)
        settled = store.get(state.id)
        assert settled.postwork_status == "timed_out"
        reasons = [r.reason for r in settled.review_queue]
        assert not any("post-sweep reading exceeded" in r for r in reasons)
        assert any("post-sweep pricing exceeded the shared deadline" in r for r in reasons)

    run(scenario())


def test_per_sweep_isolation(tmp_path):
    async def scenario():
        store = SweepStore(tmp_path / "runs")
        a = store.start("AE", "USD", "sweep-a")
        b = store.start("AE", "USD", "sweep-b")
        frame_a = _frame(store, a.id)
        frame_b = _frame(store, b.id)
        _tracks(store, a.id, frame_a.id, books=1)
        _tracks(store, b.id, frame_b.id, books=1)
        store.end(a.id, 1.0)
        store.end(b.id, 1.0)
        gate = asyncio.Event()
        seen = []

        async def reader(store_arg, sweep_id, **kwargs):
            seen.append(sweep_id)
            if sweep_id == a.id:
                await gate.wait()
            return {"skipped": True}

        vision = FakeVision()
        proc = make_processor(store, vision=vision, reader=reader)
        assert proc.start(a.id) is True
        assert proc.start(b.id) is True
        await proc.drain(b.id)
        assert store.get(b.id).postwork_status == "completed"
        assert store.get(a.id).postwork_status == "running"
        gate.set()
        await proc.drain(a.id)
        assert store.get(a.id).postwork_status == "completed"
        assert vision.drained.count(a.id) == 1
        assert vision.drained.count(b.id) == 1
        assert a.id in seen and b.id in seen

    run(scenario())


def test_cancel_is_honest_and_no_restart(tmp_path):
    async def scenario():
        store = SweepStore(tmp_path / "runs")
        state = store.start("AE", "USD", "cancel-test")
        frame = _frame(store, state.id)
        _tracks(store, state.id, frame.id, books=1)
        store.end(state.id, 1.0)
        gate = asyncio.Event()

        async def blocked(*args, **kwargs):
            await gate.wait()
            return {"skipped": True}

        proc = make_processor(store, reader=blocked)
        assert proc.start(state.id) is True
        await asyncio.sleep(0)
        deadline = time.monotonic() + 5
        while store.get(state.id).postwork_status != "running":
            if time.monotonic() > deadline:
                raise AssertionError("postwork never started")
            await asyncio.sleep(0.01)
        proc.cancel(state.id)
        await proc.drain(state.id)
        settled = store.get(state.id)
        assert settled.postwork_status == "interrupted"
        assert settled.status == "interrupted"
        assert settled.packet_ref is None
        assert proc.start(state.id) is False
        gate.set()

    run(scenario())


def test_reload_marks_running_postwork_interrupted(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "reload-test")
    store.end(state.id, 2.0)
    assert store.postwork_begin(state.id) is True
    assert store.get(state.id).postwork_status == "running"
    reopened = SweepStore(tmp_path / "runs")
    loaded = reopened.get(state.id)
    assert loaded.status == "interrupted"
    assert loaded.postwork_status == "interrupted"
    assert loaded.postwork_stage is None
    assert loaded.coverage_manifest is None
    proc = make_processor(reopened, vision=FakeVision())
    assert proc.start(state.id) is False


def test_old_state_file_loads_with_postwork_defaults(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "old-state-test")
    state_file = tmp_path / "runs" / state.id / "state.json"
    raw = json.loads(state_file.read_text())
    for key in (
        "postwork_status",
        "postwork_stage",
        "postwork_started_at",
        "postwork_finished_at",
        "processing_timings_ms",
        "coverage_manifest",
    ):
        raw.pop(key, None)
    state_file.write_text(json.dumps(raw))
    reopened = SweepStore(tmp_path / "runs")
    loaded = reopened.get(state.id)
    assert loaded.coverage_manifest is None
    assert loaded.postwork_status == "not_started"
    assert loaded.postwork_stage is None
    assert loaded.postwork_started_at is None
    assert loaded.postwork_finished_at is None
    assert loaded.processing_timings_ms == {}


def test_postwork_uses_server_dependency_overrides(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = _frame(store, sweep_id)
    _tracks(store, sweep_id, frame.id, books=1)
    _completed_vision_stage(store, sweep_id, frame.id)
    client.app.state.postwork.vision = FakeVision()

    used = {}
    stub_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(500))
    )

    async def fake_reader(store_, sid, **kwargs):
        used["sweep"] = sid
        used["client"] = kwargs.get("client")
        return {"skipped": True}

    async def override_client():
        yield stub_client

    app = client.app
    app.dependency_overrides[server.get_reader] = lambda: fake_reader
    app.dependency_overrides[server.get_http_client] = override_client
    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 0.5})
    assert resp.status_code == 200, resp.text
    try:
        settled = wait_postwork(client, sweep_id)
        assert settled["postwork_status"] == "completed"
        assert used["sweep"] == sweep_id
        assert used["client"] is stub_client
    finally:
        app.dependency_overrides.clear()
        run(stub_client.aclose())


def test_postwork_client_override_accepts_async_value(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = _frame(store, sweep_id)
    _tracks(store, sweep_id, frame.id, books=1)
    _completed_vision_stage(store, sweep_id, frame.id)
    client.app.state.postwork.vision = FakeVision()

    used = {}
    stub_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(500))
    )

    async def fake_reader(store_, sid, **kwargs):
        used["client"] = kwargs.get("client")
        return {"skipped": True}

    async def override_client():
        return stub_client

    app = client.app
    app.dependency_overrides[server.get_reader] = lambda: fake_reader
    app.dependency_overrides[server.get_http_client] = override_client
    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 0.5})
    assert resp.status_code == 200, resp.text
    try:
        settled = wait_postwork(client, sweep_id)
        assert settled["postwork_status"] == "completed"
        assert used["client"] is stub_client
    finally:
        app.dependency_overrides.clear()
        run(stub_client.aclose())


def test_postwork_orders_read_then_price_and_publishes(client):
    from tests.backend.test_packet import identify_book, price_book

    sweep_id = api_sweep(client)
    store = client.app.state.store
    frame = _frame(store, sweep_id)
    _tracks(store, sweep_id, frame.id, books=1)
    _completed_vision_stage(store, sweep_id, frame.id)
    client.app.state.postwork.vision = FakeVision()
    book_id = store.get(sweep_id).books[0].id

    order = []

    async def fake_reader(store_, sid, **kwargs):
        order.append("reading")
        identify_book(store_, sid, book_id)
        return {"skipped": False, "ref_ids": [book_id], "batches": 1}

    async def fake_pricer(store_, sid, **kwargs):
        order.append("pricing")
        price_book(store_, sid, book_id, "10.00", "4.00")
        return {"valuations": {book_id: {}}, "partial": True}

    app = client.app
    app.dependency_overrides[server.get_reader] = lambda: fake_reader
    app.state.postwork.pricer = fake_pricer
    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    assert resp.status_code == 200, resp.text
    try:
        settled = wait_postwork(client, sweep_id)
        assert settled["postwork_status"] == "completed"
        assert order == ["reading", "pricing"]

        packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
        assert packet.status_code == 200
        assert packet.headers["x-packet-stale"] == "false"
        payload = packet.json()
        assert payload["status"] == "partial"
        book = payload["books"][0]
        assert book["status"] == "identified"
        assert book["replacement_cost"]["amount"] == "10.00"
        assert book["used_value"]["amount"] == "4.00"
        assert book["replacement_cost"]["source_quote"]["url"]
        assert book["used_value"]["source_quote"]["url"]
        assert payload["totals"]["books_replacement_cost"] == "10.00"
        assert payload["totals"]["books_used_value"] == "4.00"
        frame_files = payload.get("frames") or []
        assert book["evidence"][0]["frame_ref"].startswith("frames/")
        assert all(ref.startswith("frames/") for ref in frame_files)
    finally:
        app.dependency_overrides.clear()


def _five_frames(store, sweep_id):
    return [
        store.add_frame(
            sweep_id, jpeg(scene_image(seed=100 + index)), float(index + 1)
        )
        for index in range(5)
    ]


def test_postwork_accounts_for_five_distinct_saved_views(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    vision = FakeVision(store=store)
    client.app.state.postwork.vision = vision
    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    assert resp.status_code == 200, resp.text
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert vision.analyzed == [(sweep_id, f.id) for f in frames]

    manifest = settled["coverage_manifest"]
    assert manifest["version"] == "coverage-v1"
    assert len(manifest["entries"]) == 5
    by_frame = {entry["frame_id"]: entry for entry in manifest["entries"]}
    assert list(by_frame) == [f.id for f in frames]
    for entry in manifest["entries"]:
        assert entry["disposition"] == "selected"
        assert entry["processing_status"] == "completed"
        assert entry["stage_refs"], entry
        assert entry["row_id"] is None
        assert entry["covered_range"] is None
        store.stage_path(sweep_id, entry["stage_refs"][0])
    assert manifest["spatial_coverage_status"] == "unresolved"
    packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
    assert packet.status_code == 200
    assert packet.json()["physical_book_count"] is None


def test_postwork_accounts_for_five_distinct_saved_views_after_earlier_success(
    client,
):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    _completed_vision_stage(store, sweep_id, frames[0].id)
    vision = FakeVision(store=store)
    client.app.state.postwork.vision = vision
    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    assert resp.status_code == 200, resp.text
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert vision.analyzed == [(sweep_id, f.id) for f in frames[1:]]

    manifest = settled["coverage_manifest"]
    assert len(manifest["entries"]) == 5
    first = next(
        e for e in manifest["entries"] if e["frame_id"] == frames[0].id
    )
    assert first["processing_status"] == "completed"
    assert first["stage_refs"]
    store.stage_path(sweep_id, first["stage_refs"][0])
    rest = [
        e for e in manifest["entries"] if e["frame_id"] != frames[0].id
    ]
    assert all(e["processing_status"] == "completed" for e in rest)
    assert all(e["stage_refs"] for e in rest)
    packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
    assert packet.json()["physical_book_count"] is None


def test_coverage_budget_exhaustion_preserves_remaining_views(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    _five_frames(store, sweep_id)
    vision = FakeVision(store=store, max_post_calls=2)
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert len(vision.analyzed) == 2
    manifest = settled["coverage_manifest"]
    assert len(manifest["entries"]) == 5
    completed = [
        e for e in manifest["entries"]
        if e["processing_status"] == "completed"
    ]
    assert len(completed) == 2
    remaining = [
        e for e in manifest["entries"]
        if e["processing_status"] != "completed"
    ]
    assert all(e["disposition"] == "deferred" for e in remaining)
    assert all(e["reason"] for e in remaining)
    gap_ids = {fid for g in manifest["gaps"] for fid in g["frame_ids"]}
    assert {e["frame_id"] for e in remaining} <= gap_ids
    assert (
        client.get(f"/api/sweeps/{sweep_id}/claim_packet.json").status_code
        == 200
    )


def test_failed_selected_view_does_not_hide_other_work(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    vision = FakeVision(
        store=store, fail_frames={frames[1].id: "provider exploded"}
    )
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert vision.analyzed == [(sweep_id, f.id) for f in frames]
    by_frame = {
        e["frame_id"]: e for e in settled["coverage_manifest"]["entries"]
    }
    failed = by_frame[frames[1].id]
    assert failed["processing_status"] == "failed"
    assert failed["disposition"] == "unresolved"
    assert "provider exploded" in failed["reason"]
    for frame in (frames[0], *frames[2:]):
        assert by_frame[frame.id]["processing_status"] == "completed"
        assert by_frame[frame.id]["stage_refs"]
    reasons = review_reasons(store, sweep_id)
    assert any("provider exploded" in r for r in reasons)


def test_coverage_interrupt_retains_pending_evidence(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    gate = asyncio.Event()
    vision = FakeVision(store=store, blocking=gate)
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = client.get(f"/api/sweeps/{sweep_id}").json()
        if state["postwork_status"] == "running" and state["coverage_manifest"]:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("postwork never started")
    resp = post(client, f"/api/sweeps/{sweep_id}/interrupt")
    assert resp.status_code == 200
    settled = wait_postwork(client, sweep_id)
    gate.set()
    assert settled["status"] == "interrupted"
    manifest = settled["coverage_manifest"]
    assert len(manifest["entries"]) == 5
    assert all(
        e["processing_status"] == "interrupted"
        for e in manifest["entries"]
    )
    gap_ids = {fid for g in manifest["gaps"] for fid in g["frame_ids"]}
    assert gap_ids == {f.id for f in frames}
    entries = [
        dict(e, processing_status="completed") for e in manifest["entries"]
    ]
    from backend.models import CoverageManifest

    late = CoverageManifest.model_validate({**manifest, "entries": entries})
    with pytest.raises(StoreError) as exc:
        store.record_coverage(sweep_id, late)
    assert exc.value.status_code == 409
    after = store.get(sweep_id)
    assert all(
        e.processing_status == "interrupted"
        for e in after.coverage_manifest.entries
    )


def test_duplicate_end_does_not_reanalyze_selected_views(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    vision = FakeVision(store=store)
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert vision.analyzed == [(sweep_id, f.id) for f in frames]
    again = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 9.0})
    assert again.status_code == 200
    time.sleep(0.2)
    assert vision.analyzed == [(sweep_id, f.id) for f in frames]
    state = client.get(f"/api/sweeps/{sweep_id}").json()
    assert state["postwork_finished_at"] == settled["postwork_finished_at"]


def test_vision_cutoff_defers_frames_and_completes_downstream(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    vision = FakeVision(store=store, post_budget_s=0.0)
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    manifest = settled["coverage_manifest"]
    assert len(manifest["entries"]) == 5
    assert all(
        e["disposition"] == "deferred"
        and e["processing_status"] == "skipped"
        for e in manifest["entries"]
    )
    assert vision.analyzed == []
    gap_ids = {fid for g in manifest["gaps"] for fid in g["frame_ids"]}
    assert gap_ids == {f.id for f in frames}
    assert all(g["reason"] for g in manifest["gaps"])
    reasons = review_reasons(store, sweep_id)
    assert any("coverage gap" in r for r in reasons)
    assert any("spatial" in r and "unresolved" in r for r in reasons)
    packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
    assert packet.status_code == 200
    assert packet.json()["status"] == "partial"
    assert any(
        "coverage" in r["reason"] or "spatial" in r["reason"]
        for r in packet.json()["review_queue"]
    )


def test_inflight_vision_timeout_defers_remaining_views(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    vision = FakeVision(store=store, post_budget_s=0.05, delay=0.5)
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    manifest = settled["coverage_manifest"]
    assert len(vision.analyzed) == 1
    assert all(
        e["disposition"] == "deferred"
        and e["processing_status"] == "skipped"
        for e in manifest["entries"]
    )
    gap_ids = {fid for g in manifest["gaps"] for fid in g["frame_ids"]}
    assert gap_ids == {f.id for f in frames}
    assert client.get(f"/api/sweeps/{sweep_id}/claim_packet.json").status_code == 200


def test_prior_success_survives_late_skip_and_failure(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    first_ref = _vision_stage_record(
        store, sweep_id, frames[0].id, "completed"
    )
    late_skip = _vision_stage_record(
        store, sweep_id, frames[0].id, "skipped", "post-sweep time budget exhausted"
    )
    _vision_stage_record(
        store, sweep_id, frames[1].id, "completed"
    )
    _vision_stage_record(
        store, sweep_id, frames[1].id, "failed", "provider exploded"
    )
    vision = FakeVision(store=store)
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert vision.analyzed == [(sweep_id, f.id) for f in frames[2:]]
    by_frame = {
        e["frame_id"]: e for e in settled["coverage_manifest"]["entries"]
    }
    assert by_frame[frames[0].id]["processing_status"] == "completed"
    assert first_ref in by_frame[frames[0].id]["stage_refs"]
    assert late_skip in by_frame[frames[0].id]["stage_refs"]
    assert by_frame[frames[1].id]["processing_status"] == "completed"


def test_unexpected_frame_error_marks_unresolved_and_continues(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    vision = FakeVision(
        store=store, fail_frames={frames[2].id: RuntimeError("driver boom")}
    )
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    assert vision.analyzed == [(sweep_id, f.id) for f in frames]
    by_frame = {
        e["frame_id"]: e for e in settled["coverage_manifest"]["entries"]
    }
    assert by_frame[frames[2].id]["disposition"] == "unresolved"
    assert by_frame[frames[2].id]["processing_status"] == "failed"
    assert "driver boom" in by_frame[frames[2].id]["reason"]
    others = [f for f in frames if f.id != frames[2].id]
    assert all(
        by_frame[f.id]["processing_status"] == "completed" for f in others
    )


def test_concurrent_success_stage_survives_attempt_skip(client):
    sweep_id = api_sweep(client)
    store = client.app.state.store
    frames = _five_frames(store, sweep_id)
    vision = FakeVision(store=store)
    original = vision.analyze_now

    async def analyze_with_concurrent_success(sid, frame_id, *, client=None):
        if frame_id == frames[0].id:
            vision._record_stage(sid, frame_id, "completed")
            vision._record_stage(
                sid, frame_id, "skipped", "post-sweep vision cutoff reached"
            )
            vision.analyzed.append((sid, frame_id))
            vision.calls += 1
            vision.post_calls += 1
            return {"status": "completed"}
        return await original(sid, frame_id, client=client)

    vision.analyze_now = analyze_with_concurrent_success
    client.app.state.postwork.vision = vision
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    by_frame = {
        e["frame_id"]: e for e in settled["coverage_manifest"]["entries"]
    }
    entry = by_frame[frames[0].id]
    assert entry["processing_status"] == "completed"
    assert len(entry["stage_refs"]) == 2


def test_groq_postwork_never_uses_openai_reader(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-openai")
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    requests = []

    async def override_client():
        def record(request):
            requests.append(request.url.host)
            return httpx.Response(500, json={"error": "offline fixture"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            yield client

    app.dependency_overrides[server.get_http_client] = override_client
    with TestClient(app) as groq_client:
        sweep_id = api_sweep(groq_client)
        store = app.state.store
        frame = _frame(store, sweep_id)
        _tracks(store, sweep_id, frame.id, books=1)
        _completed_vision_stage(store, sweep_id, frame.id)
        post(groq_client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
        settled = wait_postwork(groq_client, sweep_id)
        assert settled["postwork_status"] in TERMINAL
        assert requests == []
        assert any(
            stage.status == "skipped" and stage.provider == "groq"
            for stage in store.get(sweep_id).reading_stages
        )


def test_groq_failure_keeps_packet_partial_and_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-only-token")
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    requests = []

    def make_app():
        app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)

        def record(request):
            requests.append(request.url.host)
            return httpx.Response(
                429,
                headers={"retry-after": "20", "x-ratelimit-remaining-tokens": "0"},
                json={"error": "synthetic quota"},
            )

        async def override_client():
            async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
                yield client

        app.dependency_overrides[server.get_http_client] = override_client
        app.dependency_overrides[server.get_pricing_http_client] = override_client
        return app

    app = make_app()
    with TestClient(app) as groq_client:
        sweep_id = api_sweep(groq_client)
        store = app.state.store
        for index in range(3):
            store.add_frame(sweep_id, jpeg(scene_image(seed=index + 15)), float(index + 1))
        end = post(groq_client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 3.0})
        assert end.status_code == 200
        settled = wait_postwork(groq_client, sweep_id, timeout_s=30.0)
        assert settled["status"] == "partial"
        assert len(settled["coverage_manifest"]["entries"]) == 3
        assert requests == ["api.groq.com"]
        stages = store.get(sweep_id).vision_stages
        assert any(stage.status == "failed" and stage.provider == "groq" for stage in stages)
        assert all(stage.provider == "groq" for stage in stages)
        assert all(stage.status in ("failed", "skipped") for stage in stages)
        assert all(store.stage_path(sweep_id, stage.persisted_ref).is_file() for stage in stages if stage.persisted_ref)
        packet = groq_client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
        assert packet.status_code == 200
        assert packet.json()["status"] == "partial"
        assert packet.json()["physical_book_count"] is None
        assert packet.json()["books"] == []
    with TestClient(make_app()) as reopened:
        assert reopened.get(f"/api/sweeps/{sweep_id}").status_code == 200
        assert requests == ["api.groq.com"]


def test_groq_postwork_mocked_scene_and_reading_leave_prices_unverified(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-only-token")
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    requests = []
    observed = {
        "books": [{
            "bbox": {"left": 40, "top": 70, "right": 350, "bottom": 175},
            "orientation": "upright",
            "observed_title": "Clean Code",
            "observed_author": "Robert C. Martin",
            "observed_publisher": "",
            "observed_edition": "",
            "observed_isbn": "",
            "title_legible": True,
            "author_legible": True,
            "existence_confidence": 0.96,
            "text_confidence": 0.96,
            "special_flags": [],
            "visible_evidence": "printed spine title and author",
        }],
        "items": [],
        "unresolved_groups": [],
        "capture_issues": [],
    }

    def record(request):
        requests.append(request.url.host)
        payload = json.loads(request.content)
        text = payload["messages"][1]["content"][0]["text"]
        if text.startswith("ref_ids: "):
            refs = json.loads(text.removeprefix("ref_ids: "))
            content = json.dumps({"readings": [
                {
                    "ref_id": ref, "title": "Clean Code", "author": "Robert C. Martin",
                    "publisher": "", "edition": "", "isbn": "", "title_legible": True,
                    "author_legible": True, "confidence": 0.96,
                    "visible_text": ["Clean Code", "Robert C. Martin"],
                }
                for ref in refs
            ]})
        else:
            content = json.dumps(observed)
        return httpx.Response(200, json={
            "id": "groq_stage_fixture",
            "model": "qwen/qwen3.8-27b",
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }],
            "usage": {"prompt_tokens": 2048, "completion_tokens": 125},
        })

    async def override_client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            yield client

    app.dependency_overrides[server.get_http_client] = override_client
    app.dependency_overrides[server.get_pricing_http_client] = override_client
    with TestClient(app) as groq_client:
        sweep_id = api_sweep(groq_client)
        image = np.full((360, 640, 3), 255, dtype=np.uint8)
        cv2.putText(image, "Clean Code", (50, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        cv2.putText(image, "Robert C. Martin", (50, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 2)
        app.state.store.add_frame(sweep_id, jpeg(image), 1.0)
        assert post(groq_client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0}).status_code == 200
        settled = wait_postwork(groq_client, sweep_id)
        assert requests == ["api.groq.com", "api.groq.com"]
        assert len(settled["books"]) == 1
        assert settled["books"][0]["title"] == "Clean Code"
        assert settled["books"][0]["replacement_cost"] is None
        assert any("source_url_unverified" in review["reason"] for review in settled["review_queue"])
        packet = groq_client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
        assert packet.status_code == 200
        assert packet.json()["status"] == "partial"
        assert packet.json()["physical_book_count"] is None
        assert packet.json()["metrics"]["model_cost_unknown_count"] == 2
