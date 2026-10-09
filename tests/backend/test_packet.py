import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import server
from backend.models import (
    BookPrice,
    CorrectionIntent,
    FxEvidence,
    ReviewItem,
    SourceQuote,
)
from backend.packet import build_packet, publish_packet, render_report_html
from backend.sweeps import StoreError, SweepStore

ORIGIN = "http://localhost:8001"
NOW = "2026-10-08T12:00:00+00:00"
TITLE = "Clean Code"
AUTHOR = "Robert C. Martin"


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def scene_image(seed=7):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 255, (360, 640, 3), dtype=np.uint8)


def listing(url, price, currency, condition, region, title=TITLE, author=AUTHOR):
    entry = {
        "@context": "https://schema.org",
        "@type": "Book",
        "name": title,
        "author": {"@type": "Person", "name": author},
        "url": url,
        "bookFormat": "https://schema.org/Paperback",
        "offers": {
            "@type": "Offer",
            "price": price,
            "priceCurrency": currency,
            "availability": "https://schema.org/InStock",
            "itemCondition": f"https://schema.org/{condition}",
            "url": url,
            "eligibleRegion": region,
        },
    }
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(entry)
        + "</script></head><body>synthetic fixture listing</body></html>"
    )


def _stage(store, sweep_id, kind, payload):
    name = f"{kind}_{sweep_id}_{uuid.uuid4().hex}.json"
    return store.persist_stage(sweep_id, name, json.dumps(payload).encode())


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


def seed_sweep(tmp_path, book_count=0, country="US", currency="USD"):
    store = SweepStore(tmp_path / "runs")
    state = store.start(country, currency, "packet-test")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    if book_count:
        store.commit_tracks(
            state.id,
            frame.id,
            {
                "frame_id": frame.id,
                "alignment": "none",
                "assignments": [],
                "applied_props": [],
                "new_books": [
                    _book_payload(frame.id, x=40 + 80 * i)
                    for i in range(book_count)
                ],
                "new_items": [],
                "flag_tracks": [],
                "review_items": [],
                "unresolved": 0,
            },
        )
    return store, state


def _persist_crop_views(store, sweep_id, book_id):
    refs = []
    for view in ("front", "angle"):
        refs.append(
            store.persist_crop(
                sweep_id,
                f"crop_{book_id}_{view}_{uuid.uuid4().hex}.jpg",
                jpeg(scene_image(11)),
            )
        )
    return refs


def identify_book(store, sweep_id, book_id, title=TITLE, author=AUTHOR,
                  isbn="", publisher="", edition="", author_legible=True,
                  catalogue=None):
    track = next(b for b in store.get(sweep_id).books if b.id == book_id)
    ev = track.evidence[0]
    crop_refs = _persist_crop_views(store, sweep_id, book_id)
    store.register_crops(sweep_id, {book_id: crop_refs})
    reading = {
        "ref_id": book_id,
        "title": title,
        "author": author,
        "publisher": publisher,
        "edition": edition,
        "isbn": isbn,
        "title_legible": True,
        "author_legible": author_legible,
        "confidence": 0.95,
        "visible_text": [title],
    }
    ref = _stage(
        store,
        sweep_id,
        "reading",
        {
            "status": "completed",
            "ref_ids": [book_id],
            "readings": [reading],
            "elapsed_ms": 12.0,
            "sources": {
                book_id: {
                    "frame_id": ev.frame_id,
                    "bbox": {
                        "x": ev.bbox.x,
                        "y": ev.bbox.y,
                        "w": ev.bbox.w,
                        "h": ev.bbox.h,
                    },
                    "crop_refs": crop_refs,
                }
            },
        },
    )
    store.apply_readings(sweep_id, {book_id: ref}, [reading], catalogue=catalogue)
    return ref


def _identity_snapshot(track):
    return {
        "title": track.title,
        "author": track.author,
        "edition": track.edition,
        "isbn": track.isbn,
        "status": track.status,
        "assertions": [a.model_dump(mode="json") for a in track.assertions],
        "accepted": [
            (r.title, r.author)
            for r in track.identification_evidence
            if r.status == "accepted"
        ],
    }


