# Subject v3: MAGI REFINE V4 P0 實作 — Round 02 後續修正

## Delta 摘要（vs subject-v2.md）

subject-v1.md 的背景、22 atomic tasks 映射、R9 三偏離、向後相容保證，以及 subject-v2.md 的 R01→R02 delta 仍然有效。本文件只列 **Round 02 審批後的修正差異**。

**Round 02 verdict**: Codex REVISE (2 new MAJOR + 1 MINOR) / Gemini REVISE (1 MAJOR + 1 NIT)
**兩家共識**：R9 #1 UI 有系統性斷層（4 個程式碼路徑）
**作者仲裁結果**：使用者接受 reflection 判斷（2 MAJOR + 1 MINOR 接受；1 NIT 拒絕，Gemini 再次誤判 `list(distinct_reviewers)`）
**Round 03 測試結果**：**218 tests passed**（216 + 2 new behavior-level）

---

## 修正項目對照

### MAJOR-A: Dashboard R9 #1 UI 系統性斷層（Codex + Gemini 共識）

**修正策略**：抽出共用函式 `mapVoteToLamp()`，消除各路徑各自實作的重複邏輯，防止下次再漏某個路徑。

**實作位置與變更**：
- `web/static/index.html:502-510`（新增）：`mapVoteToLamp(answer, ruling, isRefine, isFailed)` 共用函式；**PARSE_ERROR 優先於 isFailed→abstain**（Codex MAJOR-A 根因）
- `web/static/index.html:944-954`：`case 'decision'` 的 nodeVotes 計算改用 helper；`failed_nodes.forEach` 改為**僅填補無 vote 的 failed node**，不覆寫已有的 warning
- `web/static/index.html:1045-1052`：`nodeVotesSummary`（finalRuling）同樣改用 helper（Gemini MAJOR 根因）
- `web/static/index.html:615-620`：`renderResultTab` 計數從 `'PARSE_ERROR'` 改為 `'warning'`（finalRuling.votes 現在存 lamp 狀態而非 raw answer）

### MAJOR-B: `confidence` 計算早於 parse_err→degraded 賦值（Codex）

**修正位置**：`refine.py:608-621`

**變更**：
- `parse_err_count` 聚合 + `decision.degraded |= (parse_err_count > 0)` + `decision.failed_nodes` 補齊邏輯**前移**到 `compute_refine_confidence` 呼叫之前
- confidence 計算看到正確的 `decision.degraded` 值，degraded penalty (−0.10) 得以套用
- 原本在 final assembly 尾段的重複邏輯區塊移除（避免雙寫）

### MINOR: Round 02 回歸測試過度表面（Codex）

**修正策略**：Round 03 為每個 MAJOR 補 **behavior-level** test（非 string presence）

- 新增 2 integration tests（見下）
- 更新 1 既有 test（`test_dashboard_final_ruling_warning_class_css_present` 斷言字串從 `parseErrorCount` 改為 `mapVoteToLamp` + `warningCount`，反映新程式碼結構）

---

## 新增 Regression Tests（2 項 behavior-level，218 total）

### Integration (tests/test_refine_integration.py)
1. **`test_parse_error_lowers_confidence`** (MAJOR-B)
   - 跑兩個 refine_protocol：一次無 parse_error、一次 casper 丟 unparseable output
   - 斷言 `bad.confidence < clean.confidence`（degraded penalty 實際套用）
   - 此 test 在 R02 程式碼下會失敗（degraded=True 但 confidence 相同）— 確認攔截能力

2. **`test_decision_event_preserves_parse_error_for_tristate`** (MAJOR-A)
   - 製造 parse_error 場景，呼叫 refine_protocol
   - 斷言 `decision.votes["casper"] == "PARSE_ERROR"`（未被 server-side 二值化）
   - 斷言 `"casper" in decision.failed_nodes`
   - 模擬 `mapVoteToLamp` 的 Python 等價邏輯，斷言最終 lamp 為 `'warning'` 而非 `'abstain'`
   - 此 test 確保 server→UI 的 contract 正確

---

## 向後相容保證（維持不變）

- `Decision` dataclass 仍然只 append `refine_summary`
- `Decision.asdict()` / `to_jsonl()` 行為未變
- Round 02 新增的 `IssueState.latest_target` / `RefineRound.issue_severity_snapshot` 仍為 append-only
- 216 既有 tests 全綠，零 regression

---

## 驗收證據

- `uv run python -m pytest tests/ -q` → **218 passed in ~7s**
- 2 new behavior-level tests 直接攔截 R02 兩個 MAJOR（不是 string presence check）
- 1 既有 test 更新反映新程式碼（不是測試造假）
- UI 修正抽出 `mapVoteToLamp` 單一來源，消除 R9 #1 四路徑各自實作的根因

---

## 審批者請關注

Round 03 僅為修正 Round 02 findings，**不應**引入新的 spec 偏離。建議審批重點：

1. `mapVoteToLamp` 的 PARSE_ERROR 優先序是否合理？（目前：PARSE_ERROR > isFailed → abstain）
2. `parseErrorCount → warningCount` 更名是否造成其他遺漏路徑？
3. `refine.py:608-621` 的前移邏輯是否破壞 abort/budget/cancel 路徑的 degraded 狀態？
4. Round 03 test 是否真的攔住 bug？可嘗試 revert 任一 MAJOR 修正，看 test 是否紅

若本輪只剩 MINOR/NIT 建議，可直接 APPROVE。
