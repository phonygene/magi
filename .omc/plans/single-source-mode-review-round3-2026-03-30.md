# MAGI `single-source-mode.md` 第三輪審批與修正建議

**審閱日期:** 2026-03-30  
**審閱對象:** `C:\Projects\magi\.omc\plans\single-source-mode.md`  
**版本:** v5

## 審批結論

**結論: 有條件通過**

v5 是目前為止最接近可實作的一版。它已經修正了前兩輪中最明顯的問題：
- structured output 升格為主路徑
- `codex` 長 prompt 改成 dual-mode
- `codex` repo-check 衝突已納入
- 文獻敘事已明顯收斂
- analytics / diff 受影響面有補列

但仍有幾個會直接影響實作成敗的關鍵點沒有收斂，尤其集中在：
- `GeminiAdapter` 的 headless invocation 很可能仍然寫錯
- `CodexAdapter` 的 `--json` 輸出格式被過度簡化
- isolation 還沒有充分利用已文件化、可驗證的 CLI flags
- `cost_mode` 的 UI 影響面仍低估

在修正這些問題前，不建議直接進入實作。

## 評分

**整體評分: 8 / 10**

## v4→v5 改進評估

### 有效修改

- 把 structured output 升為主路徑，是非常正確的修正
- `CodexAdapter` 改成 argument/stdin dual-mode，方向正確
- `--skip-git-repo-check` 已補進 `codex` 的 isolation 設計
- `NodeBase` 位置已統一，不再有文件衝突
- `analytics.py` / `diff.py` 的 `cost_mode` 影響面已被辨識出來

### 仍不足之處

- `gemini -p` 的用法在 v5 中仍可能不正確
- `codex --json` 實際上是 JSONL event stream，不是單一 JSON object
- isolation 主策略仍偏向猜測式 env var，而非文件化 CLI flags
- web 前端的 cost 顯示仍未納入變更清單

## 優點

- 整體設計比前版更貼近真實 CLI 行為，而不是抽象假設。
- 風險項目已經具備可實作層級的辨識能力。
- 對 `codex` 的 repo-check 問題處理方向是正確的。
- structured output + cleaner fallback 的主次關係已經合理。
- 文獻敘事已經大致回到安全範圍。

## 問題與風險

### HIGH 1. `GeminiAdapter` 的 `-p` 用法很可能仍然錯誤

v5 把 `GeminiAdapter.build_command()` 寫成：

```python
["gemini", "-p", "-m", self.model, "-o", "json"]
```

但依本機 `gemini --help`，`-p/--prompt` 是一個**需要字串值**的 option：

- `-p, --prompt  Run in non-interactive mode with the given prompt. Appended to input on stdin (if any).`

這表示：
- `-p` 不是像 `claude -p` 那樣的單純布林旗標
- 目前 v5 的寫法極可能把 `-m` 當成 prompt 值
- `warmup()` 也有同樣問題

這是實作阻塞級問題，不是小細節。

**建議修正**
- 在 Phase 1 最前面新增一個明確 task：
  - 實測 `gemini` 的正確 headless JSON invocation
- 在計畫書中暫時把 `GeminiAdapter` pseudocode 改成：
  - `needs verification`
  - 不要先寫死目前這個命令型態

### HIGH 2. `CodexAdapter` 對 `--json` 輸出格式的假設仍過於樂觀

本機 `codex exec --help` 顯示：
- `--json  Print events to stdout as JSONL`

這代表 `codex` 不是輸出單一 JSON object，而是**逐行事件流**。  
目前 v5 的 `_parse_json_or_fallback(stdout)` 類思路，若仍以 `json.loads(full_text)` 為模型，會直接失效。

你真正需要的是：
- 逐行 parse JSONL
- 找出最後 assistant/final 類事件
- 拼出最後回答

**建議修正**
- 在 `CodexAdapter` 設計中明確區分：
  - single-JSON provider
  - JSONL-event-stream provider
- 將 parser 介面從「parse JSON object」改成「parse structured output stream」

### HIGH 3. isolation 主策略仍未充分利用文件化旗標

目前 v5 的 isolation 仍以：
- `cwd=tmpdir`
- env vars

為主，文件化 flag 反而沒有成為主方案。這不夠穩。

實際上目前可確認的 CLI 正式旗標包括：
- `claude --bare`
- `claude --no-session-persistence`
- `claude --tools ""`
- `codex --ephemeral`
- `codex --sandbox read-only`
- `gemini --approval-mode plan`