def _price_for(
    store, sweep_id, track, amount, condition, url, price, currency, region, market
):
    snapshot = {
        "status": "completed",
        "requested_url": url,
        "url": url,
        "retrieved_at": NOW,
        "matched": True,
        "html": listing(
            url,
            price,
            currency,
            "NewCondition" if condition == "new" else "UsedCondition",
            region,
            title=track.title,
            author=track.author,
        ),
    }
    source_ref = _stage(store, sweep_id, "pricing_source", snapshot)
    discovery_ref = _stage(
        store,
        sweep_id,
        "pricing_discovery",
        {
            "status": "completed",
            "market": market,
            "ref_ids": [track.id],
            "raw_response": {
                "output": [
                    {
                        "type": "web_search_call",
                        "action": {
                            "type": "search",
                            "sources": [{"url": url}],
                        },
                    }
                ]
            },
        },
    )
    quote = SourceQuote(
        amount=Decimal(str(price)),
        currency=currency,
        condition=condition,
        market_country=market,
        url=url,
        retrieved_at=NOW,
        matched_title=" ".join(track.title.casefold().split()),
        matched_author=" ".join(track.author.casefold().split()),
        listing_title=track.title,
        listing_author=track.author,
        listing_format="Paperback",
        listing_availability="InStock",
        market_evidence="eligible_region",
        source_snapshot_ref=source_ref,
        discovery_stage_ref=discovery_ref,
    )
    converted = currency != store.get(sweep_id).currency
    foreign = market != store.get(sweep_id).country
    if converted:
        rates = {"USD": Decimal(1), "GBP": Decimal("0.75"), "AED": Decimal("3.67")}
        fx = FxEvidence(
            kind="provider_feed",
            source_url="https://open.er-api.com/v6/latest/USD",
            data_at="2026-10-08T11:00:00+00:00",
            retrieved_at=NOW,
            source_currency=currency,
            target_currency=store.get(sweep_id).currency,
            source_rate=rates[currency],
            target_rate=rates[store.get(sweep_id).currency],
            cross_rate=rates[store.get(sweep_id).currency] / rates[currency],
            attribution="Rates By Exchange Rate API",
            attribution_url="https://www.exchangerate-api.com",
        )
    else:
        fx = FxEvidence(
            kind="currency_identity",
            retrieved_at=NOW,
            source_currency=currency,
            target_currency=store.get(sweep_id).currency,
            source_rate=Decimal(1),
            target_rate=Decimal(1),
            cross_rate=Decimal(1),
        )
    return BookPrice(
        amount=Decimal(str(amount)),
        currency=store.get(sweep_id).currency,
        source="listing",
        url=url,
        retrieved_at=NOW,
        converted=converted or foreign,
        condition_assumed="good" if condition == "used" else None,
        foreign_market_fallback=foreign,
        foreign_market_same_currency=foreign and not converted,
        edition_equivalence="current_print_equivalent",
        source_quote=quote,
        fx_evidence=fx,
    )


def price_book(store, sweep_id, book_id, new_amount, used_amount,
               source_price=None, source_currency="USD", market="US"):
    track = next(b for b in store.get(sweep_id).books if b.id == book_id)
    replacement = used = None
    fx_provenance = None
    quotes = {}
    if new_amount is not None:
        replacement = _price_for(
            store, sweep_id, track, new_amount, "new",
            f"https://books.example/{book_id}/new",
            source_price if source_price is not None else new_amount,
            source_currency, market, market,
        )
        quotes["replacement"] = replacement.source_quote.model_dump(mode="json")
    if used_amount is not None:
        used = _price_for(
            store, sweep_id, track, used_amount, "used",
            f"https://books.example/{book_id}/used",
            source_price if source_price is not None else used_amount,
            source_currency, market, market,
        )
        quotes["used"] = used.source_quote.model_dump(mode="json")
    state = store.get(sweep_id)
    converted_fx = next(
        (
            p.fx_evidence
            for p in (replacement, used)
            if p is not None
            and p.fx_evidence is not None
            and p.fx_evidence.kind == "provider_feed"
        ),
        None,
    )
    if converted_fx is not None:
        fx_provenance = {
            "source_url": converted_fx.source_url,
            "data_at": converted_fx.data_at,
            "retrieved_at": converted_fx.retrieved_at,
            "attribution": converted_fx.attribution,
            "attribution_url": converted_fx.attribution_url,
            "rates_used": {
                "USD": "1",
                "GBP": "0.75",
                "AED": "3.67",
                state.currency: str(converted_fx.target_rate),
            },
        }
    val_ref = _stage(
        store,
        sweep_id,
        "pricing_valuation",
        {
            "status": "completed",
            "fx": fx_provenance or {"error": "no conversion required"},
            "valuations": {
                book_id: {
                    "quotes": quotes,
                    "needs_appraisal": False,
                    "reviews": [],
                }
            },
        },
    )
    return store.commit_prices(
        sweep_id,
        {book_id: _identity_snapshot(track)},
        {
            book_id: {
                "needs_appraisal": False,
                "replacement": replacement,
                "used": used,
                "reviews": [],
            }
        },
        val_ref,
    )


