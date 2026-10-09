import base64
import json

import cv2
import numpy as np

from .sweeps import StoreError, SweepStore

_ENVELOPE_MARGIN_BYTES = 512


def _scaled_box(bbox, sx: float, sy: float) -> dict:
    return {
        "x": max(0, int(round(bbox.x * sx))),
        "y": max(0, int(round(bbox.y * sy))),
        "w": max(1, int(round(bbox.w * sx))),
        "h": max(1, int(round(bbox.h * sy))),
    }


def _same_frame_evidence(track, frame_id: str):
    return next((e for e in track.evidence if e.frame_id == frame_id), None)


def build_voice_view(store: SweepStore, sweep_id: str, frame_id: str, contract: dict) -> dict:
    cfg = contract["visual_context"]
    path = store.frame_path(sweep_id, frame_id)
    image = cv2.imdecode(
        np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        raise StoreError(422, "saved frame is not a decodable JPEG image")
    orig_h, orig_w = image.shape[:2]
    long_edge = int(cfg["maximum_image_long_edge_px"])
    view = image
    if max(orig_w, orig_h) > long_edge:
        scale = long_edge / max(orig_w, orig_h)
        view = cv2.resize(
            view,
            (max(1, int(round(orig_w * scale))), max(1, int(round(orig_h * scale)))),
            interpolation=cv2.INTER_AREA,
        )

    def encode(img):
        ok, buf = cv2.imencode(
            ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(cfg["jpeg_quality"])]
        )
        return buf.tobytes() if ok else None

    payload = encode(view)
    cap = int(cfg["maximum_jpeg_bytes"])
    factor = float(cfg["resize_factor"])
    while payload is not None and len(payload) > cap:
        h, w = view.shape[:2]
        nw, nh = int(round(w * factor)), int(round(h * factor))
        if nw < 1 or nh < 1 or (nw == w and nh == h):
            break
        view = cv2.resize(view, (nw, nh), interpolation=cv2.INTER_AREA)
        payload = encode(view)
    if payload is None or len(payload) > cap:
        raise StoreError(413, "voice view image exceeds the configured byte cap")

    state = store.get(sweep_id)
    frame = next(f for f in state.frames if f.id == frame_id)
    sent_h, sent_w = view.shape[:2]
    sx, sy = sent_w / orig_w, sent_h / orig_h
    targets = []
    for book in state.books:
        ev = _same_frame_evidence(book, frame_id)
        if ev is None:
            continue
        targets.append(
            {
                "id": book.id,
                "kind": "book",
                "status": book.status,
                "bbox": _scaled_box(ev.bbox, sx, sy),
                "excluded": book.excluded,
                "potential_duplicate": book.potential_duplicate,
                "observed_fragments": book.observed_fragments,
            }
        )
    for item in state.items:
        ev = _same_frame_evidence(item, frame_id)
        if ev is None:
            continue
        targets.append(
            {
                "id": item.id,
                "kind": "item",
                "category": item.category,
                "status": item.status,
                "valuation_state": item.valuation_state,
                "description": item.description,
                "bbox": _scaled_box(ev.bbox, sx, sy),
                "excluded": item.excluded,
                "potential_duplicate": item.potential_duplicate,
            }
        )
    truncated = False
    if len(targets) > int(cfg["maximum_targets_per_view"]):
        targets = targets[: int(cfg["maximum_targets_per_view"])]
        truncated = True
    result = {
        "available": True,
        "frame_id": frame_id,
        "frame_ref": frame.frame_ref,
        "timestamp_s": frame.timestamp_s,
        "image": "data:image/jpeg;base64," + base64.b64encode(payload).decode("ascii"),
        "image_width": sent_w,
        "image_height": sent_h,
        "original_image_width": orig_w,
        "original_image_height": orig_h,
        "targets": targets,
        "targets_truncated": truncated,
    }
    message_cap = int(cfg["maximum_message_bytes"])
    body_cap = max(0, message_cap - _ENVELOPE_MARGIN_BYTES)
    while targets and len(json.dumps(result).encode("utf-8")) > body_cap:
        targets.pop()
        result["targets_truncated"] = True
    if len(json.dumps(result).encode("utf-8")) > body_cap:
        raise StoreError(413, "voice view exceeds the configured message cap")
    return result
