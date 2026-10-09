import html as _html
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

from preflight.prices import normalized

from .currency import convert_amount
from .locales import currency_minor_units
from .price_sources import extract_source_quote
from .pricing import cited_urls
from .sweeps import StoreError

FRAME_REF_RE = re.compile(r"^frames/(frame_[0-9a-f]{32})\.jpg$")
CROP_REF_RE = re.compile(r"^crops/(crop_[0-9a-z_]+_[0-9a-f]{32}\.jpg)$")

ROOM_FIELDS = (
    "length_m",
    "width_m",
    "height_m",
    "floor_area_m2",
    "wall_area_m2",
    "shelved_wall_area_m2",
    "floor_area_ft2",
    "wall_area_ft2",
)

ROOM_REVIEW = "required room measurements unsupported; NOT MEASURED"


def _fail(reason):
    raise StoreError(409, "claim packet validation failed: " + reason)


def _resolve_frame(store, sweep_id, ref):
    match = FRAME_REF_RE.match(ref or "")
    if match is None:
        _fail("unresolvable frame reference")
    try:
        store.frame_path(sweep_id, match.group(1))
    except StoreError:
        _fail("frame reference does not resolve")


def _resolve_crop(store, sweep_id, ref):
    match = CROP_REF_RE.match(ref or "")
    if match is None:
        _fail("unresolvable crop reference")
    try:
        store.crop_path(sweep_id, match.group(1))
    except StoreError:
        _fail("crop reference does not resolve")


def _resolve_stage(store, sweep_id, ref):
    try:
        return store.stage_path(sweep_id, ref)
    except StoreError:
        _fail("stage reference does not resolve")


def _load_stage(store, sweep_id, ref, label):
    path = _resolve_stage(store, sweep_id, ref)
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        _fail(f"{label} unreadable")
    if not isinstance(data, dict):
        _fail(f"{label} malformed")
    return data


