import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.reading import (
    ReadingManager,
    extract_crop_views,
    parse_reading_response,
    read_spines,
    usage_cost_adapter,
    validate_readings,
)
from backend.sweeps import StoreError, SweepStore
from preflight.vision import usage_cost

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "backend" / "reading_contract.json").read_text()
)


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def scene_image(width=640, height=360, seed=7):
    rng = np.random.RandomState(seed)
    image = rng.randint(0, 255, (height, width, 3), dtype=np.uint8)
    return image


def seed_books(store, sweep_id, frame_id, count=2, fragments=None, evidences=None,
               bboxes=None, frame_ref=None):
    ref = frame_ref or f"frames/{frame_id}.jpg"
    payloads = []
    for i in range(count):
        bbox = bboxes[i] if bboxes else {"x": 40 + i * 80, "y": 40, "w": 60, "h": 200}
        if evidences and evidences[i]:
            evidence_list = evidences[i]
        else:
            evidence_list = [
                {
                    "frame_id": frame_id,
                    "frame_ref": ref,
                    "bbox": bbox,
                    "image_width": 640,
                    "image_height": 360,
                    "sharpness": 10.0,
                }
            ]
        payloads.append(
            {
                "frame_ref": ref,
                "status": "unidentified",
                "bbox": bbox,
                "image_width": 640,
                "image_height": 360,
                "observed_fragments": fragments[i] if fragments else [],
                "evidence": evidence_list,
                "potential_duplicate": False,
                "track_histogram": None,
            }
        )
    result = {
        "frame_id": frame_id,
        "alignment": "none",
        "assignments": [],
        "applied_props": [],
        "new_books": payloads,
        "new_items": [],
        "flag_tracks": [],
        "review_items": [],
        "unresolved": count,
    }
    state = store.commit_tracks(sweep_id, frame_id, result)
    return [b.id for b in state.books]


def reading_for(ref_id, title="Real Title", confidence=0.95, author_legible=True):
    return {
        "ref_id": ref_id,
        "title": title,
        "author": "Some Author" if author_legible else "",
        "publisher": "",
        "edition": "",
        "isbn": "",
        "title_legible": bool(title),
        "author_legible": author_legible,
        "confidence": confidence,
        "visible_text": [title] if title else [],
    }


def payload_ref_ids(payload):
    text = payload["input"][0]["content"][0]["text"]
    return json.loads(re.search(r"ref_ids: (\[.*\])", text).group(1))


@pytest.fixture
def store_with_books(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    ids = seed_books(store, state.id, frame.id)
    return store, state, ids


def test_happy_path_identifies_and_persists_crops(store_with_books, tmp_path):
    store, state, ids = store_with_books
    calls = []

    async def provider(payload):
        calls.append(payload)
        return {"readings": [reading_for(rid) for rid in payload_ref_ids(payload)]}

    reply = asyncio.run(read_spines(
        store, state.id, contract=CONTRACT, provider=provider
    ))
    assert reply["batches"] == 1
    assert len(calls) == 1
    loaded = store.get(state.id)
    for book in loaded.books:
        assert book.status == "identified"
        assert book.title == "Real Title"
        assert book.id_confidence == 0.95
        assert len(book.crop_refs) == 3
        assert book.spine_height_cm is None
        assert book.replacement_cost is None
    assert len(loaded.reading_stages) == 1
    stage = loaded.reading_stages[0]
    assert stage.status == "completed"
    stage_file = tmp_path / "runs" / state.id / stage.persisted_ref
    raw = json.loads(stage_file.read_text())
    assert raw["status"] == "completed"
    assert len(raw["readings"]) == 2


def test_missing_ref_id_fails_without_mutating(store_with_books):
    store, state, ids = store_with_books

    async def provider(payload):
        refs = payload_ref_ids(payload)
        return {"readings": [reading_for(refs[0])]}

    with pytest.raises(StoreError) as info:
        asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))
    assert info.value.status_code == 502
    loaded = store.get(state.id)
    assert all(b.status == "unidentified" for b in loaded.books)
    assert loaded.reading_stages[-1].status == "failed"


