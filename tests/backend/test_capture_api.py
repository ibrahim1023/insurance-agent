import hashlib
import json
import math
import os
import uuid

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import server
from backend.models import (
    CoverageDecision,
    CoverageGap,
    CoverageManifest,
    VisionStageRecord,
)
from backend.sweeps import StoreError, SweepStore

ORIGIN = "http://localhost:8001"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as c:
        yield c


def post(client, path, origin=ORIGIN, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if origin is not None:
        headers["origin"] = origin
    return client.post(path, headers=headers, **kwargs)


def start_sweep(client, country="AE", currency="USD", device="test-device"):
    return post(
        client,
        "/api/sweeps",
        json={"country": country, "currency": currency, "device": device},
    )


def wait_postwork(client, sweep_id, timeout_s=10.0):
    import time

    deadline = time.monotonic() + timeout_s
    state = {}
    while time.monotonic() < deadline:
        state = client.get(f"/api/sweeps/{sweep_id}").json()
        if state["postwork_status"] in (
            "completed",
            "failed",
            "timed_out",
            "interrupted",
        ):
            return state
        time.sleep(0.02)
    raise AssertionError("postwork did not settle: " + state.get("postwork_status", "?"))


def tiny_jpeg(width=8, height=8):
    return cv2.imencode(".jpg", np.zeros((height, width, 3), np.uint8))[1].tobytes()


@pytest.mark.parametrize(
    "country,currency",
    [("AE", "AED"), ("AE", "USD"), ("US", "USD"), ("GB", "GBP")],
)
def test_locales_roundtrip_exactly(client, country, currency):
    resp = start_sweep(client, country, currency)
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert state["country"] == country
    assert state["currency"] == currency
    fetched = client.get(f"/api/sweeps/{state['id']}").json()
    assert fetched["country"] == country
    assert fetched["currency"] == currency


def test_locale_input_normalized_and_format_validated(client):
    resp = start_sweep(client, " ae ", " usd ")
    assert resp.status_code == 200
    assert resp.json()["country"] == "AE"
    assert resp.json()["currency"] == "USD"
    for country, currency in [("A1", "USD"), ("USA", "USD"), ("", "USD"), ("AE", "US"), ("AE", "USDD"), ("AE", "U1D")]:
        resp = start_sweep(client, country, currency)
        assert resp.status_code == 422, (country, currency, resp.text)


def test_device_over_limit_rejected(client):
    resp = start_sweep(client, device="x" * 201)
    assert resp.status_code == 422


def upload_frame(client, sweep_id, body=None, ts="1.0", origin=ORIGIN, content_type="image/jpeg"):
    return post(
        client,
        f"/api/sweeps/{sweep_id}/frames?ts={ts}",
        content=body if body is not None else tiny_jpeg(),
        headers={"content-type": content_type},
        origin=origin,
    )


def test_frame_upload_saves_and_serves_decoded_jpeg(client):
    state = start_sweep(client).json()
    payload = tiny_jpeg(16, 12)
    resp = upload_frame(client, state["id"], payload, ts="1.5")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["saved"] is True
    assert data["frame_ref"] == f"frames/{data['frame_id']}.jpg"
    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert len(stored["frames"]) == 1
    record = stored["frames"][0]
    assert record["width_px"] == 16
    assert record["height_px"] == 12
    assert record["timestamp_s"] == 1.5
    served = client.get(f"/api/sweeps/{state['id']}/frames/{data['frame_id']}.jpg")
    assert served.status_code == 200
    assert served.content == payload


def test_frame_equal_or_backwards_timestamp_conflict(client):
    state = start_sweep(client).json()
    assert upload_frame(client, state["id"], ts="2.0").status_code == 200
    for ts in ("2.0", "1.0"):
        resp = upload_frame(client, state["id"], ts=ts)
        assert resp.status_code == 409, (ts, resp.text)
    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert len(stored["frames"]) == 1
    assert stored["revision"] == 1


def test_frame_invalid_inputs(client):
    state = start_sweep(client).json()
    assert upload_frame(client, state["id"], body=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).status_code == 422
    assert upload_frame(client, state["id"], body=b"").status_code == 422
    for ts in ("NaN", "-1.0", "Infinity"):
        resp = upload_frame(client, state["id"], ts=ts)
        assert resp.status_code == 422, (ts, resp.status_code)
    assert upload_frame(client, state["id"], content_type="image/png").status_code == 415


def test_frame_oversized_stream_rejected(client):
    state = start_sweep(client).json()
    big = tiny_jpeg() + b"\x00" * (5 * 1024 * 1024)
    resp = upload_frame(client, state["id"], body=big)
    assert resp.status_code == 413


def test_shared_wifi_allowlist_rejects_other_clients_before_serving_any_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ALLOWED_CLIENT_IP", "10.1.2.3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app, client=("10.1.2.4", 50000)) as outsider:
        assert outsider.get("/").status_code == 403
        assert outsider.get("/api/health").status_code == 403
        assert outsider.get("/api/health", headers={"x-forwarded-for": "10.1.2.3"}).status_code == 403
        assert outsider.get("/api/sweeps/" + "a" * 32).status_code == 403
        assert start_sweep(outsider).status_code == 403
    with TestClient(app, client=("10.1.2.3", 50000)) as phone:
        assert phone.get("/").status_code == 200
        assert start_sweep(phone).status_code == 200
    with TestClient(app, client=("127.0.0.1", 50000)) as local:
        assert local.get("/api/health").status_code == 200


