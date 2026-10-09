import hashlib
import re

import cv2
import numpy as np

from .models import (
    AssertionRecord,
    BookTrack,
    CatalogueEvidence,
    ItemTrack,
    ObservationEvidence,
    SpineReading,
)
from .sweeps import ART_CATEGORIES, StoreError, SweepStore
_ITEM_CATEGORIES = (
    "shelving", "table", "chair", "sofa", "lamp", "coffee_machine", "art",
    "portrait", "framed_print", "rug", "monitor", "speaker", "electronics",
    "decor", "other",
)
_PERSON_CATEGORIES = {"person", "people", "human"}
_PERSON_DESCRIPTION_RE = re.compile(
    r"^\s*(?:(?:a|an|the|one|two|three|four|five|several|multiple|group of)\s+)?"
    r"(?:person|people|human|man|woman|men|women|boy|girl|child|children|adult|adults)(?:\s|$)",
    re.IGNORECASE,
)


def _person_proposal(item) -> bool:
    category = str(item.get("category") or "").strip().lower()
    if category in _PERSON_CATEGORIES:
        return True
    description = item.get("description")
    return isinstance(description, str) and bool(
        _PERSON_DESCRIPTION_RE.match(description)
    )


def _to_bytes(image) -> bytes | None:
    if image is None:
        return None
    if isinstance(image, dict):
        return image.get("jpeg")
    if isinstance(image, (bytes, bytearray)):
        return bytes(image)
    if isinstance(image, np.ndarray):
        ok, buf = cv2.imencode(".jpg", image)
        return buf.tobytes() if ok else None
    return None


def _frame_id_of(previous_image) -> str | None:
    if isinstance(previous_image, dict):
        return previous_image.get("frame_id")
    return None


def _decode(image) -> np.ndarray | None:
    data = _to_bytes(image)
    if data is None:
        return image if isinstance(image, np.ndarray) else None
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def _scene_box_to_image(bbox, coordinate_system, sent_w, sent_h, img_w, img_h):
    values = [bbox[k] for k in ("left", "top", "right", "bottom")]
    if coordinate_system == "pixels":
        x_base, y_base = sent_w, sent_h
    else:
        x_base = y_base = 1000
    left, top, right, bottom = values
    if not all(type(v) is int for v in values):
        return None
    if not (0 <= left < right <= x_base and 0 <= top < bottom <= y_base):
        return None
    x, y = left * img_w // x_base, top * img_h // y_base
    x1 = (right * img_w + x_base - 1) // x_base
    y1 = (bottom * img_h + y_base - 1) // y_base
    return {"x": x, "y": y, "w": x1 - x, "h": y1 - y}


def _crop(image, box):
    h, w = image.shape[:2]
    x0 = max(0, box["x"])
    y0 = max(0, box["y"])
    x1 = min(w, box["x"] + box["w"])
    y1 = min(h, box["y"] + box["h"])
    if x1 <= x0 or y1 <= y0:
        return None
    return image[y0:y1, x0:x1]


def _histogram(crop):
    if crop is None or crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist, alpha=1.0, norm_type=cv2.NORM_L1)
    return hist.flatten().astype(np.float32)


def _sharpness(crop):
    if crop is None or crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _align(previous_image, current_array, cfg):
    prev_data = _to_bytes(previous_image)
    prev = _decode(previous_image) if prev_data is not None else None
    cur_h, cur_w = current_array.shape[:2]
    if prev is None:
        return {"alignment": "none", "homography": None}
    prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    cur_gray = cv2.cvtColor(current_array, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=int(cfg["nfeatures"]))
    kp1, d1 = orb.detectAndCompute(prev_gray, None)
    kp2, d2 = orb.detectAndCompute(cur_gray, None)
    if d1 is None or d2 is None or len(kp1) == 0 or len(kp2) == 0:
        return {"alignment": "failed", "homography": None}
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(d1, d2, k=2)
    good = [m for pair in pairs if len(pair) == 2 for m in [pair[0]] if m.distance < float(cfg["ratio_test"]) * pair[1].distance]
    if len(good) < int(cfg["minimum_good_matches"]):
        return {"alignment": "failed", "homography": None}
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    thresh = max(1.5, 0.003125 * max(cur_w, cur_h))
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, thresh)
    if H is None or not np.isfinite(H).all() or abs(float(np.linalg.det(H))) < 1e-8:
        return {"alignment": "failed", "homography": None}
    inliers = int(mask.sum()) if mask is not None else 0
    if inliers < int(cfg["minimum_inliers"]) or inliers / len(good) < float(cfg["minimum_inlier_ratio"]):
        return {"alignment": "failed", "homography": None}
    return {"alignment": "homography", "homography": H}


