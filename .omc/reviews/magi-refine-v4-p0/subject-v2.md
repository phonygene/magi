# Subject v2: MAGI REFINE V4 P0 實作 — Round 01 修正後

## Delta 摘要（vs subject-v1.md）

subject-v1.md 的背景、22 atomic tasks 映射、R9 三偏離、向後相容保證仍然有效；本文件只列 **Round 01 審批後的修正差異**。

**Round 01 verdict**: Codex REVISE (7 MAJOR + 2 MINOR) / Gemini REVISE (2 MAJOR + 1 MINOR + 1 NIT)
**作者仲裁結果**: 使用者全盤接受 reflection（7 MAJOR + 2 MINOR 接受全修；1 NIT 拒絕，Gemini 誤判 `list(distinct_reviewers)` 非 bug）
**Round 02 測試結果**: **216 tests passed**（207 baseline + 9 new regression）

---

## 修正項目對照

### MAJOR-1: `reconcile_cross_round` target 欄位偏離（Codex+Gemini 共識）
**位置**: `refine_types.py:68` / `refine_types.py:218,243,307` / `refine_keys.py:149-151`
- `IssueState` append `latest_target: str = ""`（append-only）
- `IssueTracker.upsert()` 接收 `target` 參數並寫入 `latest_target`（upsert/update 雙路徑都更新）
- `IssueTracker.to_dict()` 輸出 `latest_target` 欄位
- `reconcile_cross_round()` 改用 `st.latest_target`，空字串 fallback 回 key tail（backcompat）
- `refine.py` 所有 `tracker.upsert(...)` 呼叫點傳入 `target=obj.target`

### MAJOR-2: `merge_similar_keys` 資料遺失（Codex）
**位置**: `refine_keys.py:91-102`
- 移除 `dict[issue_key] → highest-severity-only` dedup 迴圈
- 函式僅做 key normalization / remap；回傳完整 list
- 下游：tracker.upsert 自然合併（idempotent），collator 取得所有 reviewer 的 suggestion

### MAJOR-3: `ALL_REVIEWERS_OFFLINE` 只在 round 1 才 abort（Codex）
**位置**: `refine.py:419`
- 移除 `and round_num == 1` 硬編碼
- 條件改為：`if len(reviewers) > 0 and not successful_reviewers and parse_errors:`
- 任何 round 全員 offline → `terminal_status="aborted"`, `degraded=True`

### MAJOR-4: parse_error → `Decision.degraded`（Codex）
**位置**: `refine.py` final assembly
- Final assembly 前加入 `decision.degraded |= any(r.parse_error_count > 0 for r in rounds_data)`
- parse-error reviewer 名稱 merge 進 `decision.failed_nodes`（deduped）
- RefineRound 擴充 `parse_error_count` 欄位（若尚未存在）

### MAJOR-5: retry cost 低估（Codex）
**位置**: `refine.py:185-195` / `refine.py:329-340` / `refine.py:461-470`
- `handle_primary_failure()` 簽章改為回傳 `tuple[str | None, bool, float]`（proposal, degraded, cost_delta）
- 兩個 call site（primary failure + reflection retry）都捕獲 `retry_cost` 並累加到 `total_cost` / `round_cost`
- 原則：每次 `node.query()` 呼叫後立即讀 `last_cost_usd` 累加

### MAJOR-6: `track_best_round` 用最終 severity 回算歷史（Codex）
**位置**: `refine_types.py:162`（RefineRound 新欄位）/ `refine.py:529,562`（populate）/ `refine_convergence.py:97`（consume）
- `RefineRound` append `issue_severity_snapshot: dict[str, str] = field(default_factory=dict)`
- 每輪結束時寫入當輪 tracker severity snapshot
- `_round_score_breakdown()` 改用 `round_obj.issue_severity_snapshot`；空 dict 時 fallback 回 tracker（backcompat）

