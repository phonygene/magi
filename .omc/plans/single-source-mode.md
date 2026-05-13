# MAGI CLI-Native Mode — Implementation Plan

**Date:** 2026-03-30
**Status:** Draft v8 — Post Round 4 Cross-Model Review + Direct Feedback
**Author:** Claude Opus 4.6
**Changelog:**
- v2→v3: 實測 CLI flags、怪癖、模型清單
- v3→v4: NodeBase、Adapter、Stateless、Isolation、Cleaner、Error handling
- v4→v5: Structured output 主路徑、Codex stdin dual-mode、repo-check、analytics 影響
- v5→v6: Gemini `--prompt` 語意、Codex `-o file`、三層 isolation、web frontend、FileNotFoundError
- v6→v7: 移除 `--bare`（破壞 OAuth）、Adapter per-invocation state（併發安全）、Codex `-o`/`--json` 拆分、Gemini 降為 Phase 1B
- v7→v8: Phase 1A 三節點策略（第三節點用 claude/codex 替補）、mkstemp 修正 Windows handle、Claude 加 `--tools ""`、Gemini stdin fallback 策略、Windows 編碼處理

---

## 1. Motivation & Context

### Problem
Current MAGI requires 2-3 different LLM provider API keys. CLI-Native Mode 用本機 CLI 工具作為節點後端，**零 Provider API Key**（靠 CLI 訂閱/OAuth 登入）。

### CLI Tools (Verified 2026-03-30)

| CLI | Command | Model Flag | Effort Flag | Structured Output |
|-----|---------|-----------|-------------|-------------------|
| `claude` v2.1.87 | `claude -p` (stdin) | `--model` | `--effort` | `--output-format json` |
| `codex` v0.117.0 | `codex exec` (arg/stdin) | `-m` | `-c model_reasoning_effort=` | `-o <file>` / `--json` (JSONL) |
| `gemini` v0.35.3 | `gemini --prompt "text"` | `-m` | N/A | `-o json` |

> ⚠️ 觀察基於單機環境（Windows 11），可能因登入狀態、MCP、首次啟動等條件而異。

### Research Foundation

1. **NeurIPS 2025 "Debate or Vote"**: majority voting 是強 baseline；debate 不保證穩定提升；targeted interventions 可能改善。MAGI 的 ICE-style prompt 是受此方向啟發的**實作選擇**，非論文直接驗證。
2. **MAGI repo benchmark**: ICE-style critique 88% vs vote-only 76%。repo 自有數據，樣本量有限。

### Known CLI Quirks

| CLI | 行為 | 緩解 |
|-----|------|------|
| `gemini` | MCP 初始化 10-15s；可能把 prompt 當問候 | warmup + `[INSTRUCTION]` prefix |
| `gemini` | `--prompt` 需帶值（非布林旗標） | `--prompt "text"` |
| `codex` | `--json` 輸出 JSONL event stream | 改用 `-o <file>` |
| `codex` | 非 git repo 需 `--skip-git-repo-check` | Adapter 自動加入 |
| `claude` | **`--bare` 會禁用 OAuth/keychain 讀取** | **不使用 `--bare`**（v7 移除） |
| `claude` | stdin 3s 無資料顯示 warning | cosmetic，可忽略 |
| 所有 CLI | ANSI codes、progress indicators | structured output + CliOutputCleaner fallback |

### Prompt Delivery

| CLI | 方式 | 長 prompt |
|-----|------|-----------|
| `claude -p` | stdin pipe | 無限制 |
| `codex exec` | positional arg (短) | >20KB → stdin |
| `gemini --prompt` | `--prompt "text"` (短) | >20KB → `--prompt ""` + stdin（待驗證） |

### Structured Output

| CLI | Flag | 格式 | Parser |
|-----|------|------|--------|
| `claude` | `--output-format json` | 單一 JSON | `json.loads()` → extract text |
| `codex` | `-o <file>` (primary) | plain text file | 直接讀檔 |
| `codex` | `--json` (alternative mode) | JSONL events | 逐行 parse 取最後 assistant message |
| `gemini` | `-o json` | 單一 JSON | `json.loads()` → extract text |

> Codex 的 `-o` 和 `--json` 是**兩種獨立 execution mode**，不是 fallback chain（v7 修正）。

### Two Sub-Modes

| Mode | 節點組成 | 適用場景 |
|------|---------|---------|
| **`cli_multi`** | claude + codex + gemini | 最高品質辯論（三模型族多樣性） |
| **`cli_single`** | claude × 3 (不同 tier) | 只有 Claude 訂閱 |