def _project(H, box):
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    corners = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    x0, y0 = mapped[:, 0].min(), mapped[:, 1].min()
    x1, y1 = mapped[:, 0].max(), mapped[:, 1].max()
    return {"x": float(x0), "y": float(y0), "w": float(x1 - x0), "h": float(y1 - y0)}


def _iou(a, b):
    x0 = max(a["x"], b["x"])
    y0 = max(a["y"], b["y"])
    x1 = min(a["x"] + a["w"], b["x"] + b["w"])
    y1 = min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if inter <= 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _hist_distance(h1, h2):
    if h1 is None or h2 is None:
        return None
    return float(cv2.compareHist(np.asarray(h1, dtype=np.float32), np.asarray(h2, dtype=np.float32), cv2.HISTCMP_BHATTACHARYYA))


def _get(track, field, default=None):
    if isinstance(track, dict):
        return track.get(field, default)
    return getattr(track, field, default)


def _legible_title(track):
    title = _get(track, "title") or ""
    if title:
        return title
    for reading in reversed(_get(track, "identification_evidence", []) or []):
        if _get(reading, "title_legible") and _get(reading, "title"):
            return _get(reading, "title")
    for frag in reversed(_get(track, "observed_fragments", []) or []):
        if _get(frag, "field") == "observed_title" and _get(frag, "text"):
            return _get(frag, "text")
    return ""


def _proposal_title(scene_entry):
    if scene_entry.get("title_legible") and scene_entry.get("observed_title"):
        return scene_entry["observed_title"]
    return ""


def _text_key(text):
    return " ".join(text.split()).casefold()


def _title_key(text):
    words = _text_key(text).split()
    if len(words) > 2 and words[0] in ("a", "an", "the"):
        words = words[1:]
    return " ".join(words)


def _title_with_separate_byline(previous_title, title, author):
    words = _text_key(previous_title).split()
    byline = _text_key(author).split()
    if not byline or len(words) < len(byline) + 2:
        return False
    prefix = words[len(byline):] if words[:len(byline)] == byline else []
    suffix = words[:-len(byline)] if words[-len(byline):] == byline else []
    return any(_title_key(" ".join(part)) == _title_key(title) for part in (prefix, suffix) if part)


def _legible_author(track):
    author = _get(track, "author") or ""
    if author:
        return author
    for frag in reversed(_get(track, "observed_fragments", []) or []):
        if _get(frag, "field") == "observed_author" and _get(frag, "text"):
            return _get(frag, "text")
    return ""


