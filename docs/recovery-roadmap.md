# Recovery roadmap — 9 October 2026

Status: researched delivery proposal, not completed implementation or approval to change providers/budgets. The user requested updated documentation and a showcase-first consumer direction. [Readiness](readiness.md) owns actual status, [product direction](product-direction.md) owns intent, and [research](research/README.md) owns sources/alternatives. The original assignment contract remains in `build-spec.md` and the PDF.

## Recommended architecture and alternatives

Retain the browser/WebRTC, FastAPI, filesystem, existing reading/source-validation and packet layers. Separate a fast current-view/guidance path from a durable coverage/inventory path. Both use saved evidence, but current conversation does not dictate which shelf sections survive processing.

| Approach | Assessment |
|---|---|
| Coverage-aware shelf rows + locally benchmarked instances + original-crop reading | Recommended: addresses the measured failure, reuses the offline atlas and supports inspectable evidence |
| More VLM calls / stronger scene prompt with existing tracking | Retain only as a comparison/fallback proposal generator; cannot fix omitted coverage or establish physical identity by itself |
| Native depth capture + broader consumer workflow | Plausible later room-measurement branch; outside current browser-only scope and not a spine/pricing solution |

No checkpoint is selected for production. Compare official Grounding DINO tiny and SAM 2.1 tiny as fixed offline candidates; CPU/MPS speed, installation and dense-spine accuracy remain NOT MEASURED. Stop if the evidence gate fails rather than repeatedly tuning on the known 14-book row.

```text
Saved original frames + timestamps
  ├── cheap current quality/coverage cues ──> debounced voice guidance
  └── bounded keyframe manifest by observed shelf row
          └── validated atlas/row views ──> distinct spine instances
                  ├── best original crops ──> reading ──> catalogue candidates
                  ├── reference-plane measurement, when supported
                  └── accepted work ──> real listing ──> bound offer
Non-book observations ──> supported product/comparable listings
Independent same-sweep room evidence ──> supported geometry or unknown
All validated records ──> review + deterministic totals + JSON/HTML
```

This diagram is a target design. The current app lacks the coverage manifest, guidance scheduler, runtime detector/atlas integration, catalogue adapter, measurements and item-pricing stages.

## Work packages and gates

Execute small vertical slices; share the existing regression trail instead of rerunning unchanged full suites after each document/probe. Interfaces below are proposed. Implementation should start with focused failing behavior tests, then use the existing full gate once at a release checkpoint.

### R0 — Freeze acceptance evidence and observability

**Dependencies:** none. **Likely files:** new offline evaluator/fixtures under `tests/` or a dedicated evaluation folder; minimal stage metadata in `models.py` and browser event handling only when implementation starts.

Freeze labels for the saved five-book layouts, row14 and 59-book overlap controls; independently label additional rows, identical copies, stacks, unreadable and disconnected views. Existing 59-book pair labels are analyst-provisional, not final owner-approved truth. Hold out new scenes before tuning. Preserve original runs; revised inference creates a separate experiment artifact with parent run/model/parser versions.

Track capture → saved → selected/deferred/rejected → analyzed → physical instances → readable crops → accepted works → eligible valuations → URLs → accepted offers. Record stage wall time and usage by unique request/response ID; unknown voice usage remains unknown. Save benchmark definitions and sample-selection rules before looking at held-out results.

**Exit:** deterministic saved-evidence replay flags current coverage/association failures; every denominator, skipped input and failure is visible. Public-sample export is separate and privacy-reviewed. No paid replay is needed to establish this local loop.

### R1 — Fix demonstrated workflow defects and active guidance

**Dependencies:** R0 instrumentation. **Likely files:** `backend/web/lifecycle.js`, new focused `guidance.js`, `backend/realtime_contract.json`, safe capture configuration/state and focused lifecycle tests.

Fix peer failed/closed status without losing camera evidence or normal server postwork. Add a scheduler that reacts to expiring capture events, gives speech priority and coalesces warnings. Start with inexpensive quality cues on saved frames and calibrate them against actual resolutions. Inventory observation latency must not prevent blur/recovery guidance. Report missed regions only for an observed scan/coverage plan.

