# Implementation review — 9 October 2026

> Follow-up: the user selected a technical showcase first and consumer product
> later. Documentation and primary-source research are now indexed in
> [product direction](product-direction.md), [readiness](readiness.md),
> [recovery roadmap](recovery-roadmap.md) and [research](research/README.md).
> This review's reproduced failures and historical evidence are unchanged;
> new recommendations are not implemented fixes.

## Verdict and scope

This is a substantial, carefully validated development checkpoint, but it does not yet implement the assignment's central promise: count, identify, measure and value a library from one continuous moving-camera sweep. The main failure is the inventory/evidence pipeline, compounded by passive capture guidance and missing measurement/item-valuation stages. A provider or framework swap alone will not repair this.

Reviewed the original ten-page assignment, build specification, approved design, preflight/full-build plans, architecture/README, preflight results, phase briefs/reports and panorama ledger; inspected current backend/browser code and saved run metadata. The review uses current files and preserved evidence rather than a Git diff: this workspace has no Git repository. Historical reports are historical snapshots, not current feature inventories.

Fresh verification:

- `.venv/bin/python -m pytest tests/backend tests/preflight -q`: **508 passed**, one Starlette/httpx deprecation warning, 48.23 seconds.
- `node --test tests/backend/*.mjs tests/preflight/test_voice.mjs`: **120 passed**, zero failures; output saved to `/tmp/library-review-node.log`.
- Saved-state/packet inspection was read-only. No real provider requests, money operations, server restart, product-code edits or original-run rewrites occurred.

Additional reproductions (local synthetic inputs, real public seams): `.venv/bin/python /tmp/library-review-runtime-probes.py` and `node /tmp/library-review-peer-probe.mjs`. Their scripts and outputs are retained in `/tmp/library-review-runtime-probes.log` and `/tmp/library-review-peer-probe.log`; both were rerun by the primary reviewer. They establish the frame-coverage, scene-archival and peer-status findings below without external calls.

These tests establish local behavior, not real-device usability or assignment accuracy. No new physical-room capture or ground-truth collection occurred in this review.

## What already exists and should survive

| Area | Implemented behavior | Practical limit |
|---|---|---|
| Capture | Browser camera/microphone, WebRTC voice, sampled timestamped JPEGs | Product still requests ideal 640×480; current real runs are 480×640 portrait |
| Persistence | Local run directories, atomic state, saved frames/crops/stages, evidence retrieval | Valuable basis for reproducible offline diagnostics |
| Vision | Structured book/item proposals, bounded calls, appearance/geometry association | Proposals do not become a trustworthy full-room physical inventory |
| Reading | Rotated original-frame crops, persisted readings, conservative identity gates | Poor source resolution, unstable boxes/association, missing catalogue lookup |
| Corrections | Validated target and actual user-turn provenance; assertions retained | Same-frame target availability can be poor; current phone semantics unverified |
| Pricing | URL discovery, deterministic supported-source extraction, independent new/used, FX/appraisal/Decimal checks | Automated live coverage fails; many runs never admit a book to pricing |
| Postwork | One bounded worker, shared deadline, failure/partial publication | Processes existing tracks; does not recover missed shelf coverage |
| Output | Validated JSON and HTML, explicit unknowns/review queue, sourced totals | Always partial; runtime metric measurement and non-book pricing absent |
| Atlas | Offline shelf registration with source transforms and warp/parallax rejection | No selected spine-instance detector and no production integration |

Keep FastAPI, plain browser JavaScript, filesystem evidence, conservative admission, source validation, correction provenance and deterministic arithmetic. A React migration, hosted database, generic agent framework or extra insurance workflow would not address the observed failures.

## Real evidence, separate from tests

The owner-counted 59-book run `34ccbe8ed7fe4c488b6d01697a549aa4` saved 117 frames and ended normally after 118.123 seconds. It has eight vision stages, **71 provisional book tracks**, 58 potential-duplicate flags, only **five identified tracks**, **zero accepted prices**, and no room measurements. Its packet correctly withholds `physical_book_count`. If someone interpreted 71 tracks as physical books, the result would be 20.34% high; that is not a published physical-count estimate. Recorded post-sweep work was 48.489 seconds; the preflight log reports packet publication around that interval. This demonstrates timely partial output, not complete assignment processing.

The independent five-book tabletop run retained 23 provisional tracks, 19 duplicate flags, four identified tracks and zero accepted prices. The owner heard a correct five-book answer for one view, but conversation-level recognition and persisted sweep inventory are separate outcomes.