def test_packet_totals_and_canonical_shape(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=2)
    ids = [b.id for b in store.get(state.id).books]
    for bid in ids:
        identify_book(store, state.id, bid)
    price_book(store, state.id, ids[0], "10.10", "4.00")
    price_book(store, state.id, ids[1], "20.20", "5.00")
    store.end(state.id, 12.5)
    packet = build_packet(store.get(state.id), store)
    assert packet["status"] == "partial"
    sweep = packet["sweep"]
    assert sweep["id"] == state.id
    assert sweep["country"] == "US" and sweep["currency"] == "USD"
    assert sweep["duration_s"] == 12.5
    assert sweep["revision"] == store.get(state.id).revision
    room = packet["room"]
    for key in (
        "length_m", "width_m", "height_m", "floor_area_m2", "wall_area_m2",
        "shelved_wall_area_m2", "floor_area_ft2", "wall_area_ft2",
    ):
        assert room[key] is None
    assert room["shape"] == "unknown"
    totals = packet["totals"]
    assert totals["book_count"] == 2
    assert totals["books_identified"] == 2
    assert totals["books_unidentified"] == 0
    assert totals["books_needs_appraisal"] == 0
    assert totals["currency"] == "USD"
    assert totals["books_replacement_cost"] == "30.30"
    assert totals["books_used_value"] == "9.00"
    assert totals["items_replacement_cost_low"] is None
    assert totals["items_replacement_cost_high"] is None
    assert totals["shelf_run_m"] is None
    assert totals["excluded_from_totals"] == 0
    book = packet["books"][0]
    assert book["replacement_cost"]["amount"] == "10.10"
    assert book["replacement_cost"]["source_quote"]["matched_title"]
    assert "track_histogram" not in book
    assert packet["summary"] == (
        "This is a partial packet with 2 provisional book tracks: "
        "2 identified, 0 unidentified, and 0 awaiting appraisal. "
        "The physical room count is not verified. "
        "The supported book replacement subtotal is USD 30.30 across 2 records. "
        "The supported used-book subtotal is USD 9.00 across 2 records. "
        "Room measurements remain unavailable. "
        "Review the exclusions and unresolved evidence."
    )
    assert any(
        "NOT MEASURED" in r["reason"] for r in packet["review_queue"]
    )


def test_unlinked_shelf_section_has_no_whole_sweep_book_count(tmp_path):
    store, sweep = seed_sweep(tmp_path, book_count=1)
    first = store.get(sweep.id).frames[0]
    other = store.add_frame(sweep.id, jpeg(scene_image(seed=17)), 2.0)
    store.commit_tracks(sweep.id, other.id, {
        "frame_id": other.id, "alignment": "failed", "assignments": [],
        "applied_props": [], "new_books": [_book_payload(other.id, x=220)],
        "new_items": [], "flag_tracks": [], "review_items": [], "unresolved": 1,
    })
    selected_id = store.get(sweep.id).books[0].id
    store.end(sweep.id, 2.0)
    store.reconcile_book_tracks(sweep.id, [selected_id], first.id, [first.id], [other.id])
    packet = build_packet(store.get(sweep.id), store)
    assert packet["totals"]["book_count"] == 1
    assert packet["physical_book_count"] is None
    assert packet["book_count_scope"] == "corroborated_scene_subset"
    assert "one corroborated section" in packet["summary"]
    assert len(packet["unresolved_book_observations"]) == 1
    report = render_report_html(packet)
    assert "Whole-sweep physical book count: NOT MEASURED" in report
    assert report.index("Whole-sweep physical book count") < report.index("<h2>Books</h2>")


def test_unresolved_book_observations_never_verify_physical_count(tmp_path):
    store, sweep = seed_sweep(tmp_path, book_count=2)
    frame = store.get(sweep.id).frames[0]
    selected_id = store.get(sweep.id).books[0].id
    store.end(sweep.id, 1.0)
    store.reconcile_book_tracks(sweep.id, [selected_id], frame.id, [frame.id], [])
    state = store.get(sweep.id)
    state.inventory_complete = True
    packet = build_packet(state, store)
    assert packet["physical_book_count"] is None


