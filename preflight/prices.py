import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlparse


class ListingData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.parts = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.parts = []

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.parts is not None:
            self.blocks.append("".join(self.parts))
            self.parts = None


def normalized(value):
    return " ".join(re.findall(r"\w+", str(value).casefold()))


def records(value):
    if isinstance(value, list):
        for entry in value:
            yield from records(entry)
    elif isinstance(value, dict):
        yield value
        for entry in value.values():
            if isinstance(entry, (dict, list)):
                yield from records(entry)


def author_names(value):
    if isinstance(value, str):
        return [normalized(value)]
    if isinstance(value, dict):
        return [normalized(value.get("name", ""))]
    if isinstance(value, list):
        return [name for entry in value for name in author_names(entry)]
    return []


class AmazonPage(HTMLParser):
    void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    field_ids = {"productTitle": "title", "bylineInfo": "byline", "availability": "stock", "apex-pricetopay-accessibility-label": "price"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.fields = {}
        self.boxes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        box = next((node["box"] for node in reversed(self.stack) if node["box"] is not None), None)
        box_id = attrs.get("id")
        if box_id == "gsod_singleOfferDisplay_Desktop" or (box_id == "desktop_qualifiedBuyBox" and box is None):
            box = {"asin": attrs.get("data-csa-c-asin"), "asins": [], "offers": [], "stock": [], "price": [], "invalid": False}
            self.boxes.append(box)
        elif box_id == "desktop_qualifiedBuyBox" and attrs.get("data-csa-c-asin") != box["asin"]:
            box["invalid"] = True
        if tag == "input" and box is not None:
            name = attrs.get("name") or ""
            if name.startswith("items[") and not name.startswith("items[0.base]["):
                box["invalid"] = True
            key = {"items[0.base][asin]": "asins", "ASIN": "asins", "items[0.base][offerListingId]": "offers", "offerListingID": "offers"}.get(name)
            if key:
                box[key].append(attrs.get("value", ""))
        field = self.field_ids.get(attrs.get("id"))
        if "twister-plus-buying-options-price-data" in (attrs.get("class") or "").split():
            field = "data"
        if tag not in self.void_tags:
            self.stack.append({"tag": tag, "field": field, "parts": [], "box": box})

    def handle_data(self, data):
        if any(node["tag"] in {"script", "style"} for node in self.stack):
            return
        for node in self.stack:
            if node["field"]:
                node["parts"].append(data)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if index is None:
            return
        nodes, self.stack = self.stack[index:], self.stack[:index]
        for node in nodes:
            field = node["field"]
            if not field:
                continue
            text = "".join(node["parts"])
            if field in {"stock", "price"}:
                if node["box"] is not None:
                    node["box"][field].append(" ".join(text.split()))
            else:
                self.fields.setdefault(field, []).append(text)


def amazon_offer(html, page_url, retrieved_at, expected_title, expected_author):
    asin_match = re.search(r"/dp/([A-Za-z0-9]{10})(?:/|$)", urlparse(page_url).path)
    if not asin_match:
        return None
    asin = asin_match.group(1)
    parser = AmazonPage()
    parser.feed(html)
    titles, bylines = parser.fields.get("title", []), parser.fields.get("byline", [])
    if len(titles) != 1 or len(bylines) != 1 or len(parser.boxes) != 1:
        return None
    title, byline, box = titles[0].strip(), " ".join(bylines[0].split()), parser.boxes[0]
    offer_ids = set(box["offers"])
    if box["invalid"] or box["asin"] != asin or set(box["asins"]) != {asin} or len(offer_ids) != 1 or not next(iter(offer_ids)):
        return None
    selected_offer = next(iter(offer_ids))
    binding = re.search(r"\bFormat:\s*(Hardcover|Paperback|Hardback|Softcover)\s*$", byline, re.I)
    if not binding or not re.search(r"\b" + re.escape(normalized(expected_author)) + r"\b", normalized(byline)):
        return None
    suffix = re.search(r"\s*-\s*(Hardcover|Paperback|Hardback|Softcover)\s*$", title, re.I)
    if suffix and suffix.group(1).casefold() != binding.group(1).casefold():
        return None
    base = title[:suffix.start()] if suffix else title
    base = re.split(r"\s+by\s+", base, maxsplit=1, flags=re.I)[0]
    if ":" not in expected_title:
        base = base.split(":", 1)[0]
    if normalized(base).replace(" ", "") != normalized(expected_title).replace(" ", ""):
        return None
    if len(box["stock"]) != 1 or len(box["price"]) != 1:
        return None
    stock = box["stock"][0].casefold().rstrip(".")
    count = re.fullmatch(r"only (\d+) left in stock(?:\s*-\s*order soon)?", stock)
    if stock != "in stock" and not (count and int(count.group(1)) > 0):
        return None
    candidates = []
    for block in parser.fields.get("data", []):
        try:
            groups = json.loads(block, parse_float=Decimal)
        except (ValueError, TypeError):
            continue
        if not isinstance(groups, dict):
            continue
        for rows in groups.values():
            if isinstance(rows, list):
                candidates.extend(row for row in rows if isinstance(row, dict) and row.get("offerListingId") == selected_offer)
    if len(candidates) != 1:
        return None
    offer = candidates[0]
    if offer.get("buyingOptionType") != "NEW" or offer.get("currencySymbol") != "AED" or offer.get("locale") not in {"en-AE", "ar-AE"}:
        return None
    try:
        amount = Decimal(str(offer.get("priceAmount")))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            return None
        displays = [offer.get("displayPrice"), box["price"][0]]
        for display in displays:
            match = re.fullmatch(r"AED\s+([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)", str(display).strip())
            if not match or Decimal(match.group(1).replace(",", "")) != amount:
                return None
    except (InvalidOperation, ValueError, TypeError):
        return None
    return {
        "amount": format(amount, ".2f"), "currency": "AED", "source": urlparse(page_url).hostname,
        "url": page_url, "retrieved_at": retrieved_at, "availability": "InStock", "condition": "new",
        "listing_id": asin, "listing_format": binding.group(1).title(), "extraction_method": "amazon_selected_offer",
        "source_evidence": {"title": title, "byline": byline, "price": box["price"][0], "availability": box["stock"][0], "buying_option_type": "NEW"},
    }


def extract_offer(html: str, page_url: str, retrieved_at: str, expected_title: str, expected_author: str) -> dict | None:
    source = urlparse(page_url)
    if source.scheme != "https" or not source.hostname or not expected_title.strip() or not expected_author.strip():
        return None
    try:
        observed = datetime.fromisoformat(retrieved_at)
    except (ValueError, TypeError):
        return None
    if observed.tzinfo is None or observed.utcoffset() is None:
        return None
    if source.hostname in {"amazon.ae", "www.amazon.ae"}:
        return amazon_offer(html, page_url, retrieved_at, expected_title, expected_author)
    parser = ListingData()
    parser.feed(html)
    matches = []
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        for entry in records(data):
            kind = entry.get("@type", [])
            kinds = [kind] if isinstance(kind, str) else kind
            if not isinstance(kinds, list) or not any(t in ("Book", "Product") for t in kinds):
                continue
            if normalized(entry.get("name", "")) != normalized(expected_title):
                continue
            if normalized(expected_author) not in author_names(entry.get("author")):
                continue
            matches.append(entry)
    if len(matches) != 1:
        return None
    offer = matches[0].get("offers")
    if isinstance(offer, list):
        if len(offer) != 1:
            return None
        offer = offer[0]
    if not isinstance(offer, dict) or offer.get("@type", "Offer") != "Offer":
        return None
    availability = str(offer.get("availability", "")).rsplit("/", 1)[-1]
    if offer.get("priceCurrency") != "AED" or availability not in ("InStock", "LimitedAvailability"):
        return None
    try:
        amount = Decimal(str(offer.get("price")))
        if not amount.is_finite() or amount <= 0:
            return None
        amount_text = format(amount.quantize(Decimal("0.01")), "f")
    except (InvalidOperation, ValueError, TypeError):
        return None
    condition = str(offer.get("itemCondition", matches[0].get("itemCondition", ""))).rsplit("/", 1)[-1]
    return {
        "amount": amount_text,
        "currency": "AED",
        "source": source.hostname,
        "url": page_url,
        "retrieved_at": retrieved_at,
        "availability": availability,
        "condition": {"NewCondition": "new", "UsedCondition": "used"}.get(condition, ""),
    }
