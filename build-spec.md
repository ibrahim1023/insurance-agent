# Library Contents Claim Agent
## Build Specification for Codex / Devin

> **Document role, updated 9 October 2026:** this retains the original assignment
> requirements and scoring contract. Current implemented status is in
> [docs/readiness.md](docs/readiness.md). The user now wants both an employer
> showcase and a consumer product, showcase first; that direction and researched
> recovery are documented in [product direction](docs/product-direction.md) and
> [recovery roadmap](docs/recovery-roadmap.md). Proposed assisted consumer modes
> do not replace the one-sweep assignment or turn unmeasured results into passes.

## 0. Mission

Build a live voice-and-vision insurance claim agent that inventories and values the contents of a home library from **one continuous camera sweep**.

The user walks through one enclosed room once while talking to the agent. During that sweep, the system must:

- actively guide the capture
- detect and count books from their spines
- identify books when visual evidence supports identification
- measure book spine dimensions
- detect and classify non-book contents
- estimate room dimensions and surface areas
- retrieve real local-market prices
- surface uncertainty rather than invent facts
- generate an auditable claim packet
- retain evidence connecting every important output back to the captured sweep and pricing source

This is not primarily a UI exercise or an LLM-demo exercise.

The three core evaluation principles are:

1. Get real measurements from messy real-world capture.
2. Do not invent facts when money is attached.
3. Report failures honestly.

The submitted system will ultimately be evaluated against manually collected ground truth and then tested on an unseen room/shelf during a live review.

---

# 1. Timebox

## Hard constraint

Treat this as a **48-hour take-home** with approximately **15–20 hours of actual engineering work**.

Do not over-engineer.

Priority order:

1. Working one-sweep flow
2. Measured accuracy
3. Evidence and traceability
4. Pricing integrity
5. Honest uncertainty
6. Reproducibility
7. Failure analysis
8. UI polish only where required for the demo

Do not spend meaningful time on:

- authentication
- production deployment
- policy databases
- elaborate visual design
- unnecessary infrastructure
- custom model training
- generic agent-framework abstractions
- speculative stretch goals before core pass bars work

The brief explicitly states that visual polish, authentication, deployment and policy databases are not scored.

---

# 2. Development Environment vs Final Evaluation Environment

## Development

The current development environment may contain only:

- approximately 20 books
- 2 shelving units

This is acceptable for development.

Use this smaller environment to build and iterate on:

- live capture
- voice
- capture-quality checks
- tracking
- identification
- measurement
- pricing
- packet generation

Do **not** hardcode anything about this room.

## Final evaluation capture

Before final submission, the system must be tested in a compliant real environment containing:

- at least 60 distinct books
- at least 2 shelving units
- at least 8 non-book items
- one enclosed room
- enough visible wall/floor geometry for room measurement

If the final space is not owned by the developer, obtain permission.

Do not capture:

- people
- private documents
- personal data

---

# 3. User Experience

## Start

The user opens the app on a phone or browser.

The app asks for:

- camera permission
- microphone permission

The agent greets the user and obtains:

- country
- currency

For the primary test locale use:

- country: UAE
- currency: AED

The agent explains the sweep in one or two sentences.

Example:

> I’ll guide you around the room once. Move slowly across every shelf, wall and floor area. If I can’t read or measure something reliably, I’ll ask you to slow down or move closer.

Do not create a long onboarding flow.

---

# 4. Sweep Requirements

The target sweep duration is:

**under 3 minutes**

The user should perform one continuous pass across:

- every shelving unit
- books
- furniture
- decor
- electronics
- floor
- relevant walls

The agent must be active during the sweep.

It must be capable of saying things such as:

- “Slow down on this shelf.”
- “Move a little closer.”
- “There is glare on the upper shelf.”
- “I haven't seen the lower-right shelf clearly yet.”
- “Is that artwork an original or a print?”

The user may correct the system verbally.

Examples:

- “That is a first edition.”
- “Those books aren't mine.”
- “Skip this shelf.”
- “That portrait is a print.”

Corrections must update system state rather than just being stored as chat text.

The assignment permits a targeted re-capture during the same continuous pass, but does not permit asking users to remove books, scan ISBNs, or photograph each object individually.

---

# 5. Core Architectural Principle

Use this rule throughout the implementation:

> Models may propose semantic facts. Deterministic code owns evidence, arithmetic, aggregation, thresholds and final totals.

Never allow an LLM to directly generate final monetary totals or authoritative measurements.

Separate the system into independently testable stages.