def test_two_identical_copies_each_counted(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=2)
    ids = [b.id for b in store.get(state.id).books]
    for bid in ids:
        identify_book(store, state.id, bid)
    for bid in ids:
        price_book(store, state.id, bid, "10.00", None)
    packet = build_packet(store.get(state.id), store)
    assert packet["totals"]["books_replacement_cost"] == "20.00"


def test_no_accepted_prices_bucket_is_null_not_zero(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    packet = build_packet(store.get(state.id), store)
    assert packet["totals"]["books_replacement_cost"] is None
    assert packet["totals"]["books_used_value"] is None
    assert packet["totals"]["books_unidentified"] == 1
    assert packet["status"] == "partial"


def test_excluded_and_appraisal_never_counted(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=3)
    ids = [b.id for b in store.get(state.id).books]
    for bid in ids:
        identify_book(store, state.id, bid)
    price_book(store, state.id, ids[0], "10.00", "4.00")
    price_book(store, state.id, ids[1], "100.00", "20.00")
    price_book(store, state.id, ids[2], "50.00", "9.00")
    store.add_turn(state.id, "t1", "exclude", "typed_user")
    store.correct(
        state.id,
        CorrectionIntent(
            type="exclude_item", target_id=ids[1], source_turn_id="t1"
        ),
    )
    store.correct(
        state.id,
        CorrectionIntent(
            type="assert_edition",
            target_id=ids[2],
            value="rare",
            source_turn_id="t1",
        ),
    )
    packet = build_packet(store.get(state.id), store)
    totals = packet["totals"]
    assert totals["books_replacement_cost"] == "10.00"
    assert totals["book_count"] == 3
    assert totals["books_needs_appraisal"] == 1
    assert totals["excluded_from_totals"] == 2
    excluded_ids = {
        e["id"] for e in packet["coverage"]["books_replacement"]["excluded"]
    }
    assert ids[1] in excluded_ids and ids[2] in excluded_ids


def test_publish_writes_packet_and_report_atomically(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", "4.00")
    store.end(state.id, 3.0)
    result = publish_packet(store, state.id)
    assert result["status"] == "partial"
    assert result["revision"] == store.get(state.id).revision
    run_dir = tmp_path / "runs" / state.id
    packet_path = run_dir / result["packet_ref"]
    report_path = run_dir / result["report_ref"]
    assert packet_path.is_file() and report_path.is_file()
    packet = json.loads(packet_path.read_text())
    assert packet["sweep"]["revision"] == result["revision"]
    report = report_path.read_text()
    assert "PARTIAL" in report
    assert "Clean Code" in report
    assert "10.00" in report


def test_publish_rejects_capturing_sweep(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=0)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409
    assert not (tmp_path / "runs" / state.id / "claim_packet.json").exists()


def _tamper(store, sweep_id, fn):
    state = store._states[sweep_id]
    fn(state)
    store._save(state)


def test_wrong_price_currency_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].replacement_cost.currency = "AED"

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_mutated_source_price_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].replacement_cost.amount = Decimal("99.00")

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_inconsistent_fx_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1, currency="GBP")
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "7.50", None,
               source_price="10", source_currency="USD", market="US")
    store.end(state.id, 3.0)

    def mutate(s):
        fx = s.books[0].replacement_cost.fx_evidence
        s.books[0].replacement_cost.fx_evidence = fx.model_copy(
            update={"target_rate": Decimal("0.70"),
                    "cross_rate": Decimal("0.70")}
        )

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_appraisal_book_with_price_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].status = "needs_appraisal"

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_guessed_unidentified_title_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].title = "The Great Gatsby"

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_duplicate_book_ids_block_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=2)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[1].id = s.books[0].id

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_broken_frame_ref_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].frame_ref = "frames/frame_" + "0" * 32 + ".jpg"

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_traversal_stage_ref_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)

    def mutate(s):
        q = s.books[0].replacement_cost.source_quote
        s.books[0].replacement_cost.source_quote = q.model_copy(
            update={"source_snapshot_ref": "stage_outputs/../../state.json"}
        )

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_missing_stage_file_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)
    ref = store.get(state.id).books[0].replacement_cost.source_quote.source_snapshot_ref
    (tmp_path / "runs" / state.id / ref).unlink()
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_stage_ref_symlink_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)
    ref = store.get(state.id).books[0].replacement_cost.source_quote.source_snapshot_ref
    target = tmp_path / "runs" / state.id / ref
    target.unlink()
    target.symlink_to(tmp_path / "runs" / state.id / "state.json")
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_nonnull_measurement_without_provenance_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].spine_thickness_cm = 2.5

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


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


