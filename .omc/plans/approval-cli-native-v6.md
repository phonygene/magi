# Official Approval: MAGI CLI-Native Mode (v6)

**Status:** FINAL APPROVED (Technical Baseline)
**Approver:** Gemini CLI Agent / Senior Architect
**Date:** 2026-03-30

---

## 1. v5 → v6 Critical Evolution

The v6 plan has reached a peak level of engineering rigor by refining the specific flag semantics of each CLI provider:

- **Gemini Flag Correction**: Correcting `--prompt "text"` instead of the boolean assumption for `-p` is critical to prevent argument misinterpretation.
- **Codex Output Optimization**: Shifting from complex JSONL parsing to a direct file output (`-o <tmpfile>`) drastically increases the reliability of response extraction.
- **3-Layer Isolation Strategy**: Prioritizing documented CLI flags (`--bare`, `--ephemeral`, `--approval-mode plan`) over opportunistic environment variables ensures the highest possible node independence.
- **Win32 Robustness**: Correctly identifying `FileNotFoundError` as the primary detection mechanism for missing CLIs on Windows improves startup diagnostics.

## 2. Mandatory Verification Tasks (Phase 1A)

Before finalizing the Python implementation, the following manual verifications are REQUIRED:

1.  **Gemini Stdin Aggregation**: Verify if `gemini --prompt "" < stdin` correctly appends the stdin stream to the empty prompt flag without error.
2.  **Codex Output Purity**: Verify if `codex exec -o file` produces raw text or includes metadata (like "Assistant:"). Adjust the `CliOutputCleaner` accordingly.
3.  **Claude Bare-mode Scoping**: Confirm if `claude --bare` effectively ignores `CLAUDE.md` and local project memory when executed within a `tmpdir`.

## 3. Core Architecture Mandates

### [HIGH] Isolation Integrity
- **Requirement**: Use the 3-layer strategy: Documented Flags -> Isolated `cwd=tmpdir` -> Env variables.
- **Rationale**: This prevents "Context Contamination" where one node's previous actions or workspace knowledge affect another node's reasoning.

### [HIGH] Windows Error Detection
- **Requirement**: Implement `try-except FileNotFoundError` around `asyncio.create_subprocess_exec` as the primary method to detect missing CLI tools on Windows.

### [MEDIUM] Virtual Effort Naming & UX
- **Requirement**: Rename "Virtual Effort" to **"Prompt-level Depth Augmentation"** to clarify that it controls response strictness/style rather than internal model reasoning tokens (which Gemini CLI currently lacks control over).

---

## 4. Implementation Priorities

1.  **`magi/core/cli_errors.py`**: Define specialized exceptions with user-friendly installation/auth instructions.
2.  **`magi/core/cli_adapters.py`**: Implement the `CliAdapter` protocol and the 3 refined provider adapters (Claude, Codex, Gemini).
3.  **`magi/core/utils/cli.py`**: Implement the `CliOutputCleaner` with Markdown code block extraction.
4.  **`magi/web/static/index.html`**: Update the NERV Dashboard to handle `cost_mode="unavailable"` with a Tooltip explaining "Local CLI Source".

---

**Approval Signature:** 
*Gemini CLI Agent / Senior Architect*
*2026-03-30*
