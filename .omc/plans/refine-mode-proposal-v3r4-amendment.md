# REFINE Mode V3r4 Amendment — Codex R6 Findings Resolution

**Date:** 2026-04-07 (r2: 2026-04-08 CCG review integration)
**Base:** V3r3 (refine-mode-proposal-v3.md)
**Trigger:** Codex R6 verdict = REVISE（1 critical + 4 major）→ V3r4 → CCG review（Codex REVISE / Gemini ACCEPT-WITH-RESERVATIONS）→ V3r4r2
**Scope:** 本文件為增量修訂，僅列出對 V3r3 的差異。未提及的段落維持不變。

### V3r4r2 CCG Review Response

| # | CCG Finding | Source | 處理 |
|---|------------|--------|------|
| A1 | `auto_resolve_silent()` 應同時 resolve `open`（被 reject 的）+ `partial_resolved` | Codex+Gemini 共識 | 修改 F1 |
| A2 | GUIDED timeout 預設應為 `abort`，非 `approve` | Codex critical | 修改 F3 |
| A3 | Collator fallback 必須正規化為 `ConsolidatedObjection` schema | Codex+Gemini 共識 | 修改 F2 |
| A4 | Primary Reflection prompt 需更新以匹配 `suggestions` list | Codex+Gemini 共識 | 修改 F5 |
| A5 | `collator_model=None` + CLI node → skip collation | Codex 獨家 | 修改 F2 |

---

## Response Table

| # | Codex Finding | Severity | 判定 | 處理方式 |
|---|--------------|----------|------|---------|
| F1 | `NO_NEW_OBJECTIONS` 偽收斂 — reviewer 沉默 vs partial_resolved 未統一 | critical | **同意** | 修改收斂條件 §6 |
| F2 | Collator 與 3-node 架構整合方式不明確 | major | **同意** | 新增 §2.1 Collator Integration Contract |
| F3 | GUIDED callback contract 不完整（timeout/exception/partial override） | major | **同意** | 修改 §2 UserAction + §7 Failure Handling |
| F4 | parse error reviewer 在 UI 被誤判為 approve | major | **同意** | 修改 §6 compute_refine_votes + §10 UI |
| F5 | Collator 合併互斥建議丟失資訊 | major | **同意** | 修改 §4 Collator Prompt + output schema |

---

## F1. [critical] 收斂條件統一：沉默 = 自動 resolve 所有 active issues

### 問題本質

`active_issues()` 包含 `partial_resolved`，但 `NO_NEW_OBJECTIONS`（reviewer 全部沉默）直接判定 `converged`，兩者語義衝突。

**[V3r4r2 A1]** 原 V3r4 只 auto-resolve `partial_resolved`，但 `open`（被 primary reject 的）在 reviewer 沉默時也應 auto-resolve，否則會 loop 到 MAX_ROUNDS。Codex 和 Gemini 均指出此問題。

### V3r4 修訂

**修改 §6 check_convergence()：**

```python
def check_convergence(
    tracker: IssueTracker,
    threshold: int,
    current_round: int,
    max_rounds: int,
    round_objections: list[Objection],  # [V3r4] 本輪所有 reviewer 的 objection
    round_parse_errors: list[str],      # [V3r4r2] 本輪 parse error 的 node names
) -> tuple[bool, str]:
    """
    [V3r4r2] 收斂條件（依優先序）：

    1. ALL_RESOLVED: active_issues() == 0
       → terminal_status="converged"

    2. THRESHOLD: active_issues() 全為 minor 且數量 <= threshold
       → terminal_status="threshold"

    3. NO_NEW_OBJECTIONS + AUTO_RESOLVE:
       [V3r4r2] 前提：所有「成功 parse 的 reviewer」回傳空 objection list。
       Parse error 的 reviewer 不計入「沉默」判定（無法表態 ≠ 沉默接受）。

       若有 parse error reviewer 且無成功 reviewer → 不適用此規則。

       滿足前提時：
       a) 將所有 active issues（open + partial_resolved）自動轉為 resolved
          （理由：reviewer 看過 primary 的處理/拒絕理由後選擇不再追究 = 隱式接受）
          （open 被 reject 的 issue：reviewer 已在 decisions_summary 看到 reject 理由，
            選擇不再重提 = 接受 primary 的 rejection）
       b) 自動 resolve 後 active_issues() == 0（必然）→ terminal_status="converged"

    4. MAX_ROUNDS: current_round >= max_rounds → terminal_status="max_rounds"
    5. BUDGET: 累計 cost >= max_budget_usd → terminal_status="budget"
    6. CANCEL: cancel_event.is_set() → terminal_status="cancelled"
    7. USER_TERMINATE: GUIDED 使用者 action="terminate" → terminal_status="cancelled"

    非收斂中止：
    8. ALL_REVIEWERS_OFFLINE → terminal_status="aborted", degraded=True
    """
```