> **v8 三節點策略**：MAGI engine、protocols、dashboard 全部基於三節點設計（3-way vote、三角佈局、personas tuple）。Phase 1A 在 Gemini 尚未驗證時，`cli_multi` **仍使用三節點**——第三節點用 claude 或 codex 的不同 tier/persona 替補：
> ```
> Phase 1A cli_multi（Gemini 未驗證時）：
>   MELCHIOR: claude opus   → deep analysis
>   BALTHASAR: codex gpt-5.4 → logic/code
>   CASPER:    claude sonnet  → balanced（替補節點）
>
> Phase 1B cli_multi（Gemini 驗證通過後）：
>   CASPER 切換為: gemini gemini-3-flash-preview → fast practical
> ```
> 這確保 vote/critique/adaptive 的三節點語意、`engine.__init__` 的 personas tuple、以及 dashboard 三角佈局在所有 phase 都一致。

---

## 2. Architecture Design

### 2.1 Node Contract

`NodeBase` Protocol（`magi/core/node.py`）：

```python
@runtime_checkable
class NodeBase(Protocol):
    name: str
    model: str
    persona: "Persona"
    last_cost_usd: float
    async def query(self, prompt: str) -> str: ...
```

### 2.2 Adapter Pattern (v7 — per-invocation state)

> **v7 重要變更**：Adapter 不再保存 per-call mutable state（如 `_output_file`）。所有 per-invocation state 透過 `prepare()` 回傳，確保併發安全。

```python
@dataclass
class InvocationContext:
    """Per-call invocation state. Concurrency-safe."""
    command: list[str]
    stdin_data: bytes | None
    temp_files: list[str]  # files to clean up after execution

    def cleanup(self):
        for f in self.temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass

class CliAdapter(Protocol):
    """Per-provider CLI behavior adapter."""
    model_description: str
    cli_name: str

    def prepare(self, prompt: str) -> InvocationContext: ...
    def parse_output(self, ctx: InvocationContext, stdout: bytes, stderr: bytes, returncode: int) -> str: ...

class ClaudeAdapter:
    """stdin-based, --effort, --output-format json."""

    def prepare(self, prompt):
        cmd = ["claude", "-p",
               "--model", self.model_tier,
               "--effort", self.effort,
               "--output-format", "json",
               # Isolation (v7: NO --bare — it disables OAuth/keychain)
               "--no-session-persistence",
               "--permission-mode", "plan",
               "--tools", ""]  # v8: disable all tools — pure node backend
        return InvocationContext(
            command=cmd,
            stdin_data=prompt.encode("utf-8"),
            temp_files=[],
        )

    def parse_output(self, ctx, stdout, stderr, returncode):
        text = stdout.decode("utf-8")
        try:
            data = json.loads(text)
            return data.get("result", data.get("content", text)).strip()
        except (json.JSONDecodeError, AttributeError):
            return CliOutputCleaner.clean(text)

class CodexAdapter:
    """Dual prompt mode, -o <tmpfile> for output."""
    ARGUMENT_LIMIT = 20_000

    def prepare(self, prompt):
        # Per-invocation temp file (concurrency-safe, Windows-safe)
        fd, output_file = tempfile.mkstemp(suffix=".txt")
        os.close(fd)  # Release handle so codex can write to it

        cmd = ["codex", "exec",
               "-m", self.model,
               "-c", f"model_reasoning_effort={self.effort}",
               "-o", output_file,
               # Isolation
               "--skip-git-repo-check",
               "--ephemeral"]

        stdin_data = None
        if len(prompt.encode("utf-8")) <= self.ARGUMENT_LIMIT:
            cmd.append(prompt)
        else:
            stdin_data = prompt.encode("utf-8")

        return InvocationContext(
            command=cmd,
            stdin_data=stdin_data,
            temp_files=[output_file],
        )

    def parse_output(self, ctx, stdout, stderr, returncode):
        # Read from -o file (last assistant message)
        output_file = ctx.temp_files[0] if ctx.temp_files else None
        if output_file and os.path.exists(output_file):
            # v8: Windows encoding safety — try UTF-8 first, fallback to system locale
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    text = f.read().strip()
            except UnicodeDecodeError:
                with open(output_file, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
            if text:
                return CliOutputCleaner.clean(text)
        # Fallback: plain stdout
        return CliOutputCleaner.clean(stdout.decode("utf-8", errors="replace"))

class GeminiAdapter:
    """--prompt with value, -o json. Phase 1B — experimental."""
    ARGUMENT_LIMIT = 20_000

    def prepare(self, prompt):
        cmd = ["gemini",
               "-m", self.model,
               "-o", "json",
               "--approval-mode", "plan"]

        stdin_data = None
        if len(prompt.encode("utf-8")) <= self.ARGUMENT_LIMIT:
            cmd += ["--prompt", prompt]
        else:
            cmd += ["--prompt", ""]
            stdin_data = prompt.encode("utf-8")

        return InvocationContext(
            command=cmd,
            stdin_data=stdin_data,
            temp_files=[],
        )

    def parse_output(self, ctx, stdout, stderr, returncode):
        text = stdout.decode("utf-8")
        try:
            data = json.loads(text)
            return data.get("response", data.get("text", text)).strip()
        except (json.JSONDecodeError, AttributeError):
            return CliOutputCleaner.clean(text)

    async def warmup(self):
        proc = await asyncio.create_subprocess_exec(
            "gemini", "--prompt", "Reply with only: OK",
            "-m", self.model, "--approval-mode", "plan",
            stdout=PIPE, stderr=PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30.0)

    # v8 NOTE: If --prompt "" + stdin proves invalid in Phase 1B verification,
    # fallback strategies in priority order:
    # 1. Pure stdin without --prompt flag (if gemini supports headless stdin-only)
    # 2. Write prompt to temp file + gemini --prompt "$(cat file)" (if shell mode)
    # 3. Inline full prompt in --prompt "text" with shell escaping (short prompts only)
```

