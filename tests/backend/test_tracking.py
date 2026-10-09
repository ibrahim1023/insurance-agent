import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.models import CorrectionIntent
from backend.sweeps import StoreError, SweepStore
from backend.tracking import associate_observation, commit_observation

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "backend" / "tracking_contract.json").read_text()
)


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def scene_image(width=640, height=360, seed=7, dx=0, dy=0):
    rng = np.random.RandomState(seed)
    image = rng.randint(0, 255, (height, width, 3), dtype=np.uint8)
    image[300:, :] = (70, 70, 70)
    for i in range(3):
        x = 60 + i * 120
        image[80:280, x : x + 60] = (
            (seed * 40 + i * 60) % 255,
            (seed * 90 + i * 30) % 255,
            (seed * 20 + i * 80) % 255,
        )
        image[100:150, x + 10 : x + 50] = rng.randint(0, 255, (50, 40, 3))
    if dx or dy:
        shift = np.float32([[1, 0, dx], [0, 1, dy]])
        image = cv2.warpAffine(image, shift, (width, height))
    return image


def book_scene(index, box, title=""):
    return {
        "observed_title": title,
        "observed_author": "",
        "observed_publisher": "",
        "observed_edition": "",
        "observed_isbn": "",
        "title_legible": bool(title),
        "author_legible": False,
        "existence_confidence": 0.95,
        "text_confidence": 0.9 if title else 0.0,
        "orientation": "upright",
        "special_flags": [],
        "_scene_index": index,
        "_box": box,
    }


def observation(frame_id, entries, width, height):
    books = [e for e in entries if e.get("_kind", "book") == "book"]
    items = [e for e in entries if e.get("_kind") == "item"]
    scene_books = []
    boxes = []
    for e in books:
        book = {k: v for k, v in e.items() if not k.startswith("_")}
        scene_books.append(book)
        boxes.append(list(e["_box"]))
    scene = {"books": scene_books, "items": []}
    for e in items:
        item = {k: v for k, v in e.items() if not k.startswith("_")}
        scene["items"].append(item)
    return {
        "frame_id": frame_id,
        "observation": scene,
        "box_mapping": {
            "boxes": boxes,
            "accepted_indices": list(range(len(boxes))),
            "proposal_count": len(boxes),
        },
        "image_width": width,
        "image_height": height,
        "sent_image_width": width,
        "sent_image_height": height,
        "coordinate_system": "pixels",
    }


def item_entry(box, category="shelving", confidence=0.99):
    return {
        "_kind": "item",
        "category": category,
        "description": "wooden shelf",
        "material": "",
        "brand_model": "",
        "confidence": confidence,
        "bbox": {
            "left": int(box[0]),
            "top": int(box[1]),
            "right": int(box[0] + box[2]),
            "bottom": int(box[1] + box[3]),
        },
    }


def as_tracks(payloads, prefix="book"):
    tracks = []
    for i, payload in enumerate(payloads):
        track = dict(payload)
        track["id"] = f"{prefix}_{i:032x}"
        track.setdefault("title", "")
        track.setdefault("identification_evidence", [])
        tracks.append(track)
    return tracks


def test_same_image_reuses_track_ids():
    img1 = scene_image(seed=3)
    box = (60, 80, 60, 200)
    obs1 = observation("f" + "1" * 31, [book_scene(0, box)], 640, 360)
    r1 = associate_observation(None, jpeg(img1), {"books": [], "items": []}, obs1, CONTRACT)
    existing = as_tracks(r1["new_books"])
    obs2 = observation("f" + "2" * 31, [book_scene(0, box)], 640, 360)
    r2 = associate_observation(
        {"jpeg": jpeg(img1), "frame_id": "f" + "1" * 31},
        jpeg(img1),
        {"books": existing, "items": []},
        obs2,
        CONTRACT,
    )
    assert r2["alignment"] == "identical"
    assert r2["new_books"] == []
    assert r2["assignments"][0]["track_id"] == existing[0]["id"]


def test_identical_title_copies_stay_separate(tmp_path):
    img = scene_image(seed=5)
    entries = [
        book_scene(0, (60, 80, 60, 200), title="Identical Title"),
        book_scene(1, (180, 80, 60, 200), title="Identical Title"),
    ]
    obs1 = observation("f" + "1" * 31, entries, 640, 360)
    r1 = associate_observation(None, jpeg(img), {"books": [], "items": []}, obs1, CONTRACT)
    assert len(r1["new_books"]) == 2
    existing = as_tracks(r1["new_books"])
    r2 = associate_observation(
        {"jpeg": jpeg(img), "frame_id": "f" + "1" * 31},
        jpeg(img),
        {"books": existing, "items": []},
        observation("f" + "2" * 31, entries, 640, 360),
        CONTRACT,
    )
    assigned = {a["track_id"] for a in r2["assignments"]}
    assert assigned == {existing[0]["id"], existing[1]["id"]}
    assert r2["new_books"] == []


