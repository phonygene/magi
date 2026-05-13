# MAGI `single-source-mode.md` 第四輪審批與修正建議

**審閱日期:** 2026-03-30  
**審閱對象:** `C:\Projects\magi\.omc\plans\single-source-mode.md`  
**版本:** v6

## 審批結論

**結論: 有條件通過**

v6 已經是相當成熟的實作計畫，主要強項在於：
- 補上 `gemini --prompt` 的真實語義
- 把 `codex --json` 問題改成 `-o <file>` 為主路徑
- isolation 改為正式 CLI flags 優先
- 把 frontend 的 `cost_mode` 影響面納入
- 把 Windows 上 `FileNotFoundError` 納入主偵測路徑

但這一版仍然存在兩個新的高優先級問題：
- `claude --bare` 可能直接破壞「無 API key、靠 CLI 訂閱登入」這個前提
- `CodexAdapter` 把 `_output_file` 存在 adapter instance 上，對並發執行不安全

這兩點若不修，實作很可能在一開始就踩雷。

## 評分

**整體評分: 8.5 / 10**

## v5→v6 改進評估

### 有效修改

- Gemini 的 `--prompt` 語義終於被正確辨識出來，這是關鍵修正
- `codex` 改用 `-o <tmpfile>` 當主輸出路徑，比硬 parse JSONL 穩健
- `FileNotFoundError` 作為 Windows 主偵測邏輯是正確修正
- 三層 isolation 的優先順序比前版合理得多
- `web/static/index.html` 已被納入變更影響面，這是必要補強

### 仍不足之處

- `claude --bare` 與 CLI 訂閱登入模式之間可能存在直接衝突
- `CodexAdapter._output_file` 是共享 mutable state，與 engine/bench 的併發模型衝突
- `CodexAdapter` 的 JSONL fallback 路徑仍不夠一致
- `Gemini` 雖然已被修正語義，但長 prompt 路徑仍屬未驗證狀態

## 優點

- 這版終於真正從本機 CLI 介面出發，而不是憑印象設計 adapter。
- 風險描述已經很接近可實作層級，不再只是概念性討論。
- `Phase 1 前置驗證任務` 很有價值，能防止未驗證假設直接進 code。
- CLI-specific 行為差異已被寫進設計，而不是被抽象層遮蔽。
- `cost_mode` 的 downstream surface 已接近完整。

## 問題與風險

### HIGH 1. `claude --bare` 可能破壞方案成立性

依本機 `claude --help`，`--bare` 會：
- 跳過 keychain / OAuth 讀取
- Anthropic auth 嚴格依賴 `ANTHROPIC_API_KEY` 或 `apiKeyHelper via --settings`

這和本方案的主要動機之一：
- **沒有 provider API key，靠 CLI 訂閱 / 登入**

可能直接衝突。  
換句話說，`--bare` 雖然有助 isolation，但可能讓 Claude node 根本無法使用。

**建議修正**
- 不要把 `--bare` 當預設 isolation flag
- 將 Claude 的 isolation 拆成兩層候選方案：
  - `safe-auth mode`: `--no-session-persistence --tools ""` + `cwd=tmpdir`
  - `max-isolation mode`: `--bare`（僅在使用 API key / settings auth 時啟用）
- 把這點提升到 plan 的高風險區，而不是只當既有旗標之一

### HIGH 2. `CodexAdapter._output_file` 對並發不安全

v6 把 `CodexAdapter._output_file` 存在 adapter instance 上。這會在下列情境出問題：
- `engine.ask()` 重用同一組 nodes
- benchmark 會在同一個 engine 上並發跑多題
- 同一個 `CodexAdapter` 可能被多個 query 同時使用

結果會出現：
- output file path 被後一個 query 覆蓋
- 讀錯別人的輸出
- cleanup 誤刪仍在使用的檔案

