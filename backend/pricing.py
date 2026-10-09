import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from preflight.prices import normalized
from preflight.vision import usage_cost

from .currency import convert_amount
from .fx import FxClient, FxError
from .locales import currency_minor_units
from .models import (
    BookPrice,
    FxEvidence,
    PricingStageRecord,
    ReviewItem,
    SourceQuote,
)
from .price_sources import (
    FetchError,
    canonicalize_url,
    default_resolver,
    extract_source_quote,
    fetch_source,
)
from .reading import usage_cost_adapter
from .sweeps import StoreError, SweepStore


def load_pricing_contract(path: Path) -> dict:
    return json.loads(Path(path).read_text())


class _WireDiscoveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_id: str = Field(min_length=1)
    replacement_urls: list[str] = Field(max_length=2)
    used_urls: list[str] = Field(max_length=2)
    notes: list[str]


class _WireDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[_WireDiscoveryRecord]
    issues: list[str]


def parse_discovery_response(response: dict) -> dict:
    if not isinstance(response, dict):
        raise StoreError(502, "listing discovery response malformed")
    if response.get("status") != "completed":
        raise StoreError(502, "listing discovery response not completed")
    outputs = response.get("output")
    if not isinstance(outputs, list):
        raise StoreError(502, "listing discovery response malformed")
    texts = []
    for message in outputs:
        if not isinstance(message, dict) or message.get("type") != "message":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            raise StoreError(502, "listing discovery response malformed")
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise StoreError(502, "listing discovery provider refused")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if len(texts) != 1:
        raise StoreError(502, "listing discovery response malformed")
    try:
        parsed = json.loads(texts[0])
    except ValueError:
        raise StoreError(502, "listing discovery response malformed") from None
    if not isinstance(parsed, dict):
        raise StoreError(502, "listing discovery response malformed")
    return parsed


def validate_discovery(parsed: dict, ref_ids: list[str]) -> tuple:
    try:
        wire = _WireDiscovery.model_validate(parsed)
    except ValidationError:
        raise StoreError(502, "listing discovery response malformed") from None
    seen = set()
    for record in wire.records:
        if record.ref_id not in ref_ids:
            raise StoreError(502, "listing discovery returned unknown ref_id")
        if record.ref_id in seen:
            raise StoreError(502, "listing discovery returned duplicate ref_id")
        seen.add(record.ref_id)
    if set(ref_ids) != seen:
        raise StoreError(502, "listing discovery omitted supplied ref_ids")
    return [r.model_dump() for r in wire.records], list(wire.issues)


def cited_urls(response: dict) -> set:
    cited = set()
    outputs = response.get("output") if isinstance(response, dict) else None
    if not isinstance(outputs, list):
        return cited
    for item in outputs:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            action = item.get("action")
            if not isinstance(action, dict):
                continue
            sources = action.get("sources")
            if not isinstance(sources, list):
                continue
            for source in sources:
                if isinstance(source, dict) and isinstance(source.get("url"), str):
                    canonical = canonicalize_url(source["url"])
                    if canonical is not None:
                        cited.add(canonical)
        if item.get("type") == "message":
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                annotations = part.get("annotations")
                if not isinstance(annotations, list):
                    continue
                for ann in annotations:
                    if (
                        isinstance(ann, dict)
                        and ann.get("type") == "url_citation"
                        and isinstance(ann.get("url"), str)
                    ):
                        canonical = canonicalize_url(ann["url"])
                        if canonical is not None:
                            cited.add(canonical)
    return cited


def discovery_costs(response, contract: dict) -> tuple:
    if not isinstance(response, dict):
        return None, None, None
    token = usage_cost(response, usage_cost_adapter(contract))
    output = response.get("output")
    tool = None
    if isinstance(output, list):
        searches = 0
        malformed = False
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "web_search_call":
                continue
            action = item.get("action")
            if not isinstance(action, dict) or not isinstance(action.get("type"), str):
                malformed = True
                break
            if action["type"] == "search":
                searches += 1
        if not malformed:
            tool = Decimal(searches) * Decimal(
                contract["cost_evidence"]["web_search_usd_per_search_action"]
            )
    combined = token + tool if token is not None and tool is not None else None
    return token, tool, combined


