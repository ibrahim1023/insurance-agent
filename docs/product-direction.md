# Product direction — 9 October 2026

**User-confirmed direction:** both an employer-facing technical showcase and a consumer home-inventory product, with the showcase delivered first. This document records that intent. Technical choices in the research and recovery roadmap are recommendations awaiting implementation/measurement, not claims of completed features.

## Product promise

A guided library scan creates a reviewable inventory: what was observed, which physical books/items can be distinguished, which identities and values have evidence, and what still needs the owner's attention. A user can inspect the original crop and a price source, correct/exclude an item, and export a report whose totals can be explained.

The portfolio story is a measured improvement from failed moving-camera tracking to a reliable evidence pipeline. Show representative failures, the corrective architecture, before/after metrics, cost and latency. Keep the insurance assignment as a demanding evaluation track; do not market the app as an insurer's approval or a guaranteed valuation.

## Two tracks

| | Technical showcase first | Consumer release later |
|---|---|---|
| Initial scope | One library room, guided continuous sweep, evidence review and JSON/HTML | Reliable library inventory before other room types |
| Capture | Original assignment's one continuous pass; targeted re-aim within it | May additionally offer clearly labelled assisted recapture/import |
| Identity corrections | Preserve observed text, proposed match and user assertion separately | Add explicit reviewed identity editing and revision history |
| Scale | Evidence-supported marker/geometry; otherwise unknown | May accept owner-supplied dimensions labelled as such |
| Prices | Verified listing/condition/market/date; unknown sides left empty | Same integrity plus manual sourced evidence entry if needed |
| Demonstration | Unedited real scan + paired results; labelled read-only replay for visitors | Saved projects, review workflow, controlled sharing and deletion |
| Completeness | Distinguish attempted processing, observed-scope coverage, identity, price and measurement coverage | Same distinction; actionable review can still provide value |

Assisted recapture, manual dimensions or user-confirmed identities cannot be counted as automatic assignment success. The assignment still needs ≥60 books, ≥2 shelving units, ≥8 non-book items, measured pass bars and matching evidence/demo. Its 48-hour constraint is historical; portfolio development may use a separate iteration budget without rewriting the original brief.

## First portfolio experience

1. A visitor understands the promise and measured limitations from the README/case study. A privacy-reviewed sample replay is explicitly labelled recorded, with no live model or microphone implied.
2. A real user sees camera/voice state, receives short capture instructions and knows which observed shelf row is being captured.
3. Provisional inventory appears with evidence thumbnails. Physical instances, title guesses and accepted identities have distinct states.
4. Ending capture starts bounded remaining work with visible stage and coverage. Leaving voice does not lose the scan.
5. The report shows supported totals, unknowns and a small actionable review queue. Every identity/value/measurement links to its evidence. A revision after review is labelled and does not overwrite the original inference artifact.

Steps 2–5 are target behavior; current limitations are in [readiness](readiness.md). The existing app is not yet this release.

## What earns the showcase release

- Real capture/guidance works on the intended iPhone, including interruption and a persisted correction.
- Counting is evaluated on distinct rows and identical copies, with declared coverage and no hidden title-based merging.
- All readable-spine truth participates in evaluation; model self-confidence is not an accuracy metric.
- Real replacement/used retrieval is demonstrated independently; missing local used offers remain missing or use labelled sourced foreign fallback.
- At least one calibration result has tape ground truth; unsupported room geometry is visibly labelled. Claiming assignment completion additionally requires every official geometry/sample gate.
- Another person can run it from the README, and the public sample contains only deliberately reviewed evidence.
- A concise case study, unedited demo, benchmark summary and failure analysis explain the code and quantify an actual improvement. A passing test count alone is insufficient.

## Consumer follow-on and exclusions

After the showcase, prioritize review/editing, persistence usability, accessible status/error recovery, source refresh and evidence export/delete. Add public hosting, account isolation and spend controls only when launching beyond the local demo. Hosting/authentication is not a prerequisite to fixing inventory and is not requested in this documentation task.

Defer payments, insurer integrations, generalized multi-room scanning, generated floor plans, native iOS, model training and agent-framework migrations until a measured need warrants them. Native depth capture is an optional later measurement direction, not a silent dependency of the browser showcase.

Next technical work is ordered in [the recovery roadmap](recovery-roadmap.md); researched alternatives and open gates live in [research](research/README.md).