def test_unknown_and_duplicate_ref_ids_rejected(store_with_books):
    store, state, ids = store_with_books

    async def provider(payload):
        refs = payload_ref_ids(payload)
        return {"readings": [reading_for(refs[0]), reading_for("book_" + "9" * 32)]}

    with pytest.raises(StoreError):
        asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))

    async def provider_dup(payload):
        refs = payload_ref_ids(payload)
        return {"readings": [reading_for(refs[0]), reading_for(refs[0])]}

    with pytest.raises(StoreError):
        asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))
    loaded = store.get(state.id)
    assert all(b.status == "unidentified" for b in loaded.books)


def test_unreadable_book_stays_tracked_and_empty(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    ids = seed_books(store, state.id, frame.id, count=1)

    async def provider(payload):
        return {
            "readings": [
                {
                    "ref_id": payload_ref_ids(payload)[0],
                    "title": "",
                    "author": "",
                    "publisher": "",
                    "edition": "",
                    "isbn": "",
                    "title_legible": False,
                    "author_legible": False,
                    "confidence": 0.2,
                    "visible_text": [],
                }
            ]
        }

    asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))
    loaded = store.get(state.id)
    book = loaded.books[0]
    assert book.status == "unidentified"
    assert book.title == ""
    assert book.isbn == ""
    assert book.identification_evidence[0].status == "unreadable"


def test_scene_crop_contradiction_stays_unpriced(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    ids = seed_books(
        store,
        state.id,
        frame.id,
        count=1,
        fragments=[[{"field": "observed_title", "text": "Wrong Scene Title"}]],
    )

    async def provider(payload):
        return {"readings": [reading_for(payload_ref_ids(payload)[0], title="Actual Work")]}

    asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))
    loaded = store.get(state.id)
    book = loaded.books[0]
    assert book.title == ""
    assert book.status != "identified"
    assert book.identification_evidence[-1].status == "contradicted"
    assert book.observed_fragments[0]["text"] == "Wrong Scene Title"
    assert any("contradicts" in r.reason for r in loaded.review_queue)


def test_scene_byline_plus_title_agrees_with_legible_crop_roles(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    frame = store.add_frame(state.id, jpeg(scene_image()), 1.0)
    ids = seed_books(
        store, state.id, frame.id, count=1,
        fragments=[[{"field": "observed_title", "text": "SOME AUTHOR DEEP SYSTEMS"}]],
    )

    async def provider(payload):
        return {"readings": [reading_for(payload_ref_ids(payload)[0], title="Deep Systems")]}

    asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))
    book = store.get(state.id).books[0]
    assert book.id == ids[0]
    assert book.status == "identified"
    assert book.title == "Deep Systems"
    assert book.author == "Some Author"
    assert book.identification_evidence[-1].status == "accepted"
    assert book.replacement_cost is None


def test_title_without_author_or_catalogue_not_identified(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    seed_books(store, state.id, frame.id, count=1)

    async def provider(payload):
        return {
            "readings": [reading_for(payload_ref_ids(payload)[0], author_legible=False)]
        }

    asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))
    loaded = store.get(state.id)
    book = loaded.books[0]
    assert book.status == "unidentified"
    assert book.title == ""
    assert book.identification_evidence[0].status == "low_confidence"


def test_unknown_target_ref_ids_rejected(store_with_books):
    store, state, ids = store_with_books
    with pytest.raises(StoreError) as info:
        asyncio.run(read_spines(
            store,
            state.id,
            contract=CONTRACT,
            ref_ids=["book_" + "f" * 32],
            provider=lambda payload: None,
        ))
    assert info.value.status_code == 404


def test_no_api_key_returns_503(store_with_books):
    store, state, ids = store_with_books
    with pytest.raises(StoreError) as info:
        asyncio.run(read_spines(store, state.id, contract=CONTRACT, api_key=None))
    assert info.value.status_code == 503


