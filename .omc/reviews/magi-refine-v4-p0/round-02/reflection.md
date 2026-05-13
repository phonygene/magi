# Reflection by Claude (作者): Round 02

## 整體態度

Codex 本輪 **verdict 仍為 REVISE**，找到 2 個我上一輪修正時遺漏的下游邏輯破口。兩個都是高權重的諷刺 bug：

1. **R9 #1 UI tri-state 再次被蓋掉**：我 Round 01 修了 primary 三態，Round 02 修了 `finalRuling.votes`，但忘了中間那層 `nodeVotes[name] = 'abstain'` 會把 warning 覆寫回去 — 這 bug 是**我自己的 MAJOR-4 修正（parse_error → failed_nodes）**間接引入的副作用。
2. **degraded/confidence 時序錯誤**：我把 parse_err → degraded 的邏輯放在 `compute_refine_confidence` 呼叫之**後**，導致 `degraded=True` 卻 `confidence=1.0`。

**Gemini round-02 延遲回傳**（~26 分鐘，比 Round 01 慢 4×，原因：外部 API 429 rate-limit 重試）。Gemini verdict 同為 REVISE，獨立指出 1 MAJOR — 與 Codex MAJOR-A 是**同一個根因**（UI PARSE_ERROR 未貫穿），但指向不同程式碼路徑（line 826 `nodeVotesSummary` + line 632 `renderResultTab`），兩家共識 R9 #1 實作仍有破口。Gemini 的 NIT（`list(v.distinct_reviewers)`）與 Round 01 相同，仍拒絕。

更諷刺的是，我 Round 01 補的 9 個 regression tests 全綠，但完全沒攔住這兩個 bug — 這印證了 Codex 的 MINOR（測試仍過於 surface-level）。

**整體判斷：同意 REVISE**。進入 Round 03 修正。

---

## 逐項判斷

### [MAJOR-A] Dashboard UI PARSE_ERROR 貫穿仍有多個斷層（Codex + Gemini 共識）

**Codex 原文摘要**：後端把 parse-error reviewer 併進 `failed_nodes` 是對的；但前端 `index.html:948` 的 `(data.failed_nodes || []).forEach(n => { nodeVotes[n] = 'abstain'; })` 把 line 943 剛設的 `warning` 覆寫成 `abstain`。`finalRuling.votes` 更糟 — `:1040-1042` 把所有非 ruling 的 answer 先二值化成 `reject`，所以 `parseErrorCount` 實際永遠讀不到 `PARSE_ERROR`。R9 #1 仍未達標。

**Gemini 原文摘要**：`case 'decision'` 在填充 `nodeVotesSummary`（line 826）時未處理 `PARSE_ERROR`，使 `answer === 'PARSE_ERROR'` 被直接映射為 `reject`；line 632 `parseErrorCount` 讀的是已被二值化的 `votes`，永遠為 0；line 773 用 `'warning'` 標籤，但 `renderResultTab` 查 `'PARSE_ERROR'` 字串 — 標籤不一致。

**兩家共識**：R9 #1 UI 實作在多個程式碼路徑上**系統性**缺乏 PARSE_ERROR 處理，不是單點 bug。

**作者判斷**：**同意（CRITICAL-adjacent MAJOR，直接打破 R9 #1）**

**判斷理由**：實際查 `web/static/index.html:935-948`：
```js
for (const [name, answer] of Object.entries(data.votes || {})) {
  if (isRefine && answer === 'PARSE_ERROR') {
    nodeVotes[name] = 'warning';      // line 943
  } else {
    nodeVotes[name] = (answer === ruling) ? 'approve' : 'reject';
  }
}
(data.failed_nodes || []).forEach(n => { nodeVotes[n] = 'abstain'; });  // line 948
```
當 parse_error reviewer 被寫入 `failed_nodes`（Round 01 MAJOR-4 修正引入），line 948 會無條件覆寫 line 943 設定的 `warning` 為 `abstain`。**這個 bug 是我自己的修正造成的**。此外 Round 02 dashboard test 只做 CSS class / string presence 檢查，沒驗 event flow，所以 216 passed 漏出這個 bug。

**修正方案（整合兩家具體指向）**：
1. **Line 826 `nodeVotesSummary`（Gemini）**：加入 `isRefine && answer === 'PARSE_ERROR'` 分支映射 `'warning'`，與 line 943 `nodeVotes` 邏輯對齊
2. **Line 948 `failed_nodes` 覆寫（Codex）**：`(data.failed_nodes || []).forEach(n => { if (nodeVotes[n] !== 'warning') nodeVotes[n] = 'abstain'; });` — 保留 warning 不被 abstain 蓋掉
3. **Line 632 `renderResultTab`（Gemini）**：搜尋 `'warning'` 而非 `'PARSE_ERROR'`，與 nodeVotes 標籤一致
4. **finalRuling 路徑（Codex）**：`:1038-1042` 同步保留 tri-state
5. **抽出共用函式** `mapVoteToLamp(answer, ruling, isRefine, failed)` 避免下次又在某個路徑漏修
6. 補 **event-level test**：模擬 decision event `votes={"casper":"PARSE_ERROR"}` + `failed_nodes=["casper"]` + `protocol_used="refine"`，斷言 `nodeVotes[casper] === 'warning'`、`nodeVotesSummary[casper] === 'warning'`、`parseErrorCount === 1`、`finalClass === 'final-warning'`