Single-image detector evidence is also inadequate: the frozen 14-book row produced 15 VLM proposals with only seven matching truth boxes at IoU≥0.5; the independent owner-counted 24-book image produced 22 proposals. Both exceed the 5% count-error bar if proposal counts are interpreted as books. The frozen panoramic matching probe accepted **0/16** analyst-labelled same-spine pairs. These results justify changing representation, not tuning until a known total appears.

Source extraction previously recovered 3/5 new quotes from seeded UAE listing URLs, with 0/5 used quotes. The integrated discovery probe found **0/5 eligible product URLs** and accepted no prices. Seeded extraction success does not demonstrate automatic identity-to-price coverage. All inspected saved sweep states have zero priced books.

See `docs/preflight-results.md` for the preserved field results and qualifications. The 59-book room is below the assignment's minimum 60; full titles, 20 measured spines, 15 checked prices, item ground truth and tape room geometry are still missing.

## Spec review: why the core flow fails

### 1. P1 — Captured coverage is not processed

`backend/vision.py:323–330` replaces the pending frame with the latest frame while a slow model request runs. This is acceptable for a current-view conversation, but it skips intermediate shelf sections needed for an inventory. Saving those frames does not help unless a later stage selects and processes them.

`backend/processing.py:199–211` checks whether **any** successful vision stage exists. With none, it analyzes only the final saved frame; with one, it schedules no recovery analysis. The README's default `VISION_ENABLED=false` therefore cannot inventory the earlier sweep. Enabling automatic vision still leaves the latest-frame overwrite architecture in place.

Local synthetic reproduction: five saved frames, no successful prior stage → only index `[4]` analyzed; add a successful prior stage → `[]` additional frames analyzed. This reaches the real postwork seam with fake providers and requires no paid call.

**Change:** maintain a bounded coverage/keyframe plan per shelf row. Choose sharp, overlapping views from saved originals independently of provider latency. Postwork should process uncovered useful views under the existing deadline and budget, recording every unprocessed region. Live conversation can remain newest-view-oriented.

### 2. P1 — One representative scene replaces the broader inventory

`backend/scene_reconciliation.py:97–120` handles repeated views using a modal proposal count and representative frame. `backend/processing.py:224` applies reconciliation only when exactly one supported scene exists. `backend/sweeps.py:760–777` then archives every book outside the selected set into `unresolved_books`.

Local synthetic reproduction: shelf A appears twice and distinct shelf B once, with two books per view. The planner finds one supported scene and one unresolved frame; six proposals become two active books and four archived observations, including shelf B. Their evidence survives, but archived books leave the current reading/pricing pipeline. If two supported scenes exist, this reconciliation does nothing. Neither case assembles a room inventory.

The abstention is honest and preferable to invented continuity. The defect is using a fixed-view subsection strategy as the moving-camera inventory architecture.

**Change:** use the already approved shelf-atlas direction. Reconcile within supported shelf-row coverage, preserve distinct rows and unresolved observations, and identify physical instances separately from frame detections. Identical-title copies remain separate. Publish a physical count only when the required coverage and association evidence supports it. Do not sum view counts or weaken failed matching thresholds.

### 3. P1 — Quiet users get no active capture guidance

`backend/web/lifecycle.js:366–403` sends APP-STATE context, including model capture issues, without requesting a response. `backend/realtime_contract.json:12` disables automatic VAD responses; visual context is sent on committed spoken/typed turns (`:61`). After the greeting, a silent sweep has no capture-event path that requests spoken recovery guidance.

Capture issues also largely arrive from slow scene analysis. The inexpensive blur/glare/motion/coverage loop described in build-spec section 9 is not implemented as an active guidance loop.

**Change:** a small capture-guidance scheduler should consume current quality/coverage events and request brief, debounced instructions. Give user speech priority, suppress repeated warnings, expire stale events, and never announce an unseen missed shelf. Test a silent sweep with a new blur event, persistent blur without repetition, recovery, and interruption. Increasing image traffic on every poll is not the answer.

### 4. P1 — Too little readable evidence reaches either model

Product constraints at `backend/web/lifecycle.js:1079` request ideal 640×480. The saved 59-book run is 480×640 portrait. A later camera-only preflight saved 49 frames at 720×1280, but its browser-side delivery/latency log and voice concurrency are still unmeasured; this higher-resolution setting has not landed in the product.