def api_seed(client):
    resp = post(
        client,
        "/api/sweeps",
        json={"country": "US", "currency": "USD", "device": "packet-test"},
    )
    return resp.json()["id"]


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


def test_packet_endpoint_guards(client):
    sweep_id = api_seed(client)
    assert (
        post(client, f"/api/sweeps/{sweep_id}/packet", json={},
             origin="http://evil.example").status_code
        == 403
    )
    assert (
        post(client, f"/api/sweeps/{sweep_id}/packet", json={"x": 1}).status_code
        == 422
    )
    assert (
        post(client, "/api/sweeps/" + "0" * 32 + "/packet", json={}).status_code
        == 404
    )
    assert (
        post(client, f"/api/sweeps/{sweep_id}/packet", json={}).status_code
        == 409
    )


def test_end_generates_packet_and_get_serves_artifacts(client):
    sweep_id = api_seed(client)
    client.app.state.postwork.vision = _NoopVision()
    post(
        client,
        f"/api/sweeps/{sweep_id}/frames?ts=1.0",
        content=jpeg(scene_image()),
        headers={"content-type": "image/jpeg"},
    )
    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 2.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "partial"
    assert body["postwork_status"] == "not_started"
    assert body["packet"] is None
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "completed"
    packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
    assert packet.status_code == 200
    assert packet.headers["content-type"].startswith("application/json")
    payload = packet.json()
    assert payload["status"] == "partial"
    assert payload["summary"].startswith("This is a partial packet")
    assert payload["sweep"]["id"] == sweep_id
    assert packet.headers["x-packet-revision"] == str(settled["revision"])
    assert packet.headers["x-packet-stale"] == "false"
    report = client.get(f"/api/sweeps/{sweep_id}/report.html")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("text/html")
    assert "partial" in report.text.lower()
    other = api_seed(client)
    assert (
        client.get(f"/api/sweeps/{other}/claim_packet.json").status_code == 404
    )


def test_correction_after_publish_marks_packet_stale(client):
    sweep_id = api_seed(client)
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    wait_postwork(client, sweep_id)
    post(
        client,
        f"/api/sweeps/{sweep_id}/turns",
        json={"turn_id": "t1", "text": "note", "source": "typed_user"},
    )
    packet = client.get(f"/api/sweeps/{sweep_id}/claim_packet.json")
    assert packet.headers["x-packet-stale"] == "true"
    refreshed = post(client, f"/api/sweeps/{sweep_id}/packet", json={})
    assert refreshed.status_code == 200
    assert refreshed.json()["stale"] is False


def test_report_escapes_provider_strings(client):
    sweep_id = api_seed(client)
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
            "new_books": [_book_payload(frame_id)],
            "new_items": [
                {
                    **_item_payload(frame_id),
                    "description": 'shelf <script>alert("x")</script>',
                }
            ],
            "flag_tracks": [],
            "review_items": [
                {"ref_id": "room", "reason": "<img src=x onerror=alert(1)>"}
            ],
            "unresolved": 0,
        },
    )
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    wait_postwork(client, sweep_id)
    report = client.get(f"/api/sweeps/{sweep_id}/report.html")
    assert report.status_code == 200
    assert "<script>alert" not in report.text
    assert "&lt;script&gt;" in report.text
    assert "&lt;img" in report.text


