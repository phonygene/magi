# Reflection by Claude (作者): Round 01

## 整體態度
兩家審批者（Codex + Gemini）獨立指出多項嚴重的規格偏離與邏輯錯誤，且多點共識（target 欄位、refine mode option）。我在 reflection 前逐一打開原始碼查證，**幾乎全部屬實** — architect agent 的 APPROVE 並未抓到這些深層問題，207 tests 全綠也只是因為測試矩陣本身漏寫了關鍵分支。

整體判斷：**同意 REVISE**。本版不可 merge，需進入 Round 02 修正。

---

## 逐項判斷（按嚴重度排序）

### [MAJOR-1] `reconcile_cross_round` 用錯 target 比對欄位（Codex + Gemini 雙方共識）

**審批者原文摘要**：
- Codex：spec 要看 `(category, target, description)` 三元組，實作卻用 `st.issue_key.split("::")[-1]` 比對 `obj.target`；`IssueState` 根本沒保存 `latest_target`。真實 SECTION_ID（如 `S2.1`）下 reattach 會失敗。
- Gemini：同點，規格偏離。

**作者判斷**：**同意（CRITICAL 等級的規格偏離）**

**判斷理由**：實際打開 `refine_types.py:53-67`，`IssueState` 欄位為 `issue_key / first_raised_round / last_raised_round / raised_count / rejected_count / distinct_reviewers / resolution / severity / category / latest_description / resolved_at_round / auto_resolved` — 確實**沒有** `latest_target`。`refine_keys.py:155-157` 也確實拿 `st.issue_key.split("::")[-1]` 當 target proxy，這在 canonicalize 後（truncated to 40 chars、底線化）已不可還原為原始 SECTION_ID。spec `refine-mode-proposal-v4.md:820-823` 明寫 `target × 0.3`，偏離實錘。

**修正方案**：
1. `IssueState` 追加 `latest_target: str` 欄位（append-only）
2. `IssueTracker.upsert()` 接收 `target` 參數並寫入
3. `reconcile_cross_round()` 改用 `st.latest_target` 比對 `obj.target`
4. 測試改用真實 SECTION_ID 形狀（如 `S2.1`, `authoring_section`）而非 `::x` tail

---

### [MAJOR-2] `merge_similar_keys` 在 collator 前丟棄 reviewer objection 實體（Codex）

**審批者原文摘要**：B2 先 remap key，再以 `dict[issue_key] → highest-severity objection` 收斂，只留一筆；其他 reviewer 的 suggestion / provenance / conflicting_suggestions 在進 collator 之前就消失。

**作者判斷**：**同意**

**判斷理由**：實際查 `refine_keys.py:96-102`，`out: dict[str, Objection]` 以 issue_key 為鍵，只保留最高 severity 一筆。spec `refine-mode-proposal-v4.md:678-709` 要 collator 看到所有 suggestions 並產出 `conflicting_suggestions` 標示，但現況是 reviewer B 的 suggestion 在 reviewer A severity 較高時就被整個丟掉。

**修正方案**：
1. `merge_similar_keys()` 只做 key normalization + remap，**不做 dedup**；回傳完整 list（可能多筆同 key）
2. Collator 輸入改為「以 key group 的 list」；fallback_consolidate 也要聚合 suggestions
3. 補測試：兩位 reviewer 對同一 issue 提不同 suggestion，驗證 collator payload 兩筆都在

---

### [MAJOR-3] ALL_REVIEWERS_OFFLINE 只在 round 1 才 abort（Codex）

**審批者原文摘要**：`refine.py:412` 硬編碼 `round_num == 1`；晚一輪全掛會被包成 `terminal_status="max_rounds"`，`degraded=false`。

**作者判斷**：**同意**

**判斷理由**：實際查 `refine.py:410-417`，條件確實是 `len(reviewers) > 0 and not successful_reviewers and parse_errors and round_num == 1`。spec 沒有 round 限制。late-round infrastructure failure 會被偽裝成正常收斂。

**修正方案**：移除 `and round_num == 1`；條件改為 `len(reviewers) > 0 and not successful_reviewers and parse_errors`，任何 round 觸發都設 degraded + abort。補測試 `test_all_reviewers_offline_round_2_aborts`。

---

### [MAJOR-4] parse_error 不計入 `Decision.degraded`（Codex）

**審批者原文摘要**：spec `:53-55` 明寫 parse_error → degraded；實作中只有 abort / primary failure 會設 degraded，純 parse_error 不會。

**作者判斷**：**同意**

