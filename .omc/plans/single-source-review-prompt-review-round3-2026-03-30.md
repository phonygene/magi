# MAGI `single-source-review-prompt.md` 第三輪審批與修正建議

**審閱日期:** 2026-03-30  
**審閱對象:** `C:\Projects\magi\.omc\plans\single-source-review-prompt.md`  
**版本:** v5

## 審批結論

**結論: 有條件通過**

v5 review prompt 已經非常接近可直接拿去做跨模型第三輪審閱。它比前版更成熟的地方在於：
- 把 structured output 與 cleaner 的主次關係講清楚
- 把 `codex` repo-check 衝突變成顯式問題
- 補上 hybrid cost schema 與 downstream surface
- 保留了足夠多的 codebase reality，而沒有再退回抽象空談

但它還漏了兩個目前最實作級、最值得外部 reviewer 幫你驗證的問題：
- `gemini -p` 的實際語義與正確 headless 用法
- `codex --json` 是 JSONL event stream，parser 該怎麼設計

## 評分

**整體評分: 8.5 / 10**

## v4→v5 改進評估

### 有效修改

- 已把 structured output 升格成 reviewer 必須審的主題
- 已把 `codex` dual-mode 與 repo-check 納入 prompt
- 已把 downstream cost surface 擴展到 analytics / diff / cli
- 背景對論文的 framing 已明顯收斂
- v4→v5 變更摘要很有助於 reviewer 快速聚焦

### 仍不足之處

- prompt 還沒把 `gemini -p` 需要 prompt value 這個可能的介面誤解拎出來
- prompt 還沒把 `codex --json = JSONL events` 這件事明確交給 reviewer 檢查
- isolation 區塊還可以更明確地要求 reviewer 比較「正式 CLI flags vs env vars」

## 優點

- 問題設計已非常接近工程審查，而不只是產品 brainstorm。
- reviewer 幾乎不會再忽略 hidden contract、cost surface、workspace contamination 這幾個主題。
- 這版 prompt 的資訊密度高，但 framing 基本正確。
- 已經能引導 reviewer 評估 v5 是否真的解掉 v4 的問題，而不是重新從零審。
- 對 `codex` 的實作細節問題掌握已比前版完整。

## 問題與風險

### HIGH 1. prompt 還沒點名 `gemini -p` 的 value semantics

依本機 `gemini --help`：
- `-p, --prompt` 是需要值的 option，不是單純開啟 headless 模式的布林旗標

目前 review prompt 雖然寫了：
- `stdin pipe`
- `-o json`

但沒有直接要求 reviewer 檢查：
- `gemini` 是否允許 `-p` 不帶值
- `stdin` 與 `--prompt` 的組合語意到底是什麼

這是一個足以讓整個 `GeminiAdapter` 寫錯的關鍵問題。

**建議修正**
- 在 `Structured Output` 或 `Subprocess 安全與穩定性` 中新增：
  - `Gemini CLI's --prompt appears to require a value. Is the proposed stdin-based invocation valid, and what is the correct headless JSON invocation pattern?`

### HIGH 2. prompt 還沒明確要求 reviewer 檢查 `codex --json` 的 JSONL 事件流特性

目前 prompt 有問 structured output 是否穩定，但還不夠具體。  
`codex --json` 不是單一 JSON object，而是 JSONL event stream。這會直接影響：
- `parse_output()` 設計
- fallback 邏輯
- final message extraction

若不把這點點名，reviewer 可能只回「JSON output 比 cleaner 穩」這種高層答案，無法真正幫你收斂 parser 設計。

**建議修正**
- 新增：
  - `Codex --json emits JSONL events rather than a single JSON object. What is the right parser shape for extracting the final assistant answer robustly?`

### MEDIUM 3. isolation 區塊仍可再明確要求 reviewer比較正式 flag 與 env var

目前 prompt 問了：
- `cwd=tmpdir + env vars 是否足夠`

但還沒明確要求 reviewer 去比較：
- `claude --bare`
- `claude --no-session-persistence`
- `claude --tools ""`
- `codex --ephemeral`
- `codex --sandbox read-only`
- `gemini --approval-mode plan`

這會讓 reviewer 可能錯過「應優先使用正式 CLI flags」這個設計收斂點。

**建議修正**
- 在 isolation 區塊新增：
  - `Should documented CLI isolation/session flags be the primary mechanism, with cwd/env isolation only as secondary hardening?`

### MEDIUM 4. downstream impact 仍可再補到 web UI

prompt 現在已把：
- analytics
- diff
- trace

納入 downstream surface，但還沒明確點到：
- `web/static/index.html`

這是目前成本顯示最容易漏修的地方。

**建議修正**
- 在 cost 區塊或遺漏項目補一題：
  - `Does the NERV dashboard frontend also need explicit handling for unavailable cost, rather than only backend/reporting changes?`

## 建議修改

### 一、補兩條最關鍵的 implementation-review 問題

請直接新增：

1. `Gemini CLI's --prompt appears to require a value. Is the proposed stdin-based invocation valid, and what is the correct headless JSON invocation pattern?`
2. `Codex --json emits JSONL events rather than a single JSON object. What parser design should MAGI use to extract the final answer robustly?`

### 二、加強 isolation 問題的方向性

新增：

- `Should MAGI prioritize documented CLI flags (--bare, --no-session-persistence, --ephemeral, --sandbox, --approval-mode plan) over custom env vars for isolation?`

### 三、把 web UI 納入 downstream surface

新增：

- `Which frontend surfaces (e.g. NERV dashboard cost widgets) also need updates once cost_mode="unavailable" is introduced?`

## 替代方案

如果你希望第三輪 reviewer 更聚焦，可以把 prompt 再收斂成四個主問題：

- `GeminiAdapter` 是否可行
- `CodexAdapter` parser 應如何設計
- isolation 機制應如何排序
- CLI Multi 是否值得把 gemini 放入預設第三節點

這會比現在更容易換來具體、可執行的審閱意見。

## 最終審批

**審批結果: 有條件通過**

## 通過條件

- 補問 `gemini -p/--prompt` 的 value semantics
- 補問 `codex --json` 的 JSONL event parser 設計
- 補問正式 CLI flags 是否應優先於 env var isolation
- 補問 NERV dashboard frontend 的 `cost_mode` 影響

完成以上幾點後，這份 review prompt 就適合進入第三輪跨模型審閱。
