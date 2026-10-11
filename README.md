# Library Claim Agent — evidence-backed inventory prototype

Local portfolio prototype for an evidence-backed library inventory and claim report.
It captures camera frames in a browser, runs vision/reading/pricing stages, and
produces a validated **partial** claim packet (`claim_packet.json` +
`report.html`) per sweep.

The intended product is a guided library inventory with inspectable evidence,
source-backed values and honest unknowns. The user selected both a technical
portfolio showcase and a later consumer product, **showcase first**. This remains
a development checkpoint; reliable whole-room counting and measurement remain unverified.

## Start with the current evidence

- [Current readiness](docs/readiness.md) — implemented, failing and unmeasured capabilities.
- [Recovery roadmap](docs/recovery-roadmap.md) — ordered fixes and evidence gates.
- [Primary-source research](docs/research/README.md) — model, catalogue, pricing, voice and measurement options.
- [Product direction](docs/product-direction.md) — showcase scope and later consumer workflow.
- [Implementation review](docs/2026-10-09-implementation-review.md) — concrete defects and reproducible probes.
- [Historical field results](docs/preflight-results.md) — preserved real trials and failed approaches.

The owner-counted 59-book trial saved 117 frames in 118.123 seconds, but produced
71 provisional tracks, only five accepted identities, zero accepted prices and
no room measurements. The packet correctly keeps the physical count unknown.
Recorded postwork was 48.489 seconds: timely partial output, not a complete claim.
Research recommendations are not installed models or implemented features.

## Prerequisites

- Python 3.12 (tested via `.venv`)
- Node.js — required only for the JS unit tests (`node --test`)
- An OpenAI API key for WebRTC voice and the default inventory provider
- An optional Groq API key for opt-in scene analysis and spine reading; Groq listing discovery is still evidence-gated

## Install

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pip install -r preflight/requirements.txt   # preflight probes only
cp .env.example .env     # then put your real OPENAI_API_KEY in .env
```

`.env` stays local; it is ignored by `.gitignore` and must never be committed.
Fresh-setup time: NOT MEASURED.

### Optional Groq inventory provider

`INVENTORY_PROVIDER=openai` keeps the existing behavior. Setting
`INVENTORY_PROVIDER=groq` with a server-side `GROQ_API_KEY` selects Groq-hosted
Qwen for saved-frame scene proposals and spine reading (one original crop per
book; at most three images per reading request). OpenAI WebRTC voice remains
unchanged and still requires its own key. No automatic Groq-to-OpenAI fallback
is enabled. Provider failures and quota limits remain visible in saved stages;
unsupported identities and the physical book count stay unknown.

**Groq pricing discovery is not promoted.** The Groq-hosted GPT-OSS browser
search adapter is mock-tested, but product pricing stops before that call until
a separately approved public-book probe confirms machine-readable source URLs
and access/cost. In Groq mode eligible book prices stay null with a
`source_url_unverified` review reason; in default mode the existing OpenAI
candidate discovery and retrieved-page quote checks remain as before. Groq is
hosted, not local: enabling it during an actual sweep sends selected shelf
frames and crop images to Groq. A bounded public synthetic-image probe made
three Groq requests: both scene requests were rate-limited (one provider log
confirms a 1,000-output-token/minute limit against 1,024 requested), and one
crop response was incomplete and rejected. The configured scene cap is 6,000,
so Groq mode is not ready for claims on this account. No private-image upload,
accepted Groq reading, physical-count pass or price-accuracy benchmark followed.

## Run (historically verified local launch)

```sh
APP_ORIGIN=http://localhost:8001 VISION_ENABLED=false \
  .venv/bin/python -m uvicorn backend.server:app \
  --host 127.0.0.1 --port 8001 --env-file .env
