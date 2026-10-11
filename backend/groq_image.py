import base64
import copy
import json

import cv2
import httpx
import numpy as np
from pydantic import ValidationError

from preflight.vision import SceneProposal, VisionError

from .sweeps import StoreError


def _rate_limit_reason(prefix: str, headers: httpx.Headers) -> str:
    reason = prefix
    for header, label, limit in (
        ("retry-after", "retry-after", 3600),
        ("x-ratelimit-remaining-requests", "remaining-requests", 1_000_000),
        ("x-ratelimit-remaining-tokens", "remaining-tokens", 1_000_000_000),
    ):
        value = headers.get(header, "")
        if value.isascii() and value.isdecimal() and len(value) <= 10 and int(value) <= limit:
            reason += f"; {label}={int(value)}"
    return reason


def _groq_schema_format(contract_format: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {key: value for key, value in contract_format.items() if key != "type"},
    }


async def observe_groq_jpeg(client: httpx.AsyncClient, jpeg: bytes, api_key: str, contract: dict) -> dict:
    if not api_key:
        raise VisionError("vision provider unavailable")
    if not jpeg.startswith(b"\xff\xd8\xff"):
        raise VisionError("invalid JPEG evidence")
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise VisionError("invalid JPEG evidence")
    height, width = image.shape[:2]
    maximum = int(contract["runtime_limits"]["maximum_image_long_edge_px"])
    sent_width, sent_height = width, height
    if max(width, height) > maximum:
        scale = maximum / max(width, height)
        sent_width, sent_height = max(1, round(width * scale)), max(1, round(height * scale))
        image = cv2.resize(image, (sent_width, sent_height), interpolation=cv2.INTER_AREA)
        encoded, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not encoded:
            raise VisionError("could not encode vision evidence")
        jpeg = buffer.tobytes()
    response_format = copy.deepcopy(contract["response_format"])
    properties = response_format["schema"]["$defs"]["box"]["properties"]
    for key in ("left", "right"):
        properties[key]["maximum"] = sent_width
    for key in ("top", "bottom"):
        properties[key]["maximum"] = sent_height
    settings = contract["request_configuration"]
    payload = {
        "model": contract["model"],
        "messages": [
            {"role": "system", "content": contract["instructions"]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"The supplied image is {sent_width} pixels wide and {sent_height} pixels tall. "
                            "Return absolute pixel positions for visible objects; do not normalize coordinates."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")},
                    },
                ],
            },
        ],
        "reasoning_effort": settings["reasoning_effort"],
        "max_completion_tokens": settings["max_completion_tokens"],
        "stream": False,
        "response_format": _groq_schema_format(response_format),
    }
    try:
        response = await client.post(
            contract["endpoint"],
            headers={"Authorization": "Bearer " + api_key},
            json=payload,
            timeout=settings["request_timeout_s"],
        )
    except httpx.HTTPError:
        raise VisionError("vision provider unavailable") from None
    if response.status_code == 429:
        raise VisionError(_rate_limit_reason("vision provider rate limit reached", response.headers))
    if response.status_code != 200:
        raise VisionError(f"vision provider rejected request ({response.status_code})")
    try:
        raw = response.json()
    except ValueError:
        raise VisionError("invalid vision provider response") from None
    if not isinstance(raw, dict) or raw.get("model") != contract["model"]:
        raise VisionError("invalid vision provider response")
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise VisionError("invalid vision provider response")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise VisionError("vision response not completed")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("refusal"):
        raise VisionError("vision provider refused observation")
    text = message.get("content")
    if not isinstance(text, str) or not isinstance(raw.get("id"), str):
        raise VisionError("invalid vision provider response")
    try:
        scene = SceneProposal.model_validate_json(text).model_dump()
    except (ValidationError, ValueError):
        raise VisionError("invalid scene response") from None
    return {
        "scene": scene,
        "raw_response": raw,
        "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
        "model": raw["model"],
        "response_id": raw["id"],
        "cost_usd_estimate": None,
        "image_width": width,
        "image_height": height,
        "sent_image_width": sent_width,
        "sent_image_height": sent_height,
        "coordinate_system": "pixels",
        "provider": "groq",
    }


async def read_groq_crops(
    client: httpx.AsyncClient, crops: list[dict], api_key: str, contract: dict
) -> dict:
    if not api_key:
        raise StoreError(503, "GROQ_API_KEY not configured")
    if not crops or len(crops) > 3:
        raise StoreError(422, "invalid Groq crop batch size")
    refs = [entry["ref_id"] for entry in crops]
    if len(set(refs)) != len(refs):
        raise StoreError(422, "duplicate crop reference")
    content = [{"type": "text", "text": "ref_ids: " + json.dumps(refs)}]
    for entry in crops:
        original = entry["views"].get("original")
        if not isinstance(original, bytes) or not original.startswith(b"\xff\xd8\xff"):
            raise StoreError(422, "invalid original crop evidence")
        content.extend(
            [
                {"type": "text", "text": entry["ref_id"] + " original"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + base64.b64encode(original).decode("ascii")
                    },
                },
            ]
        )
    settings = contract["request_configuration"]
    payload = {
        "model": contract["model"],
        "messages": [
            {"role": "system", "content": contract["instructions"]},
            {"role": "user", "content": content},
        ],
        "reasoning_effort": settings["reasoning_effort"],
        "max_completion_tokens": settings["max_completion_tokens"],
        "stream": False,
        "response_format": _groq_schema_format(contract["response_format"]),
    }
    try:
        response = await client.post(
            contract["endpoint"],
            headers={"Authorization": "Bearer " + api_key},
            json=payload,
            timeout=settings["timeout_s"],
        )
    except httpx.HTTPError:
        raise StoreError(502, "crop reading provider unavailable") from None
    if response.status_code == 429:
        raise StoreError(
            429, _rate_limit_reason("crop reading provider rate limit reached", response.headers)
        )
    if response.status_code != 200:
        raise StoreError(502, "crop reading provider rejected request")
    try:
        raw = response.json()
    except ValueError:
        raise StoreError(502, "crop reading provider response malformed") from None
    if not isinstance(raw, dict) or raw.get("model") != contract["model"]:
        raise StoreError(502, "crop reading provider response malformed")
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise StoreError(502, "crop reading provider response malformed")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise StoreError(502, "crop reading provider response not completed")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("refusal"):
        raise StoreError(502, "crop reading provider refused")
    text = message.get("content")
    if not isinstance(text, str) or not isinstance(raw.get("id"), str):
        raise StoreError(502, "crop reading provider response malformed")
    try:
        parsed = json.loads(text)
    except ValueError:
        raise StoreError(502, "crop reading provider response malformed") from None
    if not isinstance(parsed, dict):
        raise StoreError(502, "crop reading provider response malformed")
    return {
        "provider": "groq",
        "parsed": parsed,
        "raw_response": raw,
        "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
        "model": raw["model"],
        "response_id": raw["id"],
        "cost_usd_estimate": None,
    }