**判斷理由**：grep `degraded` 共找到 7 個設點（行 332, 413, 457, 470, 503），沒有任何一處以 `parse_err_count > 0` 為條件。final assembly 也沒有此邏輯。對應測試也不存在。

**修正方案**：final assembly 前（約 refine.py:580-596）加上 `decision.degraded |= any(r.parse_error_count > 0 for r in rounds)`，並把 parse-error reviewer 加入 `failed_nodes`。補測試 `test_parse_error_sets_degraded`。

---

### [MAJOR-5] primary retry 路徑仍低估 cost（Codex）

**審批者原文摘要**：`handle_primary_failure()` 第一次失敗呼叫的 cost 被 retry 覆寫；spec `:375-376, :956-960` 要求每次 node call 後立刻累加，但實作靠 `last_cost_usd` 單槽位。

**作者判斷**：**部分同意**

**判斷理由**：審批者指稱正確：`refine.py:190-195` `handle_primary_failure` 內呼叫 `primary.query(retry_prompt)` 後，如果 MagiNode 實作中 `last_cost_usd` 被 retry 覆寫，則第一次 call 的 cost 確實遺失。但嚴格來說，MagiNode 在 exception 路徑是否會設 `last_cost_usd` 本身也不確定（可能 exception 前還沒算出 cost）。不過保守起見應該讓 retry helper 自己把 cost 累加回傳 / 或改為每次 query 立即 accumulate 到 round-local 變數，而非依賴 node 內 state。

**修正方案**：
1. `handle_primary_failure` 簽章加 `cost_sink: list[float]` 或回傳 `(proposal, degraded, cost_delta)`
2. 所有 `primary.query()` / `reviewer.query()` 呼叫處改為 immediate accumulate pattern：`c = n.last_cost_usd; total_cost += c`
3. 補測試：primary 失敗 1 次 + retry 成功，驗證 total_cost 包含兩次呼叫

---

### [MAJOR-6] `track_best_round` 用最終 severity 回算歷史 round（Codex）

**審批者原文摘要**：`_round_score_breakdown` 用 `tracker.to_dict()` 當 severity 來源；partial downgrade 發生後，舊 round 的 critical 被降級為 minor，best_round 判斷錯誤。

**作者判斷**：**同意**

**判斷理由**：`refine_convergence.py:93` 確實是 `tracker_snapshot.get(key, {}).get("severity", "minor")`，tracker 是函式進入時的最終狀態。spec `:892-901` 的 score 要求用當輪 active severity；spec `:474-475` 又允許 partial downgrade — 兩規則並存時實作取錯了。

**修正方案**：
1. `RefineRound` 追加 `issue_severity_snapshot: dict[str, str]`（記錄本輪結束時各 issue 的 severity）
2. 每輪結束時寫入 snapshot
3. `_round_score_breakdown` 改用 `round_obj.issue_severity_snapshot`，不再查 tracker
4. 補測試：Round 1 critical、Round 2 partial_resolved 降級至 minor、Round 3 新增 critical — 驗證 best_round 是 Round 2 而非 Round 1

---

### [MAJOR-7] R9 #1 在 RESULT tab / finalRuling 仍二值化 + 缺 refine mode option（Codex + Gemini 共識）

**審批者原文摘要**：
- Codex：`index.html:600-605` 只看 approve/reject 計數，PARSE_ERROR 被 dropped 或淪為 deadlock；`:335-337` mode select 沒 `refine` option
- Gemini：Dashboard UI 缺少 `refine` 選項（MAJOR）

**作者判斷**：**同意**

**判斷理由**：
- `index.html:335-337` 確實只有 adaptive / vote / critique，沒有 `<option value="refine">`
- `index.html:600-605` 確實只計 approve + reject count；PARSE_ERROR 既不計 approve 也不計 reject，只會落入 `final-deadlock`。這違反 R9 #1「三態燈號貫穿整個 UI」的要求
- subject-v1.md 聲稱「REFINE verdict switch on refine_summary.terminal_status」只修到 primary verdict pane，沒延伸到 finalRuling 計數

**修正方案**：
1. `<option value="refine">REFINE</option>` 加到 mode select
2. `finalRuling.votes` 計數改為：`parseErrorCount = Object.values(votes).filter(v => v === 'PARSE_ERROR').length`；有 parse_error 時套用 `final-warning`（黃色）class
3. 新增 `.final-warning` CSS
4. 測試用 Playwright/jsdom 驗證 DOM；至少補 server-side event test 斷言 votes 中 PARSE_ERROR 字串被保留

---