def test_known_translation_preserves_ids():
    img1 = scene_image(seed=9)
    img2 = scene_image(seed=9, dx=12, dy=4)
    box1 = (60, 80, 60, 200)
    obs1 = observation("f" + "1" * 31, [book_scene(0, box1)], 640, 360)
    r1 = associate_observation(None, jpeg(img1), {"books": [], "items": []}, obs1, CONTRACT)
    existing = as_tracks(r1["new_books"])
    box2 = (72, 84, 60, 200)
    obs2 = observation("f" + "2" * 31, [book_scene(0, box2)], 640, 360)
    r2 = associate_observation(
        {"jpeg": jpeg(img1), "frame_id": "f" + "1" * 31},
        jpeg(img2),
        {"books": existing, "items": []},
        obs2,
        CONTRACT,
    )
    assert r2["alignment"] == "homography"
    assert r2["assignments"][0]["track_id"] == existing[0]["id"]
    assert r2["new_books"] == []


def test_omitted_leading_article_does_not_split_a_visual_book():
    image = jpeg(scene_image(seed=93))
    first = observation(
        "f" + "1" * 31,
        [book_scene(0, (60, 80, 60, 200), "The Green Novel")],
        640,
        360,
    )
    original = associate_observation(None, image, {"books": [], "items": []}, first, CONTRACT)
    second = observation(
        "f" + "2" * 31,
        [book_scene(0, (60, 80, 60, 200), "Green Novel")],
        640,
        360,
    )
    track = as_tracks(original["new_books"])[0]
    result = associate_observation(
        {"jpeg": image, "frame_id": first["frame_id"]},
        image,
        {"books": [track], "items": []},
        second,
        CONTRACT,
    )
    assert [assignment["track_id"] for assignment in result["assignments"]] == [track["id"]]
    assert result["new_books"] == []


def test_book_reappearing_after_bookless_analyzed_frame_reuses_track(tmp_path):
    store = SweepStore(tmp_path / "runs")
    sweep = store.start("AE", "AED", "three-view replay")
    books_image = jpeg(scene_image(seed=9))
    book = book_scene(0, (60, 80, 60, 200), "Known Book")
    first = store.add_frame(sweep.id, books_image, 1.0)
    first_state = commit_observation(
        store,
        sweep.id,
        first.id,
        observation(first.id, [book], 640, 360),
        books_image,
        CONTRACT,
    )
    blank = jpeg(np.zeros((360, 640, 3), dtype=np.uint8))
    middle = store.add_frame(sweep.id, blank, 2.0)
    commit_observation(store, sweep.id, middle.id, observation(middle.id, [], 640, 360), blank, CONTRACT)
    last = store.add_frame(sweep.id, books_image, 3.0)
    final = commit_observation(
        store,
        sweep.id,
        last.id,
        observation(last.id, [book], 640, 360),
        books_image,
        CONTRACT,
    )
    assert len(final.books) == 1
    assert final.books[0].id == first_state.books[0].id
    assert {e.frame_id for e in final.books[0].evidence} == {first.id, last.id}


