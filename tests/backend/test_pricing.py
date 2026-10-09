import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from backend.pricing import (
    PricingManager,
    cited_urls,
    discovery_costs,
    parse_discovery_response,
    price_books,
    validate_discovery,
)
from pydantic import ValidationError

from backend.models import CorrectionIntent, PricingStageRecord, ReviewItem
from backend.price_sources import extract_source_quote
from backend.sweeps import StoreError, SweepStore

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "backend" / "pricing_contract.json").read_text()
)
NOW = "2026-10-08T12:00:00+00:00"
GLOBAL_IP = "93.184.216.34"


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def scene_image(width=640, height=360, seed=7):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 255, (height, width, 3), dtype=np.uint8)


def seed_books(store, sweep_id, frame_id, count=1):
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
    state = store.commit_tracks(
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
    return [b.id for b in state.books]


def identify(store, sweep_id, title="Clean Code", author="Robert C. Martin", confidence=0.95):
    state = store.get(sweep_id)
    readings = [
        {
            "ref_id": b.id,
            "title": title,
            "author": author,
            "publisher": "",
            "edition": "",
            "isbn": "",
            "title_legible": True,
            "author_legible": True,
            "confidence": confidence,
            "visible_text": [title],
        }
        for b in state.books
    ]
    return store.apply_readings(sweep_id, {}, readings)


@pytest.fixture
def setup(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("US", "USD", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    return store, state, ids


def page(entry, url):
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(entry)
        + "</script></head><body>synthetic fixture listing</body></html>"
    )


def offer(price, currency, condition, url, region="US", **extra):
    node = {
        "@type": "Offer",
        "price": price,
        "priceCurrency": currency,
        "availability": "https://schema.org/InStock",
        "itemCondition": f"https://schema.org/{condition}",
        "url": url,
    }
    if region is not None:
        node["eligibleRegion"] = region
    node.update(extra)
    return node


def listing(url, price, currency, condition, region, title="Clean Code", author="Robert C. Martin"):
    entry = {
        "@context": "https://schema.org",
        "@type": "Book",
        "name": title,
        "author": {"@type": "Person", "name": author},
        "url": url,
        "bookFormat": "https://schema.org/Paperback",
        "offers": offer(price, currency, condition, url, region),
    }
    return page(entry, url)


def discovery_reply(records, cited, searches=1, usage=None, model=None, status="completed"):
    output = []
    if searches:
        output.append(
            {
                "id": "ws_1",
                "type": "web_search_call",
                "action": {
                    "type": "search",
                    "query": "listing",
                    "sources": [{"url": u} for u in cited],
                },
            }
        )
    output.append(
        {
            "id": "msg_1",
            "type": "message",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "text": json.dumps({"records": records, "issues": []}),
                    "annotations": [],
                }
            ],
        }
    )
    return {
        "id": "resp_1",
        "model": model or CONTRACT["model"],
        "status": status,
        "output": output,
        "usage": usage
        or {
            "input_tokens": 100,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 0},
            "total_tokens": 150,
        },
    }


def fx_body(rates=None, updates=None):
    now = datetime.now(timezone.utc)
    value = {
        "result": "success",
        "base_code": "USD",
        "time_last_update_unix": int((now - timedelta(hours=1)).timestamp()),
        "time_next_update_unix": int((now + timedelta(hours=23)).timestamp()),
        "rates": rates or {"USD": 1, "AED": 3.67, "GBP": 0.75},
    }
    value.update(updates or {})
    return json.dumps(value).encode()


async def global_resolver(host):
    return [GLOBAL_IP]


def make_client(pages=None, fx=None):
    pages = pages or {}
    calls = []

    def handler(request):
        calls.append(request)
        host = request.headers.get("host") or request.url.host
        if host == "open.er-api.com":
            if fx is None:
                return httpx.Response(503)
            return httpx.Response(200, content=fx)
        if host in pages:
            body = pages[host]
            return body(request) if callable(body) else httpx.Response(200, text=body)
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def run(store, sweep_id, **kwargs):
    return asyncio.run(
        price_books(store, sweep_id, contract=CONTRACT, api_key="sk-test", **kwargs)
    )


def test_us_listing_new_and_used_independent_amounts(setup):
    store, state, ids = setup
    identify(store, state.id)
    new_url = "https://books.example/clean-code/new"
    used_url = "https://books.example/clean-code/used"

    def dispatch(request):
        url = str(request.url).replace(f"https://{GLOBAL_IP}", "https://books.example")
        if url == new_url:
            return httpx.Response(200, text=listing(new_url, 10, "USD", "NewCondition", "US"))
        return httpx.Response(200, text=listing(used_url, 4, "USD", "UsedCondition", "US"))

    client, calls = make_client(pages={"books.example": dispatch}, fx=fx_body())
    records = [
        {
            "ref_id": ids[0],
            "replacement_urls": [new_url],
            "used_urls": [used_url],
            "notes": [],
        }
    ]
    reply_payloads = []

    async def provider(payload):
        reply_payloads.append(payload)
        return discovery_reply(records, [new_url, used_url])

    result = run(
        store,
        state.id,
        client=client,
        provider=provider,
        resolver=global_resolver,
    )
    books = store.get(state.id).books
    price = books[0].replacement_cost
    used = books[0].used_value
    assert price.amount == Decimal("10.00")
    assert price.currency == "USD"
    assert used.amount == Decimal("4.00")
    assert price.foreign_market_fallback is False
    assert price.source_quote.market_country == "US"
    assert price.source_quote.amount == Decimal("10")
    assert price.edition_equivalence == "current_print_equivalent"
    assert price.fx_evidence.kind == "currency_identity"
    assert used.condition_assumed == "good"
    input_json = json.loads(
        reply_payloads[0]["input"][0]["content"][0]["text"].split("INPUT_JSON: ", 1)[1]
    )
    assert input_json["country"] == "US"
    assert input_json["currency"] == "USD"
    stages = store.get(state.id).price_stages
    assert any(s.kind == "discovery" and s.status == "completed" for s in stages)
    assert any(s.kind == "valuation" and s.status == "completed" for s in stages)
    assert result["valuations"][ids[0]]["skipped"] is None


def _aed_sweep(tmp_path, currency, rates, aed_price="36.70"):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", currency, "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    identify(store, state.id)
    url = "https://souq.example/book/clean-code"
    pages = {
        "souq.example": listing(url, aed_price, "AED", "NewCondition", "AE")
    }
    client, calls = make_client(pages=pages, fx=fx_body(rates))
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]
    payloads = []

    async def provider(payload):
        payloads.append(payload)
        return discovery_reply(records, [url])

    return store, state, ids, url, client, calls, provider, payloads