**Exit:** silent sweep + new actionable cue speaks once; repeated/stale cues do not flood speech; user interruption takes precedence; failed peer shows voice loss; one validated correction persists; Stop/End/reload remain idempotent. Device latency and semantic behavior require a bounded actual iPhone trial after local tests; they are not passed by mocked response events.

### R2 — Preserve usable shelf coverage at measured resolution

**Dependencies:** R0; device comparison independent of detector selection. **Likely files:** `capture.js`, capture configuration, new `backend/coverage.py`, persisted manifest and processing selection tests.

Finish the higher-resolution camera+voice preflight and collect negotiated settings, preview continuity, encoded bytes, attempted/skipped/successful uploads and p95 latency. Choose the evidence resolution from actual results, retaining a degraded fallback. Compact voice images remain separate from originals.

Replace newest-pending-frame overwrite as the authoritative inventory selector with `select_keyframes(saved_frames, quality, observed_rows, budget) -> manifest`. Each selected view has a reason and coverage interval; rejected/deferred views and gaps persist. Bound memory and provider work. A slow provider may delay a row but cannot erase it. At end, process uncovered selected evidence even if an earlier vision call succeeded. Retain one shared postwork deadline and stage reservations; do not raise existing paid budgets silently.

**Exit:** synthetic five-frame/multi-row scenarios retain every novel region or an explicit gap; no final-frame-only recovery; real saved replay reports coverage and stage-time limits. A new iPhone trial establishes higher-resolution load, not screenshots of `ideal` constraints.

### R3 — Select a physical-instance method and integrate row inventory

**Dependencies:** R0 labels, R2 coverage; A1 offline atlas remains reusable. **Likely files:** isolated detector adapter/probe, `shelf_atlas.py`, `tracking_contract.json`, `models.py`, `sweeps.py`, `processing.py`, packet evidence validation.

Resolve official pinned weights/licensing and an isolated experiment environment. Measure CPU first, MPS only after availability/output checks. Compare detectors/masks on the same original row inputs, including split/merged spines and non-book lettering. Published FPS on another GPU is not this machine's budget.

Count separated physical instances in validated row coordinates; reconcile overlapping row sections and revisits, including identical-title copies. Read and measure from original frame polygons/crops; atlas pixels organize coverage only. Replace global single-representative-scene selection with per-row evidence. Disconnected/uncertain observations remain inspectable and reviewable; they are neither automatically distinct nor discarded from future resolution.

**Exit:** count error alone is insufficient—localization precision/recall and split/merge failures must also pass predeclared gates. New multi-row sweep achieves ≤5% physical-count error with held-out labels, no false identical-copy merges and visible missing coverage. Otherwise count remains unknown, and an assisted overlay review is a separately labelled portfolio fallback. It does not satisfy the automatic count bar.

### R4 — Improve readable identity evidence and corroboration

**Dependencies:** accepted physical instances/best crops from R3; catalogue feasibility can run earlier on fixed text fixtures. **Likely files:** `reading.py`, `sweeps.py`, a small catalogue adapter, reading contract and identity evidence tests.

Compare existing VLM crop reading with a local OCR baseline on identical crops; use a bounded fallback for hard cases. Preserve literal text, orientation, crop resolution and unreads. Earlier scene text triggers review rather than overriding readable crop evidence; resolve disputes with another clear view or sourced catalogue record. Separate physical instance, work identity, candidate edition and verified owned edition.

Cache bounded metadata lookups and respect documented limits. Open Library is suitable only within its usage guidelines; Google Books can corroborate metadata but its eBook sale data does not price a physical copy. Do not assume either provides unrestricted commercial infrastructure.

**Exit:** ≥70% of hand-legible titles correct, ≤3% confidently wrong under the declared denominator; unreadable spines remain blank; duplicate works preserve multiple copies; wrong edition/language/series candidates abstain. Field accuracy remains unmeasured until all hand-legible truth is scored.

### R5 — Prove independent valuation and add non-book ranges

**Dependencies:** known-identity source probes independent of R3/R4; joining physical sweep instances requires R4. **Likely files:** source registry/adapters, `price_sources.py`, `pricing.py`, item valuation models/commit rules and `packet.py`.