def _associate(kind, proposals, existing, prev_frame_id, H, alignment, prev_array, current_array, assoc_cfg):
    """proposals: [{index, box, hist, title, scene}]
    existing: track dicts/models with id/bbox/evidence/track_histogram
    returns assignments, matched_existing_ids, unresolved proposals, review_pairs"""
    min_iou = float(assoc_cfg["minimum_projected_iou"])
    max_dist = float(assoc_cfg["maximum_histogram_distance"])
    margin = float(assoc_cfg["ambiguous_pair_iou_margin"])
    sift = cv2.SIFT_create() if kind == "book" else None
    sift_matcher = cv2.BFMatcher(cv2.NORM_L2) if sift is not None else None
    descriptors = {}

    def features(key, image, box):
        if key not in descriptors:
            crop = _crop(image, box) if image is not None else None
            descriptors[key] = (
                sift.detectAndCompute(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), None)[1]
                if crop is not None and crop.size else None
            )
        return descriptors[key]

    candidates = []
    if alignment in ("homography", "identical") and prev_frame_id is not None:
        for track in existing:
            evidence = _get(track, "evidence", []) or []
            ev = next(
                (
                    e
                    for e in reversed(evidence)
                    if _get(e, "frame_id") == prev_frame_id
                ),
                None,
            )
            if ev is None:
                continue
            box = _get(ev, "bbox")
            bbox = {"x": _get(box, "x"), "y": _get(box, "y"), "w": _get(box, "w"), "h": _get(box, "h")}
            projected = bbox if alignment == "identical" else _project(H, bbox)
            hist = None
            if prev_array is not None:
                hist = _histogram(_crop(prev_array, bbox))
            candidates.append({"track": track, "projected": projected, "hist": hist, "box": bbox})
    pairs = []
    conflicts = set()
    for prop in proposals:
        for cand in candidates:
            iou = _iou(cand["projected"], prop["box"])
            if iou < min_iou and kind != "book":
                continue
            track = cand["track"]
            track_id = _get(track, "id")
            if kind == "item" and _get(track, "category") != prop["scene"].get("category"):
                continue
            dist = _hist_distance(cand["hist"], prop["hist"])
            if iou < min_iou or dist is None or dist > max_dist:
                if sift is None:
                    continue
                old_desc = features(("old", track_id), prev_array, cand["box"])
                new_desc = features(("new", prop["index"]), current_array, prop["box"])
                if old_desc is None or new_desc is None or len(new_desc) < 2:
                    continue
                good = sum(
                    len(pair) == 2 and pair[0].distance < assoc_cfg["sift_ratio_test"] * pair[1].distance
                    for pair in sift_matcher.knnMatch(old_desc, new_desc, k=2)
                )
                if good < assoc_cfg["minimum_sift_matches_when_color_changes"]:
                    continue
            track_title = _legible_title(track)
            prop_title = prop["title"]
            if kind == "book" and track_title and prop_title and _title_key(track_title) != _title_key(prop_title):
                old_author = _legible_author(track)
                new_author = prop["scene"].get("observed_author") if prop["scene"].get("author_legible") else ""
                title_words = (_text_key(track_title).split(), _text_key(prop_title).split())
                partial_title = any(
                    len(short) == 1 and short[0] in long
                    for short, long in (title_words, title_words[::-1])
                )
                if not (
                    _get(track, "status") != "identified"
                    and (
                        (old_author and new_author and _text_key(old_author) == _text_key(new_author) and partial_title)
                        or (new_author and _text_key(track_title) == _text_key(new_author))
                        or (new_author and _title_with_separate_byline(track_title, prop_title, new_author))
                    )
                ):
                    conflicts.add((track_id, prop["index"]))
                    continue
            pairs.append({"track_id": track_id, "index": prop["index"], "iou": iou, "dist": dist})

    def ambiguous(pair):
        for other in pairs:
            if other is pair:
                continue
            if (other["track_id"] == pair["track_id"] or other["index"] == pair["index"]) and abs(other["iou"] - pair["iou"]) < margin:
                return True
        return False

    ambiguous_pairs = {id(p) for p in pairs if ambiguous(p)}
    admissible = [p for p in pairs if id(p) not in ambiguous_pairs]
    admissible.sort(key=lambda p: (-p["iou"], p["dist"], p["track_id"], p["index"]))
    assignments = []
    used_tracks = set()
    used_props = set()
    for pair in admissible:
        if pair["track_id"] in used_tracks or pair["index"] in used_props:
            continue
        used_tracks.add(pair["track_id"])
        used_props.add(pair["index"])
        assignments.append(pair)
    unresolved = []
    for prop in proposals:
        if prop["index"] in used_props:
            continue
        had_ambiguous = any(id(p) in ambiguous_pairs and p["index"] == prop["index"] for p in pairs)
        had_conflict = any(index == prop["index"] for _, index in conflicts)
        unresolved.append({"proposal": prop, "ambiguous": had_ambiguous, "conflict": had_conflict})
    flagged_tracks = set()
    for pair in pairs:
        if id(pair) in ambiguous_pairs:
            flagged_tracks.add(pair["track_id"])
    flagged_tracks.update(track_id for track_id, _ in conflicts)
    return assignments, unresolved, flagged_tracks