def build_discovery_payload(
    contract: dict, market_country: str, output_currency: str, books: list
) -> dict:
    config = contract["request_configuration"]
    input_text = (
        "Find cited print-book listing URLs for these validated identities and "
        "the requested market. INPUT_JSON: "
        + json.dumps(
            {
                "country": market_country,
                "currency": output_currency,
                "books": [
                    {
                        "ref_id": book.id,
                        "title": book.title,
                        "author": book.author,
                        "edition": book.edition,
                        "isbn": book.isbn,
                    }
                    for book in books
                ],
            }
        )
    )
    payload = {
        "model": contract["model"],
        "instructions": contract["instructions"],
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": input_text}],
            }
        ],
        "text": {"format": contract["response_format"]},
    }
    for key in (
        "reasoning",
        "tools",
        "include",
        "store",
        "max_output_tokens",
        "max_tool_calls",
    ):
        if key in config:
            payload[key] = config[key]
    return payload


async def _default_discovery(
    payload: dict, client: httpx.AsyncClient, api_key: str, contract: dict
) -> dict:
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
        raise StoreError(502, "listing discovery provider rejected request")
    return response.json()


def _skip_reason(track) -> str | None:
    if track.excluded:
        return "book excluded"
    if track.potential_duplicate:
        return "potential duplicate"
    if track.status == "needs_appraisal":
        return "book needs appraisal"
    if track.status != "identified":
        return "book not identified"
    if not track.title or not track.author:
        return "missing title or author"
    if any(r.status == "contradicted" for r in track.identification_evidence):
        return "unresolved reading contradiction"
    if not any(
        r.status == "accepted"
        and r.confidence >= 0.9
        and normalized(r.title) == normalized(track.title)
        and normalized(r.author) == normalized(track.author)
        for r in track.identification_evidence
    ):
        return "no accepted high-confidence reading"
    return None


def _used_stages(state, kind: str) -> int:
    return sum(
        1
        for s in state.price_stages
        if s.kind == kind
        and s.status in ("running", "completed", "failed", "interrupted")
    )


class PricingManager:
    def __init__(self, contract: dict, clock=time.monotonic, wallclock=None):
        self.contract = contract
        self._clock = clock
        self._wallclock = wallclock or (lambda: datetime.now(timezone.utc))
        limits = contract["runtime_limits"]
        self._deadline_s = float(limits["shared_postwork_deadline_s"])
        self._max_discovery = int(limits["maximum_discovery_calls_per_sweep"])
        self._max_fetches = int(limits["maximum_source_fetches_per_sweep"])
        self._batch_size = int(limits["maximum_identities_per_discovery_batch"])
        self._quote_max_age = float(limits["quote_cache_max_age_s"])
        self._discovery_limit = int(limits["maximum_concurrent_discovery_calls"])
        self._source_limit = int(limits["maximum_concurrent_source_fetches"])
        self._lock = asyncio.Lock()
        self._discovery_sem = asyncio.Semaphore(self._discovery_limit)
        self._source_sem = asyncio.Semaphore(self._source_limit)
        self._deadlines: dict[str, float] = {}
        self._busy: set = set()
        self._quotes: dict = {}
        self._quote_locks: dict = {}

    def quote_lock(self, key) -> asyncio.Lock:
        lock = self._quote_locks.get(key)
        if lock is None:
            lock = self._quote_locks[key] = asyncio.Lock()
        return lock

    def acquire(self, sweep_id: str) -> None:
        if sweep_id in self._busy:
            raise StoreError(409, "book pricing already in progress")
        self._busy.add(sweep_id)

    def release(self, sweep_id: str) -> None:
        self._busy.discard(sweep_id)

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
        return self._persisted_remaining(state.pricing_deadline_at)

    async def deadline(self, store: SweepStore, sweep_id: str) -> float:
        async with self._lock:
            existing = self._deadlines.get(sweep_id)
            if existing is not None:
                return existing
            state = store.get(sweep_id)
            iso = state.pricing_deadline_at
            if iso is None:
                candidate = self._wallclock() + timedelta(seconds=self._deadline_s)
                if state.ended_at:
                    try:
                        ended = datetime.fromisoformat(state.ended_at)
                    except (TypeError, ValueError):
                        ended = None
                    if ended is not None and (
                        ended.tzinfo is None or ended.utcoffset() is None
                    ):
                        ended = None
                    if ended is not None:
                        candidate = min(
                            candidate, ended + timedelta(seconds=self._deadline_s)
                        )
                iso = store.init_pricing_deadline(sweep_id, candidate.isoformat())
            remaining = max(0.0, self._persisted_remaining(iso))
            value = self._clock() + remaining
            self._deadlines[sweep_id] = value
            return value

    def gate(self, store: SweepStore, sweep_id: str, deadline: float) -> float:
        return max(
            0.0,
            min(deadline - self._clock(), self.remaining_seconds(store, sweep_id)),
        )

    def cached_quote(self, key):
        entry = self._quotes.get(key)
        if entry is None:
            return None
        try:
            retrieved = datetime.fromisoformat(entry["retrieved_at"])
        except (KeyError, TypeError, ValueError):
            self._quotes.pop(key, None)
            return None
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=timezone.utc)
        age = (self._wallclock() - retrieved).total_seconds()
        if age < 0 or age > self._quote_max_age:
            self._quotes.pop(key, None)
            return None
        return entry

    def cache_quote(self, key, quote) -> None:
        self._quotes[key] = quote