def _aware(ts):
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _check_price(store, state, book, slot, price):
    if price is None:
        return
    quote = price.source_quote
    if quote is not None:
        expected_condition = "new" if slot == "replacement" else "used"
        if quote.condition != expected_condition:
            _fail(f"book {book.id} {slot} quote condition is not {expected_condition}")
        if quote.url != price.url or quote.retrieved_at != price.retrieved_at:
            _fail(f"book {book.id} {slot} price does not match its source quote")
        if quote.source_snapshot_ref is None or quote.discovery_stage_ref is None:
            _fail(f"book {book.id} {slot} quote lacks source provenance")
        snapshot = _load_stage(
            store, state.id, quote.source_snapshot_ref,
            f"book {book.id} {slot} source snapshot",
        )
        if (
            snapshot.get("status") != "completed"
            or not isinstance(snapshot.get("html"), str)
            or not isinstance(snapshot.get("url"), str)
            or not isinstance(snapshot.get("retrieved_at"), str)
            or not isinstance(snapshot.get("requested_url"), str)
        ):
            _fail(f"book {book.id} {slot} source snapshot malformed")
        if (
            snapshot["url"] != quote.url
            or snapshot["retrieved_at"] != quote.retrieved_at
        ):
            _fail(f"book {book.id} {slot} source snapshot does not match the quote")
        discovery = _load_stage(
            store, state.id, quote.discovery_stage_ref,
            f"book {book.id} {slot} discovery stage",
        )
        if discovery.get("status") != "completed":
            _fail(f"book {book.id} {slot} discovery stage incomplete")
        cited = cited_urls(discovery.get("raw_response"))
        if snapshot["requested_url"] not in cited:
            _fail(f"book {book.id} {slot} source URL was never cited by discovery")
        reextracted = extract_source_quote(
            snapshot["html"],
            snapshot["url"],
            snapshot["retrieved_at"],
            book.title,
            book.author,
            quote.condition,
        )
        if reextracted is None or (
            reextracted["amount"] != quote.amount
            or reextracted["currency"] != quote.currency
            or reextracted["condition"] != quote.condition
            or reextracted["market_country"] != quote.market_country
            or reextracted["url"] != quote.url
            or reextracted["retrieved_at"] != quote.retrieved_at
        ):
            _fail(f"book {book.id} {slot} source quote does not match saved evidence")
    if price.amount is None:
        return
    if quote is None:
        _fail(f"book {book.id} {slot} price lacks source provenance")
    if price.valuation_stage_ref is None:
        _fail(f"book {book.id} {slot} price lacks valuation stage")
    valuation = _load_stage(
        store, state.id, price.valuation_stage_ref,
        f"book {book.id} {slot} valuation stage",
    )
    if price.currency != state.currency:
        _fail(f"book {book.id} {slot} price currency is not the sweep currency")
    source_currency = quote.currency
    converted = source_currency != state.currency
    foreign = quote.market_country != state.country
    if price.converted != (converted or foreign):
        _fail(f"book {book.id} {slot} converted flag inconsistent")
    if price.foreign_market_fallback != foreign:
        _fail(f"book {book.id} {slot} fallback flag inconsistent")
    if price.foreign_market_same_currency != (foreign and not converted):
        _fail(f"book {book.id} {slot} same-currency flag inconsistent")
    try:
        if converted:
            fx = price.fx_evidence
            if fx is None or fx.kind != "provider_feed":
                _fail(f"book {book.id} {slot} converted price lacks provider FX evidence")
            if (
                fx.source_currency != quote.currency
                or fx.target_currency != state.currency
            ):
                _fail(
                    f"book {book.id} {slot} FX currencies do not match "
                    "the quote and sweep currency"
                )
            provenance = valuation.get("fx")
            rates_used = provenance.get("rates_used") if isinstance(provenance, dict) else None
            if not isinstance(rates_used, dict):
                _fail(f"book {book.id} {slot} valuation stage lacks FX provenance")
            for field in ("source_url", "data_at", "retrieved_at", "attribution", "attribution_url"):
                if getattr(fx, field) != provenance.get(field):
                    _fail(f"book {book.id} {slot} FX evidence differs from valuation stage")
            if (
                str(fx.source_rate) != rates_used.get(source_currency)
                or str(fx.target_rate) != rates_used.get(state.currency)
            ):
                _fail(f"book {book.id} {slot} FX rates differ from valuation stage")
            if fx.cross_rate != fx.target_rate / fx.source_rate:
                _fail(f"book {book.id} {slot} FX cross rate inconsistent")
            data_at = _aware(fx.data_at)
            retrieved_at = _aware(fx.retrieved_at)
            if data_at is None or retrieved_at is None:
                _fail(f"book {book.id} {slot} FX timestamps are not aware")
            age = (retrieved_at - data_at).total_seconds()
            if age < -300 or age > 48 * 3600:
                _fail(f"book {book.id} {slot} FX data is outside the freshness window")
            expected = convert_amount(
                quote.amount,
                source_currency,
                state.currency,
                {source_currency: fx.source_rate, state.currency: fx.target_rate},
                minor_units=currency_minor_units(state.currency),
            )
        else:
            fx = price.fx_evidence
            if fx is not None and (
                fx.kind != "currency_identity"
                or fx.source_currency != fx.target_currency
                or fx.source_rate != 1
                or fx.target_rate != 1
                or fx.cross_rate != 1
            ):
                _fail(f"book {book.id} {slot} identity price has non-identity FX evidence")
            expected = convert_amount(
                quote.amount,
                source_currency,
                state.currency,
                {},
                minor_units=currency_minor_units(state.currency),
            )
    except StoreError:
        raise
    except Exception:
        _fail(f"book {book.id} {slot} price conversion cannot be recomputed")
    if expected != price.amount:
        _fail(f"book {book.id} {slot} amount does not match source quote and FX evidence")


def _reading_stage_proves(store, state, book, reading):
    if reading.stage_ref is None:
        return False
    stage = _load_stage(
        store, state.id, reading.stage_ref,
        f"book {book.id} reading stage",
    )
    if stage.get("status") != "completed":
        _fail(f"book {book.id} reading stage incomplete")
    sources = stage.get("sources")
    source = sources.get(book.id) if isinstance(sources, dict) else None
    if not isinstance(source, dict):
        return False
    frame_id = source.get("frame_id")
    if not isinstance(frame_id, str):
        return False
    try:
        store.frame_path(state.id, frame_id)
    except StoreError:
        _fail(f"book {book.id} reading stage frame does not resolve")
    bbox = source.get("bbox")
    if not isinstance(bbox, dict):
        return False
    matching = [
        ev
        for ev in book.evidence
        if ev.frame_id == frame_id
        and ev.bbox.x == bbox.get("x")
        and ev.bbox.y == bbox.get("y")
        and ev.bbox.w == bbox.get("w")
        and ev.bbox.h == bbox.get("h")
    ]
    if not matching:
        return False
    crop_refs = source.get("crop_refs")
    if not isinstance(crop_refs, list) or not crop_refs:
        return False
    for ref in crop_refs:
        _resolve_crop(store, state.id, ref)
    saved = stage.get("readings")
    if not isinstance(saved, list):
        return False
    record = next(
        (r for r in saved if isinstance(r, dict) and r.get("ref_id") == book.id),
        None,
    )
    if record is None:
        return False
    return (
        record.get("title") == reading.title
        and record.get("author") == reading.author
        and record.get("publisher") == reading.publisher
        and record.get("edition") == reading.edition
        and record.get("isbn") == reading.isbn
        and record.get("title_legible") == reading.title_legible
        and record.get("author_legible") == reading.author_legible
        and record.get("confidence") == reading.confidence
        and record.get("visible_text") == reading.visible_text
    )


