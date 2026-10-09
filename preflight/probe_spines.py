import argparse
import json
import time
from pathlib import Path

import cv2

from preflight.spines import detect_spines, evaluate_boxes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--detector", choices=("contours", "boundaries"), default="contours")
    args = parser.parse_args()
    source = Path(args.input)
    records = json.loads(source.read_text())
    if not isinstance(records, list) or not records:
        parser.error("provide images and independently labelled truth_boxes")
    output = Path(args.output)
    if output.exists():
        parser.error("output already exists; choose a new evidence filename")
    overlays = output.parent / (output.stem + "-overlays")
    overlays.mkdir(parents=True, exist_ok=False)
    rows = []
    for index, record in enumerate(records):
        path = source.parent / record["image_path"]
        image = cv2.imread(str(path))
        if image is None:
            parser.error(f"cannot read image: {path}")
        started = time.monotonic()
        predicted = detect_spines(image, method=args.detector)
        elapsed_ms = (time.monotonic() - started) * 1000
        metrics = evaluate_boxes(predicted, record["truth_boxes"])
        for x, y, width, height in predicted:
            cv2.rectangle(image, (x, y), (x + width, y + height), (0, 255, 0), 2)
        overlay = overlays / f"image_{index:03d}.jpg"
        if not cv2.imwrite(str(overlay), image):
            parser.error(f"cannot save overlay: {overlay}")
        rows.append({
            "image_path": str(path.resolve()), "predicted_boxes": predicted,
            "orientation_note": record.get("orientation_note", "NOT_MEASURED"),
            "elapsed_ms": elapsed_ms, "overlay_path": str(overlay.relative_to(output.parent)),
            **metrics,
        })
    result = {"input_source": str(source.resolve()), "detector_method": args.detector, "images": rows, "limitation": "candidate_boxes_only_not_temporal_book_count"}
    with output.open("x") as file:
        json.dump(result, file, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