**建議修正**
- 把 output file path 變成 per-invocation local state，不要掛在 adapter instance 上
- 更好的介面是：

```python
class PreparedInvocation:
    command: list[str]
    stdin_data: bytes | None
    cleanup: Callable[[], None]
    parse_result: Callable[[stdout, stderr, code], str]
```

或至少讓 `build_command()` 回傳 command + context，而不是把 state 藏在 adapter 物件內

### MEDIUM 3. `CodexAdapter` 的 fallback 路徑仍不夠一致

目前主路徑是：
- `-o <tmpfile>`

fallback 是：
- parse stdout JSONL

但 `build_command()` 本身沒有加 `--json`，所以 JSONL fallback 不會自然出現。除非是：
- retry path
- debug mode
- alternative command builder

否則這段 fallback 在同一次執行裡不成立。

**建議修正**
- 明確定義兩條獨立 execution mode：
  - `mode="outfile"` → `-o <tmpfile>`
  - `mode="jsonl"` → `--json`
- 不要把兩種輸出形式混在同一個 parser 流程裡

### MEDIUM 4. Gemini 仍應視為預設外的實驗性節點，直到前置驗證完成

v6 雖然已經把 Gemini 的風險誠實寫出來，但 `cli_multi` 仍把 Gemini 放成預設第三節點。  
在 `--prompt "" + stdin`、`-o json`、warmup 命令都尚未驗完之前，這樣的預設還是過早。

**建議修正**
- 文件上把 Gemini 標記為：
  - `Phase 1B`
  - 或 `experimental third node`
- `cli_multi` 預設先考慮：
  - `claude + codex + second claude/codex profile`
  - 或保留 Gemini 但明確標示為「需前置驗證通過後啟用」

### MEDIUM 5. isolation 策略仍應補上 Claude 的 documented flags 選項設計

現在 v6 已經把：
- `--bare`
- `--no-session-persistence`

寫進去了，但本機 help 還顯示：
- `--tools ""`
- `--permission-mode plan`

對於「讓 Claude 只做純回答，不進工具模式」這兩個旗標也非常重要。

**建議修正**
- 在 Claude isolation flag 設計中顯式比較：
  - `--bare`
  - `--no-session-persistence`
  - `--tools ""`
  - `--permission-mode plan`

## 建議修改

### 一、把 Claude isolation 分成兩種 profile

建議不要再把 `--bare` 直接硬編進預設命令。  
改成：

- `claude_auth_safe`:
  - `--no-session-persistence`
  - `--tools ""`
  - `--permission-mode plan`
- `claude_max_isolation`:
  - `--bare`
  - 僅在 API-key / settings-auth 可用時啟用

### 二、重寫 Codex invocation state 模型

不要讓 adapter 保存 `_output_file`。  
改成：
- per-call temporary file path
- per-call cleanup
- per-call parse context

### 三、把 Codex `-o` 與 `--json` 明確拆成兩種 mode

例如：

```python
class CodexAdapter:
    def prepare_invocation(self, prompt, mode="outfile"): ...
```

讓文件與實作都不會再混淆兩種輸出語意。

### 四、把 Gemini 降級成條件式預設

在前置驗證未完成前，建議直接把文件改成：
- `Gemini remains experimental in Phase 1`

## 替代方案

若你想讓第一版真的穩，建議做成：

- `Phase 1A`: `claude` + `codex`
- `Phase 1B`: `gemini`

這會比現在三家一起落地更可控。

## 最終審批

**審批結果: 有條件通過**

## 通過條件

- 釐清 `claude --bare` 與 CLI 訂閱登入的衝突，避免它成為預設旗標
- 修正 `CodexAdapter` 的共享 `_output_file` 併發風險
- 將 Codex `-o` 與 `--json` 明確拆成兩種 execution mode
- 在前置驗證完成前，將 Gemini 標記為 experimental / Phase 1B

完成以上幾點後，v6 才適合進入實作。
