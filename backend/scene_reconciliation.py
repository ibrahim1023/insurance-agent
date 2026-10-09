from collections import Counter
from math import ceil

import cv2
import numpy as np

from .sweeps import StoreError, SweepStore


def _view(image, frame, tracks, sift, border_fraction):
    gray = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return None
    h, w = gray.shape
    visible = []
    for book in tracks:
        evidence = next((e for e in book.evidence if e.frame_id == frame.id), None)
        if evidence is None:
            continue
        box = evidence.bbox
        if (box.y <= 0 or box.y + box.h >= h) and box.h / h < border_fraction:
            continue
        crop = gray[max(0, box.y):min(h, box.y + box.h), max(0, box.x):min(w, box.x + box.w)]
        descriptors = sift.detectAndCompute(crop, None)[1] if crop.size else None
        visible.append((book, evidence, descriptors))
    return {"frame_id": frame.id, "visible": visible, "index": frame.timestamp_s} if visible else None


def _matching_spines(left, right, matcher, ratio, minimum):
    pairs = []
    for i, (_, _, earlier) in enumerate(left["visible"]):
        if earlier is None:
            continue
        for j, (_, _, later) in enumerate(right["visible"]):
            if later is None or len(later) < 2:
                continue
            count = sum(
                len(pair) == 2 and pair[0].distance < ratio * pair[1].distance
                for pair in matcher.knnMatch(earlier, later, k=2)
            )
            if count >= minimum:
                pairs.append((count, i, j))
    used_left, used_right = set(), set()
    for _, i, j in sorted(pairs, reverse=True):
        if i not in used_left and j not in used_right:
            used_left.add(i)
            used_right.add(j)
    return len(used_left)


def plan_scene_books(store: SweepStore, sweep_id: str, contract: dict) -> dict:
    state = store.get(sweep_id)
    if state.ended_at is None:
        raise StoreError(409, "end the sweep before scene reconciliation")
    settings = contract["scene_reconciliation"]
    sift = cv2.SIFT_create()
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    book_frames = {e.frame_id for book in state.books for e in book.evidence}
    views = []
    for frame in state.frames:
        if frame.id not in book_frames:
            continue
        view = _view(
            store.frame_path(sweep_id, frame.id).read_bytes(), frame, state.books,
            sift, float(contract["records"]["minimum_vertical_frame_fraction_for_border_candidate"]),
        )
        if view is not None:
            views.append(view)
    groups, unresolved_frames = [], []
    for view in views:
        if len(view["visible"]) < settings["minimum_matching_spines"]:
            unresolved_frames.append(view["frame_id"])
            continue
        matched_group = None
        for group in groups:
            anchors = group[-settings["recent_anchors_per_scene"]:]
            if group[0] not in anchors:
                anchors.append(group[0])
            if any(
                (matches := _matching_spines(
                    anchor, view, matcher, contract["association"]["sift_ratio_test"],
                    contract["association"]["minimum_sift_matches_when_color_changes"],
                )) >= settings["minimum_matching_spines"]
                and matches >= ceil(settings["minimum_matching_fraction"] * min(
                    len(anchor["visible"]), len(view["visible"])
                ))
                for anchor in anchors
            ):
                matched_group = group
                break
        if matched_group is None:
            groups.append([view])
        else:
            matched_group.append(view)
    supported = []
    for group in groups:
        if len(group) < settings["minimum_views_per_scene"]:
            unresolved_frames.extend(v["frame_id"] for v in group)
            continue
        counts = Counter(len(v["visible"]) for v in group)
        ranked = counts.most_common()
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            unresolved_frames.extend(v["frame_id"] for v in group)
            continue
        count = ranked[0][0]
        eligible = [v for v in group if len(v["visible"]) == count]
        representative = max(
            eligible,
            key=lambda v: (
                sum(not book.potential_duplicate for book, _, _ in v["visible"]),
                sum(e.sharpness or 0 for _, e, _ in v["visible"]),
                -v["index"],
            ),
        )
        supported.append({
            "frame_id": representative["frame_id"],
            "frame_ids": [v["frame_id"] for v in group],
            "book_ids": [book.id for book, _, _ in representative["visible"]],
            "book_count": count,
        })
    return {"supported_scenes": len(supported), "scenes": supported, "unresolved_frames": unresolved_frames}


def reconcile_scene_books(store: SweepStore, sweep_id: str, contract: dict) -> dict:
    result = plan_scene_books(store, sweep_id, contract)
    if result["supported_scenes"] == 1:
        scene = result["scenes"][0]
        store.reconcile_book_tracks(
            sweep_id, scene["book_ids"], scene["frame_id"],
            scene["frame_ids"], result["unresolved_frames"],
        )
    return result
