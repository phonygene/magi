# Cross-Model Review Prompt: MAGI CLI-Native Mode (v8)

> 將此 Prompt 貼給其他模型進行批判審閱。
> v8 已整合四輪跨模型審閱 + 直接反饋。關鍵修正：Phase 1A 三節點策略（claude替補）、mkstemp Windows handle、Claude `--tools ""`、Gemini stdin fallback、Windows 編碼處理。

---

## Background

MAGI 是一個開源的 **Structured Disagreement Engine**。三個 LLM 節點（MELCHIOR / BALTHASAR / CASPER）獨立回答同一問題，再透過協議（投票 / ICE 批判辯論 / 自適應路由）產出 Decision Dossier。

### 研究基礎（分離敘事）

1. **NeurIPS 2025 "Debate or Vote"**: majority voting 是強 baseline；debate 不保證穩定提升；targeted interventions 可能改善。MAGI 的 ICE-style prompt 是受此啟發的**實作選擇**，非論文直接驗證。
2. **MAGI repo benchmark**: ICE-style critique 88% vs vote-only 76%。repo 自有數據，樣本量有限。

### Node Contract（已驗證）

| 屬性 | 使用位置 |
|------|---------|
| `name: str` | protocols、web server event payload |
| `query(prompt) -> str` | protocols |
| `model: str` | dashboard node info |
| `persona: Persona` | dashboard、system prompt |
| `last_cost_usd: float` | engine cost aggregation、web server、analytics、cli、diff |

`NodeBase` Protocol 位於 `magi/core/node.py`。

### CLI Tools (Verified 2026-03-30)

> ⚠️ 單機環境觀察，可能因登入狀態、MCP、首次啟動而異。

| CLI | Prompt 傳遞 | Structured Output | Isolation Flags |
|-----|------------|-------------------|-----------------|
| `claude` v2.1.87 | stdin (`-p`) | `--output-format json` | `--no-session-persistence --permission-mode plan --tools ""` |
| `codex` v0.117.0 | arg/stdin dual | `-o <file>` (primary) / `--json` (JSONL, alt mode) | `--ephemeral --skip-git-repo-check` |
| `gemini` v0.35.3 | `--prompt "text"` | `-o json` | `--approval-mode plan` |

**關鍵發現（經四輪審閱確認）：**
- `claude --bare` **禁用 OAuth/keychain 讀取**，與「零 API key」目標直接衝突 → 已從預設移除
- `codex --json` 輸出 **JSONL event stream**（非單一 JSON） → 改用 `-o <file>`
- `gemini --prompt` **需要帶值**（非布林旗標） → `--prompt "text"`
- `claude --tools ""` **禁用所有工具**，確保純 node backend 回答模式（v8 新增）

### Adapter Pattern + Per-Invocation State

```python
@dataclass
class InvocationContext:
    """Per-call state. Concurrency-safe — no mutable state on adapter instance."""
    command: list[str]
    stdin_data: bytes | None
    temp_files: list[str]
    def cleanup(self): ...

class CliAdapter(Protocol):
    model_description: str
    cli_name: str
    def prepare(self, prompt: str) -> InvocationContext: ...
    def parse_output(self, ctx: InvocationContext, stdout: bytes, stderr: bytes, returncode: int) -> str: ...

class ClaudeAdapter:   # stdin, --output-format json, --no-session-persistence --permission-mode plan --tools ""
class CodexAdapter:    # arg/stdin, -o tmpfile, --ephemeral --skip-git-repo-check
class GeminiAdapter:   # --prompt "text", -o json, --approval-mode plan, warmup() [Phase 1B]
```

**v7 關鍵設計變更：**
- `prepare()` 回傳 `InvocationContext`（per-call），不在 adapter instance 上存 mutable state
- Claude isolation 使用 `--no-session-persistence --permission-mode plan`（保留 OAuth）
- Codex `-o <file>` 和 `--json` 是**獨立 execution mode**，非 fallback chain

### Isolation（三層，CLI flags 優先）

| Layer | 方法 | 說明 |
|-------|------|------|
| 1 | Documented CLI flags | `--no-session-persistence`, `--permission-mode plan`, `--ephemeral`, etc. |
| 2 | `cwd=tmpdir` | 避免 workspace detection |
| 3 | Env vars | opportunistic hardening |

**Claude 兩種 isolation profile：**
- **`auth-safe`**（預設）：`--no-session-persistence --permission-mode plan --tools ""` — 保留 OAuth、禁用工具
- **`max-isolation`**（僅 API key 用戶）：`--bare` — 最強隔離，需 `ANTHROPIC_API_KEY`

### Phase 分期

| Phase | 內容 | 節點 |
|-------|------|------|
| **1A** | Core CLI mode（三節點） | claude + codex + claude sonnet 替補 CASPER |
| **1B** | CASPER 切換 | CASPER → gemini（前置驗證通過後） |
| **2** | Engine integration | factories + cost_mode |
| **3** | CLI integration | --source flag |

### Cost Tracking

```python
@dataclass
class Decision:
    cost_usd: float = 0.0
    cost_mode: str = "measured"  # measured | estimated | unavailable
```

CLI 模式：`cost_mode="unavailable"`。Downstream：analytics、cli、diff、**web frontend** 顯示 `N/A`。

---

## 審閱請求

### 1. 架構合理性
- Adapter Protocol + `InvocationContext` per-call state 是否適當？是否有更簡潔的設計？
- **`InvocationContext` 的 `cleanup()` 是否在所有 error path 都能被呼叫？`try/finally` 是否足夠？**
- `NodeBase` Protocol 是否涵蓋所有隱性依賴？

