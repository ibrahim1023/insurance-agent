import base64
import copy
import math
from decimal import Decimal, InvalidOperation
from typing import Literal

import cv2
import httpx
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class VisionError(RuntimeError):
    pass


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Box(StrictRecord):
    left: int = Field(ge=0, le=6000)
    top: int = Field(ge=0, le=6000)
    right: int = Field(ge=0, le=6000)
    bottom: int = Field(ge=0, le=6000)


class BookProposal(StrictRecord):
    bbox: Box
    orientation: Literal['upright', 'leaning', 'upside_down', 'flat', 'stacked', 'unclear']
    observed_title: str
    observed_author: str
    observed_publisher: str
    observed_edition: str
    observed_isbn: str
    title_legible: bool
    author_legible: bool
    existence_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    text_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    special_flags: list[Literal['possible_signed', 'possible_rare', 'possible_antiquarian']]
    visible_evidence: str


class ItemProposal(StrictRecord):
    bbox: Box
    category: Literal['shelving', 'table', 'chair', 'sofa', 'lamp', 'coffee_machine', 'art', 'portrait', 'framed_print', 'rug', 'monitor', 'speaker', 'electronics', 'decor', 'other']
    description: str
    material: str
    brand_model: str
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class UnresolvedGroup(StrictRecord):
    bbox: Box
    reason: str


class CaptureIssue(StrictRecord):
    type: Literal['blur', 'glare', 'occlusion', 'region_unclear']
    description: str


class SceneProposal(StrictRecord):
    books: list[BookProposal]
    items: list[ItemProposal]
    unresolved_groups: list[UnresolvedGroup]
    capture_issues: list[CaptureIssue]


async def observe_jpeg(client, jpeg, api_key, contract):
    if not api_key:
        raise VisionError('vision API key unavailable')
    if not jpeg.startswith(b'\xff\xd8\xff'):
        raise VisionError('invalid JPEG evidence')
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise VisionError('invalid JPEG evidence')
    height, width = image.shape[:2]
    maximum = contract['runtime_limits']['maximum_image_long_edge_px']
    sent_width, sent_height = width, height
    if max(width, height) > maximum:
        scale = maximum / max(width, height)
        sent_width, sent_height = max(1, round(width * scale)), max(1, round(height * scale))
        image = cv2.resize(image, (sent_width, sent_height), interpolation=cv2.INTER_AREA)
        encoded, buffer = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not encoded:
            raise VisionError('could not encode vision evidence')
        jpeg = buffer.tobytes()
    settings = contract['request_configuration']
    coordinate_system = contract.get('coordinate_system', 'normalized_1000')
    response_format = copy.deepcopy(contract['response_format'])
    content = [{
        'type': 'input_image', 'detail': settings['image_detail'],
        'image_url': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii'),
    }]
    if coordinate_system == 'pixels':
        content.append({'type': 'input_text', 'text': (
            f'The supplied image is {sent_width} pixels wide and {sent_height} pixels tall. '
            f'Return absolute PIXEL coordinates: x from 0 to {sent_width}, y from 0 to {sent_height}. '
            f'(0,0) is top-left and ({sent_width},{sent_height}) is bottom-right. '
            'Do not normalize either axis or use coordinates of an imagined resized image.'
        )})
        properties = response_format['schema']['$defs']['box']['properties']
        for key in ('left', 'right'):
            properties[key]['maximum'] = sent_width
        for key in ('top', 'bottom'):
            properties[key]['maximum'] = sent_height
    elif coordinate_system != 'normalized_1000':
        raise VisionError('unsupported coordinate system')
    payload = {
        'model': contract['model'], 'instructions': contract['instructions'],
        'reasoning': settings['reasoning'], 'max_output_tokens': settings['max_output_tokens'],
        'store': settings['store'],
        'input': [{'role': 'user', 'content': content}],
        'text': {'format': response_format},
    }
    try:
        response = await client.post(
            contract['endpoint'], headers={'Authorization': 'Bearer ' + api_key},
            json=payload, timeout=settings['request_timeout_s'],
        )
    except httpx.HTTPError:
        raise VisionError('vision provider unavailable') from None
    if response.status_code != 200:
        raise VisionError('vision provider rejected request (' + str(response.status_code) + ')')
    try:
        raw = response.json()
    except ValueError:
        raise VisionError('invalid vision provider response') from None
    if not isinstance(raw, dict) or raw.get('status') != 'completed':
        raise VisionError('vision response not completed')
    outputs = raw.get('output')
    if not isinstance(outputs, list):
        raise VisionError('invalid vision provider response')
    texts = []
    for message in outputs:
        if not isinstance(message, dict) or message.get('type') != 'message':
            continue
        content = message.get('content')
        if not isinstance(content, list):
            raise VisionError('invalid vision provider response')
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'refusal':
                raise VisionError('vision provider refused observation')
            if part.get('type') == 'output_text' and isinstance(part.get('text'), str):
                texts.append(part['text'])
    try:
        scene = SceneProposal.model_validate_json(''.join(texts)).model_dump()
    except (ValidationError, ValueError):
        raise VisionError('invalid scene response') from None
    cost = usage_cost(raw, contract)
    return {
        'scene': scene, 'raw_response': raw, 'usage': raw.get('usage'),
        'model': raw.get('model'), 'response_id': raw.get('id'),
        'cost_usd_estimate': format(cost, 'f') if cost is not None else None,
        'image_width': width, 'image_height': height,
        'sent_image_width': sent_width, 'sent_image_height': sent_height,
        'coordinate_system': coordinate_system,
    }


