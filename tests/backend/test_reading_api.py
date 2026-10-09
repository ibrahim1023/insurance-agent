import asyncio
import json
import os
import threading
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import server
from backend.sweeps import StoreError

ORIGIN = "http://localhost:8001"
TEST_KEY = "sk-phase2b-reading-fixture"
CONTRACT = server.READING_CONTRACT


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


def start_sweep(client, country="AE", currency="USD"):
    return post(
        client,
        "/api/sweeps",
        json={"country": country, "currency": currency, "device": "reading-test"},
    )


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def scene_image(width=640, height=360, seed=7):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 255, (height, width, 3), dtype=np.uint8)


def seed_book(client, sweep_id, count=1):
    store = client.app.state.store
    img = scene_image()
    resp = post(
        client,
        f"/api/sweeps/{sweep_id}/frames?ts=1.0",
        content=jpeg(img),
        headers={"content-type": "image/jpeg"},
    )
    frame_id = resp.json()["frame_id"]
    payloads = []
    for i in range(count):
        payloads.append(
            {
                "frame_ref": f"frames/{frame_id}.jpg",
                "status": "unidentified",
                "bbox": {"x": 40 + i * 80, "y": 40, "w": 60, "h": 200},
                "image_width": 640,
                "image_height": 360,
                "observed_fragments": [],
                "evidence": [
                    {
                        "frame_id": frame_id,
                        "frame_ref": f"frames/{frame_id}.jpg",
                        "bbox": {"x": 40 + i * 80, "y": 40, "w": 60, "h": 200},
                        "image_width": 640,
                        "image_height": 360,
                        "sharpness": 10.0,
                    }
                ],
                "potential_duplicate": False,
                "track_histogram": None,
            }
        )
    store.commit_tracks(
        sweep_id,
        frame_id,
        {
            "frame_id": frame_id,
            "alignment": "none",
            "assignments": [],
            "applied_props": [],
            "new_books": payloads,
            "new_items": [],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": count,
        },
    )
    return frame_id, [b.id for b in store.get(sweep_id).books]


def provider_response(readings, model=None, status="completed"):
    return {
        "status": status,
        "model": model or CONTRACT["model"],
        "id": "resp_reading",
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 200},
        },
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"readings": readings}),
                    }
                ],
            }
        ],
    }


def reading_for(ref_id, title="Real Title", author_legible=True):
    return {
        "ref_id": ref_id,
        "title": title,
        "author": "Some Author" if author_legible else "",
        "publisher": "",
        "edition": "",
        "isbn": "",
        "title_legible": bool(title),
        "author_legible": author_legible,
        "confidence": 0.95,
        "visible_text": [title] if title else [],
    }


def payload_ref_ids(payload):
    import re

    text = payload["input"][0]["content"][0]["text"]
    return json.loads(re.search(r"ref_ids: (\[.*\])", text).group(1))


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


