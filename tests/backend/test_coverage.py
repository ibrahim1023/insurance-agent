import math

import pytest
from pydantic import ValidationError

from backend.coverage import select_keyframes
from backend.models import (
    CoverageDecision,
    CoverageManifest,
    FrameRecord,
    ShelfAtlasRecord,
)


def frame(fid, ts, sha=None, score=None):
    return FrameRecord(
        id=fid,
        frame_ref=f"frames/{fid}.jpg",
        timestamp_s=ts,
        width_px=640,
        height_px=360,
        sha256=sha,
        sharpness_score=score,
    )


def row(rid, frame_ids, ranges=None, status="supported"):
    return ShelfAtlasRecord(
        id=rid,
        frame_ids=list(frame_ids),
        frame_transforms={
            fid: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
            for fid in frame_ids
        },
        width_px=1280,
        height_px=360,
        covered_ranges=list(ranges or [(0.0, 1.0)]),
        gaps=[],
        status=status,
    )


def quality_map(frames):
    return {f.id: f.sharpness_score for f in frames}


def selected_ids(manifest):
    return [e.frame_id for e in manifest.entries if e.disposition == "selected"]


def test_selection_keeps_all_five_when_budget_allows():
    frames = [
        frame(f"f{i}", float(i), sha=f"sha{i}", score=float(10 - i))
        for i in range(5)
    ]
    manifest = select_keyframes(
        frames, quality_map(frames), observed_rows=[], budget=5
    )
    assert isinstance(manifest, CoverageManifest)
    assert manifest.version == "coverage-v1"
    assert [e.frame_id for e in manifest.entries] == [f.id for f in frames]
    assert selected_ids(manifest) == [f.id for f in frames]
    assert manifest.spatial_coverage_status == "unresolved"
    for entry in manifest.entries:
        assert entry.row_id is None
        assert entry.covered_range is None
        assert entry.processing_status == "pending"
        assert entry.reason
    assert manifest.gaps == []


def test_rows_receive_first_view_before_revisits():
    f1 = frame("f1", 1.0, sha="a", score=0.1)
    f2 = frame("f2", 2.0, sha="b", score=0.9)
    f3 = frame("f3", 3.0, sha="c", score=0.2)
    f4 = frame("f4", 4.0, sha="d", score=0.8)
    f5 = frame("f5", 5.0, sha="e", score=0.5)
    frames = [f1, f2, f3, f4, f5]
    rows = [
        row("row_a", ["f1", "f2"], ranges=[(0.0, 0.5)]),
        row("row_b", ["f3", "f4"], ranges=[(0.5, 1.0)]),
    ]

    manifest = select_keyframes(frames, quality_map(frames), rows, budget=3)
    assert set(selected_ids(manifest)) == {"f2", "f4", "f5"}
    by_id = {e.frame_id: e for e in manifest.entries}
    assert by_id["f2"].row_id == "row_a"
    assert by_id["f2"].covered_range is None
    assert by_id["f4"].row_id == "row_b"
    assert by_id["f4"].covered_range is None
    assert by_id["f5"].row_id is None
    assert by_id["f1"].disposition == "deferred"
    assert by_id["f3"].disposition == "deferred"
    deferred = {fid for fid, e in by_id.items() if e.disposition == "deferred"}
    gap_ids = {fid for gap in manifest.gaps for fid in gap.frame_ids}
    assert deferred <= gap_ids

    manifest = select_keyframes(frames, quality_map(frames), rows, budget=4)
    assert set(selected_ids(manifest)) == {"f2", "f4", "f5", "f1"}
    assert {e.frame_id for e in manifest.entries} == {
        f.id for f in frames
    }
    assert len(manifest.entries) == 5


def test_unregistered_views_remain_spatially_unknown():
    frames = [
        frame(f"f{i}", float(i), sha=f"sha{i}", score=float(i))
        for i in range(3)
    ]
    manifest = select_keyframes(
        frames, quality_map(frames), observed_rows=[], budget=2
    )
    assert manifest.spatial_coverage_status == "unresolved"
    assert all(
        e.covered_range is None and e.row_id is None
        for e in manifest.entries
    )
    assert len(selected_ids(manifest)) == 2
    assert sum(1 for e in manifest.entries if e.disposition == "deferred") == 1


def test_budget_zero_and_overflow_leave_explicit_gaps():
    frames = [
        frame(f"f{i}", float(i), sha=f"sha{i}", score=float(i))
        for i in range(5)
    ]
    manifest = select_keyframes(
        frames, quality_map(frames), observed_rows=[], budget=0
    )
    assert selected_ids(manifest) == []
    assert [e.frame_id for e in manifest.entries] == [f.id for f in frames]
    assert all(
        e.disposition == "deferred" and e.reason
        for e in manifest.entries
    )
    gap_ids = [fid for gap in manifest.gaps for fid in gap.frame_ids]
    assert sorted(gap_ids) == [f.id for f in frames]
    assert all(gap.reason for gap in manifest.gaps)

    manifest = select_keyframes(
        frames, quality_map(frames), observed_rows=[], budget=2
    )
    assert len(selected_ids(manifest)) == 2
    deferred = [
        e.frame_id
        for e in manifest.entries
        if e.disposition == "deferred"
    ]
    assert len(deferred) == 3
    gap_ids = [fid for gap in manifest.gaps for fid in gap.frame_ids]
    assert sorted(gap_ids) == sorted(deferred)