def proposal_boxes(scene, width, height, *, confidence_min, coordinate_system='normalized_1000', sent_width=None, sent_height=None):
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise VisionError('invalid image dimensions')
    if isinstance(confidence_min, bool) or not isinstance(confidence_min, (int, float)) or not math.isfinite(confidence_min) or not 0 <= confidence_min <= 1:
        raise VisionError('invalid confidence threshold')
    if coordinate_system == 'pixels':
        x_base = width if sent_width is None else sent_width
        y_base = height if sent_height is None else sent_height
        if type(x_base) is not int or type(y_base) is not int or min(x_base, y_base) <= 0:
            raise VisionError('invalid sent image dimensions')
    elif coordinate_system == 'normalized_1000':
        x_base = y_base = 1000
    else:
        raise VisionError('unsupported coordinate system')
    boxes, accepted, rejected = [], [], []
    for index, book in enumerate(scene['books']):
        box = book['bbox']
        values = [box[key] for key in ('left', 'top', 'right', 'bottom')]
        limits = (x_base, y_base, x_base, y_base)
        valid = all(type(value) is int and 0 <= value <= limit for value, limit in zip(values, limits))
        left, top, right, bottom = values
        confidence = book['existence_confidence']
        valid_confidence = not isinstance(confidence, bool) and isinstance(confidence, (int, float)) and math.isfinite(confidence) and 0 <= confidence <= 1
        if not valid or right <= left or bottom <= top or not valid_confidence or confidence < confidence_min:
            rejected.append(index)
            continue
        x, y = left * width // x_base, top * height // y_base
        x1, y1 = (right * width + x_base - 1) // x_base, (bottom * height + y_base - 1) // y_base
        boxes.append((x, y, x1 - x, y1 - y))
        accepted.append(index)
    return {'boxes': boxes, 'accepted_indices': accepted, 'rejected_indices': rejected, 'proposal_count': len(scene['books'])}


def usage_cost(response, contract):
    if response.get('model') != contract['model']:
        return None
    usage = response.get('usage')
    if not isinstance(usage, dict) or not isinstance(usage.get('input_tokens_details'), dict):
        return None
    incoming, outgoing = usage.get('input_tokens'), usage.get('output_tokens')
    cached = usage['input_tokens_details'].get('cached_tokens')
    if any(type(value) is not int or value < 0 for value in (incoming, outgoing, cached)) or cached > incoming:
        return None
    try:
        rates = contract['cost_evidence']
        standard = Decimal(rates['standard_input_usd_per_million'])
        cache = Decimal(rates['cached_input_usd_per_million'])
        output = Decimal(rates['output_usd_per_million'])
        if any(not rate.is_finite() or rate < 0 for rate in (standard, cache, output)):
            return None
        return ((incoming - cached) * standard + cached * cache + outgoing * output) / Decimal('1000000')
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None