def _stage_name(prefix: str, sweep_id: str) -> str:
    return f"{prefix}_{sweep_id}_{uuid.uuid4().hex}.json"


def _persist(store, sweep_id, kind, payload) -> str:
    return store.persist_stage(
        sweep_id,
        _stage_name(f"pricing_{kind}", sweep_id),
        json.dumps(payload, default=str).encode(),
    )


def _source_quote(quote: dict) -> SourceQuote:
    fields = {k: quote.get(k) for k in (
        "matched_title",
        "matched_author",
        "listing_title",
        "listing_author",
        "listing_format",
        "listing_availability",
        "listing_isbn",
        "listing_edition",
        "source_snapshot_ref",
        "discovery_stage_ref",
    )}
    return SourceQuote(
        amount=quote["amount"],
        currency=quote["currency"],
        condition=quote["condition"],
        market_country=quote["market_country"],
        url=quote["url"],
        retrieved_at=quote["retrieved_at"],
        market_evidence=quote["market_evidence"],
        **fields,
    )


def _valuate(
    quote: dict,
    slot_condition: str,
    target_country: str,
    output_currency: str,
    snapshot,
    contract: dict,
) -> dict:
    threshold = Decimal(contract["admission"]["appraisal_threshold_aed"])
    foreign = quote["market_country"] != target_country
    source_currency = quote["currency"]
    amount = quote["amount"]
    needs_conversion = source_currency != output_currency
    rates = snapshot.rates if snapshot is not None else None
    fx_needed = needs_conversion or source_currency != "AED"
    if fx_needed:
        missing = (
            rates is None
            or source_currency not in rates
            or (source_currency != "AED" and "AED" not in rates)
            or (needs_conversion and output_currency not in rates)
        )
        if missing:
            return {
                "price": BookPrice(
                    amount=None,
                    currency=output_currency,
                    source="listing",
                    url=quote["url"],
                    retrieved_at=quote["retrieved_at"],
                    converted=needs_conversion or foreign,
                    condition_assumed="good" if slot_condition == "used" else None,
                    foreign_market_fallback=foreign,
                    foreign_market_same_currency=foreign and not needs_conversion,
                    edition_equivalence="current_print_equivalent",
                    source_quote=_source_quote(quote),
                ),
                "candidate": None,
                "reviews": [
                    "converted amount unavailable: "
                    "foreign exchange rates unavailable for " + source_currency
                ],
            }
    if source_currency == "AED":
        aed_value = amount
        threshold_evidence = {"source_rate": "1", "aed_rate": "1"}
    else:
        aed_value = amount * rates["AED"] / rates[source_currency]
        threshold_evidence = {
            "source_rate": str(rates[source_currency]),
            "aed_rate": str(rates["AED"]),
        }
    threshold_evidence["aed_value"] = str(aed_value)
    if aed_value > threshold:
        return {
            "price": None,
            "candidate": quote,
            "appraisal": True,
            "reviews": [],
            "threshold": threshold_evidence,
        }
    if needs_conversion:
        converted = convert_amount(
            amount,
            source_currency,
            output_currency,
            rates,
            minor_units=currency_minor_units(output_currency),
        )
        fx = FxEvidence(
            kind="provider_feed",
            source_url=str(snapshot.source_url),
            data_at=snapshot.data_at.isoformat(),
            retrieved_at=snapshot.retrieved_at.isoformat(),
            source_currency=source_currency,
            target_currency=output_currency,
            source_rate=rates[source_currency],
            target_rate=rates[output_currency],
            cross_rate=rates[output_currency] / rates[source_currency],
            attribution=snapshot.attribution,
            attribution_url=snapshot.attribution_url,
        )
    else:
        converted = convert_amount(
            amount,
            source_currency,
            output_currency,
            {source_currency: Decimal(1)},
            minor_units=currency_minor_units(output_currency),
        )
        fx = FxEvidence(
            kind="currency_identity",
            retrieved_at=quote["retrieved_at"],
            source_currency=source_currency,
            target_currency=output_currency,
            source_rate=Decimal(1),
            target_rate=Decimal(1),
            cross_rate=Decimal(1),
        )
    return {
        "price": BookPrice(
            amount=converted,
            currency=output_currency,
            source="listing",
            url=quote["url"],
            retrieved_at=quote["retrieved_at"],
            converted=needs_conversion or foreign,
            condition_assumed="good" if slot_condition == "used" else None,
            foreign_market_fallback=foreign,
            foreign_market_same_currency=foreign and not needs_conversion,
            edition_equivalence="current_print_equivalent",
            source_quote=_source_quote(quote),
            fx_evidence=fx,
        ),
        "candidate": None,
        "reviews": [],
        "threshold": threshold_evidence,
    }


