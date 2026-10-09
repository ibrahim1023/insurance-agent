import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import server

ORIGIN = "http://localhost:8001"
TEST_KEY = "sk-phase3-pricing-fixture"
CONTRACT = server.PRICING_CONTRACT
GLOBAL_IP = "93.184.216.34"


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


def start_sweep(client, country="US", currency="USD"):
    return post(
        client,
        "/api/sweeps",
        json={"country": country, "currency": currency, "device": "pricing-test"},
    )


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def scene_image(width=640, height=360, seed=7):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 255, (height, width, 3), dtype=np.uint8)


def seed_identified_book(client, sweep_id):
    store = client.app.state.store
    resp = post(
        client,
        f"/api/sweeps/{sweep_id}/frames?ts=1.0",
        content=jpeg(scene_image()),
        headers={"content-type": "image/jpeg"},
    )
    frame_id = resp.json()["frame_id"]
    store.commit_tracks(
        sweep_id,
        frame_id,
        {
            "frame_id": frame_id,
            "alignment": "none",
            "assignments": [],
            "applied_props": [],
            "new_books": [
                {
                    "frame_ref": f"frames/{frame_id}.jpg",
                    "status": "unidentified",
                    "bbox": {"x": 40, "y": 40, "w": 60, "h": 200},
                    "image_width": 640,
                    "image_height": 360,
                    "observed_fragments": [],
                    "evidence": [
                        {
                            "frame_id": frame_id,
                            "frame_ref": f"frames/{frame_id}.jpg",
                            "bbox": {"x": 40, "y": 40, "w": 60, "h": 200},
                            "image_width": 640,
                            "image_height": 360,
                            "sharpness": 10.0,
                        }
                    ],
                    "potential_duplicate": False,
                    "track_histogram": None,
                }
            ],
            "new_items": [],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 1,
        },
    )
    book_id = store.get(sweep_id).books[0].id
    store.apply_readings(
        sweep_id,
        {},
        [
            {
                "ref_id": book_id,
                "title": "Clean Code",
                "author": "Robert C. Martin",
                "publisher": "",
                "edition": "",
                "isbn": "",
                "title_legible": True,
                "author_legible": True,
                "confidence": 0.95,
                "visible_text": ["Clean Code"],
            }
        ],
    )
    return book_id


LISTING_URL = "https://books.example/listing/clean-code"

LISTING_PAGE = (
    '<html><head><script type="application/ld+json">'
    + json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Book",
            "name": "Clean Code",
            "author": {"@type": "Person", "name": "Robert C. Martin"},
            "url": LISTING_URL,
            "bookFormat": "https://schema.org/Paperback",
            "offers": {
                "@type": "Offer",
                "price": 10,
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock",
                "itemCondition": "https://schema.org/NewCondition",
                "url": LISTING_URL,
                "eligibleRegion": "US",
            },
        }
    )
    + "</script></head><body>synthetic fixture listing</body></html>"
)


def discovery_response(book_id):
    return {
        "id": "resp_1",
        "model": CONTRACT["model"],
        "status": "completed",
        "output": [
            {
                "id": "ws_1",
                "type": "web_search_call",
                "action": {
                    "type": "search",
                    "query": "clean code",
                    "sources": [{"url": LISTING_URL}],
                },
            },
            {
                "id": "msg_1",
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "records": [
                                    {
                                        "ref_id": book_id,
                                        "replacement_urls": [LISTING_URL],
                                        "used_urls": [],
                                        "notes": [],
                                    }
                                ],
                                "issues": [],
                            }
                        ),
                        "annotations": [],
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 0},
            "total_tokens": 150,
        },
    }


def fx_body():
    now = datetime.now(timezone.utc)
    return json.dumps(
        {
            "result": "success",
            "base_code": "USD",
            "time_last_update_unix": int((now - timedelta(hours=1)).timestamp()),
            "time_next_update_unix": int((now + timedelta(hours=23)).timestamp()),
            "rates": {"USD": 1, "AED": 3.67, "GBP": 0.75},
        }
    ).encode()


def stub_pricing(app, handler, resolver=None):
    requests = []

    async def record(request):
        requests.append(request)
        return await asyncio.to_thread(handler, request)

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False)
        try:
            yield http
        finally:
            await http.aclose()

    app.dependency_overrides[server.get_pricing_http_client] = override_client
    app.dependency_overrides[server.get_pricing_resolver] = (
        lambda: resolver or _global_resolver
    )
    return requests


async def _global_resolver(host):
    return [GLOBAL_IP]


