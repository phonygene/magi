# REFINE Mode Proposal V3r3 — 審閱報告（Codex Round 3）

**審閱日期**: 2026-04-02  
**審閱對象**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md`  
**審閱 Prompt**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3r3-review-prompt.md`  
**Verdict**: `REVISE`

---

## Overall

`V3r3` 已經比前一輪更接近可實作版本，尤其是：

- 移除仲裁後，state machine 明顯簡化
- `GUIDED` 被提升為 P0，符合實際工作流
- `terminal_status` 與 REFINE 專用 UI contract 是正確方向
- `proposal_text` 與 `log_round()` 讓 audit trail 真的可用

但這版仍有幾個會影響正確性或落地穩定性的問題，主要集中在：

- 收斂判定與 reviewer 沉默的語義
- Collator 與既有 3-node 架構的整合方式
- GUIDED callback 與 parse-error 對 UI 的影響

---

## Findings

### 1. [critical] `NO_NEW_OBJECTIONS` 仍可能造成偽收斂，與 `active_issues()` 語義互相衝突

**Issue**

提案同時定義了：

- `active_issues() = open + partial_resolved`
- `partial_resolved` 不視為已解決
- 只要「本輪所有 reviewer 回傳空 objection list」就可 `terminal_status="converged"`

這三者放在一起會產生語義衝突：

- 某 issue 仍是 `partial_resolved`
- reviewer 因為接受 primary 的 reasoning 而本輪不再提出
- 系統就會以 `NO_NEW_OBJECTIONS` 判定 `converged`
- 但 tracker 仍保留 active issue

這代表「沉默 = 接受」與「partial_resolved 仍未解」沒有被真正統一成單一狀態轉換。

**Evidence**

- `active_issues()` 含 `partial_resolved`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L416)
- `partial_resolved` 不被視為已解決: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L418)
- `NO_NEW_OBJECTIONS -> terminal_status="converged"`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L728)
- reviewer 「沉默 = 接受」: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L990)

**Suggestion**

- 二選一，且要明寫：
  1. reviewer 本輪未再提出的 `partial_resolved` issue，自動轉為 `resolved`
  2. 或 `NO_NEW_OBJECTIONS` 只能在 `active_issues()==0` 時成立，否則最多是 `stable_no_reopen`

如果不先統一，這個協議仍然會在「形式上無新異議，但 tracker 還有 active issue」時產生偽收斂。

---

### 2. [major] Collator 的整合方式仍與 MAGI 現有 3-node 架構不夠貼合

**Issue**

提案把 REFINE 描述成 `1 主 + N 審 + 1 彙整`，並在 `RefineConfig` 加了 `collator_model`。但現有 MAGI engine 仍是固定三個 node：

- `melchior`
- `balthasar`
- `casper`

文件沒有真正說清楚 Collator 到底是：

- 第四個獨立模型
- 暫時建立的 ad-hoc call
- 還是重用現有「最低消耗 node」

如果是重用既有 reviewer node，會出現角色混用：

- 同一模型既是 reviewer，又是 collator
- cost attribution、failure attribution、persona 隔離都會變模糊

如果是第四個模型，則已經超出目前 MAGI「3-LLM engine」的核心運作假設。

**Evidence**

- 新角色 `Collator`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L163)
- `collator_model: str | None = None  # 使用最低消耗 node`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L225)
- 現有 engine 固定初始化三個 node: [engine.py](C:\Projects\magi\magi\core\engine.py#L37)

**Suggestion**

- 明確選定一種模式，不要留語義空間：
  - `Collator` 是獨立 ad-hoc call，不屬於 `nodes`
  - 或 `Collator` 明確重用某個 node，但以「無 persona、專用 prompt、單獨 failure/reporting」方式執行
- 同時補上：
  - collator failure policy
  - collator cost attribution
  - collator 是否進入 `failed_nodes`

在目前版本，Collator 是好方向，但整合 contract 還不夠落地。

---

### 3. [major] GUIDED callback contract 仍不完整，容易死鎖且無法完整表達 `partial` 類覆核

**Issue**

`guided=True` 時，`on_user_review` 幾乎成了 P0 核心路徑，但文件仍缺少幾個必要規則：

- callback 缺失時怎麼辦
- callback timeout / exception 怎麼辦
- `override` 只允許 `{issue_key: new_verdict}`，無法攜帶 `severity_after`
- 若使用者把某一項改成 `partial`，沒有欄位表達剩餘嚴重度

這讓 GUIDED 分支雖然概念清楚，但 contract 還不夠精確。

**Evidence**

- `guided=True` 時 callback 必填: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L224)
- `UserAction.overrides` 只有 `issue_key -> new_verdict`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L251)
- `partial` 仍需要 `severity_after`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L355)
- Failure Handling 區段沒有 callback timeout / exception 規則: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L783)