def test_validate_readings_bounds_confidence():
    bad = {"readings": [dict(reading_for("book_a"), confidence=1.5)]}
    with pytest.raises(StoreError):
        validate_readings(bad, ["book_a"])


def test_extract_crop_views_shape_and_padding():
    image = scene_image()
    views = extract_crop_views(
        image, {"x": 40, "y": 40, "w": 60, "h": 200}, CONTRACT["selection"]
    )
    assert set(views) == {
        "original",
        "rotate_90_clockwise",
        "rotate_90_counterclockwise",
    }
    original = cv2.imdecode(np.frombuffer(views["original"], np.uint8), cv2.IMREAD_COLOR)
    cw = cv2.imdecode(np.frombuffer(views["rotate_90_clockwise"], np.uint8), cv2.IMREAD_COLOR)
    assert original.shape[0] >= 200 and original.shape[1] >= 60
    assert cw.shape[0] == original.shape[1]
    assert cw.shape[1] == original.shape[0]


def test_extract_crop_clips_and_bounds_long_edge():
    image = np.zeros((200, 4000, 3), dtype=np.uint8)
    views = extract_crop_views(
        image, {"x": 0, "y": 0, "w": 3900, "h": 180}, CONTRACT["selection"]
    )
    decoded = cv2.imdecode(np.frombuffer(views["original"], np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) <= 1600


def _wire_response(readings, status="completed", model=None):
    return {
        "status": status,
        "model": model or CONTRACT["model"],
        "id": "resp_1",
        "usage": {"input_tokens": 1000, "output_tokens": 100,
                  "input_tokens_details": {"cached_tokens": 200}},
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": json.dumps({"readings": readings})}
                ],
            }
        ],
    }


def _msg(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def test_validate_readings_strict_types():
    base = reading_for("book_a")
    assert validate_readings({"readings": [dict(base)]}, ["book_a"])[0]["ref_id"] == "book_a"
    cases = []
    for mutate in [
        lambda r: r.update(title_legible="false"),
        lambda r: r.update(title_legible=1),
        lambda r: r.update(author_legible=0),
        lambda r: r.update(confidence="high"),
        lambda r: r.update(confidence=True),
        lambda r: r.update(confidence=float("nan")),
        lambda r: r.update(confidence=1.5),
        lambda r: r.update(visible_text="title"),
        lambda r: r.update(visible_text=[1]),
        lambda r: r.update(extra="x"),
        lambda r: r.pop("title"),
        lambda r: r.update(author=12),
        lambda r: r.update(ref_id=""),
    ]:
        entry = dict(base)
        mutate(entry)
        cases.append(entry)
    for entry in cases:
        with pytest.raises(StoreError) as info:
            validate_readings({"readings": [entry]}, ["book_a"])
        assert info.value.status_code == 502
    for root in ([], "x", None, {"readings": "x"}):
        with pytest.raises(StoreError):
            validate_readings(root, ["book_a"])


def test_parse_reading_response_requires_completed_single_text():
    text = json.dumps({"readings": []})
    assert parse_reading_response({"status": "completed", "output": [_msg(text)]}) == {
        "readings": []
    }
    for bad in [
        {"status": "incomplete", "output": [_msg(text)]},
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]},
        {"status": "completed", "output": [_msg(text), _msg(text)]},
        {"status": "completed", "output": [_msg("{not json")]},
        {"status": "completed", "output": [_msg(json.dumps([1]))]},
        "raw string",
        {"status": "completed"},
    ]:
        with pytest.raises(StoreError) as info:
            parse_reading_response(bad)
        assert info.value.status_code == 502


def test_reading_cost_adapter_fixture():
    adapter = usage_cost_adapter(CONTRACT)
    raw = {
        "model": CONTRACT["model"],
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 200},
        },
    }
    assert usage_cost(raw, adapter) is not None
    assert float(usage_cost(raw, adapter)) == pytest.approx(0.001065)
    assert usage_cost({"model": "other", "usage": raw["usage"]}, adapter) is None
    assert usage_cost({"model": CONTRACT["model"]}, adapter) is None
    assert usage_cost({"model": CONTRACT["model"], "usage": {"input_tokens": "x"}}, adapter) is None