既然這些旗標是正式介面，就應該優先使用，而不是把未證實的 env var 放在最核心的位置。

**建議修正**
- 重寫 isolation 段落，改成：
  - 正式 CLI flags = primary isolation layer
  - `cwd=tmpdir` = secondary isolation layer
  - env vars = opportunistic hardening only

### MEDIUM 4. `cost_mode` 的 downstream 影響仍低估，尤其是 web UI

v5 已補：
- `analytics.py`
- `diff.py`

但現有前端仍把 cost 當數字顯示與累加：
- `magi/web/static/index.html`

目前 UI 仍預設：
- `$0.000`
- `$0.000000`
- `updateCost()` 對 `cost_usd` 直接加總

這代表即使後端加入 `cost_mode="unavailable"`，前端仍可能顯示成假性的免費成本，而不是 `N/A`。

**建議修正**
- File Change Summary 補列：
  - `magi/web/static/index.html`
- WebSocket payload 或前端顯示邏輯明確支援：
  - `cost_mode="unavailable"`
  - node-level cost unavailable

### MEDIUM 5. `MagiProviderNotFoundError` 的偵測仍帶有 Unix 假設

v5 仍把：
- exit 127

當成 CLI not found 的關鍵映射。這在 Windows 下不夠穩。更常見的情況是：
- `create_subprocess_exec()` 直接丟 `FileNotFoundError`

你雖然有 preflight `shutil.which()`，但錯誤映射描述仍應調整，避免未來實作誤導。

**建議修正**
- 文件改成：
  - preflight: `shutil.which()`
  - runtime: `FileNotFoundError`
  - exit code mapping only as secondary fallback

### MEDIUM 6. Gemini 的 `Virtual Effort` 仍值得收斂

v5 把 Gemini 沒有原生 effort flag 的缺口補成：
- `Virtual Effort`
- 在 high/max 時自動注入 chain-of-thought 指令

這個設計風險在於：
- 不一定提升推理品質
- 可能只增加輸出冗長
- 會讓 structured output parsing 更脆弱

更穩健的方式是把 Gemini 的 effort 語意限定為：
- answer depth
- output strictness
- context richness

而不是把它描述成 reasoning control 的等價替代。

**建議修正**
- 將 `Virtual Effort` 改名為更保守的語意，例如：
  - `response_depth_profile`
  - 或 `prompt-level depth augmentation`

## 建議修改

### 一、先實測 Gemini 真正的 headless invocation

這件事不應再留在 abstract design。  
建議在文件中加入：

1. 實測 `gemini --prompt "text" -m ... -o json`
2. 實測 `stdin + --prompt ""`
3. 驗證 `warmup()` 最小正確命令

若未驗證前，不要把目前 pseudocode 當已定案方案。

### 二、把 Codex parser 改成 JSONL event parser

建議文件直接改成：

```python
class CodexAdapter:
    def parse_output(self, stdout, stderr, returncode):
        # parse JSONL events line by line
        # locate final assistant message
        ...
```

而不是延續 generic `_parse_json_or_fallback()`

### 三、重寫 isolation 策略排序

建議新的優先順序：

1. documented CLI flags
2. isolated cwd
3. env var hardening

### 四、補列 web/static 影響面

除了 `web/server.py` 之外，請直接補進：
- `magi/web/static/index.html`

否則 v5 對 cost 顯示的修正仍不完整。

### 五、修正 not-found error 敘述

將 `exit 127` 降為次要路徑，Windows 主路徑改為：
- `shutil.which()`
- `FileNotFoundError`

## 替代方案

若你想把 v5 進一步收斂成最穩 MVP，可以考慮：

- `Phase 1A`: 只做 `claude` + `codex`
- `Phase 1B`: `gemini` 完成 headless/JSON invocation 驗證後再加入 `cli_multi`

這比現在直接把 `gemini` 放進第一版預設節點更穩。

## 最終審批

**審批結果: 有條件通過**

## 通過條件

- 驗證並修正 `GeminiAdapter` 的 headless `-p/--prompt` 用法
- 將 `CodexAdapter` parser 改成 JSONL event stream 模型
- isolation 策略改為「文件化 flag 優先」
- File Change Summary 補列 `magi/web/static/index.html`
- 將 not-found error 偵測描述調整為 Windows 相容版本

完成以上幾點後，v5 才適合進入實作。
