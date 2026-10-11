import json

import httpx

from .price_sources import canonicalize_url
from .pricing import validate_discovery
from .sweeps import StoreError


async def discover_groq_listing_urls(
    client: httpx.AsyncClient, identities: list[dict], api_key: str, contract: dict
) -> dict:
    if not api_key:
        raise StoreError(503, "GROQ_API_KEY not configured")
    batch_size = int(contract["runtime_limits"]["maximum_identities_per_discovery_batch"])
    if not identities or len(identities) > batch_size:
        raise StoreError(422, "listing discovery batch limit exceeded")
    ref_ids = [identity["ref_id"] for identity in identities]
    if len(set(ref_ids)) != len(ref_ids):
        raise StoreError(422, "duplicate listing discovery reference")
    settings = contract["request_configuration"]
    payload = {
        "model": contract["model"],
        "messages": [
            {"role": "system", "content": contract["instructions"]},
            {"role": "user", "content": json.dumps(identities)},
        ],
        "tools": settings["tools"],
        "tool_choice": settings["tool_choice"],
        "reasoning_effort": settings["reasoning_effort"],
        "max_completion_tokens": settings["max_completion_tokens"],
        "stream": False,
    }
    try:
        response = await client.post(
            contract["endpoint"],
            headers={"Authorization": "Bearer " + api_key},
            json=payload,
            timeout=settings["timeout_s"],
        )
    except httpx.HTTPError:
        raise StoreError(502, "Groq listing discovery provider unavailable") from None
    if response.status_code == 429:
        retry = response.headers.get("retry-after", "")
        reason = "Groq listing discovery rate limit reached"
        if retry.isascii() and retry.isdecimal() and len(retry) <= 4 and int(retry) <= 3600:
            reason += f"; retry-after={int(retry)}"
        raise StoreError(429, reason)
    if response.status_code != 200:
        raise StoreError(502, "Groq listing discovery provider rejected request")
    try:
        raw = response.json()
    except ValueError:
        raise StoreError(502, "Groq listing discovery response malformed") from None
    if not isinstance(raw, dict) or raw.get("model") != contract["model"]:
        raise StoreError(502, "Groq listing discovery response malformed")
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise StoreError(502, "Groq listing discovery response malformed")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise StoreError(502, "Groq listing discovery response not completed")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("refusal"):
        raise StoreError(502, "Groq listing discovery provider refused")
    content = message.get("content")
    if not isinstance(content, str) or not isinstance(raw.get("id"), str):
        raise StoreError(502, "Groq listing discovery response malformed")
    try:
        proposed = json.loads(content)
    except ValueError:
        raise StoreError(502, "Groq listing discovery response malformed") from None
    records, issues = validate_discovery(proposed, ref_ids)
    candidate_urls = []
    for record in records:
        for url in record["replacement_urls"] + record["used_urls"]:
            canonical = canonicalize_url(url)
            if canonical is None:
                raise StoreError(502, "Groq listing discovery returned invalid URL")
            if canonical not in candidate_urls:
                candidate_urls.append(canonical)
    return {
        "provider": "groq",
        "model": raw["model"],
        "response_id": raw["id"],
        "raw_response": raw,
        "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
        "cost_usd_estimate": None,
        "candidate_urls": candidate_urls,
        "verified_source_urls": [],
        "issues": issues,
    }