### 2.3 CliNode

```python
class CliNode:
    """CLI-backed MAGI node implementing NodeBase contract."""

    def __init__(self, name, persona, adapter, timeout=60.0):
        self.name = name
        self.persona = persona
        self.model = adapter.model_description
        self.last_cost_usd = 0.0
        self.cost_mode = "unavailable"
        self.adapter = adapter
        self.timeout = timeout

    async def query(self, prompt: str) -> str:
        full_prompt = self._build_prompt(prompt)
        ctx = self.adapter.prepare(full_prompt)

        try:
            stdout, stderr, returncode = await self._run_isolated(ctx)
            return self.adapter.parse_output(ctx, stdout, stderr, returncode)
        finally:
            ctx.cleanup()

    async def _run_isolated(self, ctx: InvocationContext):
        env = os.environ.copy()
        env["MAGI_NODE_MODE"] = "1"

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *ctx.command,
                    stdin=PIPE if ctx.stdin_data else None,
                    stdout=PIPE, stderr=PIPE,
                    cwd=tmpdir, env=env,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=ctx.stdin_data),
                    timeout=self.timeout,
                )
            except FileNotFoundError:
                raise MagiProviderNotFoundError(self.adapter.cli_name)
        return stdout, stderr, proc.returncode

    def _build_prompt(self, query):
        parts = [
            f"[INSTRUCTION] You are {self.name}. Follow the instructions precisely.",
            f"Your role: {self.persona.system_prompt}",
            f"Question: {query}",
        ]
        return "\n\n".join(parts)
```

### 2.4 CliOutputCleaner (Fallback)

```python
class CliOutputCleaner:
    _ANSI_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")
    _NOISE_PATTERNS = [
        re.compile(r"^⠋.*$", re.MULTILINE),
        re.compile(r"^Thinking\.+$", re.MULTILINE),
        re.compile(r"^Claude is thinking\.\.\.$", re.MULTILINE),
    ]
    _CODEBLOCK_RE = re.compile(r"^```(?:\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)

    @classmethod
    def clean(cls, text):
        text = cls._ANSI_RE.sub("", text)
        for p in cls._NOISE_PATTERNS:
            text = p.sub("", text)
        match = cls._CODEBLOCK_RE.search(text.strip())
        if match and match.group(0).strip() == text.strip():
            text = match.group(1)
        return text.strip()