**新增 IssueTracker method：**

```python
def auto_resolve_silent(self, current_round: int) -> list[str]:
    """
    [V3r4r2] 將所有 active issues（open + partial_resolved）轉為 resolved。
    回傳被自動 resolve 的 issue_key 列表（記入 round trace）。

    轉換規則：
    - resolution: open/partial_resolved → resolved
    - resolved_at_round: current_round
    - auto_resolved: True（新增標記，區分人為 resolve 與自動 resolve）
    """
```

**IssueState 新增欄位：**

```python
@dataclass
class IssueState:
    # ...existing fields...
    auto_resolved: bool = False  # [V3r4] True = 因 reviewer 沉默自動 resolve
```

**RefineRound trace 新增欄位：**

```python
@dataclass
class RefineRound:
    # ...existing fields...
    auto_resolved_keys: list[str] = field(default_factory=list)  # [V3r4]
```

### 設計理由

- **[V3r4r2]** 「沉默 = 接受」語義統一適用於所有 active issues，不僅限於 partial_resolved
- reviewer 已在 decisions_summary 看到 primary 對每個 issue 的處理理由（accept/reject/partial），選擇不再追究即為隱式接受
- 被 primary reject 的 open issue：reviewer 沉默 = 接受 primary 的 rejection reasoning
- parse error reviewer 排除在「沉默」判定外（F4 語義一致性：parse error ≠ 沉默）
- `auto_resolved` 標記確保 audit trail 可追溯，使用者在 GUIDED 模式下也能看到哪些是自動 resolve 的

---

## F2. [major] Collator Integration Contract

### V3r4 新增 §2.1

```
### 2.1 Collator Integration Contract [V3r4 NEW]

Collator 是 **ad-hoc LiteLLM call**，不是第四個 MagiNode。

定義：
- Collator 不屬於 engine.nodes[]（仍為 3 nodes）
- Collator 不加入 Decision.failed_nodes（有自己的 failure policy）
- Collator 使用獨立的 litellm.acompletion() 呼叫，不經過 MagiNode.query()
- Collator 有自己的 prompt，無 persona

Model 選擇（RefineConfig.collator_model）：
- 若 collator_model 為明確 model string → 直接使用
- 若 collator_model 為 None：
  - [V3r4r2 A5] 若 reviewer nodes 是 CLI nodes（CliMagiNode）→ skip collation，走 fallback
    （CLI node 的 model 屬性是 display string，非 LiteLLM model ID，無法用於 acompletion()）
  - 若 reviewer nodes 是 API nodes → 使用 reviewer_nodes[0].model
  （選 reviewer_nodes[0] 而非 primary 是因為 reviewer 通常是較便宜的模型）

Cost attribution：
- Collator 的 cost 計入 RefineRound.cost_usd（與 reviewer/primary 相同方式）
- Decision.total_cost 包含所有 collator calls
- Trace: collator_cost_usd 獨立欄位記入 RefineRound

Failure policy：
- Collator parse failure → retry once with schema hint
- Collator 二次 failure → skip collation，走 fallback（見下方正規化規則）
- [V3r4r2 A3] **Fallback 正規化**：不直接傳 raw objections 給 Primary。
  將每個 raw objection 包裝為 ConsolidatedObjection：
  ```python
  {
      "issue_key": objection.issue_key,
      "category": objection.category,
      "severity": objection.severity,
      "target": objection.target,
      "description": objection.description,
      "suggestions": [{"reviewer": objection.reviewer_name, "text": objection.suggestion}],
      "conflicting_suggestions": False,
      "source_reviewers": [objection.reviewer_name],
      "source_issue_keys": [objection.issue_key]
  }
  ```
  這確保 Primary 永遠只面對一種 input schema，無論 collation 成功或失敗。
- Collator failure 記入 RefineRound.collator_failed: bool = False
- Collator failure 不計入 Decision.failed_nodes（不是 node）
```

