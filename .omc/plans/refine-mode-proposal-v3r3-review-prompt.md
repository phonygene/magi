# REFINE Mode V3r3 — Cross-Model Review Prompt

## Your Role

You are an expert architecture reviewer. Review the REFINE mode design proposal for the MAGI structured disagreement engine. Provide a verdict: `ACCEPT`, `ACCEPT-WITH-RESERVATIONS`, `REVISE`, or `REJECT`.

## Context

MAGI is a 3-LLM structured disagreement engine. Three models answer the same question independently, then coordinate via protocols (vote/critique/adaptive) to produce a Decision. REFINE is a new 4th protocol: one primary model iteratively refines a proposal based on structured reviewer feedback.

### Review History

This proposal has gone through **5 rounds of review** across multiple reviewers:

| Round | Reviewer | Verdict | Key Issues |
|-------|----------|---------|------------|
| R1 | Internal (Claude) | REVISE | 3 Critical + 6 Major (ask() signature, promote failover, Decision serialization) |
| R2 | Codex | REVISE | 3 Critical + 4 Major (issue_key collision, parse failure, trace compat) |
| R3 | Gemini | YES_WITH_CHANGES | 2 Major (sycophancy, context growth) |
| R4 | Codex (on V3) | REVISE | 3 Critical + 3 Major (IssueState schema, cross-round key, Decision.confidence/votes) |
| R5 | Codex + Claude (on V3r2) | REVISE/ACCEPT-WITH-RESERVATIONS | 2 Critical + 3 Major + 2 Minor (false convergence, premature close, dissent misjudge, reopen gap, UI verdict) |

All previous findings have been addressed. **V3r3 additionally includes major structural changes based on user workflow alignment:**

### V3r3 Structural Changes (new in this revision)

1. **New role: Collator** — A low-cost, stateless model that deduplicates and consolidates all reviewer suggestions before presenting them to the primary. Reduces primary's token consumption. Does NOT judge or filter.

2. **Removed: Arbitration/Judge mechanism** — The escalation system (rejected >= 2 triggers judge arbitration with sustain/override/compromise verdicts) has been completely removed. Rationale: disputes are resolved through natural iteration (reviewers may be convinced) or user override in GUIDED mode. The user can send unresolved disputes to other MAGI protocols (vote/critique) if needed.

3. **GUIDED mode promoted to P0** — Previously deferred to P1. Now a simple ON/OFF toggle (`RefineConfig.guided: bool`). When ON, the user reviews the primary's per-item decisions before the primary revises the proposal. User can approve, override specific decisions, or terminate.

4. **Simplified state machine: 6 states → 4** — Removed `escalated` and `wontfix` (no longer needed without arbitration). Remaining: `open`, `resolved`, `partial_resolved`, `reopened` (transient).

5. **Reviewer sees primary's decision reasoning** — Reviewer prompt now includes a `decisions_summary` section showing what the primary accepted/rejected and why. This lets reviewers judge whether to persist or accept the primary's reasoning.

6. **REFINE-specific UI verdict logic** — Dashboard no longer reuses vote-mode's `approveCount >= 2` rule. Instead uses `terminal_status` field with values: `converged`, `threshold`, `max_rounds`, `budget`, `cancelled`, `aborted`.

### V3r3 Bug Fixes (from Round 5 reviews)

7. `active_issues()` now includes `partial_resolved` — prevents false convergence
8. votes/minority_report determined by **last round's objection list**, not historical `distinct_reviewers`
9. `reconcile_cross_round()` queries open + partial_resolved + recently resolved (supports reopen)
10. `partial` verdict allows severity downgrade via `severity_after`
11. `canonicalize_key()` fallback for empty keys after normalization
12. `category` hard-match constraint documented as intentional design

## Review Instructions

Please evaluate the proposal on these dimensions:

1. **Architecture Fit** — Does it integrate correctly with MAGI's existing engine/Decision/TraceLogger/dashboard?
2. **Protocol Correctness** — Is the round flow (review → collate → decide → [GUIDED gate] → revise → re-review) sound? Are there deadlocks, infinite loops, or missing transitions?
3. **State Machine** — Is the 4-state model (open/resolved/partial_resolved/reopened) complete for the use cases?
4. **Collator Design** — Is the Collator role well-defined? Are there risks of information loss or bias?
5. **GUIDED Design** — Is the ON/OFF gate + UserAction (approve/override/terminate) sufficient?
6. **UI Contract** — Does the terminal_status + REFINE-specific rendering avoid misleading displays?
7. **Data Model** — Are all dataclasses serialization-safe? Are there missing fields?
8. **Test Coverage** — Are the 26 unit + 17 integration tests sufficient?

For each issue found, provide:
- **Severity**: critical / major / minor
- **Evidence**: quote the specific section/line
- **Suggestion**: concrete fix

## File to Review

```
C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md
```

Read this file in its entirety before starting your review.

## Reference Files (for cross-checking existing code contracts)

- `C:\Projects\magi\magi\core\decision.py` — Decision dataclass (12 existing fields)
- `C:\Projects\magi\magi\core\engine.py` — MAGI.ask() method, cost aggregation at line 131
- `C:\Projects\magi\magi\trace\logger.py` — TraceLogger.log() method
- `C:\Projects\magi\magi\web\static\index.html` — Dashboard verdict logic (line 930-996)