### 2. Phase 1A 三節點策略
- **Phase 1A 使用 claude sonnet 替補 CASPER（第三節點），確保三節點語意一致。此策略是否合理？**
- **替補節點與 MELCHIOR（claude opus）是否會因同族模型導致多樣性不足（echo chamber）？Persona differentiation 是否足以緩解？**
- Phase 1B CASPER 切換為 gemini 時，是否需要 migration path 或直接替換即可？

### 3. Claude Isolation vs Auth
- **`--no-session-persistence --permission-mode plan --tools ""` 是否足以隔離 workspace context、禁用工具、同時保留 OAuth 登入？**
- **`--tools ""` 是否確實禁用所有工具？是否有 edge case？**
- 是否有其他 Claude CLI flags 可以進一步隔離（如跳過 CLAUDE.md discovery），但不破壞 OAuth？
- 兩種 profile（`auth-safe` vs `max-isolation`）的設計是否合理？

### 4. Codex Execution Modes
- **`-o <file>` 的 output 是 plain text 還是包含 metadata？**
- **`-o` 和 `--json` 作為獨立 execution mode（非 fallback chain）是否正確？是否有場景需要同時使用？**
- `codex exec` 是否會執行 prompt 描述的程式碼？安全邊界在哪？

### 5. Gemini Phase 1B
- **Gemini 降為 Phase 1B experimental 是否合理？**
- **`--prompt ""` + stdin 組合是否有效？**
- Warmup 的正確最小命令是什麼？

### 6. Concurrency Safety
- **Adapter `prepare()` 回傳 per-call `InvocationContext` 是否足以保證併發安全？**
- **Engine 的 `ask()` 和 benchmark 會重用同一組 nodes 並發跑多題。此設計是否能正確處理？**
- temp file cleanup 是否有 race condition 風險？

### 7. Structured Output
- 各 CLI 的 JSON mode 是否會改變模型回答行為？
- JSON key path（`result`/`content`/`response`/`text`）是否正確？
- CliOutputCleaner 的 Markdown code block 解析是否足夠？

### 8. Error Handling
- `FileNotFoundError` 作為 Windows primary detection 是否正確？
- CLI auth error 的 stderr 特徵如何偵測？是否需要 per-provider regex？

### 9. Cost & Observability
- Hybrid mode 需要 per-node cost breakdown 嗎？
- Dashboard frontend 在 `cost_mode="unavailable"` 時的 UX 建議？

### 10. 替代方案

| 方案 | 優點 | 缺點 |
|------|------|------|
| A: CLI subprocess + Adapter + per-call state (v7) | 零 API Key、併發安全、isolation flags | 延遲高、gemini 不穩 |
| B: Python SDK | 低延遲、streaming、metadata | 需 API Key |
| C: 混合 CLI + SDK | 最大彈性 | 兩套錯誤處理 |

### 11. 遺漏項目
- 錯誤恢復：CLI 掛掉後 protocol 能否用 2 nodes 繼續？
- Rate limiting：CLI rate limit 如何處理？
- Custom CLI：`ollama run llama3`？
- 版本變動防禦？

---

## 補充資訊

- **原始碼**: `C:\Projects\magi`（MIT License）
- **完整計畫書**: `C:\Projects\magi\.omc\plans\single-source-mode.md` (v8)
- **現有測試**: 83 個全通過（純 mock）
- **CLI 版本**: claude 2.1.87, codex 0.117.0, gemini 0.35.3
- **NeurIPS 2025**: "Debate or Vote" (https://arxiv.org/abs/2508.17536)
- **技術棧**: Python 3.10+, asyncio, litellm, click, pytest-asyncio

### v7→v8 主要修改

| 項目 | v7 | v8 |
|------|----|----|
| Phase 1A 節點數 | 2 nodes (claude+codex) | **3 nodes**（CASPER = claude sonnet 替補） |
| Claude isolation | `--no-session-persistence --permission-mode plan` | 加入 **`--tools ""`** 禁用工具 |
| Codex temp file | `NamedTemporaryFile().name` | **`mkstemp()` + `os.close(fd)`**（Windows handle 安全） |
| Gemini stdin | 未定義 fallback | 補充 **fallback 策略**（純 stdin / temp file） |
| Windows 編碼 | 假設 UTF-8 | **`errors="replace"`** fallback |

### 完整版本歷史

| 版本 | 主要變更 | 審閱結果 |
|------|---------|---------|
| v3 | 實測 CLI flags | Gemini: 有條件通過；Detailed: 需重大修改 (6.5/10) |
| v4 | NodeBase、Adapter、Stateless | Gemini: 通過；Detailed: 有條件通過 (7.5/10, 8/10) |
| v5 | Structured output、Codex dual-mode | Gemini: 最終通過；Detailed: 有條件通過 (8/10, 8.5/10) |
| v6 | Gemini prompt、3-layer isolation | Gemini: 最終通過；Detailed: 有條件通過 (8.5/10, 9/10) |
| v7 | 移除 --bare、per-call state、Phase 1A/1B | Gemini: 最終通過；Detailed: 有條件通過 (8.5/10, 9/10) |
| v8 | 三節點策略、mkstemp、--tools ""、encoding | 待審閱 |

---

請以結構化格式回覆：

```
## 評分 (1-10)

## v6→v7 改進評估

## 優點（3-5 個）

## 問題與風險（按嚴重度，標明 HIGH/MEDIUM/LOW）

## 建議修改

## 結論：通過 / 有條件通過 / 需重大修改
```
