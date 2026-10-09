# Preflight results — 2026-10-06

> **Current-status index added 9 October 2026:** use [readiness](readiness.md) for
> today's implemented/failing/unmeasured capabilities, [implementation review](2026-10-09-implementation-review.md)
> for reproduced defects, and [research](research/README.md)/[recovery roadmap](recovery-roadmap.md)
> for proposed fixes. The dated experiments and older requirement/submission
> audits below are preserved historical snapshots; their “missing”/test-count
> statements are not all current. The 9 October full gate was 508 Python and 120
> JavaScript passing tests, with no new field accuracy result. Portfolio direction
> is showcase first, consumer workflow later; neither waives assignment pass bars.

**Status: iPhone duration/delivery supported with a preview-continuity warning; the guarded adapter previously extracted 3/5 new quotes from seeded listings, but the integrated live AE/USD discovery probe found 0/5 eligible product URLs; used pricing remains incomplete; two geometric spine probes and both model-backed observations fail the single-row count check; A4 measurement remains unmeasured.** All four probe tools have been implemented and locally tested. The phone run supports basic browser capture/voice coexistence at 640 × 480. The original 0/5 pricing baseline is preserved alongside the measured source-adapter improvement, not overwritten.

| Experiment | Implementation readiness | Field result | Current blocker / next check |
|---|---|---|---|
| iPhone Safari camera + full-duplex voice + uploads | Complete owner-run log supplied | ≥120-second duration/delivery supported; preview continuity WARN | Investigate late preview-clock plateau and retest intended shelf-capture resolution/load |
| Five-book UAE replacement/used retrieval | Deterministic Amazon.ae adapter added; no new providers/dependencies | Baseline 0/5 → replay/live 3/5 new quotes; used 0/5; latest live 5/6 HTTP200 and one HTTP508 | Author/canonical-work corroboration, unsupported sources, automatic discovery, used/FX coverage and owned-edition equivalence |
| Spine candidates on current shelves | Contours baseline preserved; optional boundaries method added | Both FIELD FAIL: contours 10 candidates/0 matches; boundaries 23 candidates/10 matches against 14 books | Stop tuning this row; semantic detection/verification unresolved; independent quality/orientation cases and A4 inputs still needed |
| Same-plane A4 spine measurement | Manual-quad homography and tape comparison implemented | BLOCKED / NOT MEASURED | A4 beside spines in the same vertical plane; photo and tape-measured height/thickness pairs |

These are independent source, implementation and input gaps, not consequences of room-geometry failure. No geometry result blocks unrelated subsystems. Unsupported measurements and prices remain null.

## Live 59-book shelf trial — owner-run, 2026-10-08

The owner independently reported **59 physical books** before one continuous iPhone Safari sweep, `34ccbe8ed7fe4c488b6d01697a549aa4`. The run saved **117 portrait frames** (480×640), ended normally after **118.123 s**, and published a current, `partial` packet after **48.489 s** of post-sweep work. Eight vision stages completed, each returning 9–17 book proposals; a read-only replay of scene planning found **zero supported repeated scenes**, so no cross-shelf physical count could be established. The packet contains **71 provisional book tracks**, 58 flagged as potential duplicates, and `physical_book_count: null`. Relative to the owner's 59, misreading 71 tracks as a physical count would be **12/59 = 20.34% high**, but the packet correctly withholds that whole-sweep metric. Only **five** tracks were identified; 22 readings were unreadable, 36 low-confidence, eight contradicted, and five accepted. There were ten provisional non-book tracks, no recorded corrections or accepted prices, and room measurements remain null. Known model-cost estimate **USD 0.25543525** excludes voice and is not a reconciled bill. This is real-shelf development evidence but falls below the final ≥60-book environment requirement; other room/content requirements have not been verified.

A read-only scene-overlap check (no provider calls) found 5, 6, 6, 5 and 5 SIFT-matched spines between successive analyzed views from about 21 through 108 seconds. Their visible-proposal counts changed as the camera panned (9, 10, 12, 13, 13, 17); the existing scene planner requires repeated counts and rejected these groups as unsupported. The first and last analyzed transitions had no spine matches, and the 1-FPS ORB chain had breaks in those ranges. This explains why the same-scene reconciliation that helped the two five-book layouts cannot establish a physical count across this larger shelf. Summing view counts or weakening one threshold would conflate duplicates with genuinely new books.

A further owner-approved, **read-only one-to-one matching probe** used the frozen SIFT ratio and descriptor threshold on adjacent analyzed views. For this 59-book run, 77 admitted book observations across seven book-bearing views yielded 97 plausible pair edges but **zero pairs unique at both endpoints**; all 77 stayed single-view evidence under that strict rule. The two prior five-book runs yielded only 3 unambiguous links among 57 observations (niche) and 0 among 35 (tabletop). This shows why a greedy overlap count is not a trustworthy book-identity association: loosening the matcher to force a numerical answer could merge different books. The owner counts were used for evaluation only, not as matcher inputs; no saved sweep files, packets, provider calls or thresholds changed in this probe.

A second approved **geometry-and-neighbor feasibility probe** used a small, visually labelled saved-frame sample before checking matcher scores. In the 70→87-second overlapping pair, six same spines could be recognized by printed text, distinctive appearance and consistent neighbor order. Direct ORB alignment succeeded, yet only **one of six** pairs met both the existing SIFT feature threshold and projected IoU ≥0.5 (that pair had IoU 0.747); five legitimate repeats would remain unlinked. The 21→35-second pair showed **no visually confirmable same spines** but generated **22 SIFT-only candidate pairs**; direct ORB alignment failed, so the geometry gate rejected all 22 rather than falsely merging them. Later views contained two visibly different volumes with the same series title; direct alignment also failed there, so no identity was asserted. This small sample supports rejecting unsupported matches but not accurate panoramic counting. The hand labels are analyst observations on these frames, not comprehensive owner-approved ground truth; original sweep files and model thresholds were unchanged, and no provider call was made.

A later throwaway **one-FPS optical-flow check** (no provider calls or saved-file changes) tracked up to 60 initial feature points per labelled spine over 17 intervening frames in the same six-book overlap sample. The counts of points landing inside their independently identified later spine boxes were **0, 0, 8, 1, 1 and 0**; retaining many tracked points alone did not establish the correct book. In a different-section 14-frame control, points from eight of nine source spine boxes survived, and many landed in some later book box despite no visually confirmed same spine. Thus optical-flow endpoint location alone cannot be promoted as identity evidence; no matcher code or threshold was changed.

The approved P1 implementation-plan gate froze **16 analyst-visible same-spine pairs and six different-book controls** from the 59-book and two five-book sweeps in private `.scratch/assessment-run/panorama-labels.json`. The reproducible local `.scratch/assessment-run/panorama-probe.py` used the unchanged ORB, projected IoU≥0.5, HSV distance≤0.35 and SIFT≥8 gates with monotonic one-to-one order. It accepted **0/16** true repeat pairs and **0/6** different-book controls; no pair supplied two unambiguous spatial anchors for a neighbor-based extension. The stop gate failed, so no production panorama rule, model call, threshold change or packet rewrite followed. These analyst-provisional pair labels are a development safety check, **not** an official count-accuracy sample.

The owner clarified that the agent **did see books and read some names aloud**, but struggled to analyze many books together and to give a count. Three `/voice_view` HTTP requests returned 200 during this run, but that does not prove delivery into or understanding by the Realtime model. A read-only replay of those saved voice views under the configured 24,576-byte JPEG cap gives two **270×360** images and one **360×480** image from 480×640 source frames; all three return **zero same-frame inventory targets**. No on-phone `view_sent`/`view_unavailable` event timings or agent spoken-response recording were retained, so the precise source of the owner's experience and pause duration are **NOT MEASURED**. Do not lower book-identification confidence or claim accuracy based on this run.

A separate owner-observed voice issue in the preceding real-shelf run was repeated locale speech, including calling AE a currency. The saved sweep and Realtime session use **country AE / currency AED** correctly, but browser polling previously inserted a new locale-bearing `APP-STATE` user message after each frame-only state change. A bounded test-first change suppresses frame-only repeats, sends fresh state before each committed voice/typed turn, and tells Realtime to confirm locale once while distinguishing AE from AED. The updated local gate is **500 Python tests passed with one existing deprecation warning and 119 JavaScript tests passed**. The app was stopped during changes; no new phone run has verified the spoken behavior, so the audible issue is **NOT MEASURED** after the fix.

## Replacement shelf atlas — local geometry gate, 2026-10-08

A source-frame-aware atlas builder now runs offline on saved JPEGs, retaining each input's 3×3 frame-to-atlas transform. Synthetic translation and three-view overlap tests verify coordinate consistency; unrelated images and an insufficient-overlap warp return unresolved. A competing-motion test moves four textured synthetic book spines 31 pixels relative to their background: the first implementation falsely accepted it, and a test-first competing-homography gate now rejects it as parallax. These thresholds are initial declared geometry safety choices, not calibrated book-count accuracy.

On private saved evidence, the 70–87-second same-shelf pair yields an 801×743 atlas and the full 18-frame (70.3–87.3-second) sequence yields a 944×991 atlas with no failed adjacent links. The 21–35-second different-shelf pair is rejected. A repeated five-book pair yields a bounded 564×787 atlas rather than OpenCV's earlier implausible 2116×4272 panorama. ORB full-frame registration originally produced a 24.2-pixel disagreement between the spacings of two analyst-matched spine pairs; independent full-frame SIFT registration reduces it to 9.8 pixels. The x-only analyst labels have imprecise centers, so this is a *limited registration check*, not verified per-book instance localization, whole-shelf coverage or count accuracy. The old packet and state files were hash-checked unchanged in a read-only replay. No model call, phone server restart, detector adoption or physical-count change occurred. Spine-instance segmentation, stacked/flat coverage, and final full-room counting remain **NOT MEASURED**.

