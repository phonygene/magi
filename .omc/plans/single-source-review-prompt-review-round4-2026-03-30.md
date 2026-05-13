# MAGI `single-source-review-prompt.md` 第四輪審批與修正建議

**審閱日期:** 2026-03-30  
**審閱對象:** `C:\Projects\magi\.omc\plans\single-source-review-prompt.md`  
**版本:** v6

## 審批結論

**結論: 有條件通過**

v6 review prompt 已經是很成熟的一版。  
它成功把幾個最重要的實作風險顯式化：
- `gemini --prompt` 需要值
- `codex --json` 是 JSONL event stream
- isolation 要以正式 CLI flags 為主
- frontend 也受 `cost_mode` 影響

但這一輪又出現兩個值得直接拿去請外部 reviewer 幫你驗證的新問題：
- `claude --bare` 是否會破壞 CLI 訂閱登入
- `CodexAdapter` 若把 output file path 存在 adapter instance，併發下是否會產生 race condition

這兩題都應直接加進下一版 prompt。

## 評分

**整體評分: 9 / 10**

## v5→v6 改進評估

### 有效修改

- prompt 終於把 `gemini --prompt` 與 `codex --json` 的真實語義拉進來了
- `structured output` 與 parser 問題變得具體可審
- isolation 問題已能逼 reviewer 比較真正的 CLI flags
- `web/static/index.html` 的 cost surface 已被納入
- `v5→v6` 的差異摘要足夠清楚

### 仍不足之處

- 還沒要求 reviewer 檢查 `claude --bare` 對 OAuth/keychain 登入的副作用
- 還沒要求 reviewer 檢查 adapter-level mutable state 是否會在並發 ask/bench 下出問題
- 還沒要求 reviewer 判斷 Gemini 是否應降級為 Phase 1B / experimental

## 優點

- 這版 prompt 的工程精度已經很高，幾乎可直接拿去做架構審查。
- reviewer 不太可能再忽略 `gemini` 和 `codex` 的 CLI 語義差異。
- 問題結構明確，且與 codebase reality 對齊。
- 已能把「設計是否正確」與「命令是否真的能跑」一起審。
- 對 downstream integration 的關注面已接近完整。

## 問題與風險

### HIGH 1. prompt 還沒點名 `claude --bare` 與登入模式的潛在衝突

本機 `claude --help` 顯示：
- `--bare` 會跳過 keychain / OAuth 讀取
- 認證改為依賴 API key 或 `--settings`

這與 CLI-native mode 的核心賣點之一：
- 沒有 provider API key，改靠 CLI 訂閱/登入

可能互相衝突。  
如果不把這題丟給 reviewer，你可能會收到一堆 isolation 建議，卻沒人提醒你最根本的登入問題。

**建議修正**
- 在 isolation 區塊新增：
  - `Does claude --bare disable OAuth/keychain-based login in a way that breaks the "no provider API key" goal? If so, what documented isolation flags are safer defaults?`

### HIGH 2. prompt 還沒要求 reviewer 檢查 adapter 的並發安全性

v6 的設計開始使用：
- `codex -o <tmpfile>`

這本身沒問題，但如果 output path 被保存在 adapter instance 上，就會和 MAGI 的並發用法產生 race condition，尤其是 benchmark 會在同一 engine / 同一組 nodes 上同時跑多題。

這是非常實作級、也非常真實的風險。

**建議修正**
- 新增：
  - `If adapters store per-call state such as temp output file paths on the adapter instance, is the design concurrency-safe under benchmark or concurrent ask workloads?`

### MEDIUM 3. prompt 應更直接問 `codex -o` 與 `--json` 是否應拆成兩種 mode

目前 prompt 有問 `-o` 是否穩，但還不夠明確。更值得 reviewer 回答的是：
- 這兩種輸出模式是否應是兩個明確 execution mode，而不是單一 parser 的 fallback

**建議修正**
- 新增：
  - `Should Codex outfile mode (-o) and JSONL mode (--json) be modeled as two distinct execution modes rather than a single parser fallback chain?`

### MEDIUM 4. prompt 可以更直接要求 reviewer 判斷 Gemini 是否應降級為 experimental

雖然現在已經問了 Gemini 的穩定性，但還沒把真正的產品決策問清楚：
- 在前置驗證未完成前，Gemini 是否還適合做 `cli_multi` 預設第三節點？

**建議修正**
- 新增：
  - `Should Gemini remain an experimental / Phase 1B backend until prompt+stdin semantics and JSON output are validated?`

## 建議修改

### 一、補三條高價值問題

建議直接加入：

1. `Does claude --bare disable OAuth/keychain-based login and therefore conflict with the "no provider API key" goal?`
2. `Is adapter-level mutable state (e.g. temp output file paths) concurrency-safe under benchmark and concurrent ask workloads?`
3. `Should Codex outfile mode (-o) and JSONL mode (--json) be modeled as separate execution modes instead of a fallback chain?`

### 二、把 Gemini 的產品定位也交給 reviewer

新增：

- `Should Gemini remain experimental / Phase 1B until its --prompt + stdin semantics and JSON output path are fully validated?`

## 替代方案

如果你希望第四輪 reviewer 更聚焦，可以把 prompt 收斂成四個主問題：

- Claude isolation 與 auth 是否相容
- Codex parser / outfile mode 應如何建模
- Adapter 是否能支撐併發 workloads
- Gemini 是否應降級為 experimental backend

這會比現在更容易拿到可以直接用來收斂 v7 的意見。

## 最終審批

**審批結果: 有條件通過**

## 通過條件

- 補問 `claude --bare` 與登入模式衝突
- 補問 adapter-level mutable state 的並發安全性
- 補問 `codex -o` 與 `--json` 是否應拆成兩種 mode
- 補問 Gemini 是否應降級為 experimental / Phase 1B

完成以上幾項後，這份 review prompt 就很適合進入下一輪跨模型審閱。
