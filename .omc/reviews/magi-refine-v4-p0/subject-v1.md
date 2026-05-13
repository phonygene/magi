# Subject v1: MAGI REFINE V4 P0 實作

## 背景

MAGI REFINE V4 是 MAGI 第四個協議（與 vote / critique / adaptive 並列），為 primary-reviewer 迭代精煉協議：1 primary + N reviewers + 1 collator + 選擇性 user。規格經 Gemini ACCEPT / Claude ACCEPT / Codex REVISE 三模型審批後凍結於 `refine-mode-proposal-v4.md`（1348 行），Codex 提出 3 項 R9 偏離要求，已內化為實作強制約束。

本實作執行 P0 範圍，完成 22 atomic tasks，10 phases A→J。最終測試結果：**207 tests 全綠**（142 baseline + 65 new），architect agent APPROVE，ai-slop-cleaner pass 已執行。

## R9 三偏離（必須驗證已正確內化）

### R9 #1：Dashboard PARSE_ERROR 三態燈號
**要求**：Dashboard decision handler 完整列舉 PARSE_ERROR 三態（approve / reject / warning），不得只處理 approve/reject 二態。

**實作位置**：`magi/web/static/index.html` `case 'decision'` handler
- 新增 `.vote-lamp.warning` CSS（黃色 #f5c242）
- `answer === 'PARSE_ERROR'` → 映射為 `warning` lamp，label 為「解析不能」
- REFINE 分支使用 `refine_summary.terminal_status` 分派 verdict

### R9 #2：CLI --guided 直呼 engine.refine()
**要求**：CLI `--guided` 旗標必須直接呼叫 `engine.refine(query, RefineConfig(guided=True, on_user_review=stdin_prompter))`，**不走** `ask(mode="refine")`。

**實作位置**：`magi/cli.py`
- 新增 `_build_stdin_prompter()` async callback（stdin 讀 approve/override/terminate + JSON override array）
- 當 `mode == "refine"` 且 `--guided` 時，直接構造 `RefineConfig` 並呼叫 `engine.refine(query, config=cfg)`
- 非 guided refine 模式仍可走 `ask()` dispatch

### R9 #3：沿用 Decision.trace_id
**要求**：Decision dataclass 已有 `trace_id` 欄位（decision.py:19），REFINE 必須沿用，**不新增** `refine_trace_id`。

**實作位置**：`magi/core/decision.py` + `magi/protocols/refine.py`
- `Decision` dataclass 只 append 單一欄位：`refine_summary: dict | None = None`
- REFINE trace 子目錄命名：`{trace_dir}/refine/{trace_id}.jsonl`（直接用 Decision.trace_id）
- `test_decision_backcompat_no_refine_summary` 斷言 `"refine_trace_id" not in parsed`

## 22 Atomic Tasks 映射

| Phase | Task | 檔案 | 摘要 |
|-------|------|------|------|
| A1 | 擴展 Decision | `core/decision.py` | append `refine_summary: dict \| None = None` |
| A2 | Dataclasses | `protocols/refine_types.py` | Objection/Reflection/IssueState/UserOverride/UserAction/RefineConfig/RefineRound |
| A3 | IssueTracker | `protocols/refine_types.py` | 4 態狀態機 + transient reopened→open flip |
| B1 | canonicalize_key | `protocols/refine_keys.py` | lowercase/underscore/truncate |
| B2 | merge_similar_keys | `protocols/refine_keys.py` | difflib intra-round dedup, threshold 0.92 for short keys |
| B3 | reconcile_cross_round | `protocols/refine_keys.py` | category hard-match + target*0.3 + desc*0.4, threshold 0.80 |
| C1 | check_convergence | `protocols/refine_convergence.py` | 優先序 1.ALL_RESOLVED 2.THRESHOLD 3.SILENCE 4.MAX_ROUNDS |
| C2 | track_best_round | `protocols/refine_convergence.py` | score = -5*critical -2*major -1*minor +3*resolved |
| C3 | check_sycophancy | `protocols/refine_convergence.py` | 2 連續 round accept_rate=1.0 |
| C4 | confidence/votes/minority | `protocols/refine_convergence.py` | R9 #1 PARSE_ERROR 字串 |
| D1 | collate_objections | `protocols/refine_collator.py` | ad-hoc litellm + 1 retry + schema hint |
| D2 | fallback_consolidate | `protocols/refine_collator.py` | 包裝 raw objections |
| E1 | log_round | `trace/logger.py` | `{trace_dir}/refine/{trace_id}.jsonl` |
| F1 | Prompts | `protocols/refine_prompts.py` | 4 個 builder，`<SYSTEM_INSTRUCTION>`/`<UNTRUSTED_CONTENT>` 隔離 |
| G1 | handle_primary_failure | `protocols/refine.py` | 1 次重試 |
| G2 | refine_protocol main | `protocols/refine.py` | N-round loop 核心 |
| G3 | guided hook | `protocols/refine.py` | `_run_guided_callback` + timeout/exception 政策 |
| H1 | _resolve_cost_mode | `core/engine.py` | 從 inline 抽出 |
| H2 | engine.refine() | `core/engine.py` | 獨立 async method |
| H3 | ask() dispatch | `core/engine.py` | `elif mode == "refine"` |
| H4 | CLI refine+guided | `cli.py` | R9 #2 |
| I1 | Dashboard UI | `web/static/index.html` | R9 #1 |

