import asyncio
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from preflight.safari import server

ALLOWED_ORIGIN = "http://localhost:8000"
TEST_KEY = "sk-test-secret-value"
SDP_OFFER = "v=0\r\no=- 123 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
SDP_ANSWER = "v=0\r\no=- 456 1 IN IP4 10.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\n"
VALID_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64 + b"\xff\xd9"
NOT_JPEG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def provider_requests():
    return []


@pytest.fixture
def client(monkeypatch, tmp_path, provider_requests):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.setenv(
        "PREFLIGHT_RUNS_DIR", str(tmp_path / "runs" / "preflight-safari")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        provider_requests.append(request)
        return httpx.Response(
            200,
            content=SDP_ANSWER.encode(),
            headers={"content-type": "application/sdp"},
        )

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            yield http
        finally:
            await http.aclose()

    server.app.dependency_overrides[server.get_http_client] = override_client
    with TestClient(server.app) as c:
        yield c
    server.app.dependency_overrides.clear()


def post_session(client, body=SDP_OFFER, content_type="application/sdp", origin=ALLOWED_ORIGIN):
    headers = {"content-type": content_type}
    if origin is not None:
        headers["origin"] = origin
    return client.post("/preflight/session", content=body, headers=headers)


def post_frame(client, body=VALID_JPEG, content_type="image/jpeg", query="ts=1.5&run_id=test-run", origin=ALLOWED_ORIGIN):
    headers = {"content-type": content_type}
    if origin is not None:
        headers["origin"] = origin
    return client.post(f"/preflight/frame?{query}", content=body, headers=headers)


def test_index_serves_probe_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<video" in resp.text


def test_probe_restricts_shared_network_clients(client, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_ALLOWED_CLIENT_IP", "172.20.10.1")
    assert client.get("/").status_code == 403
    assert client.get("/preflight/health").status_code == 403
    assert post_frame(client).status_code == 403
    monkeypatch.setenv("PREFLIGHT_ALLOWED_CLIENT_IP", "testclient")
    assert client.get("/").status_code == 200


def test_probe_denies_remote_clients_by_default(client, monkeypatch):
    monkeypatch.delenv("PREFLIGHT_ALLOWED_CLIENT_IP", raising=False)

    async def request_from_remote():
        transport = httpx.ASGITransport(app=server.app, client=("203.0.113.25", 49123))
        async with httpx.AsyncClient(transport=transport, base_url=ALLOWED_ORIGIN) as remote:
            return await remote.get("/")

    assert asyncio.run(request_from_remote()).status_code == 403


def test_voice_module_served(client):
    resp = client.get("/voice.js")
    assert resp.status_code == 200
    assert "waitForConnection" in resp.text


def test_health_reports_booleans_only(client):
    resp = client.get("/preflight/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["openai_key_present"] is True
    assert TEST_KEY not in resp.text
    assert all(isinstance(v, bool) for v in payload.values())


def test_session_success_returns_sdp_answer(client, provider_requests):
    resp = post_session(client)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/sdp")
    assert resp.text == SDP_ANSWER
    assert TEST_KEY not in resp.text
    assert len(provider_requests) == 1
    sent = provider_requests[0]
    assert sent.url.host == "api.openai.com"
    assert sent.url.path == "/v1/realtime/calls"
    assert sent.headers["authorization"] == f"Bearer {TEST_KEY}"
    body = sent.content.decode()
    assert 'name="sdp"' in body
    assert 'name="session"' in body
    match = re.search(r'name="session"\r\n\r\n(.*?)\r\n--', body, re.S)
    assert match, body
    session_json = json.loads(match.group(1))
    assert session_json == {
        "type": "realtime",
        "model": "gpt-realtime-2.1",
        "audio": {"output": {"voice": "marin"}},
        "instructions": (
            "You are helping test live camera and voice concurrency. "
            "Speak briefly. On request, speak for several seconds so the user "
            "can interrupt you. Do not claim to see images, count books, "
            "measure objects, or retrieve prices in this test."
        ),
    }


def test_session_rejects_invalid_mime(client):
    resp = post_session(client, content_type="text/plain")
    assert 400 <= resp.status_code < 500


def test_session_rejects_empty_body(client):
    resp = post_session(client, body=b"")
    assert 400 <= resp.status_code < 500


def test_session_rejects_oversized_body(client):
    resp = post_session(client, body="x" * 65537)
    assert resp.status_code == 413


def test_session_rejects_disallowed_origin(client):
    resp = post_session(client, origin="https://evil.example.com")
    assert resp.status_code == 403


def test_session_missing_key_returns_503_without_provider_call(
    client, monkeypatch, provider_requests
):
    monkeypatch.delenv("OPENAI_API_KEY")
    resp = post_session(client)
    assert resp.status_code == 503
    assert provider_requests == []


def test_session_provider_error_is_sanitized(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, content=f"upstream failure, auth was {TEST_KEY}".encode()
        )

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            yield http
        finally:
            await http.aclose()

    server.app.dependency_overrides[server.get_http_client] = override_client
    resp = post_session(client)
    assert resp.status_code == 502
    assert TEST_KEY not in resp.text
    assert "upstream failure" not in resp.text


def test_session_accepts_201_sdp_answer(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            content=SDP_ANSWER.encode(),
            headers={"content-type": "application/sdp"},
        )

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            yield http
        finally:
            await http.aclose()

    server.app.dependency_overrides[server.get_http_client] = override_client
    resp = post_session(client)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/sdp")
    assert resp.text == SDP_ANSWER


def test_session_rejects_non_sdp_success(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><body>gateway page</body></html>",
            headers={"content-type": "text/html"},
        )

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            yield http
        finally:
            await http.aclose()

    server.app.dependency_overrides[server.get_http_client] = override_client
    resp = post_session(client)
    assert resp.status_code == 502
    assert "gateway" not in resp.text


def test_session_rejects_whitespace_only_offer(client):
    resp = post_session(client, body="  \r\n\t  ")
    assert 400 <= resp.status_code < 500


def test_session_provider_connection_error_is_sanitized(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"could not reach host with {TEST_KEY}")

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            yield http
        finally:
            await http.aclose()

    server.app.dependency_overrides[server.get_http_client] = override_client
    resp = post_session(client)
    assert resp.status_code == 502
    assert TEST_KEY not in resp.text


def test_frame_saves_jpeg_and_returns_id(client, tmp_path):
    resp = post_frame(client)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["saved"] is True
    frame_id = payload["frame_id"]
    run_dir = tmp_path / "runs" / "preflight-safari" / "test-run"
    saved = run_dir / f"{frame_id}.jpg"
    assert saved.exists()
    assert saved.read_bytes() == VALID_JPEG
    sidecar = json.loads((run_dir / f"{frame_id}.json").read_text())
    assert sidecar == {"frame_id": frame_id, "timestamp_s": 1.5}


def test_frame_rejects_non_jpeg_mime(client):
    resp = post_frame(client, content_type="image/png")
    assert 400 <= resp.status_code < 500


def test_frame_rejects_corrupt_jpeg(client):
    resp = post_frame(client, body=NOT_JPEG)
    assert 400 <= resp.status_code < 500


def test_frame_rejects_negative_timestamp(client):
    resp = post_frame(client, query="ts=-1&run_id=test-run")
    assert 400 <= resp.status_code < 500


def test_frame_rejects_oversized_body(client):
    resp = post_frame(client, body=VALID_JPEG + b"\x00" * (5 * 1024 * 1024))
    assert resp.status_code == 413


def test_frame_rejects_unsafe_run_id(client, tmp_path):
    resp = post_frame(client, query="ts=1&run_id=..%2F..%2Fevil")
    assert 400 <= resp.status_code < 500
    assert not (tmp_path / "runs" / "preflight-safari" / "evil").exists()


def test_frame_rejects_disallowed_origin(client):
    resp = post_frame(client, origin="https://evil.example.com")
    assert resp.status_code == 403


def test_frame_rejects_nonfinite_timestamp(client):
    for bad in ("NaN", "Infinity", "-Infinity"):
        resp = post_frame(client, query=f"ts={bad}&run_id=test-run")
        assert 400 <= resp.status_code < 500, bad


def test_frame_preserves_existing_evidence_on_run_id_reuse(client, tmp_path):
    run_dir = tmp_path / "runs" / "preflight-safari" / "collision-run"
    run_dir.mkdir(parents=True)
    existing = run_dir / "frame_000000.jpg"
    existing.write_bytes(b"PREEXISTING")
    (run_dir / "frame_000000.json").write_text(
        '{"frame_id": "frame_000000", "timestamp_s": 0.0}'
    )
    resp = post_frame(client, query="ts=2.0&run_id=collision-run")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["saved"] is True
    assert payload["frame_id"] != "frame_000000"
    assert existing.read_bytes() == b"PREEXISTING"
    new_file = run_dir / f"{payload['frame_id']}.jpg"
    assert new_file.exists()
    assert new_file.read_bytes() == VALID_JPEG
