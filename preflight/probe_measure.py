import argparse
import json
import math
from pathlib import Path
from statistics import mean, median

from preflight.measure import measure_spine_cm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    records = json.loads(Path(args.input).read_text())
    if not isinstance(records, list) or not records:
        parser.error("provide annotated spine records")
    rows, height_errors, thickness_errors = [], [], []
    for record in records:
        dimensions = measure_spine_cm(record.get("marker_quad"), record.get("spine_quad"), same_plane=record.get("same_plane") is True)
        row = {
            "id": record.get("id", ""), "image_path": record.get("image_path"),
            "height_cm": dimensions[0] if dimensions else None,
            "thickness_cm": dimensions[1] if dimensions else None,
            "height_error_pct": None, "thickness_error_pct": None,
            "reason": "unsupported_plane_or_invalid_corners" if dimensions is None else "tape_truth_missing_or_invalid",
        }
        truths = [record.get("truth_height_cm"), record.get("truth_thickness_cm")]
        valid_truth = all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 for value in truths)
        if dimensions is not None and valid_truth:
            height_error = 100 * abs(dimensions[0] - truths[0]) / truths[0]
            thickness_error = 100 * abs(dimensions[1] - truths[1]) / truths[1]
            row.update(height_error_pct=height_error, thickness_error_pct=thickness_error, reason="")
            height_errors.append(height_error)
            thickness_errors.append(thickness_error)
        rows.append(row)
    result = {
        "method": "manually_annotated_same_plane_A4_homography",
        "input_source": str(Path(args.input).resolve()),
        "requested_spines": len(rows), "compared_spines": len(height_errors),
        "measurable_spines": sum(row["height_cm"] is not None for row in rows),
        "mean_height_error_pct": mean(height_errors) if height_errors else None,
        "mean_thickness_error_pct": mean(thickness_errors) if thickness_errors else None,
        "median_height_error_pct": median(height_errors) if height_errors else None,
        "median_thickness_error_pct": median(thickness_errors) if thickness_errors else None,
        "spines": rows,
        "room": dict.fromkeys(("length_m", "width_m", "height_m", "floor_area_m2", "wall_area_m2", "shelved_wall_area_m2")),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as file:
        json.dump(result, file, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
