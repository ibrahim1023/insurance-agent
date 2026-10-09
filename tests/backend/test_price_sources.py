import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from backend.price_sources import (
    FetchError,
    canonicalize_url,
    extract_source_quote,
    fetch_source,
)

NOW = "2026-10-08T12:00:00+00:00"
URL = "https://books.example/listing/clean-code"
TITLE = "Clean Code"
AUTHOR = "Robert C. Martin"
LIMITS = {
    "maximum_redirects": 3,
    "maximum_source_response_bytes": 2097152,
    "source_fetch_timeout_s": 10,
}


def page(entry):
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(entry)
        + "</script></head><body>synthetic fixture listing</body></html>"
    )


def offer(price, currency, condition, region="US", **extra):
    node = {
        "@type": "Offer",
        "price": price,
        "priceCurrency": currency,
        "availability": "https://schema.org/InStock",
        "itemCondition": f"https://schema.org/{condition}",
        "url": URL,
    }
    if region is not None:
        node["eligibleRegion"] = region
    node.update(extra)
    return node


def us_listing(offers, **entry_over):
    entry = {
        "@context": "https://schema.org",
        "@type": "Book",
        "name": TITLE,
        "author": {"@type": "Person", "name": AUTHOR},
        "url": URL,
        "bookFormat": "https://schema.org/Paperback",
        "offers": offers,
    }
    entry.update(entry_over)
    return page(entry)


NEW_OFFER = offer(10, "USD", "NewCondition")
USED_OFFER = offer(4, "USD", "UsedCondition")
BOTH = us_listing([NEW_OFFER, USED_OFFER])


def test_synthetic_us_listing_gives_independent_new_and_used_amounts():
    new = extract_source_quote(BOTH, URL, NOW, TITLE, AUTHOR, "new")
    used = extract_source_quote(BOTH, URL, NOW, TITLE, AUTHOR, "used")
    assert new["amount"] == Decimal(10)
    assert new["currency"] == "USD"
    assert new["market_country"] == "US"
    assert new["condition"] == "new"
    assert used["amount"] == Decimal(4)
    assert used["condition"] == "used"
    assert new["url"] == URL


def test_country_object_market_and_seller_address_market_both_prove():
    regioned = us_listing(
        [offer(10, "USD", "NewCondition", {"@type": "Country", "identifier": "US"})]
    )
    sold = us_listing(
        [offer(10, "USD", "NewCondition", None, seller={"@type": "Organization", "address": {"@type": "PostalAddress", "addressCountry": "US"}})]
    )
    assert extract_source_quote(regioned, URL, NOW, TITLE, AUTHOR, "new")["market_country"] == "US"
    quote = extract_source_quote(sold, URL, NOW, TITLE, AUTHOR, "new")
    assert quote["market_country"] == "US"
    assert quote["market_evidence"] == "seller_address_country"


def test_webpage_mainentity_is_admitted_but_nested_recommendation_is_not():
    wrapped = page(
        {
            "@type": "WebPage",
            "mainEntity": {
                "@type": "Book",
                "name": TITLE,
                "author": {"@type": "Person", "name": AUTHOR},
                "url": URL,
                "bookFormat": "Paperback",
                "offers": NEW_OFFER,
            },
        }
    )
    assert extract_source_quote(wrapped, URL, NOW, TITLE, AUTHOR, "new")["amount"] == Decimal(10)
    nested = page(
        {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "Book",
                    "name": TITLE,
                    "author": {"@type": "Person", "name": AUTHOR},
                    "url": URL,
                    "bookFormat": "Paperback",
                    "offers": NEW_OFFER,
                }
            ],
        }
    )
    assert extract_source_quote(nested, URL, NOW, TITLE, AUTHOR, "new") is None