```

### 2.5 Error Handling

| 偵測層 | 方法 | Exception |
|--------|------|-----------|
| Preflight | `shutil.which()` | `MagiProviderNotFoundError` |
| Runtime | `FileNotFoundError` (Windows primary) | `MagiProviderNotFoundError` |
| Exit code | Non-zero | `MagiCliExecutionError` |
| Auth | stderr 特徵偵測 | `MagiCliAuthError` |
| Timeout | `asyncio.wait_for` | `MagiNodeTimeoutError` |

### 2.6 Isolation Strategy (v7 — 移除 `--bare`)

**v7 重要變更**：`claude --bare` 會禁用 OAuth/keychain 讀取，與「零 API key、靠 CLI 訂閱登入」的核心目標直接衝突。已從預設移除。

#### Layer 1: Documented CLI Flags (Primary)

| CLI | Flags | 效果 |
|-----|-------|------|
| `claude` | `--no-session-persistence --permission-mode plan --tools ""` | 不保存 session、唯讀模式、禁用工具、保留 OAuth |
| `codex` | `--ephemeral --skip-git-repo-check` | 不持久化、允許非 git repo |
| `gemini` | `--approval-mode plan` | 唯讀模式 |

> **Claude 兩種 isolation profile（v7 新增）**：
> - **`auth-safe`**（預設）：`--no-session-persistence --permission-mode plan --tools ""` — 保留 OAuth、禁用工具、純回答模式
> - **`max-isolation`**（僅 API key 用戶）：`--bare` — 最強隔離，需 `ANTHROPIC_API_KEY`

#### Layer 2: `cwd=tmpdir`
#### Layer 3: Env vars (opportunistic)

### 2.7 Effort Design

| 層級 | 說明 |
|------|------|
| CLI-native | `claude --effort`, `codex -c model_reasoning_effort=` |
| Prompt-level depth augmentation | MAGI 注入 prompt prefix（控制回答深度，非推理深度等價替代） |

Gemini 完全依賴 prompt-level depth augmentation（可配置停用）。

### 2.8 Cost Tracking

```python
@dataclass
class Decision:
    cost_usd: float = 0.0
    cost_mode: str = "measured"  # measured | estimated | unavailable