Recommended architecture:

```text
Camera + microphone
        |
        v
Live Capture Controller
        |
        +----> Voice / sweep agent
        |
        v
Frame Sampler
        |
        v
Capture Quality Analysis
        |
        +----> live recapture guidance
        |
        v
Scene Extraction
        |
        +----> Book candidates
        |
        +----> Non-book candidates
        |
        +----> Room geometry evidence
        |
        v
Temporal Tracking / Deduplication
        |
        +----> best evidence frames
        |
        +----> book identification
        |
        +----> object identification
        |
        +----> measurement
        |
        v
Pricing Retrieval
        |
        v
Validation / Confidence Rules
        |
        v
Review Queue
        |
        v
Claim Packet Builder
        |
        +----> claim_packet.json
        |
        +----> human-readable HTML/PDF
```

Do not implement this as one giant prompt.

Identification, measurement and pricing must remain separate stages with independently inspectable outputs.

---

# 6. Suggested Technology Choices

Optimize for implementation speed and debuggability.

## Frontend

Preferred:

- React / Next.js
- TypeScript
- browser camera access
- browser microphone access

The UI only needs to support:

- live camera
- voice interaction
- current sweep status
- live inventory
- capture warnings
- processing status
- final claim packet

Avoid elaborate styling.

## Backend

Preferred:

- Python
- FastAPI

Reasons:

- straightforward image processing
- OpenCV ecosystem
- clean separation of ML/vision stages
- simple structured models with Pydantic

## Local persistence

Use:

- filesystem
- JSON
- optionally SQLite

Do not add a hosted database unless strictly necessary.

Store:

```text
runs/
  <sweep_id>/
      metadata.json
      frames/
      crops/
      stage_outputs/
      claim_packet.json
      report.html
```

Every referenced frame must remain available after the run.

---

# 7. Data Model

Create internal structured models early.

Suggested objects:

```python
Sweep
Frame
CaptureIssue
Shelf
BookTrack
BookIdentification
SpineMeasurement
NonBookTrack
ObjectIdentification
RoomMeasurement
PriceEvidence
ReviewItem
ClaimPacket
```

Do not pass unstructured model prose between pipeline stages.

Use strict JSON-schema / Pydantic outputs from model calls where possible.

---

# 8. Frame Capture Strategy

Do **not** process every camera frame with an expensive multimodal model.

A 3-minute sweep contains thousands of frames.

Instead:

```text
camera stream
    |
sample frames
    |
quality filtering
    |
scene-change filtering
    |
tracking / deduplication
    |
best-frame selection
    |
VLM only where needed
```

Initial recommendation:

- sample approximately 1–2 FPS for semantic processing
- maintain higher-frequency lightweight local analysis where needed for blur/motion
- retain timestamps
- save useful evidence frames

Every saved frame must receive a stable identifier such as:

```text
frame_000042
```

or a timestamp reference.

---

# 9. Live Capture Quality

Implement inexpensive deterministic checks before involving an LLM.

## Blur

Possible method:

- variance of Laplacian

Produce:

```json
{
  "type": "blur",
  "severity": 0.82,
  "frame_ref": "frame_000042"
}
```

## Exposure / glare

Use:

- brightness histogram
- clipped highlights
- large high-intensity regions

## Motion

Use:

- optical flow
- frame difference
- camera motion heuristics

## Coverage

Track which shelf regions / scene regions have been adequately observed.

The live agent should react to these signals.

Example:

```json
{
  "event": "capture_warning",
  "reason": "blur",
  "region": "upper shelf"
}
```

Voice layer converts that into:

> The upper shelf is blurred. Hold there for another second.

---

# 10. Book Detection and Counting

The system must count books from visible spines, including:

- upright books
- books lying flat
- stacked books

Do not equate OCR output with book count.

Treat book counting as:

```text
candidate detection
+
temporal tracking
+
deduplication
=
persistent book inventory
```

---

# 11. Temporal Tracking

The camera will see the same book across several frames.

Do not create a new inventory record for every detection.

Each physical book should have a persistent ID:

```text
book_001
book_002
book_003
```

Potential association evidence:

- bounding-box location relative to shelf
- neighbouring books
- spine appearance
- OCR fragments
- visual embeddings
- temporal proximity

Keep the implementation practical.

Perfect general-purpose tracking is unnecessary.

The goal is to prevent obvious duplicate inventory rows.

---

# 12. Best Evidence Selection

For each book track, retain several candidate crops.

Score crops on:

- sharpness
- spine visibility
- OCR readability
- occlusion
- glare
- viewing angle
- crop resolution

Choose the best crop or small number of crops for identification.

Do not send every observation to an expensive VLM.

Save the selected evidence.

---

# 13. Book Identification

For each persistent book:

Attempt to extract:

- title
- author
- publisher where visible
- ISBN only if supported by evidence
- edition only when supported

Never infer an exact edition just because a likely book title is known.

Identification statuses:

```text
identified
unidentified
needs_appraisal
```

If the spine cannot be read reliably:

```text
status = unidentified
title = ""
author = ""
edition = ""
isbn = ""
```

Still keep:

- physical book record
- dimensions
- shelf
- position
- frame reference

---

# 14. Identification Confidence

False confident identifications are heavily penalized.

Target:

- ≥70% of human-legible spines correctly identified
- ≤3% confidently wrong

Therefore optimize for **precision before recall**.

Use conservative thresholds.

Possible confidence signals:

- readable title match
- readable author match
- external catalogue corroboration
- multiple frames agree
- OCR/VLM agreement
- edition-specific evidence

Example logic:

```python
if strong_title_evidence and supporting_metadata:
    status = "identified"
elif rare_or_special_evidence:
    status = "needs_appraisal"
else:
    status = "unidentified"
```

Do not artificially increase identification percentage by guessing.

---

# 15. Spine Measurement

Required per book:

- spine height in centimetres
- spine thickness in centimetres

Also calculate:

- total shelf run in metres

## Critical requirement

Metric scale must come from an explainable real reference.

Not acceptable:

> “The model estimated the size.”

Possible approaches allowed by the assignment:

- LiDAR
- known reference object
- known shelf dimensions
- standard physical reference

For this build, prefer the simplest reliable method that can be completed in the timebox.

### Recommended MVP

Use a known reference object visible in the scene during calibration.

Example:

- A4 sheet
- known calibration marker
- another object with fixed physical dimensions

Document exactly how pixels are converted to metric units.

Account for perspective as far as practical.

Attach a measurement confidence score.

If measurement quality is poor:

- lower confidence
- optionally request the user to adjust capture
- retain result in review queue if necessary

---

# 16. Book Pricing

Pricing is a separate pipeline stage.

Do not allow an LLM to recall or invent prices.

For each identified book produce:

## Replacement cost

Current cost of buying:

- the same edition where confidently identified
- otherwise an appropriate current equivalent

## Used market value

Current used-market price based on an explicitly stated condition assumption.

Required metadata:

```json
{
  "amount": 89.00,
  "currency": "AED",
  "source": "...",
  "url": "...",
  "retrieved_at": "...",
  "converted": false
}
```

Used:

```json
{
  "amount": 28.00,
  "currency": "AED",
  "source": "...",
  "url": "...",
  "retrieved_at": "...",
  "condition_assumed": "good"
}
```

---

# 17. Local Pricing

Primary locale:

```text
UAE
AED
```

Attempt to obtain local prices first.

Potential source classes:

- UAE book retailers
- general UAE retailers
- used-book marketplaces
- publisher/distributor listings where appropriate

Do not hardcode specific pricing results.

If no local result is available:

1. retrieve a valid price from another market
2. convert currency
3. mark:

```json
"converted": true
```

If no defensible price can be retrieved:

```text
leave price empty
exclude from monetary total
add review reason where appropriate
```

Every monetary amount must remain traceable to a retrievable source and retrieval date.

---

# 18. Second Locale Test

The same 10 books must also be priced for a second country.

Recommended secondary locale:

```text
United States
USD
```

Architecture should expose locale as configuration:

```python
price_book(book, locale="AE")
price_book(book, locale="US")
```

Do not implement separate hardcoded UAE and US pipelines.

---

# 19. Rare / Signed / Antiquarian Books

Do not auto-price books that appear:

- signed
- rare
- antiquarian
- unusually valuable
- collector-grade

Define and document a value threshold.

Suggested behaviour:

```text
status = needs_appraisal
```

Possible reason:

```text
"Possible signed/rare edition; automated valuation suppressed."
```

These records must not silently contribute speculative amounts to totals.

---

# 20. Non-Book Contents

Detect visible room contents including, where present:

- shelving
- furniture
- tables
- chairs
- lamps
- electronics
- rugs
- coffee machine
- art
- portraits
- framed prints
- decor
- speakers
- monitors
- other obvious contents

For each item record:

- category
- visible description
- material where visible
- approximate dimensions
- brand/model if legible
- frame reference
- confidence

Example:

```json
{
  "id": "item_003",
  "category": "lamp",
  "description": "black metal floor lamp",
  "brand_model": "",
  "frame_ref": "frame_000081",
  "dimensions_cm": {
    "w": 30,
    "h": 150,
    "d": 30
  },
  "confidence": 0.81
}
```

---

# 21. Non-Book Pricing

For recognizable products:

Retrieve local replacement cost.

If exact brand/model is not visible:

Return a sourced range.

Example:

```json
{
  "low": 150,
  "high": 250,
  "source": "...",
  "url": "...",
  "retrieved_at": "..."
}
```

Do not fabricate exact SKUs.

---

# 22. Art and Portraits

Art defaults to:

```text
needs_appraisal
```

unless the user explicitly establishes that it is a reproduction/print suitable for standard replacement pricing.

During the live sweep the agent should ask short questions when necessary.

Example:

> Is that framed artwork an original or a print?

Do not auto-value original artwork.

---

# 23. Room Measurement

Required outputs:

- room length in metres
- room width in metres
- ceiling height in metres
- floor area in m²
- floor area in ft²
- total wall area in m²
- total wall area in ft²
- wall area occupied by shelving
- handling of non-rectangular shape

All measurements must originate from the same sweep.

No separate measurement pass.

---

# 24. Room Geometry Strategy

Do not attempt research-grade scene reconstruction.

Use the simplest defensible approach available within the timebox.

Possible signals:

- known scale reference
- visible room boundaries
- planar wall/floor relationships
- shelf dimensions
- perspective geometry
- device depth APIs if readily available

Store:

```json
{
  "scale_method": "...",
  "confidence": 0.0
}
```

Document limitations.

Required pass bars:

- floor area within 10%
- wall area within 15%

against tape-measured ground truth.

---

# 25. Shelved Wall Area

Calculate separately:

```text
shelved_wall_area_m2
```

Use visible shelf footprint / measured shelf extents.

Do not subtract this from total wall area unless required for another presentation field.

Report both independently.

---

# 26. Live Inventory UI

During the sweep the user should see inventory populate incrementally.

Minimum interface:

```text
LIVE SWEEP

Books: 14 detected
Identified: 9
Unidentified: 5

Items:
- floor lamp
- desk
- rug

Capture quality:
✓ current frame usable

Current instruction:
Continue slowly to the right.
```

Do not wait until sweep completion to display inventory.

---

# 27. Voice Corrections

Support basic correction intents.

Examples:

```text
"That is a first edition."
"Skip that shelf."
"Those aren't my books."
"That painting is a print."
```

Translate voice into structured commands.

Example:

```json
{
  "type": "exclude_region",
  "target": "shelf_02"
}
```

Do not simply append correction text to conversation history.

---

# 28. End-of-Sweep Processing

When the user ends the sweep:

Run remaining asynchronous work:

```text
identification completion
        |
pricing retrieval
        |
measurement finalization
        |
validation
        |
review queue
        |
packet generation
```

The final packet must be available within:

**5 minutes**

Target substantially below 5 minutes if possible.

Record latency per stage.

---

# 29. Claim Packet Contract

Every sweep produces:

```text
claim_packet.json
```

and:

```text
report.html
```

PDF is optional if HTML is reliable.

The JSON must contain at least the required assignment contract:

```json
{
  "sweep": {
    "id": "",
    "captured_at": "",
    "device": "",
    "duration_s": 0,
    "country": "",
    "currency": ""
  },

  "room": {
    "length_m": 0,
    "width_m": 0,
    "height_m": 0,
    "floor_area_m2": 0,
    "wall_area_m2": 0,
    "shelved_wall_area_m2": 0,
    "scale_method": "",
    "confidence": 0
  },

  "books": [
    {
      "id": "",
      "shelf": "",
      "position": 0,
      "frame_ref": "",
      "status": "identified | unidentified | needs_appraisal",
      "title": "",
      "author": "",
      "edition": "",
      "isbn": "",
      "spine_height_cm": 0,
      "spine_thickness_cm": 0,
      "id_confidence": 0,

      "replacement_cost": {
        "amount": 0,
        "source": "",
        "url": "",
        "retrieved_at": "",
        "converted": false
      },

      "used_value": {
        "amount": 0,
        "source": "",
        "url": "",
        "retrieved_at": "",
        "condition_assumed": ""
      }
    }
  ],

  "items": [
    {
      "id": "",
      "category": "",
      "description": "",
      "brand_model": "",
      "frame_ref": "",

      "dimensions_cm": {
        "w": 0,
        "h": 0,
        "d": 0
      },

      "status": "priced | range | needs_appraisal",

      "replacement_cost": {
        "low": 0,
        "high": 0,
        "source": "",
        "url": "",
        "retrieved_at": ""
      },

      "confidence": 0
    }
  ],

  "totals": {
    "book_count": 0,
    "books_identified": 0,
    "books_unidentified": 0,
    "shelf_run_m": 0,
    "books_replacement_cost": 0,
    "books_used_value": 0,
    "items_replacement_cost_low": 0,
    "items_replacement_cost_high": 0,
    "excluded_from_totals": 0
  },

  "review_queue": [
    {
      "ref_id": "",
      "reason": ""
    }
  ]
}
```