## 關鍵設計點（審批重點）

### 收斂優先序（C1）
```
1. ALL_RESOLVED: 所有 issue 狀態為 resolved
2. THRESHOLD: active critical/major ≤ config.threshold
3. SILENCE+AUTO_RESOLVE: 所有 successful reviewer 回空 objections
   (R8-1 gate: 至少 1 位 successful reviewer 才生效)
4. MAX_ROUNDS: 達到 config.max_rounds
```

### 4 態狀態機（A3）
```
open → partial_resolved → resolved
         ↓
       reopened (transient, 本輪結束 flip 回 open)
```

### Parse-error 分離（D4）
- reviewer 回應解析失敗 → 標記 `parse_error=True`
- **不計入** `rejected_count`（避免污染 sycophancy 偵測）
- PARSE_ERROR 在 minority_report 以字串 "PARSE_ERROR" 標示

### Per-call cost 聚合
- **NOT** `sum(n.last_cost_usd)`（會被下輪覆寫）
- 每次呼叫後立即 accumulate 到 round-local counter

### Silence gating (R8-1)
- 全員回空 → 只有當 ≥1 位 reviewer 成功（非 offline）才觸發 auto_resolve
- 否則視為無效資訊，繼續下輪

### Collator 跳過條件
- `_resolve_collator_model()` 偵測 CliNode → return None
- 單一 reviewer 時直接用其 objections，不召喚 collator

### GUIDED timeout 政策
- `abort`：中止，回傳 best_round
- `approve`：視同 approve 繼續
- `reject`：raise 例外

## 測試分布（65 new tests）

| 檔案 | 測試數 | 範圍 |
|------|--------|------|
| `test_decision.py`（appended） | 2 | A1 refine_summary 序列化 + 向後相容 |
| `test_refine_unit.py` | 39 | A2-F1 unit |
| `test_refine_integration.py` | 24 | G2/G3 integration + D + H + I2 |

## 向後相容保證

- `Decision.asdict()` 包含 `refine_summary` key（None for vote/critique/adaptive）
- `Decision.to_jsonl()` 正確序列化 None 與 dict
- 142 既有 tests 全部通過
- 非 refine 模式（vote/critique/adaptive）不受影響

## 已知風險或限制（作者自陳）

1. GUIDED `timeout_policy="reject"` 會 raise 例外到上層，需呼叫者自行處理
2. Collator fallback 僅做結構包裝，不做語意整合
3. CliNode 偵測靠 `isinstance(n, CliNode)`，若未來新增 CLI 節點類型需擴充
4. Sycophancy 偵測只看連續 2 輪 100% accept，邊界情況（1 輪後 converge）無法偵測
5. Cost 聚合依賴 `n.last_cost_usd` 正確性，若 MagiNode 實作漏記會低估

## 驗收證據

- `uv run python -m pytest tests/ -q` → 207 passed
- architect agent 跑過 APPROVE（僅 2 minor stylistic 建議，已採納 #1 early-break）
- ai-slop-cleaner 移除 `handle_parse_failure` 死碼 + 重複 `import pytest`
- ralph state 已清（`state_clear(mode="ralph")` + `skill-active`）
