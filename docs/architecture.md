# Architecture — Library Claim Agent

Current checkpoint and proposed recovery, 9 October 2026. [Readiness](readiness.md)
owns status; [roadmap](recovery-roadmap.md) owns proposed changes. The original
assignment still requires one continuous sweep and an evidence-backed packet.

## Implemented

```text
Plain browser JavaScript: camera + WebRTC audio + status/inventory
  ├── 1-FPS timestamped JPEGs ──> FastAPI/SweepStore ──> saved originals
  │                                   └── newest pending view ──> scene proposals
  │                                            └── provisional tracking/crops
  ├── user-turn snapshot + app state ──> OpenAI Realtime conversation/tools
  └── sourced corrections ──> Python validation ──> persistent assertions
Normal end ──> bounded worker: drain vision / optional final frame
              ──> repeated-scene reconciliation ──> crop reading
              ──> eligible book discovery/extraction/FX
              ──> validated partial JSON ──> HTML
```

Python owns evidence links, admissions, revisions, appraisal and Decimal totals.
`runs/<id>/` holds originals, crops, stages, packet and report. The live model
proposes speech/intents; scene/crop models propose observations. There is no
hosted database, queue framework or React runtime.

**Missing:** complete coverage planning, proactive quality/guidance scheduling,
reliable physical instances, catalogue lookup, metric measurement and non-book
prices. `shelf_atlas.py` is an offline registration interface, not a runtime count.
The current packet is always partial. Passing regressions do not establish field
accuracy; the 59-book trial identified five tracks and accepted no prices.

## Proposed recovery

```text
Durable originals ──> quality + bounded coverage manifest by shelf row
  ├── current capture cues ──> debounced, interruptible voice instructions
  └── validated row atlas/views ──> separate spine instances
         ├── best ORIGINAL crops ──> OCR/VLM + catalogue corroboration
         ├── supported reference plane ──> calibrated spine metrics
         └── accepted work ──> bound real new/used listings + dated FX
Non-books ──> supported exact products or sourced comparable ranges
Independent same-sweep room geometry ──> metrics when proven; otherwise null
Validated records ──> review/revisions ──> deterministic JSON/HTML
```

A per-row atlas supplies coordinates/coverage; original crops supply text and
measurement evidence. Physical copies are separate from works/editions, so
identical-title books remain distinct. Grounding DINO tiny and SAM 2.1 tiny are
research candidates awaiting an isolated benchmark, not selected dependencies.
Live conversation remains current-view-oriented without deleting older inventory
coverage. Keep existing call budgets and shared deadlines until measured changes
justify an explicit configuration decision.

**Metric scale:** same-plane A4 homography is an unmeasured field baseline;
marker detection/coplanarity and tape validation are not implemented. A shelf
marker cannot establish room depth, perimeter or ceiling height. Native RoomPlan
is a later consumer option outside the browser-only scope. Unknown metrics stay
null; user-supplied consumer dimensions remain separately labelled.

**Values:** catalogue metadata supports work identity, not owned edition or price.
Demonstrated retailer-specific adapters must bind product/variant, condition,
stock, market, amount/currency and date. Generic comparable ranges carry sources;
original art/rare books stay appraisal. Commercial catalogue/source suitability
needs a separate release decision. Details and primary sources are in
[research](research/README.md).