Fields may be added.

Do not drop required fields.

---

# 30. Critical Claim Packet Rules

## Rule 1

Unknown data should remain empty.

Wrong:

```json
"title": "The Great Gatsby"
```

when the system merely suspects that title.

Correct:

```json
"title": ""
```

with:

```text
status = unidentified
```

## Rule 2

Every `frame_ref` must resolve to:

- a saved frame
- or an accessible saved timestamp

## Rule 3

All totals must be calculated in deterministic code.

Never ask the language model:

> What is the total value?

Instead:

```python
total = sum(valid_line_items)
```

---

# 31. Review Queue

Every uncertain material fact should be capable of entering:

```text
review_queue
```

Reasons may include:

```text
Unreadable spine
Possible title match below confidence threshold
Edition unclear
No reliable local price
Possible signed copy
Possible antiquarian item
Artwork requires appraisal
Poor measurement geometry
Brand/model unreadable
Object partially occluded
```

The queue should be meaningful to a human adjuster.

---

# 32. Evidence Traceability

A claim line should be traceable:

```text
Claim line
   |
   +----> frame_ref
   |
   +----> identification evidence
   |
   +----> measurement evidence
   |
   +----> pricing URL
   |
   +----> retrieval timestamp
```

An adjuster should be able to answer:

> Why does the system believe this item exists and why is it valued at this amount?

---

# 33. Deterministic Validation Layer

Before generating the final report, run validation rules.

Examples:

```python
assert totals.book_count == len(books)

assert totals.books_identified == len(
    [b for b in books if b.status == "identified"]
)

assert every_frame_ref_exists()

assert every_priced_item_has_source()

assert every_price_has_retrieved_at()

assert rare_items_have_no_automatic_price()

assert unidentified_books_do_not_have_guessed_titles()
```

Generate warnings rather than crashing where appropriate.

---

# 34. Ground Truth Dataset

Do not wait until the end.

The assignment requires ground truth before tuning.

Create:

```text
ground_truth/
```

with one structured CSV/JSON/spreadsheet containing:

### Books

- true book count
- hand-read title for every human-legible spine
- author where appropriate
- shelf / position

### Measurements

At least 20 books:

- measured spine height
- measured spine thickness

### Pricing

At least 15 books:

- manually verified replacement price
- same source type used for comparison
- manually verified used price where practical

### Non-books

- true inventory of visible non-book items
- category

### Room

Tape-measured:

- length
- width
- ceiling height
- floor area
- wall dimensions
- shelving dimensions

---

# 35. Evaluation Script

Build a deterministic evaluator.

Example:

```bash
python scripts/evaluate.py \
  --ground-truth ground_truth/final.json \
  --prediction runs/final/claim_packet.json
```

Output:

```text
BOOK COUNT
Truth: 63
Predicted: 61
Error: 3.17%
PASS

TITLE IDENTIFICATION
Human-legible: 54
Correct: 42
Accuracy: 77.8%
PASS

CONFIDENTLY WRONG
1 / 42
2.38%
PASS

SPINE DIMENSIONS
Mean error: 11.4%
PASS

BOOK PRICING
Mean error: 18.9%
PASS

NON-BOOK RECALL
9 / 10
90%
PASS

FLOOR AREA
Error: 7.2%
PASS

WALL AREA
Error: 12.8%
PASS

PACKET LATENCY
74 s
PASS
```

Do not manually calculate evaluation numbers in the README.

---

# 36. Required Pass Bars

The system should explicitly calculate and report these:

| Metric | Pass Bar |
|---|---:|
| Book count | within 5% |
| Title identification | ≥70% of human-legible spines |
| Confidently wrong IDs | ≤3% |
| Spine dimensions | within 15% on 20-book sample |
| Book prices | within 25% on 15-book sample |
| Non-book detection | ≥80% with correct category |
| Floor area | within 10% |
| Wall area | within 15% |
| Packet completion | under 5 minutes |

Treat these as engineering acceptance tests rather than documentation statistics.

---

# 37. Cost Tracking

Record cost per sweep.

Create stage-level metrics such as:

```json
{
  "capture_processing_usd": 0.01,
  "vision_identification_usd": 0.22,
  "voice_usd": 0.08,
  "pricing_retrieval_usd": 0.05,
  "total_usd": 0.36
}
```

If exact provider billing data is unavailable in real time, calculate from:

- token usage
- audio duration
- image calls
- provider published rates

Document calculation assumptions.

---

# 38. Latency Tracking

Record per-stage latency:

```text
capture
book detection
book identification
measurement
non-book identification
pricing
validation
report generation
```

Example:

```json
{
  "book_identification_ms": 18120,
  "pricing_ms": 9312,
  "measurement_ms": 4110,
  "report_generation_ms": 219,
  "post_sweep_total_ms": 32218
}
```

Show this in the failure/engineering report.

---

# 39. Failure Logging

Do not hide bad outcomes.

Create:

```text
docs/failure-log.md
```

For the final run identify the three worst failures.

Each entry must contain:

```text
Observed failure
Ground truth
System output
Root cause
Evidence
Fix attempted
Measured result after fix
What remains unresolved
```

Bad:

> Vision model struggled with some books.

Good:

> Eight books on shelf B were merged into five tracks because horizontal camera motion exceeded the association threshold. Reducing the frame gap from 1.0s to 0.5s improved book-count error from 11.7% to 4.8%, at the cost of 1.7× additional local processing.

At least one failure should have a measured attempted improvement.

---

# 40. Reference Application

Before major implementation:

1. Run the provided Insurance Claim Live Agent Team example.
2. Understand its interaction loop.
3. Document in README:

```text
Kept
Changed
Discarded
```

Example:

```text
Kept:
- continuous camera + voice interaction pattern
- live claim state

Changed:
- passive visual description -> active measurement/inventory
- post-hoc narrative -> structured evidence-backed records

Discarded:
- components irrelevant to contents valuation
```

Do not simply fork it and modify the prompt.

---

# 41. README Requirements

A stranger must be able to run the project in under 15 minutes.

README should contain:

```text
1. What this project does
2. Architecture diagram
3. Prerequisites
4. Environment variables
5. Install
6. Run frontend
7. Run backend
8. Start a sweep
9. Generate claim packet
10. Run evaluation
11. Models/providers used
12. Pricing sources
13. Measurement method
14. Reference app: kept / changed / discarded
15. Known limitations
16. Cost
17. Demo links
```

Include:

```text
.env.example
```

Commit no secrets.

---

# 42. Repository Structure

Recommended:

```text
library-claim-agent/
│
├── README.md
├── .env.example
│
├── frontend/
│   ├── components/
│   ├── app/
│   └── lib/
│
├── backend/
│   ├── api/
│   ├── capture/
│   ├── vision/
│   │   ├── quality.py
│   │   ├── books.py
│   │   ├── tracking.py
│   │   ├── objects.py
│   │   └── room.py
│   │
│   ├── identification/
│   ├── measurement/
│   ├── pricing/
│   ├── review/
│   ├── reports/
│   └── models/
│
├── scripts/
│   ├── evaluate.py
│   ├── generate_report.py
│   └── validate_packet.py
│
├── ground_truth/
│
├── runs/
│
├── docs/
│   ├── architecture.md
│   └── failure-log.md
│
└── tests/
```

Adjust structure if simplicity requires it.

Do not create abstractions merely to match this tree.

---

# 43. Tests

Prioritize useful tests.

Minimum:

## Unit tests

- totals computation
- review routing
- price validation
- evidence references
- locale handling
- unknown-field handling
- rare-item pricing suppression

## Integration tests

- book record -> pricing -> packet
- sweep state -> report
- ground truth evaluator

## Fixtures

Save a few representative:

- clear spine crops
- blurry spine crops
- glare examples
- stacked books
- unreadable books

Do not spend hours creating exhaustive test coverage.

---

# 44. Human-Readable Report

Generate from `claim_packet.json`.

Do not maintain separate business logic for JSON and HTML.

The report should include:

## Claim summary

