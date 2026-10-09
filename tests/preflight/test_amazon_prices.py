import json
from html import escape

import pytest

from preflight.prices import extract_offer

URL = "https://www.amazon.ae/Capital-Twenty-First-Century-Piketty-Thomas/dp/067443000X"
DATE = "2026-10-06T16:58:04+00:00"


def page(title="Capital In The Twenty-First Century By Piketty, Thomas - Hardcover",
         byline="by Thomas Piketty (Author), Arthur Goldhammer (Author, Translator) Format: Hardcover",
         stock="Only 4 left in stock - order soon.", asin="067443000X", input_asin="067443000X",
         price="176.98", display="AED 176.98", currency="AED", locale="en-AE",
         condition="NEW", offer="fixture-offer", row_offer="fixture-offer", extra_row=None):
    rows = [{"priceAmount": price, "displayPrice": display, "currencySymbol": currency,
             "locale": locale, "buyingOptionType": condition, "offerListingId": row_offer}]
    if extra_row is not None:
        rows.append(extra_row)
    return f'''<html><body>
<span id="productTitle">{escape(title)}</span>
<div id="bylineInfo">{escape(byline)}</div>
<div id="desktop_qualifiedBuyBox" data-csa-c-asin="{escape(asin)}">
<input type="hidden" name="items[0.base][asin]" value="{escape(input_asin)}">
<input type="hidden" name="items[0.base][offerListingId]" value="{escape(offer)}">
<span id="apex-pricetopay-accessibility-label">{escape(display)}</span>
<div id="availability"><span>{escape(stock)}</span><br></div>
</div>
<div class="a-section aok-hidden twister-plus-buying-options-price-data">{escape(json.dumps({"desktop_buybox_group_1": rows}))}</div>
<div id="recommendations">Other Book <span>AED 1.00</span></div>
</body></html>'''


def quote(html, url=URL, title="Capital in the Twenty-First Century", author="Thomas Piketty"):
    return extract_offer(html, url, DATE, title, author)


def test_selected_amazon_offer_without_jsonld():
    result = quote(page())
    assert result is not None
    assert result["amount"] == "176.98"
    assert result["currency"] == "AED"
    assert result["condition"] == "new"
    assert result["availability"] == "InStock"
    assert result["listing_format"] == "Hardcover"
    assert result["listing_id"] == "067443000X"
    assert result["extraction_method"] == "amazon_selected_offer"
    assert result["url"] == URL
    assert result["retrieved_at"] == DATE


@pytest.mark.parametrize("changes", [
    {"stock": "Currently unavailable."}, {"stock": "Not in stock"},
    {"stock": "Only 0 left in stock - order soon."}, {"stock": "Pre-order now"},
    {"asin": "B000000001"}, {"input_asin": "B000000001"},
    {"row_offer": "other-offer"}, {"currency": "USD"}, {"locale": "en-US"},
    {"condition": "USED"}, {"condition": ""}, {"price": "0"},
    {"price": "NaN"}, {"price": "-10"}, {"price": "175.00"},
    {"byline": "by Another Author (Author) Format: Hardcover"},
    {"byline": "by Thomas Piketty (Author) Format: Kindle Edition"},
    {"title": "Capital and Ideology By Piketty, Thomas - Hardcover"},
    {"title": "Capital In The Twenty-First Century By Piketty, Thomas - Paperback"},
    {"display": "USD 176.98"},
    {"extra_row": {"offerListingId": "fixture-offer", "priceAmount": "100.00"}},
])
def test_unsupported_or_conflicting_offer_is_missing(changes):
    assert quote(page(**changes)) is None


def test_unknown_primary_author_is_not_rescued_by_recommendations():
    html = page(byline="by Someone Else (Author) Format: Hardcover")
    html = html.replace("Other Book", "Thomas Piketty Capital in the Twenty-First Century")
    assert quote(html) is None


def gsod_page(nested_asin="067443000X"):
    outer = '<div id="desktop_qualifiedBuyBox" data-csa-c-asin="067443000X">'
    html = page().replace(outer, '<div id="gsod_singleOfferDisplay_Desktop" data-csa-c-asin="067443000X">')
    html = html.replace('name="items[0.base][asin]"', 'name="ASIN"').replace('name="items[0.base][offerListingId]"', 'name="offerListingID"')
    html = html.replace('<span id="apex-pricetopay-accessibility-label">', f'<div id="desktop_qualifiedBuyBox" data-csa-c-asin="{nested_asin}"><span id="apex-pricetopay-accessibility-label">')
    return html.replace('</div>\n<div class="a-section', '</div></div>\n<div class="a-section')


def test_selected_inputs_before_nested_buybox_are_supported():
    result = quote(gsod_page())
    assert result is not None
    assert result["amount"] == "176.98"


def test_nested_buybox_for_other_product_is_rejected():
    assert quote(gsod_page(nested_asin="B000000001")) is None


def test_matching_text_on_spoofed_domain_is_missing():
    assert quote(page(), url="https://www.amazon.ae.evil.example/dp/067443000X") is None


@pytest.mark.parametrize("name", ["items[1.base][asin]", "items[1.base][offerListingId]", "items[42.base][quantity]"])
def test_additional_bundle_items_are_rejected(name):
    html = page().replace('<span id="apex-pricetopay-accessibility-label">', f'<input name="{name}" value="other-item"><span id="apex-pricetopay-accessibility-label">')
    assert quote(html) is None


def test_missing_buybox_price_does_not_use_related_price():
    html = page().replace('<span id="apex-pricetopay-accessibility-label">AED 176.98</span>', "")
    assert quote(html) is None


def test_workweek_spacing_and_subtitle_are_accepted_with_verified_byline():
    html = page(title="The 4-Hour Work Week: Escape The 9-5, Live Anywhere And Join The New Rich",
                byline="by Timothy Ferriss (Author) Format: Paperback", asin="0091929113",
                input_asin="0091929113", price="56.00", display="AED 56.00", stock="In Stock")
    result = quote(html, url="https://www.amazon.ae/4-Hour-Work-Week-Escape-Anywhere/dp/0091929113",
                   title="The 4-Hour Workweek", author="Timothy Ferriss")
    assert result is not None
    assert result["listing_format"] == "Paperback"
    assert result["amount"] == "56.00"


def test_arabic_byline_without_corroboration_is_missing():
    html = page(title="The 4-Hour Work Week: Escape The 9-5, Live Anywhere And Join The New Rich",
                byline="by تيموثي فيريس (Author) Format: Paperback", asin="0091929113",
                input_asin="0091929113", price="56.00", display="AED 56.00")
    assert quote(html, url="https://www.amazon.ae/4-Hour-Work-Week-Escape-Anywhere/dp/0091929113",
                 title="The 4-Hour Workweek", author="Timothy Ferriss") is None


@pytest.mark.parametrize("title,display_title,author,asin,amount", [
    ("How to Talk to Anyone", "How To Talk To Anyone By Leil Lowndes - Paperback", "Leil Lowndes", "0722538073", "41.57"),
    ("Think and Grow Rich", "Think and Grow Rich by Napoleon Hill - Paperback", "Napoleon Hill", "817234564X", "10.38"),
])
def test_other_captured_offer_shapes(title, display_title, author, asin, amount):
    html = page(title=display_title, byline=f"by {author} (Author) Format: Paperback",
                asin=asin, input_asin=asin, price=amount, display=f"AED {amount}", stock="In Stock")
    result = quote(html, url=f"https://www.amazon.ae/dp/{asin}", title=title, author=author)
    assert result is not None
    assert result["amount"] == amount
    assert result["listing_id"] == asin
