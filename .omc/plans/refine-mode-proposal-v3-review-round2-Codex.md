# REFINE Mode Proposal V3 — Round 2 審核報告（Codex）

**審核日期**: 2026-04-01  
**審核對象**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md`  
**審核方式**: 文件審核 + 對照現有 MAGI 程式碼  
**結論**: `REVISE`

---

## 核心結論

`v3r2` 已經補掉我上一輪提出的大多數結構缺口，整體成熟度明顯高於 `v3r1`。尤其是：

- `IssueState` 補上 `severity/category/latest_description`
- `Decision.confidence / votes / minority_report` 開始有明確 contract
- `proposal_text` 納入 round trace
- `best_round_note` 從 `minority_report` 拆出

但這版仍有幾個會直接造成**偽收斂**或**錯誤審批語義**的問題。它已經接近 `ACCEPT`，但還差最後一輪 state machine 與 UI contract 收斂。

---

## Findings

### 1. [critical] `partial_resolved` 被排除在 `open_issues()` 之外，會導致偽收斂

**問題**

這版定義：

- `open + partial -> partial_resolved`
- `open_issues()` 只回傳 `resolution == "open"`

這會讓「部分解決，但仍需追蹤」的 issue 從 convergence、confidence、minority_report 的主要查詢路徑中直接消失。結果是：

- `check_convergence()` 可能在仍有 `partial_resolved` issue 時判定 `all_resolved`
- `compute_refine_confidence()` 會低估未解決風險
- `compute_refine_votes()` / `compute_refine_minority_report()` 看不到這些 issue

也就是說，`partial_resolved` 在目前設計裡不是「部分未解」，而是接近「被隱藏的已解」。

**證據**

- `open + partial -> partial_resolved`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L383)
- `open_issues()` 僅回傳 `resolution='open'`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L401)
- `check_convergence()` 依賴 open issues: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L854)
- `compute_refine_confidence()` 依賴 `tracker.open_issues(...)`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L493)
- `compute_refine_votes()` 只看 `resolution == "open"`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L536)
- `compute_refine_minority_report()` 只看 `resolution == "open"`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L575)

**建議**

- 你必須二選一：
  1. `partial_resolved` 視為 unresolved，所有 `open_issues()` 類查詢都要包含它
  2. 取消 `partial_resolved`，改用 `open` + `severity_after` / `progress_note`

在目前設計下，我更建議第 1 種，因為它保留了狀態細節但不破壞 convergence 邏輯。

---

### 2. [critical] `escalated + override/compromise -> resolved` 會在主模型尚未修訂前提前結案

**問題**

仲裁結果若是：

- `override`
- `compromise`

文件現在直接把 issue 轉成 `resolved`。這在流程上是錯的，因為 judge 只是裁定「應採納 reviewer / compromise」，不是代表 proposal 已經被 primary 實際修改、也不是代表 reviewer 已驗證修改成功。

若此時直接 `resolved`，會出現：

- tracker 提前閉單
- convergence 可能被提前滿足
- best_round / confidence / minority_report 都基於尚未落地的修正做判斷

這是典型的「裁決 = 已修復」混淆。

**證據**

- `escalated + override -> resolved`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L387)
- `escalated + compromise -> resolved`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L388)
- arbitration prompt 只要求 judge 裁定，不代表 primary 已更新 proposal: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L746)

**建議**

- `override` / `compromise` 的結果不應直接 `resolved`
- 應改成：
  - `escalated -> open`，附上 `arbitration_result`
  - 下一輪 primary 必須依仲裁結果修訂 proposal
  - reviewer 或驗證步驟確認後，才可 `resolved`

否則 escalation 會變成「繞過修訂直接關單」。

---

### 3. [major] `distinct_reviewers` 是歷史集合，卻被拿來當前 reviewer 立場來源，會誤判 dissent

**問題**

`compute_refine_votes()` 與 `compute_refine_minority_report()` 都用：

- `resolution == "open"`
- `node.name in issue.distinct_reviewers`

來判定某 reviewer 是否仍在反對。

但 `distinct_reviewers` 在資料模型上是「曾經提過這個 issue 的 reviewer 集合」，不是「目前仍堅持此 issue 的 reviewer 集合」。這代表：

- reviewer A、B 曾一起提出 issue
- primary 修正後，B 已接受、A 仍不滿
- 只要 issue 還是 open，`distinct_reviewers` 仍同時包含 A、B
- 系統就會把 B 也標成 reject / dissent

這會直接污染：

- `votes`
- `minority_report`
- UI 上的 lamp 狀態

**證據**

- `IssueState.distinct_reviewers` 定義: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L345)
- `compute_refine_votes()` 使用 `distinct_reviewers`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L536)
- `compute_refine_minority_report()` 使用 `distinct_reviewers`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L575)

**建議**

- 新增一個與歷史集合分離的欄位，例如：
  - `current_reviewers`
  - `last_raised_by`
  - `active_dissenters`
- `distinct_reviewers` 保留作為審計與 escalation 統計
- `votes` / `minority_report` 只能依 `current_reviewers` 或本輪 validation 結果生成

---

### 4. [major] `reconcile_cross_round()` 只對 `open issues` 比對，無法穩健支撐 reopen 語義

**問題**

文件說：

- resolved / partial_resolved issue 之後若又收到新 objection，要 `reopened -> open`

但 `reconcile_cross_round()` 只會拿新 objection 去對照 tracker 裡的 **open issues**。這代表若 issue 已經被標成 `resolved` 或 `partial_resolved`，之後因 key 漂移、section renumber、short label 改寫而重現時，reconcile 可能找不到舊 issue，直接建立一個新 issue key，而不是 reopen 舊 issue。

換句話說，你雖然定義了 reopen 狀態，但 cross-round matching 沒把 reopen 的 lookup path 補完整。

**證據**

- `reconcile_cross_round()` 只比對所有 open issues: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L803)
- resolved / partial_resolved 新 objection 應 reopen: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L384)
- `upsert()` reopen 的前提是已知是同一 issue: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L393)

**建議**

- `reconcile_cross_round()` 不應只查 open issues
- 至少要查：
  - open
  - partial_resolved
  - resolved（近期關閉）
- 然後依分數高低決定：
  - reattach to open
  - reopen resolved issue
  - create new issue

若只查 open，reopen 在 key 漂移情境下依然不可靠。

---

### 5. [major] 目前 `votes` 的 UI 相容映射，會把非正常停止誤顯示成 `APPROVED`

**問題**

你現在的 `votes` 設計是：

- primary 永遠等於 ruling
- reviewer 無 open issues 就等於 ruling
- reviewer 有 open issues 就給 dissent summary

而現有 UI 的 verdict 判定是：

- `approveCount >= 2` 就顯示 `APPROVED`

這會在下列情境出現錯誤語義：

- `max_rounds` 停止，但仍有 1 位 reviewer 留下重大異議
- `budget_exceeded` 停止，但只剩 1 位 reviewer dissent
- `cancelled` 或部分 degraded 結束，但仍有 2/3 vote 映射成 approve

結果 UI 會顯示「APPROVED — 2 of 3 agree」，但實際上這次 run 並不是正常收斂，只是被迫中止。

**證據**

- `compute_refine_votes()` 的 approve/reject 映射: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L523)
- UI `approveCount >= 2` 即顯示 approved: [index.html](C:\Projects\magi\magi\web\static\index.html#L952)
- 非正常停止原因在 `abort_reason`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L460)

**建議**

- 不要再假設 `votes` 可以單獨承載 REFINE 的 verdict semantics
- 至少新增一個明確欄位供 UI 判斷：
  - `refine_summary["terminal_status"] = converged|threshold|max_rounds|budget|cancelled|aborted`
- 或在 dashboard 端對 `protocol_used == "refine"` 採專用 verdict 規則，而不是繼續用 vote-mode 的燈號規則硬套

這點不是美觀問題，而是產品語義問題。

---

## Strengths

- 這版已經不再是「概念很多、contract 模糊」的狀態，現在大多數關鍵介面都被明文化了。
- `proposal_text` 放進 round trace 是正確決定，讓 audit / rollback / cross-model re-review 都真正可行。
- `SECTION_ID` 與 `reconcile_cross_round()` 的引入，至少已經把 issue identity 問題從「未處理」提升到「可調參的明確策略」。
- `minority_report` 與 `best_round_note` 的語義拆分是對的。

---

## 建議的升級條件

這版若要升級為 `ACCEPT`，我建議至少再補完這 3 件事：

1. 修正 `partial_resolved` 的查詢與 convergence contract，避免它被當成隱形已解問題。
2. 修正 escalation state machine，`override/compromise` 不能直接 `resolved`。
3. 把「歷史提出者」與「當前 dissent 者」分離，並讓 UI 不再單靠 vote-style 映射判定 REFINE verdict。

---

## 最終判定

`V3r2 = 高度成熟，但仍未達可直接實作`

現在剩下的問題已經不是大範圍架構發散，而是幾個非常核心的狀態語義錯誤。  
如果把這一輪指出的 state machine / UI contract 問題補齊，我認為下一版很有機會達到 `ACCEPT`。