def _catalogue_ok(catalogue, title):
    return (
        catalogue is not None
        and normalized(catalogue.matched_title) == normalized(title)
        and bool(catalogue.matched_author)
        and bool(catalogue.url)
        and bool(catalogue.retrieved_at)
    )


def _check_book(store, state, book):
    if book.status == "needs_appraisal":
        for price in (book.replacement_cost, book.used_value):
            if price is not None and price.amount is not None:
                _fail(f"book {book.id} pending appraisal carries an amount")
    if book.status == "unidentified" and (
        book.title or book.author or book.edition or book.isbn
    ):
        _fail(f"book {book.id} is unidentified but carries asserted identity")
    if book.spine_height_cm is not None or book.spine_thickness_cm is not None:
        _fail(f"book {book.id} has measurements without calibration provenance")
    if book.status == "identified":
        if not book.title:
            _fail(f"book {book.id} is identified without a title")
        provenance = False
        for reading in book.identification_evidence:
            if (
                reading.status != "accepted"
                or reading.confidence < 0.90
                or not reading.title_legible
                or normalized(reading.title) != normalized(book.title)
                or normalized(reading.author) != normalized(book.author)
            ):
                continue
            if not _reading_stage_proves(store, state, book, reading):
                continue
            if not (
                (reading.author_legible and reading.author)
                or _catalogue_ok(reading.catalogue, book.title)
            ):
                continue
            if (
                (book.isbn and book.isbn != reading.isbn)
                or (book.edition and book.edition != reading.edition)
                or (book.publisher and book.publisher != reading.publisher)
            ):
                continue
            provenance = True
            break
        if not provenance:
            _fail(
                f"book {book.id} identified without an accepted reading "
                "backed by saved stage provenance"
            )
    _resolve_frame(store, state.id, book.frame_ref)
    for ev in book.evidence:
        _resolve_frame(store, state.id, ev.frame_ref)
    for ref in book.crop_refs:
        _resolve_crop(store, state.id, ref)
    _check_price(store, state, book, "replacement", book.replacement_cost)
    _check_price(store, state, book, "used", book.used_value)


def _check_item(store, state, item):
    if item.replacement_cost is not None:
        _fail(f"item {item.id} prices lack supported source provenance")
    if item.dimensions_cm is not None and any(
        v is not None
        for v in (
            item.dimensions_cm.w,
            item.dimensions_cm.h,
            item.dimensions_cm.d,
        )
    ):
        _fail(f"item {item.id} has measurements without calibration provenance")
    _resolve_frame(store, state.id, item.frame_ref)
    for ev in item.evidence:
        _resolve_frame(store, state.id, ev.frame_ref)


def _bucket(amounts):
    return {
        "accepted_count": len(amounts),
        "amount": (
            format(sum(amounts, Decimal(0)), "f") if amounts else None
        ),
    }