- sweep metadata
- room dimensions
- floor/wall area
- book count
- identified/unidentified counts
- claim totals
- exclusions

## Books table

Columns:

```text
Book
Status
Dimensions
Replacement cost
Used value
Confidence
Evidence
```

## Non-books

Same principle.

## Review queue

Clearly visible.

## Evidence

Clickable/local references where practical.

## Pricing source

Visible source and retrieval date.

---

# 45. Submission Deliverables

All six are mandatory.

## 1. Private GitHub repository

Must run from README in <15 minutes.

## 2. Demo video

One take.

Maximum:

**6 minutes**

Must show:

- start
- live voice agent
- full sweep
- camera active
- live inventory updating
- active guidance
- sweep ending
- final packet

Do not:

- edit
- speed up
- stitch recordings

## 3. Claim packet

Include:

```text
claim_packet.json
readable report
referenced saved frames
```

It must correspond to the sweep shown in the demo.

## 4. Ground truth + results

Show:

- true count
- hand-read titles
- 20 measured spines
- 15 manual prices
- non-book list
- tape-measured room
- predicted results
- pass/fail for every pass bar

## 5. Architecture note

One page.

Must show:

- pipeline diagram
- model responsible for each step
- metric scale method
- pricing sources

## 6. Failure log

One page.

Must contain:

- three worst errors
- root causes
- at least one measured improvement
- cost per sweep
- latency per stage

---

# 46. Evaluation Priorities

Official scoring:

```text
Measured accuracy        30%
Pricing integrity        20%
Live agent / one sweep   20%
Engineering              15%
Failure analysis         15%
```

Therefore development priority should broadly follow:

```text
accuracy
   >
pricing correctness
   ≈
live recovery
   >
architecture elegance
   >
visual polish
```

---

# 47. Things the Implementation Must Never Do

Do not:

- invent book titles
- infer ISBNs without evidence
- produce prices from model memory
- fabricate URLs
- automatically price rare books
- automatically price signed books
- automatically price original artwork
- hardcode room dimensions
- hardcode test-room catalogue
- hardcode pricing
- ask users to scan barcodes
- ask users to enter ISBNs
- ask users to remove books from shelves
- ask users to photograph every item separately
- process only “easy” shelves for evaluation
- report cherry-picked accuracy
- compute financial totals through an LLM
- submit code that cannot be explained in review
- optimize specifically for the developer's final room

---

# 48. Unseen-Room Requirement

Design every feature knowing that during the review call the evaluators will point the system at:

- a shelf it has never seen
- a room it has never seen

They will compare results against:

- their own book count
- their own measurements

Therefore:

```text
NO room-specific constants
NO memorized book lists
NO test-scene coordinate hacks
NO manually inserted shelf dimensions
NO assumptions tied to the final demo room
```

Generalization matters more than making the demo dataset appear perfect.

A moderate drop in accuracy on their environment is expected.

System collapse is not.

---

# 49. Stretch Goals

Do not implement these until all core pass bars and deliverables work.

Optional:

- floor-plan sketch
- book-condition grading
- multiple rooms
- independent checker agent
- smart-glasses support

Stretch goals do not compensate for missing core requirements.

---

# 50. Recommended 20-Hour Execution Plan

## Phase 0: Read + Baseline
### 1 hour

- run reference app
- inspect assignment again
- establish repository
- define schemas
- create ground-truth template
- decide model/provider
- decide metric-scale strategy

Do not code before deciding the scale method.

## Phase 1: End-to-End Skeleton
### 2.5 hours

Build:

```text
camera
microphone
live agent
sweep start/end
frame persistence
live inventory placeholder
claim packet skeleton
```

Goal:

One complete dummy sweep can run end to end.

## Phase 2: Capture Quality
### 1.5 hours

Add:

- blur detection
- exposure/glare detection
- basic motion analysis
- voice warning events

Goal:

Agent can catch at least obvious poor capture while sweep is active.

## Phase 3: Book Pipeline
### 3.5 hours

Build:

- spine candidate extraction
- tracking/deduplication
- evidence crop selection
- identification
- confidence
- abstention

Use current 20-book shelves.

Measure immediately:

```text
count
title accuracy
false confident IDs
```

## Phase 4: Measurement
### 2.5 hours

Build:

- scale calibration
- spine height
- spine thickness
- shelf run
- room geometry MVP

Test against tape measurements.

Do not fake precision if geometry is uncertain.

## Phase 5: Pricing
### 2.5 hours

Build:

- identification -> query
- UAE source retrieval
- replacement cost
- used-market price
- source URL
- date
- currency conversion fallback
- second locale
- appraisal suppression

## Phase 6: Non-Books
### 1 hour

Build:

- non-book detection
- classification
- brand/model when visible
- approximate dimensions
- sourced replacement price/range
- art appraisal rule

## Phase 7: Packet + Evaluation
### 1.5 hours

Build:

- totals
- validation
- review queue
- `claim_packet.json`
- HTML report
- evaluator

## Phase 8: Full Evaluation
### 1.5 hours

Run against ground truth.

Identify:

- largest miss
- likely cause
- cheapest intervention

Make one measurable improvement.

## Phase 9: Submission Material
### 2 hours

Prepare:

- README
- architecture note
- failure log
- final compliant run
- demo video
- claim packet
- results sheet

Do not leave documentation until the last 20 minutes.

---

# 51. Development Discipline for Codex / Devin

Before implementing a significant subsystem:

1. State the problem being solved.
2. State the simplest implementation.
3. State how it will be measured.
4. Implement it.
5. Run the measurement.
6. Record failures.
7. Only then improve it.

Do not autonomously expand scope.

Do not introduce:

- Redis
- message queues
- Kubernetes
- complex background orchestration
- vector databases
- agent frameworks
- cloud infrastructure

unless a concrete requirement makes them necessary.

For background processing, normal Python async/tasks are sufficient unless proven otherwise.

---

# 52. Model Usage Rules

Every model call should have a narrow responsibility.

Good:

```text
Identify this book spine from these evidence crops.
Return structured fields and confidence.
```

Good:

```text
Classify this visible non-book item.
```

Bad:

```text
Analyze the whole room and return the complete insurance claim.
```

Prefer:

```text
model inference
+
deterministic validation
+
external retrieval
```

over:

```text
one powerful prompt
```

---

# 53. Source-of-Truth Hierarchy

When evidence conflicts, use this hierarchy:

```text
explicit user correction
        >
strong captured visual evidence
        >
multiple agreeing observations
        >
external catalogue match
        >
single weak model inference
```

Pricing:

```text
retrieved local listing
        >
retrieved foreign listing + conversion
        >
unknown
```

There is no:

```text
LLM remembered approximate price
```

option.

---

# 54. Definition of Done

The project is done when:

- [ ] User can start a live sweep from a phone/browser
- [ ] Voice interaction works in real time
- [ ] Camera remains active
- [ ] Agent gives live capture-quality guidance
- [ ] Inventory populates before the sweep ends
- [ ] User can verbally correct inventory state
- [ ] Books are persistently tracked rather than duplicated per frame
- [ ] Book titles are only filled when evidence supports them
- [ ] Unreadable books remain unidentified
- [ ] Every book has measurable spine dimensions or explicit uncertainty
- [ ] Metric scale method is documented
- [ ] Shelf run is computed
- [ ] Replacement and used prices are retrieval-backed
- [ ] Every price has source + URL + date
- [ ] UAE/AED pricing works
- [ ] Same 10 books can be priced in another locale
- [ ] Rare/signed/antiquarian books route to appraisal
- [ ] At least eight non-book categories can be inventoried
- [ ] Art defaults to appraisal
- [ ] Room floor/wall/shelved-wall areas are calculated
- [ ] Claim packet is generated within five minutes
- [ ] Required JSON schema is preserved
- [ ] Totals are deterministic
- [ ] Every evidence reference resolves
- [ ] Review queue exists
- [ ] Ground-truth evaluator calculates every official pass bar
- [ ] Cost per sweep is reported
- [ ] Latency per stage is reported
- [ ] Three largest failures are documented
- [ ] At least one failure includes a measured attempted fix
- [ ] README gets a stranger running in <15 minutes
- [ ] Final evaluation uses ≥60 books, ≥2 shelving units and ≥8 non-books
- [ ] Final demo is one unedited take ≤6 minutes
- [ ] No secrets are committed

---

# 55. Final Engineering Philosophy

The goal is not to maximize how many fields the model fills.

The goal is to maximize the number of **defensible fields**.

Prefer:

```json
{
  "title": "",
  "status": "unidentified"
}
```

over a plausible unsupported title.

Prefer:

```json
{
  "amount": null
}
```

over a plausible unsupported price.

Prefer:

```text
measurement confidence: low
```

over fake precision.

An empty field with insufficient evidence is valid.

A populated field without evidence is a defect.

That principle should drive the entire implementation.