@pytest.mark.parametrize(
    "html",
    [
        us_listing([NEW_OFFER], name="Other Title"),
        us_listing([NEW_OFFER], author={"@type": "Person", "name": "Someone Else"}),
        us_listing([NEW_OFFER], bookFormat="https://schema.org/EBook"),
        us_listing([NEW_OFFER], bookFormat="AudiobookFormat"),
        us_listing([NEW_OFFER], bookFormat=""),
        us_listing([offer(10, "USD", "NewCondition", availability="https://schema.org/OutOfStock")]),
        us_listing([dict(NEW_OFFER, itemCondition="https://schema.org/RefurbishedCondition")]),
        us_listing([dict(NEW_OFFER, itemCondition=None)]),
        us_listing([offer("$10.00", "USD", "NewCondition")]),
        us_listing([offer("1e1", "USD", "NewCondition")]),
        us_listing([offer(True, "USD", "NewCondition")]),
        us_listing([offer(0, "USD", "NewCondition")]),
        us_listing([offer(10, "USD", "NewCondition", None)]),
        us_listing([offer(10, "USD", "NewCondition", "US", seller={"@type": "Organization", "address": {"addressCountry": "GB"}})]),
        us_listing([NEW_OFFER, dict(NEW_OFFER)]),
        us_listing([dict(NEW_OFFER, **{"@type": "AggregateOffer"})]),
        us_listing([NEW_OFFER], url="https://other.example/listing/clean-code"),
        us_listing([dict(NEW_OFFER, url="https://other.example/x")]),
    ],
    ids=[
        "wrong_title",
        "wrong_author",
        "ebook",
        "audio",
        "unknown_format",
        "sold_out",
        "refurbished_condition",
        "unknown_condition",
        "ambiguous_dollar",
        "exponent_price",
        "bool_price",
        "zero_price",
        "missing_market",
        "conflicting_market",
        "multiple_offers",
        "aggregate_offer",
        "entry_url_mismatch",
        "offer_url_mismatch",
    ],
)
def test_synthetic_listing_rejections(html):
    assert extract_source_quote(html, URL, NOW, TITLE, AUTHOR, "new") is None
    assert extract_source_quote(html, URL, NOW, TITLE, AUTHOR, "used") is None


def test_used_is_never_derived_from_a_new_offer():
    assert extract_source_quote(us_listing([NEW_OFFER]), URL, NOW, TITLE, AUTHOR, "used") is None


AMAZON_URL = "https://www.amazon.ae/Clean-Code-Handbook-Software-Craftsmanship/dp/B001GSTOAM"

AMAZON_PAGE = """<html><body>
<div id="centerCol">
<h1><span id="productTitle">Clean Code - Paperback</span></h1>
<span id="bylineInfo">by Robert C. Martin (Author) Format: Paperback</span>
<div id="gsod_singleOfferDisplay_Desktop" data-csa-c-asin="B001GSTOAM">
<input name="ASIN" value="B001GSTOAM">
<input name="offerListingID" value="fixtureOffer1">
<span id="availability">In Stock.</span>
<span id="apex-pricetopay-accessibility-label">AED 176.98</span>
<span class="twister-plus-buying-options-price-data">{"g1":[{"offerListingId":"fixtureOffer1","buyingOptionType":"NEW","currencySymbol":"AED","locale":"en-AE","priceAmount":176.98,"displayPrice":"AED 176.98"}]}</span>
</div>
</div>
</body></html>"""


def test_synthetic_amazon_uae_selected_offer_supports_new_only():
    quote = extract_source_quote(AMAZON_PAGE, AMAZON_URL, NOW, TITLE, AUTHOR, "new")
    assert quote["amount"] == Decimal("176.98")
    assert quote["currency"] == "AED"
    assert quote["market_country"] == "AE"
    assert quote["market_evidence"] == "amazon_uae"
    assert quote["listing_format"] == "Paperback"
    assert extract_source_quote(AMAZON_PAGE, AMAZON_URL, NOW, TITLE, AUTHOR, "used") is None


def test_other_amazon_layout_or_domain_unsupported():
    assert extract_source_quote("<html><body>different layout</body></html>", AMAZON_URL, NOW, TITLE, AUTHOR, "new") is None
    us_url = "https://www.amazon.com/Clean-Code/dp/B001GSTOAM"
    assert extract_source_quote(AMAZON_PAGE, us_url, NOW, TITLE, AUTHOR, "new") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://books.example/x",
        "https://user:pw@books.example/x",
        "https://books.example:8443/x",
        "https://books.example\\evil/x",
        "ftp://books.example/x",
        "https:///x",
        "not a url",
    ],
)
def test_canonicalize_rejects_dangerous_urls(url):
    assert canonicalize_url(url) is None