def _totals(state):
    books = state.books
    counts = {
        "identified": 0,
        "unidentified": 0,
        "needs_appraisal": 0,
    }
    for book in books:
        counts[book.status] = counts.get(book.status, 0) + 1
    replacement_amt, used_amt = [], []
    item_low, item_high = [], []
    excluded_ids = set()
    coverage = {}

    def book_bucket(name, price_of, missing_reason):
        accepted, eligible, excluded = [], 0, []
        for book in books:
            reason = None
            if book.excluded:
                reason = "owner excluded"
            elif book.potential_duplicate:
                reason = "potential duplicate"
            elif book.status == "needs_appraisal":
                reason = "needs appraisal"
            elif book.status == "unidentified":
                reason = "unidentified"
            if reason is not None:
                excluded.append({"id": book.id, "reason": reason})
                excluded_ids.add(book.id)
                continue
            eligible += 1
            price = price_of(book)
            if price is None or price.amount is None:
                excluded.append({"id": book.id, "reason": missing_reason})
                excluded_ids.add(book.id)
                continue
            accepted.append(price.amount)
        for book in state.unresolved_books:
            excluded.append({"id": book.id, "reason": "unresolved repeated-view observation"})
            excluded_ids.add(book.id)
        coverage[name] = {
            "accepted_count": len(accepted),
            "eligible_count": eligible,
            "excluded": excluded,
        }
        return accepted

    replacement_amt = book_bucket(
        "books_replacement",
        lambda b: b.replacement_cost,
        "missing replacement price",
    )
    used_amt = book_bucket(
        "books_used", lambda b: b.used_value, "missing used price"
    )

    def item_bucket(name, value_of, missing_reason):
        accepted, eligible, excluded = [], 0, []
        for item in state.items:
            reason = None
            if item.excluded:
                reason = "owner excluded"
            elif item.status == "needs_appraisal":
                reason = "needs appraisal"
            elif item.valuation_state == "appraisal_pending":
                reason = "needs appraisal"
            if reason is not None:
                excluded.append({"id": item.id, "reason": reason})
                excluded_ids.add(item.id)
                continue
            eligible += 1
            value = value_of(item)
            if value is None:
                excluded.append({"id": item.id, "reason": missing_reason})
                excluded_ids.add(item.id)
                continue
            accepted.append(Decimal(str(value)))
        coverage[name] = {
            "accepted_count": len(accepted),
            "eligible_count": eligible,
            "excluded": excluded,
        }
        return accepted

    item_low = item_bucket(
        "items_low",
        lambda i: i.replacement_cost.low if i.replacement_cost else None,
        "unsupported item range",
    )
    item_high = item_bucket(
        "items_high",
        lambda i: i.replacement_cost.high if i.replacement_cost else None,
        "unsupported item range",
    )
    return {
        "totals": {
            "book_count": len(books),
            "books_identified": counts["identified"],
            "books_unidentified": counts["unidentified"],
            "books_needs_appraisal": counts["needs_appraisal"],
            "shelf_run_m": None,
            "currency": state.currency,
            "books_replacement_cost": _bucket(replacement_amt)["amount"],
            "books_used_value": _bucket(used_amt)["amount"],
            "items_replacement_cost_low": _bucket(item_low)["amount"],
            "items_replacement_cost_high": _bucket(item_high)["amount"],
            "excluded_from_totals": len(excluded_ids),
        },
        "coverage": coverage,
    }


def _metrics(state, started):
    call_sums = {"vision": Decimal(0), "reading": Decimal(0), "pricing": Decimal(0)}
    call_seen = {"vision": False, "reading": False, "pricing": False}
    latency_missing = 0
    paid_seen = set()
    known_cost = Decimal(0)
    any_cost = False
    model_unknown = 0

    def collect(family, records, model_stage):
        nonlocal any_cost, model_unknown, latency_missing, known_cost
        for record in records:
            if record.elapsed_ms is not None:
                call_sums[family] += Decimal(str(record.elapsed_ms))
                call_seen[family] = True
            else:
                latency_missing += 1
            if getattr(record, "status", None) == "cached":
                continue
            key = record.persisted_ref or ("record", id(record))
            if key in paid_seen:
                continue
            paid_seen.add(key)
            raw = record.cost_usd_estimate
            cost = None
            if raw is not None:
                try:
                    cost = Decimal(str(raw))
                    if not cost.is_finite() or cost < 0:
                        cost = None
                except ArithmeticError:
                    cost = None
            if cost is None:
                if model_stage:
                    model_unknown += 1
            else:
                known_cost += cost
                any_cost = True

    collect("vision", state.vision_stages, True)
    collect("reading", state.reading_stages, True)
    discovery = [s for s in state.price_stages if s.kind == "discovery"]
    other_pricing = [s for s in state.price_stages if s.kind != "discovery"]
    collect("pricing", discovery, True)
    collect("pricing", other_pricing, False)

    timings = getattr(state, "processing_timings_ms", None) or {}

    def wall(key):
        value = timings.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value >= 0 and value != float("inf") and value == value:
                return float(value)
        return None

    return {
        "capture_duration_s": state.duration_s,
        "post_sweep_vision_ms": wall("post_sweep_vision_ms"),
        "book_identification_ms": wall("book_identification_ms"),
        "pricing_ms": wall("pricing_ms"),
        "post_sweep_work_ms": wall("post_sweep_work_ms"),
        "measurement_ms": None,
        "report_generation_ms": None,
        "post_sweep_total_ms": None,
        "packet_validation_ms": round((time.monotonic() - started) * 1000.0, 3),
        "recorded_vision_call_ms_sum": (
            float(call_sums["vision"]) if call_seen["vision"] else None
        ),
        "recorded_reading_call_ms_sum": (
            float(call_sums["reading"]) if call_seen["reading"] else None
        ),
        "recorded_pricing_call_ms_sum": (
            float(call_sums["pricing"]) if call_seen["pricing"] else None
        ),
        "recorded_latency_missing_count": latency_missing,
        "known_model_cost_usd_estimate": (
            format(known_cost, "f") if any_cost else None
        ),
        "model_cost_unknown_count": model_unknown,
        "voice_cost_usd": None,
        "total_cost_usd": None,
    }