**Detector A2 gate blocked, not passed.** The local environment has no book-spine model weights and no Torch, Ultralytics, ONNX Runtime or Detectron2. Published candidates checked: Open Shelves has a non-commercial CC BY-NC-SA dataset and no reproducible local checkpoint; an older Mask R-CNN demo requires TensorFlow 1.15/Python 3.4 and has no verified model licence; Roboflow Universe candidates offer hosted inference or weights downloaded through an authenticated service, with model terms/access still to verify; one Hugging Face detection Space exposes pickle-serialized `.pt` checkpoints but no established model licence. A book-specific, locally runnable, licensed *instance* model has not been selected, downloaded, benchmarked or installed. Existing contour/boundary/VLM attempts are failed baselines, not substitutes. No private shelf was sent to a new provider. The A3 claim-count integration gate remains closed; the 14-book upright benchmark and new independently labelled real/flat/stacked cases are still required before any count claim. Candidate sources: [Open Shelves](https://github.com/capjamesg/open-shelves), [older Mask R-CNN demo](https://github.com/mabouseif/Library_Book_Detection), [Roboflow weight access](https://docs.roboflow.com/models/model-weights/download-roboflow-model-weights), [Hugging Face detection Space](https://huggingface.co/spaces/siddharth060104/book-spine-detection/tree/main).

## Higher-resolution iPhone probe — offline-ready, device NOT MEASURED

The standalone Safari page now requests an *ideal* 1280×720 rear-camera stream while opening the microphone, logs the actual negotiated `MediaStreamTrack.getSettings()` values, flags non-advancing preview timestamps, and records 1-FPS upload success/failure, encoded dimensions/bytes and start-to-upload latency. It starts in local camera/microphone-only mode: OpenAI voice has a separate unchecked billed opt-in. The preflight server denies remote clients by default and accepts only the explicitly allowed phone IP when configured. Mock-browser and server tests passed; the existing product camera settings were **not** changed. An owner-run camera-only iPhone Safari session on private Wi-Fi (`runs/preflight-safari/run-2026-10-08T17-54-47-601Z`) saved **49 JPEG frames at 720×1280**, versus the previous 480×640 saved stream. Its 48 consecutive server-side video timestamp intervals were all positive (minimum 1.000 s, median 1.004 s, maximum 1.007 s), from 0.875 s through 49.052 s; saved JPEGs ranged from 102,509 to 242,884 bytes, median 185,431. All **49 received** frame POSTs returned HTTP 200 in the guarded server log; this is not a claim that all attempted uploads succeeded. No `/preflight/session` request occurred and the server had no usable provider key. The camera server was stopped afterward. The browser's actual `getSettings()` values, attempted/skipped uploads, upload latency and preview continuity need the downloadable on-device raw log and remain **NOT MEASURED**; full-duplex voice interruption at this resolution requires separate billed approval. Product camera settings were not changed. Latest complete local checks: **508 Python passed** (one existing deprecation warning) and **120 JavaScript passed**.

## Experimental scene reconciliation — local-only replay, 2026-10-08

After the unsuccessful previous-frame tracker experiment, a post-sweep scene reconciliation slice was tested **offline against copies of both saved runs**. It does not make provider calls and does not edit the original owner packets. It uses independent SIFT spine correspondences to link repeated analyzed views, requires a strict plurality of visible book proposals over at least two related views, and keeps unlinked/partial book records in `unresolved_books` with saved-frame links rather than counting or pricing them as distinct physical books. The niche replay returns **five provisional book records from 31 preliminary tracks**, retaining the other 26 as unresolved; the tabletop replay returns **five from five**. An HTTP-level synthetic two-frame test verifies that reconciliation runs before crop reading/pricing and that the partial packet and HTML report retain links to archived observations. A synthetic two-scene case does not force unrelated views into one scene. Neither real phone packet was regenerated; the HTTPS process still has the prior build loaded until a controlled restart.

This is **not a verified accuracy pass**. Both phone runs show the same five book works in different layouts, not independently labelled books on an unseen shelf, and the frozen 14-book single-row probe remains 15 proposals versus 14 physical spines. The original runs include partial/occluded views, and the supported-scene book count remains explicitly provisional with unresolved evidence. Existing room geometry, pricing coverage and spoken-response timing gaps are unaffected.

## Independent static bookshelf image — owner count 24, 2026-10-08

The owner supplied a new 1024×716 photo of one clearly visible shelf and independently counted **24 physical books**; the earlier ambiguous image was not used. On the unchanged local OpenCV detectors, the full image produced 8 contour proposals and 0 boundary proposals. A manually selected shelf ROI `(x=190, y=145, w=635, h=370)` produced 15 and 16 respectively. These are candidate boxes, not physical book counts; no individually labelled truth boxes were supplied, so localization precision/recall are **NOT MEASURED**. No thresholds were tuned to this image.

With the owner's approval, one model-backed scene analysis was run on a saved JPEG of the new image, with no voice, crop reading, pricing, automatic postwork, or expected count in the model input. Sweep `535092768331484d9d42e38630a06bd4` and its saved vision stage retain the evidence locally. The model returned **22 raw and admitted book proposals**, yielding 22 **unidentified provisional tracks**, one shelving proposal and no unresolved groups. Against the owner's count of 24, the raw proposal-count shortfall is **2/24 = 8.33%**; this exceeds the ≤5% count bar if proposals were treated as books, but it is **not** a validated continuous-sweep physical-count metric. Scene text was not crop-read or independently verified, so no proposed title was accepted as an identification. The single provider request took **34.375 s**; recorded model-cost estimate **USD 0.0435525**, not a reconciled bill. The saved sweep is `interrupted` to prevent automatic follow-on calls; no packet was published. A new real continuous sweep and independent spine-box labels are still needed to evaluate count and localization.

## iPhone sweep of a full-screen Mac image — 2026-10-08

The owner aimed iPhone Safari at the **24-book static image displayed on a Mac** over the iPhone hotspot. This is a repeatable screen-image workflow test, **not** a sweep of physical books or an enclosed library room. The saved run `281b0768329c46b4a09fe216d9e84bac` contains **75 portrait frames** (480×640), ended normally after **75.379 s**, and persisted three user turns. Realtime connected; the owner's evaluation of spoken replies is not yet recorded. The first analyzed frame shows the Mac's chat page rather than the bookshelf image, so retain this capture locally only.

Six vision stages completed at approximately 1, 6, 26, 47, 66 and 75 seconds, proposing 0, 14, 11, 9, 11 and 11 books respectively. Two reading stages completed, with **zero accepted identifications**; no pricing stage ran. Saved frames show distinct left and right portions of the displayed shelf, not one verified full-row view. Scene reconciliation selected the two repeated right-side frames, retaining **11 provisional book tracks** and archiving **39 other observations** as unresolved evidence; those 39 cannot be treated as distinct books or all duplicates. Relative to the owner's 24-book image, the packet's 11-track figure is a **54.17% shortfall if misread as a physical count**, not a compliant-room accuracy result. It exposes a panoramic-coverage limitation in the one-supported-scene reconciliation path; do not hardcode 24 or force unrelated views together.

A read-only geometry check of saved evidence (no provider calls) found 21/21, 19/19 and 9/9 accepted adjacent-frame ORB alignments across the 26→47, 47→66 and 66→75-second spans. Yet chaining those transforms and projecting spine boxes produced only **2, 0 and 1** one-to-one matches respectively at IoU ≥0.5. Direct SIFT crop matching produced 3, 3 and 8 spine correspondences; the earlier 6→26-second chain had four alignment gaps. Thus an accepted alignment chain alone is insufficient evidence to stitch these sections or announce 24 books. No thresholds were adjusted to fit the owner's count.

The original phone packet is `partial`, at current state revision 116 with no stale flag; it contains five provisional non-books including Mac monitor/electronics and shelving, no corrections, no accepted prices, and null unsupported room/spine measurements. Automatic post-sweep work settled in **33.012 s**; known model-cost estimate **USD 0.13525575** excludes voice and is not an invoice. Point-and-talk semantics, interruption timing and spoken count remain owner-observed or **NOT MEASURED** until device feedback is supplied. This test establishes hotspot delivery and honest partial artifact generation, not physical inventory accuracy.

A later **offline-only packet projection change** exposes `physical_book_count: null` and `book_count_scope: corroborated_scene_subset` when book-bearing views remain uncorroborated; the HTML and spoken-summary text say the provisional tracks cover one supported section only. Rebuilding this run in memory retains **11 provisional tracks and 39 unresolved observations**, without asserting a whole-sweep count or editing the original packet. The local checks after this change report **499 Python tests passing with one existing deprecation warning and 117 JavaScript tests passing**. The phone build has not been restarted with this change; the Mac left the hotspot, so the HTTPS server was stopped.

## Second iPhone-to-Mac image sweep — different book layout, 2026-10-08

Owner-run sweep `59b81d8429eb428890fae3eb15e7d9a4` scanned a **different** full-screen shelf image on the Mac over the iPhone hotspot, not physical books. The owner independently counted **26 books** in this image; because the packet withholds a whole-sweep physical count, its official count-error metric is **NOT MEASURED**. Title accuracy and real-room accuracy are also **NOT MEASURED**. The run saved **99 portrait frames** (480×640), normally ended after **100.171 s**, and persisted six user turns; one contains a count question. The owner reports hearing **“about 27”** books. This is an owner-observed approximate current-view answer, not a persisted agent transcript or a verified sweep inventory count. The saved analyzed views cover different portions of the displayed row, with laptop/monitor elements also visible.

Six completed vision stages proposed 17, 13, 15, 16, 12 and 12 books at approximately 1, 27, 47, 72, 98 and 99 seconds. Scene reconciliation retained **12 provisional tracks from one supported group** and archived **45 other observations as unresolved**, not as 45 distinct books. The **current, partial** packet at revision 144 explicitly has `physical_book_count: null` and `book_count_scope: corroborated_scene_subset`. If its 12 provisional tracks were mistaken for a whole-image count, they would be **14/26 = 53.85% short** of the owner's count; this is not a published physical-count estimate. There are seven provisional non-books (three electronics, four shelving), two completed reading stages but **zero accepted book identifications**, no price stages or accepted prices, and no recorded corrections. Post-sweep work took **43.074 s**; known model-cost estimate **USD 0.1865910** excludes voice and is not a reconciled bill. This confirms the new fail-closed packet semantics on an actual phone run, **not** a corrected physical count or compliant-room result.

Offline reading diagnosis found 12 retained crop readings: **eight low-confidence, three crop/scene contradictions, and one unreadable**. One high-confidence crop visibly contains the author and title separately while the scene had concatenated those exact strings into its title field. A test-first validator change now accepts only the exact normalized author-plus-title or title-plus-author combination when both crop roles are legible; the other two real contradictions still fail that check. Full local verification after the change: **500 Python tests passed with one existing deprecation warning; 117 JavaScript tests passed**. The archived sweep and packet remain unchanged, and any improvement to accepted identification in a new phone run is **NOT MEASURED**.

## Independent tabletop phone sweep — 2026-10-08, 10:30 UTC

Owner-run sweep `8a4eb5399e444b268fa97282809a41bf` used a different arrangement of five physical books on a table. The owner reports the live agent answered **“I see 5 books”** when asked. This is owner-observed correctness for one current camera view, not a verified persisted response transcript, a deduplicated sweep count or a final assessment result. The saved analyzed views at roughly 9, 18, 26 and 61 seconds visibly show the same five-book row; a foot is visible in the first analyzed frame, so keep this run local rather than using it as submission footage.

- Normal end after **61.777 s**, **61 saved frames**, 8 completed vision stages, 3 completed reading stages; postwork `completed` and partial JSON/HTML published **20.652 s after end**. The original packet has **23 provisional book tracks for five physical books: 360% count error**, 19 flagged possible duplicates, 4 identified, 0 accepted replacement or used listings, and 2 provisional `table` item tracks. Known model-cost estimate USD 0.13205500 excludes voice cost.
- The owner-confirmed voice count of five and the packet's 23 tracks must be graded separately. The owner later reported interrupted speech and the agent repeatedly saying something like “the frame starts with F”. This speech is not recorded. Three user transcripts are saved for this run; none contains the reported phrase, so there is no evidence of that exact phrase being transcribed back as user speech. The application sends raw frame identifiers in APP-CURRENT-VIEW user-message context and repeated APP-STATE messages while polling; Realtime server VAD permits response interruption. Without the phone browser's timestamped response/VAD event log, the cause of the interruption/repetition is NOT MEASURED. An offline read-only replay of both sweeps through the **experimental local tracker changes** now produces five tracks for this tabletop layout but **31 for the prior five-book niche** (520% error). It reuses frozen stage outputs without new provider calls and does not change either original packet. This narrow success cannot establish reliable generalization; the five-book niche regression remains failing, and the live server still runs the earlier imported build.
- In 8 vision stages here, the first book-bearing full row at 9 seconds recorded five; the original tracker then created more IDs across later views. Generic handling for scene text that combines printed title and author improved the offline tabletop replay, but not the other layout. Do not promote model scene titles to authoritative identities, hardcode a five-book ceiling or call the count bar met.

## Reviewed product iPhone sweep — 2026-10-08, 09:25 UTC

Owner-run sweep `289b3e8e9bcd4a7baa231f64c3db33b9` used the reviewed HTTPS build. The saved user agent reports iPhone/iOS 18.7/Safari Version 27.0.1; source evidence is the local `runs/289b3e8e9bcd4a7baa231f64c3db33b9/` state, frames, stages, packet and report. The owner reports that the agent spoke, described books and non-book items in view, read book names correctly to their ear, and announced the selected country/currency. This is owner-observed conversational relevance, not a verified title-accuracy rate or a recorded agent-audio transcript. The server log contains successful `/realtime` and repeated saved-frame `/voice_view` requests, but delivery of every image to the provider and all spoken words are not independently recorded.

- 137 saved frames; normal `/end` persisted a 137.605-second capture duration and `ended_at=2026-10-08T09:28:00.341849+00:00`. The packet was generated at 09:29:02.145205 UTC, **61.803 seconds after the end**. This one measured packet latency is under five minutes; repeatability is NOT MEASURED.
- 15 completed vision stages, 10 completed reading stages, and 9 pricing stages (7 completed, 2 failed). Postwork status is `completed`, meaning the bounded attempt settled, **not** that the claim is complete. Fourteen user turns were saved; **zero corrections** were recorded, so grounded voice-correction effects are NOT MEASURED.
- The partial packet contains **57 provisional book tracks** (3 identified, 54 unidentified) and **14 provisional item tracks** (9 shelving, 5 decor); 28 book tracks carry potential-duplicate flags. These are not ground-truth physical counts or category recall. Crop-reading review reasons include 24 contradictions with prior proposals, 21 low-confidence readings and 11 unreadable readings; identity accuracy and item recall remain NOT MEASURED without complete legibility/item ground truth; the book-count error against the owner's five-book count is measured below.
- **Count failure with owner ground truth:** the owner states that only five physical books were present. The saved analyzed frames at approximately 12, 23, 33, 54 and 137 seconds show the same five-book niche from changing viewpoints. The 15 vision stages recorded 62 book observations across 14 book-bearing frames; only five later observations joined an existing track, leaving 57 tracks. The packet's count error is **1040%** against five physical books (`100 × |57 - 5| / 5`), far above the ≤5% bar for this bounded development case. This is not the ≥60-book compliant final environment. Of 57 tracks, 28 are flagged potential duplicates but are still included in `book_count`. The direct failure is temporal association: candidate matching requires the immediately preceding analyzed frame's evidence and accepted homography/IoU/histogram/title agreement; unmatched proposals create fresh book IDs, which packet aggregation counts indiscriminately. Five frame-alignment failures and title-conflict reviews are recorded. Do not tune solely to this scene, merge on book title alone, silently rewrite the packet to five, or call this a count pass.
- **Bounded fix attempt (offline, same frozen inputs, NOT a passing counter):** a private-fixture regression replays all 15 saved vision outputs through the existing tracking seam without provider calls. Small test-first changes to title-role normalization, incomplete-frame admission, spine-feature fallback and limited earlier-frame evidence reduce the replay count from the prior 57 book tracks to **31** for the owner's five physical books: **520% error**, still far above ≤5%. Focused tracking/vision verification after the attempt: **52 passed, 1 failed**; the latest complete backend/preflight check reports **492 passed, 1 failed** (only the niche five-book replay remains red, with one existing warning); the Node browser/preflight gate reports **115 passed**. A bounded alternative using intermediate one-FPS frame alignment was also measured: 10/11 adjacent frame pairs align between 12–23 seconds, but the one failure breaks a complete transform chain; 30/40 pairs align between 73–113 seconds, with ten breaks. Neither previous-frame tracking nor that simple chain resolves the repeated scene reliably. A further **read-only scene-group feasibility check** used SIFT matches on saved book-spine crops, without model calls: the tabletop sweep has five independent spine matches between every adjacent full-row analyzed view from 9 through 61 seconds; the niche's book-bearing views from 23 through 73 seconds have 3–4, but the 73→85 and 85→93 pairs have none, 93→103 and 103→113 also have none, and 113→137 reconnects to five-book views. Archived images at 85, 93 and 103 seconds include a narrow one-book view, a hand over several spines and a shifted book. The 73-second frame still matches the 113-second frame in three spines, but other fragments lack a uniquely supported scene assignment. A simple per-scene modal five-book count would fit these two captures without proving identity or coverage for a different panoramic shelf. Do not substitute that statistic for an evidence-linked physical book inventory or claim the failure fixed. The live server process still serves the prior imported build; the original run's JSON/HTML remains unchanged. Single-frame model proposals themselves were sometimes mislabeled, and the earlier independently labelled 14-book row still had 15 proposals; simple threshold changes cannot establish the official bar. Treat this code as WIP, not a measured fix or an assessment checkpoint.
- Replacement and used price coverage are both **0 accepted**; their subtotals are null. Room and spine measurements are null. The usage-based known-model estimate is **USD 0.36939945**, excluding unknown voice cost; this is not an invoice. No real-world price or metric measurement can be inferred from the owner's spoken observations.
- The owner later reported occasional long spoken-response pauses and that the agent did not answer a request for the number of books. Two of the 14 persisted user turns contain a book-count question (turn indices 9 and 11); the microphone/transcript path was not simply absent. The voice APP-STATE message supplies book identifiers/statuses rather than a deterministic numeric count, while the Realtime instructions forbid presenting model proposals as a verified physical room count. The end packet's 57 book tracks include 28 potential duplicates; it does not prove 57 physical books or what the count was at the time of either question. The server does not persist per-turn response/audio timing, and the browser's event log was not supplied; **cause and size of the audible pauses are NOT MEASURED**. Separate recorded vision-call median/max are 10,035.8/11,849.7 ms; these do not establish the voice-response delay.
- Saved state revision 304 is newer than packet revision 303, and the packet endpoint reports `x-packet-stale: true`; a later recorded turn left that specific artifact formally stale. Do not use it as the final claim or quietly treat the state as reconciled. Preserve the evidence; report/check the stale flag before any deliberate republishing.

**Checkpoint:** owner-observed point-and-talk and a normally ended phone capture now exist, and automatic stages produced a partial packet. The tested run still has no accepted prices or physical dimensions, many uncertain book tracks, no applied voice correction, and no compliant-room ground truth. It is a useful integrated test, **not** assessment readiness.

## Current product iPhone checkpoint — 2026-10-08

Owner-run sweep `fcfd31c3197d4704ae7d1415a4cf3974` used `https://192.168.1.13:8443`; the saved user agent reports iPhone/iOS 18.7/Safari Version 27.0.1. This is evidence for the current product, separate from the earlier standalone concurrency probe. The owner reports being able to sweep. Source records are `runs/fcfd31c3197d4704ae7d1415a4cf3974/state.json`, `claim_packet.json`, the saved frames and the persisted vision stage.

- 58 saved portrait frames, 480 × 640 pixels. First/last saved capture timestamps are 1.0155928750173189 s and 58.22245770835434 s. These are sampling timestamps, not a measured completed-sweep duration or a preview-continuity result.
- One completed vision stage: 10,639.894916006597 ms; usage-based vision-cost estimate USD 0.014415. Voice and total cost remain unknown.
- Five provisional book tracks and one shelving track; zero accepted identifications or prices. No tracks in this packet have the potential-duplicate flag. These are predictions, not a ground-truth count-accuracy result.
- Realtime SDP exchange returned HTTP 200 and two user turns were saved. One other `/turns` request returned HTTP 422; its rejected payload/reason is not retained in the access log. The owner confirms hearing the agent clearly and successfully interrupting it while sweeping. This establishes owner-observed audible voice and interruption usability, not a timed interruption-latency measurement. Preview continuity remains NOT MEASURED on this build.
- The `/read_spines` response precedes the first `/analyze_frame` completion in the access log. No reading stages/crops were saved; pricing stages are also empty. The existing reading-readiness UI gap remains unresolved.
- State and packet revision 63 show `status: interrupted`, with `ended_at` and `duration_s` null. The access log records `/interrupt` and `/packet`, but no `/end` request for this run. The initiating client action is not established; do not label this a normally completed sweep.
- Packet status is partial; unsupported prices and measurements remain null. Only the analyzed shelf frame was visually inspected, not all 58 frames for privacy or full-room coverage.

**Checkpoint:** actual iPhone-to-Mac HTTPS capture, saved evidence, scene analysis and partial-packet retrieval are supported by this run. Full phone acceptance, normal sweep closure, crop reading, accuracy and complete claim processing are not established. Preserve this run; fix reading readiness and investigate transcript rejection/closure before another assessment attempt.

## Complete iPhone trial — supplied log starting 2026-10-06T16:09:08.450Z

Origin: `https://192.168.168.111:8443`; same reported iPhone/iOS 18.7/Safari Version 27.0.1, at 640 × 480 and 30 FPS with approximately 1 FPS JPEG sampling. A Stop event is present. The filtered source capture and frozen derived metrics are `runs/preflight-safari/chat-capture-2026-10-06T16-09-08-450Z.json` and the adjacent `chat-metrics-2026-10-06T16-09-08-450Z.json`; these are analyst captures of the pasted log, not full raw-log copies.

| Metric | Observed result |
|---|---:|
| Capture-start → Stop | 171.462 s |
| Voice-connected → Stop | 168.439 s |
| Voice connected after Start | 4.847 s |
| Successful / attempted HTTP uploads | 169 / 169 (100%) |
| Successful uploads / all recorded sampling ticks | 169 / 170 (99.41%) |
| Conservative nominal 1 FPS delivery (`floor(171.462)` opportunities) | 169 / 171 (98.83%) |
| Sampling ticks skipped due to upload already in flight | 1 |
| Upload RTT, median / p95 / p99 | 58 / 181 / 840 ms |
| Upload RTT, min / max | 23 / 1,663 ms |
| VAD speech-start events | 22 |
| Agent audio-buffer starts | 20 |
| Provider audio-buffer clears | 15 |
| Owner-marked observed interruptions | 3 |

The ≥120-second duration and ≥95% scheduled-frame-delivery targets are supported by this complete run. No setup failure, failed HTTP upload, audio-playback issue, camera-track-ended, preview-issue marker, or peer disconnect was recorded. Three owner interruption-observed marks supplement the provider VAD/buffer-clear/truncation signals. Response cancellation/truncation during speech is expected barge-in behavior, not automatically a provider failure; the log lacks response IDs, response statuses and spoken content, so do not grade semantic correctness or infer the cause of every cancellation.

**Owner observation:** “It was quite smooth, I was able to communicate very well.” This adds human-observed voice usability evidence alongside the event log. The owner also reports approximately **$0.26 of additional dashboard usage** from the earlier $0.11 baseline; record that as an unreconciled dashboard estimate, not an exact per-run bill. The repeated-image warning remains independent of perceived conversational smoothness.

**Warnings to retain:** Frame 151 took 1,663 ms round trip (154.449 → 156.112 s); the bounded one-in-flight uploader deliberately skipped the sampling tick at 155.433 s. Separately, at wall-clock samples 168.501 and 169.695 s, `video_time_s` is unchanged at 165.814655625 despite ready_state 4. It advances again at 170.697 s. Offline checks of `runs/preflight-safari/run-2026-10-06T16-08-51-774Z/` corroborate the plateau: frame 164 and 165 sidecars have that exact timestamp, and their JPEGs have identical SHA-256 hashes; adjacent frames 163 and 166 differ. A repeated captured image is verified, but a frozen camera sensor versus preview/sampler scheduling or another cause is **not** established. HTTP delivery alone does not establish image freshness. The plateau is outside the initial 120-second window but included in the full-run assessment rather than discarded. Capture continuity remains **WARN**; investigate with the next shelf-quality test rather than repeat paid voice sessions solely to hide the warning.

The Stop event's `video_time_s:0` is caused by clearing the video element before logging Stop; durations above use monotonic `t_ms` instead. Token-reconciled cost remains null because usage was not retained; the owner-reported dashboard increase is an estimate. This result supports the web architecture's basic concurrency feasibility, not higher-resolution book detection or simultaneous vision-model processing.

## Earlier iPhone smoke — supplied log starting 2026-10-06T13:24:10.234Z

Origin: `https://192.168.1.13:8443`; user agent reports iPhone/iOS 18.7 and Safari Version 27.0.1. Camera settings are 640 × 480, 30 FPS; uploads sample the preview at approximately 1 FPS. The pasted log is preserved as a filtered timing capture (without device/group IDs) at `runs/preflight-safari/chat-capture-2026-10-06T13-24-10-234Z.json`; frozen derived results are in the adjacent `chat-metrics-2026-10-06T13-24-10-234Z.json`. The full raw event log has not been saved as a file.

| Observed metric | Result |
|---|---:|
| Voice connected after Start | 2.922 s |
| Recorded successful uploads / recorded sampling ticks | 25 / 25 (100%) |
| Upload round-trip latency, median / p95 | 28 / 31 ms |
| Upload round-trip latency, min / max | 22 / 33 ms |
| Initial speech-button request → audio-buffer start | 1.112 s |
| Detected speech segments | 4 |
| Audio-buffer starts | 5 |
| Speech-stop event → next audio-buffer start, median | 699.5 ms (four samples: 584, 710, 714, 689 ms) |
| Last event since Start | 26.687 s |
| Capture-start → last event evidence window | 25.356 s |
| Voice-connected → last event evidence window | 23.765 s |

All 25 sampled video times increase and preview ready_state stays 4. No failed upload, setup failure, audio playback issue, preview issue, or camera-track-ended event appears in the supplied log. Four provider VAD speech starts are followed by audio-buffer-cleared and conversation-truncated events, supporting operation of the barge-in mechanism. These event types do not prove which words were heard, that each interruption was deliberate speech rather than echo/background audio, or that the generated audio was audible at the speaker; the user reports the interaction now works. There is no manual interruption-observed marker.

Upload latency is matching success minus start client timestamps, excluding JPEG encoding; p95 uses nearest rank. Speech latency is provider event-arrival timing, not acoustic end-of-utterance to audible response. The log has **no stopped event** and is shorter than the required 120-second endurance run, so duration, sustained dropout rate, billing and full concurrency acceptance remain unverified. At 640 × 480 this supports transport feasibility only; dense-shelf identification at a higher capture resolution still needs testing. Do not label the full preflight PASS from this prefix.

## Local evidence (not field results)

Commands run against the current implementation:

```text
.venv/bin/python -m pytest tests/preflight -q
  91 passed, 1 warning in 0.60s, exit 0 (historical pricing-adapter gate)
node --test tests/preflight/test_voice.mjs
  8 passed, 0 failed, exit 0
node --check /tmp/index-script-current.mjs
  exit 0 (current inline browser module syntax check)
```

Python 3.12.11, Node v24.1.0, pytest 9.1.1. The 91-test pricing-adapter gate is historical, preserved at `/tmp/pricing-adapter-final-pytest.log`; earlier 57-test initial-probe and 23-test Safari-only evidence remain historical. The boundary-detector work's full gate reported 98 passing tests at `/tmp/spines-final-pytest.log` (run before test-only review), and the focused spine/CLI gate after test rework reported 18 passing tests at `/tmp/spines-test-rework.log`. Node/voice helper and inline syntax logs are `/tmp/preflight-final-node.log` and `/tmp/preflight-final-jscheck.log` from the earlier unchanged voice probe, not rerun for this pricing-only change. The known warning is Starlette's deprecation of its `httpx` TestClient integration; it is not a failed test. Server/API tests use a stubbed provider and image tests use synthetic rectangles; actual phone and actual retailer replay/live evidence are reported separately.

The probe captures camera/mic together, uploads JPEGs with timestamps, opens Realtime WebRTC, provides an actual speech/interrupt test, and exports raw event logs. It reports voice connection only after a connected peer state; timeouts are failures. Camera-only partial mode remains explicit if voice setup fails. The page permits one run per reload so logs never mix timing origins. Saved evidence is not overwritten when a run ID is reused.

Initial local-only server smoke returned HTTP 200 with `openai_key_present:false`. During owner-run setup, a 403 was reproduced for the intended HTTPS origin; restarting with the matching `PREFLIGHT_ORIGIN` removed that blocker. A later 503 was traced to a missing key in the restarted process; exporting it in the server terminal resolved that blocker. Subsequent health checks returned `openai_key_present:true`, and the iPhone log above records a live session. No billing measurement has been made. The original local-only start command is:

```bash
.venv/bin/python preflight/safari/server.py
```

The script's default origin is `http://localhost:8000` and binding is loopback; the owner-run iPhone setup now uses Uvicorn HTTPS on port 8443 with the exact matching `PREFLIGHT_ORIGIN`. The owner also enabled camera/microphone access on iPhone; the precise permission/trust change was not recorded, so no specific root cause is claimed for the initial getUserMedia denial. The server reads the key from its process environment, not `.env` automatically. Mac and iPhone Safari permissions are independent. Keep keys and certificate private keys local, never in a report/chat.

## Five-book UAE pricing — actual source test, 2026-10-06

The owner supplied five titles/authors and a readable shelf photo. Confirmed names are Capital in the Twenty-First Century (Thomas Piketty), The 4-Hour Workweek (Timothy Ferriss), The Snowball: Warren Buffett and the Business of Life (Alice Schroeder), How to Talk to Anyone (Leil Lowndes), and Think and Grow Rich (Napoleon Hill). The photo supports the Snowball title and Lowndes spelling corrections. Owned ISBNs/editions remain null: listing metadata is not proof of the owned edition.

Evidence lives under `runs/preflight-pricing-2026-10-06/`: `identities.json`, immutable `inputs.json`, `manual-evidence.json`, `baseline-result.json`, `source-inspection.json`, six raw page snapshots, and frozen `summary.json`. URLs were discovered through manual/agent-assisted web search, not through an implemented app search service.

### Full-page manual reference observations (not automated valuations)

| Work | Observed AED quote | Listing format / eligibility |
|---|---:|---|
| Capital in the Twenty-First Century | 177.00 | Amazon.ae hardcover; page's matching item card says only four left, sold by Amazon.ae |
| The 4-Hour Workweek | 56.00 | Amazon.ae English paperback; matching item card says In Stock |
| The Snowball | null | Dar AlSa'da page displays AED60, but stock/new-condition evidence is insufficient; keep it a candidate, not an accepted quote |
| How to Talk to Anyone | 41.59 | Amazon.ae English paperback; matching item card says In Stock |
| Think and Grow Rich | 10.38 | Amazon.ae Fingerprint! English paperback; matching item card says In Stock |

Source URLs, quoted context, listing ISBN/format, verification date and limitations are in `manual-evidence.json`. Observations were verified from full public pages, not prices recalled by a model or copied from search snippets. Item prices exclude shipping and conditional card/member discounts. Exact edition equivalence and checkout/destination-specific pricing are not established; **do not use this table as final owned-book replacement valuations or sum it into a claim**.

Manual source coverage: **4/5 readable in-stock new reference quotes**, one unconfirmed retail candidate, and **0/5 eligible current UAE used quotes** in the bounded source search. The Snowball's BookHero AED10 listing explicitly says Sold out and is excluded. This does not prove that no used offers exist anywhere. Other rejected/inconclusive results include inaccessible Kinokuniya pages (403), digital/Hindi/Chinese editions, unavailable listings, and a store's printed-first-copy notice. Foreign-market/FX fallback remains untested.

### Application baseline — failed extraction, safely missing values

```bash
.venv/bin/python -m preflight.probe_prices \
  --input runs/preflight-pricing-2026-10-06/inputs.json \
  --output runs/preflight-pricing-2026-10-06/baseline-result.json
```

Observed on 2026-10-06 16:51 UTC: **6 selected URL fetches, 6 HTTP 200 responses, replacement accepted 0/5, used accepted 0/5, both 0/5**. Four used slots had no defensible discovered URL; only the sold-out Snowball used listing was fetched. Sum of per-fetch/validation processing was **14.317 seconds**, median **2,547.689 ms**; this excludes manual discovery and is not end-to-end automatic pricing latency. The runner's `MEASURED` status means requests were attempted, not a PASS. Manual quotes were never inserted into the automatic result.

Follow-up raw snapshots reproduce the data-contract failure: the five replacement pages have **no `application/ld+json` blocks**, while BookHero has one malformed block with unescaped line breaks inside its description string (`Invalid control character`). At the baseline, the extractor only read valid JSON-LD Book/Product/Offer records, so it could not reach a valid price record from these structures. Successful HTTP retrieval is not successful extraction; synthetic JSON-LD unit fixtures are not evidence of real-retailer integration.

**Baseline decision:** a supported-source seam was needed; broad validation relaxation, hardcoded quotes, invented used prices and access-restriction bypass were rejected. The owner approved the bounded adapter and selected deterministic extraction first, deferring Context.dev/Groq. No new OpenAI model calls were made by the Python pricing/diagnostic probes.

### Guarded source-adapter improvement

`preflight/prices.py` now recognizes Amazon.ae `/dp/` pages and matches the selected product and offer across two verified buybox layouts. It reads the page's non-JSON-LD buying-option JSON, not a model's price recollection. Acceptance requires primary title/byline and print binding, matching URL/buybox product IDs, one selected offer, explicit NEW/AE/AED metadata, positive stock, and agreement between metadata and the primary displayed price. Additional bundled item inputs, conflicting prices/IDs/bindings, related-product fallback, unknown primary author and unsupported formats return null. Non-Amazon JSON-LD behavior is unchanged.

TDD evidence: four valid-shape tests failed before the adapter; a real nested-buybox layout regression failed before its scope fix; three bundled-item ambiguity tests failed before their guard. The final full local suite is **91 passed, one pre-existing warning**. `tests/preflight/test_amazon_prices.py` contains the new source-shape and negative cases. Actual full-page replay is separate from these reduced source-shaped unit fixtures.

| Work | Replay / latest live AED new-listing quote |
|---|---:|
| Capital in the Twenty-First Century (hardcover) | 176.98 |
| The 4-Hour Workweek | null: Arabic byline is not independently corroborated to the expected English author by this adapter |
| The Snowball | null: unsupported retail format/uncertain evidence; latest live retail request returned HTTP508; used offer stays excluded |
| How to Talk to Anyone (paperback) | 41.57 |
| Think and Grow Rich (Fingerprint! paperback) | 10.38 |

Full preserved-page replay with the final bundle guard: **3/5 new quotes, 0/5 used**, captured in `adapter-replay-result-v2.json`. Fresh live run at **18:21 UTC**: **3/5 new quotes, 0/5 used**, **5 HTTP200 / 6 fetches**, one retail-source HTTP508; `adapter-live-result-v2.json` preserves URLs, timestamps, raw primary-field evidence and failures. Fetch/validation processing summed to **12.028 seconds**, median **2,393.938 ms**, still excluding discovery. Frozen comparison/limitations are in `adapter-summary.json`. Older baseline, manual observations and first-pass replay/live artifacts remain untouched; the few-cent differences between earlier rendered observations and later primary offer fields are retained as separate observations, not forced into one price.

This is **partial source integration**, not completed automatic valuation. URLs are still seeded; only supported `/dp/` source shapes and Latin/AED price formatting are tested; unclosed/duplicate fields abstain; unverified author translations, used-market discovery, foreign/FX fallback and edition-equivalence checks remain unresolved. Listing IDs/formats are not proof of the owned editions. No new dependencies, credentials, model calls, Context.dev accounts or Groq integration were added. Groq remains a possible later semantic-identification aid, never monetary authority.

## Real-shelf spine baseline — owner-approved 14-book row

The owner supplied a 1024×768 photo and independently counted **14 upright books**, then explicitly confirmed that the cropped horizontal stack at the far left is excluded. Manual left-to-right boxes 01–14 were rendered and approved by the owner **before** running the unchanged detector. Evaluation uses a manually selected ROI `(x=150,y=25,w=820,h=485)`; this is a bounded development test, not whole-frame/whole-room coverage or automatic shelf-region discovery. Approximate axis-aligned truth boxes enclose the leaning spines.

Artifacts: `runs/preflight-spines-2026-10-06-row14/` contains the preserved original, working image, `row14-annotated.png`, `row14-crop.jpg`, `draft-annotation.json` (status owner-approved), `probe-input.json`, `baseline-result.json` and `baseline-result-overlays/image_000.jpg`.

```bash
.venv/bin/python -m preflight.probe_spines \
  --input runs/preflight-spines-2026-10-06-row14/probe-input.json \
  --output runs/preflight-spines-2026-10-06-row14/baseline-result.json
```

| Metric | Result |
|---|---:|
| Owner-labelled physical books | 14 |
| Candidate boxes | 10 |
| Matched spines (one-to-one IoU ≥0.5) | 0 |
| False positives / missed spines | 10 / 14 |
| Candidate precision / recall | 0% / 0% |
| Count error | 28.57% |
| Detector execution time | 1.57 ms (one invocation, not a latency benchmark) |

**Result: FIELD FAIL for this upright-row baseline.** The reviewed overlay shows boxes around printed lettering/fragments rather than whole book spines. This directly demonstrates that passing synthetic-rectangle tests did not establish real-spine detection; even the apparent count of ten is not ten correctly detected books. The current Canny/closed-contour/aspect-filter pipeline has no semantic whole-spine validation. No thresholds were changed, detector output was not used to author truth, and no title-identification or metric-size claim follows from this test. Further diagnosis/intervention must retain this failed baseline and test representative additional scenes rather than optimize only this crop. Horizontal/stacked, blur/glare cases, temporal counting and the official final-sweep count bar remain NOT MEASURED. This failure does not block A4, pricing, voice or packet work.

## Boundary-based detector experiment — 2026-10-07

A second local-only method is available through `detect_spines(image, method="boundaries")` and CLI `--detector boundaries`; default remains the original `contours`. It uses long edge segments, clusters nearby parallel boundaries, pairs neighbouring boundaries, and checks both upright/rotated orientations. Parameters were fixed before real-photo evaluation; the owner-approved labels and ROI were unchanged. No expected-count input, room-specific constants, additional dependencies or model calls were introduced.

```bash
.venv/bin/python -m preflight.probe_spines \
  --detector boundaries \
  --input runs/preflight-spines-2026-10-06-row14/probe-input.json \
  --output runs/preflight-spines-2026-10-06-row14/boundaries-result.json
```

| Metric | Original contours | Boundaries |
|---|---:|---:|
| Labelled books | 14 | 14 |
| Candidates | 10 | 23 |
| One-to-one matches at IoU ≥0.5 | 0 | 10 |
| False positives / misses | 10 / 14 | 13 / 4 |
| Precision | 0% | 43.48% |
| Recall | 0% | 71.43% |
| Count error | 28.57% | 64.29% |
| Single-invocation detector time | 1.57 ms | 11.54 ms |

**Result: both FIELD FAIL as book counters.** Whole-spine matching improved, but over-segmentation worsened count error. The reviewed boundary overlay shows extra/overlapping proposals from internal spine edges; geometric edges alone do not establish separate physical books. Do not promote candidate count to inventory count or call this an accuracy pass. Freeze this attempted fix and stop parameter tuning on this row. Artifacts: `boundaries-result.json`, `boundaries-result-overlays/image_000.jpg` and `detector-comparison.json` under the same row14 run directory. Timings are individual invocations, not a benchmark. A second independently labelled scene, stacked/flat real books, blur/glare and temporal tracking remain unmeasured.

Local verification: the new touching-spine test first failed behaviorally with one merged candidate instead of three; after implementation, the full preflight gate reported 98 passing tests (one known Starlette warning) at `/tmp/spines-final-pytest.log`. Subsequent test-only review clipped lettering within each fixture spine and expanded invalid-method coverage; the focused spine/CLI gate then reported 18 passing tests at `/tmp/spines-test-rework.log`. Source/parameters did not change during that review; the earlier 98-test gate is historical rather than a claim of a fresh full-suite run after test refinement. Tests cover synthetic touching/textured, flat/tilted, text-only and blank inputs; passing fixtures does not override the real-shelf failures. Full cumulative diff: `/tmp/spines-boundaries-diff.diff`.

## First real model-backed observation — 2026-10-07, V1

Pinned `gpt-5.4-2026-03-05` observed the same unlabelled 820×485 crop through the product server. The API input contained the image and lead-authored schema/instructions, not the owner count, labels or filenames. The immutable result and overlay are `runs/preflight-spines-2026-10-06-row14/vision-result.json` and `vision-result.overlay.jpg`.

- Raw proposals: **13**, valid-box count error **7.14%** against 14; admitted at the previously declared 0.85 existence threshold: **11**, count error **21.43%**.
- Both raw/admitted box sets matched **0** truth boxes at the unchanged one-to-one IoU≥0.5 rule. The overlay truncates the book heights. This is FIELD FAIL, not an accepted counter or localization result.
- One client invocation: **23,905 ms**; handler observation time **23,886 ms**. Provider usage: **1,340 input, 0 cached input, 1,639 output tokens**. Standard documented-rate estimate **$0.027935**, not a reconciled invoice.
- Unsupported/incorrect raw text proposals are retained for audit, not authoritative identifications. This probe does not establish the title pass bar; source-side confidence is uncalibrated.
- Coordinate semantics are under investigation: the independently tested V1 mapper follows per-axis normalized units, while the returned boxes do not cover the physical spines. An explicit pixel-coordinate V2 with actual sent-image dimensions is being tested; old boxes are not reinterpreted post hoc to improve scores. Model, confidence thresholds, labels, ROI and evaluation rule remain fixed for that discriminating test.

The product capture/state foundation has reviewed local tests and persistent evidence; automatic model processing remains disabled until the bounded observation path is validated. None of this is an official compliant-room or iPhone-under-vision-load pass.

## Explicit pixel-coordinate observation — 2026-10-07, V2

The discriminating V2 request changed only coordinate convention/context: actual sent-image pixel width/height, pixel schema bounds, and pixel-to-original mapping. Same model/reasoning, 0.85 existence admission, ROI, frozen labels and IoU rule; no post-hoc correction of V1 outputs. Immutable artifacts: `runs/preflight-spines-2026-10-06-row14/vision-result-v2.json` and `vision-result-v2.overlay.jpg`.

- **15 raw/admitted proposals against 14**, count error **7.14%**; **7 matched**, **8 unmatched proposals**, **7 missed truth boxes**, precision **46.67%**, recall **50%**. Localization improved over V1 but this is still FIELD FAIL, not a reliable final counter.
- One client invocation **22,733 ms**; handler **22,718 ms**. Usage **1,435 input / 0 cached / 1,812 output tokens**; standard-rate estimate **$0.0307675**, not invoice reconciliation.
- The returned green-spine reading “THE 10X RULE — Grant Cardone” has 0.95 text confidence, while the source photo visibly reads Think and Grow Rich. This demonstrates that scene self-confidence alone cannot authorize identity/valuation. Other incorrect/unclear raw proposals remain untrusted.
- No third round of count/threshold tuning on this image. Continue the working flow with provisional tracks, explicit unresolved association/coverage, dedicated rotated-crop reading and independently sourced work corroboration before pricing. Readable-text evidence and a second real scene are still required for title/generalization claims. A4/room accuracy remains unmeasured.

## Reference execution review and next delivery decision

The reference is an integrated narrative-intake demo, not a metric contents inventory. Targeted code review found asynchronous tools, revision-aware cached workflow updates, immutable selected-frame verification and evidence ZIP export. Its browser sends roughly 1 FPS at 512-pixel width; the server is loopback-only and state/evidence are in memory. These are source observations, not measured runtime limitations. Proposed principles to keep: concurrent conversation/background work, visible state, evidence provenance and correction-safe updates. Replace the domain machinery with tracked contents, calibrated measurement, retrieved prices and deterministic claim validation. Discard policy checks, generated incident sketches and avatars from our scored scope. A runtime-based README comparison remains pending: owner confirmed Google/Gemini credentials unavailable, so the required original live run is BLOCKED, not completed or waived.

Delivery decision: stop isolated contour tuning and plan the simple end-to-end sweep → state/corrections → validated partial JSON/HTML flow, with the detector still an explicit unresolved seam. A4 remains BLOCKED awaiting same-plane image/tape truth, and independent additional shelf cases are unavailable. Neither geometry nor the reference credential blocker may block unrelated capture/state/packet work. Do not lock final detector/measurement implementations or claim full preflight completion. Unsupported measurements/prices stay null with review reasons; draft/partial output must not be described as a passed final claim.

## Image and pricing probe boundaries

Run the CLI probes as Python modules from the project root: `.venv/bin/python -m preflight.probe_prices`, `preflight.probe_spines`, or `preflight.probe_measure`, each with `--input` and `--output`. Outputs use exclusive creation to preserve prior evidence; image overlays are written beside the chosen result file. Actual input/result files belong under local `runs/`, not tracked docs. These commands were exercised end-to-end on synthetic temporary inputs by the CLI tests.

Pricing inputs are five `{title, author}` records with optional `replacement_url`, `used_url` and per-slot `replacement_uae_delivery_confirmed` / `used_uae_delivery_confirmed` assertions from listing inspection. The probe fetches supplied URLs, **not automated search**. For Amazon.ae it uses the guarded selected-offer HTML/embedded-JSON path described above; other hosts retain explicit JSON-LD title/author, in-stock AED offer and condition checks. Unsupported formats or ambiguous offers yield missing values. It does not verify edition equivalence automatically. Fetch-attempt status is not a pricing-integrity PASS, including when every URL fails.

Spine inputs reference `image_path` and independently annotated pixel `truth_boxes` (`x,y,width,height`); measurement inputs reference ordered `marker_quad`, `spine_quad`, `same_plane`, and positive `truth_height_cm` / `truth_thickness_cm`. Calibration relies on manually supplied corners and coplanarity, not automatic marker detection. The scripts are small prototypes: malformed annotation batches may stop with an input error, and fixed contour thresholds/merged candidates remain risks until tested on real shelves. Room fields remain null in the measurement output.

## Field metrics still missing

Safari: characterize the repeated-frame cause, verify intended higher-resolution/load behavior, and collect token usage for billing. Pricing: broaden supported identity/source coverage safely, implement discovery, retrieve eligible used quotes, test FX/second locale and measure 15-book comparable-price accuracy; baseline 0/5 and current 3/5 new-quote integration are both recorded, not conflated with accuracy. Detection: two detectors now fail as counters on the single labelled upright row (contours 10 candidates/0 matches, boundaries 23 candidates/10 matches); stop further parameter tuning on this photo; additional independent quality/orientation cases, semantic detection and tracking remain needed. Measurement: per-book predicted/tape truth height/thickness, sample coverage and mean percentage errors. Final-sweep count and scale accuracy remain unmeasured; failed extraction and unknown measurements are never presented as passing.

## Next input request

1. Preserve the original downloaded complete raw log/filename if available and the paired saved frames. Inspect the late preview-clock plateau offline before deciding whether a targeted retest is needed; the key remains local. The basic 120-second transport test need not be repeated before unrelated probes proceed.
2. Review the partial supported-source result and retained pricing gaps, then continue independent shelf/scale preflight; adding a second model/provider is not required to validate the captured numeric prices.
3. The 14-book upright row has owner-approved count/labels and two failed detector results (contours 0/14 matches; boundaries 10/14 matches with worse count error); no further single-photo parameter tuning. Provide additional clear/blurry/glare/flat-stack cases with independent labels where available; the cropped stack in this photo was explicitly outside the first test.
4. Supply one usable same-plane A4 image and tape measurements for at least five visible spines (aim for 20 for the final measurement bar).

Proceed with any independent probe whose inputs are available. Review measured outcomes before selecting the final detector, source adapters, or measurement implementation, then finalize the full-build implementation plan. The PDF links the required reference example online; a targeted source review (live server/client, tool contracts, workflow and regression tests) is now complete, but the live run is BLOCKED because the owner confirmed Google/Gemini credentials are unavailable, so no completed runtime-based kept/changed/discarded comparison is claimed. The ordering deviation is retained.

## Assignment audit — all 10 PDF pages, 2026-10-06

**Verdict: broadly aligned design, incomplete implementation and submission.** This audit compares the original PDF with `build-spec.md`, the approved design, the preflight plan, the actual `preflight/` modules and saved results. It is not a final evaluation. Page references below refer to the original assignment PDF, not the derived build spec.

Status vocabulary: **FIELD/PARTIAL** means bounded real-device or real-source evidence; **SYNTHETIC ONLY** means local fixtures, not accuracy evidence; **MISSING** means the product behavior/artifact is absent; **BLOCKED** means required inputs are unavailable; **NOT MEASURED** means no defensible result exists. A planned feature is not an implemented feature.

### Feature-by-feature coverage

| PDF requirement | Spec/design coverage | Actual state and remaining gap |
|---|---|---|
| p1: live voice-and-vision contents claim agent, one continuous sweep | Build §§0, 4; design intent/capture | FIELD/PARTIAL: voice, preview and uploads coexist; no integrated vision/inventory/claim agent. |
| p1: read and run linked reference before any code | Build §40 weakens timing; design §Test makes availability conditional | DEVIATION: preflight code already exists without a reference run. Targeted source review completed (live server/client, tool contracts, workflow and regression tests); live run BLOCKED — owner has no Google/Gemini credentials, so no runtime-based kept/changed/discarded comparison exists. Ordering deviation retained. |
| p2: greet, confirm country/currency, explain sweep in 1–2 sentences | Build §3; design intent/pricing, but not explicit onboarding steps | MISSING: probe instructions are for concurrency testing, not this onboarding flow. |
| p2: one pass across every shelf, wall and floor, target <3 min | Build §4; design capture | NOT MEASURED: 171.462 s transport trial is not proof of complete room coverage. |
| p2–3: real-time speech in/out with camera on; user interruption | Build §§3–4, 27; design capture | FIELD/PARTIAL: iPhone trial and three owner interruption markers support transport/usability at 640×480; integrated vision load remains untested. |
| p2–3: say what is being logged and actively direct capture | Build §§4, 9; design capture | MISSING: voice probe explicitly must not claim it sees, counts, measures or prices. |
| p3: notice blur, glare, occlusion and missed shelves in time to recover | Build §§4, 9; design quality/coverage | MISSING: no quality-to-guidance pipeline. Occlusion must be explicit, not treated as equivalent to blur or omitted. |
| p2–3: live on-screen inventory, not only final results | Build §26; design capture | MISSING: probe displays test telemetry, not persistent book/item inventory. |
| p2–3: voice corrections change state, including edition, skip and ownership | Build §§4, 27; design typed/validated intents | MISSING: no correction-to-inventory seam. Unsupported edition assertions must not become verified editions/prices. |
| p2, 4: short question for original artwork versus print | Build §22; design non-books | MISSING: no object-aware question or appraisal state. |
| p2: background completion of identification, pricing and measurement | Build §28; design processing | MISSING: standalone probes are not integrated post-sweep processing. |
| p2: spoken summary and packet within 5 minutes of sweep end | Build §§28–29 cover packet; design intent covers receipt | MISSING: spoken summary readback must be explicitly retained alongside packet delivery. |
| p2: targeted re-aim within continuous pass allowed; no barcode/ISBN/removal/item-by-item capture | Build §4 and prohibitions; design capture | Planned correctly; integrated user flow NOT MEASURED. Manual annotation/tape truth are evaluation work, not an acceptable runtime dependency. |
| p3: count every visible spine, including flat and stacked books | Build §§10–11; design book processing | FIELD FAIL: contours 10 candidates/0 matches; boundaries 23 candidates/10 matches on the same 14-book crop. Both fail counting; real stacked/horizontal cases remain unmeasured. |
| p3: read title, author, publisher where visible; resolve work and supported edition | Build §13; design book processing omits explicit publisher | MISSING: no runtime identification. Keep publisher as an added field; neither missing publisher nor listing metadata justifies guessing owned editions/ISBNs. |
| p3: persistent physical inventory across frames | Build §§10–12; design tracking | MISSING: candidate-box matching against truth is not temporal tracking/deduplication. |
| p3: spine height/thickness in cm and total shelf run in m | Build §15; design scale | SYNTHETIC ONLY: manual-quad homography exists; real tape errors, automatic corners and shelf-run aggregation are absent. |
| p3: explain metric scale, not model-estimated size | Build §15; design A4 calibration | Documented same-plane 21×29.7 cm A4 method; field feasibility NOT MEASURED. One marker plane cannot establish the room's 3D geometry. |
| p3: unreadable books remain unidentified, with dimensions rather than guessed title | Build §§13–14, 30; design unknowns | Planned; product enforcement MISSING. Unsupported dimensions stay null/review, which is honest but not a measurement pass. |
| p3: local country/currency prices for identified books | Build §§16–18; design pricing | FIELD/PARTIAL: seeded UAE pages yield 3/5 new-listing quotes, not owned-book valuations. Automatic discovery and arbitrary input identities remain absent. |
| p3: replacement and used value independently per book | Build §16; design pricing | FIELD/PARTIAL: 3/5 replacement-source quotes, 0/5 eligible used quotes; no inferred depreciation/discount is allowed. |
| p3: source, retrieval date and assumed condition on every price | Build §16; design pricing | Partial probe records exist; final schema/validation absent. Preserve new condition as well as used condition assumption. |
| p3: foreign-market conversion marked converted; no defensible price excluded | Build §17; design pricing | Missing-price abstention exists in probe; foreign/dated FX evidence, exclusions and claim totals MISSING. |
| p3: rare/signed/antiquarian/high-value appraisal, no auto-price | Build §19; design sets AED1,000-equivalent routing threshold | MISSING: no appraisal routing/suppression in integrated claims; threshold is a design decision, not an estimated item value. |
| p3–4: same 10 books priced for second country | Build §18; design chooses US/USD | NOT MEASURED: no second locale or locale-parameterized product pricing. US is our choice, not mandated by PDF. |
| p4: all non-books detected/classified, including shelving/furniture/coffee machine/lamps/art/rugs/electronics/decor | Build §§20–22; design non-books | MISSING: no non-book detector/tracker. Final room needs ≥8 items, not necessarily eight distinct categories. |
| p4: visible category/material/dimensions/legible brand-model | Build §20; design non-books | MISSING. Material needs an added output field; unknown brand/model stays empty and unsupported dimensions null. |
| p4: sourced local replacement or sourced range for unknown product | Build §21; design pricing | MISSING: no non-book retrieval or range validation; do not invent SKUs. |
| p4: art/portraits default appraisal unless user establishes print | Build §22; design non-books | MISSING: no runtime rule, user-assertion provenance or automatic-price suppression. |
| p4: floor/total wall area in m² AND ft² | Build §23; design room geometry | NOT MEASURED: room probe outputs null. Add ft² fields even though the illustrative JSON shows m² only. |
| p4: length/width/ceiling height; explain non-rectangular handling | Build §§23–24; design polygon/segments or unresolved | MISSING: no measured room reconstruction; unsupported shape/dimensions remain null with reasons. |
| p4: shelved wall area separately; geometry from same sweep | Build §§23–25; design scale | NOT MEASURED: no shelf footprints or same-sweep room result. Tape ground truth is not runtime measurement input. |
| p4, 6: JSON and readable report built from it per sweep | Build §§29, 44; design packet | MISSING: no `claim_packet.json`, report renderer or end-to-end packet pipeline. |
| p4, 6–7: review every uncertain line, with reason | Build §31; design validation | MISSING: probe reason fields are not the claim review queue. |
| p4, 7: totals computed in code, trace figure to frame/source | Build §§30–33; design validation | PARTIAL frame persistence/source snapshots only; final evidence resolution, Decimal aggregation and reconciliation MISSING. |

### Output contract audit (PDF pp6–7)

Preserve all required keys below. Unknown numeric values are null, not illustrative zeros; unknown identification strings remain empty. Extra provenance fields are permitted and needed.

- `sweep`: `id`, `captured_at`, `device`, `duration_s`, `country`, `currency`.
- `room`: `length_m`, `width_m`, `height_m`, `floor_area_m2`, `wall_area_m2`, `shelved_wall_area_m2`, `scale_method`, `confidence`.
- Each `books[]` line: `id`, `shelf`, `position`, `frame_ref`, `status` (`identified`, `unidentified`, `needs_appraisal`), `title`, `author`, `edition`, `isbn`, `spine_height_cm`, `spine_thickness_cm`, `id_confidence`; `replacement_cost` with `amount`, `source`, `url`, `retrieved_at`, `converted`; `used_value` with `amount`, `source`, `url`, `retrieved_at`, `condition_assumed`.
- Each `items[]` line: `id`, `category`, `description`, `brand_model`, `frame_ref`, `dimensions_cm` (`w`, `h`, `d`), `status` (`priced`, `range`, `needs_appraisal`), `replacement_cost` (`low`, `high`, `source`, `url`, `retrieved_at`), `confidence`.
- `totals`: `book_count`, `books_identified`, `books_unidentified`, `shelf_run_m`, `books_replacement_cost`, `books_used_value`, `items_replacement_cost_low`, `items_replacement_cost_high`, `excluded_from_totals`.
- `review_queue[]`: `ref_id`, `reason`.
- Add publisher, material, ft² areas, room-shape handling, per-price currency/condition, exact/equivalent and FX evidence, exclusion/correction provenance, measurement evidence, timing and cost without dropping required keys.
- Every `frame_ref` must open saved evidence. Report is rendered from the same validated JSON. A filled unsupported field is a defect. No model-authored monetary totals. No implemented product contract/validator/report currently exists.

### Official pass bars (PDF p5)

| Measure | Required bar | Current evidence |
|---|---|---|
| Book count | ≤5% error against true count | NOT MEASURED on a real sweep. Synthetic candidates do not establish it. |
| Correct titles | ≥70% of human-legible spines | NOT MEASURED; no complete hand-read truth or identification pipeline. |
| Confidently wrong titles | ≤3% | NOT MEASURED; confidence rule/denominator must be declared before tuning. |
| Spine dimensions | Within 15%, hand-measured sample of 20 | BLOCKED / NOT MEASURED; no A4/tape pairs. Five-book preflight cannot establish the 20-book bar. |
| Book prices | Within 25%, hand-checked sample of 15, same source type | NOT MEASURED; 3/5 extraction coverage is not 15-book price accuracy. |
| Non-book items | ≥80% of items found with correct category | NOT MEASURED; denominator is ground-truth items, not distinct categories. |
| Floor area | ≤10% error against tape area | NOT MEASURED; null means unavailable, not pass. |
| Wall area | ≤15% error against tape area | NOT MEASURED; null means unavailable, not pass. |
| Packet time | <300 s after sweep ends | NOT MEASURED; no packet workflow. |

The PDF does not fully define aggregate dimension/price error or the confidently-wrong denominator. The approved design uses mean percentage error for dimensions/prices; the preflight reports height and thickness separately on measurable pairs. Before final evaluation, document the exact rules and sample selection, show coverage/missing outputs, and never silently remove hard cases. No official pass bar is currently established by these preflights.

### Submission audit (PDF pp7–10)

| Mandatory deliverable | Required contents | Current state |
|---|---|---|
| 1. Private GitHub repository | Shared with evaluators; README setup <15 min; `.env.example`; no secrets | MISSING. No Git repository, root README or `.env.example`. Git initialization/publication remains a separate owner decision. Setup time has not been measured. |
| 2. Demo | One unedited ≤6-minute take; entire sweep, audible agent, live inventory, final packet | MISSING. Device telemetry is not a submission video. Capture <3 min plus packet <5 min does not itself guarantee a ≤6-min demo; actual combined flow must fit. |
| 3. Claim packet | JSON, PDF/HTML from video sweep, all referenced frames | MISSING. Preflight JPEGs are not a matching final packet/evidence set. |
| 4. Ground truth and results | True count; titles for every legible spine; 20 measured spines; 15 checked prices; non-book list; tape room; predictions beside each pass bar | MISSING. Five source observations are not the complete dataset. Build truth before detector/identification/geometry tuning. |
| 5. Architecture note | One page; diagram, model ownership, metric scale, price sources | DRAFT exists at `docs/architecture.md`; describes intended architecture. Must reflect actual final implementation and check one-page presentation. |
| 6. Failure log | One page; final run's three worst errors, root causes, changes; sweep cost and stage latency | MISSING. Preflight failures/improvements are evidence, not final-run failure log. At least one measured fix is rewarded by p9 rubric. |

Additional submission/evaluation obligations:

- **Real space:** ≥60 books across ≥2 shelving units, ≥8 non-book items, one enclosed room, captured by the author with permission where necessary. Small development shelves do not replace this capture.
- **Ground truth before tuning:** independently count/read/measure/check prices; retain full denominators. Current contour thresholds and synthetic tests are a baseline, not evidence of a ground-truth-calibrated detector. Do not tune on the real shelf before labels exist.
- **Model/library/tool disclosure:** README must name pretrained models, hosted providers, open-source libraries and AI coding tools actually used. Keep identification, pricing and measurement separately inspectable/testable.
- **Cost and latency:** report per sweep and per stage, including assumptions. The approximately $0.26 owner dashboard delta is not a reconciled sweep cost; upload RTT and seeded-page fetch time are not full stage timings.
- **Failures:** document what broke, attempted fixes, quantified outcomes and what two more weeks would change. The pricing baseline 0/5 → 3/5 is a measured preflight fix; the repeated-frame mechanism remains unproven and must not be presented as a known root cause.
- **Ambiguity:** state assumptions explicitly. No inferred edition, catalogue, price list, room dimensions or hardcoded scene coordinates; no unreadable-title guessing, memory prices, art/rare/signed auto-prices or cherry-picked evaluation. No barcode scans, typed ISBNs, book removal or item-by-item photography. No prompt-only fork, unnecessary polish/auth/deployment/policy database, or model training from scratch. Any fine-tuning would require justification. Author must be able to explain the submitted code line by line.
- **Review call:** 60 minutes, beginning with a 15-minute architecture walkthrough; unseen shelf/room evaluation against evaluator count/tape truth, then code/failure-log questions. Nothing may depend on the demo room's catalogue or dimensions.
- **Timing/logistics:** PDF says 48 hours from receipt, expecting 15–20 hours of work; late submissions have delay noted. Receipt/deadline is not verified here. Reply to original email with repository link, video link and four documents. Deliverables 3–6 are the natural document grouping; packet includes JSON, readable report and referenced evidence, so “four documents” must not be misread as permission to omit those files. Confirm attachment/link packaging if the evaluator needs a particular format.
- **Privacy/ownership:** no people or documents with personal data in capture; permission for others' spaces. Keep keys/private images local until a deliberately reviewed submission export. Code remains the author's, used for application evaluation.
- **Rubric:** measured accuracy 30%, pricing integrity 20%, live agent/one sweep 20%, engineering 15%, failure analysis 15%. Missing deliverables score zero for the part they support.
- **Stretch only:** floor plan, condition grading, multiple rooms, independent checker and smart glasses are optional and do not compensate for core gaps. Do not add them now.

### Discrepancies to carry into the full-build plan

1. **Reference prerequisite was weakened:** PDF p1/p7 requires read/run before any code; build §40 says before major implementation; design line about “only if supplied” is too conditional because the PDF contains a public link. Read/run the reference and record the late-run deviation honestly. Targeted source review is complete; the authorized live run remains BLOCKED because the owner confirmed Google/Gemini credentials are unavailable. Reference URL: `https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team`. Its inspected README requires Python 3.12 and a Google API key.
2. **Behavior details are thinner in the design:** onboarding country/currency confirmation, spoken end summary, visible publisher and live occlusion detection need explicit acceptance tests. All are in the PDF; this is clarification, not optional scope expansion.
3. **Narrative requirements exceed illustrative JSON:** add ft² areas, publisher, material, conditions and shape handling. The sample zeros are placeholders, not an instruction to replace unknowns with zero.
4. **Eight categories is not the official minimum:** build definition of done adds ≥8 supported categories; PDF requires ≥8 physical non-book items in the evaluation room and ≥80% correctly categorized item recall. Keep the official metric and label any additional category-coverage goal separately.
5. **Null fallback is not waived scoring:** failed room geometry must not block independent work, but floor/wall accuracy and same-sweep measurement remain required. Manual A4 corners in a probe do not establish automatic measurement during a live unseen-room run.
6. **Assumption/evidence boundaries:** define error aggregations, confident-ID denominator, missing-price/dimension coverage, source comparability, exact/equivalent editions and excluded shelf-run segments before tuning. User corrections update assertions/state, not unsupported facts or authoritative prices.
7. **Submission is not ready:** the one-page architecture draft and preflight report do not substitute for the six deliverables. Repository sharing, privacy-reviewed evidence export, README timing, final demo and matched results need actual verification.

### Prioritized remaining work

- [ ] **P0 — Prerequisites/evidence:** reference source review is complete; the authorized runtime remains BLOCKED on unavailable owner Google/Gemini credentials; preserve the missed ordering prerequisite. Arrange a compliant final room and collect ground truth before real-scene tuning. Do not initialize Git or request/publish secrets automatically.
- [ ] **P0 — Finish independent image preflights:** 14-book upright-row labels are recorded and both detectors fail as counters (contours 0/14 matches, boundaries 10/14 matches); stop further parameter tuning on this photo and collect additional representative quality/orientation cases with independent labels. Obtain same-plane A4 photo and at least five tape-measured height/thickness pairs (20 for final bar). Record failures/coverage; keep unrelated work unblocked.
- [ ] **P1 — Review evidence and settle full-build plan:** choose detector, corner/scale handling, tracking and supported pricing sources from measured results. Explicitly include the PDF details above; no optional infrastructure/stretch work.
- [ ] **P1 — End-to-end vertical slice:** validated sweep state, secure camera/voice, greeting/locale, incremental inventory, correction effects, persisted evidence, partial packet/report, spoken summary, cost/latency instrumentation. Then quality/coverage/occlusion guidance and counting/tracking/identification, non-books and defensible scale/room geometry.
- [ ] **P1 — Valuation integrity:** automatic identity-to-source discovery, replacement/used retrieval, exact/equivalent handling, local/FX fallback, same ten books in second locale, appraisal suppression, review routing, deterministic totals and resolving evidence links.
- [ ] **P2 — Final measured evaluation:** implement declared evaluator rules, measure all bars on the complete compliant sweep, quantify unknowns/misses and at least one attempted fix; preserve final evidence and actual timing/cost.
- [ ] **P2 — Submission packaging:** owner-approved private repository, verified <15-min README and `.env.example`, ≤6-min unedited demo, matching JSON/report/frames, full truth/results sheet, one-page actual architecture and three-error failure log; review privacy and email links/document packaging.

### Skill setup status

The ten Matt Pocock skill directories and provenance entries are present under `.devin/skills` and `skills-lock.json`: ask-matt, setup-matt-pocock-skills, writing-for-agents, grill-with-docs, grilling, domain-modeling, codebase-design, tdd, diagnosing-bugs and code-review. `docs/agents` links to `.devin/skill-references`, with tracker/domain references present. Earlier upstream integrity checks remain historical; no reinstall or changes to official skill content are required for this audit. Skill installation is tooling readiness, not assignment feature compliance.