def test_frame_unknown_sweep_and_origin(client):
    assert upload_frame(client, "0" * 32).status_code == 404
    state = start_sweep(client).json()
    assert upload_frame(client, state["id"], origin="https://evil.example.com").status_code == 403
    assert upload_frame(client, state["id"], origin=None).status_code == 403


def test_frame_rejected_after_end(client):
    state = start_sweep(client).json()
    ended = post(client, f"/api/sweeps/{state['id']}/end", json={"duration_s": 3.0})
    assert ended.status_code == 200
    resp = upload_frame(client, state["id"])
    assert resp.status_code == 409


def add_turn(client, sweep_id, turn_id="t1", text="exclude the left stack", source="typed_user"):
    return post(
        client,
        f"/api/sweeps/{sweep_id}/turns",
        json={"turn_id": turn_id, "text": text, "source": source},
    )


def correct(client, sweep_id, intent):
    return post(client, f"/api/sweeps/{sweep_id}/corrections", json=intent)


def test_turn_duplicate_id_is_idempotent(client):
    state = start_sweep(client).json()
    resp = add_turn(client, state["id"])
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["user_turns"]) == 1
    again = add_turn(client, state["id"], text="different text same id")
    assert again.status_code == 200
    stored = again.json()
    assert len(stored["user_turns"]) == 1
    assert stored["user_turns"][0]["text"] == "exclude the left stack"
    assert stored["revision"] == resp.json()["revision"]


def test_turn_rejects_bad_source_and_empty_text(client):
    state = start_sweep(client).json()
    assert add_turn(client, state["id"], source="model_output").status_code == 422
    assert add_turn(client, state["id"], text="").status_code == 422
    assert add_turn(client, state["id"], turn_id="t2", text="x" * 8001).status_code == 422


def test_room_exclude_applies_once_and_persists(client):
    state = start_sweep(client).json()
    add_turn(client, state["id"], turn_id="t1")
    intent = {
        "type": "exclude_region",
        "target_id": "room",
        "value": True,
        "source_turn_id": "t1",
    }
    resp = correct(client, state["id"], intent)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["applied"] is True
    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert stored["regions"] == [{"id": "room", "excluded": True}]
    assert len(stored["corrections"]) == 1
    again = correct(client, state["id"], intent)
    assert again.status_code == 200
    assert again.json()["applied"] is False
    assert client.get(f"/api/sweeps/{state['id']}").json()["revision"] == stored["revision"]


