import importlib

import cv2
import numpy as np
import pytest


def module():
    return importlib.import_module("preflight.spines")


TOUCHING_TRUTH = [(20, 40, 25, 220), (45, 40, 30, 220), (75, 40, 35, 220)]


def touching_spines_image():
    image = np.zeros((300, 300), dtype=np.uint8)
    for (x, y, w, h), fill in zip(TOUCHING_TRUTH, (230, 100, 180)):
        patch = np.full((h, w), fill, dtype=np.uint8)
        for ty in (60, 140):
            cv2.putText(patch, "BOOK", (2, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.3, 255, 1)
        image[y:y + h, x:x + w] = patch
    return image


def test_clear_parallel_spines():
    image = np.zeros((300, 300), dtype=np.uint8)
    for left in (20, 70, 120, 170, 220):
        image[40:260, left:left + 25] = 255
    boxes = module().detect_spines(image)
    assert len(boxes) == 5
    assert all(0 <= x < 300 and 0 <= y < 300 and x + w <= 300 and y + h <= 300 for x, y, w, h in boxes)


def test_blank_image():
    assert module().detect_spines(np.zeros((300, 300), dtype=np.uint8)) == []


def test_flat_stack_candidates():
    image = np.zeros((300, 300), dtype=np.uint8)
    for top in (20, 70, 120):
        image[top:top + 25, 40:260] = 255
    assert len(module().detect_spines(image)) == 3


def test_one_to_one_matching_prevents_double_credit():
    result = module().evaluate_boxes([(0, 0, 10, 10), (0, 0, 10, 10)], [(0, 0, 10, 10)])
    assert result == {
        "predicted": 2, "truth": 1, "matched": 1, "false_positives": 1,
        "missed": 0, "precision": 0.5, "recall": 1.0, "count_error_pct": 100.0,
    }


def test_maximum_matching_avoids_greedy_miss():
    result = module().evaluate_boxes([(2, 0, 10, 10), (0, 0, 10, 10)], [(0, 0, 10, 10), (5, 0, 10, 10)])
    assert result["matched"] == 2
    assert result["recall"] == 1.0


def test_boundaries_detects_three_touching_filled_spines():
    boxes = module().detect_spines(touching_spines_image(), method="boundaries")
    assert len(boxes) == 3
    result = module().evaluate_boxes(boxes, TOUCHING_TRUTH)
    assert result["matched"] == 3


def test_boundaries_text_only_returns_empty():
    image = np.full((300, 300), 127, dtype=np.uint8)
    for y in (80, 120, 160, 200):
        cv2.putText(image, "BOOK", (100, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 0, 1)
    assert module().detect_spines(image, method="boundaries") == []


def test_boundaries_blank_image_returns_empty():
    assert module().detect_spines(np.zeros((300, 300), dtype=np.uint8), method="boundaries") == []


def test_boundaries_rotated_fixture_finds_flat_spines():
    rotated = cv2.rotate(touching_spines_image(), cv2.ROTATE_90_CLOCKWISE)
    truth = [(40, 20, 220, 25), (40, 45, 220, 30), (40, 75, 220, 35)]
    boxes = module().detect_spines(rotated, method="boundaries")
    assert len(boxes) == 3
    assert module().evaluate_boxes(boxes, truth)["matched"] == 3


def test_boundaries_tilted_touching_spines():
    image = np.zeros((300, 300), dtype=np.uint8)
    cv2.fillPoly(image, [np.array([(30, 40), (60, 40), (100, 260), (70, 260)], np.int32)], 230)
    cv2.fillPoly(image, [np.array([(60, 40), (95, 40), (135, 260), (100, 260)], np.int32)], 100)
    truth = [(30, 40, 71, 221), (60, 40, 76, 221)]
    boxes = module().detect_spines(image, method="boundaries")
    assert len(boxes) == 2
    assert module().evaluate_boxes(boxes, truth)["matched"] == 2


@pytest.mark.parametrize("image", [None, np.array([], dtype=np.uint8), np.zeros((300, 300), dtype=np.uint8)])
def test_unknown_method_raises_valueerror(image):
    with pytest.raises(ValueError, match="unknown detector method"):
        module().detect_spines(image, method="grid")


def test_empty_denominators_are_unknown():
    result = module().evaluate_boxes([], [])
    assert result["precision"] is None
    assert result["recall"] is None
    assert result["count_error_pct"] is None