`backend/voice_view.py:53–63` repeatedly downsizes to a 24,576-byte JPEG cap. The 59-book replay yielded 270×360 or 360×480 voice images with zero same-frame inventory targets. A `/voice_view` HTTP 200 proves endpoint success, not image delivery or model comprehension. Current snapshots commonly differ from the older frame that completed vision analysis, so same-frame target requirements can demand a slow explicit inspection before a correction.

**Change:** retain higher-resolution originals for reading and a compact separate voice view. Finish the negotiated-resolution/upload/voice preflight before product rollout. Select evidence by sharpness, spine pixels, angle and overlap; retain original coordinates. Log view delivery, actual dimensions, unavailable reasons, response timing and correction outcomes without logging private image/transcript contents. Do not loosen provenance merely to make correction tools appear successful.

### 5. P1 — Identification is too dependent on earlier unreliable observations

The reader chooses original-frame crops and conservatively rejects unknowns, which is correct. However `backend/sweeps.py:991–1040` can mark a legible high-confidence crop contradicted by earlier scene text. The narrow author/title concatenation correction handles one known case, but the scene observer still influences authoritative acceptance even though its text is explicitly provisional. Catalogue evidence has a model/store slot; runtime lookup is absent.

**Change:** count instances before resolving works. Read best original crops; preserve literal observed text. Let scene text generate a review signal, then resolve disagreement with another independent readable view or retrievable catalogue evidence. A catalogue match may support a work; it does not prove the owned edition/ISBN. Calibrate acceptance against hand-read spines and confidently-wrong cases, not model confidence alone.

### 6. P1 — Zero prices have both upstream and discovery causes

`backend/pricing.py:238–257` excludes potential duplicates, unidentified books and unresolved contradictions. In many saved sweeps, no books pass these gates, explaining empty pricing-stage lists. Separately, the five-known-book discovery probe still returned no eligible product URLs. Both must be repaired; changing a search prompt alone cannot fix upstream eligibility.

**Change:** show a stage funnel: observed instances → readable crops → accepted works → eligible valuations → discovered product URLs → accepted new/used quotes. Diagnose each loss independently. Prove discovery on the known-book sample through demonstrated source-specific search/navigation, then fetch and extract actual full listings. Keep stock, condition, edition-equivalence, currency, date and FX evidence. Do not infer used prices from new-price discounts. Complete the same-ten-book AE/US demonstration only after this works.

### 7. P1 — Measurement and non-book valuation are missing capabilities

`backend/packet.py:632–636` unconditionally creates unknown/null room geometry. `:304–305` rejects populated book measurements, and `:349–360` rejects populated item prices and measurements because provenance support is absent. Shelf run remains null. The postwork pipeline contains vision, reading and book pricing, with no metric-measurement or non-book pricing stage. Packet completeness requires non-null room fields (`:649–653`), so completeness is unreachable in the current build.

**Change:** implement validated measurement records and packet admission, not just populated output fields. The same-plane A4 preflight can support a rectified shelf/spine plane after real corner and tape validation. It cannot establish room depth/height by itself. Room geometry needs a separate, measured feasibility decision using observable scale and boundaries from the same sweep. If it fails, retain nulls and report the missed scoring bars. Non-book source retrieval and generic sourced ranges can proceed independently of room geometry. Art remains appraisal unless established as a print.

## Standards/runtime review

- **P2: voice status remains misleading after connection loss.** `backend/web/lifecycle.js:690–694` only logs peer changes. The local controller probe changes the peer to failed: the display remains “sweep running: camera + voice connected”, warnings remain empty, and only the log records failure. This reproduces the UI defect locally; no new phone-network trial occurred. Make voice state explicit, retain camera-only capture and offer reconnection.
- **Documentation mixes historical and current readiness.** README's test numbers and parts of the older preflight audit predate later implementations. The architecture note still sketches React/TypeScript while the approved/current implementation is plain JavaScript. Keep immutable experiment history, but add a concise current readiness table and link dated evidence. Do not replace historical failures with successful synthetic tests.
- **Possible divergent change:** `sweeps.py` (~1,324 lines) owns persistence, recovery, tracking commits, identity admission, price commits, corrections and publication; `lifecycle.js` (~1,424 lines) owns capture, voice, view preparation, tools, polling and packet presentation. Their complexity makes cross-stage invariants hard to inspect. Extract only the modules required for coverage selection, guidance scheduling and calibrated evidence; a general refactor would spend time without resolving pass bars.
- **Missing acceptance feedback loop:** 508 Python and 120 JavaScript tests can all pass while a real sweep cannot produce a physical count or accepted prices. Keep those regression tests, but add a frozen saved-sweep acceptance harness with independent labels and explicit missing-output rates. The public seams should test the actual moving-view path, not only ideal synthetic request shapes.