```

Then open http://localhost:8001 in a desktop browser.
`GET /api/health` returns `{"ok": true, "openai_key_present": <bool>}`.

> **Note:** `VISION_ENABLED=false` disables automatic vision analysis during
> capture — this launch is a manual development checkpoint, not an
> assessment-ready automated sweep. It does not affect voice: starting a sweep
> still sends microphone audio plus selected saved camera snapshots to OpenAI
> for voice context, and **Stop Sweep** still launches bounded post-sweep
> vision/reading/pricing work. With automatic vision off, postwork selects
> saved frames that have not been analyzed within the existing call/time budget
> and records deferred views. It cannot prove spatial shelf coverage or a
> physical book count. Enabling vision does not fix that limitation. Follow
> the device and detector gates before treating a sweep as a measured result.
> This command does not imply a server is running now.

## Manual testing sequence

For a development phone sweep with `VISION_ENABLED=true`, use **Start Sweep**, then **Stop Sweep**, and wait for automatic processing. This currently yields a partial inventory. Analyze/Read/Retrieve prices are diagnostic/manual actions; invoking Read or Price before the end may retain IDs and prevent automatic scene reconciliation rather than silently overwrite a correction or price.

1. **Start Sweep** — choose country (e.g. AE) and currency; frames upload as
   you point the camera.
2. **Analyze latest saved frame** — explicitly requests book/item proposals;
   this button works with automatic vision disabled.
3. **Read spine crops** — extracts evidence-linked text with conservative
   identification gates. Catalogue lookup and manual identity editing are not
   implemented; uncertain readings stay unidentified.
4. **Retrieve prices** — the default provider attempts UAE-first then fallback
   discovery; every accepted price requires a retrievable source URL and timestamp.
   Groq mode currently reports source-proof review instead of a model-produced price.
5. **Stop Sweep** — saves the real end timing, returns promptly, and starts
   bounded post-sweep work in the background: per-sweep vision drain, selection
   of not-yet-analyzed saved frames within the existing call budget, repeated-view
   scene reconciliation, spine reading, then eligible pricing, sharing one 240-second
   post-sweep deadline. Deferred and unmatched views remain in review with their
   evidence; a provisional track count is not a verified physical room count. The UI keeps polling, shows the
   persisted processing stage, and the validated packet is published
   automatically when processing settles — including after a recoverable
   stage failure. Voice stays connected while processing runs; **End call**
   stops audio but does not cancel the saved-sweep work (the
   `POST /api/sweeps/{id}/interrupt` endpoint does).
6. **JSON / HTML** — open `claim_packet.json` and `report.html` from the UI or
   `GET /api/sweeps/{id}/claim_packet.json` / `.../report.html`.

**Start sweep** also connects voice. Voice and its selected snapshots still
use OpenAI. With the default inventory provider, analyze/read and eligible
price discovery can use OpenAI; with Groq selected, analyzed frames and crop
reading can consume Groq quota instead. Groq price discovery currently makes
no request and returns an explicit review reason. These hosted services may
incur charges depending on your account. Uploading frames and generating the
packet artifact itself do not call models.

### iPhone testing (local HTTPS)

Phone and Mac must be on the same trusted Wi-Fi network. Historical launch
example (LAN address, binding, certificate SAN and APP_ORIGIN must match the
current network; this is not a current reachability check):

```sh
APP_ORIGIN=https://192.168.1.13:8443 VISION_ENABLED=true \
  .venv/bin/python -m uvicorn backend.server:app \
  --host 192.168.1.13 --port 8443 --env-file .env \
  --ssl-certfile .devin/certs/safari.pem --ssl-keyfile .devin/certs/safari-key.pem