def test_correction_unknown_target_and_source_leave_state_unchanged(client):
    state = start_sweep(client).json()
    add_turn(client, state["id"], turn_id="t0", text="real turn")
    before = client.get(f"/api/sweeps/{state['id']}").json()
    resp = correct(
        client,
        state["id"],
        {"type": "exclude_region", "target_id": "room", "value": True, "source_turn_id": "ghost"},
    )
    assert resp.status_code == 409
    resp = correct(
        client,
        state["id"],
        {"type": "exclude_region", "target_id": "shelf-9", "value": None, "source_turn_id": "t0"},
    )
    assert resp.status_code == 409
    after = client.get(f"/api/sweeps/{state['id']}").json()
    assert after["revision"] == before["revision"]
    assert after["regions"] == before["regions"]
    assert after["corrections"] == []


def test_item_book_correction_never_fabricates_records(client):
    state = start_sweep(client).json()
    add_turn(client, state["id"], turn_id="t1")
    for intent_type, target in (
        ("exclude_item", "item-1"),
        ("assert_edition", "book-1"),
        ("assert_art_print", "book-1"),
    ):
        resp = correct(
            client,
            state["id"],
            {"type": intent_type, "target_id": target, "value": "x", "source_turn_id": "t1"},
        )
        assert resp.status_code == 409, (intent_type, resp.text)
    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert stored["books"] == []
    assert stored["items"] == []
    assert stored["corrections"] == []


def test_end_marks_partial_with_pending_stages_and_is_idempotent(client):
    state = start_sweep(client).json()
    resp = post(client, f"/api/sweeps/{state['id']}/end", json={"duration_s": 12.5})
    assert resp.status_code == 200, resp.text
    ended = resp.json()
    assert ended["status"] == "partial"
    assert ended["ended_at"]
    assert ended["duration_s"] == 12.5
    assert ended["inventory_complete"] is False
    assert ended["processing_complete"] is False
    assert ended["postwork_status"] == "not_started"
    assert ended["packet"] is None
    pending_refs = {item["ref_id"] for item in ended["review_queue"]}
    assert {"extraction", "measurement", "pricing"} <= pending_refs
    settled = wait_postwork(client, state["id"])
    assert settled["postwork_status"] == "completed"
    assert settled["processing_complete"] is True
    again = post(client, f"/api/sweeps/{state['id']}/end", json={"duration_s": 99.0})
    assert again.status_code == 200
    repeated = again.json()
    assert repeated["duration_s"] == 12.5
    assert repeated["ended_at"] == ended["ended_at"]
    assert repeated["postwork_finished_at"] == settled["postwork_finished_at"]


def test_end_rejects_invalid_duration_and_unknown_sweep(client):
    state = start_sweep(client).json()
    assert post(client, f"/api/sweeps/{state['id']}/end", json={"duration_s": -1.0}).status_code == 422
    resp = post(
        client,
        f"/api/sweeps/{state['id']}/end",
        content=b'{"duration_s": NaN}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    assert post(client, f"/api/sweeps/{'0' * 32}/end", json={"duration_s": 1.0}).status_code == 404


def test_interrupt_preserves_saved_evidence(client, tmp_path):
    state = start_sweep(client).json()
    payload = tiny_jpeg()
    uploaded = upload_frame(client, state["id"], payload)
    frame_id = uploaded.json()["frame_id"]
    resp = post(client, f"/api/sweeps/{state['id']}/interrupt")
    assert resp.status_code == 200
    assert resp.json()["status"] == "interrupted"
    served = client.get(f"/api/sweeps/{state['id']}/frames/{frame_id}.jpg")
    assert served.status_code == 200
    assert served.content == payload
    still = post(client, f"/api/sweeps/{state['id']}/interrupt")
    assert still.json()["status"] == "interrupted"
    assert still.json()["revision"] == resp.json()["revision"]


def test_evidence_paths_cannot_escape_root(client, tmp_path):
    state = start_sweep(client).json()
    for bad in ("..%2Fstate.json", "..%2F..%2Fsecret.jpg", "state.json", "notaframe.jpg"):
        resp = client.get(f"/api/sweeps/{state['id']}/frames/{bad}")
        assert resp.status_code == 404, (bad, resp.status_code)
    assert client.get("/api/sweeps/..%2F..%2Fetc").status_code == 404
    resp = client.get(f"/api/sweeps/{state['id']}/frames/frame_{'0' * 32}.jpg")
    assert resp.status_code == 404


def test_fresh_app_marks_inflight_sweep_interrupted(tmp_path):
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as c:
        state = start_sweep(c).json()
    reopened = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(reopened) as c:
        loaded = c.get(f"/api/sweeps/{state['id']}").json()
    assert loaded["status"] == "interrupted"
    assert loaded["processing_complete"] is False


def test_create_app_does_not_mutate_runs_before_startup(tmp_path):
    from backend.sweeps import SweepStore

    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    state_file = tmp_path / "runs" / state.id / "state.json"
    raw = state_file.read_bytes()
    server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    assert state_file.read_bytes() == raw
    assert '"capturing"' in raw.decode()


SDP_OFFER = "v=0\r\no=- 123 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
SDP_ANSWER = "v=0\r\no=- 456 1 IN IP4 10.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\n"
TEST_KEY = "sk-phase1-test-secret"


def realtime(client, sweep_id, body=SDP_OFFER, content_type="application/sdp", origin=ORIGIN):
    return post(
        client,
        f"/api/sweeps/{sweep_id}/realtime",
        content=body,
        headers={"content-type": content_type},
        origin=origin,
    )


def stub_provider(app, handler):
    import httpx

    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(record))
        try:
            yield http
        finally:
            await http.aclose()

    app.dependency_overrides[server.get_http_client] = override_client
    return requests


