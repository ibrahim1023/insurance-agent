import json
import subprocess
import sys

import cv2
import numpy as np


def invoke(module, data, tmp_path, extra_args=()):
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    source.write_text(json.dumps(data))
    result = subprocess.run(
        [sys.executable, "-m", module, "--input", str(source), "--output", str(output), *extra_args],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text())


def test_price_probe_without_urls_keeps_prices_missing(tmp_path):
    data = [{"title": f"Test Book {i}", "author": "Example Author"} for i in range(5)]
    result = invoke("preflight.probe_prices", data, tmp_path)
    assert result["status"] == "BLOCKED"
    assert result["fetch_attempts"] == 0
    assert all(row["replacement"] is None and row["used"] is None for row in result["books"])


def test_measure_probe_reports_pair_errors_and_null_room(tmp_path):
    data = [{
        "id": "synthetic_spine", "same_plane": True,
        "marker_quad": [[0, 0], [210, 0], [210, 297], [0, 297]],
        "spine_quad": [[240, 10], [260, 10], [260, 250], [240, 250]],
        "truth_height_cm": 24, "truth_thickness_cm": 2,
    }]
    result = invoke("preflight.probe_measure", data, tmp_path)
    assert result["compared_spines"] == 1
    assert result["mean_height_error_pct"] < 0.001
    assert result["mean_thickness_error_pct"] < 0.001
    assert all(value is None for value in result["room"].values())


def test_spine_probe_reports_fixture_count_and_overlay(tmp_path):
    image = np.zeros((300, 300), dtype=np.uint8)
    image[40:260, 20:45] = 255
    assert cv2.imwrite(str(tmp_path / "shelf.png"), image)
    result = invoke("preflight.probe_spines", [{"image_path": "shelf.png", "truth_boxes": [[20, 40, 25, 220]]}], tmp_path)
    row = result["images"][0]
    assert row["truth"] == 1
    assert row["predicted"] == 1
    assert row["matched"] == 1
    assert (tmp_path / row["overlay_path"]).exists()


def test_spine_probe_boundaries_detector_on_touching_fixture(tmp_path):
    image = np.zeros((300, 300), dtype=np.uint8)
    truth = [[20, 40, 25, 220], [45, 40, 30, 220], [75, 40, 35, 220]]
    for (x, y, w, h), fill in zip(truth, (230, 100, 180)):
        image[y:y + h, x:x + w] = fill
    assert cv2.imwrite(str(tmp_path / "shelf.png"), image)
    result = invoke(
        "preflight.probe_spines",
        [{"image_path": "shelf.png", "truth_boxes": truth}],
        tmp_path,
        extra_args=("--detector", "boundaries"),
    )
    assert result["detector_method"] == "boundaries"
    row = result["images"][0]
    assert row["truth"] == 3
    assert row["matched"] == 3
    assert (tmp_path / row["overlay_path"]).exists()