---

### [MAJOR-B] `confidence` 計算順序錯誤（Codex）

**審批者原文摘要**：`refine.py:611` 用當前 `decision.degraded` 算 confidence；但 `:655-656` 才把 `parse_err_count > 0` 寫進 `decision.degraded`。`compute_refine_confidence` 在 degraded=True 時應扣 0.10，此時序錯誤讓 parse-error-only case 拿到「degraded=True, confidence=1.0」不一致狀態。

**作者判斷**：**同意（影響上層 observability 與自動化決策）**

**判斷理由**：實際查 `refine.py:609-611` 和 `:651-662`，確認 confidence 計算在先、parse_err → degraded 設定在後。`compute_refine_confidence` 簽章第三個參數 `degraded` 確實讀 `decision.degraded`，此時值可能還是 False，之後才被 parse_err 改為 True。此邏輯要嘛順序錯、要嘛重新計算。Round 02 integration test `test_parse_error_sets_degraded` 也只驗了 degraded 旗標本身，沒驗 confidence penalty。

**修正方案**：
1. 重排序：先計算 `parse_err_count` 並套用 `decision.degraded |= (parse_err_count > 0)`，**再**呼叫 `compute_refine_confidence`
2. 或把 parse_err 聚合拆到獨立函式，在 confidence 計算前呼叫
3. 補測試 `test_parse_error_lowers_confidence`：同一 scenario 下，有 parse_error 的 confidence < 無 parse_error 的 baseline

---

### [MINOR] Round 02 回歸測試過度表面（Codex）

**審批者原文摘要**：dashboard 新測試只驗 `value="refine"`、`final-warning`、`parseErrorCount` 字串存在；parse_error integration test 只驗 degraded，沒驗 confidence。兩個 MAJOR 都能在 216 passed 下漏出。

**作者判斷**：**同意**

**判斷理由**：這是 Round 01 MINOR-2 的具體示範 — 我表面上補了 test，但沒補到真正守得住 bug 的 test。新的 test 屬於「它還在嗎」級別，不是「它正確運作嗎」級別。

**修正方案**：Round 03 為每個 MAJOR 補 **behavior-level** test（非 string presence）：
- MAJOR-A：event-driven DOM/state test（上面已列出）
- MAJOR-B：`test_parse_error_lowers_confidence` 斷言數值差異

---

### [NIT] Gemini 再次提出 `list(v.distinct_reviewers)`

**作者判斷**：**不同意（與 Round 01 判定一致）**

**判斷理由**：Round 01 已判定為 defensive copy，非 bug。Gemini 重複提出。維持拒絕。

---

## 總結

| 分類 | 數量 | 處理 |
|------|------|------|
| MAJOR 接受 | 2 | Round 03 全修（A 整合 Codex+Gemini 兩家指向；B Codex 獨家） |
| MINOR 接受 | 1 | Round 03 測試升級 |
| NIT 拒絕 | 1 | Gemini 重複提出，維持 Round 01 判定 |

**兩家共識點**：R9 #1 Dashboard 實作有系統性斷層。Codex 指向 `nodeVotes` 初始化 + `failed_nodes` 覆寫；Gemini 指向 `nodeVotesSummary` + `renderResultTab` 渲染；兩者是同一個根因的不同表現 — Round 03 修正要同時處理這 4 個程式碼路徑，並抽出共用 `mapVoteToLamp()` 函式防止下次再漏。

## Round 03 修正計畫骨架

1. **`index.html`**：
   - `nodeVotes` 計算：`failed_nodes` 覆寫時優先保留 `warning`
   - `finalRuling` 同步
2. **`refine.py`**：
   - 重排序 parse_err 聚合在 confidence 計算之前
   - 或抽成獨立 pre-assembly 函式
3. **`tests`**：
   - `test_parse_error_warning_survives_failed_nodes_overwrite`（event-level UI 或 state test）
   - `test_parse_error_lowers_confidence`（數值斷言）
4. **驗收**：218 passed（216 + 2 新 behavior test，不含 Gemini 可能新增項目）

---

**下一步**：等待使用者仲裁決定。使用者可：
- 確認本 reflection 判斷（進入 Round 03 修正）
- 等待 Gemini 回傳後再仲裁（風險：無限期）
- 取消 review loop（若判斷這兩個 MAJOR 可接受為已知風險）
- 指示 Round 03 範圍調整