def test_realtime_missing_key_503_without_provider_call(client, monkeypatch, tmp_path):
    import httpx

    app = client.app
    requests = stub_provider(app, lambda req: httpx.Response(200, content=SDP_ANSWER.encode()))
    state = start_sweep(client).json()
    resp = realtime(client, state["id"])
    assert resp.status_code == 503
    assert requests == []


def test_realtime_success_returns_sdp_without_secret(client, monkeypatch):
    import httpx

    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    requests = stub_provider(
        client.app,
        lambda req: httpx.Response(200, content=SDP_ANSWER.encode(), headers={"content-type": "application/sdp"}),
    )
    state = start_sweep(client, "AE", "USD").json()
    resp = realtime(client, state["id"])
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/sdp")
    assert resp.text == SDP_ANSWER
    assert TEST_KEY not in resp.text
    assert len(requests) == 1
    sent = requests[0]
    assert sent.url.host == "api.openai.com"
    assert sent.headers["authorization"] == f"Bearer {TEST_KEY}"
    import json, re

    match = re.search(r'name="session"\r\n\r\n(.*?)\r\n--', sent.content.decode(), re.S)
    session = json.loads(match.group(1))
    assert session["type"] == "realtime"
    assert session["model"] == "gpt-realtime-2.1"
    assert session["audio"]["output"]["voice"] == "marin"
    assert '{"country": "AE", "currency": "USD"}' in session["instructions"]


def test_realtime_provider_rejection_and_error_sanitized(client, monkeypatch):
    import httpx

    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()

    def bad_request(req):
        return httpx.Response(500, content=f"upstream auth {TEST_KEY}".encode())

    stub_provider(client.app, bad_request)
    resp = realtime(client, state["id"])
    assert resp.status_code == 502
    assert TEST_KEY not in resp.text
    assert "upstream" not in resp.text

    def unreachable(req):
        raise httpx.ConnectError(f"host down {TEST_KEY}")

    stub_provider(client.app, unreachable)
    resp = realtime(client, state["id"])
    assert resp.status_code == 502
    assert TEST_KEY not in resp.text


