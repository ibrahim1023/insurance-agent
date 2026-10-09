# Agent instructions

- Read `build-spec.md` for the scoring contract and `docs/superpowers/specs/2026-10-06-library-claim-agent-design.md` for approved design decisions before proposing product code.
- **Current status:** read `docs/readiness.md` when reporting capabilities; dated preflight and phase reports are historical evidence. The user confirmed a technical showcase first, consumer product later in `docs/product-direction.md`.
- **Recovery research:** for coverage/guidance, detector/OCR, catalogue/pricing or metric capture decisions, read the corresponding note in `docs/research/README.md` and the relevant work package in `docs/recovery-roadmap.md`. Those are proposals with measured stop gates, not installed models, completed features or permission to change provider/budget contracts. Keep assisted consumer results separate from automatic assignment metrics.
- **Preflight first:** when working on camera/voice, UAE prices, spine candidates, or A4 calibration, follow `docs/superpowers/plans/2026-10-06-preflight-experiments.md`. Report real measurements and blockers before locking in product implementations.
- Keep preflight probes independent. A failure in room geometry does not block other work; unsupported dimensions and prices are `null` with a review reason. Never convert unknown into zero or a model-generated estimate.
- Models propose identities and voice intents. Python validates corrections, evidence references, prices, measurements and deterministic totals; every priced claim needs a retrievable source URL and timestamp.
- Distinguish synthetic test results from iPhone Safari, real-shelf, tape-measured, and live UAE-listing results. Mark unrun checks `NOT MEASURED` and unavailable inputs `BLOCKED`; do not claim an accuracy pass without ground truth.
- Keep secrets, private images, and raw sweep files local. No Git repository has been initialized; do not claim commits or invent Git status. Only document install/test commands after verifying they run.
- **Verification:** the current preflight checks are `.venv/bin/python -m pytest tests/preflight -v` and `node --test tests/preflight/test_voice.mjs`. These are local tests, not real-device accuracy evidence. Read `docs/preflight-results.md` when reporting readiness or choosing final implementations.
- **Full checkpoint:** `.venv/bin/python -m pytest tests/backend tests/preflight -q` and `node --test tests/backend/*.mjs tests/preflight/test_voice.mjs` were verified on 9 October 2026 (508 Python / 120 JavaScript passed). Reuse unchanged evidence for documentation-only work; new code needs focused behavior checks and an appropriate final regression gate.

## Agent skills

Project skills are installed from `mattpocock/skills` under `.devin/skills`; `skills-lock.json` records installer provenance. Read the applicable installed `SKILL.md` before using a helper. Project constraints and approved specs/plans still govern the work; reuse completed artifacts and verification evidence instead of duplicating workflows.

### Which skill to use when

| Need or condition | Skill |
|---|---|
| User wants help choosing a skill/workflow | `/ask-matt` (user-invoked) |
| User wants to configure or change tracker/domain-doc setup | `/setup-matt-pocock-skills` (user-invoked; not every task) |
| User wants an interview to refine a new/reopened design with domain docs | `/grill-with-docs` (user-invoked; calls `grilling` and `domain-modeling`) |
| Stress-test unresolved requirements/design decisions | `grilling`; skip renewed interviews for already approved scope |
| Define/change domain terminology, glossary entries or ADRs | `domain-modeling`; reading existing vocabulary alone does not require changing it |
| Design a module's interface/seam, testability or a source adapter | `codebase-design`; keep improvements tied to the current task |
| Implement a feature/bugfix test-first or add integration behavior | `tdd`; agree the public seams and exercise behavior through them |
| Diagnose broken/slow behavior or a difficult regression | `diagnosing-bugs`; establish a tight reproduction before fixes |
| Review changed code against standards and the originating spec | `code-review`; specify the change/fixed-point evidence |
| Create/edit agent instructions, skills or their reference docs | `writing-for-agents` |

The first three are intentionally user-only commands; the other helpers are available to the agent as well. Invoke applicable helpers, but share one implementation/test/review evidence trail across Matt and Superpowers rather than rerunning unchanged work. Git-dependent review steps require a real repository/fixed point; in the current no-Git preflight use explicit changed-file/evidence review and never invent SHAs or initialize Git just to satisfy a skill.

### Issue tracker

Local Markdown under `.scratch/<feature>/`; see `.devin/skill-references/issue-tracker.md`. “Publish” means write locally, not create hosted issues.

### Domain docs

Single context with lazy root `GLOSSARY.md` and `docs/adr/`; see `.devin/skill-references/domain.md`. No placeholder glossary, ADRs or tickets are required. `docs/agents/` links to the canonical `.devin` references for unmodified official skill consumers.