def test_identical_bytes_do_not_merge_rows():
    f0 = frame("f0", 0.0, sha="dupsha", score=0.5)
    f1 = frame("f1", 1.0, sha="dupsha", score=0.4)
    f2 = frame("f2", 2.0, sha="x", score=0.7)
    f3 = frame("f3", 3.0, sha="y", score=0.6)
    frames = [f0, f1, f2, f3]
    rows = [row("row_a", ["f0", "f2"]), row("row_b", ["f1", "f3"])]
    manifest = select_keyframes(frames, quality_map(frames), rows, budget=4)
    assert len(manifest.entries) == 4
    by_id = {e.frame_id: e for e in manifest.entries}
    assert by_id["f1"].duplicate_of == "f0"
    assert by_id["f0"].duplicate_of is None
    assert by_id["f0"].row_id == "row_a"
    assert by_id["f1"].row_id == "row_b"
    assert set(selected_ids(manifest)) == {"f0", "f2", "f1", "f3"}


def test_row_extents_do_not_leak_into_frame_decisions():
    frames = [
        frame(f"f{i}", float(i), sha=f"s{i}", score=float(i))
        for i in range(3)
    ]
    rows = [row("row_a", ["f0", "f1"], ranges=[(0.2, 0.8), (1.4, 2.0)])]
    manifest = select_keyframes(frames, quality_map(frames), rows, budget=2)
    by_id = {e.frame_id: e for e in manifest.entries}
    assert by_id["f0"].row_id is None or by_id["f0"].covered_range is None
    assert all(e.covered_range is None for e in manifest.entries)
    selected = [e for e in manifest.entries if e.disposition == "selected"]
    assert any(e.row_id == "row_a" for e in selected)


def test_invalid_rows_rejected():
    frames = [frame(f"f{i}", float(i)) for i in range(3)]
    with pytest.raises(ValueError):
        select_keyframes(
            frames,
            {},
            [
                row("row_a", ["f0", "f1"]),
                row("row_a", ["f1", "f2"]),
            ],
            budget=2,
        )
    with pytest.raises(ValueError):
        select_keyframes(
            frames, {}, [row("row_a", ["f0", "f0", "f1"])], budget=2
        )
    with pytest.raises(ValueError):
        select_keyframes(
            frames,
            {},
            [row("row_a", ["f0", "ghost"], status="unresolved")],
            budget=2,
        )
    with pytest.raises(ValueError):
        select_keyframes(
            frames,
            {},
            [row("row_a", ["f0", "f1"], ranges=[(math.inf, 1.0)])],
            budget=2,
        )
    with pytest.raises(ValueError):
        select_keyframes(
            frames,
            {},
            [row("row_a", ["f0", "f1"], ranges=[(0.9, 0.2)])],
            budget=2,
        )


def test_conflicting_row_membership_stays_unresolved():
    frames = [
        frame(f"f{i}", float(i), sha=f"s{i}", score=float(i))
        for i in range(3)
    ]
    rows = [row("row_a", ["f0", "f1"]), row("row_b", ["f1", "f2"])]
    manifest = select_keyframes(frames, quality_map(frames), rows, budget=3)
    by_id = {e.frame_id: e for e in manifest.entries}
    assert by_id["f1"].disposition == "unresolved"
    assert "multiple" in by_id["f1"].reason
    assert by_id["f0"].row_id == "row_a"
    assert by_id["f2"].row_id == "row_b"
    gap_ids = [fid for gap in manifest.gaps for fid in gap.frame_ids]
    assert "f1" in gap_ids


def test_empty_and_invalid_inputs():
    manifest = select_keyframes([], {}, [], budget=3)
    assert manifest.entries == []
    assert manifest.spatial_coverage_status == "unresolved"

    frames = [frame("f0", 0.0), frame("f1", 1.0)]
    with pytest.raises(ValueError):
        select_keyframes(frames, {"ghost": 1.0}, [], budget=1)
    with pytest.raises(ValueError):
        select_keyframes(
            frames, {}, [row("row_a", ["f0", "ghost"])], budget=1
        )
    with pytest.raises(ValueError):
        select_keyframes(
            [frame("f0", 0.0), frame("f0", 1.0)], {}, [], budget=1
        )


def test_nonfinite_and_missing_quality_rank_below_known_scores():
    frames = [
        frame("f0", 0.0),
        frame("f1", 1.0, score=0.5),
        frame("f2", 2.0),
    ]
    manifest = select_keyframes(
        frames,
        {"f0": math.inf, "f1": 0.5, "f2": math.nan},
        [],
        budget=1,
    )
    assert selected_ids(manifest) == ["f1"]
    with pytest.raises(ValidationError):
        CoverageDecision(
            frame_id="f0",
            disposition="selected",
            reason="x",
            covered_range=(0.0, math.nan),
        )


def test_selection_is_permutation_stable():
    ordered = [
        frame(f"f{i}", float(i), sha=f"sha{i}", score=float(5 - i))
        for i in range(5)
    ]
    shuffled = [ordered[i] for i in (3, 0, 4, 1, 2)]
    a = select_keyframes(ordered, quality_map(ordered), [], budget=3)
    b = select_keyframes(shuffled, quality_map(shuffled), [], budget=3)
    assert a.model_dump() == b.model_dump()
    assert [e.frame_id for e in b.entries] == [f.id for f in ordered]


def test_missing_score_does_not_reject_frame():
    frames = [frame("f0", 0.0, score=None), frame("f1", 1.0, score=None)]
    manifest = select_keyframes(frames, {}, [], budget=1)
    assert len(selected_ids(manifest)) == 1
    assert selected_ids(manifest) == ["f0"]
    deferred = [
        e for e in manifest.entries if e.disposition == "deferred"
    ]
    assert len(deferred) == 1 and deferred[0].reason