**Suggestion**

- 把 `UserAction` 擴充成結構化 override：
  - `overrides: list[{issue_key, verdict, severity_after?, reasoning?}]`
- 補上 GUIDED failure policy：
  - callback missing → config error
  - callback timeout → `terminal_status="cancelled"` 或 `aborted`
  - callback exception → `degraded + abort_reason="user_review_failed"`

不補這一層，GUIDED 會是最容易在實作中卡死的分支。

---

### 4. [major] parse error 的 reviewer 仍可能在 UI 上被誤判為 approve

**Issue**

這版已經把 parse error 從 state machine 分離出來，方向正確。但在 UI contract 上還沒完全閉合：

- `compute_refine_votes()` 用「本輪是否提出 objection」判定 approve/reject
- `parse_error` reviewer 可能沒有 objection
- 目前 UI 只把 `failed_nodes` 顯示為 abstain
- parse error 並不等於 failed node

結果是：

- reviewer 不是 approve，只是 parse failed
- 但 UI 仍可能把它畫成綠燈 approve

**Evidence**

- parse error 第二次失敗只標記 parse_error: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L814)
- `compute_refine_votes()` 只看本輪 objection list: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L977)
- 現有 UI 只用 `failed_nodes` 決定 abstain: [index.html](C:\Projects\magi\magi\web\static\index.html#L933)

**Suggestion**

- 新增 reviewer-level terminal marker，例如：
  - `round_parse_errors: [node_name]`
  - 或 `reviewer_states: {name: approve|reject|abstain|invalid}`
- REFINE UI contract 要把 parse-error reviewer 顯示成 `abstain` 或 `invalid`，不能當 approve

這不是視覺細節，而是結果語義正確性。

---

### 5. [major] Collator prompt 對「同一問題但建議互斥」的情況仍可能丟失資訊

**Issue**

Collator 目前被要求：

- merge duplicates
- keep the clearest description
- combine suggestions

這在 reviewer 對「問題存在」有共識、但對「怎麼修」有分歧時，容易把互斥建議壓成單一 suggestion。Primary 最終看到的可能不再是兩個 alternative，而是一段被扁平化的折衷文字。

這種資訊損失與 bias，不是 judge/filter 才會發生，單純 consolidate 也會發生。

**Evidence**

- Collator prompt 要求 `combine suggestions`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L568)
- 輸出 schema 只有單一 `suggestion`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L575)

**Suggestion**

- Collator 輸出不要只留單一 `suggestion`
- 至少改成：
  - `suggestions: list[{reviewer, text}]`
  - 或 `alternatives: [...]`
- 若 suggestions 互相矛盾，collator 應標記 `conflicting_suggestions=true`，而不是強行合併

目前版本對「去重」有明確規則，對「保留建議分歧」則還不夠。

---

## Strengths

- `terminal_status` + REFINE 專用 UI 分支是正確改動，這比硬套 vote-mode 強很多。
- `GUIDED` 升為 P0 之後，協議終於更貼近你實際的工作流，而不是只停留在理想化自動流程。
- 移除仲裁和 `wontfix/escalated` 後，state machine 清楚很多。
- `proposal_text`、`decisions`、`user_overrides` 進 round trace，讓這套流程真的具備可審計性。

---

## Final Assessment

**Ready for implementation:** `NO`

這版已經接近 `ACCEPT-WITH-RESERVATIONS`，但還差最後幾個 contract 細節。  
如果把以下三件事補齊，我認為下一版很有機會升到可實作狀態：

1. 把 `NO_NEW_OBJECTIONS` 與 `active_issues()` 的語義統一，避免偽收斂。
2. 明確定義 Collator 是第四角色還是 ad-hoc call，並補上其 failure/cost contract。
3. 把 GUIDED 與 parse-error 的 reviewer 狀態做成正式 contract，而不是靠隱含推論。