def _quote_key(
    sweep_id: str,
    track,
    original_target: str,
    market: str,
    output_currency: str,
    condition: str,
) -> tuple:
    return (
        sweep_id,
        normalized(track.title),
        normalized(track.author),
        track.edition,
        track.isbn,
        original_target,
        market,
        output_currency,
        condition,
    )


async def price_books(
    store: SweepStore,
    sweep_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    contract: dict,
    book_ids: list[str] | None = None,
    manager: PricingManager | None = None,
    provider=None,
    resolver=None,
    fx_client=None,
) -> dict:
    mgr = manager or PricingManager(contract)
    limits = contract["runtime_limits"]
    state = store.get(sweep_id)
    initial_reviews = len(state.review_queue)
    target_country = state.country
    output_currency = state.currency
    fallback_country = (
        limits["fallback_country_if_target_is_us"]
        if target_country == "US"
        else limits["fallback_country_otherwise"]
    )
    known = {b.id: b for b in state.books}
    if book_ids is not None:
        missing = [bid for bid in book_ids if bid not in known]
        if missing:
            raise StoreError(404, "unknown book")
        selected = []
        seen_ids = set()
        for bid in book_ids:
            if bid not in seen_ids:
                seen_ids.add(bid)
                selected.append(known[bid])
    else:
        selected = list(state.books)
    results = {}
    reviews = []
    eligible = []
    snapshots = {}
    for track in selected:
        reason = _skip_reason(track)
        results[track.id] = {"skipped": reason}
        if reason is None:
            eligible.append(track)
            snapshots[track.id] = {
                "title": track.title,
                "author": track.author,
                "edition": track.edition,
                "isbn": track.isbn,
                "status": track.status,
                "assertions": [
                    a.model_dump(mode="json") for a in track.assertions
                ],
                "accepted": [
                    (r.title, r.author)
                    for r in track.identification_evidence
                    if r.status == "accepted"
                ],
            }
        else:
            reviews.append(
                ReviewItem(ref_id=track.id, reason="pricing skipped: " + reason)
            )
    if reviews:
        store.append_reviews(sweep_id, reviews)
    if not eligible:
        return {
            "sweep_id": sweep_id,
            "valuations": {
                bid: {
                    "replacement": None,
                    "used": None,
                    "needs_appraisal": known[bid].status == "needs_appraisal",
                    "skipped": entry["skipped"],
                    "source_notes": [],
                }
                for bid, entry in results.items()
            },
            "reviews_added": len(store.get(sweep_id).review_queue) - initial_reviews,
            "partial": True,
        }
    if not api_key and provider is None:
        raise StoreError(503, "OPENAI_API_KEY not configured")
    fetcher = resolver or default_resolver
    mgr.acquire(sweep_id)
    try:
        deadline = await mgr.deadline(store, sweep_id)
        discovery_sem = mgr._discovery_sem
        source_sem = mgr._source_sem
        quotes_map: dict = {}
        book_reviews: dict = {t.id: [] for t in eligible}
        source_notes: dict = {t.id: [] for t in eligible}
        discovery_refs: dict = {}
        discovery_errors = []
        missing: dict = {
            t.id: {"replacement", "used"} for t in eligible
        }

        def finish_stage(index, kind, ref_ids, status, *, payload=None, error=None,
                         started=None, model=None, usage=None, cost=None,
                         cost_evidence=None):
            elapsed = (
                (mgr._clock() - started) * 1000.0 if started is not None else None
            )
            ref = (
                _persist(store, sweep_id, kind, payload)
                if payload is not None
                else None
            )
            store.update_price_stage(
                sweep_id,
                index,
                PricingStageRecord(
                    kind=kind,
                    ref_ids=ref_ids,
                    status=status,
                    persisted_ref=ref,
                    model=model,
                    usage=usage,
                    cost_usd_estimate=cost,
                    cost_evidence=cost_evidence,
                    elapsed_ms=elapsed,
                    error=error,
                ),
            )
            return ref

        def record_cached_stage(track, quote):
            store.record_price_stage(
                sweep_id,
                PricingStageRecord(
                    kind="source",
                    ref_ids=[track.id],
                    status="cached",
                    persisted_ref=quote.get("source_snapshot_ref"),
                    cost_usd_estimate="0",
                ),
            )

        for track in eligible:
            for slot_key, condition in (("replacement", "new"), ("used", "used")):
                for market in (target_country, fallback_country):
                    quote = mgr.cached_quote(
                        _quote_key(
                            sweep_id,
                            track,
                            target_country,
                            market,
                            output_currency,
                            condition,
                        )
                    )
                    if quote is not None and quote["market_country"] == market:
                        record_cached_stage(track, quote)
                        quotes_map.setdefault(track.id, {})[slot_key] = quote
                        missing[track.id].discard(slot_key)
                        break

        async def run_discovery(batch, market):
            ref_ids = [b.id for b in batch]
            started = mgr._clock()
            raw = None
            index = None
            try:
                payload = build_discovery_payload(
                    contract, market, output_currency, batch
                )
                async with discovery_sem:
                    async with mgr._lock:
                        used = _used_stages(store.get(sweep_id), "discovery")
                        if used >= mgr._max_discovery:
                            index = store.record_price_stage(
                                sweep_id,
                                PricingStageRecord(
                                    kind="discovery",
                                    ref_ids=ref_ids,
                                    status="skipped",
                                    error="discovery call budget exhausted",
                                ),
                            )
                            return {"skipped": True}
                        index = store.record_price_stage(
                            sweep_id,
                            PricingStageRecord(
                                kind="discovery",
                                ref_ids=ref_ids,
                                status="running",
                            ),
                        )
                    remaining = mgr.gate(store, sweep_id, deadline)
                    if remaining <= 0:
                        raise StoreError(429, "pricing shared deadline exceeded")
                    raw = await asyncio.wait_for(
                        (
                            _default_discovery(payload, client, api_key, contract)
                            if provider is None
                            else provider(payload)
                        ),
                        timeout=min(
                            float(
                                contract["request_configuration"]["timeout_s"]
                            ),
                            remaining,
                        ),
                    )
                parsed = parse_discovery_response(raw)
                records, issues = validate_discovery(parsed, ref_ids)
            except StoreError as exc:
                token, tool, combined = discovery_costs(raw, contract)
                finish_stage(
                    index,
                    "discovery",
                    ref_ids,
                    "failed",
                    payload={
                        "status": "failed",
                        "market": market,
                        "ref_ids": ref_ids,
                        "error": exc.detail,
                        "raw_response": raw,
                        "usage": raw.get("usage") if isinstance(raw, dict) else None,
                        "model": raw.get("model") if isinstance(raw, dict) else None,
                        "cost_evidence": contract["cost_evidence"],
                    },
                    error=exc.detail,
                    started=started,
                    model=raw.get("model") if isinstance(raw, dict) else None,
                    usage=raw.get("usage") if isinstance(raw, dict) else None,
                    cost=format(combined, "f") if combined is not None else None,
                    cost_evidence={
                        "token_usd_estimate": (
                            format(token, "f") if token is not None else None
                        ),
                        "tool_usd_estimate": (
                            format(tool, "f") if tool is not None else None
                        ),
                    },
                )
                raise
            except asyncio.CancelledError:
                if index is not None:
                    finish_stage(
                        index,
                        "discovery",
                        ref_ids,
                        "interrupted",
                        error="interrupted",
                        started=started,
                    )
                raise
            except Exception:
                finish_stage(
                    index,
                    "discovery",
                    ref_ids,
                    "failed",
                    payload={
                        "status": "failed",
                        "market": market,
                        "ref_ids": ref_ids,
                        "error": "listing discovery provider unavailable",
                        "raw_response": raw,
                    },
                    error="listing discovery provider unavailable",
                    started=started,
                )
                raise StoreError(
                    502, "listing discovery provider unavailable"
                ) from None
            token, tool, combined = discovery_costs(raw, contract)
            ref = finish_stage(
                index,
                "discovery",
                ref_ids,
                "completed",
                payload={
                    "status": "completed",
                    "market": market,
                    "ref_ids": ref_ids,
                    "raw_response": raw,
                    "usage": raw.get("usage"),
                    "model": raw.get("model"),
                    "untrusted_issues": issues,
                    "untrusted_notes": {
                        r["ref_id"]: r["notes"] for r in records
                    },
                    "cost_evidence": contract["cost_evidence"],
                },
                started=started,
                model=raw.get("model"),
                usage=raw.get("usage"),
                cost=format(combined, "f") if combined is not None else None,
                cost_evidence={
                    "token_usd_estimate": (
                        format(token, "f") if token is not None else None
                    ),
                    "tool_usd_estimate": (
                        format(tool, "f") if tool is not None else None
                    ),
                },
            )
            return {
                "records": {r["ref_id"]: r for r in records},
                "issues": issues,
                "cited": cited_urls(raw),
                "stage_ref": ref,
            }

        async def fetch_and_extract(track, canonical, condition):
            hop_indices = []

            async def before_request(current):
                async with mgr._lock:
                    if mgr.gate(store, sweep_id, deadline) <= 0:
                        store.record_price_stage(
                            sweep_id,
                            PricingStageRecord(
                                kind="source",
                                ref_ids=[track.id],
                                status="skipped",
                                error="pricing shared deadline exceeded",
                            ),
                        )
                        raise FetchError("pricing shared deadline exceeded")
                    used = _used_stages(store.get(sweep_id), "source")
                    if used >= mgr._max_fetches:
                        store.record_price_stage(
                            sweep_id,
                            PricingStageRecord(
                                kind="source",
                                ref_ids=[track.id],
                                status="skipped",
                                error="source fetch budget exhausted",
                            ),
                        )
                        raise FetchError("source fetch budget exhausted")
                    hop_indices.append(
                        store.record_price_stage(
                            sweep_id,
                            PricingStageRecord(
                                kind="source",
                                ref_ids=[track.id],
                                status="running",
                            ),
                        )
                    )

            def finish_hops(status, error=None, ref=None):
                for idx in hop_indices[:-1]:
                    store.update_price_stage(
                        sweep_id,
                        idx,
                        PricingStageRecord(
                            kind="source",
                            ref_ids=[track.id],
                            status="completed",
                            persisted_ref=ref,
                        ),
                    )
                if hop_indices:
                    store.update_price_stage(
                        sweep_id,
                        hop_indices[-1],
                        PricingStageRecord(
                            kind="source",
                            ref_ids=[track.id],
                            status=status,
                            persisted_ref=ref,
                            error=error,
                        ),
                    )

            retrieved_at = None
            try:
                async with source_sem:
                    remaining = mgr.gate(store, sweep_id, deadline)
                    if remaining <= 0:
                        raise FetchError("pricing shared deadline exceeded")
                    retrieved_at = mgr._wallclock().isoformat()
                    final_url, html = await asyncio.wait_for(
                        fetch_source(
                            client,
                            canonical,
                            resolver=fetcher,
                            limits=limits,
                            before_request=before_request,
                        ),
                        timeout=min(
                            float(limits["source_fetch_timeout_s"]), remaining
                        ),
                    )
            except asyncio.CancelledError:
                finish_hops("interrupted")
                raise
            except asyncio.TimeoutError:
                finish_hops("interrupted")
                return None, "source fetch timed out"
            except FetchError as exc:
                ref = _persist(
                    store,
                    sweep_id,
                    "source",
                    {
                        "status": "failed",
                        "requested_url": canonical,
                        "retrieved_at": retrieved_at,
                        "error": str(exc),
                    },
                )
                finish_hops("failed", str(exc), ref)
                return None, str(exc)
            try:
                quote = extract_source_quote(
                    html,
                    final_url,
                    retrieved_at,
                    track.title,
                    track.author,
                    condition,
                )
            except Exception:
                ref = _persist(
                    store,
                    sweep_id,
                    "source",
                    {
                        "status": "failed",
                        "requested_url": canonical,
                        "url": final_url,
                        "retrieved_at": retrieved_at,
                        "error": "source extraction failed",
                        "html": html,
                    },
                )
                finish_hops("failed", "source extraction failed", ref)
                return None, "source extraction failed"
            ref = _persist(
                store,
                sweep_id,
                "source",
                {
                    "status": "completed",
                    "requested_url": canonical,
                    "url": final_url,
                    "retrieved_at": retrieved_at,
                    "matched": quote is not None,
                    "html": html,
                },
            )
            finish_hops("completed", ref=ref)
            if quote is not None:
                quote["source_snapshot_ref"] = ref
            return quote, None

        async def consume_slot(track, urls, condition, market, cited):
            uncited = 0
            foreign = 0
            last_error = None
            for url in urls[:2]:
                canonical = canonicalize_url(url)
                if canonical is None or canonical not in cited:
                    uncited += 1
                    continue
                key = _quote_key(
                    sweep_id,
                    track,
                    target_country,
                    market,
                    output_currency,
                    condition,
                )
                async with mgr.quote_lock(key):
                    quote = mgr.cached_quote(key)
                    if quote is not None:
                        record_cached_stage(track, quote)
                        return quote, {"uncited": uncited, "foreign": foreign}
                    quote, last_error = await fetch_and_extract(
                        track, canonical, condition
                    )
                    if quote is None:
                        continue
                    if quote["market_country"] != market:
                        foreign += 1
                        continue
                    quote["discovery_stage_ref"] = discovery_refs.get(track.id)
                    mgr.cache_quote(key, quote)
                    return quote, {"uncited": uncited, "foreign": foreign}
            return None, {"uncited": uncited, "foreign": foreign, "error": last_error}

        async def price_one(track, records, cited, market, wanted_slots):
            quotes = {}
            out_reviews = []
            for slot_key, url_key, condition in (
                ("replacement", "replacement_urls", "new"),
                ("used", "used_urls", "used"),
            ):
                if slot_key not in wanted_slots:
                    continue
                if records is None:
                    out_reviews.append("listing discovery did not cover this book")
                    continue
                urls = records.get(url_key) or []
                quote, info = await consume_slot(
                    track, urls, condition, market, cited
                )
                if info["uncited"]:
                    out_reviews.append(
                        f"discarded {info['uncited']} uncited {slot_key} listing url(s)"
                    )
                if info["foreign"]:
                    out_reviews.append(
                        f"discarded {info['foreign']} {slot_key} listing(s) "
                        f"outside {market} market"
                    )
                if quote is not None:
                    quotes[slot_key] = quote
                elif info.get("error"):
                    out_reviews.append(
                        f"{slot_key} listing source failed: {info['error']}"
                    )
                else:
                    out_reviews.append(
                        "no supported "
                        + (
                            "replacement-new"
                            if slot_key == "replacement"
                            else "used"
                        )
                        + " listing among cited sources"
                    )
            return track.id, quotes, out_reviews

        def collect_wave(outcomes, batches):
            records_map = {}
            cited = set()
            for batch, outcome in zip(batches, outcomes):
                if isinstance(outcome, Exception):
                    err = (
                        outcome
                        if isinstance(outcome, StoreError)
                        else StoreError(502, "listing discovery provider unavailable")
                    )
                    discovery_errors.append(err)
                    for b in batch:
                        book_reviews[b.id].append(
                            "not priced: listing discovery failed"
                        )
                    continue
                if outcome.get("skipped"):
                    for b in batch:
                        book_reviews[b.id].append(
                            "not priced: discovery call budget exhausted"
                        )
                    continue
                records_map.update(outcome["records"])
                cited |= outcome["cited"]
                for record in outcome["records"].values():
                    discovery_refs[record["ref_id"]] = outcome["stage_ref"]
                    source_notes[record["ref_id"]].extend(
                        record.get("notes") or []
                    )
            return records_map, cited

        async def slot_wave(books, batches, outcomes, market, wanted_map):
            records_map, cited = collect_wave(outcomes, batches)
            tasks = [
                price_one(
                    track,
                    records_map.get(track.id),
                    cited,
                    market,
                    wanted_map[track.id],
                )
                for track in books
            ]
            results_wave = await asyncio.gather(*tasks, return_exceptions=True)
            for track, outcome in zip(books, results_wave):
                if isinstance(outcome, Exception):
                    err = (
                        outcome
                        if isinstance(outcome, StoreError)
                        else StoreError(502, "pricing source work failed")
                    )
                    discovery_errors.append(err)
                    book_reviews[track.id].append(
                        "not priced: " + getattr(err, "detail", str(err))
                    )
                    continue
                _, quotes, revs = outcome
                quotes_map.setdefault(track.id, {}).update(quotes)
                missing[track.id] -= set(quotes)
                book_reviews[track.id].extend(revs)

        discovery_books = [t for t in eligible if missing[t.id]]
        batches = [
            discovery_books[i : i + mgr._batch_size]
            for i in range(0, len(discovery_books), mgr._batch_size)
        ]
        local_outcomes = await asyncio.gather(
            *(run_discovery(batch, target_country) for batch in batches),
            return_exceptions=True,
        )
        await slot_wave(
            discovery_books, batches, local_outcomes, target_country, missing
        )

        fallback_books = [t for t in eligible if missing[t.id]]
        fb_batches = [
            fallback_books[i : i + mgr._batch_size]
            for i in range(0, len(fallback_books), mgr._batch_size)
        ]
        if fb_batches:
            fb_outcomes = await asyncio.gather(
                *(run_discovery(batch, fallback_country) for batch in fb_batches),
                return_exceptions=True,
            )
            await slot_wave(
                fallback_books, fb_batches, fb_outcomes, fallback_country, missing
            )

        attempts = len(batches) + len(fb_batches)
        if attempts and len(discovery_errors) == attempts and not any(
            quotes_map.get(t.id) for t in eligible
        ):
            raise discovery_errors[0]

        any_quotes = [
            quote for slots in quotes_map.values() for quote in slots.values()
        ]
        needs_fx = any(
            quote["currency"] != output_currency or quote["currency"] != "AED"
            for quote in any_quotes
        )
        snapshot = None
        fx_error = None
        if needs_fx:
            remaining = mgr.gate(store, sweep_id, deadline)
            if remaining <= 0:
                fx_error = "pricing shared deadline exceeded"
            else:
                try:
                    fx = fx_client or FxClient(client)
                    snapshot = await asyncio.wait_for(
                        fx.get(), timeout=min(10.0, remaining)
                    )
                except (FxError, asyncio.TimeoutError, httpx.HTTPError):
                    fx_error = "foreign exchange rates unavailable"

        valuations = {}
        fx_provenance = None
        if snapshot is not None:
            used_currencies = sorted(
                {q["currency"] for q in any_quotes} | {"AED", output_currency}
            )
            fx_provenance = {
                "source_url": str(snapshot.source_url),
                "data_at": snapshot.data_at.isoformat(),
                "retrieved_at": snapshot.retrieved_at.isoformat(),
                "attribution": snapshot.attribution,
                "attribution_url": snapshot.attribution_url,
                "rates_used": {
                    c: str(snapshot.rates[c])
                    for c in used_currencies
                    if c in snapshot.rates
                },
            }
        elif fx_error:
            fx_provenance = {"error": fx_error}
        committed = {}
        for track in eligible:
            quotes = quotes_map.get(track.id, {})
            val_reviews = list(book_reviews[track.id])
            priced = {}
            candidates = {}
            threshold_evidence = {}
            appraisal = False
            for slot_key, quote in quotes.items():
                condition = "new" if slot_key == "replacement" else "used"
                outcome = _valuate(
                    quote,
                    condition,
                    target_country,
                    output_currency,
                    snapshot,
                    contract,
                )
                if outcome.get("threshold"):
                    threshold_evidence[slot_key] = outcome["threshold"]
                if outcome.get("appraisal"):
                    appraisal = True
                    candidates[slot_key] = quote
                else:
                    priced[slot_key] = outcome["price"]
                    val_reviews.extend(outcome.get("reviews", []))
            if appraisal:
                val_reviews.append(
                    "candidate listing exceeds appraisal threshold; "
                    "book needs appraisal"
                )
            committed[track.id] = {
                "needs_appraisal": appraisal,
                "replacement": priced.get("replacement"),
                "used": priced.get("used"),
                "reviews": val_reviews,
            }
            valuations[track.id] = {
                "quotes": quotes,
                "candidates": candidates,
                "needs_appraisal": appraisal,
                "threshold": threshold_evidence,
                "reviews": val_reviews,
                "source_notes": source_notes[track.id],
            }

        stage_ref = _persist(
            store,
            sweep_id,
            "valuation",
            {
                "status": "completed",
                "fx": fx_provenance,
                "valuations": {
                    bid: {
                        "quotes": v["quotes"],
                        "candidates": v["candidates"],
                        "needs_appraisal": v["needs_appraisal"],
                        "threshold": v["threshold"],
                        "reviews": v["reviews"],
                        "untrusted_source_notes": v["source_notes"],
                    }
                    for bid, v in valuations.items()
                },
            },
        )
        store.record_price_stage(
            sweep_id,
            PricingStageRecord(
                kind="valuation",
                ref_ids=[t.id for t in eligible],
                status="completed",
                persisted_ref=stage_ref,
            ),
        )
        final = store.commit_prices(sweep_id, snapshots, committed, stage_ref)
        final_books = {b.id: b for b in final.books}
        partial = bool(discovery_errors)
        valuations_out = {}
        for bid, entry in results.items():
            book = final_books[bid]
            valuations_out[bid] = {
                "replacement": (
                    book.replacement_cost.model_dump(mode="json")
                    if book.replacement_cost is not None
                    else None
                ),
                "used": (
                    book.used_value.model_dump(mode="json")
                    if book.used_value is not None
                    else None
                ),
                "needs_appraisal": book.status == "needs_appraisal",
                "skipped": entry["skipped"],
                "source_notes": source_notes.get(bid, []),
            }
            if (
                entry["skipped"]
                or book.status == "needs_appraisal"
                or book.replacement_cost is None
                or book.replacement_cost.amount is None
                or book.used_value is None
                or book.used_value.amount is None
            ):
                partial = True
        return {
            "sweep_id": sweep_id,
            "valuations": valuations_out,
            "reviews_added": len(final.review_queue) - initial_reviews,
            "partial": partial,
        }
    finally:
        mgr.release(sweep_id)
