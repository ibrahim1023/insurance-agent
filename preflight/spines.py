import cv2
import numpy as np


def _vertical_boundary_boxes(grey: np.ndarray) -> list[tuple[int, int, int, int]]:
    height, width = grey.shape
    edges = cv2.Canny(grey, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=max(10, round(0.10 * height)),
        minLineLength=int(np.ceil(0.45 * height)),
        maxLineGap=round(0.03 * height),
    )
    if lines is None:
        return []
    segments = []
    for line in lines:
        x1, y1, x2, y2 = (int(v) for v in line[0])
        dx, dy = x2 - x1, y2 - y1
        if abs(dy) < 0.45 * height or abs(dx) > 0.35 * abs(dy):
            continue
        if dy < 0:
            x1, y1, x2, y2 = x2, y2, x1, y1
            dx, dy = -dx, -dy
        slope = dx / dy
        intercept = x1 - slope * y1
        segments.append({
            "a": slope, "b": intercept, "ymin": y1, "ymax": y2, "weight": abs(dy),
        })
    segments.sort(key=lambda s: s["a"] * height / 2 + s["b"])
    clusters = []
    for segment in segments:
        seg_x = segment["a"] * height / 2 + segment["b"]
        best, best_dist = None, None
        for cluster in clusters:
            mean_a = cluster["sum_wa"] / cluster["sum_w"]
            mean_x = mean_a * height / 2 + cluster["sum_wb"] / cluster["sum_w"]
            dist = abs(seg_x - mean_x)
            if dist <= max(2, 0.008 * width) and abs(segment["a"] - mean_a) <= 0.10:
                if best is None or dist < best_dist:
                    best, best_dist = cluster, dist
        if best is None:
            clusters.append({
                "sum_w": segment["weight"], "sum_wa": segment["a"] * segment["weight"],
                "sum_wb": segment["b"] * segment["weight"],
                "ymin": segment["ymin"], "ymax": segment["ymax"],
            })
        else:
            best["sum_w"] += segment["weight"]
            best["sum_wa"] += segment["a"] * segment["weight"]
            best["sum_wb"] += segment["b"] * segment["weight"]
            best["ymin"] = min(best["ymin"], segment["ymin"])
            best["ymax"] = max(best["ymax"], segment["ymax"])
    clusters.sort(key=lambda c: c["sum_wa"] / c["sum_w"] * height / 2 + c["sum_wb"] / c["sum_w"])
    boxes = []
    for left, right in zip(clusters, clusters[1:]):
        la, lb = left["sum_wa"] / left["sum_w"], left["sum_wb"] / left["sum_w"]
        ra, rb = right["sum_wa"] / right["sum_w"], right["sum_wb"] / right["sum_w"]
        gap = (ra * height / 2 + rb) - (la * height / 2 + lb)
        if gap < max(3, 0.015 * height) or gap > 0.35 * height:
            continue
        left_len = left["ymax"] - left["ymin"]
        right_len = right["ymax"] - right["ymin"]
        overlap = min(left["ymax"], right["ymax"]) - max(left["ymin"], right["ymin"])
        if overlap < 0.70 * min(left_len, right_len):
            continue
        top = min(left["ymin"], right["ymin"])
        bottom = max(left["ymax"], right["ymax"])
        if not (la * top + lb < ra * top + rb and la * bottom + lb < ra * bottom + rb):
            continue
        xs = [la * top + lb, ra * top + rb, la * bottom + lb, ra * bottom + rb]
        x0 = max(0, int(np.floor(min(xs))))
        x1 = min(width, int(np.ceil(max(xs))))
        y0 = max(0, int(np.floor(top)))
        y1 = min(height, int(np.ceil(bottom)))
        bw, bh = x1 - x0, y1 - y0
        if bw >= 3 and bh >= 0.45 * height and bh / max(bw, 1) >= 1.8 and bw * bh < 0.6 * width * height:
            boxes.append((x0, y0, bw, bh))
    return boxes


def _boundary_spines(grey: np.ndarray) -> list[tuple[int, int, int, int]]:
    height = grey.shape[0]
    boxes = list(_vertical_boundary_boxes(grey))
    rotated = cv2.rotate(grey, cv2.ROTATE_90_CLOCKWISE)
    for xr, yr, wr, hr in _vertical_boundary_boxes(rotated):
        boxes.append((yr, height - xr - wr, hr, wr))
    kept = []
    for box in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
        if all(intersection_over_union(box, kept_box) < 0.75 for kept_box in kept):
            kept.append(box)
    return sorted(kept, key=lambda b: (b[0], b[1]))


def detect_spines(image: np.ndarray, *, method: str = "contours") -> list[tuple[int, int, int, int]]:
    if method not in ("contours", "boundaries"):
        raise ValueError("unknown detector method")
    if image is None or image.size == 0:
        return []
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    if method == "boundaries":
        return _boundary_spines(grey)
    edges = cv2.Canny(grey, 50, 150)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_height, image_width = grey.shape
    boxes = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        upright = height / max(width, 1) >= 1.8 and height >= 0.08 * image_height
        flat = width / max(height, 1) >= 1.8 and width >= 0.08 * image_width
        if min(width, height) >= 3 and width * height < 0.6 * image_width * image_height and (upright or flat):
            boxes.append((x, y, width, height))
    return sorted(boxes, key=lambda box: (box[0], box[1]))


def intersection_over_union(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    union = a[2] * a[3] + b[2] * b[3] - intersection
    return intersection / union if union > 0 else 0


def evaluate_boxes(predicted, truth):
    for box in [*predicted, *truth]:
        if len(box) != 4 or not np.isfinite(box).all() or min(box[2:]) <= 0:
            raise ValueError("boxes must have finite x, y and positive width, height")
    edges = [[i for i, expected in enumerate(truth) if intersection_over_union(box, expected) >= 0.5] for box in predicted]
    assigned = {}

    def associate(index, seen):
        for target in edges[index]:
            if target in seen:
                continue
            seen.add(target)
            if target not in assigned or associate(assigned[target], seen):
                assigned[target] = index
                return True
        return False

    matched = sum(associate(index, set()) for index in range(len(predicted)))
    return {
        "predicted": len(predicted), "truth": len(truth), "matched": matched,
        "false_positives": len(predicted) - matched, "missed": len(truth) - matched,
        "precision": matched / len(predicted) if predicted else None,
        "recall": matched / len(truth) if truth else None,
        "count_error_pct": 100 * abs(len(predicted) - len(truth)) / len(truth) if truth else None,
    }