**RefineRound 新增欄位：**

```python
@dataclass
class RefineRound:
    # ...existing fields...
    collator_cost_usd: float = 0.0    # [V3r4]
    collator_failed: bool = False      # [V3r4] True = skip collation, raw objections sent
```

---

## F3. [major] GUIDED Callback Contract 完善

### V3r4 修改 §2 UserAction

```python
@dataclass
class UserAction:
    action: str  # "approve" | "override" | "terminate"
    overrides: list[UserOverride] | None = None  # [V3r4] 結構化 override

@dataclass
class UserOverride:  # [V3r4 NEW]
    issue_key: str
    verdict: str           # "accept" | "reject" | "partial"
    severity_after: str | None = None  # [V3r4] partial 時必填
    reasoning: str | None = None       # [V3r4] 可選，記入 trace
```

原本 `overrides: dict[str, str]` 改為結構化 `list[UserOverride]`，支援 partial + severity_after。

### V3r4 修改 §7 Failure Handling — 新增 GUIDED Failure Policy

```python
# GUIDED Failure Policy [V3r4 NEW]

# 1. Config validation（啟動時）
if config.guided and config.on_user_review is None:
    raise ValueError("on_user_review callback is required when guided=True")

# 2. Callback timeout
#    on_user_review 必須在 config.guided_timeout_seconds（預設 300）內回傳
#    [V3r4r2 A2] timeout 行為由 config.guided_timeout_policy 決定：
#    - "abort"（預設）→ terminal_status="aborted", abort_reason="user_review_timeout"
#    - "approve" → UserAction(action="approve")（靜默通過，需明確 opt-in）
#    記入 RefineRound: guided_timeout = True

# 3. Callback exception
#    on_user_review 拋出 exception → terminal_status="aborted"
#    abort_reason = "user_review_failed: {exception}"
#    Decision.degraded = True
```

**RefineConfig 新增：**

```python
@dataclass
class RefineConfig:
    # ...existing fields...
    guided_timeout_seconds: float = 300.0  # [V3r4] GUIDED callback timeout
    guided_timeout_policy: str = "abort"   # [V3r4r2] "abort" | "approve" — 預設安全中止
```

**RefineRound 新增：**

```python
@dataclass
class RefineRound:
    # ...existing fields...
    guided_timeout: bool = False  # [V3r4] True = callback 超時，靜默通過
```

---

## F4. [major] Parse Error Reviewer 在 UI 不可顯示為 Approve

### V3r4 修改 §6 compute_refine_votes

```python
def compute_refine_votes(
    primary_node_name: str,
    reviewer_nodes: list,
    last_round_objections: list[Objection],
    last_round_parse_errors: list[str],  # [V3r4] node names with parse errors
    ruling: str,
) -> dict[str, str]:
    """
    [V3r4] 三種 reviewer 狀態：

    - reviewer 本輪未提 objection 且無 parse error → votes[reviewer] = ruling（approve）
    - reviewer 本輪有提 objection → votes[reviewer] = dissent summary（reject）
    - reviewer 本輪 parse error → votes[reviewer] = "PARSE_ERROR"（invalid）

    「沉默 = 接受」僅適用於成功 parse 且回傳空 list 的 reviewer。
    Parse error 不是沉默，是無法表態。
    """
```

### V3r4 修改 §10 Dashboard UI — REFINE 專用 Verdict