def associate_observation(previous_image, current_image, existing_tracks, observation, contract, *, prior_images=None) -> dict:
    frame_id = observation["frame_id"]
    scene = observation["observation"]
    mapping = observation["box_mapping"]
    img_w = int(observation["image_width"])
    img_h = int(observation["image_height"])
    coord = observation.get("coordinate_system", "normalized_1000")
    sent_w = int(observation.get("sent_image_width") or img_w)
    sent_h = int(observation.get("sent_image_height") or img_h)
    current_array = _decode(current_image)
    if current_array is None:
        raise StoreError(422, "undecodable current image")

    prev_data = _to_bytes(previous_image)
    cur_data = _to_bytes(current_image)
    prev_frame_id = _frame_id_of(previous_image)
    prev_array = _decode(previous_image) if prev_data is not None else None
    align_cfg = contract["frame_alignment"]
    if prev_data is not None and cur_data is not None and hashlib.sha256(prev_data).hexdigest() == hashlib.sha256(cur_data).hexdigest():
        aligned = {"alignment": "identical", "homography": np.eye(3)}
    else:
        aligned = _align(previous_image, current_array, align_cfg)
    alignment = aligned["alignment"]
    H = aligned["homography"]
    assoc = contract["association"]
    nonbook_min = float(contract["records"]["nonbook_confidence_min"])

    book_props = []
    partial_books = False
    border_fraction = float(contract["records"]["minimum_vertical_frame_fraction_for_border_candidate"])
    for position, scene_index in enumerate(mapping.get("accepted_indices", [])):
        book = scene["books"][scene_index]
        x, y, w, h = mapping["boxes"][position]
        if (y <= 0 or y + h >= img_h) and h / img_h < border_fraction:
            partial_books = True
            continue
        crop = _crop(current_array, {"x": x, "y": y, "w": w, "h": h})
        book_props.append({
            "index": scene_index,
            "box": {"x": x, "y": y, "w": w, "h": h},
            "hist": _histogram(crop),
            "sharpness": _sharpness(crop),
            "title": _proposal_title(book),
            "scene": book,
        })
    item_props = []
    for scene_index, item in enumerate(scene.get("items", [])):
        if _person_proposal(item):
            continue
        box = _scene_box_to_image(item["bbox"], coord, sent_w, sent_h, img_w, img_h)
        confidence = item.get("confidence")
        if box is None or not isinstance(confidence, (int, float)) or confidence < nonbook_min:
            continue
        crop = _crop(current_array, box)
        item_props.append({
            "index": scene_index,
            "box": box,
            "hist": _histogram(crop),
            "sharpness": _sharpness(crop),
            "title": "",
            "scene": item,
        })

    failed = alignment == "failed"
    book_assign, book_unres, book_flagged = _associate(
        "book", book_props, existing_tracks["books"], prev_frame_id, H, alignment, prev_array, current_array, assoc)
    for prior in prior_images or []:
        if not book_unres:
            break
        prior_id = _frame_id_of(prior)
        prior_array = _decode(prior)
        if not prior_id or prior_array is None:
            continue
        if _to_bytes(prior) == cur_data:
            earlier = {"alignment": "identical", "homography": np.eye(3)}
        else:
            earlier = _align(prior, current_array, align_cfg)
        assigned_ids = {a["track_id"] for a in book_assign}
        available = [t for t in existing_tracks["books"] if _get(t, "id") not in assigned_ids]
        additional, book_unres, flagged = _associate(
            "book", [entry["proposal"] for entry in book_unres], available,
            prior_id, earlier["homography"], earlier["alignment"], prior_array,
            current_array, assoc,
        )
        book_assign.extend(additional)
        book_flagged.update(flagged)
    item_assign, item_unres, item_flagged = _associate(
        "item", item_props, existing_tracks["items"], prev_frame_id, H, alignment, prev_array, current_array, assoc)

    reviews = []
    if partial_books:
        reviews.append({"ref_id": frame_id, "reason": "partial book spine at frame border; complete view needed before counting"})
    result = {
        "frame_id": frame_id,
        "alignment": alignment,
        "assignments": [],
        "new_books": [],
        "new_items": [],
        "flag_tracks": sorted(book_flagged | item_flagged),
        "review_items": [],
        "unresolved": 0,
    }

    def ibox(box):
        return {
            "x": max(0, int(round(box["x"]))),
            "y": max(0, int(round(box["y"]))),
            "w": max(1, int(round(box["w"]))),
            "h": max(1, int(round(box["h"]))),
        }

    def fragments(entry):
        out = []
        for field in ("observed_title", "observed_author", "observed_publisher", "observed_edition", "observed_isbn"):
            text = entry.get(field)
            if isinstance(text, str) and text:
                out.append({"field": field, "text": text})
        return out

    def evidence(prop):
        return {
            "frame_id": frame_id,
            "frame_ref": f"frames/{frame_id}.jpg",
            "bbox": ibox(prop["box"]),
            "image_width": img_w,
            "image_height": img_h,
            "sharpness": prop["sharpness"],
        }

    all_props = {("book", p["index"]): p for p in book_props}
    all_props.update({("item", p["index"]): p for p in item_props})
    for pair in book_assign:
        result["assignments"].append({"kind": "book", "track_id": pair["track_id"], "proposal_index": pair["index"]})
    for pair in item_assign:
        result["assignments"].append({"kind": "item", "track_id": pair["track_id"], "proposal_index": pair["index"]})
    result["applied_props"] = []
    for assigned in result["assignments"]:
        prop = all_props.get((assigned["kind"], assigned["proposal_index"]))
        if prop is None:
            continue
        result["applied_props"].append({
            "kind": assigned["kind"],
            "index": prop["index"],
            "evidence": evidence(prop),
            "hist": prop["hist"].tolist() if prop["hist"] is not None else None,
            "fragments": fragments(prop["scene"]) if assigned["kind"] == "book" else [],
            "flags": (prop["scene"].get("special_flags") or []) if assigned["kind"] == "book" else [],
        })

    if failed:
        reviews.append({"ref_id": frame_id, "reason": "frame alignment failed; observations retained as unresolved associations"})
    for entry in book_unres:
        prop = entry["proposal"]
        flags = prop["scene"].get("special_flags") or []
        payload = {
            "frame_ref": f"frames/{frame_id}.jpg",
            "status": "needs_appraisal" if flags else "unidentified",
            "bbox": ibox(prop["box"]),
            "image_width": img_w,
            "image_height": img_h,
            "observed_fragments": fragments(prop["scene"]),
            "evidence": [evidence(prop)],
            "potential_duplicate": entry["ambiguous"]
            or entry["conflict"]
            or (failed and len(existing_tracks["books"]) > 0),
            "track_histogram": prop["hist"].tolist() if prop["hist"] is not None else None,
        }
        result["new_books"].append(payload)
        if entry["conflict"]:
            reviews.append({"ref_id": frame_id, "reason": "conflicting legible title; retained as unresolved potential duplicate"})
        elif (failed and len(existing_tracks["books"]) > 0) or entry["ambiguous"]:
            reviews.append({"ref_id": frame_id, "reason": "potential duplicate book proposal retained unresolved"})
    for entry in item_unres:
        prop = entry["proposal"]
        scene_item = prop["scene"]
        category = scene_item.get("category", "other")
        art = category in ART_CATEGORIES
        result["new_items"].append({
            "category": category,
            "description": scene_item.get("description", ""),
            "material": scene_item.get("material", ""),
            "brand_model": scene_item.get("brand_model", ""),
            "frame_ref": f"frames/{frame_id}.jpg",
            "status": "needs_appraisal" if art else "range",
            "valuation_state": "unpriced",
            "confidence": scene_item.get("confidence"),
            "bbox": ibox(prop["box"]),
            "image_width": img_w,
            "image_height": img_h,
            "evidence": [evidence(prop)],
            "potential_duplicate": entry["ambiguous"]
            or (failed and len(existing_tracks["items"]) > 0),
            "track_histogram": prop["hist"].tolist() if prop["hist"] is not None else None,
        })
        if (failed and len(existing_tracks["items"]) > 0) or entry["ambiguous"]:
            reviews.append({"ref_id": frame_id, "reason": "potential duplicate item proposal retained unresolved"})
    result["review_items"] = reviews
    result["unresolved"] = len(book_unres) + len(item_unres)
    return result


