# REFINE Mode V3r4r2 — Cross-Model Review Prompt

## Your Role

You are an expert architecture reviewer. Review the REFINE mode V3r4r2 amendment for the MAGI structured disagreement engine. Provide a verdict: `ACCEPT`, `ACCEPT-WITH-RESERVATIONS`, `REVISE`, or `REJECT`.

## Context

MAGI is a 3-LLM structured disagreement engine. REFINE is a 4th protocol: one primary model iteratively refines a proposal based on structured reviewer feedback.

### Review History (8 rounds)

| Round | Reviewer | Verdict | Key Issues |
|-------|----------|---------|------------|
| R1 | Internal (Claude) | REVISE | 3 Critical + 6 Major |
| R2 | Codex | REVISE | 3 Critical + 4 Major |
| R3 | Gemini | YES_WITH_CHANGES | 2 Major |
| R4 | Codex (V3) | REVISE | 3 Critical + 3 Major |
| R5 | Codex + Claude (V3r2) | REVISE / ACCEPT-WITH-RESERVATIONS | 2 Critical + 3 Major |
| R6-Claude | Claude (V3r3) | ACCEPT-WITH-RESERVATIONS | 0 Critical + 2 Major + 3 Minor |
| R6-Codex | Codex (V3r3) | REVISE | 1 Critical + 4 Major |
| R6-Gemini | Gemini (V3r3) | APPROVE (Final Sign-Off) | 1 Major |
| R7-Codex | Codex (V3r4) | REVISE | 2 Critical + 5 Major |
| R7-Gemini | Gemini (V3r4) | ACCEPT-WITH-RESERVATIONS | 1 Major + 2 Minor |

### V3r4r2 Scope

V3r4r2 integrates 5 additional fixes from the CCG (Claude-Codex-Gemini) review of V3r4:

1. **A1**: `auto_resolve_silent()` now resolves ALL active issues (open + partial_resolved), not just partial_resolved. Prevents infinite loop when primary rejects an issue and reviewer accepts by silence.
2. **A2**: GUIDED timeout default changed from `auto-approve` to `abort`. New `guided_timeout_policy: str = "abort"` config. User must explicitly opt-in to approve-on-timeout.
3. **A3**: Collator fallback normalizes raw objections into `ConsolidatedObjection` schema (single-entry suggestions list). Primary always sees one interface.
4. **A4**: Primary Reflection prompt fully updated with `suggestions` list, `conflicting_suggestions` handling, `consolidated_id`, and `source_issue_keys` echo.
5. **A5**: `collator_model=None` + CLI nodes → skip collation (CLI node.model is display string, not LiteLLM ID). Parse errors excluded from "silence" judgment in convergence.

## Review Instructions

Focus your review on:

1. **F1+A1 Correctness** — Does auto-resolving ALL active issues (open + partial_resolved) on reviewer silence fully eliminate false convergence? Is parse-error exclusion correct?
2. **F2+A3+A5 Completeness** — Is the Collator contract (ad-hoc call, CLI skip, normalized fallback) implementable without ambiguity?
3. **F3+A2 Safety** — Is timeout → abort the right default? Is the opt-in approve policy well-defined?
4. **F4 Consistency** — Does `PARSE_ERROR` vote value integrate with ALL existing UI comparison paths?
5. **F5+A4 Completeness** — Is the updated Primary Reflection prompt (suggestions list, conflicting_suggestions, consolidated_id) fully specified?
6. **Cross-finding Interactions** — Do all fixes (F1-F5 + A1-A5) work together without conflict?
7. **Test Sufficiency** — Are the 15 new tests (T1-T15) adequate? Any missing edge cases?

For each issue found, provide:
- **Severity**: critical / major / minor
- **Evidence**: quote the specific section
- **Suggestion**: concrete fix

## Files to Review

- **Amendment**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3r4-amendment.md` (primary review target)
- **Base proposal**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md` (for cross-referencing)

## Reference Files

- `C:\Projects\magi\magi\core\decision.py` — Decision dataclass
- `C:\Projects\magi\magi\core\engine.py` — MAGI.ask() method
- `C:\Projects\magi\magi\trace\logger.py` — TraceLogger
- `C:\Projects\magi\magi\web\static\index.html` — Dashboard verdict logic
