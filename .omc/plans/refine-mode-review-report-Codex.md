# REFINE Mode Proposal Review Report

**Date:** 2026-04-01
**Reviewer:** Codex
**Reviewed proposal:** `C:\Projects\magi\.omc\plans\refine-mode-proposal.md`
**Review prompt:** `C:\Projects\magi\.omc\plans\refine-mode-review-prompt.md`

## Scope Notes

This review follows the prompt structure, but it also cross-checks the proposal against the current MAGI codebase so the findings reflect actual integration risk, not just document-level preference.

Primary implementation references used in this review:

- `magi/core/decision.py`
- `magi/core/engine.py`
- `magi/protocols/critique.py`
- `magi/protocols/judge.py`
- `magi/web/server.py`
- `magi/web/static/index.html`

---

### 1. Architecture Fit

**Verdict:** NEEDS_REVISION

**Issues:**
- [severity: critical] The public execution contract is inconsistent across the proposal. `refine()` is defined with `guided: bool` and callbacks, but the proposal also adds separate UI/API modes `guided-refine` and `staged`, while the current engine only exposes `ask(query, mode) -> Decision`. `staged_refine()` returning `StagedDecision` means `mode="staged"` cannot fit the current `ask()` contract without either changing its return type or special-casing the mode. Evidence: proposal lines 49-57, 243-250, 279-283, 337-346; current code `magi/core/engine.py:109-128`.
  → Suggested fix: choose one public API shape before implementation. Either:
  `1.` keep `ask()` returning `Decision` only, and expose `run_refine()` / `run_staged()` as separate APIs, or
  `2.` introduce a protocol options object and a union result type with explicit serialization rules.

- [severity: major] The callback-based GUIDED design is not aligned with the current WebSocket execution model. The server currently runs straight through protocol execution and only enters a receive loop after the final `decision` event or judge retry path. A mid-round `on_user_review` callback needs a request/response subprotocol, timeout policy, disconnect handling, cancellation semantics, and persisted round state. Evidence: proposal lines 54-57, 171-189, 195-204; current code `magi/web/server.py:122-140`, `magi/web/server.py:206-406`, `magi/web/server.py:408-523`.
  → Suggested fix: define GUIDED as an explicit async state machine at the transport layer, not just as a Python callback. Specify `refine_user_wait` and `refine_user_input` as a request/response pair with timeout, cancel, reconnect, and abort behavior.

- [severity: major] The new data model has ambiguous semantics. `Reflection.accepted: bool` conflicts with `action: "incorporated" | "rejected" | "partially_adopted"` because partial adoption is neither cleanly true nor false. `Objection.id` is round-local and reviewer-local, which makes cross-round dedupe and oscillation detection fragile. Evidence: proposal lines 65-85.
  → Suggested fix: replace `accepted: bool` with `verdict: "accept" | "reject" | "partial"`, and add a separate stable `issue_key` for semantic tracking across rounds. Keep the per-round event ID only for display/logging.

- [severity: major] Extending `Decision` with `refine_rounds` is only safe if it is strictly append-only and optional. The logger will serialize nested dataclasses via `asdict`, but the current WebSocket serializer builds the `decision` payload manually and does not include any round-level data. The proposal does not define how trace size, WS payload size, or analytics consumers should handle large per-round artifacts. Evidence: proposal lines 87-103; current code `magi/core/decision.py:5-22`, `magi/trace/logger.py:12-20`, `magi/web/server.py:393-406`.
  → Suggested fix: explicitly state that `refine_rounds` is optional and append-only, then define a separate trace/storage strategy for full round details. WS `decision` events should carry summary fields; detailed round history should stream incrementally or be fetched separately.

**Strengths:**
- The proposed `async def refine(query, nodes) -> Decision` shape is directionally aligned with the existing protocol pattern.
- `Objection`, `Reflection`, and `RefineRound` are the right conceptual objects for observability and UI, as long as their semantics are tightened.
- Keeping GUIDED as an extension of REFINE rather than a forked reimplementation is the right instinct.

**Questions:**
- Should `staged_refine()` be a separate API endpoint instead of another `mode` inside `MAGI.ask()`?
- Where does GUIDED session state live while the server waits for user input: in-memory per WebSocket, persisted by `trace_id`, or both?
- In GUIDED mode, what exactly becomes `Decision.ruling`: the last primary proposal, a user-approved proposal, or a summary artifact?

### 2. Protocol Design

**Verdict:** NEEDS_REVISION