def test_realtime_wrong_mime_and_inactive_sweep(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    assert realtime(client, state["id"], content_type="text/plain").status_code == 415
    post(client, f"/api/sweeps/{state['id']}/end", json={"duration_s": 2.0})
    resp = realtime(client, state["id"])
    assert resp.status_code == 409
    assert realtime(client, "0" * 32).status_code == 404


def test_realtime_rejects_oversized_and_empty_offer(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    assert realtime(client, state["id"], body="x" * 65537).status_code == 413
    assert realtime(client, state["id"], body=b"").status_code == 400


def test_png_body_rejected_as_frame(client):
    state = start_sweep(client).json()
    png = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    assert upload_frame(client, state["id"], body=png).status_code == 422


def test_json_routes_check_origin_before_parsing(client):
    resp = post(
        client,
        "/api/sweeps",
        content=b"{malformed json not json",
        headers={"content-type": "application/json"},
        origin="https://evil.example.com",
    )
    assert resp.status_code == 403
    resp = post(
        client,
        "/api/sweeps",
        content=b"{malformed json not json",
        headers={"content-type": "application/json"},
        origin=None,
    )
    assert resp.status_code == 403


def test_malformed_json_with_valid_origin_is_422(client):
    resp = post(
        client,
        "/api/sweeps",
        content=b"{malformed",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422


def test_json_body_capped_at_limit(client):
    big = b'{"country":"AE","currency":"USD","device":"' + b"x" * 200000 + b'"}'
    resp = post(client, "/api/sweeps", content=big, headers={"content-type": "application/json"})
    assert resp.status_code == 413

    def chunks():
        yield b'{"country":"AE","currency":"USD","device":"'
        yield b"x" * 200000
        yield b'"}'

    resp = post(
        client,
        "/api/sweeps",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413


def test_non_ascii_turn_text_preserved(client):
    state = start_sweep(client).json()
    resp = add_turn(client, state["id"], turn_id="t-unicode", text="استبعد الرف الأيسر من الجرد")
    assert resp.status_code == 200, resp.text
    stored = client.get(f"/api/sweeps/{state['id']}").json()
    assert stored["user_turns"][0]["text"] == "استبعد الرف الأيسر من الجرد"


def test_json_error_does_not_reflect_payload(client):
    resp = post(
        client,
        "/api/sweeps",
        content=b'{"country":"AE","currency":"USD","device":' + b'"' + b"y" * 500 + b'"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    assert "y" * 300 not in resp.text


def test_validation_errors_expose_only_loc_msg_type(client):
    secret = "sk-fake-secret-do-not-use-123"
    resp = post(
        client,
        "/api/sweeps",
        json={"country": "AE", "currency": "USD", "device": "d", "api_key": secret},
    )
    assert resp.status_code == 422
    assert secret not in resp.text
    detail = resp.json()["detail"]
    assert detail
    assert all(set(err) == {"loc", "msg", "type"} for err in detail)

    resp = post(
        client,
        "/api/sweeps",
        json={"country": "AE", "currency": secret, "device": "d"},
    )
    assert resp.status_code == 422
    assert secret not in resp.text
    detail = resp.json()["detail"]
    assert detail
    if isinstance(detail, list):
        assert all(set(err) == {"loc", "msg", "type"} for err in detail)
    else:
        assert detail == "currency must be three ASCII letters"


def test_symlinked_frame_file_not_served(client, tmp_path):
    state = start_sweep(client).json()
    uploaded = upload_frame(client, state["id"], tiny_jpeg())
    frame_id = uploaded.json()["frame_id"]
    frames_dir = tmp_path / "runs" / state["id"] / "frames"
    frame_path = frames_dir / f"{frame_id}.jpg"
    outside = tmp_path / "outside-dummy.jpg"
    outside.write_bytes(b"outside-bytes")
    frame_path.rename(frames_dir / "kept-aside.jpg")
    os.symlink(outside, frame_path)
    resp = client.get(f"/api/sweeps/{state['id']}/frames/{frame_id}.jpg")
    assert resp.status_code == 404
    assert outside.read_bytes() == b"outside-bytes"


def test_symlinked_frames_directory_not_served(client, tmp_path):
    state = start_sweep(client).json()
    uploaded = upload_frame(client, state["id"], tiny_jpeg())
    frame_id = uploaded.json()["frame_id"]
    sweep_dir = tmp_path / "runs" / state["id"]
    (sweep_dir / "frames").rename(sweep_dir / "frames-real")
    external = tmp_path / "external-frames"
    external.mkdir()
    (external / f"{frame_id}.jpg").write_bytes(b"outside-bytes")
    os.symlink(external, sweep_dir / "frames")
    resp = client.get(f"/api/sweeps/{state['id']}/frames/{frame_id}.jpg")
    assert resp.status_code == 404


def _seed_store(tmp_path, frames=2):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "coverage-store-test")
    saved = []
    for index in range(frames):
        image = np.full((32, 48, 3), index * 40, np.uint8)
        rng = np.random.RandomState(index + 1)
        image[:, index * 8 :] = rng.randint(
            0, 255, (32, 48 - index * 8, 3), dtype=np.uint8
        )
        saved.append(
            store.add_frame(
                state.id,
                cv2.imencode(".jpg", image)[1].tobytes(),
                float(index + 1),
            )
        )
    return store, state, saved


def _manifest(frames, **entry_kwargs):
    return CoverageManifest(
        entries=[
            CoverageDecision(
                frame_id=frame.id,
                disposition="selected",
                reason="selected within vision budget",
                **entry_kwargs,
            )
            for frame in frames
        ]
    )


def test_add_frame_persists_sha256_and_sharpness(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "frame-facts")
    image = np.random.RandomState(3).randint(
        0, 255, (300, 640, 3), dtype=np.uint8
    )
    payload = cv2.imencode(".jpg", image)[1].tobytes()
    first = store.add_frame(state.id, payload, 1.0)
    assert first.sha256 == hashlib.sha256(payload).hexdigest()
    assert isinstance(first.sharpness_score, float)
    assert math.isfinite(first.sharpness_score)
    assert first.sharpness_score >= 0.0
    assert (first.width_px, first.height_px) == (640, 300)
    assert store.frame_path(state.id, first.id).read_bytes() == payload
    second = store.add_frame(state.id, payload, 2.0)
    assert second.id != first.id
    assert second.sha256 == first.sha256


def test_record_coverage_validates_membership_and_refs(tmp_path):
    store, state, frames = _seed_store(tmp_path, frames=2)
    store.end(state.id, 1.0)
    manifest = _manifest(frames)
    before = store.get(state.id).revision
    updated = store.record_coverage(state.id, manifest)
    assert updated.coverage_manifest == manifest
    assert updated.revision == before + 1
    unchanged = store.record_coverage(state.id, manifest)
    assert unchanged.revision == before + 1

    bad = CoverageManifest(
        entries=[
            manifest.entries[0],
            CoverageDecision(
                frame_id="frame_" + "0" * 32,
                disposition="deferred",
                reason="not selected within vision budget",
            ),
        ]
    )
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, bad)
    assert exc.value.status_code == 422

    swapped = CoverageManifest(
        entries=[manifest.entries[1], manifest.entries[0]]
    )
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, swapped)
    assert exc.value.status_code == 422

    unsafe = _manifest(frames, stage_refs=["../escape.json"])
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, unsafe)
    assert exc.value.status_code == 409

    missing = _manifest(
        frames, stage_refs=[f"stage_outputs/vision_{'0' * 32}_{'0' * 32}.json"]
    )
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, missing)
    assert exc.value.status_code == 409

    bad_gap = manifest.model_copy(
        update={
            "gaps": [
                CoverageGap(
                    frame_ids=["frame_" + "0" * 32],
                    reason="unselected",
                )
            ]
        }
    )
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, bad_gap)
    assert exc.value.status_code == 422