def _summary(packet):
    totals = packet["totals"]
    coverage = packet["coverage"]
    replacement = totals["books_replacement_cost"]
    if replacement is None:
        replacement_sentence = "No supported book replacement subtotal is available."
    else:
        replacement_sentence = (
            "The supported book replacement subtotal is "
            f"{totals['currency']} {replacement} across "
            f"{coverage['books_replacement']['accepted_count']} records."
        )
    used = totals["books_used_value"]
    if used is None:
        used_sentence = "No supported used-book subtotal is available."
    else:
        used_sentence = (
            "The supported used-book subtotal is "
            f"{totals['currency']} {used} across "
            f"{coverage['books_used']['accepted_count']} records."
        )
    unresolved = packet.get("unresolved_book_observations") or []
    unresolved_notice = (
        f"{len(unresolved)} other book observations remain unresolved and are not counted. "
        if unresolved else ""
    )
    scope_notice = (
        "These tracks cover one corroborated section; other captured sections remain unresolved. "
        if packet["book_count_scope"] == "corroborated_scene_subset" else ""
    )
    return (
        "This is a partial packet with "
        f"{totals['book_count']} provisional book tracks: "
        f"{totals['books_identified']} identified, "
        f"{totals['books_unidentified']} unidentified, and "
        f"{totals['books_needs_appraisal']} awaiting appraisal. "
        "The physical room count is not verified. "
        f"{scope_notice}{unresolved_notice}{replacement_sentence} {used_sentence} "
        "Room measurements remain unavailable. "
        "Review the exclusions and unresolved evidence."
    )


