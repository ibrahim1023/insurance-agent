import asyncio
import ipaddress
import json
import re
import socket
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from preflight.prices import ListingData, author_names, extract_offer, normalized

from .locales import validate_country, validate_currency

AMAZON_HOSTS = frozenset({"amazon.ae", "www.amazon.ae"})
PRINT_FORMATS = {"hardcover", "paperback", "hardback", "softcover"}
STOCK_OK = {"instock", "limitedavailability"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_PRICE_RE = re.compile(r"\d+(?:\.\d+)?")


class FetchError(Exception):
    pass


def canonicalize_url(url: str) -> str | None:
    if not isinstance(url, str):
        return None
    text = url.strip()
    if (
        not text
        or "\\" in text
        or any(ord(c) < 0x21 or ord(c) == 0x7F for c in text)
    ):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not host:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if port not in (None, 443):
        return None
    try:
        host = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if not host or host[0] == "." or host[-1] == ".":
        return None
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def _types(entry: dict) -> set:
    kind = entry.get("@type")
    if isinstance(kind, str):
        return {kind}
    if isinstance(kind, list):
        return {k for k in kind if isinstance(k, str)}
    return set()


def _trailing_token(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1].strip().casefold()


def _strict_price(value) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        price = value
    elif isinstance(value, int):
        price = Decimal(value)
    elif isinstance(value, str) and _PRICE_RE.fullmatch(value.strip()):
        try:
            price = Decimal(value.strip())
        except InvalidOperation:
            return None
    else:
        return None
    if not price.is_finite() or price <= 0:
        return None
    return price


def _country_strings(value) -> list:
    out = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        if "Country" in _types(value):
            for key in ("identifier", "name"):
                item = value.get(key)
                if isinstance(item, str):
                    out.append(item)
    elif isinstance(value, list):
        for entry in value:
            out.extend(_country_strings(entry))
    return out


def _market_country(offer: dict) -> str | None:
    values = []
    if "eligibleRegion" in offer:
        values.extend(_country_strings(offer["eligibleRegion"]))
    seller = offer.get("seller")
    if isinstance(seller, dict):
        address = seller.get("address")
        if isinstance(address, dict):
            values.extend(_country_strings(address.get("addressCountry")))
    countries = set()
    for value in values:
        try:
            countries.add(validate_country(value))
        except ValueError:
            return None
    if len(countries) != 1:
        return None
    return next(iter(countries))


def _top_level(data) -> list:
    out = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        graph = item.get("@graph")
        if isinstance(graph, list):
            out.extend(entry for entry in graph if isinstance(entry, dict))
        out.append(item)
    expanded = []
    for entry in out:
        types = _types(entry)
        if "WebPage" in types and not ({"Book", "Product"} & types):
            main = entry.get("mainEntity")
            mains = main if isinstance(main, list) else [main]
            expanded.extend(x for x in mains if isinstance(x, dict))
        else:
            expanded.append(entry)
    return expanded


def _matching_entries(entries, expected_title, expected_author, page_url) -> list:
    title_norm = normalized(expected_title)
    author_norm = normalized(expected_author)
    found = []
    for entry in entries:
        if not ({"Book", "Product"} & _types(entry)):
            continue
        if normalized(entry.get("name")) != title_norm:
            continue
        if author_norm not in author_names(entry.get("author")):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or canonicalize_url(url) != page_url:
            continue
        if _trailing_token(entry.get("bookFormat")) not in PRINT_FORMATS:
            continue
        found.append(entry)
    return found


def _eligible_offers(entry: dict, page_url: str, requested_condition: str) -> list:
    offers = entry.get("offers")
    items = offers if isinstance(offers, list) else [offers]
    candidates = []
    for offer in items:
        if not isinstance(offer, dict):
            continue
        types = _types(offer)
        if "Offer" not in types or "AggregateOffer" in types:
            continue
        condition = _trailing_token(offer.get("itemCondition"))
        if condition == "newcondition":
            condition = "new"
        elif condition == "usedcondition":
            condition = "used"
        else:
            continue
        if condition != requested_condition:
            continue
        if _trailing_token(offer.get("availability")) not in STOCK_OK:
            continue
        price = _strict_price(offer.get("price"))
        if price is None:
            continue
        currency_raw = offer.get("priceCurrency")
        if not isinstance(currency_raw, str):
            continue
        try:
            currency = validate_currency(currency_raw)
        except ValueError:
            continue
        offer_url = offer.get("url")
        if offer_url is not None and (
            not isinstance(offer_url, str)
            or canonicalize_url(offer_url) != page_url
        ):
            continue
        market = _market_country(offer)
        if market is None:
            continue
        candidates.append(
            {
                "price": price,
                "currency": currency,
                "market": market,
                "offer": offer,
            }
        )
    return candidates


def _reject_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _raw_author_names(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(value.get("name") or "")]
    if isinstance(value, list):
        return [name for entry in value for name in _raw_author_names(entry)]
    return []


def extract_source_quote(
    html: str,
    page_url: str,
    retrieved_at: str,
    title: str,
    author: str,
    requested_condition: str,
) -> dict | None:
    canonical = canonicalize_url(page_url)
    if canonical is None or requested_condition not in ("new", "used"):
        return None
    try:
        observed = datetime.fromisoformat(retrieved_at)
    except (ValueError, TypeError):
        return None
    if observed.tzinfo is None or observed.utcoffset() is None:
        return None
    host = urlsplit(canonical).hostname
    if host in AMAZON_HOSTS:
        if requested_condition != "new":
            return None
        offer = extract_offer(html, canonical, retrieved_at, title, author)
        if offer is None or offer.get("currency") != "AED":
            return None
        evidence = offer.get("source_evidence") or {}
        matched_title = normalized(evidence.get("title"))
        matched_author = normalized(evidence.get("byline"))
        if (
            not matched_title
            or not matched_author
            or not offer.get("listing_format")
            or not offer.get("availability")
        ):
            return None
        return {
            "amount": Decimal(offer["amount"]),
            "currency": "AED",
            "condition": "new",
            "market_country": validate_country("AE"),
            "url": canonical,
            "retrieved_at": retrieved_at,
            "matched_title": matched_title,
            "matched_author": matched_author,
            "listing_title": evidence["title"],
            "listing_author": evidence["byline"],
            "listing_format": offer["listing_format"],
            "listing_availability": offer["availability"],
            "market_evidence": "amazon_uae",
            "listing_isbn": None,
            "listing_edition": None,
        }
    parser = ListingData()
    parser.feed(html)
    entries = []
    for block in parser.blocks:
        try:
            data = json.loads(
                block, parse_float=Decimal, object_pairs_hook=_reject_duplicate_keys
            )
        except (ValueError, TypeError):
            continue
        entries.extend(_top_level(data))
    matches = _matching_entries(entries, title, author, canonical)
    if len(matches) != 1:
        return None
    entry = matches[0]
    candidates = _eligible_offers(entry, canonical, requested_condition)
    if len(candidates) != 1:
        return None
    chosen = candidates[0]
    authors = author_names(entry.get("author"))
    matched_author = next(
        (name for name in authors if name == normalized(author)), None
    )
    listing_author = next(
        (
            raw
            for raw in _raw_author_names(entry.get("author"))
            if normalized(raw) == normalized(author)
        ),
        None,
    )
    return {
        "amount": chosen["price"],
        "currency": chosen["currency"],
        "condition": requested_condition,
        "market_country": chosen["market"],
        "url": canonical,
        "retrieved_at": retrieved_at,
        "matched_title": normalized(entry.get("name")),
        "matched_author": matched_author or normalized(author),
        "listing_title": str(entry.get("name") or ""),
        "listing_author": listing_author,
        "listing_format": _trailing_token(entry.get("bookFormat")),
        "listing_availability": _trailing_token(chosen["offer"].get("availability")),
        "market_evidence": (
            "eligible_region" if "eligibleRegion" in chosen["offer"] else "seller_address_country"
        ),
        "listing_isbn": (
            entry.get("isbn") if isinstance(entry.get("isbn"), str) else None
        ),
        "listing_edition": (
            entry.get("bookEdition") if isinstance(entry.get("bookEdition"), str) else None
        ),
    }


async def default_resolver(host: str) -> list:
    infos = await asyncio.to_thread(
        socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM
    )
    return [info[4][0] for info in infos]


def _public_ip(address) -> str:
    ip = ipaddress.ip_address(address)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if not ip.is_global:
        raise FetchError("host resolves to a non-public address")
    return str(ip)


async def fetch_source(
    client: httpx.AsyncClient,
    url: str,
    *,
    resolver=default_resolver,
    limits: dict,
    before_request=None,
) -> tuple:
    canonical = canonicalize_url(url)
    if canonical is None:
        raise FetchError("unsafe or unsupported url")
    max_redirects = int(limits["maximum_redirects"])
    max_bytes = int(limits["maximum_source_response_bytes"])
    timeout = float(limits["source_fetch_timeout_s"])
    current = canonical
    for _ in range(max_redirects + 1):
        if before_request is not None:
            await before_request(current)
        host = urlsplit(current).hostname
        try:
            resolved = await resolver(host)
        except FetchError:
            raise
        except Exception:
            raise FetchError("host resolution failed") from None
        pinned_ip = None
        for candidate in resolved or []:
            try:
                ip = _public_ip(candidate)
            except ValueError:
                raise FetchError("host resolution failed") from None
            if pinned_ip is None:
                pinned_ip = ip
        if pinned_ip is None:
            raise FetchError("host resolution failed")
        netloc = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        parts = urlsplit(current)
        pinned = urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
        redirect_to = None
        try:
            async with client.stream(
                "GET",
                pinned,
                headers={"Host": host, "Connection": "close"},
                extensions={"sni_hostname": host},
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code in REDIRECT_STATUSES:
                    redirect_to = response.headers.get("location")
                elif response.status_code != 200:
                    raise FetchError("unexpected response status")
                else:
                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise FetchError("response too large")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    return current, body.decode(
                        response.encoding or "utf-8", "replace"
                    )
        except httpx.HTTPError:
            raise FetchError("source fetch failed") from None
        if redirect_to is None:
            raise FetchError("redirect without location")
        nxt = canonicalize_url(urljoin(current, redirect_to))
        if nxt is None:
            raise FetchError("unsafe redirect")
        current = nxt
    raise FetchError("too many redirects")