def test_record_coverage_links_resolving_stage_refs(tmp_path):
    store, state, frames = _seed_store(tmp_path, frames=2)
    ref = store.persist_stage(
        state.id,
        f"vision_{frames[0].id[6:]}_{uuid.uuid4().hex}.json",
        json.dumps({"frame_id": frames[0].id, "status": "completed"}).encode(),
    )
    store.record_observation(
        state.id,
        frames[0].id,
        {
            "stage": VisionStageRecord(
                frame_id=frames[0].id,
                status="completed",
                persisted_ref=ref,
                model="fake",
                cost_usd_estimate="0",
                elapsed_ms=1.0,
                error=None,
            ),
            "observation": None,
        },
    )
    store.end(state.id, 1.0)
    manifest = CoverageManifest(
        entries=[
            CoverageDecision(
                frame_id=frames[0].id,
                disposition="selected",
                reason="prior analysis satisfied",
                processing_status="completed",
                stage_refs=[ref],
            ),
            CoverageDecision(
                frame_id=frames[1].id,
                disposition="deferred",
                reason="not selected within vision budget",
                processing_status="skipped",
            ),
        ]
    )
    updated = store.record_coverage(state.id, manifest)
    assert updated.coverage_manifest.entries[0].stage_refs == [ref]


def test_record_coverage_interrupted_settles_only(tmp_path):
    store, state, frames = _seed_store(tmp_path, frames=2)
    store.end(state.id, 1.0)
    manifest = _manifest(frames)
    manifest.entries[1] = manifest.entries[1].model_copy(
        update={"processing_status": "running"}
    )
    store.record_coverage(state.id, manifest)
    store.interrupt(state.id)
    settled = store.record_coverage(state.id, manifest)
    assert all(
        entry.processing_status == "interrupted"
        for entry in settled.coverage_manifest.entries
    )
    late = manifest.model_copy(
        update={
            "entries": [
                manifest.entries[0].model_copy(
                    update={
                        "processing_status": "completed",
                        "stage_refs": [],
                    }
                ),
                manifest.entries[1],
            ]
        }
    )
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, late)
    assert exc.value.status_code == 409
    after = store.get(state.id)
    assert all(
        entry.processing_status == "interrupted"
        for entry in after.coverage_manifest.entries
    )
    tampered = manifest.model_copy(
        update={
            "entries": [
                manifest.entries[0].model_copy(
                    update={"row_id": "row_injected", "reason": "changed"}
                ),
                manifest.entries[1],
            ]
        }
    )
    store.record_coverage(state.id, tampered)
    stored = store.get(state.id).coverage_manifest
    assert all(entry.row_id is None for entry in stored.entries)
    assert stored.entries[0].reason == "selected within vision budget"
    with pytest.raises(StoreError) as exc:
        store.record_observation(
            state.id,
            frames[0].id,
            {
                "stage": VisionStageRecord(
                    frame_id=frames[0].id,
                    status="completed",
                    persisted_ref=None,
                    model=None,
                    cost_usd_estimate=None,
                    elapsed_ms=None,
                    error=None,
                ),
                "observation": None,
            },
        )
    assert exc.value.status_code == 409


