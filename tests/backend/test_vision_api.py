import asyncio
import base64
import contextlib
import hashlib
import json
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import server
from backend.models import CorrectionIntent
from backend.sweeps import StoreError
from backend.vision import VisionManager

ORIGIN = "http://localhost:8001"
TEST_KEY = "sk-phase2a-vision-fixture"
CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "backend" / "vision_contract.json").read_text()
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as c:
        yield c


def post(client, path, origin=ORIGIN, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if origin is not None:
        headers["origin"] = origin
    return client.post(path, headers=headers, **kwargs)


def start_sweep(client, country="AE", currency="USD", device="vision-test"):
    return post(
        client,
        "/api/sweeps",
        json={"country": country, "currency": currency, "device": device},
    )


def tiny_jpeg(width=16, height=12):
    return cv2.imencode(".jpg", np.zeros((height, width, 3), np.uint8))[1].tobytes()


def upload_frame(client, sweep_id, body=None, ts="1.0", origin=ORIGIN):
    return post(
        client,
        f"/api/sweeps/{sweep_id}/frames?ts={ts}",
        content=body if body is not None else tiny_jpeg(),
        headers={"content-type": "image/jpeg"},
        origin=origin,
    )


def scene():
    return {
        "books": [{
            "bbox": {"left": 4, "top": 2, "right": 12, "bottom": 10},
            "orientation": "upright", "observed_title": "Test Volume",
            "observed_author": "Test Author", "observed_publisher": "",
            "observed_edition": "", "observed_isbn": "", "title_legible": True,
            "author_legible": True, "existence_confidence": 0.95,
            "text_confidence": 0.95, "special_flags": [], "visible_evidence": "visible printed spine",
        }],
        "items": [], "unresolved_groups": [], "capture_issues": [],
    }


def provider_response(payload=None, status="completed"):
    return {
        "id": "resp_fixture", "model": CONTRACT["model"], "status": status,
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload if payload is not None else scene())}]}],
        "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 100}, "output_tokens": 100},
    }


def stub_provider(app, handler):
    requests = []

    async def record(request):
        requests.append(request)
        return await asyncio.to_thread(handler, request)

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(record))
        try:
            yield http
        finally:
            await http.aclose()

    app.dependency_overrides[server.get_http_client] = override_client
    return requests


def analyze(client, sweep_id, body=None, origin=ORIGIN):
    return post(
        client,
        f"/api/sweeps/{sweep_id}/analyze_frame",
        json=body if body is not None else {},
        origin=origin,
    )


def upload_and_id(client, sweep_id, ts="1.0", body=None):
    resp = upload_frame(client, sweep_id, body, ts=ts)
    assert resp.status_code == 200, resp.text
    return resp.json()["frame_id"]