def test_item_price_range_blocks_publish(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=0)
    frame_id = store.get(state.id).frames[0].id
    store.commit_tracks(
        state.id,
        frame_id,
        {
            "frame_id": frame_id,
            "alignment": "none",
            "assignments": [],
            "applied_props": [],
            "new_books": [],
            "new_items": [_item_payload(frame_id)],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 0,
        },
    )

    def mutate(s):
        from backend.models import ItemPriceRange

        s.items[0].replacement_cost = ItemPriceRange(low=10.0, high=20.0)

    _tamper(store, state.id, mutate)
    store.end(state.id, 1.0)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_excluded_item_with_price_range_still_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=0)
    frame_id = store.get(state.id).frames[0].id
    store.commit_tracks(
        state.id,
        frame_id,
        {
            "frame_id": frame_id,
            "alignment": "none",
            "assignments": [],
            "applied_props": [],
            "new_books": [],
            "new_items": [
                {**_item_payload(frame_id), "excluded": True,
                 "status": "needs_appraisal",
                 "valuation_state": "appraisal_pending"}
            ],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 0,
        },
    )

    def mutate(s):
        from backend.models import ItemPriceRange

        s.items[0].replacement_cost = ItemPriceRange(low=1.0, high=2.0)

    _tamper(store, state.id, mutate)
    store.end(state.id, 1.0)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_unpriced_item_dimensions_are_null_object(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=0)
    frame_id = store.get(state.id).frames[0].id
    store.commit_tracks(
        state.id,
        frame_id,
        {
            "frame_id": frame_id,
            "alignment": "none",
            "assignments": [],
            "applied_props": [],
            "new_books": [],
            "new_items": [_item_payload(frame_id)],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 0,
        },
    )
    packet = build_packet(store.get(state.id), store)
    assert packet["items"][0]["dimensions_cm"] == {
        "w": None, "h": None, "d": None
    }
    assert packet["totals"]["items_replacement_cost_low"] is None


def test_used_condition_on_replacement_slot_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    track = store.get(state.id).books[0]
    used_quote_price = _price_for(
        store, state.id, track, "10.00", "used",
        "https://books.example/" + bid + "/used",
        "10.00", "USD", "US", "US",
    )
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].replacement_cost = used_quote_price

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_mismatched_price_retrieved_at_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].replacement_cost.retrieved_at = "2026-10-08T13:00:00+00:00"

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_discovery_without_cited_url_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)
    ref = store.get(state.id).books[0].replacement_cost.source_quote.discovery_stage_ref
    stage_path = tmp_path / "runs" / state.id / ref
    payload = json.loads(stage_path.read_text())
    payload["raw_response"] = {"output": []}
    stage_path.write_text(json.dumps(payload))
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_retained_quote_without_snapshot_refs_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", "4.00")
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].used_value.amount = None
        q = s.books[0].used_value.source_quote
        s.books[0].used_value.source_quote = q.model_copy(
            update={"source_snapshot_ref": None}
        )

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_fx_and_amount_mutated_together_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1, currency="GBP")
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "7.50", None,
               source_price="10", source_currency="USD", market="US")
    store.end(state.id, 3.0)

    def mutate(s):
        price = s.books[0].replacement_cost
        fx = price.fx_evidence
        price.fx_evidence = fx.model_copy(
            update={
                "target_rate": Decimal("0.80"),
                "cross_rate": Decimal("0.80"),
            }
        )
        price.amount = Decimal("8.00")

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_stale_fx_data_at_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1, currency="GBP")
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "7.50", None,
               source_price="10", source_currency="USD", market="US")
    store.end(state.id, 3.0)

    def mutate(s):
        price = s.books[0].replacement_cost
        price.fx_evidence = price.fx_evidence.model_copy(
            update={"data_at": "2026-10-05T11:00:00+00:00"}
        )

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_visible_isbn_matching_listing_passes(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid, isbn="9780132350884")
    price_book(store, state.id, bid, "10.00", None)
    packet = build_packet(store.get(state.id), store)
    assert packet["books"][0]["isbn"] == "9780132350884"


def test_isbn_without_reading_provenance_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].isbn = "9780132350884"

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_identified_empty_title_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].title = ""

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_dummy_reading_stage_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    ref = store.get(state.id).books[0].identification_evidence[0].stage_ref
    stage_path = tmp_path / "runs" / state.id / ref
    stage_path.write_text(json.dumps({"status": "completed"}))
    store.end(state.id, 3.0)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_unrelated_reading_stage_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    ref = store.get(state.id).books[0].identification_evidence[0].stage_ref
    stage_path = tmp_path / "runs" / state.id / ref
    payload = json.loads(stage_path.read_text())
    payload["readings"][0]["title"] = "Different Title Entirely"
    stage_path.write_text(json.dumps(payload))
    store.end(state.id, 3.0)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_missing_reading_stage_ref_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].identification_evidence[0].stage_ref = None

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409