def _catalogue_corroborates(reading, evidence: CatalogueEvidence | None) -> bool:
    if evidence is None or not evidence.url or not evidence.retrieved_at:
        return False
    return bool(reading.get("title")) and evidence.matched_title == reading["title"]


def commit_observation(store: SweepStore, sweep_id: str, frame_id: str, reply: dict, jpeg: bytes, contract: dict):
    state = store.get(sweep_id)
    prev_id = state.last_analyzed_frame_id
    source_id = reply.get("source_frame_id")
    if reply.get("cached") and source_id and (
        any(e.frame_id == source_id for t in state.books for e in t.evidence)
        or any(e.frame_id == source_id for t in state.items for e in t.evidence)
    ):
        prev_id = source_id
    previous = None
    if prev_id is not None:
        try:
            previous = {"jpeg": store.frame_path(sweep_id, prev_id).read_bytes(), "frame_id": prev_id}
        except (StoreError, OSError):
            previous = None
    prior_images = []
    if state.books and reply["box_mapping"].get("accepted_indices"):
        book_frames = {
            e.frame_id for book in state.books for e in book.evidence
            if e.frame_id not in (prev_id, frame_id)
        }
        recent = (
            f.id for f in reversed(state.frames) if f.id in book_frames
        )
        for prior_id in list(recent)[:contract["association"]["maximum_prior_book_frames"]]:
            try:
                prior_images.append({"jpeg": store.frame_path(sweep_id, prior_id).read_bytes(), "frame_id": prior_id})
            except (StoreError, OSError):
                continue
    result = associate_observation(
        previous,
        jpeg,
        {"books": state.books, "items": state.items},
        reply,
        contract,
        prior_images=prior_images,
    )
    return store.commit_tracks(sweep_id, frame_id, result)
