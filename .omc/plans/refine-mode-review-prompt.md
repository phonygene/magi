# Cross-Model Review Prompt: MAGI REFINE Mode Proposal

Copy everything below the line and paste it to the reviewing model.

---

## Context

You are reviewing a design proposal for a new protocol mode called **REFINE** in the MAGI system — a structured disagreement engine where 3 LLMs answer the same question independently, then coordinate via protocols to produce a final decision.

### Existing Protocols (for reference)
- **Vote**: 3 models answer independently → majority wins (single round)
- **Critique (ICE)**: 3 models answer → multi-round error-detection debate → synthesis (NeurIPS 2025 ICE framing)
- **Adaptive**: Query all 3 → measure agreement → route to vote (high) / critique (mid) / escalate (low)

### Architecture Constraints
- Protocols are **async functions**, not classes: `async def protocol(query, nodes) -> Decision`
- Nodes expose `async query(prompt) -> str` (duck-typed: MagiNode for API, CliNode for subprocess)
- Dashboard streams events via WebSocket (JSON, one event per line)
- LLM-as-Judge (`judge.py`) scores agreement with fallback chain (OpenRouter API → Claude CLI → Gemini CLI → Codex CLI)

---

## The Proposal

REFINE introduces a **primary-reviewer** model where:
1. One model (primary) produces and iteratively refines a proposal
2. Other models (reviewers) examine each revision and raise structured objections
3. Primary model reflects on each objection (accept/reject/partially adopt with reasoning)
4. Loop continues until convergence (no new objections) or max rounds
5. **GUIDED REFINE** sub-mode adds user checkpoints between rounds
6. **Staged Pipeline** chains GUIDED REFINE (architecture) → REFINE (module splitting) → REFINE × N (per-module)

### Key Design Decisions
1. Primary-reviewer (not peer-to-peer like critique)
2. Structured objections with category/severity/target (not free text)
3. Forced reflection — primary must respond to every objection
4. Oscillation detection — force convergence if same objections repeat
5. GUIDED as a callback parameter, not a separate protocol
6. Staged builds on REFINE — orchestration layer, not reimplementation

---

## Full Proposal Document

(Paste the entire content of `refine-mode-proposal.md` here, or provide it as an attachment)

---

## Review Instructions

Please evaluate this proposal from the following perspectives. For each, provide specific, actionable feedback.

### 1. Architecture Fit
- Does REFINE integrate cleanly with the existing protocol pattern (`async def → Decision`)?
- Are the new dataclasses (`Objection`, `Reflection`, `RefineRound`) appropriately scoped?
- Does the `Decision` extension (adding `refine_rounds`) break backward compatibility?
- Is the callback-based GUIDED approach the right abstraction for WebSocket integration?

### 2. Protocol Design
- Is the primary-reviewer model the right choice vs. alternatives (e.g., rotating primary, consensus-building)?
- Are the convergence conditions sufficient? Missing any edge cases?
- Is oscillation detection (same objections repeating) robust enough? What about semantic equivalence of differently-worded objections?
- Should there be a minimum round count to prevent premature convergence?
- How should the system handle a primary model that accepts ALL objections blindly (sycophancy)?

### 3. Prompt Engineering
- Will the structured objection format (OBJECTION [category] [severity]) be reliably followed by different LLMs?
- Is the reflection prompt likely to produce genuine reasoning, or will models default to accepting everything?
- Should the reviewer prompt include the previous round's reflections to avoid re-raising resolved issues?
- Are there prompt injection risks in passing one model's output as another's input?

### 4. Staged Pipeline
- Is the 3-phase decomposition (architecture → module split → per-module refine) the right granularity?
- How should module dependencies affect execution order?
- What happens if Phase 2 (module splitting) produces an unreasonable decomposition?
- Should there be a user checkpoint between Phase 2 and Phase 3?

### 5. Practical Concerns
- Cost estimation: How many LLM calls does a typical 3-round REFINE with 2 reviewers require?
- Latency: Is sequential primary→reviewers→primary acceptable, or should rounds overlap?
- What happens when a reviewer model is unavailable mid-refinement?
- How does this interact with the existing retry mechanism (retry individual nodes)?

### 6. Missing Pieces
- What is NOT covered in this proposal that should be?
- Are there failure modes that aren't addressed?
- What would you change or add before implementation?

---

## Output Format

For each section (1-6), provide:

```
### [Section Name]

**Verdict:** APPROVE / NEEDS_REVISION / RETHINK

**Issues:**
- [severity: critical|major|minor] Description of issue
  → Suggested fix

**Strengths:**
- What works well in this section

**Questions:**
- Open questions that need answers before implementation
```

End with an overall assessment:

```
### Overall

**Ready for implementation:** YES / YES_WITH_CHANGES / NO

**Top 3 changes required:**
1. ...
2. ...
3. ...

**Top 3 strengths:**
1. ...
2. ...
3. ...
```
