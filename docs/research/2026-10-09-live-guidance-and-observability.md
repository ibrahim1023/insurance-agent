# Live guidance and observability research — 9 October 2026

Status: documentation/source research and local code inspection. No new API calls, model changes or phone trial. Proposed behavior is not implemented.

## Verified provider capabilities

The existing [GPT-Realtime-2.1 model](https://developers.openai.com/api/docs/models/gpt-realtime-2.1) supports image input and audio input/output; video input is not supported. Keep snapshots distinct from continuous video. The model page also documents reasoning effort as a latency/cost consideration; a more expensive or newer model is not a demonstrated fix for the application's coverage failures.

[Realtime conversation guidance](https://developers.openai.com/api/docs/guides/realtime-conversations) describes adding image message parts and explicitly requesting responses. [VAD documentation](https://developers.openai.com/api/docs/guides/realtime-vad) separates speech segmentation, automatic response creation and interruption. [Client events](https://developers.openai.com/api/reference/resources/realtime/client-events) document `response.create`, one response writing to the default conversation at a time, and cancellation/audio clearing.

The [server-controls guide](https://developers.openai.com/api/docs/guides/voice-server-controls) documents an optional server sideband for the same WebRTC session. For the existing Realtime calls route, the SDP response's Location supplies the call ID. The same guide also contains newer Live session instructions: these are different protocols, so retain the current Realtime contract until a separate migration is tested.

These sources establish available mechanisms, not device latency, reading accuracy or success of our current account's requests. Source access date: 2026-10-09.

## Root cause and options

Current `lifecycle.js` sends APP-STATE without requesting speech. With automatic responses disabled, a silent sweep receives no capture-event guidance after greeting. The live model only receives a current image on a user turn. Quality/coverage and voice response scheduling therefore need their own interface.

| Option | Benefit | Cost / limitation | Decision |
|---|---|---|---|
| Explicit guidance scheduler on the existing data channel | Smallest change; retains tested audio/correction transport | Browser owns event/response lifecycle | Recommended first |
| Server sideband scheduling/tools | Centralizes control and usage collection | Another socket, lifecycle/reconnection and contract integration | Consider after the first route proves insufficient |
| Replace provider/Realtime transport | May alter conversation behavior | Does not repair missing keyframes, geometry or source coverage; requires fresh device tests | Defer |

## Proposed scheduler

Create a small module reached by capture events, not by every polling tick. Its input is a timestamped, validated quality/coverage event containing saved-frame reference, observed row/region, issue, severity and expiry. Local blur/exposure/motion checks run cheaply at a sustainable frequency; model observations remain slower supporting evidence. Calibrate thresholds per actual resolution/region using labelled failures. A bright A4 marker is not automatically glare, and camera motion is not automatically unreadable footage.

Priority: current user speech/correction → actionable capture recovery → processing summary. Maintain one active spoken response. Coalesce warnings; once an issue clears, discard its queued message. A sustained issue gets a bounded reminder, not a response on every frame. Ask for a hold, closer view or overlap inside the ongoing pass. Only describe gaps in observed/registered rows; an unseen shelf cannot be reported as a known omission.

Keep `create_response=false` with interruption enabled if the existing manual-response policy remains; an explicit scheduler makes that policy useful. A new user turn invalidates an obsolete guidance response. Function results use the existing validated backend. Unknown targets cause clarification. Do not relax same-frame evidence validation: solve target availability by analyzing selected current evidence or offer a spatial clarification while inspection runs.

Send only changed, compact application facts plus the current-view reference; retain an explicit unavailable context for stale frames. Preserve high-resolution reading originals independently of the compressed voice view. Choose a voice image policy from measured dimensions, readable content and negotiated SCTP limits, not arbitrary byte-cap increases. No periodic image flood is necessary for a quality event whose facts are already validated.

## Telemetry and recovery

Record event IDs, monotonic timestamps, frame age/dimensions, channel byte size, unavailable reason, input commit, response created/done status, playback start/clear, tool latency and authoritative correction result. Store provider usage by response ID once; retain missing usage as unknown. Record model/rate version and distinguish partial token estimates from total reconciled sweep cost. Private transcripts/images belong in local evidence with explicit export review, not routine logs.

On peer failed/closed, show voice unavailable immediately and preserve ongoing capture/postwork. A transient disconnected state may receive a documented grace interval; it must not keep displaying connected indefinitely. Reconnection creates a new voice-session identity and restores compact validated sweep state without replaying paid tools. End call leaves normal server postwork intact. Camera-only and muted modes have explicit states.

## Acceptance gate

Local controller tests must cover: silent sweep + actionable event creates one response; unchanged polling creates none; repeated warning coalesces; clear/stale event is discarded; user speech cancels/supersedes guidance; disconnect changes the UI; tool/summary lifecycle preserves existing ordering and correction gates. Integration must distinguish saved-frame HTTP success from image delivery and model response.

Then run one bounded owner device trial at the negotiated evidence resolution: silent blur/recovery, glare, overlapping row transition, interruption, one source-grounded correction, Stop Sweep, completed bounded work and summary. Measure event-to-request and event-to-audible-guidance distributions, interruptions, missing events and errors. Declare proposed latency targets before testing; do not retroactively tune the verdict. Voice quality after these changes remains **NOT MEASURED**.