```

> Hybrid 限制：Phase 1 不支援 per-node cost breakdown。

### 2.9 Default Configurations

#### `cli_multi` Phase 1A — claude + codex + claude替補 (recommended)
```
MELCHIOR:  claude -p --model opus --effort high --output-format json --no-session-persistence --permission-mode plan --tools ""
BALTHASAR: codex exec -m gpt-5.4 -c model_reasoning_effort=xhigh -o output.txt --ephemeral --skip-git-repo-check
CASPER:    claude -p --model sonnet --effort medium --output-format json --no-session-persistence --permission-mode plan --tools ""
```
> Phase 1A 仍保持三節點。CASPER 用 claude sonnet 替補，確保 engine/protocols/dashboard 語意完整。

#### `cli_multi` Phase 1B — CASPER 切換為 gemini (after validation)
```
CASPER: gemini --prompt "..." -m gemini-3-flash-preview -o json --approval-mode plan
```

#### `cli_single` — Claude Only
```
MELCHIOR:  claude -p --model opus --effort high --output-format json --no-session-persistence --permission-mode plan --tools ""
BALTHASAR: claude -p --model sonnet --effort medium --output-format json --no-session-persistence --permission-mode plan --tools ""
CASPER:    claude -p --model haiku --effort low --output-format json --no-session-persistence --permission-mode plan --tools ""
```

---

## 3. Implementation Plan

### Phase 1A: claude + codex + claude替補（三節點 Core）

> **v8 關鍵決策**：Phase 1A 仍使用三節點。第三節點 (CASPER) 用 claude sonnet 替補，確保 engine `__init__` 的 3-tuple personas、vote/critique/adaptive 的三節點語意、以及 dashboard 三角佈局全部一致。Phase 1B 時 CASPER 切換為 gemini。

**前置驗證任務（必須在寫 code 前完成）：**
1. 驗證 `claude -p --output-format json` 的 JSON response structure
2. 驗證 `codex exec -o <file>` 的 output 內容格式
3. 驗證 `claude --no-session-persistence --permission-mode plan --tools ""` 是否成功保留 OAuth + 隔離 workspace + 禁用工具

**New files:**
- `magi/core/cli_adapters.py` — CliAdapter Protocol + ClaudeAdapter + CodexAdapter + InvocationContext + CliOutputCleaner
- `magi/core/cli_node.py` — CliNode
- `magi/core/cli_errors.py` — 統一錯誤類型

### Phase 1B: + gemini (Experimental)

**前置驗證任務：**
4. 驗證 `gemini --prompt "text" -o json` 的 JSON 格式
5. 驗證 `gemini --prompt "" + stdin` 組合
6. 驗證 warmup 最小命令

**Add:** GeminiAdapter to `cli_adapters.py`

### Phase 2: Engine Integration
- `engine.py`: `cli_multi()` / `cli_single()` factories + `cost_mode`
- `decision.py`: `cost_mode` field

### Phase 3: CLI Integration
- `cli.py`: `--source`, `--tier`, `--effort` flags

### Phase 4: Session Management (Deferred)

### Phase 5: Tests

---

## 4. File Change Summary

| File | Action | Description |
|------|--------|-------------|
| `magi/core/node.py` | MODIFY | `NodeBase` Protocol |
| `magi/core/cli_adapters.py` | **NEW** | Adapters + InvocationContext + CliOutputCleaner |
| `magi/core/cli_node.py` | **NEW** | CliNode |
| `magi/core/cli_errors.py` | **NEW** | Error types |
| `magi/core/engine.py` | MODIFY | Factories + `cost_mode` |
| `magi/core/decision.py` | MODIFY | `cost_mode` field |
| `magi/cli.py` | MODIFY | `--source`, `--tier`, `--effort` + cost display |
| `magi/web/server.py` | MODIFY | `cost_mode` in WebSocket payloads |
| `magi/web/static/index.html` | MODIFY | Frontend: `N/A` when unavailable |
| `magi/commands/analytics.py` | MINOR | `N/A` cost display |
| `magi/commands/diff.py` | MINOR | `cost_mode` formatting |
| `tests/test_cli_node.py` | **NEW** | CliNode tests |
| `tests/test_cli_adapters.py` | **NEW** | Adapter tests + concurrency tests |

---

## 5. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| CLI not installed | HIGH | `shutil.which()` + `FileNotFoundError` |
| CLI auth expired | HIGH | stderr detection → `MagiCliAuthError` |
| `--bare` 破壞 OAuth | **CRITICAL** | **不使用 `--bare`**（v7 移除） |
| Adapter 併發不安全 | HIGH | `InvocationContext` per-call state（v7 修正） |
| Gemini 未驗證 | HIGH | 降為 Phase 1B（v7） |
| Workspace contamination | HIGH | 三層 isolation（CLI flags primary） |
| Codex `-o` 格式不確定 | MEDIUM | Phase 1A 前置驗證 |
| No cost tracking | MEDIUM | `cost_mode="unavailable"` |

---

## 6. Known Limitations (v7)

1. **Cost**: CLI 模式 `cost_usd=0.0`。所有 surfaces 顯示 `N/A`。
2. **Isolation**: Best-effort。`--bare` 不可用（破壞 OAuth）；`--no-session-persistence + --permission-mode plan` 是 auth-safe 替代。
3. **No streaming**: batch stdout。
4. **Gemini**: Phase 1B experimental。需前置驗證。
5. **Stateless**: 不實作 cross-turn memory。
6. **Hybrid cost**: 不支援 per-node breakdown。

---

## 7. Decisions Log (v7)

| 決策 | 選擇 | 被拒方案 | 理由 |
|------|------|---------|------|
| Codex 後端 | CLI subprocess | httpx API | 未公開、ToS |
| Node 介面 | Adapter Protocol | 統一 `_run_cli()` | 差異大 |
| Phase 1 Memory | Stateless | transcript injection | 避免污染 |
| Cost | `cost_mode` 欄位 | 固定 0 | 區分 measured/unavailable |
| Output | Structured JSON + Cleaner fallback | Regex 為主 | JSON 更可靠 |
| Codex prompt | stdin dual-mode | `$(cat file)` | exec 不做 shell substitution |
| Codex output | `-o <tmpfile>` | `--json` JSONL parse | plain text 更穩 |
| Gemini prompt | `--prompt "text"` | `-p`（布林假設） | 需帶值 |
| Isolation 策略 | CLI flags 優先 | env var 優先 | 正式 flag 更可靠 |
| Error detection | `FileNotFoundError` primary | exit 127 | Windows 相容 |
| Depth augmentation | prompt-level | Virtual Effort | 非推理深度等價 |
| Claude isolation | `--no-session-persistence --permission-mode plan` | `--bare` | **`--bare` 禁用 OAuth**（v7 關鍵修正） |
| Adapter state | `InvocationContext` per-call | instance `_output_file` | 併發安全（v7 修正） |
| Gemini 定位 | Phase 1B experimental | Phase 1A 預設 | 未驗證太多（v7） |
| Codex modes | `-o` 和 `--json` 獨立 mode | fallback chain | 同一執行只用其一（v7） |
| Phase 1A 節點數 | 三節點（CASPER 用 claude sonnet 替補） | 兩節點 | engine/protocols/dashboard 全基於三節點設計（v8） |
| Codex temp file | `mkstemp()` + `os.close(fd)` | `NamedTemporaryFile().name` | Windows handle lock 問題（v8） |
| Claude 工具禁用 | `--tools ""` 加入 auth-safe profile | 僅 `--permission-mode plan` | 純 node backend 不需工具（v8） |
| Windows 編碼 | `errors="replace"` fallback | 假設 UTF-8 | Codex `-o` 可能隨 locale 輸出非 UTF-8（v8） |