```
[V3r4] Node 燈號邏輯：

- vote === ruling → 綠燈（approve）
- vote !== ruling && vote !== "PARSE_ERROR" → 紅燈（reject/dissent）
- vote === "PARSE_ERROR" → 黃燈 + "⚠ Parse Error" 標籤（不計入 approve/reject）
- failed_nodes → 灰燈（offline）
```

---

## F5. [major] Collator 保留互斥建議，不強制合併

### V3r4 修改 §4 Collator Prompt

替換 Collator 輸出 schema：

```json
[
  {
    "issue_key": "merged canonical key",
    "category": "error|risk|gap|improvement",
    "severity": "critical|major|minor",
    "target": "SECTION_ID",
    "description": "merged description",
    "suggestions": [
      {"reviewer": "reviewer1", "text": "suggestion text"},
      {"reviewer": "reviewer2", "text": "alternative suggestion"}
    ],
    "conflicting_suggestions": false,
    "source_reviewers": ["reviewer1", "reviewer2"],
    "source_issue_keys": ["system_key_1", "system_key_2"]
  }
]
```

**變更點：**

1. `"suggestion": string` → `"suggestions": list[{reviewer, text}]`
   - 保留每個 reviewer 的原始建議，不強制合併成單一句子
   - Primary 在 reflection 時可以看到所有 alternative

2. 新增 `"conflicting_suggestions": bool`
   - Collator 判斷多個 suggestion 是否互斥
   - `true` 時 Primary 的 reflection prompt 額外提示：「以下建議互相矛盾，請選擇最適合的方案或提出折衷」

3. `"issue_key"` → 加上 `"source_issue_keys": list[str]`（對應 Codex R6 + Claude R6 的 M1）
   - Collator 合併時保留所有原始 system key
   - Primary reflect 後，`resolve()` 對 source_issue_keys 中所有 key 套用相同 verdict
   - 解決「幽靈 open issue」問題

### V3r4 Collator Prompt 新增規則

```
Rules:
- NEVER drop an objection. If in doubt, keep it separate.
- NEVER add your own opinions or new issues.
- NEVER change the meaning of any objection.
- [V3r4] If two reviewers suggest DIFFERENT fixes for the SAME issue,
  set "conflicting_suggestions": true and keep BOTH suggestions separate
  in the "suggestions" array. Do NOT merge conflicting suggestions into one.
- [V3r4] Always include "source_issue_keys" listing ALL system-assigned keys
  that were merged into this consolidated entry.
```

### V3r4 修改 Primary Reflection Prompt [V3r4r2 A4]

**[V3r4r2]** Primary Reflection prompt 需同步更新以匹配新的 `suggestions` list schema：

```
You are the PRIMARY author. Reviewers raised these objections (consolidated).

<SYSTEM_INSTRUCTION priority="high">
IMPORTANT: Your job is to IMPROVE the proposal, not to please reviewers.
- DEFEND correct parts of your design, even under pressure.
- ACCEPT objections only when they genuinely improve the proposal.
- CHECK: does accepting X conflict with previously accepted Y?
- Blindly accepting everything produces incoherent results.
- [V3r4r2] Each objection may contain MULTIPLE suggestions from different reviewers.
  If "conflicting_suggestions" is true, the reviewers disagree on HOW to fix it.
  Evaluate each alternative and choose the best one, or propose a superior synthesis.
</SYSTEM_INSTRUCTION>

<UNTRUSTED_CONTENT source="reviewers (collated)" round="{n}">
{collated_suggestions_json}
</UNTRUSTED_CONTENT>

Your current proposal:
{current_proposal}

Respond with a JSON array:
[
  {
    "consolidated_id": "collated entry index (0-based)",
    "source_issue_keys": ["key1", "key2"],
    "verdict": "accept|reject|partial",
    "chosen_suggestion": "which reviewer's suggestion was adopted (or 'synthesis')",
    "reasoning": "honest explanation of your decision",
    "change_summary": "what changed (null if rejected)",
    "conflict_check": "conflicts with other changes? (null if none)",
    "severity_after": "for partial: remaining severity (null otherwise)"
  }
]

Then provide your REVISED PROPOSAL:

REVISED_PROPOSAL:
<full updated proposal>
```

### V3r4 修改 Primary Reflection — 支援 multi-key resolve

