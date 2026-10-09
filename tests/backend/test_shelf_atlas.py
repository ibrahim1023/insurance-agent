import json
from pathlib import Path

import cv2
import numpy as np
import pytest

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "backend" / "tracking_contract.json").read_text())


def jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


def test_atlas_maps_overlap_to_saved_frames():
    from backend.shelf_atlas import build_shelf_atlas

    original = np.random.RandomState(23).randint(0, 256, (240, 320, 3), dtype=np.uint8)
    shifted = cv2.warpAffine(original, np.float32([[1, 0, 24], [0, 1, 2]]), (320, 240))
    first, second = "frame_" + "a" * 32, "frame_" + "b" * 32

    result = build_shelf_atlas([(first, jpeg(original)), (second, jpeg(shifted))], CONTRACT)

    atlas = result["atlas"]
    assert atlas is not None and atlas.status == "supported"
    assert atlas.frame_ids == [first, second]
    image = cv2.imdecode(np.frombuffer(result["jpeg"], dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape[:2] == (atlas.height_px, atlas.width_px)
    first_point = cv2.perspectiveTransform(np.float32([[[100, 120]]]), np.array(atlas.frame_transforms[first]))
    second_point = cv2.perspectiveTransform(np.float32([[[124, 122]]]), np.array(atlas.frame_transforms[second]))
    assert np.linalg.norm(first_point - second_point) < 2
    assert atlas.covered_ranges and atlas.gaps == []


def test_atlas_rejects_different_shelves_and_implausible_warps():
    from backend.shelf_atlas import build_shelf_atlas

    first = np.random.RandomState(23).randint(0, 256, (240, 320, 3), dtype=np.uint8)
    different = np.random.RandomState(47).randint(0, 256, (240, 320, 3), dtype=np.uint8)
    narrow_overlap = cv2.warpPerspective(first, np.float32([[1, 0, 250], [0, 1, 0], [0, 0, 1]]), (320, 240))
    first_id, second_id = "frame_" + "a" * 32, "frame_" + "b" * 32

    unrelated = build_shelf_atlas([(first_id, jpeg(first)), (second_id, jpeg(different))], CONTRACT)
    unsafe = build_shelf_atlas([(first_id, jpeg(first)), (second_id, jpeg(narrow_overlap))], CONTRACT)

    assert unrelated["atlas"] is None and unrelated["reason"]
    assert unsafe["atlas"] is None and unsafe["reason"]


def test_atlas_maps_three_overlapping_views_without_double_counting_space():
    from backend.shelf_atlas import build_shelf_atlas

    shelf = np.random.RandomState(41).randint(0, 256, (240, 640, 3), dtype=np.uint8)
    ids = ["frame_" + char * 32 for char in "abc"]
    views = [shelf[:, offset:offset + 320] for offset in (0, 160, 320)]

    result = build_shelf_atlas([(frame_id, jpeg(image)) for frame_id, image in zip(ids, views)], CONTRACT)

    atlas = result["atlas"]
    assert atlas is not None and atlas.frame_ids == ids
    assert atlas.width_px < 700
    for first_id, first_xy, second_id, second_xy in (
        (ids[0], (200, 120), ids[1], (40, 120)),
        (ids[1], (240, 120), ids[2], (80, 120)),
    ):
        first = cv2.perspectiveTransform(np.float32([[first_xy]]), np.array(atlas.frame_transforms[first_id]))
        second = cv2.perspectiveTransform(np.float32([[second_xy]]), np.array(atlas.frame_transforms[second_id]))
        assert np.linalg.norm(first - second) < 3
    assert atlas.gaps == []


def test_atlas_maps_spine_spacing_in_saved_same_shelf_pair():
    from backend.shelf_atlas import build_shelf_atlas

    root = Path(__file__).resolve().parents[2] / "runs" / "34ccbe8ed7fe4c488b6d01697a549aa4" / "frames"
    first = "frame_f977b7c85b5749f0aca4f43ba975ea19"
    second = "frame_b1c20b984e5048149c7e237a7b04b0c5"
    if not (root / f"{first}.jpg").is_file():
        pytest.skip("private saved sweep unavailable")
    frames = [(frame_id, (root / f"{frame_id}.jpg").read_bytes()) for frame_id in (first, second)]

    result = build_shelf_atlas(frames, CONTRACT)

    assert result["atlas"] is not None
    transforms = result["atlas"].frame_transforms
    source = cv2.perspectiveTransform(np.float32([[[90, 320]], [[230, 320]]]), np.array(transforms[first]))
    matched = cv2.perspectiveTransform(np.float32([[[310, 320]], [[450, 320]]]), np.array(transforms[second]))
    source_span = source[1, 0, 0] - source[0, 0, 0]
    matched_span = matched[1, 0, 0] - matched[0, 0, 0]
    assert abs(float(source_span - matched_span)) < 10


def test_atlas_rejects_saved_unrelated_shelves_and_bounds_repeated_five():
    from backend.shelf_atlas import build_shelf_atlas

    root = Path(__file__).resolve().parents[2] / "runs"
    controls = (
        ("34ccbe8ed7fe4c488b6d01697a549aa4", "frame_3bed8eae834745239f3d574d3b47af3f", "frame_91f10b34b1ae42faa8d7261093ccd7ea"),
        ("289b3e8e9bcd4a7baa231f64c3db33b9", "frame_4ae86823723445978105b975fb705cb5", "frame_a414b6c335cc4b70a02695a0a7598fce"),
    )
    if not all((root / run / "frames" / f"{first}.jpg").is_file() for run, first, _ in controls):
        pytest.skip("private saved sweeps unavailable")
    results = []
    for run, first, second in controls:
        frames = [(frame_id, (root / run / "frames" / f"{frame_id}.jpg").read_bytes()) for frame_id in (first, second)]
        results.append(build_shelf_atlas(frames, CONTRACT))

    assert results[0]["atlas"] is None and results[0]["reason"]
    repeated = results[1]["atlas"]
    assert repeated is not None and repeated.status == "supported"
    assert repeated.width_px * repeated.height_px < 2 * 480 * 640 * 2


def test_atlas_rejects_competing_book_plane_motion():
    from backend.shelf_atlas import build_shelf_atlas

    random = np.random.RandomState(51)
    background = random.randint(0, 256, (240, 400, 3), dtype=np.uint8)
    first = background.copy()
    second = cv2.warpAffine(background, np.float32([[1, 0, 24], [0, 1, 0]]), (400, 240))
    for x in (35, 125, 215, 305):
        spine = random.randint(0, 256, (170, 40, 3), dtype=np.uint8)
        first[35:205, x:x + 40] = spine
        second[35:205, x + 55:x + 95] = spine

    result = build_shelf_atlas([("frame_a", jpeg(first)), ("frame_b", jpeg(second))], CONTRACT)

    assert result["atlas"] is None and "parallax" in result["reason"]