### MAJOR-7: Dashboard refine option + tri-state finalRuling（Codex+Gemini 共識）
**位置**: `web/static/index.html`
- 加入 `<option value="refine">REFINE</option>` 到 mode select
- `finalRuling` 計算加入 `parseErrorCount`；優先序：approve≥2 → approve；reject≥2 → reject；parseErrorCount>0 → `final-warning`；else deadlock
- 新增 `.final-warning` CSS（沿用 `.vote-lamp.warning` 黃色 `#f5c242` 調色板）

### MINOR-1: reviewer round>1 summaries UNTRUSTED 隔離（Codex）
**位置**: `refine_prompts.py` `build_reviewer()`
- round > 1 時，`decisions_summary` / `resolved_summary` / `unresolved_summary` 包進獨立 `<UNTRUSTED_CONTENT type="prior_round_summary">` block
- 防止 primary reasoning 中的指令性文字被 reviewer 誤當 prompt

### MINOR-2: 測試矩陣漏寫（Codex）— 本輪修復
- 新增 9 regression tests（見下）
- Plan vs 實際覆蓋差異：下輪 wrap-up 時可重新盤點

### MINOR-3: best_round recovery on cancel/abort（Gemini 部分接受）
- MAJOR-6 修完後同步解決；無需額外程式碼
- Round 02 測試已覆蓋 `test_best_round_after_partial_downgrade`

### NIT-1: `list(v.distinct_reviewers)`（Gemini — 拒絕）
- 已說明為 defensive copy，非 bug；不改動

---

## 新增 Regression Tests（9 項，216 total）

### Unit (tests/test_refine_unit.py, 6 項)
1. `test_reconcile_cross_round_uses_real_section_id` — MAJOR-1
2. `test_merge_similar_keys_preserves_multi_reviewer_suggestions` — MAJOR-2
3. `test_best_round_after_partial_downgrade` — MAJOR-6
4. `test_reviewer_prompt_wraps_prior_summaries_in_untrusted` — MINOR-1
5. `test_dashboard_has_refine_option` — MAJOR-7 UI
6. `test_dashboard_final_ruling_warning_class_css_present` — MAJOR-7 UI

### Integration (tests/test_refine_integration.py, 3 項)
7. `test_all_reviewers_offline_round_2_aborts` — MAJOR-3
8. `test_parse_error_sets_degraded` — MAJOR-4
9. `test_primary_retry_cost_accumulated` — MAJOR-5

---

## 向後相容保證（維持不變）

- `Decision` dataclass **仍然只 append 一個** `refine_summary` 欄位（MAJOR-6 的 `issue_severity_snapshot` 在 `RefineRound` 而非 `Decision`）
- `Decision.asdict()` / `to_jsonl()` 行為未變
- `IssueState` 新 `latest_target` 為 append-only，預設 `""`
- `RefineRound` 新 `issue_severity_snapshot` 為 append-only，預設 `{}`，舊 trace 反序列化時 fallback OK
- 207 既有 tests 全綠（0 regression）

---

## 驗收證據

- `uv run python -m pytest tests/ -q` → **216 passed in ~9s**
- 9 new regression tests 覆蓋 Round 01 審批指出的每一項 MAJOR/MINOR（NIT-1 拒絕、MINOR-3 藉由 MAJOR-6 test 覆蓋）
- production code 無超出 reflection 範圍的修改

---

## 審批者請關注

Round 02 僅為修正 Round 01 findings，**不應**引入新的 spec 偏離。建議審批重點：

1. 修正是否**真的**解決原 finding（而非只是讓 test 通過）
2. 是否引入新的 regression（尤其 `merge_similar_keys` 不再 dedup 對下游的影響）
3. `handle_primary_failure` 改回傳 tuple 是否有遺漏的 call site 未更新
4. `issue_severity_snapshot` populate 時機是否正確（本輪結束而非下輪開始）
5. `parseErrorCount` 的 UI 優先序是否合理（目前 reject > parse_error，亦可商榷）

若本輪只剩 MINOR/NIT 建議，可直接 APPROVE；若仍有 MAJOR，繼續 revise。