Start with one demonstrated UAE new source, one UAE used source and one US source; the research lists candidates, not accepted integrations. Discovery finds real product URLs; source adapters bind actual selected variant/condition/stock/market evidence; shared code validates identity, equivalent replacement, currency/FX and appraisal. Persist precise rejection reasons. Retailer dimensions describe the listing, not the owned object.

Add comparable non-book ranges from individually retrieved relevant products; each endpoint has source/date/basis. Preserve art-print assertions and appraisal exclusions. Extend packet validators to accept supported item evidence rather than bypass their current refusals.

**Exit:** fixed five-known-book funnel demonstrates discovery separately from seeded extraction; then same ten books in AE/AED and US/USD through one interface; final 15-price sample meets ≤25% comparable error with coverage/missing rates reported. Zero quotes cannot pass by latency. No fabricated used discount or guessed SKU. Review source access/terms before operating a public consumer service; no scraping bypass.

### R6 — Add calibrated measurement, keeping room feasibility independent

**Dependencies:** actual same-plane A4/tape inputs; physical instance/corner evidence for runtime. **Likely files:** reusable calibration module, measurement records/commit rules, `processing.py`, `packet.py`.

First verify the current manual-corner arithmetic on ≥5 real tape pairs, then the required 20. Implement marker/spine-corner evidence only after that gate. Compare plain A4 with a verified-size ArUco reference if corner detection needs it; ChArUco may help development calibration but adds user friction. Reject off-plane, clipped and untrusted corners.

Room depth/perimeter/height require their own reconstruction/reference evidence from the same sweep. A shelf homography cannot establish them. Do not make detector/pricing progress wait on room feasibility. If browser geometry cannot meet the gate, document it; optional future native RoomPlan or owner-supplied consumer dimensions are different modes, not assignment measurements.

**Exit:** height/thickness within the declared ≤15% error on 20 pairs, plus acceptance coverage and off-plane negatives; shelf run sums only distinct measured covered segments. Separate room gate: floor ≤10%, walls ≤15% against tape truth with shape handling. Until passed, room numbers stay unknown and assignment geometry remains unmet.

### R7 — Showcase review experience and evidence package

**Dependencies:** a meaningful measured vertical slice from R1–R5; R6 status explicit. **Likely files:** existing browser views/report renderer and a read-only sample artifact path; no framework migration required.

Hide diagnostic Analyze/Read/Price controls from the normal primary flow while keeping them available in a development view. Present capture, processing and review states; each row opens the best original crop and accepted source evidence. Distinguish machine proposal, user assertion and verified match. Export a new revision after review; preserve original inference. Add a labelled privacy-reviewed recorded replay for visitors without model calls, microphone prompts or simulated live accuracy.

**Exit:** a stranger can understand and run the product; an unedited real demo shows guidance, inventory, a correction, normal end and evidence review. Publish measured before/after results, cost/latency and the three worst failures. The original assignment additionally requires all official bars, ≥60/2/8 environment, matched packet/frames and ≤6-minute unedited demo. An honest narrower portfolio release can ship with clearly unmet assignment bars, but must not advertise assignment completion.

## Budget, progress and consumer follow-on

Start with R0/R1 local defects, the R2 device gate and independent R5 known-book source probes. R3 is a bounded model comparison, not an unlimited model hunt. R6 field inputs can be collected concurrently; no algorithm can substitute for them. Re-estimate time after each experiment reports runtime and coverage. No calendar promises or per-sweep price claims are supported yet.

Display separate coverage for observed physical inventory, accepted identities, new/used prices and measurement; `processing_complete` records a finished attempt only. Cache expensive results by evidence/config revision and never automatically retry paid jobs on restart. Model/dependency/provider changes require explicit provenance and the existing project execution gates.

Once the showcase is reliable, prioritize consumer review/editing, receipts/source links, save/reopen, deletion/export and accessible recovery. Assisted recapture/user dimensions retain provenance and separate evaluation. Add account isolation, public hosting and spend limits only for an actual external launch. No hosting or code changes are authorized by this documentation task.

## Ready for implementation definition

A work package is ready when its public seam, concrete failing behavior, source/dependency suitability, benchmark inputs and stop condition are agreed; code is complete only when behavior and relevant regressions pass. A feature is portfolio-ready only after its real-device/source/ground-truth evidence is recorded. Treat these as three different states.