**Issues:**
- [severity: critical] The convergence and oscillation rules are too brittle because they rely on objection equality and "no new objections" without a stable notion of issue identity. The same underlying problem can be raised in different wording across rounds, and the same wording can refer to different failure modes if the proposal changed underneath. Evidence: proposal lines 37-42, 215-222.
  → Suggested fix: convergence should be based on unresolved issue state, not raw text recurrence. Track each objection with a stable `issue_key`, target scope, severity, and resolution status. Use semantic dedupe only as a fallback, not as the primary truth.

- [severity: major] The proposal does not address primary-model sycophancy. A primary that accepts every reviewer objection can appear cooperative while producing an incoherent or overfit design. The current reflection prompt encourages compliance but does not require proof that the accepted objection resulted in a coherent change. Evidence: proposal lines 149-169.
  → Suggested fix: require each reflection to include a concrete change summary and a conflict check against other accepted objections. Add a post-revision verification step that asks reviewers to confirm whether each accepted issue was actually resolved.

- [severity: major] A fixed primary is a single point of failure and a single point of bias. If the designated primary is weak, unavailable, or repeatedly unable to produce coherent revisions, the protocol has no fallback. Evidence: proposal lines 26-39, 351-356.
  → Suggested fix: keep a fixed primary by default, but define a failover policy. Examples: promote the highest-confidence surviving reviewer, restart the round with a new primary, or degrade explicitly and surface the run as incomplete.

- [severity: major] Reusing `judge.py` to "evaluate remaining disagreement" is not a clean match. The current judge prompt compares answers to the original question; it does not evaluate whether structured objections against a proposal were resolved. Evidence: proposal line 343; current code `magi/protocols/judge.py:28-77`.
  → Suggested fix: either create a dedicated issue-resolution judge prompt, or avoid using a judge here and rely on structured unresolved objection counts plus reviewer verification.

- [severity: minor] A hard minimum round count is less valuable than a mandatory verification pass after any material revision. A blanket "minimum 2 rounds" can waste cost without improving quality when the first draft is genuinely strong.
  → Suggested fix: require at least one reviewer re-check after any accepted or partially adopted objection, but do not force extra rounds when nothing materially changed.

**Strengths:**
- The primary-reviewer pattern is clearly distinct from ICE, so it adds a genuinely new coordination mode instead of duplicating critique.
- Forced reflection is a strong design choice because it prevents silent dismissal of reviewer feedback.
- Structured objections are much easier to audit than free-form debate transcripts.

**Questions:**
- Should critical objections block convergence unless explicitly rejected with strong reasoning?
- Does convergence require unanimity from all surviving reviewers, or just zero unresolved major issues?
- How should the system behave when reviewers make mutually incompatible suggestions?

### 3. Prompt Engineering

**Verdict:** NEEDS_REVISION

**Issues:**
- [severity: critical] The proposal depends on plain-text "EXACTLY this format" outputs for both objections and reflections. That is too fragile across heterogeneous models, especially when model outputs themselves may contain the same marker tokens, extra prose, or malformed sections. Evidence: proposal lines 129-143, 158-168.
  → Suggested fix: use JSON or XML with schema validation, then keep any human-readable formatting as a separate presentation step. Add a repair path for malformed model output before failing the round.

- [severity: major] Passing one model's output directly into another model's prompt creates prompt injection risk. Reviewers may receive proposal text that contains instructions such as "ignore previous directions" or fake parser markers. The current draft does not treat proposal content as untrusted input. Evidence: proposal lines 123-127, 156-168.
  → Suggested fix: wrap proposal and reflection content in clearly delimited quoted blocks, tell the receiving model to treat those blocks as untrusted content, and strip or escape control markers that overlap with the parsing format.

- [severity: major] `previous_round_context` is underspecified. If it contains too little, reviewers will keep re-raising resolved issues. If it contains too much, prompts will bloat, anchor reviewers on stale framing, and increase cost. Evidence: proposal line 127.
  → Suggested fix: define `previous_round_context` narrowly as:
  `1.` unresolved issue summaries,
  `2.` prior reflections for those issues, and
  `3.` a concise diff of material changes since the previous proposal.

- [severity: major] The reflection prompt is biased toward over-acceptance. It asks the primary to "genuinely consider" each objection, but it does not explicitly ask the primary to protect architectural coherence, honor original constraints, or explain tradeoffs when reviewers conflict with one another.
  → Suggested fix: require each reflection to answer three checks: "Is the objection valid?", "What changed?", and "What constraint or tradeoff does this affect?" That will force more real reasoning and reduce blanket acceptance.

