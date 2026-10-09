# Library Claim Preflight Experiments Implementation Plan

> **9 October navigation amendment:** preserve the experiment protocol and dated
> failures below. Current results/blockers are indexed in [readiness](../../readiness.md);
> researched additional probes are in [recovery roadmap](../../recovery-roadmap.md)
> and [research](../../research/README.md). Manual-corner, seeded-URL and mock-device
> successes remain separate from automatic field performance. The new detector,
> catalogue and higher-resolution choices are unmeasured candidates.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans according to the user's selected execution method. Steps use checkbox (`- [ ]`) syntax for tracking. These probes answer design questions; do not turn them into the full product without reviewing measured results.

**Goal:** Measure four independent risks—iPhone Safari concurrency, five-book UAE price retrieval, spine candidate detection on the current shelves, and A4-based spine measurement—before fixing the final product implementation.

**Architecture:** Four small probes share only a results template. Keep experiments independent so a failed room-geometry assumption cannot block audio, pricing, or spine work. Record observed outputs, provenance and blockers; unsupported room measurements remain `null`.

**Tech Stack:** Minimal browser JS and FastAPI for Safari/WebRTC; Python/OpenCV + NumPy for image probes; Python HTTP/HTML parsing for public price listings; pytest for deterministic unit checks. Use the versions available/selected at execution time and document them, rather than claiming an untested integration works.

**Spec:** `docs/superpowers/specs/2026-10-06-library-claim-agent-design.md`; source acceptance bars: `build-spec.md` sections 1, 8–18, 23–24, 34–36. This plan is the **preflight slice** of the approved full-build design. After reporting the probes, write the conditional full-build implementation plan rather than presuming a successful result.

## Global Constraints

- One uninterrupted iPhone browser sweep under three minutes; full-duplex speech via OpenAI Realtime WebRTC; no native iOS code.
- A4 is 21.0 × 29.7 cm, placed in approximately the same vertical plane as the sampled spines. A flat shelf-top marker cannot validate vertical-spine scale.
- Primary locale UAE/AED; five real book identities, not a hardcoded catalogue; every price requires a real source URL and retrieval timestamp. Never infer price from model memory.
- Null for unsupported dimensions or prices; never claim a pass bar from simulated data or infer whole-room geometry from one A4 plane.
- No auth, hosted database, generic agent framework, added issue tracker, or production infrastructure. No secrets/captured private data in the repository.
- There is no Git repository yet and the user chose not to initialize one; replace commit steps with recorded review checkpoints until that decision changes.

## Review Focus

- Safari permissions or secure-origin failures: record device/browser/origin/error; label device probe BLOCKED, not PASS (Task 1 live test).
- WebRTC interrupts camera capture, or uploads interrupt voice: record simultaneous duration, upload successes/failures, and interruption result, not independent feature demos (Task 1 live test).
- Search results or JSON-LD list multiple sellers/variants: do not attach an ambiguous or non-AED offer to a book (Task 2 parser test).
- Spine candidates merge adjacent books or miss flat stacks: measure per-image precision/recall and error cases, not only visual examples (Task 3 shelf test).
- Marker/quad belongs to a different plane or has invalid corners: return null with reason rather than a number (Task 4 test and live measurement).

## Inputs and result contract

Inputs to request, not fabricate: access to a LiDAR-capable iPhone running Safari and permission to test a secure local URL; an OpenAI API key supplied securely; five distinct ordinary books and their readable titles/authors; 3–5 current-shelf images with hand-marked spine rectangles/counts (including a flat/stacked example if present); a same-plane A4 photo with 5–20 books and tape-measured spine heights/thicknesses. Avoid people/documents. If any input is unavailable, complete independent probes and mark only that probe `BLOCKED` in `docs/preflight-results.md` with the missing input. Record versions, exact command, dataset/date, denominator, failures and manual steps. A desktop simulation or synthetic fixture is never an iPhone result.

## Task 1: Safari camera + voice concurrency probe

**Files:** Create `preflight/safari/index.html` (standalone JS browser probe), `preflight/safari/server.py` (minimal FastAPI route and OpenAI SDP exchange), `tests/preflight/test_safari_server.py`, and Task 1 section in `docs/preflight-results.md`.