def test_best_evidence_crop_selection(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    sharp = scene_image(seed=50)
    blurry = cv2.GaussianBlur(scene_image(seed=51), (15, 15), 0)
    f1 = store.add_frame(state.id, jpeg(sharp), 1.0)
    f2 = store.add_frame(state.id, jpeg(blurry), 2.0)
    evid = [
        [
            {"frame_id": f1.id, "frame_ref": f"frames/{f1.id}.jpg",
             "bbox": {"x": 40, "y": 40, "w": 60, "h": 200},
             "image_width": 640, "image_height": 360, "sharpness": 500.0},
            {"frame_id": f2.id, "frame_ref": f"frames/{f2.id}.jpg",
             "bbox": {"x": 100, "y": 50, "w": 60, "h": 180},
             "image_width": 640, "image_height": 360, "sharpness": 1.0},
        ]
    ]
    seed_books(store, state.id, f2.id, count=1, evidences=evid,
               bboxes=[{"x": 100, "y": 50, "w": 60, "h": 180}])

    async def provider(payload):
        return {"readings": [reading_for(payload_ref_ids(payload)[0])]}

    asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))
    loaded = store.get(state.id)
    book = loaded.books[0]
    stage_file = tmp_path / "runs" / state.id / loaded.reading_stages[0].persisted_ref
    stage = json.loads(stage_file.read_text())
    assert stage["sources"][book.id]["frame_id"] == f1.id
    assert stage["sources"][book.id]["bbox"] == {"x": 40, "y": 40, "w": 60, "h": 200}
    crop_file = tmp_path / "runs" / state.id / book.crop_refs[0]
    expected = extract_crop_views(
        cv2.imdecode(np.frombuffer(jpeg(sharp), np.uint8), cv2.IMREAD_COLOR),
        {"x": 40, "y": 40, "w": 60, "h": 200},
        CONTRACT["selection"],
    )
    assert crop_file.read_bytes() == expected["original"]


