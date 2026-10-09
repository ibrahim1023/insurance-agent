import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from preflight.prices import extract_offer


def fetch_offer(book, slot):
    url = book.get(f"{slot}_url")
    if not url:
        return None, {"reason": "listing_url_not_supplied", "attempted": False}
    address = urlparse(url)
    if address.scheme != "https" or not address.hostname or address.username or address.password:
        return None, {"reason": "public_https_listing_required", "attempted": False}
    started = time.monotonic()
    observed = datetime.now(timezone.utc).isoformat()
    details = {"attempted": True, "url": url, "retrieved_at": observed}
    try:
        request = Request(url, headers={"User-Agent": "LibraryClaimPreflight/0.1"})
        with urlopen(request, timeout=20) as response:
            details["http_status"] = response.status
            details["url"] = response.url
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                details["reason"] = "listing_too_large"
                return None, details
            html = data.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        offer = extract_offer(html, details["url"], observed, book["title"], book["author"])
        if offer is None:
            details["reason"] = "no_unambiguous_author_matched_in_stock_AED_offer"
            return None, details
        expected_condition = "new" if slot == "replacement" else "used"
        if offer["condition"] != expected_condition:
            details["reason"] = "new_or_used_condition_not_supported"
            return None, details
        if book.get(f"{slot}_uae_delivery_confirmed") is not True:
            details["candidate_offer"] = offer
            details["reason"] = "UAE_delivery_requires_manual_listing_verification"
            return None, details
        return offer, details
    except HTTPError as error:
        details.update(http_status=error.code, reason="http_error")
        return None, details
    except (URLError, OSError, ValueError) as error:
        details["reason"] = type(error).__name__
        return None, details
    finally:
        details["elapsed_ms"] = (time.monotonic() - started) * 1000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    books = json.loads(Path(args.input).read_text())
    if not isinstance(books, list) or len(books) != 5:
        parser.error("provide exactly five book records")
    if any(not isinstance(book, dict) or not str(book.get("title", "")).strip() or not str(book.get("author", "")).strip() for book in books):
        parser.error("every book requires a title and author")
    rows = []
    attempts = 0
    for book in books:
        row = {"title": book["title"], "author": book["author"], "evidence": {}}
        for slot in ("replacement", "used"):
            row[slot], row["evidence"][slot] = fetch_offer(book, slot)
            attempts += int(row["evidence"][slot]["attempted"])
        rows.append(row)
    result = {
        "status": "MEASURED" if attempts else "BLOCKED",
        "discovery_mode": "supplied_listing_urls_only",
        "books": rows, "fetch_attempts": attempts,
        "replacement_found": sum(row["replacement"] is not None for row in rows),
        "used_found": sum(row["used"] is not None for row in rows),
        "both_found": sum(row["replacement"] is not None and row["used"] is not None for row in rows),
        "denominator": 5,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as file:
        json.dump(result, file, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
