# MAGI `single-source-review-prompt.md` 第二輪審批與修正建議

**審閱日期:** 2026-03-30  
**審閱對象:** `C:\Projects\magi\.omc\plans\single-source-review-prompt.md`  
**版本:** v4

## 審批結論

**結論: 有條件通過**

這份 review prompt 已比前版成熟很多，已經具備：
- 正確暴露 hidden node contract
- 顯式詢問 session / workspace isolation
- 把 Phase 1 stateless 納入問題設計
- 用 v3→v4 diff 幫 reviewer 快速聚焦

作為跨模型第二輪審閱 prompt，這版基本可用。  
但它仍然保留幾個會讓 reviewer 偏題或錯估風險的 framing 問題，建議在送出前再修一次。

## 評分

**整體評分: 8 / 10**

## v3→v4 改進評估

### 有效修改

- 已把 hidden node contract 顯式化，這是關鍵提升
- 已把 session isolation 提升為主要審閱題，而不是旁支風險
- 已把 Phase 1 stateless 決策說清楚
- 已把 review 問題收斂到真正會影響落地的 integration 面
- 已加入 v3→v4 主要修改對照，讓 reviewer 更容易做增量審閱

### 仍不足之處

- 背景段落對 `Debate or Vote` 與 ICE framing 的關聯仍然偏強
- prompt 還沒把 structured output vs cleaner 這個高價值設計選項拉出來問
- 還沒把 `codex` 在 isolated cwd 下的 repo-check 衝突寫成明確審閱點

## 優點

- 問題設計已經具備工程深度，而不是只有高層哲學討論。
- reviewer 幾乎不可能再忽略 hidden dependency 與 session contamination 問題。
- 這版 prompt 對 review 成本友善，因為背景與增量變更都整理好了。
- 已把 memory 問題改到更合理的層次，不再預設 transcript injection。
- cost / observability 區塊比前版實用得多。

## 問題與風險

### HIGH 1. 背景對論文的 framing 仍稍微過頭

目前仍寫：

- `ICE error-detection framing 優於 persuasion-based debate。MAGI 採用 ICE framing 的核心思路。`

即使這比前版好，仍可能讓 reviewer 誤以為論文直接驗證了 MAGI 的具體 prompt 設計。這個 framing 還可以再保守一點。

**建議修正**
- 改成：
  - `The paper motivates skepticism toward debate-only gains and suggests targeted interventions may help. MAGI's ICE-style prompt is an implementation choice inspired by that direction.`

### HIGH 2. prompt 尚未要求 reviewer 評估 structured output 方案

v4 已新增 `CliOutputCleaner`，但 prompt 並沒有直接問：
- 是否應優先使用 `--json` / structured output
- cleaner 是否應該只是 fallback

這會讓 reviewer 可能只在 regex 層面給建議，而錯過一個更根本的簡化方向。

**建議修正**
- 在「架構合理性」或「Subprocess 安全與穩定性」新增一題：
  - `Should MAGI prefer structured JSON output from each CLI instead of plain text + CliOutputCleaner?`

### HIGH 3. prompt 尚未點出 `codex` isolation 與 repo-check 的衝突

目前 prompt 問了 isolation，也問了 temp file，但沒有明確提醒 reviewer：
- `codex exec` 在 repo 外可能需要 `--skip-git-repo-check`

這是一個具體、可預見、且很可能阻塞實作的問題。若 prompt 不點出來，reviewer 未必會主動想到。

**建議修正**
- 在「Session & Workspace Isolation」或「Subprocess 安全與穩定性」補一題：
  - `If isolation uses cwd=tmpdir, how should Codex handle its git-repo check requirement?`

### MEDIUM 4. `cost_mode` 問題仍可再深入 hybrid 情境

目前 prompt 問：
- `cost_mode` 是否足夠？

這是對的，但可以再尖一點。最值得問的是：
- hybrid mode 下，若部分 node measured、部分 unavailable，Decision schema 該如何表示？

**建議修正**
- 補成：
  - `Is a single Decision.cost_mode sufficient, or does hybrid mode require per-node cost breakdown / partial availability semantics?`

### MEDIUM 5. 缺少對 large prompt fallback 真正技術邊界的提問

現在有問 temp file 是否穩健，但還沒明確區分：
- command-line argument length limit
- stdin/pipe 傳輸

這會讓 reviewer 比較難指出「哪一類 provider 才真的需要 fallback」。

**建議修正**
- 把問題改成：
  - `Should fallback be provider-specific, with argument-based providers switching to stdin, rather than a generic temp-file strategy?`

### LOW 6. 可再補一題關於 downstream integration surface

雖然 prompt 已問 hidden node deps，但尚未明確提醒 reviewer：
- `analytics.py`
- trace formatting
- CLI output rendering

這些在 cost_mode / unavailable cost 下也會受影響。

**建議修正**
- 在最後的「遺漏項目」加一題：
  - `Which downstream reporting / analytics surfaces also need changes once cost_mode is introduced?`

## 建議修改

### 一、把 structured output 變成必問題

新增：

- `All three CLIs appear to support structured or JSON-like output modes. Should MAGI use those as the primary path and keep CliOutputCleaner only as a fallback?`

### 二、點名 `codex` repo-check 與 isolation 衝突

新增：

- `If running Codex in cwd=tmpdir for isolation, does the design need --skip-git-repo-check or a different isolation model?`

### 三、把研究基礎再收斂一點

讓 reviewer 不會再誤以為：
- `ICE-style prompt = paper-proven mechanism`

### 四、強化 hybrid cost schema 問題

把 `cost_mode` 從一般 schema 問題，提升成：
- mixed observability / mixed backend aggregation 問題

## 替代方案

如果你希望第二輪 reviewer 更聚焦，可以把 prompt 再壓縮成「三大審核軸」：

- isolation 是否真能成立
- adapter + structured output 是否比 cleaner-based 設計更穩
- cli_multi 是否值得引入 gemini 作為預設第三節點

這樣會比現在更容易得到尖銳、少廢話的評語。

## 最終審批

**審批結果: 有條件通過**

## 通過條件

- 再保守化論文與 ICE framing 的背景描述
- 補問 structured output vs cleaner
- 補問 `codex` repo-check 與 isolation 衝突
- 補問 hybrid mode 下的 cost schema

完成這幾個修正後，這份 review prompt 就適合進入第二輪跨模型審閱。
