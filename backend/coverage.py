import math

from .models import (
    CoverageDecision,
    CoverageGap,
    CoverageManifest,
    FrameRecord,
    ShelfAtlasRecord,
)


def _score(quality: dict, frame_id: str):
    value = quality.get(frame_id)
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        return None
    return float(value)


def _rank(frame: FrameRecord, order: dict, quality: dict):
    score = _score(quality, frame.id)
    return (0 if score is not None else 1, -(score or 0.0), order[frame.id])


def select_keyframes(
    saved_frames: list[FrameRecord],
    quality: dict,
    observed_rows: list[ShelfAtlasRecord],
    budget: int,
) -> CoverageManifest:
    frames = sorted(saved_frames, key=lambda f: (f.timestamp_s, f.id))
    ids = [f.id for f in frames]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate saved frame id")
    known = set(ids)
    for frame_id in quality:
        if frame_id not in known:
            raise ValueError("quality references unknown frame id")
    row_ids = [row.id for row in observed_rows]
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("duplicate row id")
    for row in observed_rows:
        if len(set(row.frame_ids)) != len(row.frame_ids):
            raise ValueError("duplicate frame membership in row")
        for frame_id in row.frame_ids:
            if frame_id not in known:
                raise ValueError("row references unknown frame id")
        for span in row.covered_ranges:
            if not all(math.isfinite(v) for v in span) or span[0] > span[1]:
                raise ValueError("row covered range must be finite and ordered")
    rows = [row for row in observed_rows if row.status == "supported"]

    order = {f.id: index for index, f in enumerate(frames)}
    budget = max(0, int(budget))

    membership: dict[str, list[ShelfAtlasRecord]] = {f.id: [] for f in frames}
    for row in rows:
        for frame_id in row.frame_ids:
            membership[frame_id].append(row)
    ordered_rows = sorted(
        rows,
        key=lambda row: (min(order[fid] for fid in row.frame_ids), row.id),
    )

    primary_by_sha: dict[str, str] = {}
    for frame in frames:
        if frame.sha256 is not None and frame.sha256 not in primary_by_sha:
            primary_by_sha[frame.sha256] = frame.id

    selected: dict[str, CoverageDecision] = {}
    chosen: set[str] = set()
    slots = budget

    def pick(frame, row, reason):
        nonlocal slots
        duplicate_of = None
        if (
            frame.sha256 is not None
            and primary_by_sha.get(frame.sha256) != frame.id
        ):
            duplicate_of = primary_by_sha.get(frame.sha256)
        selected[frame.id] = CoverageDecision(
            frame_id=frame.id,
            disposition="selected",
            reason=reason,
            row_id=row.id if row is not None else None,
            covered_range=None,
            duplicate_of=duplicate_of,
            processing_status="pending",
        )
        chosen.add(frame.id)
        slots -= 1

    row_members: dict[str, list[FrameRecord]] = {}
    frame_lookup = {f.id: f for f in frames}
    for row in ordered_rows:
        members = [
            frame_lookup[fid]
            for fid in row.frame_ids
            if len(membership[fid]) == 1
        ]
        members.sort(key=lambda f: _rank(f, order, quality))
        row_members[row.id] = members

    for row in ordered_rows:
        if slots <= 0:
            break
        candidates = [f for f in row_members[row.id] if f.id not in chosen]
        if candidates:
            pick(candidates[0], row, "best-quality view for atlas row")

    unassigned = [f for f in frames if not membership[f.id]]
    k = min(slots, len(unassigned))
    if k > 0:
        bins: list[list[FrameRecord]] = [[] for _ in range(k)]
        boundaries = [(index * len(unassigned)) // k for index in range(k + 1)]
        for index, frame in enumerate(unassigned):
            for slot_index in range(k):
                if boundaries[slot_index] <= index < boundaries[slot_index + 1]:
                    bins[slot_index].append(frame)
                    break
        for members in bins:
            if not members or slots <= 0:
                continue
            best = min(members, key=lambda f: _rank(f, order, quality))
            pick(best, None, "best-quality view in unassigned span")

    progressed = True
    while slots > 0 and progressed:
        progressed = False
        for row in ordered_rows:
            if slots <= 0:
                break
            remaining = [f for f in row_members[row.id] if f.id not in chosen]
            if remaining:
                pick(remaining[0], row, "additional view for atlas row")
                progressed = True

    gaps: dict[tuple, CoverageGap] = {}

    def record_gap(frame_id, row_id, reason):
        key = (row_id, reason)
        if key not in gaps:
            gaps[key] = CoverageGap(
                frame_ids=[], row_id=row_id, reason=reason
            )
        gaps[key].frame_ids.append(frame_id)

    entries: list[CoverageDecision] = []
    for frame in frames:
        if frame.id in selected:
            entries.append(selected[frame.id])
            continue
        rows_for_frame = membership[frame.id]
        duplicate_of = None
        if (
            frame.sha256 is not None
            and primary_by_sha.get(frame.sha256) != frame.id
        ):
            duplicate_of = primary_by_sha.get(frame.sha256)
        if len(rows_for_frame) > 1:
            entries.append(
                CoverageDecision(
                    frame_id=frame.id,
                    disposition="unresolved",
                    reason="frame belongs to multiple atlas rows",
                    row_id=None,
                    covered_range=None,
                    duplicate_of=duplicate_of,
                    processing_status="skipped",
                )
            )
            record_gap(
                frame.id, None, "frame belongs to multiple atlas rows"
            )
        else:
            row = rows_for_frame[0] if rows_for_frame else None
            row_id = row.id if row is not None else None
            entries.append(
                CoverageDecision(
                    frame_id=frame.id,
                    disposition="deferred",
                    reason="not selected within vision budget",
                    row_id=row_id,
                    covered_range=None,
                    duplicate_of=duplicate_of,
                    processing_status="skipped",
                )
            )
            record_gap(
                frame.id, row_id, "not selected within vision budget"
            )

    return CoverageManifest(
        entries=entries,
        gaps=list(gaps.values()),
        spatial_coverage_status="unresolved",
    )