## Reference project: useful pattern, different problem

The [linked example](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team) separates live transport from its structured claim workflow. Its [current server source](https://raw.githubusercontent.com/Shubhamsaboo/awesome-llm-apps/main/voice_ai_agents/insurance_claim_live_agent_team/live_demo/server.py) forwards camera JPEGs as live visual input, freezes exact frames for evidence tools, and runs tools asynchronously while the conversation continues. Its [workflow source](https://raw.githubusercontent.com/Shubhamsaboo/awesome-llm-apps/main/voice_ai_agents/insurance_claim_live_agent_team/agent.py) combines semantic extraction with deterministic validation and packet building.

Keep those interaction/ownership principles: camera-aware conversation, visible authoritative state, background work, exact-frame evidence and interruption. Replace narrative claims with physical inventory, calibrated geometry and retrieved offers. Policy lookup, avatars and generated sketches do not solve the scored requirements. Gemini is not required by the assignment; retain the existing OpenAI transport unless a measured comparison justifies a change.

The reference was source-reviewed, not live-run in this review. Existing project records say its original live run is blocked on unavailable Google credentials. The assignment's requirement to run it before writing code was missed and cannot be retroactively satisfied by saying it was read. Record that ordering deviation honestly.

## Better implementation sequence

This is a recovery recommendation, not an amendment to the approved execution plan. Existing evidence/cost gates still apply.

1. **Create the real feedback loop.** Freeze independently labelled development inputs for distinct rows, overlaps, identical copies, flat/stacked books, blur/glare and unreadable text. Report every case. Use saved evidence offline; preserve original packets. Establish the full compliant-room ground truth before tuning for submission.
2. **Repair capture coverage and active guidance.** Quality/overlap events, debounced speech, clear voice-loss status, measured high-resolution originals and a bounded keyframe manifest. Verify captured-but-unprocessed views cannot silently disappear.
3. **Finish one row end to end.** Atlas registration → validated spine instances → best original crops → work identification → real source-backed valuation → packet. Test a new arrangement and different books, then multiple rows. The atlas module is useful groundwork; the detector and integration are still unresolved gates.
4. **Prove the detector rather than blocking on its name.** Compare permitted, runnable pretrained spine detection or prompted segmentation approaches against independent masks/boxes, including stacks. A generic segmentation method is a candidate, not a guaranteed spine counter. The [library inventory paper](https://arxiv.org/html/2407.19812) separates spine segmentation, OCR and catalogue matching, but its SAM masks include manual checking; its results do not establish automatic live counting here. Do not download an unvetted checkpoint or promote a failed baseline.
5. **Work pricing and measurement independently.** First prove known identity → real URL → accepted new/used quote. Implement non-book sourced ranges separately. Validate same-plane metric scale and run a distinct room-geometry feasibility experiment before promising area accuracy. Leave unsupported results unknown.
6. **Gate with the assignment, then package.** Compliant ≥60-book, ≥2-shelving-unit, ≥8-item room; all hand-legible titles, 20 tape-measured spines, 15 comparable checked prices, item list and tape room. Measure count ≤5%, titles ≥70% correct/≤3% confidently wrong, spine error ≤15%, prices ≤25%, item recall ≥80%, floor ≤10%, walls ≤15%, and packet <300 seconds. Also measure the combined flow for an unedited ≤6-minute demo. Unknown metrics remain NOT MEASURED/BLOCKED, not PASS.

The smallest useful next milestone is a **measured single-row sweep that counts once, reads reliably and retrieves actual prices**, alongside a working capture-guidance loop. It should exercise the real saved-frame→inventory→price→packet seams. More framework work or additional synthetic lifecycle review waves cannot substitute for that evidence.

## Readiness and remaining deliverables

Transport and timely partial packets have real supporting evidence. Complete-room count, legible-title accuracy, dimension accuracy, price accuracy, item recall and room-area accuracy do not have passing evidence. Runtime measurement and item pricing are unimplemented; automatic pricing coverage and spine counting fail existing probes.

Still required: a compliant final dataset/results sheet, deterministic accuracy evaluator, matched unedited demo/packet/evidence, final three-error failure log, measured total cost including voice, verified fresh setup under 15 minutes, and owner-authorized private repository/submission packaging. `postwork_status=completed` means the bounded attempt finished; it does not mean the claim inventory is complete or accurate.