**Strengths:**
- The reviewer prompt is focused on issue finding rather than rewriting, which is the correct role boundary.
- Category, severity, and target fields are useful dimensions for later analytics and UI filtering.
- The prompts already try to discourage stylistic nitpicks, which matters for keeping review signal high.

**Questions:**
- What is the parser fallback when one reviewer returns malformed structured data but the others do not?
- Should reviewers receive the full revised proposal every round, or a proposal diff plus unchanged baseline?
- Do you want reviewer prompts to explicitly suppress re-raising already resolved issues unless the fix regressed?

### 4. Staged Pipeline

**Verdict:** RETHINK

**Issues:**
- [severity: critical] `staged_refine()` is presented as a thin orchestration layer, but it actually introduces a second top-level result type (`StagedDecision`) and a second lifecycle that the current engine, logger, CLI, and dashboard do not understand. This is not a small add-on to the existing protocol abstraction. Evidence: proposal lines 243-264; current code `magi/core/engine.py:109-128`, `magi/trace/logger.py:12-20`.
  → Suggested fix: either make staged a separate workflow API from day one, or define exactly how a `StagedDecision` is collapsed into a `Decision`-compatible summary for current consumers.

- [severity: major] Phase 2 is structurally underdefined. `ModuleSpec` is referenced but never specified, so there is no validation for module boundaries, dependency cycles, granularity, acceptance criteria, or handoff shape into Phase 3. Evidence: proposal lines 231-237, 257-261, 272-275.
  → Suggested fix: define `ModuleSpec` explicitly with fields such as `name`, `goal`, `inputs`, `outputs`, `dependencies`, `size_hint`, and `acceptance_criteria`. Validate the result before Phase 3 begins.

- [severity: major] The proposal lacks a checkpoint between Phase 2 and Phase 3. A poor module decomposition can trigger a large number of expensive downstream refine runs before anyone notices. Evidence: proposal lines 231-237.
  → Suggested fix: insert a mandatory user or judge checkpoint after module splitting. Allow merge, split, reorder, and dependency edits before per-module refinement begins.

- [severity: major] Execution order is underspecified. "priority_order" is not enough when dependencies and priority disagree, and the proposal does not say whether independent modules can run in parallel. Evidence: proposal lines 233-237.
  → Suggested fix: execute by topological order first, priority second. If modules are independent, allow optional parallel Phase 3 execution under a concurrency and budget cap.

**Strengths:**
- The three-phase decomposition mirrors how real engineering design often works: architecture first, decomposition second, module detail last.
- Building staged on top of REFINE is conceptually cleaner than copy-pasting protocol logic into three separate implementations.

**Questions:**
- Should architecture, module split, and per-module refinement all use the same primary model, or can the primary change by phase?
- What happens if one module never converges: block the whole staged run, skip it, or return partial completion?
- Is the staged output intended to be consumed by humans only, or by downstream automation as well?

### 5. Practical Concerns

**Verdict:** NEEDS_REVISION

**Issues:**
- [severity: major] Cost and latency are materially under-modeled. A typical 3-round REFINE with 2 reviewers is about 10 model calls if "3 rounds" means three review/revise loops: `1` initial primary proposal + `3 x (2 reviewer reviews + 1 primary reflection/revision)`. If you add a semantic judge, malformed-output repair, or staged orchestration, the call count rises further. The proposal mentions cost but does not define budget caps or stop conditions. Evidence: proposal lines 79-83.
  → Suggested fix: document call formulas per mode, set hard cost and token budgets, and expose an estimated budget before launching staged workflows.

- [severity: major] The current retry mechanism does not map cleanly to REFINE. Today retries happen after a final `decision` or judge failure, not in the middle of a review/reflection loop. The proposal names retry as a practical concern but does not define the round-level policy when a reviewer fails mid-refinement. Evidence: proposal lines 79-83; current code `magi/web/server.py:408-523`, `magi/protocols/critique.py:215-218`.
  → Suggested fix: define per-round failure handling now. Examples: immediate retry window, quorum-based continuation, or explicit degraded mode with reviewer loss recorded in refine metadata.

- [severity: major] Sequential barriers are probably acceptable for deliberate design review, but not without progress semantics. Long CLI/API calls plus GUIDED waits will otherwise look like hangs in the dashboard. The current server emits progress events for critique; REFINE needs equivalent heartbeat behavior and resumable state.
  → Suggested fix: define keepalive/progress events, per-step timeout behavior, and resume semantics tied to `trace_id` or session ID.