```python
# After Primary reflection, for each reflected item:
for reflection in reflections:
    source_keys = reflection["source_issue_keys"]  # [V3r4r2] Primary 回傳的 keys
    for key in source_keys:
        tracker.resolve(key, reflection["verdict"],
                       severity_after=reflection.get("severity_after"))
```

---

## 新增測試需求（V3r4）

| # | Test | 對應 Finding |
|---|------|-------------|
| T1 | `test_no_new_objections_auto_resolves_all_active` — reviewer 沉默時 open + partial_resolved 全部自動 resolve | F1+A1 |
| T2 | `test_silence_after_primary_reject_auto_resolves` — primary reject + reviewer 沉默 → open 自動 resolve | F1+A1 |
| T3 | `test_silence_with_parse_error_no_converge` — 有 parse error reviewer 且無成功 reviewer → 不適用 silence 規則 | F1+F4 |
| T4 | `test_collator_failure_fallback_normalized_schema` — Collator 失敗時 raw objections 正規化為 ConsolidatedObjection | F2+A3 |
| T5 | `test_collator_cost_attribution` — Collator cost 計入 round 和 total | F2 |
| T6 | `test_collator_cli_node_skip` — CLI node + collator_model=None → skip collation 走 fallback | F2+A5 |
| T7 | `test_guided_callback_timeout_aborts_by_default` — timeout + 預設 policy → abort | F3+A2 |
| T8 | `test_guided_callback_timeout_approve_opt_in` — timeout + policy="approve" → 靜默通過 | F3+A2 |
| T9 | `test_guided_callback_exception_aborts` — exception → abort | F3 |
| T10 | `test_guided_config_validation` — guided=True + no callback → ValueError | F3 |
| T11 | `test_guided_override_with_severity_after` — partial override 含 severity_after | F3 |
| T12 | `test_parse_error_reviewer_not_counted_as_approve` — parse error → PARSE_ERROR 票 | F4 |
| T13 | `test_parse_error_neutral_in_ui_summary` — PARSE_ERROR 在 UI final result 中立 | F4 |
| T14 | `test_collator_preserves_conflicting_suggestions` — 互斥建議不合併 | F5 |
| T15 | `test_collator_multi_key_resolve` — source_issue_keys 全部被 resolve | F5 |

**V3r4r2 測試總數：** 26 + 17 + 15 = **58 tests**

---

## 修訂影響範圍

| 檔案（預計） | 變更 |
|-------------|------|
| `refine_types.py` | IssueState.auto_resolved, UserOverride, RefineRound 新欄位, guided_timeout_policy |
| `refine_protocol.py` | check_convergence(+parse_errors), auto_resolve_silent(open+partial), GUIDED timeout policy |
| `refine_collator.py` | 新 prompt + output schema + fallback 正規化 + CLI node 偵測 |
| `refine_reflection.py` | 更新 prompt（suggestions list + conflicting + consolidated_id）+ multi-key resolve |
| `refine_votes.py` | compute_refine_votes() 新增 parse_errors 參數 |
| `web/static/index.html` | PARSE_ERROR 黃燈 + auto_resolved 標記 + final result neutral |
| `tests/` | 15 new tests |

---

## 對其他審閱者的回應

### Claude R6 的 M1（Collator key 分歧）
→ 由 F5 的 `source_issue_keys` 方案解決（採用 Claude 建議的方案 A）

### Claude R6 的 M2（collator_model fallback）
→ 由 F2 的 Collator Integration Contract 解決

### Claude R6 的 n1（guided 驗證）
→ 由 F3 的 config validation 解決

### Claude R6 的 n2（ask(mode="refine") 限制文件化）
→ 維持不變，在 §2 API Design 補一句：
> `ask(mode="refine")` 僅支援預設 RefineConfig。需自訂配置請直接呼叫 `engine.refine(query, config=RefineConfig(...))`。

### Claude R6 的 n3（Callable → UserReviewCallback）
→ 隨 F3 一併修正：

```python
# RefineConfig
on_user_review: UserReviewCallback | None = None  # [V3r4] 改用 Protocol type
```
