from uuid import uuid4

import cv2
import numpy as np

from .models import ShelfAtlasRecord


def _align_shelf(previous, current, cfg, atlas_cfg):
    detector = cv2.SIFT_create(nfeatures=int(cfg["nfeatures"]))
    first, first_descriptors = detector.detectAndCompute(cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY), None)
    second, second_descriptors = detector.detectAndCompute(cv2.cvtColor(current, cv2.COLOR_BGR2GRAY), None)
    if first_descriptors is None or second_descriptors is None:
        return None, "shelf overlap unverified"
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(first_descriptors, second_descriptors, k=2)
    good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < cfg["ratio_test"] * b.distance]
    if len(good) < cfg["minimum_good_matches"]:
        return None, "shelf overlap unverified"
    source = np.float32([first[match.queryIdx].pt for match in good]).reshape(-1, 1, 2)
    target = np.float32([second[match.trainIdx].pt for match in good]).reshape(-1, 1, 2)
    threshold = max(1.5, 0.003125 * max(current.shape[:2]))
    homography, mask = cv2.findHomography(source, target, cv2.RANSAC, threshold)
    if homography is None or mask is None or not np.isfinite(homography).all() or abs(float(np.linalg.det(homography))) < 1e-8:
        return None, "shelf overlap unverified"
    inliers = int(mask.sum())
    if inliers < cfg["minimum_inliers"] or inliers / len(good) < cfg["minimum_inlier_ratio"]:
        return None, "shelf overlap unverified"
    unmatched = mask.reshape(-1) == 0
    if unmatched.sum() >= cfg["minimum_good_matches"]:
        competing, competing_mask = cv2.findHomography(source[unmatched], target[unmatched], cv2.RANSAC, threshold)
        if competing is not None and competing_mask is not None and np.isfinite(competing).all():
            competing_inliers = int(competing_mask.sum())
            if competing_inliers >= cfg["minimum_inliers"] and competing_inliers / len(good) >= atlas_cfg["minimum_competing_inlier_fraction"]:
                primary_points = source[~unmatched]
                disagreement = np.linalg.norm(
                    cv2.perspectiveTransform(primary_points, homography) - cv2.perspectiveTransform(primary_points, competing),
                    axis=2,
                )
                limit = atlas_cfg["maximum_competing_displacement_fraction_of_frame_width"] * min(previous.shape[1], current.shape[1])
                if not np.isfinite(disagreement).all() or float(np.median(disagreement)) > limit:
                    return None, "parallax between shelf planes"
    return homography, None


def _corners(image):
    height, width = image.shape[:2]
    return np.float32([[0, 0], [width, 0], [width, height], [0, height]])


def build_shelf_atlas(frames: list[tuple[str, bytes]], contract: dict) -> dict:
    def unresolved(reason):
        return {"atlas": None, "jpeg": None, "reason": reason}

    frame_ids = [frame_id for frame_id, _ in frames]
    if len(frames) < 2 or len(set(frame_ids)) != len(frame_ids):
        return unresolved("distinct saved frames required")
    images = [cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR) for _, jpeg in frames]
    if any(image is None for image in images):
        return unresolved("source frame unreadable")

    cfg = contract["atlas_registration"]
    transforms = [np.eye(3, dtype=np.float64)]
    for index in range(1, len(frames)):
        previous, current = images[index - 1:index + 1]
        homography, reason = _align_shelf(previous, current, contract["frame_alignment"], cfg)
        if homography is None:
            return unresolved(reason)
        projected = cv2.perspectiveTransform(_corners(previous).reshape(-1, 1, 2), homography).reshape(-1, 2)
        if not np.isfinite(projected).all() or not cv2.isContourConvex(projected):
            return unresolved("implausible shelf warp")
        projected_area = abs(cv2.contourArea(projected))
        previous_area = previous.shape[0] * previous.shape[1]
        current_area = current.shape[0] * current.shape[1]
        area_ratio = projected_area / previous_area
        if not cfg["minimum_projected_area_ratio"] <= area_ratio <= cfg["maximum_projected_area_ratio"]:
            return unresolved("implausible shelf scale")
        overlap, _ = cv2.intersectConvexConvex(projected, _corners(current))
        if overlap / min(projected_area, current_area) < cfg["minimum_overlap_fraction_of_smaller_frame"]:
            return unresolved("insufficient shelf overlap")
        bounds = np.concatenate((projected, _corners(current)))
        pair_width, pair_height = np.ceil(bounds.max(axis=0) - bounds.min(axis=0)).astype(int)
        if pair_width <= 0 or pair_height <= 0 or pair_width * pair_height / (previous_area + current_area) > cfg["maximum_canvas_area_ratio_of_sources"]:
            return unresolved("implausible shelf extent")
        transforms.append(transforms[-1] @ np.linalg.inv(homography))

    projected_corners = [cv2.perspectiveTransform(_corners(image).reshape(-1, 1, 2), transform).reshape(-1, 2)
                         for image, transform in zip(images, transforms)]
    if any(not np.isfinite(corners).all() for corners in projected_corners):
        return unresolved("implausible shelf warp")
    bounds = np.concatenate(projected_corners)
    minimum = bounds.min(axis=0)
    width, height = np.ceil(bounds.max(axis=0) - minimum).astype(int)
    source_area = sum(image.shape[0] * image.shape[1] for image in images)
    if width <= 0 or height <= 0 or width * height / source_area > cfg["maximum_canvas_area_ratio_of_sources"]:
        return unresolved("implausible atlas extent")
    shift = np.array([[1, 0, -minimum[0]], [0, 1, -minimum[1]], [0, 0, 1]], dtype=np.float64)
    transforms = [shift @ transform for transform in transforms]
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for source, transform in zip(images, transforms):
        warped = cv2.warpPerspective(source, transform, (width, height))
        mask = cv2.warpPerspective(np.full(source.shape[:2], 255, np.uint8), transform, (width, height))
        image[mask != 0] = warped[mask != 0]
    ok, encoded = cv2.imencode(".jpg", image)
    if not ok:
        return unresolved("atlas image encoding failed")
    atlas = ShelfAtlasRecord(
        id=f"shelf_{uuid4().hex}",
        frame_ids=frame_ids,
        frame_transforms={frame_id: transform.tolist() for frame_id, transform in zip(frame_ids, transforms)},
        width_px=width,
        height_px=height,
        covered_ranges=[(0.0, float(width))],
        gaps=[],
        status="supported",
    )
    return {"atlas": atlas, "jpeg": encoded.tobytes(), "reason": None}