def test_spine_features_preserve_track_when_lighting_changes_color_histogram():
    first_image = scene_image(seed=9)
    cv2.putText(first_image, "BOOK", (63, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.putText(first_image, "NAME", (62, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (20, 20, 20), 2)
    gray = cv2.cvtColor(first_image, cv2.COLOR_BGR2GRAY)
    second_image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    box = (60, 80, 60, 200)
    first = observation("f" + "1" * 31, [book_scene(0, box, "Same Printed Book")], 640, 360)
    recorded = associate_observation(None, jpeg(first_image), {"books": [], "items": []}, first, CONTRACT)
    existing = as_tracks(recorded["new_books"])
    next_view = observation("f" + "2" * 31, [book_scene(0, box, "Same Printed Book")], 640, 360)
    result = associate_observation(
        {"jpeg": jpeg(first_image), "frame_id": first["frame_id"]},
        jpeg(second_image),
        {"books": existing, "items": []},
        next_view,
        CONTRACT,
    )
    assert [assignment["track_id"] for assignment in result["assignments"]] == [existing[0]["id"]]
    assert result["new_books"] == []


def test_conflicting_titles_do_not_merge():
    img = scene_image(seed=11)
    box = (60, 80, 60, 200)
    obs1 = observation("f" + "1" * 31, [book_scene(0, box, "Alpha Title")], 640, 360)
    r1 = associate_observation(None, jpeg(img), {"books": [], "items": []}, obs1, CONTRACT)
    existing = as_tracks(r1["new_books"])
    obs2 = observation("f" + "2" * 31, [book_scene(0, box, "Different Title")], 640, 360)
    r2 = associate_observation(
        {"jpeg": jpeg(img), "frame_id": "f" + "1" * 31},
        jpeg(img),
        {"books": existing, "items": []},
        obs2,
        CONTRACT,
    )
    assert r2["assignments"] == []
    assert len(r2["new_books"]) == 1


def test_scene_title_case_and_spacing_do_not_split_visual_track():
    image = jpeg(scene_image(seed=11))
    box = (60, 80, 60, 200)
    first = observation("f" + "1" * 31, [book_scene(0, box, "The Love Hypothesis")], 640, 360)
    original = associate_observation(None, image, {"books": [], "items": []}, first, CONTRACT)
    existing = as_tracks(original["new_books"])
    second = observation("f" + "2" * 31, [book_scene(0, box, " the  LOVE hypothesis ")], 640, 360)
    result = associate_observation(
        {"jpeg": image, "frame_id": first["frame_id"]},
        image,
        {"books": existing, "items": []},
        second,
        CONTRACT,
    )
    assert [a["track_id"] for a in result["assignments"]] == [existing[0]["id"]]
    assert result["new_books"] == []


def test_failed_alignment_marks_unresolved_review():
    img1 = scene_image(seed=13)
    blank = np.zeros((360, 640, 3), dtype=np.uint8)
    box = (60, 80, 60, 200)
    obs1 = observation("f" + "1" * 31, [book_scene(0, box)], 640, 360)
    r1 = associate_observation(None, jpeg(img1), {"books": [], "items": []}, obs1, CONTRACT)
    existing = as_tracks(r1["new_books"])
    obs2 = observation("f" + "2" * 31, [book_scene(0, box)], 640, 360)
    r2 = associate_observation(
        {"jpeg": jpeg(blank), "frame_id": "f" + "1" * 31},
        jpeg(img1),
        {"books": existing, "items": []},
        obs2,
        CONTRACT,
    )
    assert r2["alignment"] == "failed"
    assert r2["assignments"] == []
    assert r2["new_books"][0]["potential_duplicate"] is True
    assert any("alignment" in r["reason"] for r in r2["review_items"])


def test_ambiguous_candidates_not_forced():
    img = scene_image(seed=17)
    box = (60, 80, 60, 200)
    obs1 = observation("f" + "1" * 31, [book_scene(0, box)], 640, 360)
    r1 = associate_observation(None, jpeg(img), {"books": [], "items": []}, obs1, CONTRACT)
    existing = as_tracks(r1["new_books"])
    twin = dict(existing[0])
    twin["id"] = "book_" + "e" * 32
    existing.append(twin)
    obs2 = observation("f" + "2" * 31, [book_scene(0, box)], 640, 360)
    r2 = associate_observation(
        {"jpeg": jpeg(img), "frame_id": "f" + "1" * 31},
        jpeg(img),
        {"books": existing, "items": []},
        obs2,
        CONTRACT,
    )
    assert r2["assignments"] == []
    assert set(r2["flag_tracks"]) == {existing[0]["id"], twin["id"]}
    assert r2["new_books"][0]["potential_duplicate"] is True


def test_commit_tracks_assigns_shelf_and_positions(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=21)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(
        frame.id,
        [
            item_entry((20, 40, 480, 300)),
            book_scene(0, (60, 80, 60, 200)),
            book_scene(1, (180, 80, 60, 200)),
        ],
        640,
        360,
    )
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    assert len(committed.items) == 1
    shelf = committed.items[0]
    assert shelf.category == "shelving"
    assert len(committed.books) == 2
    positions = sorted(b.position for b in committed.books)
    assert positions == [1, 2]
    assert all(b.shelf == shelf.id for b in committed.books)
    for book in committed.books:
        assert book.status == "unidentified"
        assert book.title == ""
        assert book.spine_height_cm is None
        assert book.spine_thickness_cm is None
        assert book.replacement_cost is None
        assert book.used_value is None
        assert book.isbn == ""


def test_book_without_shelf_is_unassigned(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=22)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(frame.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    assert committed.books[0].shelf is None
    assert committed.books[0].shelf_reason


def test_exclusions_survive_delayed_commit(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=23)
    frame1 = store.add_frame(state.id, jpeg(img), 1.0)
    obs1 = observation(
        frame1.id,
        [item_entry((20, 40, 480, 300)), book_scene(0, (60, 80, 60, 200))],
        640,
        360,
    )
    committed = commit_observation(store, state.id, frame1.id, obs1, jpeg(img), CONTRACT)
    shelf_id = committed.items[0].id
    book_id = committed.books[0].id
    store.add_turn(state.id, "t1", "exclude that shelf", "typed_user")
    result = store.correct(
        state.id,
        CorrectionIntent(
            type="exclude_region", target_id=shelf_id, value=True, source_turn_id="t1"
        ),
    )
    assert result.applied is True
    frame2 = store.add_frame(state.id, jpeg(img), 2.0)
    obs2 = observation(
        frame2.id,
        [item_entry((20, 40, 480, 300)), book_scene(0, (60, 80, 60, 200)), book_scene(1, (300, 80, 60, 200))],
        640,
        360,
    )
    after = commit_observation(store, state.id, frame2.id, obs2, jpeg(img), CONTRACT)
    book = next(b for b in after.books if b.id == book_id)
    assert book.excluded is True
    new_book = next(b for b in after.books if b.id != book_id)
    assert new_book.excluded is True
    shelf = after.items[0]
    assert shelf.excluded is True


def test_same_jpeg_committed_twice_keeps_ids(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=31)
    frame1 = store.add_frame(state.id, jpeg(img), 1.0)
    obs1 = observation(frame1.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    first = commit_observation(store, state.id, frame1.id, obs1, jpeg(img), CONTRACT)
    frame2 = store.add_frame(state.id, jpeg(img), 2.0)
    obs2 = observation(frame2.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    second = commit_observation(store, state.id, frame2.id, obs2, jpeg(img), CONTRACT)
    assert len(second.books) == 1
    assert second.books[0].id == first.books[0].id
    assert len(second.books[0].evidence) == 2


def test_exclude_item_targets_books_and_items(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=41)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(
        frame.id,
        [book_scene(0, (60, 80, 60, 200)), item_entry((20, 40, 480, 300), category="decor")],
        640,
        360,
    )
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    book_id = committed.books[0].id
    item_id = committed.items[0].id
    store.add_turn(state.id, "t1", "exclude these", "typed_user")
    r1 = store.correct(
        state.id,
        CorrectionIntent(type="exclude_item", target_id=book_id, value=True, source_turn_id="t1"),
    )
    r2 = store.correct(
        state.id,
        CorrectionIntent(type="exclude_item", target_id=item_id, value=True, source_turn_id="t1"),
    )
    assert r1.applied and r2.applied
    loaded = store.get(state.id)
    assert loaded.books[0].excluded is True
    assert loaded.items[0].excluded is True
    with pytest.raises(StoreError) as info:
        store.correct(
            state.id,
            CorrectionIntent(type="exclude_item", target_id="book_missing", value=True, source_turn_id="t1"),
        )
    assert info.value.status_code == 409


def test_edition_assertion_records_claim_not_verification(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=43)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(frame.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    book_id = committed.books[0].id
    store.add_turn(state.id, "t1", "it is a first edition", "typed_user")
    result = store.correct(
        state.id,
        CorrectionIntent(type="assert_edition", target_id=book_id, value="first edition", source_turn_id="t1"),
    )
    assert result.applied is True
    loaded = store.get(state.id)
    book = loaded.books[0]
    assert book.edition == ""
    assert book.assertions[0].type == "assert_edition"
    assert book.assertions[0].needs_review is False
    negated = store.correct(
        state.id,
        CorrectionIntent(type="assert_edition", target_id=book_id, value="not a first edition", source_turn_id="t1"),
    )
    assert negated.applied is True
    loaded = store.get(state.id)
    assert loaded.books[0].assertions[1].needs_review is True
    assert any("edition" in r.reason for r in loaded.review_queue)


def test_art_print_assertion_flow(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=47)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(
        frame.id,
        [item_entry((300, 20, 200, 150), category="art")],
        640,
        360,
    )
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    art = committed.items[0]
    assert art.status == "needs_appraisal"
    store.add_turn(state.id, "t1", "that is a print", "typed_user")
    unclear = store.correct(
        state.id,
        CorrectionIntent(type="assert_art_print", target_id=art.id, value="not sure if it's a print", source_turn_id="t1"),
    )
    loaded = store.get(state.id)
    assert loaded.items[0].status == "needs_appraisal"
    assert loaded.items[0].assertions[0].needs_review is True
    affirmed = store.correct(
        state.id,
        CorrectionIntent(type="assert_art_print", target_id=art.id, value="it is a reproduction print", source_turn_id="t1"),
    )
    loaded = store.get(state.id)
    assert loaded.items[0].status == "range"
    assert loaded.items[0].valuation_state == "unpriced"
    assert loaded.items[0].assertions[1].needs_review is False


def test_same_frame_reanalysis_does_not_duplicate(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=61)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(frame.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    first = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    again = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    assert len(again.books) == 1
    assert again.books[0].id == first.books[0].id
    assert len(again.books[0].evidence) == 1


def test_item_category_must_match_to_associate():
    img = scene_image(seed=67)
    obs1 = observation(
        "f" + "1" * 31,
        [item_entry((20, 40, 480, 300), category="shelving")],
        640, 360,
    )
    r1 = associate_observation(None, jpeg(img), {"books": [], "items": []}, obs1, CONTRACT)
    existing = as_tracks(r1["new_items"], prefix="item")
    obs2 = observation(
        "f" + "2" * 31,
        [item_entry((20, 40, 480, 300), category="rug")],
        640, 360,
    )
    r2 = associate_observation(
        {"jpeg": jpeg(img), "frame_id": "f" + "1" * 31},
        jpeg(img),
        {"books": [], "items": existing},
        obs2,
        CONTRACT,
    )
    assert r2["assignments"] == []
    assert len(r2["new_items"]) == 1


def test_conflicting_title_flags_both_sides(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=71)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs1 = observation(frame.id, [book_scene(0, (60, 80, 60, 200), "Alpha Title")], 640, 360)
    first = commit_observation(store, state.id, frame.id, obs1, jpeg(img), CONTRACT)
    frame2 = store.add_frame(state.id, jpeg(img), 2.0)
    obs2 = observation(frame2.id, [book_scene(0, (60, 80, 60, 200), "Different Title")], 640, 360)
    second = commit_observation(store, state.id, frame2.id, obs2, jpeg(img), CONTRACT)
    assert len(second.books) == 2
    old = next(b for b in second.books if b.id == first.books[0].id)
    new = next(b for b in second.books if b.id != first.books[0].id)
    assert old.potential_duplicate is True
    assert new.potential_duplicate is True
    assert any("title" in r.reason or "conflict" in r.reason for r in second.review_queue)


def test_special_flags_promote_matched_track(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=73)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs1 = observation(frame.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    first = commit_observation(store, state.id, frame.id, obs1, jpeg(img), CONTRACT)
    assert first.books[0].status == "unidentified"
    frame2 = store.add_frame(state.id, jpeg(img), 2.0)
    flagged = book_scene(0, (60, 80, 60, 200))
    flagged["special_flags"] = ["possible_signed"]
    obs2 = observation(frame2.id, [flagged], 640, 360)
    second = commit_observation(store, state.id, frame2.id, obs2, jpeg(img), CONTRACT)
    assert len(second.books) == 1
    assert second.books[0].id == first.books[0].id
    assert second.books[0].status == "needs_appraisal"
    assert second.books[0].replacement_cost is None


def test_shelf_membership_only_same_frame(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img1 = scene_image(seed=79)
    frame1 = store.add_frame(state.id, jpeg(img1), 1.0)
    obs1 = observation(
        frame1.id,
        [item_entry((20, 40, 480, 300)), book_scene(0, (60, 80, 60, 200))],
        640, 360,
    )
    first = commit_observation(store, state.id, frame1.id, obs1, jpeg(img1), CONTRACT)
    shelf1 = first.items[0].id
    book1 = first.books[0].id
    img2 = scene_image(seed=83)
    frame2 = store.add_frame(state.id, jpeg(img2), 2.0)
    obs2 = observation(
        frame2.id,
        [item_entry((0, 0, 640, 360)), book_scene(0, (60, 80, 60, 200))],
        640, 360,
    )
    second = commit_observation(store, state.id, frame2.id, obs2, jpeg(img2), CONTRACT)
    kept = next(b for b in second.books if b.id == book1)
    assert kept.shelf == shelf1
    new_books = [b for b in second.books if b.id != book1]
    assert len(new_books) == 1
    assert new_books[0].shelf == second.items[-1].id


def test_partial_shelf_view_unresolves_positions(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=21)
    frame1 = store.add_frame(state.id, jpeg(img), 1.0)
    obs1 = observation(
        frame1.id,
        [item_entry((20, 40, 480, 300)),
         book_scene(0, (60, 80, 60, 200)),
         book_scene(1, (180, 80, 60, 200))],
        640, 360,
    )
    first = commit_observation(store, state.id, frame1.id, obs1, jpeg(img), CONTRACT)
    shelf_id = first.items[0].id
    book1, book2 = first.books[0].id, first.books[1].id
    assert sorted(b.position for b in first.books) == [1, 2]

    frame2 = store.add_frame(state.id, jpeg(img), 2.0)
    obs2 = observation(
        frame2.id,
        [item_entry((20, 40, 480, 300)), book_scene(1, (180, 80, 60, 200))],
        640, 360,
    )
    second = commit_observation(store, state.id, frame2.id, obs2, jpeg(img), CONTRACT)
    b1 = next(b for b in second.books if b.id == book1)
    b2 = next(b for b in second.books if b.id == book2)
    assert b1.position == 1
    assert b1.shelf == shelf_id
    assert b2.position is None
    assert b2.shelf == shelf_id
    assert any(
        r.ref_id == book2 and "incomplete same-frame view" in r.reason
        for r in second.review_queue
    )

    frame3 = store.add_frame(state.id, jpeg(img), 3.0)
    obs3 = observation(
        frame3.id,
        [item_entry((20, 40, 480, 300)),
         book_scene(1, (180, 80, 60, 200)),
         book_scene(2, (300, 80, 60, 200))],
        640, 360,
    )
    third = commit_observation(store, state.id, frame3.id, obs3, jpeg(img), CONTRACT)
    b1 = next(b for b in third.books if b.id == book1)
    b2 = next(b for b in third.books if b.id == book2)
    new_book = next(b for b in third.books if b.id not in (book1, book2))
    assert b1.position == 1
    assert b2.position is None
    assert new_book.position is None
    assert any(
        r.ref_id == new_book.id and "incomplete same-frame view" in r.reason
        for r in third.review_queue
    )


def test_room_exclusion_applies_immediately(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=89)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(
        frame.id,
        [book_scene(0, (60, 80, 60, 200)), item_entry((20, 40, 480, 300), category="decor")],
        640, 360,
    )
    commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    store.add_turn(state.id, "t1", "exclude everything", "typed_user")
    store.correct(
        state.id,
        CorrectionIntent(type="exclude_region", target_id="room", value=True, source_turn_id="t1"),
    )
    loaded = store.get(state.id)
    assert all(b.excluded for b in loaded.books)
    assert all(i.excluded for i in loaded.items)


def test_end_appends_pending_reviews_deduped(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=97)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(frame.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    store.add_turn(state.id, "t1", "note", "typed_user")
    store.correct(
        state.id,
        CorrectionIntent(type="assert_edition", target_id=committed.books[0].id,
                         value="not sure", source_turn_id="t1"),
    )
    before = len(store.get(state.id).review_queue)
    ended = store.end(state.id, 3.0)
    reasons = {(r.ref_id, r.reason) for r in ended.review_queue}
    assert len(ended.review_queue) == len(reasons)
    assert len(ended.review_queue) == before + 3


def test_apply_readings_second_conflict_clears_identity(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=101)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    from tests.backend.test_reading import seed_books, reading_for

    ids = seed_books(store, state.id, frame.id, count=1)
    book_id = ids[0]
    store.apply_readings(state.id, {book_id: "stage_outputs/r1.json"},
                         [reading_for(book_id, title="First Work")])
    identified = store.get(state.id).books[0]
    assert identified.status == "identified"
    store.apply_readings(state.id, {book_id: "stage_outputs/r2.json"},
                         [reading_for(book_id, title="Different Work")])
    loaded = store.get(state.id).books[0]
    assert loaded.status == "unidentified"
    assert loaded.title == ""
    assert loaded.author == ""
    assert loaded.id_confidence is None
    assert len(loaded.identification_evidence) == 2
    assert loaded.identification_evidence[-1].status == "contradicted"
    assert any("contradict" in r.reason for r in store.get(state.id).review_queue)


def test_apply_readings_catalogue_resolves_scene_conflict(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=103)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    from tests.backend.test_reading import seed_books, reading_for

    ids = seed_books(
        store, state.id, frame.id, count=1,
        fragments=[[{"field": "observed_title", "text": "Wrong Scene Title"}]],
    )
    book_id = ids[0]
    catalogue = {
        book_id: {
            "source": "openlibrary",
            "url": "https://openlibrary.org/works/OL1",
            "retrieved_at": "2026-10-07T00:00:00+00:00",
            "matched_title": "Actual Work",
            "matched_author": "Some Author",
        }
    }
    store.apply_readings(
        state.id, {book_id: "stage_outputs/r1.json"},
        [reading_for(book_id, title="Actual Work")], catalogue=catalogue,
    )
    book = store.get(state.id).books[0]
    assert book.status == "identified"
    assert book.title == "Actual Work"
    assert book.isbn == ""


def test_apply_readings_low_confidence_gets_review(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image()
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    from tests.backend.test_reading import seed_books, reading_for

    ids = seed_books(store, state.id, frame.id, count=1)
    store.apply_readings(state.id, {ids[0]: "s"}, [reading_for(ids[0], confidence=0.5)])
    loaded = store.get(state.id)
    book = loaded.books[0]
    assert book.status == "unidentified"
    assert book.title == ""
    assert book.identification_evidence[0].status == "low_confidence"
    assert any(r.ref_id == book.id for r in loaded.review_queue)


def test_collector_edition_assertion_routes_appraisal(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=107)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(frame.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    book_id = committed.books[0].id
    store.add_turn(state.id, "t1", "it is a rare first edition", "typed_user")
    store.correct(
        state.id,
        CorrectionIntent(type="assert_edition", target_id=book_id,
                         value="rare first edition", source_turn_id="t1"),
    )
    book = store.get(state.id).books[0]
    assert book.status == "needs_appraisal"
    assert book.edition == ""
    assert book.replacement_cost is None


def test_art_print_only_targets_art(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=109)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(
        frame.id,
        [book_scene(0, (60, 80, 60, 200)), item_entry((300, 20, 200, 150), category="decor")],
        640, 360,
    )
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    store.add_turn(state.id, "t1", "it is a print", "typed_user")
    for target in (committed.books[0].id, committed.items[0].id):
        with pytest.raises(StoreError) as info:
            store.correct(
                state.id,
                CorrectionIntent(type="assert_art_print", target_id=target,
                                 value="print", source_turn_id="t1"),
            )
        assert info.value.status_code == 409
    loaded = store.get(state.id)
    assert loaded.items[0].status == "range"
    assert loaded.items[0].assertions == []


def test_negated_print_restores_appraisal(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    img = scene_image(seed=113)
    frame = store.add_frame(state.id, jpeg(img), 1.0)
    obs = observation(frame.id, [item_entry((300, 20, 200, 150), category="art")], 640, 360)
    committed = commit_observation(store, state.id, frame.id, obs, jpeg(img), CONTRACT)
    art_id = committed.items[0].id
    store.add_turn(state.id, "t1", "print claim", "typed_user")
    store.correct(state.id, CorrectionIntent(
        type="assert_art_print", target_id=art_id, value="it is a print", source_turn_id="t1"))
    assert store.get(state.id).items[0].status == "range"
    store.correct(state.id, CorrectionIntent(
        type="assert_art_print", target_id=art_id, value="actually it is the original, not a print",
        source_turn_id="t1"))
    loaded = store.get(state.id)
    assert loaded.items[0].status == "needs_appraisal"
    assert loaded.items[0].replacement_cost is None
    assert len(loaded.items[0].assertions) == 2


def test_failed_alignment_only_flags_when_same_kind_history(tmp_path):
    store = SweepStore(tmp_path / "runs")
    state = store.start("AE", "USD", "dev")
    blank = np.zeros((360, 640, 3), dtype=np.uint8)
    img = scene_image(seed=131)
    frame1 = store.add_frame(state.id, jpeg(blank), 1.0)
    obs1 = observation(
        frame1.id,
        [item_entry((20, 40, 480, 300), category="shelving")],
        640, 360,
    )
    first = commit_observation(store, state.id, frame1.id, obs1, jpeg(blank), CONTRACT)
    assert len(first.items) == 1
    assert len(first.books) == 0
    frame2 = store.add_frame(state.id, jpeg(img), 2.0)
    obs2 = observation(frame2.id, [book_scene(0, (60, 80, 60, 200))], 640, 360)
    second = commit_observation(store, state.id, frame2.id, obs2, jpeg(img), CONTRACT)
    book = second.books[0]
    assert book.potential_duplicate is False
    assert any("alignment" in r.reason for r in second.review_queue)


def test_failed_alignment_new_item_flagged_only_with_prior_items(tmp_path):
    img = scene_image(seed=137)
    blank = np.zeros((360, 640, 3), dtype=np.uint8)
    obs1 = observation(
        "f" + "1" * 31,
        [item_entry((20, 40, 480, 300), category="decor")],
        640, 360,
    )
    r1 = associate_observation(None, jpeg(img), {"books": [], "items": []}, obs1, CONTRACT)
    existing = as_tracks(r1["new_items"], prefix="item")
    obs2 = observation(
        "f" + "2" * 31,
        [item_entry((40, 60, 100, 100), category="decor")],
        640, 360,
    )
    r2 = associate_observation(
        {"jpeg": jpeg(blank), "frame_id": "f" + "1" * 31},
        jpeg(img),
        {"books": [], "items": existing},
        obs2,
        CONTRACT,
    )
    assert r2["alignment"] == "failed"
    assert r2["new_items"][0]["potential_duplicate"] is True


def test_visual_match_accepts_partial_title_with_same_visible_author():
    image = jpeg(scene_image(seed=89))
    first = book_scene(0, (60, 80, 60, 200), "LOVE")
    first.update({"observed_author": "ANA HUANG", "author_legible": True})
    original = observation("f" + "1" * 31, [first], 640, 360)
    seen = associate_observation(None, image, {"books": [], "items": []}, original, CONTRACT)
    later = book_scene(0, (60, 80, 60, 200), "twisted LOVE")
    later.update({"observed_author": "ANA HUANG", "author_legible": True})
    current = observation("f" + "2" * 31, [later], 640, 360)
    track = as_tracks(seen["new_books"])[0]
    result = associate_observation(
        {"jpeg": image, "frame_id": original["frame_id"]},
        image,
        {"books": [track], "items": []},
        current,
        CONTRACT,
    )
    assert [assignment["track_id"] for assignment in result["assignments"]] == [track["id"]]
    assert result["new_books"] == []


def test_border_touching_spine_visible_for_most_of_frame_stays_a_book():
    image = jpeg(scene_image(seed=93))
    frame_id = "f" + "8" * 31
    seen = associate_observation(
        None,
        image,
        {"books": [], "items": []},
        observation(frame_id, [book_scene(0, (60, 0, 60, 300))], 640, 360),
        CONTRACT,
    )
    assert len(seen["new_books"]) == 1
    assert seen["new_books"][0]["bbox"]["h"] == 300


def test_author_misread_as_title_does_not_create_a_second_visual_book():
    image = jpeg(scene_image(seed=91))
    first = observation(
        "f" + "1" * 31,
        [book_scene(0, (60, 80, 60, 200), "WRITER NAME")],
        640,
        360,
    )
    recorded = associate_observation(None, image, {"books": [], "items": []}, first, CONTRACT)
    corrected = book_scene(0, (60, 80, 60, 200), "A DIFFERENT NOVEL")
    corrected.update({"observed_author": "WRITER NAME", "author_legible": True})
    second = observation("f" + "2" * 31, [corrected], 640, 360)
    track = as_tracks(recorded["new_books"])[0]
    result = associate_observation(
        {"jpeg": image, "frame_id": first["frame_id"]},
        image,
        {"books": [track], "items": []},
        second,
        CONTRACT,
    )
    assert [assignment["track_id"] for assignment in result["assignments"]] == [track["id"]]
    assert result["new_books"] == []


@pytest.mark.parametrize("earlier_title", (
    "WRITER NAME The Green Novel",
    "The Green Novel WRITER NAME",
))
def test_scene_title_with_printed_byline_does_not_create_another_book(earlier_title):
    image = jpeg(scene_image(seed=95))
    first = observation(
        "f" + "1" * 31,
        [book_scene(0, (60, 80, 60, 200), earlier_title)],
        640,
        360,
    )
    recorded = associate_observation(None, image, {"books": [], "items": []}, first, CONTRACT)
    corrected = book_scene(0, (60, 80, 60, 200), "The Green Novel")
    corrected.update({"observed_author": "WRITER NAME", "author_legible": True})
    second = observation("f" + "2" * 31, [corrected], 640, 360)
    track = as_tracks(recorded["new_books"])[0]
    result = associate_observation(
        {"jpeg": image, "frame_id": first["frame_id"]},
        image,
        {"books": [track], "items": []},
        second,
        CONTRACT,
    )
    assert [assignment["track_id"] for assignment in result["assignments"]] == [track["id"]]
    assert result["new_books"] == []


def test_border_truncated_book_spine_stays_unresolved_until_a_complete_view():
    image = jpeg(scene_image(seed=73))
    frame_id = "f" + "7" * 31
    scene = observation(
        frame_id,
        [
            book_scene(0, (60, 0, 60, 120)),
            book_scene(1, (180, 80, 60, 200)),
        ],
        640,
        360,
    )
    result = associate_observation(None, image, {"books": [], "items": []}, scene, CONTRACT)
    assert len(result["new_books"]) == 1
    assert result["new_books"][0]["bbox"] == {"x": 180, "y": 80, "w": 60, "h": 200}
    assert any(
        item["ref_id"] == frame_id and "partial" in item["reason"]
        for item in result["review_items"]
    )


@pytest.mark.parametrize("source_id", (
    "289b3e8e9bcd4a7baa231f64c3db33b9",
    "8a4eb5399e444b268fa97282809a41bf",
))
def test_owner_confirmed_five_book_sweep_keeps_one_track_per_book(tmp_path, source_id):
    from preflight.vision import proposal_boxes
    from backend.vision import load_contract

    source = Path(__file__).resolve().parents[2] / "runs" / source_id
    if not (source / "state.json").is_file():
        pytest.skip("private owner-confirmed sweep unavailable")
    saved = json.loads((source / "state.json").read_text())
    by_frame = {frame["id"]: frame for frame in saved["frames"]}
    vision_contract = load_contract(Path(__file__).resolve().parents[2] / "backend" / "vision_contract.json")
    store = SweepStore(tmp_path / "runs")
    sweep = store.start("AE", "AED", "offline evidence replay")
    progress = []
    for stage in saved["vision_stages"]:
        if stage["status"] != "completed":
            continue
        recorded = json.loads((source / stage["persisted_ref"]).read_text())
        original_frame = by_frame[stage["frame_id"]]
        frame_bytes = (source / original_frame["frame_ref"]).read_bytes()
        frame = store.add_frame(sweep.id, frame_bytes, original_frame["timestamp_s"])
        mapping = proposal_boxes(
            recorded["scene"],
            recorded["image_width"],
            recorded["image_height"],
            confidence_min=vision_contract["admission"]["book_existence_confidence_min"],
            coordinate_system=recorded["coordinate_system"],
            sent_width=recorded["sent_image_width"],
            sent_height=recorded["sent_image_height"],
        )
        commit_observation(
            store,
            sweep.id,
            frame.id,
            {
                "frame_id": frame.id,
                "observation": recorded["scene"],
                "box_mapping": mapping,
                "image_width": recorded["image_width"],
                "image_height": recorded["image_height"],
                "sent_image_width": recorded["sent_image_width"],
                "sent_image_height": recorded["sent_image_height"],
                "coordinate_system": recorded["coordinate_system"],
            },
            frame_bytes,
            CONTRACT,
        )
        current_count = len(store.get(sweep.id).books)
        progress.append((round(original_frame["timestamp_s"], 1), current_count))
        if source_id == "289b3e8e9bcd4a7baa231f64c3db33b9" and 32 <= original_frame["timestamp_s"] <= 34:
            assert current_count == 5
    from backend.scene_reconciliation import reconcile_scene_books

    before = store.get(sweep.id)
    store.end(sweep.id, saved["duration_s"] or 0)
    outcome = reconcile_scene_books(store, sweep.id, CONTRACT)
    after = store.get(sweep.id)
    assert outcome["supported_scenes"] == 1
    assert len(after.books) == 5, "timeline: " + ",".join(f"{t}:{n}" for t, n in progress)
    assert len(after.unresolved_books) == len(before.books) - 5
    assert all(book.evidence for book in after.unresolved_books)
    from backend.packet import build_packet

    packet = build_packet(after, store)
    assert packet["status"] == "partial"
    assert packet["totals"]["book_count"] == 5
    assert len(packet["unresolved_book_observations"]) == len(before.books) - 5
    assert all(item["evidence"] for item in packet["unresolved_book_observations"])
    revision = after.revision
    repeated = reconcile_scene_books(store, sweep.id, CONTRACT)
    assert repeated["supported_scenes"] == 1
    assert store.get(sweep.id).revision == revision


def test_person_descriptions_rejected_before_association():
    img = scene_image(seed=139)
    entries = [
        item_entry((20, 40, 100, 100), category="other"),
        item_entry((140, 40, 100, 100), category="other"),
        item_entry((260, 40, 100, 100), category="person"),
        item_entry((380, 40, 100, 100), category="portrait"),
        item_entry((500, 40, 100, 100), category="lamp"),
        item_entry((20, 200, 100, 100), category="decor"),
    ]
    entries[0]["description"] = "a person standing near the shelf"
    entries[1]["description"] = "Group of children playing"
    entries[2]["description"] = "tall figure"
    entries[3]["description"] = "framed portrait of a woman"
    entries[4]["description"] = "a person-shaped lamp"
    entries[5]["description"] = "mannequin wearing a jacket"
    obs = observation("f" + "1" * 31, entries, 640, 360)
    result = associate_observation(
        None, jpeg(img), {"books": [], "items": []}, obs, CONTRACT
    )
    descriptions = {item["description"] for item in result["new_items"]}
    categories = {item["category"] for item in result["new_items"]}
    assert "a person standing near the shelf" not in descriptions
    assert "Group of children playing" not in descriptions
    assert "person" not in categories
    assert "framed portrait of a woman" in descriptions
    assert "a person-shaped lamp" in descriptions
    assert "mannequin wearing a jacket" in descriptions
    assert len(result["new_items"]) == 3
