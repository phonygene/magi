# Official Approval: MAGI CLI-Native Mode (v3)

**Review Date:** 2026-03-30
**Assigned Mode:** CLI-Native (Zero-Key Architecture)
**Approval Status:** **CONDITIONALLY APPROVED** (Passed with 3 Mandatory Fixes)

---

## 1. Executive Summary
The v3 plan for CLI-Native integration is architecturally sound and leverages the latest "Bring Your Own Subscription" (BYOS) trends of 2026. The dual-layer effort design (Native Flag + Prompt Injection) is a key differentiator that ensures debate quality while maintaining performance.

## 2. Mandatory Architectural Requirements

### R1: The "Balthasar" OpenAI Direct Route
*   **Requirement**: Do NOT rely solely on `codex exec` for OpenAI.
*   **Mitigation**: If `~/.opencode/auth.json` is present, the node MUST use a direct Python `httpx` call to `chatgpt.com/backend-api/codex/responses`. 
*   **Rationale**: This bypasses subprocess overhead, reduces latency by ~3s, and enables future streaming support.

### R2: The "Casper" Gemini Warmup Strategy
*   **Requirement**: Address the 15s MCP initialization delay.
*   **Mitigation**: The `GeminiCliNode` must implement an `async _warmup()` method triggered upon MAGI engine initialization. A dummy query `gemini -p "echo"` should be run in the background.
*   **Hardening**: Force-inject a strict "No-Chat" system prompt for Gemini to prevent it from greeting the user instead of following protocol.

### R3: The Win32 Pipe Guard
*   **Requirement**: Prevent deadlocks in the Windows environment (`win32`).
*   **Mitigation**: 
    1.  Force `asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())`.
    2.  Implement a size check: If prompt > 8KB, write to a temporary file and use CLI file input flags (e.g., `claude -p "$(cat file)"`).

---

## 3. Component Specifications

### 3.1 `CliOutputCleaner` (Critical Component)
All CLI nodes MUST pass their output through a centralized cleaner before returning to the Protocol.
*   **Remove**: ANSI color codes (`\x1B[...]`).
*   **Remove**: Progress indicators (ticks, spinning icons).
*   **Remove**: Tool-specific metadata (e.g., "Claude is thinking...").

### 3.2 Error Code Mapping
Standardize CLI exits into MAGI Exceptions:
*   `Exit 127`: `MagiProviderNotFoundError` (Suggest install command).
*   `Exit 1`: `MagiCliExecutionError` (Capture last 500 chars of stderr).
*   `Timeout`: `MagiNodeTimeout` (Trigger retry with `effort=low`).

---

## 4. Performance & Quality Targets (KPIs)

| Metric | Target | Acceptable Range |
|--------|--------|------------------|
| **Latency (3-node Parallel)** | < 10s | 8s - 15s |
| **Success Rate** | > 98% | No parser failures due to CLI noise |
| **Diversity Index** | High | Distinct personas across Claude/GPT/Gemini |

---

## 5. Implementation Roadmap (Immediate Steps)

1.  **Step 1**: Create `magi/core/utils/cli.py` containing `CliOutputCleaner` and Win32 helpers.
2.  **Step 2**: Implement `BaseCliNode` implementing the `NodeBase` protocol.
3.  **Step 3**: Implement `ClaudeCliNode`, `GeminiCliNode` (with warmup), and `OpenAIBackendNode` (Direct OAuth).
4.  **Step 4**: Update `magi/core/engine.py` with `cli_multi()` and `cli_single()` factories.

---

**Approver Signature:** 
*Gemini CLI Agent / Senior Architect*
*2026-03-30*