def build_packet(state, store) -> dict:
    started = time.monotonic()
    book_ids = [b.id for b in state.books]
    archived_ids = [b.id for b in state.unresolved_books]
    item_ids = [i.id for i in state.items]
    if len(set(book_ids + archived_ids)) != len(book_ids + archived_ids):
        _fail("duplicate book observation ids")
    if len(set(book_ids)) != len(book_ids) or len(set(item_ids)) != len(item_ids):
        _fail("duplicate track ids")
    if set(book_ids + archived_ids) & set(item_ids):
        _fail("book and item ids overlap")
    for book in state.books + state.unresolved_books:
        _check_book(store, state, book)
    for item in state.items:
        _check_item(store, state, item)
    for frame in state.frames:
        _resolve_frame(store, state.id, frame.frame_ref)
    aggregated = _totals(state)
    room = {key: None for key in ROOM_FIELDS}
    room["shape"] = "unknown"
    room["scale_method"] = "unknown"
    room["confidence"] = None
    review_queue = []
    seen_reviews = set()
    for item in state.review_queue:
        key = (item.ref_id, item.reason)
        if key not in seen_reviews:
            seen_reviews.add(key)
            review_queue.append({"ref_id": item.ref_id, "reason": item.reason})
    if ("room", ROOM_REVIEW) not in seen_reviews:
        review_queue.append({"ref_id": "room", "reason": ROOM_REVIEW})
    coverage_complete = all(
        not bucket["excluded"] and bucket["accepted_count"] == bucket["eligible_count"]
        for bucket in aggregated["coverage"].values()
    )
    complete = (
        state.inventory_complete
        and state.processing_complete
        and all(room[k] is not None for k in ROOM_FIELDS)
        and coverage_complete
    )
    books = []
    for book in state.books:
        record = book.model_dump(mode="json")
        record.pop("track_histogram", None)
        books.append(record)
    unresolved_books = []
    for book in state.unresolved_books:
        record = book.model_dump(mode="json")
        record.pop("track_histogram", None)
        record["review_reason"] = "unresolved book proposal; cannot verify as distinct"
        unresolved_books.append(record)
    items = []
    for item in state.items:
        record = item.model_dump(mode="json")
        record.pop("track_histogram", None)
        if record.get("dimensions_cm") is None:
            record["dimensions_cm"] = {"w": None, "h": None, "d": None}
        items.append(record)
    count_scope = (
        "corroborated_scene_subset"
        if state.scene_reconciliation and state.scene_reconciliation.get("unresolved_frame_ids")
        else "provisional_tracks"
    )
    packet = {
        "sweep": {
            "id": state.id,
            "captured_at": state.captured_at,
            "device": state.device,
            "duration_s": state.duration_s,
            "country": state.country,
            "currency": state.currency,
            "ended_at": state.ended_at,
            "packet_generated_at": datetime.now(timezone.utc).isoformat(),
            "revision": state.revision,
        },
        "room": room,
        "frames": [f.frame_ref for f in state.frames],
        "books": books,
        "physical_book_count": (
            len(books) if state.inventory_complete and not unresolved_books
            and count_scope == "provisional_tracks" else None
        ),
        "book_count_scope": count_scope,
        "unresolved_book_observations": unresolved_books,
        "scene_reconciliation": state.scene_reconciliation,
        "items": items,
        "totals": aggregated["totals"],
        "coverage": aggregated["coverage"],
        "review_queue": review_queue,
        "status": "partial" if not complete else "complete",
        "metrics": _metrics(state, started),
    }
    packet["summary"] = _summary(packet)
    return packet


def _esc(value):
    return _html.escape("" if value is None else str(value))


def _link(href, text):
    if (
        isinstance(href, str)
        and (
            href.startswith("https://")
            or FRAME_REF_RE.match(href)
            or CROP_REF_RE.match(href)
        )
    ):
        return (
            f'<a href="{_html.escape(href, quote=True)}"'
            f' rel="noopener noreferrer" target="_blank">{_esc(text)}</a>'
        )
    return _esc(text)


def _price_cells(price):
    if price is None:
        return "unsupported"
    bits = []
    if price.get("amount") is None:
        bits.append("unsupported")
    else:
        bits.append(f"{_esc(price['amount'])} {_esc(price.get('currency'))}")
    quote = price.get("source_quote") or {}
    if quote.get("condition"):
        bits.append("condition " + _esc(quote["condition"]))
    if price.get("condition_assumed"):
        bits.append("assumed condition " + _esc(price["condition_assumed"]))
    if quote.get("amount") and quote.get("currency"):
        bits.append(
            f"source amount {_esc(quote['amount'])} {_esc(quote['currency'])}"
        )
    url = price.get("url") or quote.get("url")
    if url:
        bits.append("source " + _link(url, url))
    when = price.get("retrieved_at") or quote.get("retrieved_at")
    if when:
        bits.append("retrieved " + _esc(when))
    if quote.get("market_country"):
        bits.append("market " + _esc(quote["market_country"]))
    if price.get("foreign_market_fallback"):
        bits.append("foreign market fallback")
    if price.get("foreign_market_same_currency"):
        bits.append("foreign market same currency")
    if price.get("edition_equivalence"):
        bits.append(_esc(price["edition_equivalence"]))
    fx = price.get("fx_evidence") or {}
    if fx.get("cross_rate"):
        bits.append("FX rate " + _esc(fx["cross_rate"]))
    if fx.get("data_at"):
        bits.append("FX data " + _esc(fx["data_at"]))
    if fx.get("attribution"):
        attribution = _esc(fx["attribution"])
        if fx.get("attribution_url"):
            attribution = _link(fx["attribution_url"], fx["attribution"])
        bits.append("FX " + attribution)
    return " — ".join(bits)


def _evidence_links(refs):
    links = [_link(ref, ref) for ref in refs if ref]
    return ", ".join(links)


