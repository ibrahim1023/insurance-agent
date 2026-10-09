import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import cv2
import httpx

from preflight.spines import evaluate_boxes
from preflight.vision import proposal_boxes


async def probe(source, server, origin):
    records = json.loads(source.read_text())
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError('this first probe requires exactly one independently labelled image')
    record = records[0]
    image_path = source.parent / record['image_path']
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError('cannot decode labelled image')
    height, width = image.shape[:2]
    truth = record['truth_boxes']
    evaluate_boxes([], truth)
    result = {
        'status': 'BLOCKED', 'input_source': str(source.resolve()),
        'image_path': str(image_path.resolve()), 'truth_count': len(truth),
        'scope': record.get('orientation_note', ''),
        'model_input_contains_truth': False, 'server': server,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'limitation': 'single manually cropped image, not a continuous or compliant-room evaluation',
    }
    sweep_id = None
    async with httpx.AsyncClient(timeout=90, headers={'Origin': origin}) as client:
        try:
            health = await client.get(server + '/api/health')
            health.raise_for_status()
            if health.json().get('openai_key_present') is not True:
                result['reason'] = 'product server has no OpenAI credential configured'
                return result
            created = await client.post(server + '/api/sweeps', json={
                'country': 'AE', 'currency': 'AED', 'device': 'frozen-vision-probe',
            })
            created.raise_for_status()
            sweep_id = created.json()['id']
            result['sweep_id'] = sweep_id
            uploaded = await client.post(
                server + '/api/sweeps/' + sweep_id + '/frames?ts=1.0',
                content=image_path.read_bytes(), headers={'Content-Type': 'image/jpeg'},
            )
            uploaded.raise_for_status()
            frame_id = uploaded.json()['frame_id']
            result['frame_id'] = frame_id
            started = time.perf_counter()
            analysed = await client.post(
                server + '/api/sweeps/' + sweep_id + '/analyze_frame',
                json={'frame_id': frame_id},
            )
            result['request_elapsed_ms_single_invocation'] = (time.perf_counter() - started) * 1000
            if analysed.status_code != 200:
                result.update(status='FAILED', reason='analysis endpoint returned HTTP ' + str(analysed.status_code))
                return result
            observation = analysed.json()
            if observation['image_width'] != width or observation['image_height'] != height:
                raise ValueError('analysis dimensions disagree with independently decoded evidence')
            raw_scene = observation['observation']
            contract = json.loads((Path(__file__).resolve().parents[1] / 'backend/vision_contract.json').read_text())
            mapping_options = {
                'coordinate_system': observation.get('coordinate_system', 'normalized_1000'),
                'sent_width': observation.get('sent_image_width'),
                'sent_height': observation.get('sent_image_height'),
            }
            all_boxes = proposal_boxes(raw_scene, width, height, confidence_min=0, **mapping_options)
            admitted = proposal_boxes(raw_scene, width, height, confidence_min=contract['admission']['book_existence_confidence_min'], **mapping_options)
            result.update(
                status='MEASURED', observation=observation,
                raw_book_proposal_count=len(raw_scene['books']),
                all_valid_boxes=all_boxes, admitted_boxes=admitted,
                all_valid_box_metrics=evaluate_boxes(all_boxes['boxes'], truth),
                admitted_box_metrics=evaluate_boxes(admitted['boxes'], truth),
                cost_usd_estimate=observation.get('cost_usd_estimate'),
                cost_label='usage-based estimate, not reconciled invoice',
            )
            overlay = image.copy()
            for x, y, w, h in admitted['boxes']:
                cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 255, 0), 2)
            result['_overlay'] = overlay
            return result
        except httpx.HTTPError as exc:
            result.update(status='FAILED', reason='product-server request failed: ' + type(exc).__name__)
            return result
        finally:
            if sweep_id is not None:
                try:
                    ended = await client.post(server + '/api/sweeps/' + sweep_id + '/end', json={'duration_s': 0.0})
                    result['probe_end_saved'] = ended.status_code == 200
                except httpx.HTTPError:
                    result['probe_end_saved'] = False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--server', default='http://localhost:8001')
    parser.add_argument('--origin')
    args = parser.parse_args()
    source, output = Path(args.input), Path(args.output)
    server = args.server.rstrip('/')
    parsed = urlparse(server)
    if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1'}:
        parser.error('this local-server probe only permits localhost/127.0.0.1')
    if output.exists() or output.with_suffix('.overlay.jpg').exists():
        parser.error('output already exists; choose a new evidence filename')
    result = asyncio.run(probe(source, server, args.origin or server))
    overlay = result.pop('_overlay', None)
    output.parent.mkdir(parents=True, exist_ok=True)
    if overlay is not None:
        overlay_path = output.with_suffix('.overlay.jpg')
        if not cv2.imwrite(str(overlay_path), overlay):
            raise ValueError('cannot save prediction overlay')
        result['overlay_path'] = str(overlay_path.resolve())
    with output.open('x') as file:
        json.dump(result, file, indent=2, allow_nan=False)
    print(json.dumps({key: result.get(key) for key in ('status', 'raw_book_proposal_count', 'admitted_box_metrics', 'cost_usd_estimate', 'reason')}))


if __name__ == '__main__':
    main()
