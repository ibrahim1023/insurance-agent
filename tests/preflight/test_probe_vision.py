import asyncio
import json

import cv2
import httpx
import numpy as np

from preflight import probe_vision


def labelled_input(tmp_path):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[10:90, 20:60] = 255
    assert cv2.imwrite(str(tmp_path / 'image.jpg'), image)
    source = tmp_path / 'input.json'
    source.write_text(json.dumps([{'image_path': 'image.jpg', 'truth_boxes': [[20, 10, 40, 80]], 'orientation_note': 'synthetic fixture'}]))
    return source


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(probe_vision.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_probe_computes_known_literal_metrics_without_sending_truth(tmp_path, monkeypatch):
    source = labelled_input(tmp_path)
    requests = []
    frame_id = 'frame_' + 'b' * 32

    def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'openai_key_present': True})
        if request.url.path.endswith('/api/sweeps'):
            return httpx.Response(200, json={'id': 'a' * 32})
        if request.url.path.endswith('/frames'):
            return httpx.Response(200, json={'frame_id': frame_id})
        if request.url.path.endswith('/end'):
            return httpx.Response(200, json={'status': 'partial'})
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={
            'frame_id': frame_id, 'image_width': 200, 'image_height': 100,
            'observation': {'books': [{'bbox': {'left': 100, 'top': 100, 'right': 300, 'bottom': 900}, 'existence_confidence': 0.95}]},
            'cost_usd_estimate': '0.01',
        })

    install_transport(monkeypatch, handle)
    result = asyncio.run(probe_vision.probe(source, 'http://localhost:8001', 'http://localhost:8001'))
    assert result['status'] == 'MEASURED'
    assert requests == [{'frame_id': frame_id}]
    assert result['admitted_box_metrics'] == {
        'predicted': 1, 'truth': 1, 'matched': 1, 'false_positives': 0,
        'missed': 0, 'precision': 1.0, 'recall': 1.0, 'count_error_pct': 0.0,
    }
    assert result['probe_end_saved'] is True


def test_probe_missing_credential_stays_blocked(tmp_path, monkeypatch):
    source = labelled_input(tmp_path)
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={'openai_key_present': False}))
    result = asyncio.run(probe_vision.probe(source, 'http://localhost:8001', 'http://localhost:8001'))
    assert result['status'] == 'BLOCKED'
    assert 'admitted_box_metrics' not in result
    assert 'sweep_id' not in result
