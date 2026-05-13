# MAGI CLI-Native Optimization Plan

**Target:** Improve reliability, latency, and diversity of the CLI-backed model nodes.
**Reference:** Based on OpenAI Connect (OAuth) and modern CLI tool behaviors (2026).

---

## 1. High-Performance OpenAI Provider (Balthasar)

Instead of relying on ambiguous CLI wrappers like `codex exec`, we leverage the **"Bring Your Own Subscription" (BYOS)** flow. If the user has authenticated via `opencode` or a similar tool, we can skip the subprocess overhead.

### Direct OAuth Prototype (Python)
Instead of `subprocess.run(["opencode", ...])`, we use:

```python
async def query_openai_backend(self, prompt: str) -> str:
    # 1. Load token from local Opencode/OpenAI config
    config_path = Path.home() / ".local/share/opencode/auth.json"
    if not config_path.exists():
        return await self._run_cli_fallback(prompt)

    auth_data = json.loads(config_path.read_text())
    access_token = auth_data.get("access_token")

    # 2. Direct Call to ChatGPT Backend (No API Key needed)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "MAGI-Structured-Disagreement-Engine/1.0"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://chatgpt.com/backend-api/codex/responses",
            headers=headers,
            json={
                "model": "openai-codex/gpt-5.4",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            },
            timeout=60.0
        )
        return response.json()["content"]["parts"][0]
```

---

## 2. Robust CLI Communication (The `CliNode` Base)

CLI tools are "noisy." They output ANSI colors, progress bars, and ASCII art.

### Optimization: `CliOutputCleaner`
We must implement a filter to ensure the nodes only return "Model Truth," not "Terminal Metadata."

```python
import re

def clean_cli_output(text: str) -> str:
    # Remove ANSI escape sequences (colors, bold, etc.)
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)
    
    # Strip common CLI prefix noise (e.g., "Claude is thinking...")
    noise_patterns = [
        r"^Claude is thinking.*\n",
        r"^.*?Working on it.*\n",
        r"^[✓✗ℹ].*\n"
    ]
    for pattern in noise_patterns:
        text = re.sub(pattern, '', text, flags=re.MULTILINE)
        
    return text.strip()
```

---

## 3. Windows (Win32) System Integrity

The user's environment is `win32`. `asyncio` subprocesses require specific handling.

### Mandatory Fixes:
1.  **Event Loop Policy**: Ensure `ProactorEventLoop` is used.
    ```python
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    ```
2.  **Large Input Handling**: If the injected context (Project Brief + Code) exceeds 8,192 characters, `stdin.write()` might block. 
    *   **Fix**: Use `tempfile` for the prompt and pass the path to the CLI: `claude -p "$(cat tmp_prompt.txt)"` (or use powershell equivalent).

---

## 4. Diversity Tuning (The "Persona Offset")

In `cli_single` mode (e.g., all 3 nodes use `claude`), the "Echo Chamber" risk is high.

### Optimization: System Prompt Variation
We inject specific "Critical Biases" into each persona to force disagreement:
*   **MELCHIOR (Scientific)**: "Focus on formal verification, edge cases, and algorithmic complexity. Be skeptical of shortcuts."
*   **BALTHASAR (Caregiver)**: "Focus on user experience, documentation clarity, and maintainability. Prioritize the human factor."
*   **CASPER (Pragmatic)**: "Focus on deployment speed, cost-effectiveness, and YAGNI (You Ain't Gonna Need It) principles."

---

## 5. Failure Recovery (Graceful Degradation)

| Error Condition | CLI Response | MAGI Action |
|-----------------|--------------|-------------|
| **Auth Expired** | "Please login", "Session expired" | Raise `MagiAuthError`, stop execution, and show login command. |
| **Rate Limited** | "Too many requests", "429" | Wait 60s or fallback to a different CLI provider (e.g., move from Claude to Gemini). |
| **Timeout** | (No output) | Retry once with `effort="quick"`. |

---

## 6. Decision Metadata Changes

Since CLI usage has no `usage` field (tokens), we will add:
*   `decision.metadata.source`: `cli-native`
*   `decision.metadata.cli_versions`: `{ "claude": "2.1.87", ... }`
*   `decision.cost_usd`: 0.00 (But log `input_chars` and `output_chars` for telemetry).

---

**Approval Status:** This optimization plan is ready for implementation alongside Phase 1 of the main plan.
