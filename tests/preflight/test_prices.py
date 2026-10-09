import importlib
import json

import pytest


def product_html(**changes):
    product = {
        "@type": "Book",
        "name": "The Sample Book",
        "author": {"name": "Example Author"},
        "offers": {
            "@type": "Offer",
            "price": "89.00",
            "priceCurrency": "AED",
            "availability": "https://schema.org/InStock",
            "itemCondition": "https://schema.org/NewCondition",
        },
    }
    product.update(changes)
    return '<script type="application/ld+json">' + json.dumps(product) + '</script>'


def extract(html):
    module = importlib.import_module("preflight.prices")
    return module.extract_offer(
        html, "https://retailer.example/books/sample", "2026-10-06T12:00:00Z",
        "The Sample Book", "Example Author",
    )


def test_valid_aed_offer():
    result = extract(product_html())
    assert result["amount"] == "89.00"
    assert result["currency"] == "AED"
    assert result["url"] == "https://retailer.example/books/sample"
    assert result["retrieved_at"] == "2026-10-06T12:00:00Z"
    assert result["condition"] == "new"


def test_mismatched_title_is_missing():
    assert extract(product_html(name="Another Book")) is None


def test_mismatched_author_is_missing():
    assert extract(product_html(author={"name": "Another Author"})) is None


def test_missing_author_is_missing():
    assert extract(product_html(author=None)) is None


@pytest.mark.parametrize("currency", ["USD", "", None])
def test_foreign_or_missing_currency_is_missing(currency):
    html = product_html(offers={
        "price": "89", "priceCurrency": currency,
        "availability": "https://schema.org/InStock",
    })
    assert extract(html) is None


def test_ambiguous_variant_is_missing():
    assert extract(product_html(offers=[
        {"price": "89", "priceCurrency": "AED"},
        {"price": "110", "priceCurrency": "AED"},
    ])) is None


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", "0", "not a price"])
def test_invalid_price_is_missing(price):
    html = product_html(offers={
        "price": price, "priceCurrency": "AED",
        "availability": "https://schema.org/InStock",
    })
    assert extract(html) is None


def test_out_of_stock_is_missing():
    html = product_html(offers={
        "price": "89", "priceCurrency": "AED",
        "availability": "https://schema.org/OutOfStock",
    })
    assert extract(html) is None


def test_malformed_json_is_missing():
    assert extract('<script type="application/ld+json">{broken</script>') is None


@pytest.mark.parametrize("retrieved_at", ["", "yesterday", "2026-10-06T12:00:00"])
def test_missing_or_unzoned_retrieval_date_is_missing(retrieved_at):
    result = importlib.import_module("preflight.prices").extract_offer(
        product_html(), "https://retailer.example/books/sample", retrieved_at,
        "The Sample Book", "Example Author",
    )
    assert result is None