def test_analyze_frame_returns_observation_and_persists_stage(client, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    requests = stub_provider(client.app, lambda req: httpx.Response(200, json=provider_response()))
    state = start_sweep(client).json()
    frame_id = upload_and_id(client, state["id"], body=tiny_jpeg(16, 12))
    resp = analyze(client, state["id"], {"frame_id": frame_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["frame_id"] == frame_id
    assert data["source_frame_id"] == frame_id
    assert data["cached"] is False
    assert data["model"] == CONTRACT["model"]
    assert data["cost_usd_estimate"] == "0.003775"
    assert data["usage"]["input_tokens"] == 1000
    assert isinstance(data["elapsed_ms"], float) and data["elapsed_ms"] >= 0
    assert data["observation"]["books"][0]["observed_title"] == "Test Volume"
    assert data["box_mapping"]["boxes"] == [[4, 2, 8, 8]]
    assert data["box_mapping"]["accepted_indices"] == [0]
    assert data["coordinate_system"] == "pixels"
    assert TEST_KEY not in resp.text
    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert stored["latest_observation"]["frame_id"] == frame_id
    assert stored["latest_observation"]["source_frame_id"] == frame_id
    assert stored["latest_observation"]["cached"] is False
    stages = stored["vision_stages"]
    assert len(stages) == 1 and stages[0]["status"] == "completed"
    stage_file = tmp_path / "runs" / state["id"] / stages[0]["persisted_ref"]
    stage = json.loads(stage_file.read_text())
    assert stage["status"] == "completed"
    assert stage["raw_response"]["usage"]["input_tokens"] == 1000
    assert stage["scene"]["books"][0]["observed_title"] == "Test Volume"
    assert stage["frame_id"] == frame_id
    assert stage["coordinate_system"] == "pixels"
    assert len(requests) == 1


def test_failures_persist_sanitized_stage_without_observation(client, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    good = upload_and_id(client, state["id"], ts="1.0", body=tiny_jpeg(16, 12))
    bad = upload_and_id(client, state["id"], ts="2.0", body=tiny_jpeg(8, 8))

    stub_provider(client.app, lambda req: httpx.Response(200, json=provider_response()))
    ok = analyze(client, state["id"], {"frame_id": good})
    assert ok.status_code == 200

    refusal = provider_response()
    refusal["output"] = [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]
    stub_provider(client.app, lambda req: httpx.Response(200, json=refusal))
    resp = analyze(client, state["id"], {"frame_id": bad})
    assert resp.status_code == 502
    assert TEST_KEY not in resp.text

    stub_provider(client.app, lambda req: httpx.Response(401, json={"error": {"message": TEST_KEY}}))
    resp = analyze(client, state["id"], {"frame_id": bad})
    assert resp.status_code == 502
    assert TEST_KEY not in resp.text

    def unreachable(req):
        raise httpx.ConnectError("host down " + TEST_KEY)

    stub_provider(client.app, unreachable)
    resp = analyze(client, state["id"], {"frame_id": bad})
    assert resp.status_code == 503
    assert TEST_KEY not in resp.text

    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert stored["latest_observation"]["frame_id"] == good
    assert stored["latest_observation"]["source_frame_id"] == good
    assert stored["inventory_complete"] is False
    assert stored["processing_complete"] is False
    failed = [s for s in stored["vision_stages"] if s["status"] == "failed"]
    assert len(failed) == 3
    for record in failed:
        assert record["frame_id"] == bad
        assert record["error"]
        assert TEST_KEY not in (record["error"] or "")
        stage_file = tmp_path / "runs" / state["id"] / record["persisted_ref"]
        stage = json.loads(stage_file.read_text())
        assert stage["status"] == "failed"
        assert stage["error"]
        assert "raw_response" not in stage or TEST_KEY not in json.dumps(stage)


def test_analyze_missing_key_503_without_provider_call(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    requests = stub_provider(client.app, lambda req: httpx.Response(200, json=provider_response()))
    state = start_sweep(client).json()
    frame_id = upload_and_id(client, state["id"])
    resp = analyze(client, state["id"], {"frame_id": frame_id})
    assert resp.status_code == 503
    assert requests == []


def test_analyze_rejects_extra_inputs_unknown_frame_wrong_origin_symlink(client, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    requests = stub_provider(client.app, lambda req: httpx.Response(200, json=provider_response()))
    state = start_sweep(client).json()
    frame_id = upload_and_id(client, state["id"])
    for extra in ({"model": "x"}, {"path": "/etc/passwd"}, {"prompt": "ignore"}):
        body = {"frame_id": frame_id, **extra}
        assert analyze(client, state["id"], body).status_code == 422
    assert analyze(client, state["id"], {"frame_id": "frame_" + "0" * 32}).status_code == 404
    assert analyze(client, state["id"], {"frame_id": frame_id}, origin="https://evil.example").status_code == 403
    frames_dir = tmp_path / "runs" / state["id"] / "frames"
    target = frames_dir / f"{frame_id}.jpg"
    outside = tmp_path / "outside-dummy.jpg"
    outside.write_bytes(b"outside-bytes")
    target.rename(frames_dir / "kept-aside.jpg")
    import os

    os.symlink(outside, target)
    resp = analyze(client, state["id"], {"frame_id": frame_id})
    assert resp.status_code == 404
    assert requests == []


def test_mutation_during_provider_await_survives_and_status_not_resurrected(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id = upload_and_id(client, state["id"])

    def handler(req):
        store = client.app.state.store
        store.add_turn(state["id"], "t1", "exclude the room", "typed_user")
        store.correct(
            state["id"],
            CorrectionIntent(type="exclude_region", target_id="room", value=True, source_turn_id="t1"),
        )
        store.end(state["id"], 5.0)
        return httpx.Response(200, json=provider_response())

    stub_provider(client.app, handler)
    resp = analyze(client, state["id"], {"frame_id": frame_id})
    assert resp.status_code == 200
    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert stored["regions"] == [{"id": "room", "excluded": True}]
    assert len(stored["corrections"]) == 1
    assert stored["status"] == "partial"
    assert stored["latest_observation"]["frame_id"] == frame_id
    assert stored["ended_at"]


def test_identical_frame_bytes_reuse_cached_observation(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    requests = stub_provider(client.app, lambda req: httpx.Response(200, json=provider_response()))
    state = start_sweep(client).json()
    payload = tiny_jpeg(16, 12)
    first = upload_and_id(client, state["id"], ts="1.0", body=payload)
    second = upload_and_id(client, state["id"], ts="2.0", body=payload)
    first_reply = analyze(client, state["id"], {"frame_id": first}).json()
    second_reply = analyze(client, state["id"], {"frame_id": second})
    assert second_reply.status_code == 200
    data = second_reply.json()
    assert data["cached"] is True
    assert data["frame_id"] == second
    assert data["source_frame_id"] == first
    assert data["stage_ref"] == first_reply["stage_ref"]
    assert data["cost_usd_estimate"] == first_reply["cost_usd_estimate"]
    assert len(requests) == 1
    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert stored["latest_observation"]["frame_id"] == second
    assert stored["latest_observation"]["cached"] is True
    cached_stages = [s for s in stored["vision_stages"] if s["status"] == "cached"]
    assert len(cached_stages) == 1
    assert cached_stages[0]["cost_usd_estimate"] == "0"
    assert cached_stages[0]["persisted_ref"] == first_reply["stage_ref"]
    paid = [s for s in stored["vision_stages"] if s["status"] == "completed"]
    assert len(paid) == 1
    total = sum(float(s["cost_usd_estimate"]) for s in stored["vision_stages"] if s["cost_usd_estimate"])
    assert total == float(first_reply["cost_usd_estimate"])


def test_cached_reanalysis_across_unrelated_frame_uses_source_evidence(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    scene_b = {
        "books": [{
            "bbox": {"left": 0, "top": 0, "right": 4, "bottom": 6},
            "orientation": "upright", "observed_title": "Other Volume",
            "observed_author": "", "observed_publisher": "",
            "observed_edition": "", "observed_isbn": "", "title_legible": True,
            "author_legible": False, "existence_confidence": 0.95,
            "text_confidence": 0.9, "special_flags": [], "visible_evidence": "spine",
        }],
        "items": [], "unresolved_groups": [], "capture_issues": [],
    }
    responses = iter([provider_response(), provider_response(scene_b)])
    requests = stub_provider(
        client.app, lambda req: httpx.Response(200, json=next(responses))
    )
    state = start_sweep(client).json()
    sid = state["id"]
    bytes_a = tiny_jpeg(16, 12)
    fa = upload_and_id(client, sid, ts="1.0", body=bytes_a)
    assert analyze(client, sid, {"frame_id": fa}).status_code == 200
    fb = upload_and_id(client, sid, ts="2.0", body=tiny_jpeg(8, 8))
    assert analyze(client, sid, {"frame_id": fb}).status_code == 200
    assert len(client.get(f"/api/sweeps/{sid}").json()["books"]) == 2

    fc = upload_and_id(client, sid, ts="3.0", body=bytes_a)
    resp = analyze(client, sid, {"frame_id": fc})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cached"] is True
    assert data["source_frame_id"] == fa
    assert len(requests) == 2
    stored = client.get(f"/api/sweeps/{sid}").json()
    assert len(stored["books"]) == 2


def test_same_frame_cached_reanalysis_keeps_ids(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    requests = stub_provider(
        client.app, lambda req: httpx.Response(200, json=provider_response())
    )
    state = start_sweep(client).json()
    sid = state["id"]
    payload = tiny_jpeg(16, 12)
    fa = upload_and_id(client, sid, ts="1.0", body=payload)
    assert analyze(client, sid, {"frame_id": fa}).status_code == 200
    books = client.get(f"/api/sweeps/{sid}").json()["books"]
    assert len(books) == 1
    resp = analyze(client, sid, {"frame_id": fa})
    assert resp.status_code == 200
    assert resp.json()["cached"] is True
    assert resp.json()["source_frame_id"] == fa
    assert len(requests) == 1
    stored = client.get(f"/api/sweeps/{sid}").json()
    assert len(stored["books"]) == 1
    assert stored["books"][0]["id"] == books[0]["id"]


def test_manager_bounds_concurrency_calls_and_post_sweep_work():
    async def scenario():
        now = [0.0]

        async def fake_sleep(seconds):
            now[0] += seconds

        active = [0]
        peak = [0]
        calls = []
        skips = []

        async def fake_analyze(sweep_id, frame_id, client):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            calls.append(frame_id)
            await asyncio.sleep(0)
            active[0] -= 1
            return {"cached": False}

        manager = VisionManager(
            analyze=fake_analyze,
            contract=CONTRACT,
            enabled=True,
            record_skip=lambda sid, fid, reason: skips.append((fid, reason)),
            clock=lambda: now[0],
            sleep=fake_sleep,
        )
        for index in range(30):
            manager.notify_frame("sweep", f"frame_{index}", f"sha{index}")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        await manager.drain()
        limits = CONTRACT["runtime_limits"]
        assert len(calls) <= limits["maximum_calls_per_sweep"]
        assert peak[0] <= limits["maximum_concurrent_vision_calls"]
        assert any("budget" in reason for _, reason in skips)

        post_calls = [0]

        async def counting(sweep_id, frame_id, client):
            post_calls[0] += 1
            return {"cached": False}

        skips.clear()
        manager2 = VisionManager(
            analyze=counting,
            contract=CONTRACT,
            enabled=True,
            record_skip=lambda sid, fid, reason: skips.append((fid, reason)),
            clock=lambda: now[0],
            sleep=fake_sleep,
        )
        manager2.notify_ended("sweep")
        for index in range(10):
            manager2.notify_frame("sweep", f"frame_{index}", f"sha{index}")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        await manager2.drain()
        assert post_calls[0] <= limits["maximum_post_sweep_calls"]
        assert any("budget" in reason or "deadline" in reason for _, reason in skips)

    asyncio.run(scenario())


@contextlib.asynccontextmanager
async def _nullclient():
    yield object()


def test_queued_manual_analyzes_bounded_by_call_budget():
    async def scenario():
        now = [0.0]

        async def fake_sleep(seconds):
            now[0] += seconds

        new_calls = []

        async def fake_analyze(sweep_id, frame_id, client):
            new_calls.append(frame_id)
            await asyncio.sleep(0)
            return {"cached": False}

        skips = []
        manager = VisionManager(
            analyze=fake_analyze,
            contract=CONTRACT,
            enabled=True,
            record_skip=lambda sid, fid, reason: skips.append(reason),
            client_factory=_nullclient,
            clock=lambda: now[0],
            sleep=fake_sleep,
        )
        results = await asyncio.gather(
            *[manager.analyze_now("sweep", f"frame_{i}") for i in range(30)],
            return_exceptions=True,
        )
        limit = CONTRACT["runtime_limits"]["maximum_calls_per_sweep"]
        assert len(new_calls) == limit
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(failures) == 30 - limit
        assert all(isinstance(r, StoreError) and r.status_code == 429 for r in failures)
        assert len(skips) == 30 - limit

    asyncio.run(scenario())


def test_cached_replay_succeeds_after_budget_exhausted():
    async def scenario():
        now = [0.0]

        async def fake_sleep(seconds):
            now[0] += seconds

        paid_calls = []

        async def fake_analyze(sweep_id, frame_id, client):
            if frame_id == "dup":
                return {"cached": True}
            paid_calls.append(frame_id)
            return {"cached": False}

        skips = []
        manager = VisionManager(
            analyze=fake_analyze,
            contract=CONTRACT,
            enabled=True,
            record_skip=lambda sid, fid, reason: skips.append((fid, reason)),
            client_factory=_nullclient,
            clock=lambda: now[0],
            sleep=fake_sleep,
            sha_for=lambda sid, fid: "shaA" if fid == "dup" else None,
        )
        manager.sha_cache("sweep")[f"{CONTRACT['version']}:shaA"] = {"source_frame_id": "orig"}
        limit = CONTRACT["runtime_limits"]["maximum_calls_per_sweep"]
        for index in range(limit):
            await manager.analyze_now("sweep", f"frame_{index}", client=object())
        assert len(paid_calls) == limit
        with pytest.raises(StoreError):
            await manager.analyze_now("sweep", "frame_new", client=object())
        reply = await manager.analyze_now("sweep", "dup", client=object())
        assert reply["cached"] is True
        assert len(paid_calls) == limit

    asyncio.run(scenario())


def test_post_sweep_call_capped_by_remaining_time_budget():
    async def scenario():
        now = [0.0]
        finished = []
        started = []

        async def fake_sleep(seconds):
            now[0] += seconds

        async def slow_analyze(sweep_id, frame_id, client):
            started.append(frame_id)
            await asyncio.sleep(60)
            finished.append(frame_id)
            return {"cached": False}

        skips = []
        manager = VisionManager(
            analyze=slow_analyze,
            contract=CONTRACT,
            enabled=True,
            record_skip=lambda sid, fid, reason: skips.append((fid, reason)),
            client_factory=_nullclient,
            clock=lambda: now[0],
            sleep=fake_sleep,
        )
        manager.notify_ended("sweep")
        budget = CONTRACT["runtime_limits"]["post_sweep_time_budget_s"]
        manager._jobs["sweep"]["post_seconds"] = budget - 0.2
        t0 = time.monotonic()
        with pytest.raises(StoreError):
            await manager.analyze_now("sweep", "frame_late", client=object())
        assert time.monotonic() - t0 < 10
        assert started == ["frame_late"]
        assert finished == []
        assert skips and "budget" in skips[-1][1]

    asyncio.run(scenario())


def test_scheduler_does_not_block_frame_upload(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    analyzed = []

    async def fake_analyze(sweep_id, frame_id, client):
        analyzed.append(frame_id)
        await asyncio.sleep(0.3)
        return {"cached": False}

    manager = VisionManager(analyze=fake_analyze, contract=CONTRACT, enabled=True)
    client.app.state.vision = manager
    state = start_sweep(client).json()
    started = time.monotonic()
    first = upload_frame(client, state["id"], ts="1.0")
    second = upload_frame(client, state["id"], ts="2.0")
    elapsed = time.monotonic() - started
    assert first.status_code == 200
    assert second.status_code == 200
    assert elapsed < 0.3
    deadline = time.monotonic() + 2
    while not analyzed and time.monotonic() < deadline:
        time.sleep(0.05)
    assert analyzed


def test_vision_disabled_leaves_frames_unprocessed(client, monkeypatch):
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    analyzed = []

    async def fake_analyze(sweep_id, frame_id, client):
        analyzed.append(frame_id)
        return {"cached": False}

    manager = VisionManager(analyze=fake_analyze, contract=CONTRACT, enabled=False)
    client.app.state.vision = manager
    state = start_sweep(client).json()
    assert upload_frame(client, state["id"], ts="1.0").status_code == 200
    time.sleep(0.3)
    assert analyzed == []


def test_remaining_budget_shares_call_counters():
    async def scenario():
        now = [0.0]

        async def fake_sleep(seconds):
            now[0] += seconds

        async def fake_analyze(sweep_id, frame_id, client):
            return {"cached": False}

        manager = VisionManager(
            analyze=fake_analyze,
            contract=CONTRACT,
            enabled=True,
            record_skip=lambda *args: None,
            client_factory=_nullclient,
            clock=lambda: now[0],
            sleep=fake_sleep,
        )
        budget = manager.remaining_budget("sweep")
        assert budget == {
            "calls_left": 24,
            "post_calls_left": 6,
            "seconds_left": 180.0,
        }
        for index in range(3):
            await manager.analyze_now("sweep", f"frame_{index}", client=object())
        budget = manager.remaining_budget("sweep")
        assert budget["calls_left"] == 21
        manager.notify_ended("sweep")
        await manager.analyze_now("sweep", "frame_post", client=object())
        budget = manager.remaining_budget("sweep")
        assert budget["calls_left"] == 20
        assert budget["post_calls_left"] == 5
        assert 0.0 <= budget["seconds_left"] <= 180.0

    asyncio.run(scenario())


def test_set_postwork_deadline_only_tightens_and_gates_calls():
    async def scenario():
        now = [0.0]

        async def fake_sleep(seconds):
            now[0] += seconds

        calls = []

        async def fake_analyze(sweep_id, frame_id, client):
            calls.append(frame_id)
            return {"cached": False}

        skips = []
        manager = VisionManager(
            analyze=fake_analyze,
            contract=CONTRACT,
            enabled=True,
            record_skip=lambda sid, fid, reason: skips.append((fid, reason)),
            client_factory=_nullclient,
            clock=lambda: now[0],
            sleep=fake_sleep,
        )
        manager.notify_ended("sweep")
        manager.set_postwork_deadline("sweep", 50.0)
        assert manager.remaining_budget("sweep")["seconds_left"] == 50.0
        await manager.analyze_now("sweep", "frame_1", client=object())
        assert calls == ["frame_1"]
        manager.set_postwork_deadline("sweep", 30.0)
        manager.set_postwork_deadline("sweep", 90.0)
        assert manager.remaining_budget("sweep")["seconds_left"] == 30.0
        now[0] = 40.0
        with pytest.raises(StoreError) as exc:
            await manager.analyze_now("sweep", "frame_2", client=object())
        assert exc.value.status_code == 429
        assert "cutoff" in skips[-1][1]
        assert calls == ["frame_1"]

    asyncio.run(scenario())


def test_set_postwork_deadline_rejects_nonfinite_input():
    manager = VisionManager(
        analyze=lambda *a, **k: None,
        contract=CONTRACT,
        enabled=True,
        client_factory=_nullclient,
    )
    manager.notify_ended("sweep")
    for bad in (float("nan"), float("inf"), float("-inf"), "10", True):
        with pytest.raises(ValueError):
            manager.set_postwork_deadline("sweep", bad)
    assert manager.remaining_budget("sweep")["seconds_left"] >= 0.0


def test_groq_mode_missing_key_never_calls_openai(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        requests = stub_provider(
            app, lambda request: httpx.Response(200, json=provider_response())
        )
        sweep_id = start_sweep(groq_client).json()["id"]
        frame_id = upload_and_id(groq_client, sweep_id)
        reply = analyze(groq_client, sweep_id, {"frame_id": frame_id})
        assert reply.status_code == 503
        assert requests == []
        stages = app.state.store.get(sweep_id).vision_stages
        assert any(
            stage.frame_id == frame_id
            and stage.status == "failed"
            and stage.provider == "groq"
            and stage.persisted_ref
            for stage in stages
        )


def test_invalid_inventory_provider_rejected_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "unknown-provider")
    with pytest.raises(ValueError, match="unsupported inventory provider"):
        with TestClient(server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)):
            pass


def test_default_provider_remains_openai(tmp_path, monkeypatch):
    monkeypatch.delenv("INVENTORY_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as openai_client:
        requests = stub_provider(
            app, lambda request: httpx.Response(200, json=provider_response())
        )
        sweep_id = start_sweep(openai_client).json()["id"]
        frame_id = upload_and_id(openai_client, sweep_id)
        assert analyze(openai_client, sweep_id, {"frame_id": frame_id}).status_code == 200
        assert [request.url.host for request in requests] == ["api.openai.com"]


def test_groq_scene_stage_and_cache_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        requests = stub_provider(
            app,
            lambda request: httpx.Response(
                200,
                json={
                    "id": "groq_response_1",
                    "model": "qwen/qwen3.8-27b",
                    "choices": [{
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(scene())},
                    }],
                    "usage": {"prompt_tokens": 2100, "completion_tokens": 100, "total_tokens": 2200},
                },
            ),
        )
        sweep_id = start_sweep(groq_client).json()["id"]
        first = upload_and_id(groq_client, sweep_id)
        sha = hashlib.sha256(tiny_jpeg()).hexdigest()
        app.state.vision.sha_cache(sweep_id)[f"{CONTRACT['version']}:{sha}"] = {"wrong_provider": True}
        reply = analyze(groq_client, sweep_id, {"frame_id": first})
        assert reply.status_code == 200, reply.text
        assert [request.url.host for request in requests] == ["api.groq.com"]
        payload = json.loads(requests[0].content)
        assert payload["model"] == "qwen/qwen3.8-27b"
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert set(payload["response_format"]["json_schema"]) == {"name", "strict", "schema"}
        images = [part for part in payload["messages"][1]["content"] if part["type"] == "image_url"]
        assert len(images) == 1
        assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        state = app.state.store.get(sweep_id)
        stage = state.vision_stages[-1]
        assert stage.status == "completed"
        assert stage.provider == "groq"
        assert stage.model == "qwen/qwen3.8-27b"
        raw_stage = json.loads(app.state.store.stage_path(sweep_id, stage.persisted_ref).read_text())
        assert raw_stage["response_id"] == "groq_response_1"
        assert raw_stage["usage"]["prompt_tokens"] == 2100
        assert raw_stage["image_width"] == 16
        assert raw_stage["sent_image_width"] == 16
        second = upload_and_id(groq_client, sweep_id, ts="2.0")
        cached = analyze(groq_client, sweep_id, {"frame_id": second})
        assert cached.status_code == 200
        assert cached.json()["cached"] is True
        assert len(requests) == 1
        assert app.state.store.get(sweep_id).vision_stages[-1].provider == "groq"


@pytest.mark.parametrize("variant,expected_status", [
    ("rate_limit", 429),
    ("refusal", 502),
    ("malformed", 502),
    ("wrong_model", 502),
    ("incomplete", 502),
])
def test_groq_scene_rejects_unusable_provider_output(tmp_path, monkeypatch, variant, expected_status):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    raw = {
        "id": "groq_response_bad",
        "model": "qwen/qwen3.8-27b",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(scene())},
        }],
    }
    if variant == "refusal":
        raw["choices"][0]["message"] = {"role": "assistant", "refusal": "fixture refusal"}
    elif variant == "malformed":
        raw["choices"][0]["message"]["content"] = "not-json"
    elif variant == "wrong_model":
        raw["model"] = "unexpected-model"
    elif variant == "incomplete":
        raw["choices"][0]["finish_reason"] = "length"
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        requests = stub_provider(
            app,
            lambda request: httpx.Response(429 if variant == "rate_limit" else 200, json=raw),
        )
        sweep_id = start_sweep(groq_client).json()["id"]
        frame_id = upload_and_id(groq_client, sweep_id)
        reply = analyze(groq_client, sweep_id, {"frame_id": frame_id})
        assert reply.status_code == expected_status
        assert [request.url.host for request in requests] == ["api.groq.com"]
        state = app.state.store.get(sweep_id)
        assert all(stage.status != "completed" for stage in state.vision_stages)
        assert any(stage.status == "failed" and stage.provider == "groq" for stage in state.vision_stages)


def test_groq_scene_downscales_only_the_sent_image(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    original = tiny_jpeg(width=1800, height=900)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        requests = stub_provider(
            app,
            lambda request: httpx.Response(200, json={
                "id": "groq_large_image",
                "model": "qwen/qwen3.8-27b",
                "choices": [{
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(scene())},
                }],
            }),
        )
        sweep_id = start_sweep(groq_client).json()["id"]
        frame_id = upload_and_id(groq_client, sweep_id, body=original)
        reply = analyze(groq_client, sweep_id, {"frame_id": frame_id})
        assert reply.status_code == 200, reply.text
        assert (reply.json()["image_width"], reply.json()["sent_image_width"]) == (1800, 1600)
        assert (reply.json()["image_height"], reply.json()["sent_image_height"]) == (900, 800)
        payload = json.loads(requests[0].content)
        limits = payload["response_format"]["json_schema"]["schema"]["$defs"]["box"]["properties"]
        assert (limits["right"]["maximum"], limits["bottom"]["maximum"]) == (1600, 800)
        image_part = next(part for part in payload["messages"][1]["content"] if part["type"] == "image_url")
        sent = base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1])
        decoded = cv2.imdecode(np.frombuffer(sent, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape[:2] == (800, 1600)
        assert app.state.store.frame_path(sweep_id, frame_id).read_bytes() == original


def test_groq_scene_rate_limit_records_retry_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        requests = stub_provider(
            app,
            lambda request: httpx.Response(
                429,
                headers={"retry-after": "15", "x-ratelimit-remaining-tokens": "0"},
                json={"error": "quota reached"},
            ),
        )
        sweep_id = start_sweep(groq_client).json()["id"]
        frame_id = upload_and_id(groq_client, sweep_id)
        reply = analyze(groq_client, sweep_id, {"frame_id": frame_id})
        assert reply.status_code == 429
        assert len(requests) == 1
        stage = app.state.store.get(sweep_id).vision_stages[-1]
        assert stage.status == "failed" and stage.provider == "groq"
        assert "retry-after=15" in stage.error
        assert "remaining-tokens=0" in stage.error


def test_groq_rate_limit_blocks_immediate_repeat_on_another_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        requests = stub_provider(
            app,
            lambda request: httpx.Response(
                429, headers={"retry-after": "30"}, json={"error": "quota reached"}
            ),
        )
        sweep_id = start_sweep(groq_client).json()["id"]
        first = upload_and_id(groq_client, sweep_id)
        second = upload_and_id(groq_client, sweep_id, body=tiny_jpeg(width=32), ts="2.0")
        assert analyze(groq_client, sweep_id, {"frame_id": first}).status_code == 429
        assert analyze(groq_client, sweep_id, {"frame_id": second}).status_code == 429
        assert len(requests) == 1
        stage = app.state.store.get(sweep_id).vision_stages[-1]
        assert stage.frame_id == second
        assert stage.status == "skipped"
        assert stage.provider == "groq"
        assert "rate limit" in stage.error


def test_groq_key_is_not_exposed_by_browser_routes(tmp_path, monkeypatch):
    marker = "groq-test-only-secret-marker"
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", marker)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        for route in ("/", "/api/health", "/api/capture_config"):
            reply = groq_client.get(route)
            assert reply.status_code == 200
            assert marker not in reply.text