def test_read_spines_endpoint_identifies_with_null_metrics(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id, ids = seed_book(client, state["id"], count=1)

    def handler(request):
        payload = json.loads(request.content.decode())
        refs = payload_ref_ids(payload)
        return httpx.Response(200, json=provider_response([reading_for(r) for r in refs]))

    requests = stub_provider(client.app, handler)
    resp = post(client, f"/api/sweeps/{state['id']}/read_spines", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skipped"] is False
    assert len(requests) == 1
    loaded = client.get(f"/api/sweeps/{state['id']}").json()
    book = loaded["books"][0]
    assert book["status"] == "identified"
    assert book["title"] == "Real Title"
    assert book["spine_height_cm"] is None
    assert book["spine_thickness_cm"] is None
    assert book["replacement_cost"] is None
    assert book["used_value"] is None
    assert book["isbn"] == ""
    assert len(book["crop_refs"]) == 3


def test_read_spines_boundary_and_unknown_refs(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id, ids = seed_book(client, state["id"])
    sid = state["id"]
    assert post(
        client, f"/api/sweeps/{sid}/read_spines", json={}, origin=None
    ).status_code == 403
    assert post(
        client,
        f"/api/sweeps/{sid}/read_spines",
        content=b"{not json",
        headers={"content-type": "application/json"},
    ).status_code == 422
    assert post(
        client,
        f"/api/sweeps/{sid}/read_spines",
        json={"ref_ids": None, "extra": 1},
    ).status_code == 422
    assert post(
        client,
        f"/api/sweeps/{sid}/read_spines",
        content=b"x" * 131073,
        headers={"content-type": "application/json"},
    ).status_code == 413
    assert post(
        client,
        f"/api/sweeps/{sid}/read_spines",
        json={"ref_ids": ["book_" + "f" * 32]},
    ).status_code == 404
    assert post(client, "/api/sweeps/" + "0" * 32 + "/read_spines", json={}).status_code == 404


def test_read_spines_missing_key_no_provider_call(client):
    state = start_sweep(client).json()
    seed_book(client, state["id"])
    requests = stub_provider(
        client.app, lambda req: httpx.Response(200, json=provider_response([]))
    )
    resp = post(client, f"/api/sweeps/{state['id']}/read_spines", json={})
    assert resp.status_code == 503
    assert requests == []


def test_contradicted_track_reselected_by_default_read(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id, ids = seed_book(client, state["id"])
    sid = state["id"]
    queued_titles = ["First Title", "Different Title", "First Title"]

    def handler(request):
        payload = json.loads(request.content.decode())
        refs = payload_ref_ids(payload)
        title = queued_titles.pop(0)
        return httpx.Response(
            200, json=provider_response([reading_for(r, title=title) for r in refs])
        )

    requests = stub_provider(client.app, handler)
    assert post(client, f"/api/sweeps/{sid}/read_spines", json={}).status_code == 200
    loaded = client.get(f"/api/sweeps/{sid}").json()
    assert loaded["books"][0]["status"] == "identified"
    assert loaded["books"][0]["title"] == "First Title"

    resp = post(client, f"/api/sweeps/{sid}/read_spines", json={"ref_ids": ids})
    assert resp.status_code == 200
    loaded = client.get(f"/api/sweeps/{sid}").json()
    assert loaded["books"][0]["status"] == "unidentified"
    assert loaded["books"][0]["title"] in (None, "")

    resp = post(client, f"/api/sweeps/{sid}/read_spines", json={})
    assert resp.status_code == 200
    assert len(requests) == 3
    loaded = client.get(f"/api/sweeps/{sid}").json()
    assert loaded["books"][0]["status"] == "identified"


def test_same_title_conflicting_author_cannot_overwrite(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id, ids = seed_book(client, state["id"])
    sid = state["id"]
    queued_authors = ["Accepted Author", "Conflicting Author"]

    def handler(request):
        payload = json.loads(request.content.decode())
        refs = payload_ref_ids(payload)
        author = queued_authors.pop(0)
        readings = []
        for r in refs:
            reading = reading_for(r, title="Same Title")
            reading["author"] = author
            readings.append(reading)
        return httpx.Response(200, json=provider_response(readings))

    stub_provider(client.app, handler)
    assert post(client, f"/api/sweeps/{sid}/read_spines", json={}).status_code == 200
    loaded = client.get(f"/api/sweeps/{sid}").json()
    book = loaded["books"][0]
    assert book["status"] == "identified"
    assert book["author"] == "Accepted Author"

    resp = post(client, f"/api/sweeps/{sid}/read_spines", json={"ref_ids": ids})
    assert resp.status_code == 200
    loaded = client.get(f"/api/sweeps/{sid}").json()
    book = loaded["books"][0]
    assert book["status"] == "unidentified"
    assert book["title"] in (None, "")
    assert book["author"] in (None, "")
    assert book["replacement_cost"] is None
    assert book["used_value"] is None
    assert len(book["identification_evidence"]) == 2
    statuses = [r["status"] for r in book["identification_evidence"]]
    assert "contradicted" in statuses
    assert any("conflict" in r["reason"] or "contradict" in r["reason"] for r in loaded["review_queue"])


def test_read_spines_provider_failure_sanitized(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    seed_book(client, state["id"])

    def handler(request):
        return httpx.Response(500, text="upstream secret detail should not leak")

    stub_provider(client.app, handler)
    resp = post(client, f"/api/sweeps/{state['id']}/read_spines", json={})
    assert resp.status_code == 502
    assert "secret" not in resp.text
    loaded = client.get(f"/api/sweeps/{state['id']}").json()
    assert all(b["status"] == "unidentified" for b in loaded["books"])
    assert loaded["reading_stages"][-1]["status"] == "failed"
    assert loaded["reading_stages"][-1]["cost_usd_estimate"] is None


def test_read_spines_incomplete_response_rejected(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id, ids = seed_book(client, state["id"])

    def handler(request):
        return httpx.Response(200, json=provider_response([], status="incomplete"))

    stub_provider(client.app, handler)
    resp = post(client, f"/api/sweeps/{state['id']}/read_spines", json={})
    assert resp.status_code == 502
    loaded = client.get(f"/api/sweeps/{state['id']}").json()
    assert loaded["reading_stages"][-1]["status"] == "failed"
    raw = (
        Path(client.app.state.store.root)
        / state["id"]
        / loaded["reading_stages"][-1]["persisted_ref"]
    )
    stage = json.loads(raw.read_text())
    assert stage["status"] == "failed"
    assert "raw_response" in stage


def test_failed_reading_stage_persists_cost_evidence(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    seed_book(client, state["id"])

    def handler(request):
        raw = provider_response([{"ref_id": "bogus", "title": "x"}])
        return httpx.Response(200, json=raw)

    stub_provider(client.app, handler)
    resp = post(client, f"/api/sweeps/{state['id']}/read_spines", json={})
    assert resp.status_code == 502
    loaded = client.get(f"/api/sweeps/{state['id']}").json()
    stage = loaded["reading_stages"][-1]
    assert stage["status"] == "failed"
    assert stage["cost_usd_estimate"] == "0.001065"
    assert stage["model"] == CONTRACT["model"]
    stage_file = (
        Path(client.app.state.store.root) / state["id"] / stage["persisted_ref"]
    )
    saved = json.loads(stage_file.read_text())
    assert saved["status"] == "failed"
    assert saved["usage"]["input_tokens"] == 1000
    assert saved["cost_usd_estimate"] == "0.001065"
    assert saved["cost_evidence"]["output_usd_per_million"]
    book = loaded["books"][0]
    assert book["status"] == "unidentified"
    assert book["title"] in (None, "")

    second = start_sweep(client).json()
    seed_book(client, second["id"])

    def no_usage(request):
        raw = provider_response([{"ref_id": "bogus"}])
        raw.pop("model", None)
        raw.pop("usage", None)
        return httpx.Response(200, json=raw)

    stub_provider(client.app, no_usage)
    resp = post(client, f"/api/sweeps/{second['id']}/read_spines", json={})
    assert resp.status_code == 502
    loaded = client.get(f"/api/sweeps/{second['id']}").json()
    stage = loaded["reading_stages"][-1]
    assert stage["status"] == "failed"
    assert stage["cost_usd_estimate"] is None
    assert stage["model"] is None


def test_crop_evidence_get_and_traversal(client, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id, ids = seed_book(client, state["id"])

    def handler(request):
        payload = json.loads(request.content.decode())
        return httpx.Response(
            200, json=provider_response([reading_for(r) for r in payload_ref_ids(payload)])
        )

    stub_provider(client.app, handler)
    resp = post(client, f"/api/sweeps/{state['id']}/read_spines", json={})
    assert resp.status_code == 200
    loaded = client.get(f"/api/sweeps/{state['id']}").json()
    crop_ref = loaded["books"][0]["crop_refs"][0]
    name = crop_ref.split("/")[-1]
    resp = client.get(f"/api/sweeps/{state['id']}/crops/{name}")
    assert resp.status_code == 200
    expected = (
        Path(client.app.state.store.root) / state["id"] / crop_ref
    ).read_bytes()
    assert resp.content == expected
    assert client.get(f"/api/sweeps/{state['id']}/crops/..%2F..%2Fstate.json").status_code == 404
    assert client.get(f"/api/sweeps/{'0' * 32}/crops/{name}").status_code == 404
    real = Path(client.app.state.store.root) / state["id"] / crop_ref
    moved = real.with_suffix(".moved")
    real.rename(moved)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"not a real crop")
    os.symlink(outside, real)
    assert client.get(f"/api/sweeps/{state['id']}/crops/{name}").status_code == 404
    real.unlink()
    moved.rename(real)


def test_frame_upload_responsive_during_blocked_read(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id, ids = seed_book(client, state["id"])
    entered = threading.Event()
    release = threading.Event()

    def handler(request):
        entered.set()
        release.wait(timeout=10)
        payload = json.loads(request.content.decode())
        return httpx.Response(
            200, json=provider_response([reading_for(r) for r in payload_ref_ids(payload)])
        )

    stub_provider(client.app, handler)
    result = {}

    def do_read():
        result["resp"] = post(
            client, f"/api/sweeps/{state['id']}/read_spines", json={}
        )

    worker = threading.Thread(target=do_read)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        started = time.monotonic()
        upload = post(
            client,
            f"/api/sweeps/{state['id']}/frames?ts=2.0",
            content=jpeg(scene_image(seed=9)),
            headers={"content-type": "image/jpeg"},
        )
        elapsed = time.monotonic() - started
        assert upload.status_code == 200
        assert elapsed < 8
        assert worker.is_alive()
    finally:
        release.set()
    worker.join(timeout=15)
    assert result["resp"].status_code == 200


def test_exclusion_during_blocked_read_preserved(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    frame_id, ids = seed_book(client, state["id"])
    sid = state["id"]
    store = client.app.state.store
    store.add_turn(sid, "t1", "exclude it", "typed_user")
    release = threading.Event()
    entered = threading.Event()

    def handler(request):
        entered.set()
        release.wait(timeout=10)
        payload = json.loads(request.content.decode())
        return httpx.Response(
            200, json=provider_response([reading_for(r) for r in payload_ref_ids(payload)])
        )

    stub_provider(client.app, handler)

    class _NoopVision:
        downstream_reserve_s = 60.0

        async def drain_sweep(self, sweep_id):
            return None

        def remaining_budget(self, sweep_id):
            return {
                "calls_left": 24,
                "post_calls_left": 6,
                "seconds_left": 180.0,
            }

        def set_postwork_deadline(self, sweep_id, deadline_mono):
            return None

        async def analyze_now(self, sweep_id, frame_id, *, client=None):
            return {"status": "completed"}

    async def _noop_stage(*args, **kwargs):
        return {"skipped": True}

    client.app.state.postwork.vision = _NoopVision()
    client.app.state.postwork.reader = lambda: _noop_stage
    client.app.state.postwork.pricer = _noop_stage
    result = {}

    def do_read():
        result["resp"] = post(client, f"/api/sweeps/{sid}/read_spines", json={})

    worker = threading.Thread(target=do_read)
    worker.start()
    assert entered.wait(timeout=15)
    corrected = post(
        client,
        f"/api/sweeps/{sid}/corrections",
        json={
            "type": "exclude_item",
            "target_id": ids[0],
            "value": True,
            "source_turn_id": "t1",
        },
    )
    assert corrected.status_code == 200
    ended = post(client, f"/api/sweeps/{sid}/end", json={"duration_s": 5.0})
    assert ended.status_code == 200
    release.set()
    worker.join(timeout=15)
    assert result["resp"].status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        loaded = client.get(f"/api/sweeps/{sid}").json()
        if loaded["postwork_status"] in (
            "completed",
            "failed",
            "timed_out",
            "interrupted",
        ):
            break
        time.sleep(0.02)
    loaded = client.get(f"/api/sweeps/{sid}").json()
    assert loaded["status"] == "partial"
    assert loaded["books"][0]["excluded"] is True
    assert loaded["books"][0]["status"] == "identified"
    assert loaded["corrections"][-1]["type"] == "exclude_item"


def test_realtime_session_matches_contract(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    answer = "v=0\r\no=- 1 1 IN IP4 10.0.0.1\r\ns=-\r\nt=0 0\r\n"
    requests = stub_provider(
        client.app,
        lambda req: httpx.Response(
            200, content=answer.encode(), headers={"content-type": "application/sdp"}
        ),
    )
    state = start_sweep(client, "AE", "USD").json()
    resp = post(
        client,
        f"/api/sweeps/{state['id']}/realtime",
        content="v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n",
        headers={"content-type": "application/sdp"},
    )
    assert resp.status_code == 200
    import re

    match = re.search(
        r'name="session"\r\n\r\n(.*?)\r\n--', requests[0].content.decode(), re.S
    )
    session = json.loads(match.group(1))
    contract_session = server.REALTIME_CONTRACT["session"]
    assert session["model"] == contract_session["model"]
    assert session["audio"] == contract_session["audio"]
    assert session["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe-2025-12-15"
    assert session["tools"] == contract_session["tools"]
    assert session["tool_choice"] == contract_session["tool_choice"]
    assert session["instructions"].startswith(contract_session["instructions"])
    assert "frame_id 'current'" in session["instructions"]
    assert "Never speak frame identifiers" in session["instructions"]
    assert "confirm the selected country and currency once" in session["instructions"]
    assert "AE is a country code, not a currency" in session["instructions"]
    assert '{"country": "AE", "currency": "USD"}' in session["instructions"]


def test_read_spines_without_book_tracks_rejected_409(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    requests = stub_provider(
        client.app, lambda req: httpx.Response(200, json=provider_response([]))
    )
    resp = post(client, f"/api/sweeps/{state['id']}/read_spines", json={})
    assert resp.status_code == 409
    assert "track" in resp.text.lower() or "analy" in resp.text.lower()
    assert requests == []


def test_read_spines_item_only_sweep_still_rejected(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    sid = state["id"]
    store = client.app.state.store
    img = scene_image(seed=11)
    resp = post(
        client,
        f"/api/sweeps/{sid}/frames?ts=1.0",
        content=jpeg(img),
        headers={"content-type": "image/jpeg"},
    )
    frame_id = resp.json()["frame_id"]
    store.commit_tracks(
        sid,
        frame_id,
        {
            "frame_id": frame_id,
            "alignment": "none",
            "assignments": [],
            "applied_props": [],
            "new_books": [],
            "new_items": [
                {
                    "category": "shelving",
                    "description": "wooden shelf",
                    "material": "",
                    "brand_model": "",
                    "frame_ref": f"frames/{frame_id}.jpg",
                    "status": "range",
                    "valuation_state": "unpriced",
                    "confidence": 0.9,
                    "bbox": {"x": 20, "y": 40, "w": 480, "h": 300},
                    "image_width": 640,
                    "image_height": 360,
                    "evidence": [
                        {
                            "frame_id": frame_id,
                            "frame_ref": f"frames/{frame_id}.jpg",
                            "bbox": {"x": 20, "y": 40, "w": 480, "h": 300},
                            "image_width": 640,
                            "image_height": 360,
                            "sharpness": 9.0,
                        }
                    ],
                    "potential_duplicate": False,
                    "track_histogram": None,
                }
            ],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 1,
        },
    )
    requests = stub_provider(
        client.app, lambda req: httpx.Response(200, json=provider_response([]))
    )
    resp = post(client, f"/api/sweeps/{sid}/read_spines", json={})
    assert resp.status_code == 409
    assert requests == []


def test_groq_reading_missing_key_never_calls_openai(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        sweep_id = start_sweep(groq_client).json()["id"]
        _, book_ids = seed_book(groq_client, sweep_id)
        requests = stub_provider(
            app,
            lambda req: httpx.Response(200, json=provider_response([reading_for(book_ids[0])])),
        )
        reply = post(groq_client, f"/api/sweeps/{sweep_id}/read_spines", json={})
        assert reply.status_code == 503
        assert requests == []
        stages = app.state.store.get(sweep_id).reading_stages
        assert any(
            stage.ref_ids == book_ids
            and stage.status == "skipped"
            and stage.provider == "groq"
            and "GROQ_API_KEY" in (stage.error or "")
            for stage in stages
        )


def test_groq_crop_batch_is_bounded_and_preserves_unknowns(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        sweep_id = start_sweep(groq_client).json()["id"]
        frame_id, ids = seed_book(groq_client, sweep_id, count=4)

        def handler(request):
            payload = json.loads(request.content)
            refs = json.loads(payload["messages"][1]["content"][0]["text"].removeprefix("ref_ids: "))
            readings = [
                reading_for(ref, title="", author_legible=False)
                if ref == ids[0] else reading_for(ref)
                for ref in refs
            ]
            return httpx.Response(200, json={
                "id": "groq_reading_fixture",
                "model": "qwen/qwen3.8-27b",
                "choices": [{
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps({"readings": readings})},
                }],
                "usage": {"prompt_tokens": 2048 * len(refs), "completion_tokens": 100},
            })

        requests = stub_provider(app, handler)
        reply = post(groq_client, f"/api/sweeps/{sweep_id}/read_spines", json={})
        assert reply.status_code == 200, reply.text
        assert reply.json()["batches"] == 2
        assert [request.url.host for request in requests] == ["api.groq.com"] * 2
        image_counts = [
            sum(part["type"] == "image_url" for part in json.loads(req.content)["messages"][1]["content"])
            for req in requests
        ]
        assert sorted(image_counts) == [1, 3]
        state = app.state.store.get(sweep_id)
        assert state.books[0].title == ""
        assert state.books[0].status == "unidentified"
        assert [book.title for book in state.books[1:]] == ["Real Title"] * 3
        assert all(len(book.crop_refs) == 3 for book in state.books)
        assert all(app.state.store.crop_path(sweep_id, Path(ref).name).is_file() for book in state.books for ref in book.crop_refs)
        completed = [stage for stage in state.reading_stages if stage.status == "completed"]
        assert len(completed) == 2
        assert all(stage.provider == "groq" and stage.model == "qwen/qwen3.8-27b" for stage in completed)
        evidence = json.loads(app.state.store.stage_path(sweep_id, completed[0].persisted_ref).read_text())
        assert evidence["model"] == "qwen/qwen3.8-27b"
        assert evidence["usage"]["prompt_tokens"] in (2048, 6144)
        assert evidence["cost_usd_estimate"] is None
        assert set(evidence["sources"]).issubset(set(ids))
        assert all(source["frame_id"] == frame_id for source in evidence["sources"].values())


@pytest.mark.parametrize("variant,status_code", [
    ("unknown_ref", 502),
    ("missing_ref", 502),
    ("malformed", 502),
    ("refusal", 502),
    ("quota", 429),
])
def test_groq_crop_rejects_untrusted_or_incomplete_reading(tmp_path, monkeypatch, variant, status_code):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        sweep_id = start_sweep(groq_client).json()["id"]
        _, refs = seed_book(groq_client, sweep_id)
        result = [reading_for(refs[0])]
        if variant == "unknown_ref":
            result[0]["ref_id"] = "book_" + "a" * 32
        elif variant == "missing_ref":
            result = []
        content = json.dumps({"readings": result}) if variant != "malformed" else "not-json"
        message = {"role": "assistant", "content": content}
        if variant == "refusal":
            message = {"role": "assistant", "refusal": "fixture refusal"}
        raw = {
            "id": "groq_crop_bad",
            "model": "qwen/qwen3.8-27b",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        }
        requests = stub_provider(
            app, lambda request: httpx.Response(429 if variant == "quota" else 200, json=raw)
        )
        reply = post(groq_client, f"/api/sweeps/{sweep_id}/read_spines", json={})
        assert reply.status_code == status_code
        assert [request.url.host for request in requests] == ["api.groq.com"]
        state = app.state.store.get(sweep_id)
        assert state.books[0].title == ""
        assert any(stage.status == "failed" and stage.provider == "groq" for stage in state.reading_stages)


def test_groq_reading_stops_after_first_quota_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-only-token")
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        sweep_id = start_sweep(groq_client).json()["id"]
        seed_book(groq_client, sweep_id, count=4)
        requests = stub_provider(
            app,
            lambda request: httpx.Response(
                429,
                headers={"retry-after": "21", "x-ratelimit-remaining-tokens": "0"},
                json={"error": "quota reached"},
            ),
        )
        reply = post(groq_client, f"/api/sweeps/{sweep_id}/read_spines", json={})
        assert reply.status_code == 429
        assert len(requests) == 1
        state = app.state.store.get(sweep_id)
        assert all(book.title == "" for book in state.books)
        assert any("retry-after=21" in (stage.error or "") for stage in state.reading_stages)
        assert any("remaining-tokens=0" in (stage.error or "") for stage in state.reading_stages)