def test_report_has_resolvable_frame_crop_and_source_links(client):
    sweep_id = api_seed(client)
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
            "new_books": [_book_payload(frame_id)],
            "new_items": [],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 0,
        },
    )
    bid = store.get(sweep_id).books[0].id
    identify_book(store, sweep_id, bid)
    price_book(store, sweep_id, bid, "10.00", "4.00")
    client.app.state.postwork.vision = _NoopVision()
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    wait_postwork(client, sweep_id)
    report = client.get(f"/api/sweeps/{sweep_id}/report.html")
    assert report.status_code == 200
    assert 'href="frames/' in report.text
    assert 'href="crops/' in report.text
    assert 'href="https://books.example/' in report.text
    frame_link = report.text.split('href="frames/')[1].split('"')[0]
    linked = client.get(f"/api/sweeps/{sweep_id}/frames/{frame_link}")
    assert linked.status_code == 200
    crop_link = report.text.split('href="crops/')[1].split('"')[0]
    linked_crop = client.get(f"/api/sweeps/{sweep_id}/crops/{crop_link}")
    assert linked_crop.status_code == 200


def test_planted_tmp_symlink_not_followed(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)
    store.end(state.id, 3.0)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    run_dir = tmp_path / "runs" / state.id
    (run_dir / "claim_packet.json.tmp").symlink_to(outside)
    (run_dir / "report.html.tmp").symlink_to(outside)
    publish_packet(store, state.id)
    assert outside.read_text() == "keep me"
    assert (run_dir / "claim_packet.json").is_file()


def test_symlink_report_target_blocks_before_json_write(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=0)
    store.end(state.id, 1.0)
    run_dir = tmp_path / "runs" / state.id
    outside = tmp_path / "outside-report.html"
    outside.write_text("x")
    (run_dir / "report.html").symlink_to(outside)
    with pytest.raises(StoreError):
        publish_packet(store, state.id)
    assert outside.read_text() == "x"
    assert not (run_dir / "claim_packet.json").exists()


def test_get_report_requires_valid_packet_json(client):
    sweep_id = api_seed(client)
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    wait_postwork(client, sweep_id)
    store = client.app.state.store
    run_dir = Path(client.app.state.store.root) / sweep_id
    assert client.get(f"/api/sweeps/{sweep_id}/report.html").status_code == 200
    (run_dir / "claim_packet.json").unlink()
    assert client.get(f"/api/sweeps/{sweep_id}/report.html").status_code == 404


def test_get_artifact_rejects_symlinked_packet_json(client):
    sweep_id = api_seed(client)
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    wait_postwork(client, sweep_id)
    run_dir = Path(client.app.state.store.root) / sweep_id
    victim = run_dir / "state.json"
    (run_dir / "claim_packet.json").unlink()
    (run_dir / "claim_packet.json").symlink_to(victim)
    assert client.get(f"/api/sweeps/{sweep_id}/claim_packet.json").status_code == 404
    assert client.get(f"/api/sweeps/{sweep_id}/report.html").status_code == 404


def test_unpublished_artifact_name_is_404(client):
    sweep_id = api_seed(client)
    post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    wait_postwork(client, sweep_id)
    run_dir = Path(client.app.state.store.root) / sweep_id
    (run_dir / "report.html").write_text("<html>arbitrary</html>")
    (run_dir / "claim_packet.json").unlink()
    assert client.get(f"/api/sweeps/{sweep_id}/report.html").status_code == 404