def test_record_coverage_interrupted_rejects_first_manifest(tmp_path):
    store, state, frames = _seed_store(tmp_path, frames=2)
    store.end(state.id, 1.0)
    store.interrupt(state.id)
    manifest = _manifest(frames)
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, manifest)
    assert exc.value.status_code == 409
    assert store.get(state.id).coverage_manifest is None


def test_record_coverage_requires_stage_evidence_for_success(tmp_path):
    store, state, frames = _seed_store(tmp_path, frames=2)
    ref = store.persist_stage(
        state.id,
        f"vision_{frames[0].id[6:]}_{uuid.uuid4().hex}.json",
        json.dumps({"frame_id": frames[0].id, "status": "completed"}).encode(),
    )
    store.record_observation(
        state.id,
        frames[0].id,
        {
            "stage": VisionStageRecord(
                frame_id=frames[0].id,
                status="completed",
                persisted_ref=ref,
                model="fake",
                cost_usd_estimate="0",
                elapsed_ms=1.0,
                error=None,
            ),
            "observation": None,
        },
    )
    store.end(state.id, 1.0)

    def decision(fid, **kwargs):
        return CoverageDecision(
            frame_id=fid,
            disposition="selected",
            reason="prior analysis satisfied",
            **kwargs,
        )

    evidence_free = CoverageManifest(
        entries=[
            decision(frames[0].id, processing_status="completed"),
            decision(frames[1].id, processing_status="pending"),
        ]
    )
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, evidence_free)
    assert exc.value.status_code == 422

    foreign_ref = store.persist_stage(
        state.id,
        f"vision_{frames[1].id[6:]}_{uuid.uuid4().hex}.json",
        json.dumps({"frame_id": frames[1].id, "status": "completed"}).encode(),
    )
    unrelated = CoverageManifest(
        entries=[
            decision(
                frames[0].id,
                processing_status="completed",
                stage_refs=[foreign_ref],
            ),
            decision(frames[1].id, processing_status="pending"),
        ]
    )
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, unrelated)
    assert exc.value.status_code == 422

    valid = CoverageManifest(
        entries=[
            decision(
                frames[0].id,
                processing_status="completed",
                stage_refs=[ref],
            ),
            decision(frames[1].id, processing_status="pending"),
        ]
    )
    updated = store.record_coverage(state.id, valid)
    assert updated.coverage_manifest.entries[0].stage_refs == [ref]

    supported = valid.model_copy(
        update={"spatial_coverage_status": "supported"}
    )
    with pytest.raises(StoreError) as exc:
        store.record_coverage(state.id, supported)
    assert exc.value.status_code == 422