def test_best_evidence_tie_and_missing_sharpness_pick_first(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img1 = scene_image(seed=60)
    img2 = scene_image(seed=61)
    f1 = store.add_frame(state.id, jpeg(img1), 1.0)
    f2 = store.add_frame(state.id, jpeg(img2), 2.0)

    def evidence(frame, sharpness, x):
        ev = {"frame_id": frame.id, "frame_ref": f"frames/{frame.id}.jpg",
              "bbox": {"x": x, "y": 40, "w": 60, "h": 200},
              "image_width": 640, "image_height": 360}
        if sharpness is not None:
            ev["sharpness"] = sharpness
        return ev

    async def provider(payload):
        return {"readings": [reading_for(r) for r in payload_ref_ids(payload)]}

    seed_books(
        store, state.id, f2.id, count=1,
        evidences=[[evidence(f1, 10.0, 40), evidence(f2, 10.0, 100)]],
        bboxes=[{"x": 100, "y": 40, "w": 60, "h": 200}],
    )
    asyncio.run(read_spines(store, state.id, contract=CONTRACT, provider=provider))
    loaded = store.get(state.id)
    stage_file = tmp_path / "runs" / state.id / loaded.reading_stages[0].persisted_ref
    stage = json.loads(stage_file.read_text())
    assert stage["sources"][loaded.books[0].id]["frame_id"] == f1.id

    seed_books(
        store, state.id, f2.id, count=1,
        evidences=[[evidence(f1, None, 40), evidence(f2, None, 100)]],
        bboxes=[{"x": 100, "y": 40, "w": 60, "h": 200}],
    )
    unresolved = [b for b in store.get(state.id).books if b.status != "identified"]
    asyncio.run(read_spines(
        store, state.id, contract=CONTRACT, provider=provider,
        ref_ids=[b.id for b in unresolved]))
    loaded = store.get(state.id)
    stage_file = tmp_path / "runs" / state.id / loaded.reading_stages[-1].persisted_ref
    stage = json.loads(stage_file.read_text())
    target = [b for b in loaded.books if b.id in [u.id for u in unresolved]][0]
    assert stage["sources"][target.id]["frame_id"] == f1.id


def test_crop_views_no_upscale(tmp_path):
    image = scene_image(width=640, height=360, seed=70)
    views = extract_crop_views(
        image, {"x": 10, "y": 10, "w": 30, "h": 40}, CONTRACT["selection"]
    )
    original = cv2.imdecode(
        np.frombuffer(views["original"], np.uint8), cv2.IMREAD_COLOR
    )
    assert original.shape == (44, 34, 3)
    rotated = cv2.imdecode(
        np.frombuffer(views["rotate_90_clockwise"], np.uint8), cv2.IMREAD_COLOR
    )
    assert rotated.shape == (34, 44, 3)


def _run(coro):
    return asyncio.run(coro)


def test_manager_rejects_concurrent_same_track(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    ids = seed_books(store, state.id, frame.id, count=2)
    manager = ReadingManager(CONTRACT)

    async def scenario():
        release = asyncio.Event()

        async def provider(payload):
            await release.wait()
            return {"readings": [reading_for(r) for r in payload_ref_ids(payload)]}

        first = asyncio.create_task(
            read_spines(store, state.id, contract=CONTRACT, ref_ids=ids[:1],
                        provider=provider, manager=manager)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        with pytest.raises(StoreError) as info:
            await read_spines(store, state.id, contract=CONTRACT, ref_ids=ids[:1],
                              provider=provider, manager=manager)
        assert info.value.status_code == 409
        release.set()
        await first

    _run(scenario())


def test_manager_concurrency_and_batch_cap(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    ids = seed_books(store, state.id, frame.id, count=3)
    manager = ReadingManager(CONTRACT)
    in_flight = {"count": 0, "max": 0}

    async def scenario():
        release = asyncio.Event()

        async def provider(payload):
            in_flight["count"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["count"])
            await release.wait()
            in_flight["count"] -= 1
            return {"readings": [reading_for(r) for r in payload_ref_ids(payload)]}

        tasks = [
            asyncio.create_task(read_spines(
                store, state.id, contract=CONTRACT, ref_ids=[rid],
                provider=provider, manager=manager))
            for rid in ids
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert in_flight["max"] <= 2
        release.set()
        await asyncio.gather(*tasks)

    _run(scenario())


def test_manager_batch_cap_counts_inflight_and_persisted(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    from backend.models import ReadingStageRecord

    for _ in range(12):
        store.record_reading_stage(
            state.id,
            ReadingStageRecord(ref_ids=["book_x"], status="failed", error="x"),
        )
    ids = seed_books(store, state.id, frame.id, count=1)
    manager = ReadingManager(CONTRACT)

    async def provider(payload):
        raise AssertionError("provider must not be called")

    with pytest.raises(StoreError) as info:
        _run(read_spines(store, state.id, contract=CONTRACT, provider=provider,
                         manager=manager))
    assert info.value.status_code == 429
    loaded = store.get(state.id)
    assert all(b.status == "unidentified" for b in loaded.books)
    assert any("not read" in r.reason or "budget" in r.reason for r in loaded.review_queue)


def test_manager_deadline_fixed_once(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    seed_books(store, state.id, frame.id, count=2)
    manager = ReadingManager(CONTRACT)

    async def provider(payload):
        return {"readings": [reading_for(r) for r in payload_ref_ids(payload)]}

    _run(read_spines(store, state.id, contract=CONTRACT, ref_ids=None,
                     provider=provider, manager=manager))
    loaded = store.get(state.id)
    assert loaded.reading_deadline_at is not None
    persisted = loaded.reading_deadline_at
    first = _run(manager.deadline(store, state.id))

    reloaded = SweepStore(tmp_path / "runs")
    manager2 = ReadingManager(CONTRACT)
    dl2 = _run(manager2.deadline(reloaded, state.id))
    assert dl2 <= first + 1.0
    assert store.get(state.id).reading_deadline_at == persisted


def test_persisted_deadline_survives_restart_and_blocks_provider(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    ids = seed_books(store, state.id, frame.id, count=1)
    wall = [datetime.now(timezone.utc)]
    calls = []

    async def provider(payload):
        calls.append(payload)
        return {"readings": [reading_for(r) for r in payload_ref_ids(payload)]}

    manager = ReadingManager(CONTRACT, wallclock=lambda: wall[0])
    _run(read_spines(store, state.id, contract=CONTRACT, ref_ids=ids,
                     provider=provider, manager=manager))
    assert len(calls) == 1

    wall[0] = wall[0] + timedelta(seconds=300)
    reloaded = SweepStore(tmp_path / "runs")
    manager2 = ReadingManager(CONTRACT, wallclock=lambda: wall[0])
    with pytest.raises(StoreError) as info:
        _run(read_spines(reloaded, state.id, contract=CONTRACT, ref_ids=ids,
                         provider=provider, manager=manager2))
    assert info.value.status_code == 429
    assert len(calls) == 1


def test_deadline_expired_while_waiting_on_semaphore_blocks_provider(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    ids = seed_books(store, state.id, frame.id, count=2)
    wall = [datetime.now(timezone.utc)]
    manager = ReadingManager(CONTRACT, wallclock=lambda: wall[0])
    calls = []

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def provider(payload):
            calls.append(payload)
            entered.set()
            await release.wait()
            return {"readings": [reading_for(r) for r in payload_ref_ids(payload)]}

        first = asyncio.create_task(
            read_spines(store, state.id, contract=CONTRACT, ref_ids=ids[:1],
                        provider=provider, manager=manager)
        )
        await asyncio.wait_for(entered.wait(), 5)
        wall[0] = wall[0] + timedelta(seconds=300)
        try:
            with pytest.raises(StoreError) as info:
                await read_spines(store, state.id, contract=CONTRACT,
                                  ref_ids=ids[1:], provider=provider,
                                  manager=manager)
            assert info.value.status_code == 429
        finally:
            release.set()
            await first

    _run(scenario())
    assert len(calls) == 1


def test_monotonic_deadline_blocks_provider_when_wallclock_unchanged(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    ids = seed_books(store, state.id, frame.id, count=2)
    now = [0.0]
    manager = ReadingManager(CONTRACT, clock=lambda: now[0])
    calls = []

    async def provider(payload):
        calls.append(payload)
        return {"readings": [reading_for(r) for r in payload_ref_ids(payload)]}

    _run(read_spines(store, state.id, contract=CONTRACT, ref_ids=ids[:1],
                     provider=provider, manager=manager))
    assert len(calls) == 1
    now[0] += 500.0
    with pytest.raises(StoreError) as info:
        _run(read_spines(store, state.id, contract=CONTRACT, ref_ids=ids[1:],
                         provider=provider, manager=manager))
    assert info.value.status_code == 429
    assert len(calls) == 1


def test_running_stage_restored_as_interrupted_and_counts(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    from backend.models import ReadingStageRecord

    store.record_reading_stage(
        state.id, ReadingStageRecord(ref_ids=["book_x"], status="running")
    )
    seed_books(store, state.id, frame.id, count=1)
    reloaded = SweepStore(tmp_path / "runs")
    loaded = reloaded.get(state.id)
    assert loaded.reading_stages[0].status == "interrupted"
    for _ in range(11):
        reloaded.record_reading_stage(
            state.id, ReadingStageRecord(ref_ids=["b"], status="failed", error="x")
        )
    manager = ReadingManager(CONTRACT)

    async def provider(payload):
        raise AssertionError("must not call")

    with pytest.raises(StoreError) as info:
        _run(read_spines(reloaded, state.id, contract=CONTRACT, provider=provider,
                         manager=manager))
    assert info.value.status_code == 429