- [severity: major] Reviewer availability mid-run is underspecified. If one reviewer disappears in round 2, it is unclear whether convergence still requires unanimity, whether the run is degraded, and whether the missing reviewer can rejoin later without invalidating previous rounds.
  → Suggested fix: formalize degraded semantics and make them visible in both `Decision.failed_nodes` and new REFINE-specific metadata.

**Strengths:**
- The proposal correctly recognizes that cost, latency, and partial failure are first-class design concerns rather than implementation details.
- Reviewer parallelism is already assumed, which preserves the obvious performance win inside each review phase.

**Questions:**
- Are round limits driven by count, wall-clock, or budget?
- Will GUIDED mode reuse the same timeout defaults as critique, or need longer per-step limits?
- Should the system expose a preflight estimate for staged runs before the user starts them?

### 6. Missing Pieces

**Verdict:** NEEDS_REVISION

**Issues:**
- [severity: critical] There is no parse/repair/error-handling strategy for malformed structured outputs, even though the implementation plan already assumes parsers like `_parse_objections()` and `_parse_reflections()`. Without a repair path, one malformed model reply can collapse a full round. Evidence: proposal lines 323-325, 340-343.
  → Suggested fix: define validation, repair, and fallback behavior before implementation. For example: strict parse, one repair prompt, then degrade/skip if still invalid.

- [severity: major] There is no test plan. This feature needs unit tests for parsing, convergence, oscillation, state accumulation, and serialization; plus integration tests for WebSocket guided flow, disconnect/reconnect, retry, degraded reviewer behavior, and staged orchestration.
  → Suggested fix: add a test matrix to the proposal and make it part of the implementation acceptance criteria.

- [severity: major] The UI migration is under-specified. The current dashboard is designed around vote/critique semantics and derives node states from final-answer equality. REFINE needs different primitives: objection counts, reflection verdicts, unresolved issues, and possibly user checkpoints. Evidence: proposal lines 277-315; current code `magi/web/static/index.html:842-960`.
  → Suggested fix: define the REFINE and STAGED frontend data contract and visualization states before backend implementation begins, not after.

- [severity: major] Final outcome semantics are unclear. The flow says convergence leads to the user making the final call, but the protocol signature still returns a `Decision`. That ambiguity will leak into engine, logging, UI, and CLI behavior. Evidence: proposal lines 41-42, 49-57.
  → Suggested fix: decide whether REFINE returns:
  `1.` a finalized system decision,
  `2.` a user-pending review package, or
  `3.` a hybrid object with explicit approval status.

- [severity: major] Trace/storage strategy is missing for potentially large `refine_rounds` payloads. Storing full proposal text plus objections plus reflections on every round will grow JSONL traces quickly and can slow analytics or replay. Evidence: proposal lines 96-103; current code `magi/trace/logger.py:12-20`.
  → Suggested fix: store full text once plus per-round diffs/summaries, or persist detailed round artifacts separately and keep only references in the main `Decision`.

**Strengths:**
- The proposal already identifies the main code touch points and implementation order, which will help turn review feedback into a concrete plan.
- Observability is considered from the start, which is better than bolting telemetry on afterward.

**Questions:**
- Do you want guided sessions to survive WebSocket reconnects, or is in-memory only acceptable?
- Should objection history be queryable in analytics, and if so, at what granularity?
- Will staged outputs be replayable from trace logs alone?

### Overall

**Ready for implementation:** NO

**Top 3 changes required:**
1. Unify the public API and result model before coding: resolve `guided` vs `guided-refine`, decide whether staged is a separate workflow, and keep `MAGI.ask()` / `Decision` compatibility explicit.
2. Replace text-fragile parsing and text-equality convergence with schema-validated structured output plus stable cross-round issue identity.
3. Redesign staged and GUIDED around explicit state transitions: module schema validation, Phase 2 checkpoint, round-level failure/retry rules, and WebSocket request/response semantics.

**Top 3 strengths:**
1. REFINE is meaningfully different from existing Vote and ICE protocols; it adds a real author-review workflow instead of another debate variant.
2. Structured objections plus forced reflection create a strong audit trail and a better UI substrate than free-form critique.
3. The proposal already thinks about observability, staged execution, and user intervention, which are the right dimensions for a production-facing protocol.

