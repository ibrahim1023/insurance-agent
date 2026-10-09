import asyncio
import base64
import json
from decimal import Decimal
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from preflight.vision import VisionError, observe_jpeg, proposal_boxes, usage_cost


CONTRACT = json.loads((Path(__file__).resolve().parents[2] / '.scratch/assessment-run/vision-contract.json').read_text())


def scene():
    return {
        'books': [{
            'bbox': {'left': 100, 'top': 100, 'right': 300, 'bottom': 900},
            'orientation': 'upright', 'observed_title': 'Test Volume',
            'observed_author': 'Test Author', 'observed_publisher': '',
            'observed_edition': '', 'observed_isbn': '', 'title_legible': True,
            'author_legible': True, 'existence_confidence': 0.95,
            'text_confidence': 0.95, 'special_flags': [], 'visible_evidence': 'visible printed spine',
        }],
        'items': [], 'unresolved_groups': [], 'capture_issues': [],
    }


def response(result=None, status='completed'):
    return {
        'id': 'resp_fixture', 'model': CONTRACT['model'], 'status': status,
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result if result is not None else scene())}]}],
        'usage': {'input_tokens': 1000, 'input_tokens_details': {'cached_tokens': 100}, 'output_tokens': 100},
    }


def run(payload, status_code=200, contract=CONTRACT):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(status_code, json=payload)

    async def execute():
        image = cv2.imencode('.jpg', np.zeros((20, 40, 3), dtype=np.uint8))[1].tobytes()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await observe_jpeg(client, image, 'test-only-token', contract)

    return asyncio.run(execute()), requests


def test_observation_uses_lead_contract_without_truth_or_filename():
    result, requests = run(response())
    assert len(requests) == 1
    payload = requests[0]
    assert payload['model'] == 'gpt-5.4-2026-03-05'
    assert payload['store'] is False
    assert payload['text']['format']['strict'] is True
    image = payload['input'][0]['content'][0]
    assert image['type'] == 'input_image' and image['detail'] == 'original'
    assert base64.b64decode(image['image_url'].split(',')[1]).startswith(b'\xff\xd8')
    assert 'row14' not in json.dumps(payload)
    assert 'truth_boxes' not in json.dumps(payload)
    assert result['scene']['books'][0]['observed_title'] == 'Test Volume'
    assert result['cost_usd_estimate'] == '0.003775'


def test_normalized_box_mapping_and_rejected_proposals_keep_indices():
    data = scene()
    data['books'].append({**data['books'][0], 'bbox': {'left': 300, 'top': 100, 'right': 300, 'bottom': 900}})
    data['books'].append({**data['books'][0], 'existence_confidence': 0.5})
    result = proposal_boxes(data, 400, 200, confidence_min=0.85)
    assert result['boxes'] == [(40, 20, 80, 160)]
    assert result['accepted_indices'] == [0]
    assert result['rejected_indices'] == [1, 2]
    assert result['proposal_count'] == 3


@pytest.mark.parametrize('status', ['incomplete', 'failed'])
def test_incomplete_or_failed_response_cannot_be_inventory(status):
    with pytest.raises(VisionError, match='not completed'):
        run(response(status=status))


def test_refusal_does_not_become_empty_success():
    data = response()
    data['output'] = [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'fixture refusal'}]}]
    with pytest.raises(VisionError, match='refused'):
        run(data)


def test_unexpected_money_field_rejected():
    data = scene()
    data['books'][0]['price'] = 99
    with pytest.raises(VisionError, match='invalid scene'):
        run(response(data))


def test_invalid_provider_output_shape_is_controlled():
    data = response()
    data['output'] = None
    with pytest.raises(VisionError, match='invalid vision provider response'):
        run(data)


def test_provider_error_is_sanitized():
    with pytest.raises(VisionError, match='provider rejected request') as failure:
        run({'error': {'message': 'fake-secret-must-not-be-echoed'}}, 401)
    assert 'fake-secret' not in str(failure.value)


def test_pixel_coordinate_mapping_accounts_for_sent_image_resize():
    data = scene()
    data['books'][0]['bbox'] = {'left': 20, 'top': 10, 'right': 60, 'bottom': 90}
    result = proposal_boxes(data, 400, 200, confidence_min=0.85,
                            coordinate_system='pixels', sent_width=200, sent_height=100)
    assert result['boxes'] == [(40, 20, 80, 160)]
    data['books'][0]['bbox']['right'] = 201
    result = proposal_boxes(data, 400, 200, confidence_min=0.85,
                            coordinate_system='pixels', sent_width=200, sent_height=100)
    assert result['boxes'] == []
    assert result['rejected_indices'] == [0]


@pytest.mark.parametrize('sent_width', [0, False, -1, '200'])
def test_invalid_sent_dimensions_are_not_silently_replaced(sent_width):
    with pytest.raises(VisionError, match='invalid sent image dimensions'):
        proposal_boxes(scene(), 400, 200, confidence_min=0.85,
                       coordinate_system='pixels', sent_width=sent_width, sent_height=100)


def test_native_request_declares_dimensions_and_dynamic_schema_bounds():
    contract = json.loads((Path(__file__).resolve().parents[2] / 'backend/vision_contract.json').read_text())
    data = scene()
    data['books'][0]['bbox'] = {'left': 4, 'top': 2, 'right': 12, 'bottom': 18}
    result, requests = run(response(data), contract=contract)
    content = requests[0]['input'][0]['content']
    text = next(item['text'] for item in content if item['type'] == 'input_text')
    assert '40 pixels wide' in text and '20 pixels tall' in text
    bounds = requests[0]['text']['format']['schema']['$defs']['box']['properties']
    assert bounds['right']['maximum'] == 40
    assert bounds['bottom']['maximum'] == 20
    assert result['coordinate_system'] == 'pixels'


def test_usage_cost_is_decimal_and_unknown_model_is_not_guessed():
    result = usage_cost(response(), CONTRACT)
    assert result == Decimal('0.003775')
    data = response()
    data['model'] = 'unconfigured-model'
    assert usage_cost(data, CONTRACT) is None
    data = response()
    data['usage']['input_tokens_details']['cached_tokens'] = 1001
    assert usage_cost(data, CONTRACT) is None
