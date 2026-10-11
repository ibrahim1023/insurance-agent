import asyncio
import json

import httpx
import pytest

from backend.groq_discovery import discover_groq_listing_urls
from backend.server import GROQ_PRICING_CONTRACT
from backend.sweeps import StoreError


LISTING_URL = "https://books.example/listing/clean-code"
BOOK = {
    "ref_id": "book_" + "b" * 32,
    "title": "Clean Code",
    "author": "Robert C. Martin",
    "edition": "",
    "isbn": "",
    "market_country": "AE",
    "output_currency": "USD",
}


def test_groq_discovery_mock_transport_rejects_prose_urls():
    requests = []
    raw = {
        "id": "groq_search_fixture",
        "model": "openai/gpt-oss-20b",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "records": [{
                        "ref_id": BOOK["ref_id"],
                        "replacement_urls": [LISTING_URL],
                        "used_urls": [],
                        "notes": [],
                    }],
                    "issues": [],
                }),
                "executed_tools": [{"search_results": [{"url": LISTING_URL}]}],
            },
        }],
        "usage": {"prompt_tokens": 75, "completion_tokens": 65, "total_tokens": 140},
    }

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=raw)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False) as client:
            return await discover_groq_listing_urls(
                client, [BOOK], "groq-test-only-token", GROQ_PRICING_CONTRACT
            )

    result = asyncio.run(scenario())
    assert [request.url.host for request in requests] == ["api.groq.com"]
    payload = json.loads(requests[0].content)
    assert payload["model"] == "openai/gpt-oss-20b"
    assert payload["tools"] == [{"type": "browser_search"}]
    assert payload["tool_choice"] == "required"
    assert "response_format" not in payload
    assert "text" not in payload
    assert payload["messages"][1]["content"] == json.dumps([BOOK])
    assert result["candidate_urls"] == [LISTING_URL]
    assert result["verified_source_urls"] == []
    assert result["raw_response"]["id"] == "groq_search_fixture"
    assert result["usage"]["prompt_tokens"] == 75
    assert result["cost_usd_estimate"] is None


@pytest.mark.parametrize("variant,status_code", [
    ("wrong_ref", 502),
    ("malformed", 502),
    ("quota", 429),
])
def test_groq_discovery_rejects_unsupported_evidence(variant, status_code):
    records = [{
        "ref_id": "book_" + "c" * 32 if variant == "wrong_ref" else BOOK["ref_id"],
        "replacement_urls": [LISTING_URL],
        "used_urls": [],
        "notes": [],
    }]
    raw = {
        "id": "groq_search_bad",
        "model": "openai/gpt-oss-20b",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "not-json" if variant == "malformed" else json.dumps({"records": records, "issues": []})},
        }],
    }

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    429 if variant == "quota" else 200,
                    headers={"retry-after": "12"} if variant == "quota" else {},
                    json=raw,
                )
            ),
            trust_env=False,
        ) as client:
            return await discover_groq_listing_urls(
                client, [BOOK], "groq-test-only-token", GROQ_PRICING_CONTRACT
            )

    with pytest.raises(StoreError) as failure:
        asyncio.run(scenario())
    assert failure.value.status_code == status_code
    if variant == "quota":
        assert "retry-after=12" in failure.value.detail
