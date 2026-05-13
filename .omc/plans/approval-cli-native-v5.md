# Official Approval: MAGI CLI-Native Mode (v5)

**Status:** FINAL APPROVED
**Approver:** Gemini CLI Agent / Senior Architect
**Date:** 2026-03-30

---

## 1. v4 → v5 Improvement Evaluation

The v5 plan has reached production-grade maturity by addressing the remaining edge cases and stability concerns:

- **Structured Output (JSON) as Primary**: Elevating JSON output to the primary path (with `CliOutputCleaner` as a fallback) significantly increases the reliability of the protocol parsing.
- **Codex Stdin Dual-mode**: The refined approach to handling long prompts (switching between arguments and stdin) is the most robust solution for Windows shell limitations.
- **Codex Isolation Fix**: Adding `--skip-git-repo-check` ensures the node can run within an isolated `tmpdir` without failing due to git metadata absence.
- **Schema & Downstream Impact**: The inclusion of `analytics.py` and `diff.py` in the impact analysis shows a comprehensive understanding of the system-wide changes required for "unavailable" cost tracking.

## 2. Final Implementation Mandates

### [HIGH] JSON Path Specificity
- **Requirement**: Each `CliAdapter` subclass MUST define the exact JSON path to extract the model's response.
- **Implementation**: Avoid generic "best-guess" parsing. Use specific keys: `.result` for Claude, `.candidates[0].content` for Gemini (based on final verification).

### [MEDIUM] Background Warmup
- **Requirement**: `GeminiAdapter.warmup()` MUST be executed as a background task during engine initialization.
- **Goal**: Ensure the user does not experience the 10-15s delay on the first query without blocking the main program flow.

### [MEDIUM] Virtual Effort Option
- **Requirement**: For providers lacking native effort control (Gemini), the prompt-level "Virtual Effort" (Chain-of-Thought injection) should be toggleable or at least well-documented as a quality/latency tradeoff.

---

## 3. Approved File Changes Summary

| Category | Target Files |
| :--- | :--- |
| **Core Protocols** | `magi/core/node.py` (NodeBase Protocol) |
| **CLI Implementation** | `magi/core/cli_node.py`, `magi/core/cli_adapters.py`, `magi/core/cli_errors.py` |
| **Engine & Data** | `magi/core/engine.py`, `magi/core/decision.py` |
| **CLI & Reports** | `magi/cli.py`, `magi/commands/analytics.py`, `magi/commands/diff.py` |

---

## 4. Conclusion

The architecture presented in **v5** is approved for immediate implementation. It balances performance, cost-efficiency (Zero-Key), and model diversity through a robust subprocess-based adapter pattern.

**Approver Signature:** 
*Gemini CLI Agent / Senior Architect*
*2026-03-30*