def test_aed_listing_converts_to_usd_and_gbp(tmp_path):
    store, state, ids, url, client, calls, provider, payloads = _aed_sweep(
        tmp_path / "usd", "USD", {"USD": 1, "AED": 3.67, "GBP": 0.75}
    )
    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    book = store.get(state.id).books[0]
    price = book.replacement_cost
    assert price.amount == Decimal("10.00")
    assert price.currency == "USD"
    assert price.converted is True
    assert price.foreign_market_fallback is False
    assert price.source_quote.amount == Decimal("36.70")
    assert price.source_quote.currency == "AED"
    assert price.fx_evidence.kind == "provider_feed"
    assert price.fx_evidence.cross_rate == Decimal(1) / Decimal("3.67")
    input_json = json.loads(
        payloads[0]["input"][0]["content"][0]["text"].split("INPUT_JSON: ", 1)[1]
    )
    assert input_json["country"] == "AE"

    store2, state2, ids2, url2, client2, calls2, provider2, _ = _aed_sweep(
        tmp_path / "gbp", "GBP", {"USD": 1, "AED": 3.67, "GBP": 0.75}
    )
    run(store2, state2.id, client=client2, provider=provider2, resolver=global_resolver)
    book2 = store2.get(state2.id).books[0]
    assert book2.replacement_cost.amount == Decimal("7.50")
    assert book2.replacement_cost.currency == "GBP"