**Interfaces:** `POST /preflight/session` takes the browser SDP offer and returns provider SDP answer without revealing the server API key; `POST /preflight/frame` accepts one JPEG + timestamp and returns `{saved: bool, frame_id: str}`. A throwaway probe, not a general product session API.

- [ ] Write test `test_rejects_non_jpeg_and_missing_key` (invalid content type -> 4xx; missing key -> failure before provider call) with stubbed provider; run `python -m pytest tests/preflight/test_safari_server.py -v` and observe FAIL before adding routes.
- [ ] Implement the two routes in `preflight/safari/server.py`, with a bounded upload size and no key sent to the client; use OpenAI's current official Realtime WebRTC server SDP flow after checking docs. Rerun the test: PASS.
- [ ] Implement `preflight/safari/index.html`: from a tap, request simultaneous camera/mic, start video preview + WebRTC audio tracks, send one JPEG per second, display response audio and upload successes/errors, and expose a button to trigger a spoken interruption. Keep camera preview active while the user speaks and agent responds.
- [ ] On the **real iPhone over a secure origin**, record iPhone/iOS/Safari version, URL origin type, uninterrupted camera-preview seconds (target 120), expected vs successful uploads (target ≥95%), user/agent overlap and observed interruption success, audio-output count, audio/video dropouts and median/p95 upload latency. Try at least one continuous 120-second run. Failing/missing access is FAIL/BLOCKED with reason; never count a desktop run as pass. Add findings to the result file. If a certificate or device setup requires owner action, stop for the owner rather than weaken secure-origin requirements.
- [ ] Review checkpoint: only this probe determines whether proposed browser voice+camera concurrency is viable; it does not gate the other three probes.

## Task 2: Five-book UAE listing retrieval probe

**Files:** Create `preflight/prices.py`, `preflight/probe_prices.py`, `tests/preflight/test_prices.py`, and Task 2 section in `docs/preflight-results.md`. Keep private book inputs and retrieved pages out of tracked docs if they include personal data.

**Interfaces:** `extract_offer(html: str, page_url: str, retrieved_at: str, expected_title: str, expected_author: str) -> dict | None` returns a sourced, identity-matched AED `amount`, `currency`, `source`, `url`, `retrieved_at`, `availability`, or `None`. `probe_prices.py` accepts a JSON array of five `{title, author}` records and writes per-book `replacement`, `used`, and reason for missing results. Title/author corroboration and new/used condition must be inspectable on the listing; label unproven condition missing, not used. URL discovery (search or store navigation) is measured separately from extracting a price at a supplied URL.

- [ ] Write tests `test_valid_aed_offer`, `test_mismatched_title_is_missing`, `test_mismatched_author_is_missing`, `test_foreign_currency_is_missing`, and `test_ambiguous_variant_is_missing` using tiny offline HTML fixtures; run `python -m pytest tests/preflight/test_prices.py -v` and observe FAIL.
- [ ] Implement conservative JSON-LD Product/Offer extraction and explicit title/currency/availability checks. No price from snippets, search result summaries, inaccessible pages, remembered prices, or ambiguous variants. Rerun tests: PASS.
- [ ] For each of five actual supplied books, discover real publicly accessible UAE replacement and used listings where possible. Fetch each page without bypassing protections; record HTTP status, whether search/discovery worked, whether full listing was retrievable, title match, AED price/condition/availability, URL and UTC retrieval time, and time spent. A manually supplied URL establishes extraction only, not automated discovery. Leave missing side null with reason.
- [ ] Report coverage as `replacement_found/5` and `used_found/5`, plus `both_found/5`, reproducible source classes, broken URLs and source restrictions. Do not claim 15-book accuracy or ten-book US support from this five-book probe. Review checkpoint: decide whether price discovery/retrieval is feasible and what remains unproven.

## Task 3: Spine candidates on current shelves

**Files:** Create `preflight/spines.py`, `preflight/probe_spines.py`, `tests/preflight/test_spines.py`, and Task 3 section in `docs/preflight-results.md`.