def test_record_coverage_deep_copies_manifest(tmp_path):
    store, state, frames = _seed_store(tmp_path, frames=2)
    store.end(state.id, 1.0)
    manifest = _manifest(frames)
    updated = store.record_coverage(state.id, manifest)
    revision = updated.revision
    manifest.entries[0] = manifest.entries[0].model_copy(
        update={"reason": "mutated after write", "row_id": "row_x"}
    )
    manifest.entries.append(
        CoverageDecision(
            frame_id=frames[0].id,
            disposition="rejected",
            reason="injected",
        )
    )
    stored = store.get(state.id).coverage_manifest
    assert stored.entries[0].reason == "selected within vision budget"
    assert stored.entries[0].row_id is None
    assert len(stored.entries) == 2
    assert store.get(state.id).revision == revision


def test_record_coverage_rebuilds_gaps_for_unsatisfied(tmp_path):
    store, state, frames = _seed_store(tmp_path, frames=2)
    store.end(state.id, 1.0)
    manifest = _manifest(frames)
    manifest.entries[1] = manifest.entries[1].model_copy(
        update={
            "disposition": "deferred",
            "reason": "not selected within vision budget",
            "processing_status": "skipped",
        }
    )
    updated = store.record_coverage(state.id, manifest)
    gaps = updated.coverage_manifest.gaps
    assert len(gaps) == 1
    assert gaps[0].frame_ids == [frames[1].id]
    assert gaps[0].reason == "not selected within vision budget"


def test_legacy_state_defaults_null_coverage_manifest(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "legacy-coverage")
    store.add_frame(state.id, tiny_jpeg(16, 16), 1.0)
    state_file = tmp_path / "runs" / state.id / "state.json"
    raw = json.loads(state_file.read_text())
    raw.pop("coverage_manifest", None)
    for frame in raw["frames"]:
        frame.pop("sha256", None)
        frame.pop("sharpness_score", None)
    state_file.write_text(json.dumps(raw))
    reopened = SweepStore(tmp_path / "runs")
    loaded = reopened.get(state.id)
    assert loaded.coverage_manifest is None
    assert loaded.frames[0].sha256 is None
    assert loaded.frames[0].sharpness_score is None


def test_capture_config_exposes_only_safe_guidance_policy(client):
    resp = client.get("/api/capture_config")
    assert resp.status_code == 200
    cfg = resp.json()
    assert set(cfg.keys()) == {"vision_enabled", "visual_context", "guidance"}
    assert set(cfg["guidance"].keys()) == {
        "enabled",
        "maximum_responses_per_sweep",
        "minimum_response_interval_ms",
        "maximum_event_age_ms",
    }
    assert cfg["guidance"]["enabled"] is False
    assert "instructions" not in cfg["guidance"]
    assert "session" not in cfg


def test_guidance_module_is_served(client):
    resp = client.get("/guidance.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert "createGuidanceScheduler" in resp.text


def test_coverage_decision_rejects_reversed_range():
    with pytest.raises(Exception):
        CoverageDecision(
            frame_id="f1",
            disposition="selected",
            reason="x",
            covered_range=(2.0, 1.0),
        )
    with pytest.raises(Exception):
        CoverageDecision(
            frame_id="f1",
            disposition="selected",
            reason="x",
            covered_range=(float("nan"), 1.0),
        )
    ok = CoverageDecision(
        frame_id="f1", disposition="selected", reason="x", covered_range=(1.0, 2.0)
    )
    assert ok.covered_range == (1.0, 2.0)