def render_report_html(packet) -> str:
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        "<title>Library claim packet</title></head><body>",
        f"<h1>Claim packet — {_esc(packet['status'].upper())}</h1>",
        "<p>This is a provisional, partial packet — not a final room count or valuation.</p>",
        "<h2>Sweep</h2><ul>",
    ]
    for key, value in packet["sweep"].items():
        parts.append(f"<li>{_esc(key)}: {_esc(value)}</li>")
    parts.append("</ul><h2>Room</h2><ul>")
    for key, value in packet["room"].items():
        parts.append(f"<li>{_esc(key)}: {_esc(value if value is not None else 'NOT MEASURED')}</li>")
    parts.append("</ul>")
    parts.append("<h2>Captured frames</h2><ul>")
    for ref in packet["frames"]:
        parts.append(f"<li>{_link(ref, ref)}</li>")
    parts.append("</ul>")
    physical_count = packet["physical_book_count"]
    count_note = (
        " Tracks cover one corroborated section only; other views remain unresolved."
        if packet["book_count_scope"] == "corroborated_scene_subset" else ""
    )
    parts.append(
        "<p>Whole-sweep physical book count: "
        + _esc(physical_count if physical_count is not None else "NOT MEASURED")
        + f".{count_note}</p>"
    )
    parts.append("<h2>Books</h2><ul>")
    for book in packet["books"]:
        label = book.get("title") or "(unidentified)"
        refs = [book.get("frame_ref")] + list(book.get("crop_refs") or [])
        refs += [ev.get("frame_ref") for ev in book.get("evidence") or []]
        ref_text = _evidence_links(refs)
        parts.append(
            f"<li>{_esc(label)} [{_esc(book.get('id'))}] — {_esc(book.get('status'))}"
            + (f" — {_esc(book.get('author'))}" if book.get("author") else "")
            + (f"<br>evidence: {ref_text}" if ref_text else "")
            + f"<br>new: {_price_cells(book.get('replacement_cost'))}"
            + f"<br>used: {_price_cells(book.get('used_value'))}</li>"
        )
    if packet.get("unresolved_book_observations"):
        parts.append("</ul><h2>Unresolved book observations (not counted)</h2><ul>")
        for book in packet["unresolved_book_observations"]:
            refs = [book.get("frame_ref")]
            refs += [ev.get("frame_ref") for ev in book.get("evidence") or []]
            parts.append(
                f"<li>{_esc(book.get('id'))} — {_esc(book.get('review_reason'))}"
                + (f"<br>evidence: {_evidence_links(refs)}" if refs else "")
                + "</li>"
            )
    parts.append("</ul><h2>Items</h2><ul>")
    for item in packet["items"]:
        refs = [item.get("frame_ref")]
        refs += [ev.get("frame_ref") for ev in item.get("evidence") or []]
        ref_text = _evidence_links(refs)
        parts.append(
            f"<li>{_esc(item.get('category'))} [{_esc(item.get('id'))}] — "
            f"{_esc(item.get('status'))} — {_esc(item.get('description'))}"
            + f" — {_esc(item.get('valuation_state'))}"
            + (f"<br>evidence: {ref_text}" if ref_text else "")
            + "</li>"
        )
    parts.append("</ul><h2>Totals</h2><ul>")
    for key, value in packet["totals"].items():
        parts.append(f"<li>{_esc(key)}: {_esc(value)}</li>")
    parts.append("</ul><h2>Exclusions</h2><ul>")
    for bucket, detail in packet["coverage"].items():
        for entry in detail["excluded"]:
            parts.append(
                f"<li>{_esc(bucket)}: {_esc(entry['id'])} — {_esc(entry['reason'])}</li>"
            )
    parts.append("</ul><h2>Review queue</h2><ul>")
    for review in packet["review_queue"]:
        parts.append(
            f"<li>{_esc(review['ref_id'])}: {_esc(review['reason'])}</li>"
        )
    parts.append("</ul><h2>Metrics</h2><ul>")
    for key, value in packet["metrics"].items():
        parts.append(f"<li>{_esc(key)}: {_esc(value)}</li>")
    parts.append("</ul>")
    parts.append(f"<p>{_esc(packet['summary'])}</p></body></html>")
    return "".join(parts)


def publish_packet(store, sweep_id) -> dict:
    return store.publish_packet(sweep_id)