def test_canonicalize_normalizes_and_strips_fragment():
    assert (
        canonicalize_url("HTTPS://BOOKS.Example./x?y=1#frag") is None
    )
    assert canonicalize_url("https://BOOKS.example/x?y=1#frag") == "https://books.example/x?y=1"
    assert canonicalize_url("https://books.example:443/x") == "https://books.example/x"


def resolve_global(host):
    async def call(host_arg):
        return ["93.184.216.34"]

    return call


async def _global_resolver(host):
    return ["93.184.216.34"]


def serve(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_serves_public_https_page():
    async def scenario():
        seen = []

        def handler(request):
            seen.append(request)
            assert request.headers["host"] == "books.example"
            return httpx.Response(200, text="synthetic page")

        async with serve(handler) as client:
            final, html = await fetch_source(
                client, URL + "#frag", resolver=_global_resolver, limits=LIMITS
            )
            assert final == URL
            assert html == "synthetic page"
            assert str(seen[0].url) == "https://93.184.216.34/listing/clean-code"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "addresses",
    [["10.0.0.1"], ["127.0.0.1"], ["169.254.1.1"], ["::1"], ["93.184.216.34", "10.0.0.1"], []],
    ids=["private", "loopback", "linklocal", "v6_loopback", "mixed", "empty"],
)
def test_fetch_rejects_nonpublic_resolution(addresses):
    async def scenario():
        async def resolver(host):
            return addresses

        def handler(request):
            raise AssertionError("request must not leave the resolver guard")

        async with serve(handler) as client:
            with pytest.raises(FetchError):
                await fetch_source(client, URL, resolver=resolver, limits=LIMITS)

    asyncio.run(scenario())


def test_fetch_rejects_public_to_private_redirect_and_http_redirect():
    async def scenario():
        calls = []

        async def resolver(host):
            calls.append(host)
            return ["10.0.0.1"] if host == "internal.lan" else ["93.184.216.34"]

        def handler(request):
            return httpx.Response(302, headers={"location": "https://internal.lan/x"})

        async with serve(handler) as client:
            with pytest.raises(FetchError):
                await fetch_source(client, URL, resolver=resolver, limits=LIMITS)

        def handler2(request):
            return httpx.Response(301, headers={"location": "http://books.example/x"})

        async with serve(handler2) as client:
            with pytest.raises(FetchError):
                await fetch_source(client, URL, resolver=_global_resolver, limits=LIMITS)

    asyncio.run(scenario())


def test_fetch_redirect_limit_and_oversize_and_status():
    async def scenario():
        def loop(request):
            return httpx.Response(302, headers={"location": URL})

        async with serve(loop) as client:
            with pytest.raises(FetchError):
                await fetch_source(client, URL, resolver=_global_resolver, limits=LIMITS)

        big = {"maximum_source_response_bytes": 64, "maximum_redirects": 3, "source_fetch_timeout_s": 10}

        def huge(request):
            return httpx.Response(200, content=b"x" * 100)

        async with serve(huge) as client:
            with pytest.raises(FetchError):
                await fetch_source(client, URL, resolver=_global_resolver, limits=big)

        def missing(request):
            return httpx.Response(404)

        async with serve(missing) as client:
            with pytest.raises(FetchError):
                await fetch_source(client, URL, resolver=_global_resolver, limits=LIMITS)

    asyncio.run(scenario())


def test_fetch_follows_valid_redirect_chain():
    async def scenario():
        hops = []

        def handler(request):
            hops.append(request.headers["host"])
            if request.headers["host"] == "books.example":
                return httpx.Response(
                    301, headers={"location": "https://shop.example/final"}
                )
            return httpx.Response(200, text="landed")

        async with serve(handler) as client:
            final, html = await fetch_source(
                client, URL, resolver=_global_resolver, limits=LIMITS
            )
            assert final == "https://shop.example/final"
            assert html == "landed"
            assert hops == ["books.example", "shop.example"]

    asyncio.run(scenario())