### [MINOR-1] reviewer round>1 的 `decisions_summary` 沒在 `<UNTRUSTED_CONTENT>` 內（Codex）

**審批者原文摘要**：`build_reviewer()` 只把 `proposal_or_diff` 包進 UNTRUSTED，但 `format_decisions_summary()` 輸出的 primary reasoning 原文直接插在 trusted 區段；若 primary reasoning 含指令性文字會被當 prompt。

**作者判斷**：**同意**

**判斷理由**：prompt injection 風險真實存在。Round 1 沒問題因為沒有上輪摘要，但 round>1 的 `decisions_summary` 含 reviewer 與 primary 的自然語言輸出，確實應視為 untrusted。

**修正方案**：`build_reviewer()` 把 `decisions_summary` / `resolved_summary` / `unresolved_summary` 都包進獨立的 `<UNTRUSTED_CONTENT type="prior_round_summary">` block。補測試驗證 round 2+ prompt 中這些區塊位於 UNTRUSTED 內。

---

### [MINOR-2] 測試矩陣與實際覆蓋不符（Codex）

**審批者原文摘要**：spec/plan 宣稱的 `test_parse_failure_not_counted`、`test_refine_terminal_status_ui`、`test_parse_error_neutral_in_ui_summary` 在實際檔裡不存在；dashboard 只有 server-side event test。

**作者判斷**：**同意**

**判斷理由**：這是根因 — 測試矩陣漏寫讓 MAJOR 1/3/4/6/7 全部帶著綠燈通過。不是單純 MINOR，與 MAJOR bugs 耦合。

**修正方案**：Round 02 修 MAJOR 1-7 時，每項至少補 1-2 個測試（已在各 MAJOR 項目中列出）。Round 02 結束時重新盤點 plan 宣稱的測試 vs 實際測試，差異補齊或修正 plan。

---

### [MINOR-3] Gemini 提出的 TERMINAL_CANCELLED / ABORTED 的 best_round 恢復

**審批者原文摘要**：非 primary 故障的 cancel/abort 路徑是否正確返回 best_round。

**作者判斷**：**部分同意**

**判斷理由**：需要具體定位 Gemini 所指行號；目前 `refine.py` 的 abort/cancel/budget 路徑都有呼叫 `track_best_round`，但 MAJOR-6 已指出 `track_best_round` 本身錯誤。修完 MAJOR-6 後這個 MINOR 可能一併解決，或需要額外補 test。

**修正方案**：MAJOR-6 修完後回頭補測試 `test_best_round_recovery_on_cancel`、`test_best_round_recovery_on_budget_exceeded`。

---

### [NIT-1] Gemini 提出的 `IssueTracker.to_dict()` 使用 `list(v.distinct_reviewers)`

**作者判斷**：**不同意（誤判）**

**判斷理由**：`IssueState.distinct_reviewers` 本身就已經宣告為 `list[str]`（見 `refine_types.py:61`），`list(v.distinct_reviewers)` 只是 defensive copy。不是錯誤，至多是 redundant。保持現況。

---

## 總結

| 分類 | 數量 | 處理 |
|------|------|------|
| MAJOR 接受 | 7 | Round 02 全修 |
| MAJOR 部分接受 | 0 | - |
| MINOR 接受 | 2 | Round 02 併修 |
| MINOR 部分接受 | 1 | MAJOR-6 修完後回視 |
| NIT 拒絕 | 1 | 說明誤判 |

## Round 02 修正計畫骨架（待使用者仲裁後確定）

1. **資料模型**：`IssueState` append `latest_target`；`RefineRound` append `issue_severity_snapshot`
2. **refine_keys**：`merge_similar_keys` 不 dedup；`reconcile_cross_round` 用真 target
3. **refine.py**：移除 `round_num == 1` 硬編碼；parse_error → degraded；retry cost 立即累加
4. **refine_convergence**：`track_best_round` 用 per-round severity snapshot
5. **UI**：加 refine option；finalRuling 保留 tri-state + 黃燈 class
6. **prompts**：round>1 summaries 包進 UNTRUSTED
7. **測試**：補上述各項對應 test，同步更新 plan
8. **向後相容**：新增欄位都是 append-only，Decision.asdict/to_jsonl 不受影響

---

**下一步**：等待使用者仲裁決定。使用者可：
- 確認本 reflection 的判斷（直接進入 Round 02 修正）
- 推翻某幾項（例如 Gemini MINOR-3 要求更嚴格的 test、或覺得 NIT-1 值得改）
- 指示 Round 02 範圍（全修 / 只修 CRITICAL-risk 幾項 / 延後部分）
