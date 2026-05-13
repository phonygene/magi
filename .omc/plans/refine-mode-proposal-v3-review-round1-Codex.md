# REFINE Mode Proposal V3 — Round 1 審核報告（Codex）

**審核日期**: 2026-04-01  
**審核對象**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md`  
**審核方式**: 文件審核 + 對照現有 MAGI 程式碼  
**結論**: `REVISE`

---

## 核心結論

`v3` 比 `v2` 明顯更成熟，尤其是 scope 收斂到 Core REFINE、移除 reviewer promotion、把 parse failure 與 reject 分離、把 trace contract 明文化，這些都是真正的前進。

但這版仍有幾個結構性缺口沒有閉合。它們不是文案層小問題，而是會直接影響實作正確性、Decision 相容性或 round state 的真實語義。換句話說，`v3` 已經接近可實作，但還不能視為「可以直接開工而不再出現架構返工」。

---

## Findings

### 1. [critical] `IssueState` 缺少 `severity`，但收斂與最佳輪評分都依賴它

**問題**

`IssueState` 的欄位定義沒有 `severity`，但後續多個核心演算法都直接依賴 issue severity：

- `open_issues(min_severity)` 需要做 severity filtering
- `check_convergence()` 的 `threshold_met` 需要判斷「僅剩 minor」
- `track_best_round()` 需要計算 `critical_open / major_open / minor_open`

目前文件中的資料模型與演算法彼此不相容，這不是實作細節，而是 schema 本身不足。

**證據**

- `IssueState` 欄位沒有 `severity`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L338)
- `open_issues(min_severity)` 需要 severity: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L369)
- `check_convergence()` 需要區分 minor: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L632)
- `track_best_round()` 直接使用 `critical_open / major_open / minor_open`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L658)

**建議**

- 在 `IssueState` 顯式加入至少這些欄位：
  - `severity`
  - `category`
  - `latest_target`
  - `latest_description`
- 說清楚 severity 更新規則：
  - 取首次提出
  - 取最近一次
  - 或取歷史最高值

若不先定義，收斂與 best-round 評分會各自發明不同邏輯。

---

### 2. [critical] `issue_key` 的「跨輪穩定性」仍未真正解決

**問題**

`v3` 把 `hash(target + category)` 改成 `candidate_key + canonicalization`，方向正確，但它只處理了「同輪內 merge」，沒有真正解決「跨輪 identity stability」。

目前設計依賴 reviewer 自己產生：

`section_slug::category::short_label`

但：

- primary prompt 只要求「numbered sections」，沒有要求穩定 section slug 或 stable section ID
- `canonicalize_key()` 只做 lowercase / strip / truncate
- `merge_similar_keys()` 只定義「同一輪內」相似 key 合併

這代表同一個問題在 round 2/3 只要 reviewer 換了 slug、改了 short label、或主模型重編 section，系統就可能把它當成新 issue。原本最核心的 `issue lifecycle tracking` 仍然可能漂移。

**證據**

- primary prompt 只要求 numbered sections: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L453)
- reviewer key 格式依賴 `section_slug`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L488)
- D3 聲稱 short_label 微調不影響 match，但未定義跨輪 merge: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L56)
- `merge_similar_keys()` 僅限同一輪: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L595)

**建議**

- 不要把跨輪穩定性寄託在 reviewer 自發 key 一致性
- 加一個明確的 cross-round reconciliation step：
  - 新 objection 進來後，先對照目前 open issues
  - 若 `target/category/summary` 高度相近，沿用既有 `issue_key`
  - 只有無法對應時才建立新 key
- 若不想用 embedding/LLM，可以先採保守規則：
  - primary section 必須有 stable `SECTION_ID`
  - reviewer 必須引用 `SECTION_ID`
  - `issue_key` 由 system 根據 `SECTION_ID + category + normalized_issue_summary` 產生

---

### 3. [critical] `Decision.confidence` 與 `Decision.votes` 仍未定義，現有 consumer 無法正確解讀 REFINE 結果

**問題**

現有 `Decision` contract 裡，`confidence` 與 `votes` 都是必要欄位，而且前端會直接消費它們。但 `v3` 幾乎沒有定義 REFINE 版的語義：

- `confidence` 只提到「若 max_rounds 則降到 0.5 以下」，沒有定義正常情況怎麼算
- `votes` 完全沒有定義在 REFINE 中代表什麼

這在現有 MAGI 中不是抽象問題，而是具體相容性問題。前端目前會把 `votes` 中「等於 ruling 的 answer」當成 approve，不等於 ruling 當成 reject。REFINE 的 reviewers 並不產生 answer string，而是 objection list；如果不重新定義 `votes`，UI 會直接誤判。

**證據**

- `Decision` 既有欄位：`confidence`, `votes`: [decision.py](C:\Projects\magi\magi\core\decision.py#L8)
- `v3` 對 confidence 的唯一明確規則是 max-rounds 時下調: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L635)
- `v3` 只說回傳 `Decision with protocol_used="refine"`，但沒有定義 `votes`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L289)
- 現有 UI 直接拿 `votes` 跟 `ruling` 比對 approve/reject: [index.html](C:\Projects\magi\magi\web\static\index.html#L925)

**建議**

- 明確定義 REFINE 的 `confidence` 公式
  - 例如依 `open issue severity`, `escalation count`, `degraded state`, `max_round hit` 綜合計算
- 明確定義 REFINE 的 `votes` contract
  - 如果要沿用既有欄位，建議改成 reviewer status summary 而不是 raw answer
  - 或承認 REFINE 不能沿用 `votes` 語義，改擴充新欄位，例如 `reviewer_states`
- 若短期不改 UI，必須明示 `ask(mode="refine")` 的現有 dashboard 呈現是降級模式，不是完整正確可視化

---

### 4. [major] `IssueTracker.resolve()` 的狀態機仍然不完整，`partial` / `compromise` / reopen 都沒有規則

**問題**

文件定義的 reflection verdict 有：

- `accept`
- `reject`
- `partial`

arbitration judge 也有：

- `sustain`
- `override`
- `compromise`

但 `IssueTracker.resolve()` 只定義了：

- accept → resolved
- reject → rejected_count++

這表示至少三種真實情境沒有 state transition：

- `partial` 之後 issue 應維持 open、轉成 partial_resolved、還是拆成子 issue？
- `compromise` 之後 issue 應算 resolved 還是保留 open？
- 已 `resolved` 的 issue 若下輪被 reviewer 指出 fix 不完整，是否允許 reopen？

沒有這些規則，tracker 雖然存在，但無法成為可信的 lifecycle source of truth。

**證據**

- reflection verdict 含 `partial`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L329)
- `IssueTracker.resolve()` 只描述 accept/reject: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L363)
- arbitration judge 含 `compromise`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L562)

**建議**

- 把 `resolution` 從目前的 4 態改成完整 state machine
- 最少補上：
  - `partial`
  - `reopened`
  - `aborted`
- 文件中要明講 transition table，而不是只留 method docstring

---

### 5. [major] round trace 只存 `proposal_hash + diff`，不足以支撐審計、最佳輪回退與 trace replay

**問題**

`RefineRound` 現在只存：

- `proposal_hash`
- `proposal_diff`
- objections/reflections

但不存該輪 proposal 本文或可重建的 snapshot。這會讓你在以下情境失去可追溯性：

- 想從 trace 檔直接重建每輪 proposal
- 想驗證 `best_round` 實際內容
- 想離線比對「最終 ruling」與某個中間輪的差異
- 想用 round log 作為 review artifact 提供給其他模型再審

如果 round log 的目的只是 telemetry，這樣可以；但 `v3` 把它描述成 round-level trace contract，這個契約目前不夠完整。

**證據**

- `RefineRound` 只有 `proposal_hash` 和 `proposal_diff`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L380)
- budget exceeded 需要返回 `best proposal`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L756)
- summary-only mode 又強調不保留完整 history: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L840)

**建議**

- round trace 至少保留下面其中一種：
  - `proposal_text`
  - `proposal_snapshot_path`
  - `base_round + reversible diff chain`
- 若你擔心 trace 膨脹，可以：
  - daily decision log 只存 summary
  - refine round log 存完整 proposal

否則這個 trace 只能證明「這輪存在過」，不能證明「這輪的實際內容是什麼」。

---

### 6. [major] 把 `best_round` 提示塞進 `minority_report`，會破壞既有 dissent 語義與 UI 呈現

**問題**

`v3` 提議：如果最終輪不是最佳輪，就在 `Decision.minority_report` 加一段 note，提醒某個中間輪更好。這在語義上不對，因為 `minority_report` 在現有系統就是 dissenting opinion，前端也會用紅色 `反對意見 DISSENT` 區塊渲染它。

把「最佳輪提示」塞進 `minority_report` 會讓：

- 使用者以為這是模型反對意見
- UI 將系統內部回退提示誤標成 dissent
- 未來 analytics 難以區分真正 dissent 與 orchestration note

**證據**

- `best_round` 提示寫入 `Decision.minority_report`: [refine-mode-proposal-v3.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md#L660)
- 現有 UI 將 `minority_report` 顯示為 `反對意見 DISSENT`: [index.html](C:\Projects\magi\magi\web\static\index.html#L969)

**建議**

- 新增獨立欄位，例如：
  - `refine_summary["best_round_note"]`
  - `decision.meta["quality_note"]`
- 不要重載 `minority_report`

---

## Strengths

- `v3` 最大的進步是 scope 收斂正確，知道先把 Core REFINE 做扎實，再談 GUIDED / Staged / Resume。
- 移除 reviewer promotion 是對的，這版比 `v2` 更尊重現有 heterogeneous node reality。
- cost aggregation、trace logging、parse failure 的處理都比 `v2` 實在，不再只是口號。
- `RefineConfig.primary_index`、`cancel_event`、`log_round()` 這些都是能真的減少實作歧義的好修正。

---

## 建議的升級條件

這版若要升級為 `ACCEPT / READY FOR IMPLEMENTATION`，我建議至少補完這 4 件事：

1. 補齊 `IssueState` schema，使它真的能支撐 convergence / escalation / best_round 演算法。
2. 明確定義 cross-round issue reconciliation，而不是只做 same-round key merge。
3. 為 REFINE 定義 `Decision.confidence` 與 `Decision.votes` 的相容語義。
4. 把 `partial / compromise / reopen` 的 state transition table 寫出來。

---

## 最終判定

`V3 = 接近可實作，但還差最後一輪架構收斂`

它已經不再像 `v2` 那樣有大面積散開的問題；現在剩下的是幾個很關鍵、但可明確修補的 contract 細節。  
如果把這幾個缺口補齊，我認為下一版很有機會進入可實作狀態。