```

Then open https://192.168.1.13:8443 in iPhone Safari. Prerequisites:

- The certificate and private key in `.devin/certs/` are **not included in the
  repository**. Generate and trust your own local mkcert pair for the Mac's
  current address. The certificate SAN, `APP_ORIGIN`, `--host`, and the URL you
  open must match; never reuse a certificate for a different LAN address.
- Safari will show a certificate warning unless the mkcert root CA is
  installed and trusted on the iPhone (install the public `rootCA.pem`, then
  enable full trust in Settings → General → About → Certificate Trust
  Settings). Do not bypass warnings; without root trust, camera and
  microphone will not work.
- Safari will prompt for camera and microphone permission on first use.
- **Start Sweep** connects paid voice immediately and sends selected saved
  snapshots for voice context regardless of `VISION_ENABLED` (the flag only
  controls automatic per-frame analysis during capture, which the command
  above enables for the next trial); Analyze/Read can make hosted model calls,
  and default-provider pricing can make search calls. In Groq mode price
  discovery is gated and returns unknown. **Stop Sweep** launches bounded
  post-sweep stages; **End call** stops audio but leaves that work running.
- Earlier iPhone trials support lower-resolution capture delivery, owner-heard
  voice/interruption and normal-end partial processing. Higher-resolution voice
  concurrency, revised active guidance and complete inventory accuracy remain
  NOT MEASURED. See the dated field results rather than treating this as a pass.

## Tests

```sh
.venv/bin/python -m pytest tests/backend tests/preflight -q
node --test tests/backend/*.mjs tests/preflight/test_voice.mjs
```

Latest local `main` verification: **576 Python passed**, one existing
Starlette/httpx deprecation warning; **160 JavaScript passed**, zero failed.
The clean feature worktree passed 572 Python tests with 4 skipped because it
lacked ignored local fixtures. Groq-provider cases use synthetic evidence and
mocked HTTP; these tests do not establish real-device, physical-count, title,
price or measurement accuracy.

## Known blockers and limitations

- **Spatial coverage and device guidance are unverified**: postwork now accounts
  for every saved view, processing selected frames within its budget and marking
  the rest deferred. It cannot identify all shelf rows or certify whole-room
  coverage. The spoken guidance scheduler is disabled pending a bounded phone
  test; voice-loss status is fixed in local tests, not yet verified on-device.
- **Physical tracking is unresolved**: repeated-scene reconciliation can retain
  a corroborated subsection while other observations remain outside the active
  reading/pricing inventory. Offline shelf atlases are not integrated into claims.
- **Live UAE discovery is blocked**: the real five-book AE/USD probe found
  0/5 product URLs and 0 accepted prices in 17.2 s — search returned
  homepages/irrelevant results. Automatic discovery needs improvement; this
  result did not exercise source extraction because there were no product URLs.
- **Physical book counting is unproven**: the frozen 14-book row produced
  15 proposals, a 7.14% count error. Only 7/14 spine boxes matched the labels
  at the preset overlap threshold; this is not 7.14% recall or sweep accuracy.
- **Room measurement is unsupported**: all room geometry fields are `null` /
  `unknown`. The A4 homography preflight exists, but runtime calibration and
  real photo/tape validation are not complete.
- **Non-book items are unpriced**: any item price is rejected as unsupported;
  item subtotals stay `null`.
- **Packets are always partial** — no path in this build can emit a complete
  claim; totals only sum validated sourced prices.
- **iPhone camera needs a secure origin**: use trusted local HTTPS. Lower-resolution
  owner trials are documented; the later camera-only preflight received 49 frames
  at 720×1280, but its full browser telemetry and concurrent voice remain
  unmeasured. Product capture still requests ideal 640×480.
- No evaluator/demo mode claims; this checkpoint is not a finished product.

## Reference and portfolio evidence

The [Insurance Claim Live Agent Team](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team)
is a reviewed interaction reference, not a benchmarked inventory solution. It
informed camera-aware conversation and asynchronous tools; the physical-book
count, evidence integrity and sourced values must be measured independently.
Its live runtime was not run here because its Google credentials were unavailable.

Current code uses OpenAI Realtime (`gpt-realtime-2.1`), scene proposals
(`gpt-5.4-2026-03-05`) and crop reading/discovery (`gpt-5.4-mini-2026-03-17`),
with Python/FastAPI/Pydantic/httpx/OpenCV and plain browser JavaScript. Contract
JSON files define the pinned configuration; research candidates are not runtime
dependencies. Fresh setup time, final compliant-room evaluator, complete cost
including voice, privacy-reviewed public sample and unedited final demo remain
pending. Keep original private runs local.