def test_jpy_and_kwd_minor_unit_rounding(tmp_path):
    for country, currency, price_str, rate, expected in (
        ("JP", "JPY", "1234.5", "150", "1235"),
        ("KW", "KWD", "1.2345", "0.31", "1.235"),
    ):
        store = SweepStore(tmp_path / f"runs-{currency}")
        state = store.start(country, currency, "dev")
        frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
        ids = seed_books(store, state.id, frame.id)
        identify(store, state.id)
        url = f"https://{country.lower()}.example/book"
        pages = {
            f"{country.lower()}.example": listing(
                url, price_str, currency, "NewCondition", country
            )
        }
        rates = {"USD": 1, "AED": 3.67, "GBP": 0.75, currency: float(rate)}
        client, _ = make_client(pages=pages, fx=fx_body(rates))
        records = [
            {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
        ]

        async def provider(payload, records=records):
            return discovery_reply(records, [url])

        run(store, state.id, client=client, provider=provider, resolver=global_resolver)
        book = store.get(state.id).books[0]
        assert book.replacement_cost.amount == Decimal(expected)


def test_fx_failure_and_missing_rate_keep_source_quote(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "EUR", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    identify(store, state.id)
    url = "https://souq.example/book"
    pages = {"souq.example": listing(url, "36.70", "AED", "NewCondition", "AE")}
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]

    async def provider(payload):
        return discovery_reply(records, [url])

    client, _ = make_client(pages=pages, fx=None)
    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    book = store.get(state.id).books[0]
    price = book.replacement_cost
    assert price.amount is None
    assert price.source_quote.amount == Decimal("36.70")
    assert price.source_quote.currency == "AED"
    assert any(
        "foreign exchange" in r.reason for r in store.get(state.id).review_queue
    )


def _payload_country(payload):
    text = payload["input"][0]["content"][0]["text"]
    return json.loads(text.split("INPUT_JSON: ", 1)[1])["country"]


def test_foreign_same_currency_fallback_flags(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("US", "USD", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    identify(store, state.id)
    url = "https://uk.example/book"
    pages = {"uk.example": listing(url, 10, "USD", "NewCondition", "GB")}
    client, _ = make_client(pages=pages, fx=fx_body())
    countries = []

    async def provider(payload):
        country = _payload_country(payload)
        countries.append(country)
        if country == "US":
            return discovery_reply(
                [{"ref_id": ids[0], "replacement_urls": [], "used_urls": [], "notes": []}],
                [],
            )
        return discovery_reply(
            [{"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}],
            [url],
        )

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    assert countries == ["US", "GB"]
    price = store.get(state.id).books[0].replacement_cost
    assert price.amount == Decimal("10.00")
    assert price.foreign_market_fallback is True
    assert price.foreign_market_same_currency is True
    assert price.converted is True
    assert price.fx_evidence.kind == "currency_identity"
    assert price.fx_evidence.cross_rate == Decimal(1)


def test_local_empty_triggers_fallback_discovery_in_second_country(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "AED", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    identify(store, state.id)
    url = "https://us.example/book"
    pages = {"us.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, _ = make_client(pages=pages, fx=fx_body())
    countries = []

    async def provider(payload):
        country = _payload_country(payload)
        countries.append(country)
        if country == "AE":
            return discovery_reply(
                [{"ref_id": ids[0], "replacement_urls": [], "used_urls": [], "notes": []}],
                [],
            )
        return discovery_reply(
            [{"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}],
            [url],
        )

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    assert countries == ["AE", "US"]
    price = store.get(state.id).books[0].replacement_cost
    assert price.amount == Decimal("36.70")
    assert price.currency == "AED"
    assert price.foreign_market_fallback is True
    assert price.source_quote.market_country == "US"


def test_local_slot_survives_foreign_fallback_for_missing_slot(setup):
    store, state, ids = setup
    identify(store, state.id)
    new_url = "https://books.example/new"
    used_url = "https://uk.example/used"
    pages = {
        "books.example": listing(new_url, 10, "USD", "NewCondition", "US"),
        "uk.example": listing(used_url, "7.50", "GBP", "UsedCondition", "GB"),
    }
    client, _ = make_client(pages=pages, fx=fx_body())
    countries = []

    async def provider(payload):
        country = _payload_country(payload)
        countries.append(country)
        if country == "US":
            return discovery_reply(
                [{"ref_id": ids[0], "replacement_urls": [new_url], "used_urls": [], "notes": []}],
                [new_url],
            )
        return discovery_reply(
            [{"ref_id": ids[0], "replacement_urls": [new_url], "used_urls": [used_url], "notes": []}],
            [new_url, used_url],
        )

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    assert countries == ["US", "GB"]
    book = store.get(state.id).books[0]
    assert book.replacement_cost.amount == Decimal("10.00")
    assert book.replacement_cost.foreign_market_fallback is False
    assert book.used_value.amount == Decimal("10.00")
    assert book.used_value.foreign_market_fallback is True
    assert book.used_value.source_quote.market_country == "GB"
    assert book.used_value.source_quote.currency == "GBP"


def test_fallback_wrong_actual_market_stays_null(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "AED", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    identify(store, state.id)
    url = "https://fr.example/book"
    pages = {"fr.example": listing(url, 12, "EUR", "NewCondition", "FR")}
    client, _ = make_client(pages=pages, fx=fx_body())
    countries = []

    async def provider(payload):
        country = _payload_country(payload)
        countries.append(country)
        if country == "AE":
            return discovery_reply(
                [{"ref_id": ids[0], "replacement_urls": [], "used_urls": [], "notes": []}],
                [],
            )
        return discovery_reply(
            [{"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}],
            [url],
        )

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    assert countries == ["AE", "US"]
    book = store.get(state.id).books[0]
    assert book.replacement_cost is None
    assert any(
        "outside US market" in r.reason for r in store.get(state.id).review_queue
    )


def test_incidental_foreign_source_in_local_response_not_substituted(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "AED", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    identify(store, state.id)
    url = "https://us.example/book"
    pages = {"us.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, _ = make_client(pages=pages, fx=fx_body())
    countries = []

    async def provider(payload):
        country = _payload_country(payload)
        countries.append(country)
        if country == "AE":
            return discovery_reply(
                [{"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}],
                [url],
            )
        return discovery_reply(
            [{"ref_id": ids[0], "replacement_urls": [], "used_urls": [], "notes": []}],
            [],
        )

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    assert countries == ["AE", "US"]
    book = store.get(state.id).books[0]
    assert book.replacement_cost is None
    assert any(
        "outside AE market" in r.reason for r in store.get(state.id).review_queue
    )


def test_used_never_populated_from_new_listing(setup):
    store, state, ids = setup
    identify(store, state.id)
    url = "https://books.example/new"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, _ = make_client(pages=pages, fx=fx_body())
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [url], "notes": []}
    ]

    async def provider(payload):
        return discovery_reply(records, [url])

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    book = store.get(state.id).books[0]
    assert book.replacement_cost.amount == Decimal("10.00")
    assert book.used_value is None
    assert any("used" in r.reason for r in store.get(state.id).review_queue)


@pytest.mark.parametrize("amount,appraisal", [("1000", False), ("1000.001", True)])
def test_appraisal_threshold_unrounded(tmp_path, amount, appraisal):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "AED", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    identify(store, state.id)
    url = "https://souq.example/book"
    pages = {
        "souq.example": listing(url, amount, "AED", "NewCondition", "AE")
    }
    client, _ = make_client(pages=pages, fx=fx_body())
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]

    async def provider(payload):
        return discovery_reply(records, [url])

    result = run(
        store, state.id, client=client, provider=provider, resolver=global_resolver
    )
    book = store.get(state.id).books[0]
    if appraisal:
        assert book.status == "needs_appraisal"
        assert book.replacement_cost is None
        assert book.used_value is None
        assert result["valuations"][ids[0]]["needs_appraisal"] is True
    else:
        assert book.status == "identified"
        assert book.replacement_cost.amount == Decimal("1000")


def test_uncited_url_is_never_fetched(setup):
    store, state, ids = setup
    identify(store, state.id)
    url = "https://books.example/sneaky"
    client, calls = make_client(pages={}, fx=fx_body())
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]

    async def provider(payload):
        return discovery_reply(records, ["https://books.example/other"], searches=0)

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    assert calls == []
    assert any("uncited" in r.reason for r in store.get(state.id).review_queue)
    assert store.get(state.id).books[0].replacement_cost is None


@pytest.mark.parametrize(
    "records,key",
    [
        ([{"ref_id": "book_unknown", "replacement_urls": [], "used_urls": [], "notes": []}], None),
        (None, "duplicate"),
        (None, "missing"),
        ([{"ref_id": "book_x", "replacement_urls": [], "used_urls": [], "notes": [], "price": 10}], "wire"),
    ],
)
def test_discovery_wire_rejections_are_safe_502(setup, records, key):
    store, state, ids = setup
    identify(store, state.id)
    if key == "duplicate":
        records = [
            {"ref_id": ids[0], "replacement_urls": [], "used_urls": [], "notes": []},
            {"ref_id": ids[0], "replacement_urls": [], "used_urls": [], "notes": []},
        ]
    elif key == "missing":
        records = []
    elif key == "wire":
        records = [
            {
                "ref_id": ids[0],
                "replacement_urls": [],
                "used_urls": [],
                "notes": [],
                "price": 10,
            }
        ]

    async def provider(payload):
        return discovery_reply(records, [], searches=0)

    with pytest.raises(StoreError) as info:
        run(store, state.id, provider=provider)
    assert info.value.status_code == 502
    assert store.get(state.id).books[0].replacement_cost is None


@pytest.mark.parametrize(
    "response",
    [
        {"status": "incomplete", "output": []},
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]},
        {"status": "completed", "output": []},
        "not a dict",
    ],
)
def test_discovery_envelope_rejections(response):
    with pytest.raises(StoreError):
        parse_discovery_response(response)


def test_cost_estimate_combines_token_and_search_action():
    response = discovery_reply([], ["https://x.example"], searches=1)
    token, tool, combined = discovery_costs(response, CONTRACT)
    assert token == Decimal("0.0003")
    assert tool == Decimal("0.01")
    assert combined == Decimal("0.0103")
    no_tool = discovery_reply([], [], searches=0)
    _, tool2, _ = discovery_costs(no_tool, CONTRACT)
    assert tool2 == Decimal("0")
    missing_usage = dict(response)
    missing_usage["usage"] = None
    token3, _, combined3 = discovery_costs(missing_usage, CONTRACT)
    assert token3 is None and combined3 is None


def test_ineligible_books_get_null_with_review(setup):
    store, state, ids = setup
    sid = state.id
    result = run(store, sid, book_ids=ids)
    assert result["valuations"][ids[0]]["skipped"] == "book not identified"
    assert any(
        r.ref_id == ids[0] and "pricing skipped" in r.reason
        for r in store.get(sid).review_queue
    )
    identify(store, sid)
    store.add_turn(sid, "t1", "exclude it", "typed_user")
    store.correct(
        sid,
        CorrectionIntent(
            type="exclude_item", target_id=ids[0], source_turn_id="t1"
        ),
    )
    result = run(store, sid, book_ids=ids)
    assert result["valuations"][ids[0]]["skipped"] == "book excluded"


def test_ineligible_variants_get_null_with_review(tmp_path):
    cases = {
        "potential_duplicate": (
            lambda book: book.model_copy(update={"potential_duplicate": True}),
            "potential duplicate",
        ),
        "appraisal": (
            lambda book: book.model_copy(update={"status": "needs_appraisal"}),
            "book needs appraisal",
        ),
        "low_confidence": (
            lambda book: book.model_copy(
                update={
                    "identification_evidence": [
                        r.model_copy(update={"confidence": 0.5})
                        for r in book.identification_evidence
                    ]
                }
            ),
            "no accepted high-confidence reading",
        ),
        "contradicted": (
            lambda book: book.model_copy(
                update={
                    "identification_evidence": [
                        r.model_copy(update={"status": "contradicted"})
                        for r in book.identification_evidence
                    ]
                }
            ),
            "unresolved reading contradiction",
        ),
    }
    for name, (mutate, reason) in cases.items():
        store = SweepStore(tmp_path / f"runs-{name}")
        state = store.start("US", "USD", "dev")
        frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
        ids = seed_books(store, state.id, frame.id)
        identify(store, state.id)
        with store._lock:
            st = store._loaded(state.id)
            st.books[0] = mutate(st.books[0])
            store._save(st)
        called = []

        async def provider(payload):
            called.append(payload)
            return discovery_reply([], [])

        result = run(store, state.id, provider=provider)
        assert result["valuations"][ids[0]]["skipped"] == reason
        assert called == []
        assert any(
            r.ref_id == ids[0] and "pricing skipped" in r.reason
            for r in store.get(state.id).review_queue
        )


def test_restart_converts_running_stage_to_interrupted_and_counts_budget(setup):
    store, state, ids = setup
    identify(store, state.id)
    store.record_price_stage(
        state.id,
        PricingStageRecord(kind="discovery", ref_ids=[ids[0]], status="running"),
    )
    reloaded = SweepStore(store.root)
    stage = next(
        s for s in reloaded.get(state.id).price_stages if s.kind == "discovery"
    )
    assert stage.status == "interrupted"
    for _ in range(23):
        reloaded.record_price_stage(
            state.id,
            PricingStageRecord(kind="discovery", ref_ids=[ids[0]], status="completed"),
        )
    called = []

    async def provider(payload):
        called.append(payload)
        return discovery_reply([], [])

    run(reloaded, state.id, provider=provider)
    assert called == []


def test_synthetic_amazon_uae_end_to_end_new_only(tmp_path):
    from tests.backend.test_price_sources import AMAZON_PAGE, AMAZON_URL

    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "AED", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id)
    identify(store, state.id)
    client, calls = make_client(pages={"www.amazon.ae": AMAZON_PAGE}, fx=fx_body())
    records = [
        {
            "ref_id": ids[0],
            "replacement_urls": [AMAZON_URL],
            "used_urls": [AMAZON_URL],
            "notes": [],
        }
    ]

    async def provider(payload):
        return discovery_reply(records, [AMAZON_URL])

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    book = store.get(state.id).books[0]
    assert book.replacement_cost.amount == Decimal("176.98")
    assert book.replacement_cost.currency == "AED"
    assert book.replacement_cost.source_quote.market_country == "AE"
    assert book.used_value is None
    assert any("used" in r.reason for r in store.get(state.id).review_queue)


def test_missing_api_key_503_without_fetch_or_provider(setup):
    store, state, ids = setup
    identify(store, state.id)
    client, calls = make_client()
    with pytest.raises(StoreError) as info:
        asyncio.run(
            price_books(
                store, state.id, client=client, contract=CONTRACT, api_key=None
            )
        )
    assert info.value.status_code == 503
    assert calls == []


def test_concurrent_price_work_gets_409(setup):
    store, state, ids = setup
    identify(store, state.id)
    mgr = PricingManager(CONTRACT)
    mgr.acquire(state.id)
    with pytest.raises(StoreError) as info:
        run(store, state.id, manager=mgr)
    assert info.value.status_code == 409


def test_discovery_budget_cap_records_skip(setup):
    store, state, ids = setup
    identify(store, state.id)
    from backend.models import PricingStageRecord

    for _ in range(24):
        store.record_price_stage(
            state.id,
            PricingStageRecord(kind="discovery", ref_ids=[ids[0]], status="completed"),
        )
    called = []

    async def provider(payload):
        called.append(payload)
        return discovery_reply([], [])

    run(store, state.id, provider=provider)
    assert called == []
    assert any("budget exhausted" in r.reason for r in store.get(state.id).review_queue)


def test_late_exclusion_and_identity_change_suppress_attach(setup):
    store, state, ids = setup
    identify(store, state.id)
    url = "https://books.example/book"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, _ = make_client(pages=pages, fx=fx_body())
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]

    store.add_turn(state.id, "t1", "exclude it", "typed_user")

    async def provider(payload):
        store.correct(
            state.id,
            CorrectionIntent(
                type="exclude_item", target_id=ids[0], source_turn_id="t1"
            ),
        )
        return discovery_reply(records, [url])

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    book = store.get(state.id).books[0]
    assert book.replacement_cost is None
    assert book.excluded is True
    assert any("suppressed" in r.reason for r in store.get(state.id).review_queue)


def test_late_assertion_and_identity_change_suppress_attach(tmp_path):
    for mutate, expected_status in (
        (
            lambda store, sid, bid: (
                store.add_turn(sid, "t1", "first edition", "typed_user"),
                store.correct(
                    sid,
                    CorrectionIntent(
                        type="assert_edition",
                        target_id=bid,
                        value="first edition",
                        source_turn_id="t1",
                    ),
                ),
            ),
            "needs_appraisal",
        ),
        (
            lambda store, sid, bid: store.apply_readings(
                sid,
                {},
                [
                    {
                        "ref_id": bid,
                        "title": "Different Book",
                        "author": "Robert C. Martin",
                        "publisher": "",
                        "edition": "",
                        "isbn": "",
                        "title_legible": True,
                        "author_legible": True,
                        "confidence": 0.95,
                        "visible_text": ["Different Book"],
                    }
                ],
            ),
            "unidentified",
        ),
    ):
        store = SweepStore(tmp_path / f"runs-{expected_status}")
        state = store.start("US", "USD", "dev")
        frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
        ids = seed_books(store, state.id, frame.id)
        identify(store, state.id)
        url = "https://books.example/book"
        pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
        client, _ = make_client(pages=pages, fx=fx_body())
        records = [
            {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
        ]

        async def provider(payload, mutate=mutate):
            mutate(store, state.id, ids[0])
            return discovery_reply(records, [url])

        run(
            store,
            state.id,
            client=client,
            provider=provider,
            resolver=global_resolver,
        )
        book = store.get(state.id).books[0]
        assert book.status == expected_status
        assert book.replacement_cost is None
        assert any("suppressed" in r.reason for r in store.get(state.id).review_queue)


def test_identical_copies_share_quote_cache(setup):
    store, state, ids = setup
    seed_books(store, state.id, store.get(state.id).frames[0].id, count=1)
    identify(store, state.id)
    url = "https://books.example/book"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, calls = make_client(pages=pages, fx=fx_body())
    records = [
        {
            "ref_id": bid,
            "replacement_urls": [url],
            "used_urls": [],
            "notes": [],
        }
        for bid in [b.id for b in store.get(state.id).books]
    ]

    async def provider(payload):
        return discovery_reply(records, [url])

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    fetches = [r for r in calls if r.headers.get("host") == "books.example"]
    assert len(fetches) == 1
    books = store.get(state.id).books
    assert len(books) == 2
    assert all(b.replacement_cost.amount == Decimal("10.00") for b in books)
    assert books[0].id != books[1].id
    stages = store.get(state.id).price_stages
    assert any(s.kind == "source" and s.status == "cached" for s in stages)


def test_pricing_deadline_expired_skips_provider(setup):
    store, state, ids = setup
    identify(store, state.id)
    store.init_pricing_deadline(
        state.id, (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    )
    called = []

    async def provider(payload):
        called.append(payload)
        return discovery_reply([], [])

    mgr = PricingManager(CONTRACT)
    with pytest.raises(StoreError) as info:
        run(store, state.id, provider=provider, manager=mgr)
    assert info.value.status_code == 429
    assert called == []


def test_discovery_stage_persists_usage_and_tool_cost(setup):
    store, state, ids = setup
    identify(store, state.id)
    url = "https://books.example/book"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, _ = make_client(pages=pages, fx=fx_body())
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]

    async def provider(payload):
        return discovery_reply(records, [url])

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    stage = next(
        s for s in store.get(state.id).price_stages if s.kind == "discovery"
    )
    assert stage.status == "completed"
    assert stage.cost_usd_estimate == "0.0103"
    assert stage.usage["input_tokens"] == 100
    assert stage.model == CONTRACT["model"]
    stage_file = Path(store.root) / state.id / stage.persisted_ref
    assert json.loads(stage_file.read_text())["raw_response"]["status"] == "completed"


def test_cited_urls_collected_from_sources_and_annotations():
    response = {
        "output": [
            {
                "type": "web_search_call",
                "action": {"type": "search", "sources": [{"url": "https://a.example/x"}]},
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "{}",
                        "annotations": [
                            {"type": "url_citation", "url": "https://b.example/y"}
                        ],
                    }
                ],
            },
        ]
    }
    assert cited_urls(response) == {
        "https://a.example/x",
        "https://b.example/y",
    }


def make_async_client(dispatch):
    calls = []

    async def record(request):
        calls.append(request)
        return await dispatch(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(record)), calls


def test_distinct_books_fetch_sources_concurrently(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("US", "USD", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id, count=2)
    store.apply_readings(
        state.id,
        {},
        [
            {
                "ref_id": ids[0],
                "title": "Clean Code",
                "author": "Robert C. Martin",
                "publisher": "",
                "edition": "",
                "isbn": "",
                "title_legible": True,
                "author_legible": True,
                "confidence": 0.95,
                "visible_text": ["Clean Code"],
            },
            {
                "ref_id": ids[1],
                "title": "Refactoring",
                "author": "Martin Fowler",
                "publisher": "",
                "edition": "",
                "isbn": "",
                "title_legible": True,
                "author_legible": True,
                "confidence": 0.95,
                "visible_text": ["Refactoring"],
            },
        ],
    )
    works = {
        ids[0]: ("Clean Code", "Robert C. Martin"),
        ids[1]: ("Refactoring", "Martin Fowler"),
    }
    urls = {bid: f"https://books.example/{i}" for i, bid in enumerate(ids)}

    async def go():
        entered = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def dispatch(request):
            host = request.headers.get("host") or request.url.host
            if host == "open.er-api.com":
                return httpx.Response(200, content=fx_body())
            if host == "books.example":
                seen.append(request)
                if len(seen) == 2:
                    entered.set()
                await asyncio.wait_for(release.wait(), 5)
                path = request.url.path
                url = f"https://books.example{path}"
                bid = next(k for k, v in urls.items() if v == url)
                title, author = works[bid]
                return httpx.Response(
                    200,
                    text=listing(
                        url, 10, "USD", "NewCondition", "US",
                        title=title, author=author,
                    ),
                )
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
        records = [
            {
                "ref_id": bid,
                "replacement_urls": [urls[bid]],
                "used_urls": [],
                "notes": [],
            }
            for bid in ids
        ]

        async def provider(payload):
            return discovery_reply(records, list(urls.values()))

        task = asyncio.create_task(
            price_books(
                store,
                state.id,
                client=client,
                contract=CONTRACT,
                api_key="sk-test",
                provider=provider,
                resolver=global_resolver,
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        return await task, seen

    _, seen = asyncio.run(go())
    assert len(seen) == 2
    books = store.get(state.id).books
    assert all(b.replacement_cost.amount == Decimal("10.00") for b in books)


def test_source_budget_counts_real_http_hops_including_redirects(setup):
    store, state, ids = setup
    identify(store, state.id)
    for _ in range(199):
        store.record_price_stage(
            state.id,
            PricingStageRecord(kind="source", ref_ids=[ids[0]], status="completed"),
        )
    url = "https://books.example/book"
    target = "https://books.example/final"

    async def dispatch(request):
        host = request.headers.get("host") or request.url.host
        if host == "open.er-api.com":
            return httpx.Response(200, content=fx_body())
        if host == "books.example" and request.url.path == "/book":
            return httpx.Response(
                302,
                headers={"location": target},
            )
        if host == "books.example":
            return httpx.Response(
                200, text=listing(target, 10, "USD", "NewCondition", "US")
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]

    async def provider(payload):
        return discovery_reply(records, [url])

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    book = store.get(state.id).books[0]
    assert book.replacement_cost is None
    reasons = [r.reason for r in store.get(state.id).review_queue]
    assert any("budget exhausted" in r for r in reasons)
    source_stages = [
        s for s in store.get(state.id).price_stages if s.kind == "source"
    ]
    assert not any(s.status == "running" for s in source_stages)


def test_shared_deadline_clamps_reading_and_pricing_both_ways(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("US", "USD", "dev")
    early = datetime.now(timezone.utc) + timedelta(seconds=60)
    late = datetime.now(timezone.utc) + timedelta(seconds=240)
    store.init_pricing_deadline(state.id, early.isoformat())
    assert store.init_reading_deadline(state.id, late.isoformat()) == early.isoformat()
    store2 = SweepStore(tmp_path / "runs2")
    state2 = store2.start("US", "USD", "dev")
    store2.init_reading_deadline(state2.id, early.isoformat())
    assert store2.init_pricing_deadline(
        state2.id, late.isoformat()
    ) == early.isoformat()


def test_deadline_expired_before_source_fetch_cancels_with_review(setup):
    store, state, ids = setup
    identify(store, state.id)
    url = "https://books.example/book"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, calls = make_client(pages=pages, fx=fx_body())
    wall = [datetime.now(timezone.utc)]

    async def provider(payload):
        wall[0] = wall[0] + timedelta(seconds=400)
        return discovery_reply(
            [{"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}],
            [url],
        )

    mgr = PricingManager(CONTRACT, wallclock=lambda: wall[0])
    run(
        store,
        state.id,
        client=client,
        provider=provider,
        resolver=global_resolver,
        manager=mgr,
    )
    book = store.get(state.id).books[0]
    assert book.replacement_cost is None
    assert any(
        "deadline" in r.reason for r in store.get(state.id).review_queue
    )
    assert not any(
        s.status == "running" for s in store.get(state.id).price_stages
    )
    fetches = [r for r in calls if r.headers.get("host") == "books.example"]
    assert fetches == []


def test_failed_batch_isolated_successful_batch_commits(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("US", "USD", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(store, state.id, frame.id, count=6)
    identify(store, state.id)
    url = "https://books.example/book"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, _ = make_client(pages=pages, fx=fx_body())
    calls = []
    good = ids[-1]

    async def provider(payload):
        calls.append(payload)
        books = json.loads(
            payload["input"][0]["content"][0]["text"].split("INPUT_JSON: ", 1)[1]
        )["books"]
        if any(b["ref_id"] == good for b in books):
            return discovery_reply(
                [
                    {
                        "ref_id": good,
                        "replacement_urls": [url],
                        "used_urls": [],
                        "notes": [],
                    }
                ],
                [url],
            )
        return {"status": "incomplete", "output": []}

    result = run(
        store, state.id, client=client, provider=provider, resolver=global_resolver
    )
    assert result["partial"] is True
    book = next(b for b in store.get(state.id).books if b.id == good)
    assert book.replacement_cost.amount == Decimal("10.00")
    failed_ids = [bid for bid in ids if bid != good]
    reasons = store.get(state.id).review_queue
    for bid in failed_ids:
        assert any(
            r.ref_id == bid and "discovery" in r.reason for r in reasons
        ), bid
    assert not any(
        s.status == "running" for s in store.get(state.id).price_stages
    )


def test_all_discovery_failed_raises_first_error(setup):
    store, state, ids = setup
    identify(store, state.id)

    async def provider(payload):
        return {"status": "incomplete", "output": []}

    with pytest.raises(StoreError) as info:
        run(store, state.id, provider=provider)
    assert info.value.status_code == 502
    assert not any(
        s.status == "running" for s in store.get(state.id).price_stages
    )


def test_discovery_costs_accepts_non_dict_raw():
    assert discovery_costs("not a dict", CONTRACT) == (None, None, None)
    assert discovery_costs(["list"], CONTRACT) == (None, None, None)


def test_cached_quote_within_24h_skips_paid_work_and_keeps_proof(setup):
    store, state, ids = setup
    identify(store, state.id)
    new_url = "https://books.example/new"
    used_url = "https://books.example/used"
    pages = {
        "books.example": {
            new_url: listing(new_url, 10, "USD", "NewCondition", "US"),
            used_url: listing(used_url, 4, "USD", "UsedCondition", "US"),
        }
    }

    def dispatch(request):
        url = str(request.url).replace(
            f"https://{GLOBAL_IP}", "https://books.example"
        )
        if url in pages["books.example"]:
            return httpx.Response(200, text=pages["books.example"][url])
        return httpx.Response(404)

    client, calls = make_client(pages={"books.example": dispatch}, fx=fx_body())
    wall = [datetime.now(timezone.utc)]
    mgr = PricingManager(CONTRACT, wallclock=lambda: wall[0])
    records = [
        {
            "ref_id": ids[0],
            "replacement_urls": [new_url],
            "used_urls": [used_url],
            "notes": [],
        }
    ]
    provider_calls = []

    async def provider(payload):
        provider_calls.append(payload)
        return discovery_reply(records, [new_url, used_url])

    async def go():
        await price_books(
            store,
            state.id,
            client=client,
            contract=CONTRACT,
            api_key="sk-test",
            provider=provider,
            resolver=global_resolver,
            manager=mgr,
        )
        first_fetch_count = len(
            [r for r in calls if r.headers.get("host") == "books.example"]
        )
        assert first_fetch_count == 2
        paid_stage = next(
            s
            for s in store.get(state.id).price_stages
            if s.kind == "source" and s.status == "completed"
        )
        quote_ref = (
            store.get(state.id)
            .books[0]
            .replacement_cost.source_quote.source_snapshot_ref
        )
        assert quote_ref == paid_stage.persisted_ref

        wall[0] = wall[0] + timedelta(hours=2)
        await price_books(
            store,
            state.id,
            client=client,
            contract=CONTRACT,
            api_key="sk-test",
            provider=provider,
            resolver=global_resolver,
            manager=mgr,
        )

    asyncio.run(go())
    assert len(provider_calls) == 1
    assert (
        len([r for r in calls if r.headers.get("host") == "books.example"]) == 2
    )
    cached_stages = [
        s
        for s in store.get(state.id).price_stages
        if s.kind == "source" and s.status == "cached"
    ]
    assert len(cached_stages) == 2
    assert all(s.cost_usd_estimate == "0" for s in cached_stages)
    assert all(s.persisted_ref is not None for s in cached_stages)
    source_quote = store.get(state.id).books[0].replacement_cost.source_quote
    assert source_quote.discovery_stage_ref is not None


def test_cache_older_than_24h_refreshes(setup):
    store, state, ids = setup
    identify(store, state.id)
    url = "https://books.example/book"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, calls = make_client(pages=pages, fx=fx_body())
    wall = [datetime.now(timezone.utc)]
    mgr = PricingManager(CONTRACT, wallclock=lambda: wall[0])
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]
    provider_calls = []

    async def provider(payload):
        provider_calls.append(payload)
        return discovery_reply(records, [url])

    async def go():
        await price_books(
            store,
            state.id,
            client=client,
            contract=CONTRACT,
            api_key="sk-test",
            provider=provider,
            resolver=global_resolver,
            manager=mgr,
        )
        for quote in mgr._quotes.values():
            quote["retrieved_at"] = (wall[0] - timedelta(hours=25)).isoformat()
        await price_books(
            store,
            state.id,
            client=client,
            contract=CONTRACT,
            api_key="sk-test",
            provider=provider,
            resolver=global_resolver,
            manager=mgr,
        )

    asyncio.run(go())
    assert len(provider_calls) == 4
    assert (
        len([r for r in calls if r.headers.get("host") == "books.example"]) == 2
    )


def test_cached_quote_rejects_future_and_stale_retrieved_at():
    mgr = PricingManager(CONTRACT)
    key = ("sweep_1", "work", "author", None, None, "US", "US", "USD", "new")
    mgr.cache_quote(
        key,
        {
            "amount": Decimal("10"),
            "currency": "USD",
            "market_country": "US",
            "retrieved_at": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
        },
    )
    assert mgr.cached_quote(key) is None
    mgr.cache_quote(
        key,
        {
            "amount": Decimal("10"),
            "currency": "USD",
            "market_country": "US",
            "retrieved_at": (
                datetime.now(timezone.utc) - timedelta(hours=25)
            ).isoformat(),
        },
    )
    assert mgr.cached_quote(key) is None
    fresh = {
        "amount": Decimal("10"),
        "currency": "USD",
        "market_country": "US",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    mgr.cache_quote(key, fresh)
    assert mgr.cached_quote(key) is fresh
    assert mgr.cached_quote(key)["retrieved_at"] == fresh["retrieved_at"]


def test_first_eligible_local_url_stops_without_second_fetch(setup):
    store, state, ids = setup
    identify(store, state.id)
    url1 = "https://books.example/first"
    url2 = "https://books.example/second"
    pages = {
        "books.example": {
            url1: listing(url1, 10, "USD", "NewCondition", "US"),
            url2: listing(url2, 20, "USD", "NewCondition", "US"),
        }
    }

    def dispatch(request):
        url = str(request.url).replace(
            f"https://{GLOBAL_IP}", "https://books.example"
        )
        if url in pages["books.example"]:
            return httpx.Response(200, text=pages["books.example"][url])
        return httpx.Response(404)

    client, calls = make_client(pages={"books.example": dispatch}, fx=fx_body())
    records = [
        {
            "ref_id": ids[0],
            "replacement_urls": [url1, url2],
            "used_urls": [],
            "notes": [],
        }
    ]

    async def provider(payload):
        return discovery_reply(records, [url1, url2])

    run(store, state.id, client=client, provider=provider, resolver=global_resolver)
    fetches = [
        str(r.url) for r in calls if r.headers.get("host") == "books.example"
    ]
    assert len(fetches) == 1
    book = store.get(state.id).books[0]
    assert book.replacement_cost.amount == Decimal("10.00")


def test_response_reflects_late_suppression_and_exact_review_count(setup):
    store, state, ids = setup
    identify(store, state.id)
    new_url = "https://books.example/new"
    used_url = "https://books.example/used"
    pages = {
        "books.example": {
            new_url: listing(new_url, 10, "USD", "NewCondition", "US"),
            used_url: listing(used_url, 4, "USD", "UsedCondition", "US"),
        }
    }

    def dispatch(request):
        url = str(request.url).replace(
            f"https://{GLOBAL_IP}", "https://books.example"
        )
        body = pages["books.example"].get(url)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body)

    client, _ = make_client(pages={"books.example": dispatch}, fx=fx_body())
    records = [
        {
            "ref_id": ids[0],
            "replacement_urls": [new_url],
            "used_urls": [used_url],
            "notes": [],
        }
    ]
    store.add_turn(state.id, "t1", "exclude it", "typed_user")
    store.append_reviews(
        state.id, [ReviewItem(ref_id=ids[0], reason="preexisting")]
    )
    initial = len(store.get(state.id).review_queue)

    async def provider(payload):
        store.correct(
            state.id,
            CorrectionIntent(
                type="exclude_item", target_id=ids[0], source_turn_id="t1"
            ),
        )
        return discovery_reply(records, [new_url, used_url])

    result = run(
        store, state.id, client=client, provider=provider, resolver=global_resolver
    )
    final = store.get(state.id)
    assert result["valuations"][ids[0]]["replacement"] is None
    assert result["valuations"][ids[0]]["used"] is None
    assert final.books[0].replacement_cost is None
    assert final.books[0].used_value is None
    assert result["partial"] is True
    assert result["reviews_added"] == len(final.review_queue) - initial


def test_409_busy_until_all_children_finished(setup):
    store, state, ids = setup
    identify(store, state.id)
    url = "https://books.example/book"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, _ = make_client(pages=pages, fx=fx_body())
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]

    async def go():
        mgr = PricingManager(CONTRACT)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def provider(payload):
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
            return discovery_reply(records, [url])

        task = asyncio.create_task(
            price_books(
                store,
                state.id,
                client=client,
                contract=CONTRACT,
                api_key="sk-test",
                provider=provider,
                resolver=global_resolver,
                manager=mgr,
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(StoreError) as info:
            await price_books(
                store,
                state.id,
                client=client,
                contract=CONTRACT,
                api_key="sk-test",
                provider=provider,
                manager=mgr,
            )
        assert info.value.status_code == 409
        release.set()
        await task
        return mgr

    mgr = asyncio.run(go())
    assert state.id not in mgr._busy


def test_price_models_reject_bad_money_and_fx_metadata():
    from backend.models import BookPrice, FxEvidence, SourceQuote

    quote = SourceQuote(
        amount=Decimal("10"),
        currency="USD",
        condition="new",
        market_country="US",
        url="https://books.example/x",
        retrieved_at="2026-10-08T12:00:00+00:00",
        matched_title="clean code",
        matched_author="robert c martin",
        listing_format="paperback",
        listing_availability="instock",
        market_evidence="eligible_region",
    )
    with pytest.raises(ValidationError):
        BookPrice(
            amount=Decimal("NaN"),
            currency="USD",
            source="listing",
            url="https://books.example/x",
            retrieved_at="2026-10-08T12:00:00+00:00",
            source_quote=quote,
        )
    with pytest.raises(ValidationError):
        BookPrice(
            amount=Decimal("10.00"),
            currency="USD",
            source="listing",
            source_quote=quote,
        )
    with pytest.raises(ValidationError):
        BookPrice(
            amount=Decimal("10.00"),
            currency="USD",
            source="listing",
            url="https://books.example/x",
            retrieved_at="2026-10-08T12:00:00+00:00",
        )
    with pytest.raises(ValidationError):
        FxEvidence(
            kind="provider_feed",
            retrieved_at="2026-10-08T12:00:00+00:00",
            source_currency="AED",
            target_currency="USD",
            source_rate=Decimal("3.67"),
            target_rate=Decimal("1"),
            cross_rate=Decimal("0.999"),
        )
    with pytest.raises(ValidationError):
        FxEvidence(
            kind="provider_feed",
            retrieved_at="2026-10-08T12:00:00+00:00",
            source_currency="AED",
            target_currency="USD",
            source_rate=Decimal("3.67"),
            target_rate=Decimal("1"),
            cross_rate=Decimal(1) / Decimal("3.67"),
        )
    with pytest.raises(ValidationError):
        FxEvidence(
            kind="currency_identity",
            retrieved_at="2026-10-08T12:00:00+00:00",
            source_currency="AED",
            target_currency="USD",
            source_rate=Decimal(1),
            target_rate=Decimal(1),
            cross_rate=Decimal(1),
        )


def _payload_ref_ids(payload):
    text = payload["input"][0]["content"][0]["text"]
    return [b["ref_id"] for b in json.loads(text.split("INPUT_JSON: ", 1)[1])["books"]]


def test_quote_cache_scoped_to_sweep(tmp_path):
    store = SweepStore(tmp_path / "runs")
    sweep_ids = []
    book_ids = []
    for _ in range(2):
        state = store.start("US", "USD", "dev")
        frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
        ids = seed_books(store, state.id, frame.id)
        identify(store, state.id)
        sweep_ids.append(state.id)
        book_ids.append(ids[0])
    url = "https://books.example/book"
    pages = {"books.example": listing(url, 10, "USD", "NewCondition", "US")}
    client, calls = make_client(pages=pages, fx=fx_body())
    mgr = PricingManager(CONTRACT)
    provider_calls = []

    async def provider(payload):
        provider_calls.append(payload)
        return discovery_reply(
            [
                {
                    "ref_id": ref,
                    "replacement_urls": [url],
                    "used_urls": [],
                    "notes": [],
                }
                for ref in _payload_ref_ids(payload)
            ],
            [url],
        )

    async def go():
        for sid in sweep_ids:
            await price_books(
                store,
                sid,
                client=client,
                contract=CONTRACT,
                api_key="sk-test",
                provider=provider,
                resolver=global_resolver,
                manager=mgr,
            )

    asyncio.run(go())
    fetches = [r for r in calls if r.headers.get("host") == "books.example"]
    assert len(fetches) == 2
    assert len(provider_calls) == 4
    for sid, bid in zip(sweep_ids, book_ids):
        sweep = store.get(sid)
        quote = sweep.books[0].replacement_cost.source_quote
        for ref in (quote.source_snapshot_ref, quote.discovery_stage_ref):
            assert ref is not None
            assert sid in ref
            target = Path(store._dir(sid)) / ref
            assert target.is_file()
        other = [s for s in sweep_ids if s != sid][0]
        assert other not in quote.source_snapshot_ref


def test_deadline_init_compares_aware_instants(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("US", "USD", "dev")
    earlier = datetime(2026, 10, 8, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    later = datetime(2026, 10, 8, 10, 30, tzinfo=timezone.utc)
    assert earlier < later
    assert earlier.isoformat() > later.isoformat()
    assert store.init_reading_deadline(state.id, earlier.isoformat()) == earlier.isoformat()
    assert (
        store.init_pricing_deadline(state.id, later.isoformat())
        == earlier.isoformat()
    )


def test_malformed_persisted_deadline_fails_closed(setup):
    store, state, ids = setup
    identify(store, state.id)
    store.init_reading_deadline(state.id, "not-a-date")
    called = []

    async def provider(payload):
        called.append(payload)
        return discovery_reply([], [])

    with pytest.raises(StoreError) as info:
        run(store, state.id, provider=provider)
    assert info.value.status_code == 429
    assert called == []


def test_naive_persisted_deadline_fails_closed(setup):
    store, state, ids = setup
    identify(store, state.id)
    store.init_reading_deadline(state.id, "2999-01-01T00:00:00")
    called = []

    async def provider(payload):
        called.append(payload)
        return discovery_reply([], [])

    with pytest.raises(StoreError) as info:
        run(store, state.id, provider=provider)
    assert info.value.status_code == 429
    assert called == []


def _duplicate_price_page(url):
    body = (
        '{"@context":"https://schema.org","@type":"Book","name":"Clean Code",'
        '"author":{"@type":"Person","name":"Robert C. Martin"},'
        f'"url":"{url}","bookFormat":"https://schema.org/Paperback",'
        '"offers":{"@type":"Offer","price":10,"price":99,'
        '"priceCurrency":"USD","availability":"https://schema.org/InStock",'
        f'"itemCondition":"https://schema.org/NewCondition","url":"{url}",'
        '"eligibleRegion":"US"}}'
    )
    return (
        '<html><head><script type="application/ld+json">'
        + body
        + "</script></head><body>dup</body></html>"
    )


def test_duplicate_json_ld_price_key_abstains(setup):
    store, state, ids = setup
    identify(store, state.id)
    url = "https://books.example/book"
    pages = {"books.example": _duplicate_price_page(url)}
    client, _ = make_client(pages=pages, fx=fx_body())
    records = [
        {"ref_id": ids[0], "replacement_urls": [url], "used_urls": [], "notes": []}
    ]

    async def provider(payload):
        return discovery_reply(records, [url])

    result = run(
        store, state.id, client=client, provider=provider, resolver=global_resolver
    )
    book = store.get(state.id).books[0]
    assert book.replacement_cost is None
    assert result["partial"] is True
    assert any(
        "no supported" in r.reason or "listing" in r.reason
        for r in store.get(state.id).review_queue
    )
    assert not any(
        s.status == "running" for s in store.get(state.id).price_stages
    )


def test_malformed_json_ld_abstains():
    html = (
        '<html><head><script type="application/ld+json">'
        + '{"@type":"Book","name":"Clean Code","offers":'
        + "</script></head><body>broken</body></html>"
    )
    quote = extract_source_quote(
        html, "https://books.example/x", NOW, "Clean Code", "Robert C. Martin", "new"
    )
    assert quote is None


def test_valid_slots_with_fx_failure_partial_and_proof_retained(setup):
    store, state, ids = setup
    identify(store, state.id)
    new_url = "https://books.example/new"
    used_url = "https://books.example/used"
    pages = {
        "books.example": {
            new_url: listing(new_url, 10, "USD", "NewCondition", "US"),
            used_url: listing(used_url, 4, "USD", "UsedCondition", "US"),
        }
    }

    def dispatch(request):
        url = str(request.url).replace(f"https://{GLOBAL_IP}", "https://books.example")
        body = pages["books.example"].get(url)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body)

    client, _ = make_client(pages={"books.example": dispatch}, fx=None)
    records = [
        {
            "ref_id": ids[0],
            "replacement_urls": [new_url],
            "used_urls": [used_url],
            "notes": [],
        }
    ]

    async def provider(payload):
        return discovery_reply(records, [new_url, used_url])

    result = run(
        store, state.id, client=client, provider=provider, resolver=global_resolver
    )
    book = store.get(state.id).books[0]
    assert result["partial"] is True
    assert book.replacement_cost is not None
    assert book.replacement_cost.amount is None
    assert book.replacement_cost.currency == "USD"
    assert book.replacement_cost.source_quote.url == new_url
    assert book.replacement_cost.source_quote.amount == Decimal("10")
    assert book.replacement_cost.fx_evidence is None
    assert any(
        "foreign exchange" in r.reason for r in store.get(state.id).review_queue
    )