**Interfaces:** `detect_spines(image: np.ndarray) -> list[tuple[int, int, int, int]]` returns pixel `(x, y, width, height)` proposals only, not title or track IDs. `probe_spines.py` accepts local images + labelled bounding-box JSON and emits per-image detections, precision/recall using maximum-cardinality one-to-one matching at IoU ≥0.5, and count error `100 * abs(predicted - truth) / truth` for nonzero truth, with saved overlays outside tracked docs. Empty denominators are null with an explicit reason, never a fabricated rate.

- [ ] Write tests `test_clear_parallel_spines` (synthetic five spines -> five candidates within image bounds) and `test_blank_image` (zero candidates); run `python -m pytest tests/preflight/test_spines.py -v` and observe FAIL.
- [ ] Implement one minimal OpenCV baseline (grayscale/contrast, edge or gradient proposals, contour/aspect filtering); avoid model training, OCR-as-count, or scene-specific coordinates. Rerun unit tests: PASS.
- [ ] Run on all 3–5 current-shelf images; independently hand-label each physical visible spine, including horizontal stacks if present. Report ground truth/candidate/matched/false positives/misses and elapsed ms per image; show failure crops and separate upright versus flat/stacked results. Mark absent orientations `NOT MEASURED`, not 0% recall. Review checkpoint: choose whether this baseline is sufficient for tracking, needs a model-backed detector, or is inconclusive for dense unseen shelves.

## Task 4: Same-plane A4 measurement probe

**Files:** Create `preflight/measure.py`, `preflight/probe_measure.py`, `tests/preflight/test_measure.py`, and Task 4 section in `docs/preflight-results.md`.

**Interfaces:** `measure_spine_cm(marker_quad: np.ndarray | None, spine_quad: np.ndarray, *, same_plane: bool) -> tuple[float, float] | None` returns `(height_cm, thickness_cm)` after homography to A4 coordinates, or `None` when corners/plane are untrusted. The probe consumes manually annotated four-corner marker and spine quadrilaterals plus tape truth; it does not claim automatic A4/spine corner detection. Corner order is top-left, top-right, bottom-right, bottom-left for an upright portrait A4 marker mapped to `(0,0), (21,0), (21,29.7), (0,29.7)` in cm. Spine height is the mean transformed left/right edge length; thickness is the mean top/bottom edge length. Mean absolute percentage error uses matched measurable spines only; report measured versus requested sample counts alongside it, and no rate when no measurements are available.

- [ ] Write tests `test_rectified_known_rectangle` (synthetic same-plane 29.7 × 21.0 cm marker and 24 × 2 cm spine -> within 0.1 cm), `test_missing_or_degenerate_marker` -> None, and `test_other_plane` -> None; run `python -m pytest tests/preflight/test_measure.py -v` and observe FAIL.
- [ ] Implement `measure_spine_cm` with OpenCV perspective transform and explicit degenerate/finite/plane checks; rerun tests: PASS.
- [ ] Use a real photo of A4 mounted vertically beside books, mark corners and tape-measure at least five book spines (aim for 20 if available). Report paired predicted/truth height and thickness, mean absolute percentage error and median error separately; mark unknown coplanarity and poor corner visibility as missing. An upright marker on a different wall cannot calibrate a shelf. Test a deliberately off-plane/occluded sample and report its null reason.
- [ ] Review checkpoint: compare observed error with ≤15% spine bar; **do not** claim floor/wall-area compliance. Unless independently measured from the sweep, `room.length_m`, `width_m`, `height_m`, `floor_area_m2`, `wall_area_m2`, and `shelved_wall_area_m2` stay null with review reason. Geometry failure does not stop voice, detection, pricing or the packet pipeline.

## Preflight report and handoff

- [ ] Run all deterministic tests: `python -m pytest tests/preflight -v`; log actual pass/fail output, not an assumed result.
- [ ] Self-review `docs/preflight-results.md`: for each of four probes write status `PASS`/`FAIL`/`BLOCKED`/`NOT MEASURED`, dataset/device details, measured numerator/denominator, observed outputs, blockers, and the smallest next change. Clearly distinguish test-fixture behavior from real-device/real-shelf/real-market behavior.
- [ ] Ask the user to review the measured results **before** selecting detector, pricing source, measurement strategy, or full-product implementation tasks. Draft the entire remaining implementation roadmap/plan only after these results are understood; a room geometry failure remains isolated and nullable.