def test_end_reports_packet_error_when_invalid(client):
    sweep_id = api_seed(client)
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
            "new_books": [_book_payload(frame_id)],
            "new_items": [],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 0,
        },
    )

    def mutate(s):
        s.books[0].spine_thickness_cm = 2.5

    _tamper(store, sweep_id, mutate)
    resp = post(client, f"/api/sweeps/{sweep_id}/end", json={"duration_s": 1.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "partial"
    assert body["packet"] is None
    settled = wait_postwork(client, sweep_id)
    assert settled["postwork_status"] == "failed"
    reasons = [r["reason"] for r in settled["review_queue"]]
    assert any("packet publication failed" in r for r in reasons)
    assert not (run_dir_exists(Path(store.root) / sweep_id / "claim_packet.json"))


def run_dir_exists(path):
    return path.exists()


def test_metrics_truthful_names_and_latency_sums(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", None)

    def mutate(s):
        from backend.models import PricingStageRecord

        s.price_stages = [
            PricingStageRecord(
                kind="source", ref_ids=[bid], status="completed",
                persisted_ref="stage_outputs/x.json", elapsed_ms=20.0,
                cost_usd_estimate="0.01",
            ),
            PricingStageRecord(
                kind="source", ref_ids=[bid], status="completed",
                persisted_ref="stage_outputs/x.json", elapsed_ms=20.0,
                cost_usd_estimate="0.01",
            ),
            PricingStageRecord(
                kind="discovery", ref_ids=[bid], status="completed",
                persisted_ref="stage_outputs/y.json",
                cost_usd_estimate="NaN",
            ),
        ]

    _tamper(store, state.id, mutate)
    packet = build_packet(store.get(state.id), store)
    metrics = packet["metrics"]
    assert metrics["book_identification_ms"] is None
    assert metrics["pricing_ms"] is None
    assert metrics["measurement_ms"] is None
    assert metrics["report_generation_ms"] is None
    assert metrics["post_sweep_total_ms"] is None
    assert metrics["recorded_pricing_call_ms_sum"] == 40.0
    assert metrics["known_model_cost_usd_estimate"] == "0.01"
    assert metrics["model_cost_unknown_count"] == 1
    assert metrics["packet_validation_ms"] is not None
    assert metrics["voice_cost_usd"] is None
    assert metrics["total_cost_usd"] is None


def test_publish_metadata_reports_real_timing(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=0)
    store.end(state.id, 1.0)
    result = publish_packet(store, state.id)
    assert result["rendering_elapsed_ms"] is not None
    assert result["rendering_elapsed_ms"] >= 0
    assert result["packet_ready_elapsed_ms"] is not None
    assert result["packet_ready_elapsed_ms"] >= 0


def test_publish_then_correction_snapshot_stays_consistent(tmp_path):
    import threading

    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", "4.00")
    store.end(state.id, 3.0)
    store.add_turn(state.id, "t1", "note", "typed_user")
    rev_before = store.get(state.id).revision
    outcome = {}

    def do_publish():
        outcome["result"] = publish_packet(store, state.id)

    with store._lock:
        thread = threading.Thread(target=do_publish)
        thread.start()
    thread.join()
    result = outcome["result"]
    assert result["revision"] == rev_before
    run_dir = tmp_path / "runs" / state.id
    packet = json.loads((run_dir / "claim_packet.json").read_text())
    assert packet["sweep"]["revision"] == rev_before
    assert packet["totals"]["books_replacement_cost"] == "10.00"
    report = (run_dir / "report.html").read_text()
    assert f"revision: {rev_before}" in report
    store.correct(
        state.id,
        CorrectionIntent(
            type="exclude_item", target_id=bid, source_turn_id="t1"
        ),
    )
    content, packet_revision, stale = store.read_packet_artifact(
        state.id, "claim_packet.json"
    )
    assert packet_revision == rev_before
    assert stale is True
    assert json.loads(content)["sweep"]["revision"] == rev_before


def test_correction_before_publish_is_reflected(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1)
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "10.00", "4.00")
    store.end(state.id, 3.0)
    store.add_turn(state.id, "t1", "note", "typed_user")
    store.correct(
        state.id,
        CorrectionIntent(
            type="exclude_item", target_id=bid, source_turn_id="t1"
        ),
    )
    rev_after = store.get(state.id).revision
    result = publish_packet(store, state.id)
    assert result["revision"] == rev_after
    run_dir = tmp_path / "runs" / state.id
    packet = json.loads((run_dir / "claim_packet.json").read_text())
    assert packet["sweep"]["revision"] == rev_after
    assert packet["totals"]["books_replacement_cost"] is None
    report = (run_dir / "report.html").read_text()
    assert f"revision: {rev_after}" in report


def test_fx_currency_label_mismatch_blocks(tmp_path):
    store, state = seed_sweep(tmp_path, book_count=1, currency="GBP")
    bid = store.get(state.id).books[0].id
    identify_book(store, state.id, bid)
    price_book(store, state.id, bid, "7.50", None,
               source_price="10", source_currency="USD", market="US")
    store.end(state.id, 3.0)

    def mutate(s):
        s.books[0].replacement_cost.fx_evidence = (
            s.books[0].replacement_cost.fx_evidence.model_copy(
                update={"source_currency": "EUR"}
            )
        )

    _tamper(store, state.id, mutate)
    with pytest.raises(StoreError) as info:
        publish_packet(store, state.id)
    assert info.value.status_code == 409
