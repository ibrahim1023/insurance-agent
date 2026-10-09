# Current readiness — 9 October 2026

This is the current status index. [Preflight results](preflight-results.md), dated plans and `.scratch/assessment-run/` remain historical evidence. [Product direction](product-direction.md) records the user's showcase-first consumer ambition; [recovery roadmap](recovery-roadmap.md) contains researched recommendations. Documentation work does not implement those recommendations.

## Verified current checkpoint

| Capability | Status | Evidence / practical limit |
|---|---|---|
| Local capture and evidence persistence | IMPLEMENTED; real iPhone evidence exists | 59-book trial saved 117 portrait 480×640 frames in 118.123 s |
| Audible voice/interruption | Owner-observed in earlier trials | Revised guidance, current-view semantics and higher-resolution voice behavior still NOT MEASURED |
| Automatic active sweep direction | LOCAL SCHEDULER IMPLEMENTED; disabled / device NOT MEASURED | Explicit coalesced, expiring guidance shares user/tool/playback arbitration; fresh proposed vision issues and preview-freeze events only. No calibrated cheap blur/glare/occlusion or missed-row producer yet; automatic speech stays disabled pending a separately approved trial |
| Normal end and bounded work | IMPLEMENTED; partial-output trial exists | 59-book trial recorded 48.489 s postwork; completion means the attempt finished |
| Whole-sweep keyframe coverage | LOCAL FRAME ACCOUNTING REPAIRED; spatial coverage UNRESOLVED | Five distinct saved views are processed within available budget even after earlier success. Every saved view has a persisted decision/status, evidence links or explicit deferred/unresolved gap; no runtime row producer or full-shelf coverage proof exists |
| Physical book count | FAILING / unknown | 59 physical books → 71 provisional tracks, 58 duplicate flags, physical count null; frozen row detectors also fail |
| Shelf atlas registration | OFFLINE INTERFACE ONLY | Local warp/parallax controls pass; detector and runtime integration absent |
| Book identification | IMPLEMENTED; accuracy NOT MEASURED | Five accepted tracks in the 59-book trial; complete hand-legible truth absent |
| Catalogue corroboration | NOT IMPLEMENTED | Model/store slot exists; no lookup adapter |
| Book pricing | INTEGRITY IMPLEMENTED; coverage FAILING | Seeded 3/5 UAE new quotes, 0/5 used; integrated discovery 0/5 eligible URLs; saved sweeps have no accepted prices |
| Non-book classification | PROVISIONAL IMPLEMENTATION | No complete item ground truth / recall result |
| Non-book prices/ranges | NOT IMPLEMENTED | Packet validation rejects populated prices |
| Spine/shelf/room measurement | NOT IMPLEMENTED IN RUNTIME | A4 arithmetic probe exists; field corners/coplanarity/tape results BLOCKED; room fields null |
| JSON/HTML and sourced totals | IMPLEMENTED; always partial | Required unknowns/review retained; complete packet unreachable |
| Voice disconnect status | FIXED LOCALLY; device NOT MEASURED | Failed/closed/disconnected peer shows voice unavailable without stopping video/evidence/postwork; Stop remains truthful and idempotent. Same-peer transient reconnect preserves guidance cap/episode suppression |
| Public replay, review edits, export/delete workflow | PROPOSED PORTFOLIO FEATURES | No polished visitor or consumer mode exists |
| Fresh setup / final demo / final evaluator | NOT MEASURED / MISSING | No verified <15-min fresh setup, compliant dataset, final matched demo or full accuracy evaluator |

The owner-counted 59-book trial is development evidence below the final ≥60-book minimum; shelving/item/room completeness has not been independently established. Neither null outputs nor a partial packet are assignment passes.

## Regression evidence

Latest full gate, after the approved R0–R2 recovery implementation and complete changed-file reviews:

```sh
.venv/bin/python -m pytest tests/backend tests/preflight -q
node --test tests/backend/*.mjs tests/preflight/test_voice.mjs
```

**546 Python passed**, one existing Starlette/httpx deprecation warning; **160 JavaScript passed**, zero failed, both exit 0. Evidence: `.scratch/assessment-run/r1r2-final-py.log` and `r1r2-final-node.log`; red/green behavior logs and full incremental diffs are retained beside them. The full Python run exposed a test setup incompatible with the new interrupted-track-write guard (`test_running_stage_restored_as_interrupted_and_counts`); tracks now seed before crash-reload, preserving the interrupted-stage/cap assertion. The earlier documentation checkpoint was 508 Python / 120 JavaScript; it remains historical evidence, not the new gate.

The read-only [implementation review](2026-10-09-implementation-review.md) remains the failure baseline. Subsequent approved code work is recorded in `.scratch/assessment-run/panorama-progress.md`. No provider calls, checkpoints/dependencies, server launches, Git initialization or original-run rewrites were made in this recovery slice. Its final preservation check verifies three original states/packets and 315 saved JPEGs hash-unchanged. The full gate does not prove camera, count, identity, price or metric accuracy.

Frozen local coverage accounting reports 137/15, 61/8 and 117/8 saved/successfully analyzed frames across the two five-book runs and the 59-book run. The offline six-slot selection simulation retains every saved-frame denominator and explicitly leaves 116/47/103 pending views nonselected; it is not paid replay, new inventory or physical coverage. Legacy quality scores and shelf-row identities remain unknown. Raw artifacts stay private under `.scratch/assessment-run/`.

## Release decisions

1. Field-validate the local accounting/guidance/connection fixes before another broad demo attempt; obtain the missing Safari raw log, calibrate quality producers and separately approve any billed device trial. Automatic guidance remains disabled, product capture resolution unchanged.
2. Benchmark proposed detectors on labelled originals; record installation, licensing, CPU/MPS behavior and whole-pipeline latency before selection.
3. Prove title-to-listing discovery and extraction independently of upstream identity gates; preserve missing prices.
4. Validate A4 plane scale and automatic corner evidence before accepting metric fields. Room geometry stays independent and unmet until measured.
5. Publish the showcase only with declared scope, measured results and privacy-reviewed evidence. A labelled assisted/replay demonstration may show product value but cannot claim automatic assignment success.

Historical local server commands, ports and LAN IPs are examples of past launches, not assertions that a server is currently running. Check the actual interface/certificate/origin before a new phone session.