def test_price_books_endpoint_prices_identified_book(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    book_id = seed_identified_book(client, state["id"])

    def handler(request):
        host = request.headers.get("host") or request.url.host
        if host == "api.openai.com":
            payload = json.loads(request.content.decode())
            assert payload["model"] == CONTRACT["model"]
            assert payload["text"]["format"] == CONTRACT["response_format"]
            return httpx.Response(200, json=discovery_response(book_id))
        if host == "open.er-api.com":
            return httpx.Response(200, content=fx_body())
        if host == "books.example":
            return httpx.Response(200, text=LISTING_PAGE)
        return httpx.Response(404)

    requests = stub_pricing(client.app, handler)
    resp = post(client, f"/api/sweeps/{state['id']}/price_books", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valuations"][book_id]["replacement"]["amount"] == "10.00"
    loaded = client.get(f"/api/sweeps/{state['id']}").json()
    price = loaded["books"][0]["replacement_cost"]
    assert price["amount"] == "10.00"
    assert price["currency"] == "USD"
    assert price["url"] == LISTING_URL
    assert price["source_quote"]["market_country"] == "US"
    assert price["source_quote"]["retrieved_at"]
    assert len(requests) >= 2


def test_price_books_guards(client, monkeypatch):
    state = start_sweep(client).json()
    book_id = seed_identified_book(client, state["id"])
    sid = state["id"]
    assert post(
        client, f"/api/sweeps/{sid}/price_books", json={}, origin=None
    ).status_code == 403
    assert post(
        client,
        f"/api/sweeps/{sid}/price_books",
        json={"book_ids": None, "extra": 1},
    ).status_code == 422
    assert post(
        client,
        f"/api/sweeps/{sid}/price_books",
        content=b"{not json",
        headers={"content-type": "application/json"},
    ).status_code == 422
    calls = []
    stub_pricing(client.app, lambda request: calls.append(request) or httpx.Response(500))
    assert post(client, f"/api/sweeps/{sid}/price_books", json={}).status_code == 503
    assert calls == []
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    assert (
        post(
            client,
            f"/api/sweeps/{sid}/price_books",
            json={"book_ids": ["book_unknown"]},
        ).status_code
        == 404
    )


def test_uploaded_frame_responsive_while_discovery_awaits(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    book_id = seed_identified_book(client, state["id"])
    sid = state["id"]
    entered = threading.Event()
    release = threading.Event()

    async def record(request):
        host = request.headers.get("host") or request.url.host
        if host == "api.openai.com":
            entered.set()
            await asyncio.to_thread(release.wait, 10)
            return httpx.Response(200, json=discovery_response(book_id))
        if host == "open.er-api.com":
            return httpx.Response(200, content=fx_body())
        if host == "books.example":
            return httpx.Response(200, text=LISTING_PAGE)
        return httpx.Response(404)

    async def override_client():
        http = httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False)
        try:
            yield http
        finally:
            await http.aclose()

    client.app.dependency_overrides[server.get_pricing_http_client] = override_client
    client.app.dependency_overrides[server.get_pricing_resolver] = lambda: _global_resolver

    outcome = {}

    def price_call():
        outcome["resp"] = post(client, f"/api/sweeps/{sid}/price_books", json={})

    worker = threading.Thread(target=price_call, daemon=True)
    worker.start()
    try:
        assert entered.wait(timeout=10)
        resp = post(
            client,
            f"/api/sweeps/{sid}/frames?ts=2.0",
            content=jpeg(scene_image(seed=9)),
            headers={"content-type": "image/jpeg"},
        )
        assert resp.status_code == 200
        assert resp.json()["saved"] is True
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert outcome["resp"].status_code == 200


def test_price_books_provider_malformed_returns_502(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    seed_identified_book(client, state["id"])

    def handler(request):
        host = request.headers.get("host") or request.url.host
        if host == "api.openai.com":
            return httpx.Response(200, json={"status": "incomplete", "output": []})
        return httpx.Response(404)

    stub_pricing(client.app, handler)
    resp = post(client, f"/api/sweeps/{state['id']}/price_books", json={})
    assert resp.status_code == 502
    assert "raw_response" not in resp.text


def test_price_books_oversize_json_returns_413(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    resp = post(
        client,
        f"/api/sweeps/{state['id']}/price_books",
        content=b'{"book_ids": null, "pad": "' + b"x" * 200000 + b'"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413


def test_price_books_response_and_state_suppress_after_late_exclusion(
    client, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    state = start_sweep(client).json()
    book_id = seed_identified_book(client, state["id"])
    sid = state["id"]
    store = client.app.state.store
    store.add_turn(sid, "t1", "exclude it", "typed_user")
    from backend.models import CorrectionIntent
    before = len(client.get(f"/api/sweeps/{sid}").json()["review_queue"])

    def handler(request):
        host = request.headers.get("host") or request.url.host
        if host == "api.openai.com":
            store.correct(
                sid,
                CorrectionIntent(
                    type="exclude_item",
                    target_id=book_id,
                    source_turn_id="t1",
                ),
            )
            return httpx.Response(200, json=discovery_response(book_id))
        if host == "open.er-api.com":
            return httpx.Response(200, content=fx_body())
        if host == "books.example":
            return httpx.Response(200, text=LISTING_PAGE)
        return httpx.Response(404)

    stub_pricing(client.app, handler)
    resp = post(client, f"/api/sweeps/{sid}/price_books", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valuations"][book_id]["replacement"] is None
    assert body["valuations"][book_id]["used"] is None
    loaded = client.get(f"/api/sweeps/{sid}").json()
    assert loaded["books"][0]["replacement_cost"] is None
    assert body["reviews_added"] == len(loaded["review_queue"]) - before


def test_groq_pricing_missing_key_never_calls_openai(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as groq_client:
        sweep_id = start_sweep(groq_client).json()["id"]
        book_id = seed_identified_book(groq_client, sweep_id)
        requests = stub_pricing(
            app, lambda request: httpx.Response(200, json=discovery_response(book_id))
        )
        reply = post(groq_client, f"/api/sweeps/{sweep_id}/price_books", json={})
        assert reply.status_code == 503
        assert requests == []
        stages = app.state.store.get(sweep_id).price_stages
        assert any(
            stage.kind == "discovery"
            and stage.status == "skipped"
            and stage.provider == "groq"
            and "GROQ_API_KEY" in (stage.error or "")
            for stage in stages
        )
