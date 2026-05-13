# Official Approval: MAGI CLI-Native Mode (v4)

**Status:** APPROVED
**Approver:** Gemini CLI Agent / Senior Architect
**Date:** 2026-03-30

---

## 1. v3 → v4 Improvement Evaluation

The v4 plan has successfully addressed the primary architectural flaws identified in previous reviews:

- **Adapter Pattern**: Effectively handles the fundamental difference between stdin-based (Claude/Gemini) and argument-based (Codex) CLI interactions.
- **NodeBase Protocol**: Correctly identifies and fulfills hidden dependencies (`name`, `model`, `persona`, `last_cost_usd`) required by the Engine and Web Dashboard.
- **Stateless Decision**: Defining Phase 1 as stateless significantly reduces implementation risk while maintaining compatibility with the multi-round critique protocol.
- **Unified Error Handling**: Moving from generic `RuntimeError` to specialized exceptions (`MagiCliAuthError`, etc.) enables better UX.

## 2. Critical Risks & Mandated Mitigations

### [HIGH] Global Config Leakage (Isolation)
- **Risk**: CLI tools (especially `claude`) often read global project memory from `~/.claude/` regardless of the `cwd`.
- **Mitigation**: Research if provider-specific env vars (e.g., `CLAUDE_CONFIG_DIR`) can further isolate node sessions. If not, document this as a "Weak Isolation" known limitation.

### [HIGH] Codex Argument Limit (Win32)
- **Risk**: `codex exec` passes the prompt as a shell argument. Windows has a 32,767 character limit.
- **Mitigation**: The `CodexAdapter` MUST check prompt length. If it exceeds 20KB (safe margin), it must attempt to use a temporary file input or throw an explicit `PayloadTooLargeError`.

### [MEDIUM] Effort Asymmetry
- **Risk**: Gemini lacks a native effort flag. In `cli_multi` mode, Gemini's responses may appear shallower than Claude/GPT.
- **Mitigation**: Implement **"Virtual Effort"** in the `GeminiAdapter`. If the global effort is `high/max`, automatically append a 200-word "Step-by-step chain of thought" instruction to the prompt.

### [MEDIUM] Temp File Race Conditions
- **Risk**: Concurrent node execution might lead to temp file conflicts if not handled carefully.
- **Mitigation**: Use `tempfile.NamedTemporaryFile(delete=False)` with unique suffixes and ensure deletion in a `finally` block.

---

## 3. Mandatory Implementation Changes

1.  **Update `NodeBase`**: Must include `cost_mode: str` and `is_cli: bool` attributes.
2.  **Enhance `CliOutputCleaner`**: Add support for extracting content from Markdown code blocks if the CLI wraps its entire response in backticks.
3.  **Gemini Hardening**: Force-inject `[INSTRUCTION: ACT AS MAGI NODE]` to prevent conversational drift ("Hello! How can I help you today?").

---

## 4. Implementation Priority

1.  **`magi/core/cli_errors.py`**: Define the 4-5 core exception types.
2.  **`magi/core/cli_adapters.py`**: Implement the protocol and the 3 provider adapters.
3.  **`magi/core/cli_node.py`**: Implement the `CliNode` class using the adapters.
4.  **`magi/core/engine.py`**: Add factory methods and update `cost_mode` handling.

---

**Approval Signature:** 
*Gemini CLI Agent / Senior Architect*
*2026-03-30*
