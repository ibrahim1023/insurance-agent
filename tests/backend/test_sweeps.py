import cv2
import numpy as np
import pytest

from backend.sweeps import StoreError, SweepStore


def jpeg(width=8, height=8):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 1] = 200
    return cv2.imencode(".jpg", image)[1].tobytes()


def expect_error(code, fn, *args):
    with pytest.raises(StoreError) as info:
        fn(*args)
    assert info.value.status_code == code


def test_sweep_frames_survive_restart(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "iphone-test")
    payload = jpeg()
    frame = store.add_frame(state.id, payload, 1.0)

    reloaded = SweepStore(tmp_path / "runs")
    loaded = reloaded.get(state.id)

    assert loaded.id == state.id
    assert loaded.country == "AE"
    assert loaded.currency == "USD"
    assert loaded.status == "interrupted"
    assert loaded.processing_complete is False
    assert len(loaded.frames) == 1
    assert loaded.frames[0].id == frame.id
    saved = tmp_path / "runs" / state.id / frame.frame_ref
    assert saved.read_bytes() == payload


def test_frame_timestamp_must_advance(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    store.add_frame(state.id, jpeg(), 2.0)
    expect_error(409, store.add_frame, state.id, jpeg(), 2.0)
    expect_error(409, store.add_frame, state.id, jpeg(), 1.0)
    assert len(store.get(state.id).frames) == 1


def test_frame_requires_real_jpeg(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    png = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    expect_error(422, store.add_frame, state.id, png, 1.0)
    expect_error(422, store.add_frame, state.id, b"\xff\xd8\xff" + b"\x00" * 64, 1.0)
    expect_error(422, store.add_frame, state.id, b"", 1.0)
    expect_error(422, store.add_frame, state.id, None, 1.0)


def test_frame_sof_dimensions_bounded_before_decode(tmp_path, monkeypatch):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    header = (
        b"\xff\xd8"
        + b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08"
        + (65535).to_bytes(2, "big") + (65535).to_bytes(2, "big")
        + b"\x00" * 10
    )

    def unreachable_decoder(data, flags):
        raise AssertionError("cv2.imdecode reached before the dimension bound")

    monkeypatch.setattr(cv2, "imdecode", unreachable_decoder)
    expect_error(422, store.add_frame, state.id, header, 1.0)


def test_frame_oversized_bytes_rejected(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    expect_error(413, store.add_frame, state.id, jpeg() + b"\x00" * (5 * 1024 * 1024), 1.0)


def test_frame_requires_capturing_status(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    store.end(state.id, 1.0)
    expect_error(409, store.add_frame, state.id, jpeg(), 2.0)


def test_unknown_sweep_raises_not_found(tmp_path):
    store = SweepStore(tmp_path / "runs")
    expect_error(404, store.get, "0" * 32)
    expect_error(404, store.add_frame, "0" * 32, jpeg(), 1.0)
    expect_error(404, store.get, "../../etc")


def test_turn_duplicate_and_validation(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    first = store.add_turn(state.id, "t1", "hello", "typed_user")
    again = store.add_turn(state.id, "t1", "changed", "typed_user")
    assert again.revision == first.revision
    assert len(again.user_turns) == 1
    assert again.user_turns[0].text == "hello"
    expect_error(422, store.add_turn, state.id, "t2", "", "typed_user")
    expect_error(422, store.add_turn, state.id, "t2", "x", "model_output")


def test_exclude_region_once_and_unknowns(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    store.add_turn(state.id, "t1", "exclude", "typed_user")
    from backend.models import CorrectionIntent

    intent = CorrectionIntent(type="exclude_region", target_id="room", value=True, source_turn_id="t1")
    result = store.correct(state.id, intent)
    assert result.applied is True
    repeat = store.correct(state.id, intent)
    assert repeat.applied is False
    assert repeat.revision == result.revision
    expect_error(409, store.correct, state.id, CorrectionIntent(type="exclude_region", target_id="nope", value=None, source_turn_id="t1"))
    expect_error(409, store.correct, state.id, CorrectionIntent(type="exclude_region", target_id="room", value=None, source_turn_id="ghost"))
    expect_error(409, store.correct, state.id, CorrectionIntent(type="assert_edition", target_id="b1", value="x", source_turn_id="t1"))
    loaded = store.get(state.id)
    assert loaded.books == [] and loaded.items == []


def test_end_idempotent_and_interrupt(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    ended = store.end(state.id, 12.5)
    assert ended.status == "partial"
    assert ended.duration_s == 12.5
    assert {item.ref_id for item in ended.review_queue} >= {"extraction", "measurement", "pricing"}
    repeat = store.end(state.id, 99.0)
    assert repeat.duration_s == 12.5
    assert repeat.revision == ended.revision
    expect_error(422, store.end, state.id, -1.0)

    other = store.start("AE", "USD", "dev2")
    interrupted = store.interrupt(other.id)
    assert interrupted.status == "interrupted"
    assert store.interrupt(other.id).status == "interrupted"


def test_corrupted_state_surfaces_error(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    (tmp_path / "runs" / state.id / "state.json").write_text("{not json")
    reloaded = SweepStore(tmp_path / "runs")
    expect_error(500, reloaded.get, state.id)


def test_restore_rejects_id_mismatch(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    import json as _json

    state_file = tmp_path / "runs" / state.id / "state.json"
    raw = _json.loads(state_file.read_text())
    raw["id"] = "f" * 32
    state_file.write_text(_json.dumps(raw))
    reloaded = SweepStore(tmp_path / "runs")
    expect_error(500, reloaded.get, state.id)


def test_restore_rejects_symlinked_sweep(tmp_path):
    source = SweepStore(tmp_path / "other-runs")
    real = source.start("AE", "USD", "dev")
    import json as _json
    import os

    root = tmp_path / "runs"
    outside = tmp_path / "outside" / ("a" * 32)
    outside.mkdir(parents=True)
    raw = _json.loads((tmp_path / "other-runs" / real.id / "state.json").read_text())
    raw["id"] = "a" * 32
    (outside / "state.json").write_text(_json.dumps(raw))
    root.mkdir(exist_ok=True)
    os.symlink(outside, root / ("a" * 32))
    store = SweepStore(root)
    expect_error(500, store.get, "a" * 32)


def test_restore_rejects_bad_frame_records(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    store.add_frame(state.id, jpeg(), 1.0)
    import json as _json

    state_file = tmp_path / "runs" / state.id / "state.json"
    raw = _json.loads(state_file.read_text())
    raw["frames"][0]["frame_ref"] = "../other.jpg"
    state_file.write_text(_json.dumps(raw))
    reloaded = SweepStore(tmp_path / "runs")
    expect_error(500, reloaded.get, state.id)


def test_returned_states_are_snapshots(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    store.add_frame(state.id, jpeg(), 1.0)
    loaded = store.get(state.id)
    loaded.frames.append(loaded.frames[0].model_copy(update={"id": "frame_fake"}))
    loaded.status = "complete"
    loaded.frames[0].timestamp_s = 999.0
    fresh = store.get(state.id)
    assert fresh.status == "capturing"
    assert len(fresh.frames) == 1
    assert fresh.frames[0].timestamp_s == 1.0
