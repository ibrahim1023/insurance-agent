import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import server

ORIGIN = "http://localhost:8001"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VISION_ENABLED", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as c:
        yield c


def post(client, path, origin=ORIGIN, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if origin is not None:
        headers["origin"] = origin
    return client.post(path, headers=headers, **kwargs)


def start_sweep(client):
    return post(
        client,
        "/api/sweeps",
        json={"country": "AE", "currency": "USD", "device": "voice-view-test"},
    )


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def scene_image(width=1280, height=720, seed=7):
    rng = np.random.RandomState(seed)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    block = 80
    for y in range(0, height, block):
        for x in range(0, width, block):
            image[y : y + block, x : x + block] = rng.randint(0, 255, 3)
    return image


def upload(client, sweep_id, image, ts):
    resp = post(
        client,
        f"/api/sweeps/{sweep_id}/frames?ts={ts}",
        content=jpeg(image),
        headers={"content-type": "image/jpeg"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["frame_id"]


def commit(client, sweep_id, frame_id, new_books=None, new_items=None):
    return client.app.state.store.commit_tracks(
        sweep_id,
        frame_id,
        {
            "frame_id": frame_id,
            "alignment": "none",
            "assignments": [],
            "applied_props": [],
            "new_books": new_books or [],
            "new_items": new_items or [],
            "flag_tracks": [],
            "review_items": [],
            "unresolved": 0,
        },
    )


def book_payload(frame_id, x=40, w=60):
    return {
        "frame_ref": f"frames/{frame_id}.jpg",
        "status": "unidentified",
        "bbox": {"x": x, "y": 40, "w": w, "h": 200},
        "image_width": 1280,
        "image_height": 720,
        "observed_fragments": [{"field": "observed_title", "text": "Partial Title"}],
        "evidence": [
            {
                "frame_id": frame_id,
                "frame_ref": f"frames/{frame_id}.jpg",
                "bbox": {"x": x, "y": 40, "w": w, "h": 200},
                "image_width": 1280,
                "image_height": 720,
                "sharpness": 10.0,
            }
        ],
        "potential_duplicate": False,
        "track_histogram": None,
    }


def item_payload(frame_id, x=300, w=120, category="shelving"):
    return {
        "category": category,
        "description": "wooden shelf",
        "material": "wood",
        "brand_model": "",
        "frame_ref": f"frames/{frame_id}.jpg",
        "status": "range",
        "valuation_state": "unpriced",
        "confidence": 0.9,
        "bbox": {"x": x, "y": 40, "w": w, "h": 300},
        "image_width": 1280,
        "image_height": 720,
        "evidence": [
            {
                "frame_id": frame_id,
                "frame_ref": f"frames/{frame_id}.jpg",
                "bbox": {"x": x, "y": 40, "w": w, "h": 300},
                "image_width": 1280,
                "image_height": 720,
                "sharpness": 9.0,
            }
        ],
        "potential_duplicate": False,
        "track_histogram": None,
    }


def test_voice_view_downscales_and_scales_target_bboxes(client):
    state = start_sweep(client).json()
    frame_id = upload(client, state["id"], scene_image(), 1.0)
    committed = commit(
        client,
        state["id"],
        frame_id,
        new_books=[book_payload(frame_id)],
        new_items=[item_payload(frame_id)],
    )
    resp = client.get(
        f"/api/sweeps/{state['id']}/voice_view?frame_id={frame_id}"
    )
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["available"] is True
    assert view["frame_id"] == frame_id
    assert view["frame_ref"] == f"frames/{frame_id}.jpg"
    assert view["timestamp_s"] == 1.0
    assert view["image"].startswith("data:image/jpeg;base64,")
    assert view["image_width"] == 640
    assert view["image_height"] == 360
    kinds = {t["kind"] for t in view["targets"]}
    assert kinds == {"book", "item"}
    book_target = next(t for t in view["targets"] if t["kind"] == "book")
    assert book_target["id"] == committed.books[0].id
    assert book_target["bbox"] == {"x": 20, "y": 20, "w": 30, "h": 100}
    assert book_target["observed_fragments"] == [
        {"field": "observed_title", "text": "Partial Title"}
    ]
    item_target = next(t for t in view["targets"] if t["kind"] == "item")
    assert item_target["description"] == "wooden shelf"
    assert item_target["bbox"] == {"x": 150, "y": 20, "w": 60, "h": 150}
    assert view["targets_truncated"] is False


def test_voice_view_small_frame_not_upscaled(client):
    state = start_sweep(client).json()
    frame_id = upload(client, state["id"], scene_image(width=320, height=200), 1.0)
    resp = client.get(f"/api/sweeps/{state['id']}/voice_view?frame_id={frame_id}")
    assert resp.status_code == 200
    view = resp.json()
    assert view["image_width"] == 320
    assert view["image_height"] == 200


def test_voice_view_only_same_frame_targets(client):
    state = start_sweep(client).json()
    frame_a = upload(client, state["id"], scene_image(seed=1), 1.0)
    frame_b = upload(client, state["id"], scene_image(seed=2), 2.0)
    commit(client, state["id"], frame_a, new_books=[book_payload(frame_a)])
    resp = client.get(
        f"/api/sweeps/{state['id']}/voice_view?frame_id={frame_b}"
    )
    assert resp.status_code == 200
    assert resp.json()["targets"] == []


def test_voice_view_unknown_frame_and_sweep(client):
    state = start_sweep(client).json()
    bogus = "f" * 32
    assert (
        client.get(f"/api/sweeps/{state['id']}/voice_view?frame_id=frame_{bogus}")
        .status_code
        == 404
    )
    assert (
        client.get(f"/api/sweeps/{'0' * 32}/voice_view?frame_id=frame_{bogus}")
        .status_code
        == 404
    )
    assert (
        client.get(f"/api/sweeps/{state['id']}/voice_view?frame_id=../../state")
        .status_code
        == 404
    )


def test_voice_view_byte_cap_fails_closed(client, monkeypatch):
    contract = json.loads(json.dumps(server.REALTIME_CONTRACT))
    contract["visual_context"]["maximum_jpeg_bytes"] = 1
    monkeypatch.setattr(server, "REALTIME_CONTRACT", contract)
    state = start_sweep(client).json()
    frame_id = upload(client, state["id"], scene_image(), 1.0)
    resp = client.get(f"/api/sweeps/{state['id']}/voice_view?frame_id={frame_id}")
    assert resp.status_code == 413


def test_voice_view_target_cap_marks_truncated(client, monkeypatch):
    contract = json.loads(json.dumps(server.REALTIME_CONTRACT))
    contract["visual_context"]["maximum_targets_per_view"] = 1
    monkeypatch.setattr(server, "REALTIME_CONTRACT", contract)
    state = start_sweep(client).json()
    frame_id = upload(client, state["id"], scene_image(), 1.0)
    commit(
        client,
        state["id"],
        frame_id,
        new_books=[book_payload(frame_id), book_payload(frame_id, x=200)],
    )
    resp = client.get(f"/api/sweeps/{state['id']}/voice_view?frame_id={frame_id}")
    assert resp.status_code == 200
    view = resp.json()
    assert len(view["targets"]) == 1
    assert view["targets_truncated"] is True


def test_voice_view_leaves_client_envelope_headroom(client, monkeypatch):
    contract = json.loads(json.dumps(server.REALTIME_CONTRACT))
    contract["visual_context"]["maximum_message_bytes"] = 40000
    monkeypatch.setattr(server, "REALTIME_CONTRACT", contract)
    state = start_sweep(client).json()
    frame_id = upload(client, state["id"], scene_image(), 1.0)
    books = []
    for i in range(64):
        b = book_payload(frame_id, x=40 + i)
        b["observed_fragments"] = [
            {"field": "observed_title", "text": "Fragment text " + "x" * 150 + f" {i}"}
        ]
        books.append(b)
    commit(client, state["id"], frame_id, new_books=books)
    resp = client.get(f"/api/sweeps/{state['id']}/voice_view?frame_id={frame_id}")
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert len(json.dumps(view).encode("utf-8")) <= 40000 - 512
    assert view["targets_truncated"] is True


def test_capture_config_exposes_policy_and_vision_flag(client):
    resp = client.get("/api/capture_config")
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["vision_enabled"] is False
    policy = cfg["visual_context"]
    assert policy["maximum_images_per_sweep"] == 24
    assert policy["maximum_frame_age_s"] == 3
    assert policy["maximum_message_bytes"] == 49152
    assert "instructions" not in json.dumps(cfg)
    assert "session" not in cfg


def test_correction_source_frame_evidence(client):
    state = start_sweep(client).json()
    sid = state["id"]
    frame_a = upload(client, sid, scene_image(seed=3), 1.0)
    frame_b = upload(client, sid, scene_image(seed=4), 2.0)
    committed = commit(client, sid, frame_a, new_books=[book_payload(frame_a)])
    book_id = committed.books[0].id
    post(client, f"/api/sweeps/{sid}/turns", json={
        "turn_id": "t1", "text": "exclude that book", "source": "typed_user"})
    ok = post(client, f"/api/sweeps/{sid}/corrections", json={
        "type": "exclude_item", "target_id": book_id, "value": True,
        "source_turn_id": "t1", "source_frame_id": frame_a})
    assert ok.status_code == 200, ok.text
    wrong = post(client, f"/api/sweeps/{sid}/corrections", json={
        "type": "exclude_item", "target_id": book_id, "value": True,
        "source_turn_id": "t1", "source_frame_id": frame_b})
    assert wrong.status_code == 409
    bogus = post(client, f"/api/sweeps/{sid}/corrections", json={
        "type": "exclude_item", "target_id": book_id, "value": True,
        "source_turn_id": "t1", "source_frame_id": "frame_" + "e" * 32})
    assert bogus.status_code == 409


def test_correction_source_frame_region_semantics(client):
    state = start_sweep(client).json()
    sid = state["id"]
    frame_a = upload(client, sid, scene_image(seed=5), 1.0)
    commit(client, sid, frame_a, new_books=[book_payload(frame_a)])
    post(client, f"/api/sweeps/{sid}/turns", json={
        "turn_id": "t2", "text": "exclude the room", "source": "typed_user"})
    resp = post(client, f"/api/sweeps/{sid}/corrections", json={
        "type": "exclude_region", "target_id": "room", "value": True,
        "source_turn_id": "t2", "source_frame_id": frame_a})
    assert resp.status_code == 200, resp.text


def test_blank_turn_rejected_with_sanitized_detail(client):
    state = start_sweep(client).json()
    resp = post(client, f"/api/sweeps/{state['id']}/turns", json={
        "turn_id": "t1", "text": "   ", "source": "typed_user"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list)
    assert {"loc", "msg", "type"} <= set(detail[0].keys())
    loaded = client.get(f"/api/sweeps/{state['id']}").json()
    assert loaded["user_turns"] == []
