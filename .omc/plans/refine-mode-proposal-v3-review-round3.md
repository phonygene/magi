# REFINE Mode Proposal V3r3 — Round 3 審核報告

**審核日期**: 2026-04-01
**審核對象**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md` (V3r3)
**審核方式**: 文件全文審核 + 對照 decision.py / engine.py / logger.py / index.html
**結論**: ✅ **ACCEPT-WITH-RESERVATIONS**（2 個 major 實作時處理，3 個 minor 可後補）

---

## 統計摘要

| 嚴重度 | 數量 |
|--------|------|
| Critical | 0 |
| Major | 2 |
| Minor | 3 |

---

## Round 5 Findings 解決確認

| Finding | 來源 | 狀態 |
|---------|------|------|
| partial_resolved 排除在 active_issues() → 偽收斂 | Codex R2 Critical | ✅ active_issues() 已含 partial_resolved |
| escalated/override/compromise 提前結案 | Codex R2 Critical | ✅ 仲裁機制整體移除（D8），狀態機 6→4 態 |
| distinct_reviewers 誤判當前 dissent | Codex R2 Major | ✅ votes/minority_report 改用最後一輪 objection list |
| reconcile_cross_round() 只查 open | Codex R2 Major | ✅ 擴大查詢：open + partial_resolved + 近 2 輪 resolved |
| votes UI 在非正常停止誤顯 APPROVED | Codex R2 Major | ✅ terminal_status + REFINE 專用 verdict 邏輯 |
| category 硬約束未文件化 | Claude R2 Minor | ✅ 文件化為設計意圖（reconcile §5 已說明） |
| severity 降級語義矛盾 | Claude R2 Minor | ✅ partial verdict 允許 severity_after 降級，resolve() 負責更新 |

**結論：Round 5 全部 7 個 finding 均已正確解決。**

---

## 8 維度評估

### 1. Architecture Fit ✅

- `engine.refine()` 獨立方法，不改 `ask()` 簽名 ✓
- thin dispatch `elif mode == "refine": return await self.refine(query)` ✓
- 成本聚合改為 protocol 內部逐 call 累加，非 `sum(n.last_cost_usd)` ✓
- `Decision` 擴充僅加 `refine_summary: dict | None` + `refine_trace_id: str | None`，兩者皆 primitive，`asdict()` + `to_jsonl()` 相容 ✓
- `_resolve_cost_mode()` 從 engine.py:134-144 抽取 ✓

### 2. Protocol Correctness ✅

流程：Primary → Reviewers (並行) → canonicalize → merge_similar_keys → reconcile_cross_round → Collator → Primary reflection → [GUIDED gate] → revise → re-review

- parse failure 與 reject 完全分離，不計入 rejected_count ✓
- ALL_REVIEWERS_OFFLINE → abort（非偽收斂）✓
- budget check 在每輪開始前執行 ✓
- sycophancy detection 為警告不為中止（合理）✓

### 3. State Machine ✅

4 態模型（open / resolved / partial_resolved / reopened）完整、無歧義：

| Transition | 觸發 | 結果 |
|-----------|------|------|
| open + accept | resolve() | resolved |
| open + reject | resolve() | open (rejected_count++) |
| open + partial | resolve() | partial_resolved (severity 降級) |
| resolved/partial_resolved + new objection | upsert() | reopened → open（暫態） |

移除 escalated/wontfix 是正確決策；仲裁移除後這兩個狀態無存在必要。

### 4. Collator Design ⚠️（見 M1）

角色定義清晰（去重彙整、不裁決、不過濾）。Prompt 設計合理，含「NEVER drop」規則。
但存在一個 major gap：Collator 合併兩個來自不同 reviewer、但 system 已分配了不同 issue_key 的 objection 後，其輸出中 `"issue_key": "merged canonical key"` 可能與 IssueTracker 的 key 不一致（見 M1）。

### 5. GUIDED Design ✅（見 n1）

ON/OFF 設計清晰，UserAction（approve/override/terminate）語義完整。
`on_user_review` callback 在 `guided=True` 時為必填，但缺乏 runtime 驗證（見 n1）。

### 6. UI Contract ✅

terminal_status 分支覆蓋完整（converged / threshold / max_rounds / budget / cancelled / aborted）。
Node 燈號邏輯正確：`compute_refine_votes()` 預設 approve=ruling，objection=dissent string，現有比較邏輯（vote === ruling）可正確運作。
`minority_report` 改標為「殘餘異議」，不再與 vote-mode 的 DISSENT 混淆。

### 7. Data Model ✅

所有 dataclass 欄位型別明確，serialization-safe（primitive only 在 Decision 擴充）。
`IssueState` 含 severity / category / latest_description / resolved_at_round ✓
`RefineRound` 含 proposal_text（供 audit/rollback）✓
`RefineRound.collated_suggestions` 含 Collator 彙整結果 ✓
`RefineRound.user_overrides` 含 GUIDED 使用者推翻記錄 ✓

### 8. Test Coverage ✅

26 unit + 17 integration = 43 tests。V3r3 新增 10 個 unit tests 針對新功能，覆蓋：
- active_issues() 含 partial_resolved
- cross-round reconcile（open/partial/recently resolved/category 硬約束）
- collator dedup + no-drop
- GUIDED 三種 action（approve/override/terminate）
- terminal_status UI 各值
- reviewer 收到 decisions_summary
- severity 升降邏輯

唯一缺口：無 `test_collator_key_reconciliation` 驗證 Collator 輸出 key 與 tracker key 的一致性（與 M1 相關）。

---

## Findings

### M1. [major] Collator 合併跨 reviewer 的 objection 時，issue_key 與 IssueTracker 可能分岐

**問題**

`reconcile_cross_round()` 在 Collator 之前執行，已為每個 objection 分配穩定的 system `issue_key`。但若兩個 reviewer 對同一問題提出的 objection，因語義相近但文字差異超過 0.80 threshold 而分配了不同的 system key（例如 `s1_arch::error::no_replica` 和 `s1_arch::error::single_point_of_failure`），Collator 會將它們合併為一個建議，輸出一個 `"issue_key": "merged canonical key"`。

此後：
- Primary 的 reflection 只回應這個合併建議（一個 issue_key）
- `IssueTracker.resolve()` 只更新一個 key
- 另一個 tracker entry 維持 `open`，但 Primary 實際已處理了它
- 下一輪 `active_issues()` 仍會計算這個「幽靈 open issue」，影響收斂判定與 confidence

**證據**

- Collator prompt (§4): 輸出 `"issue_key": "merged canonical key"`，但 system 已賦值的 key 可能有兩個
- Primary Reflection prompt (§4): `"issue_key": "{system_assigned_key}"` — 合併情況下「system_assigned_key」指哪一個未定義
- `IssueTracker.resolve()` 只接受單一 `issue_key` 參數 (§3)

**建議**

選擇以下之一明確化：

**方案 A（推薦）**：Collator 輸出 `"source_issue_keys": ["key1", "key2"]`（列表）。System 在傳給 Primary 前，以第一個 key 作為代表 key，Primary reflection 後 `resolve()` 對所有 source keys 依相同 verdict 更新。

**方案 B**：明文規定 Collator 不可合併擁有不同 system key 的 issue，只能合併 key 相同的重複 objection（即不同 reviewer 提出但 system 已給同一 key 的）。

---

### M2. [major] `collator_model` fallback 邏輯未定義

**問題**

`RefineConfig.collator_model: str | None = None`，注釋「None = 使用最低消耗 node」，但未定義：
1. 「最低消耗」的判斷依據是什麼（`cost_per_token`？LiteLLM model tier？）
2. 若 nodes 沒有 cost metadata，如何 fallback？
3. Collator 是直接呼叫現有 node（佔用 reviewer 的 node object）還是建立新的 LiteLLM call？

若實作者各自解釋，可能在 cost 計算和 trace 上出現不一致。

**證據**

- `RefineConfig` (§2): `collator_model: str | None = None  # None=使用最低消耗 node`
- 成本模型 (§9): 只說 "低消耗模型"，未定義 fallback 選擇邏輯
- `refine_protocol()` 函數簽名 (§2): 未含 collator 選擇邏輯

**建議**

在 §2 或 §7 明確定義 fallback：

```
collator_model=None 時：
  - 若 nodes 中有 cost_per_token：取 min(reviewer_nodes, key=...)
  - 否則：使用 reviewer_nodes[0]（第一個非 primary node）
Collator 呼叫方式：直接建立 LiteLLM call（model string），不重用 MagiNode 物件
cost 計入 RefineRound.cost_usd（與 reviewer/primary 相同方式）
```

---

### n1. [minor] `guided=True` + `on_user_review=None` 缺乏提前驗證

**問題**

`RefineConfig` 注釋「guided=True 時必填」，但沒有 dataclass validator 或 `refine_protocol()` 啟動驗證。若使用者傳入 `guided=True` 而忘記設定 callback，第一輪 GUIDED gate 才會 crash（AttributeError on None call），且 traceback 遠離設定點。

**建議**

在 `refine_protocol()` 起始或 `RefineConfig.__post_init__()` 加入：
```python
if self.guided and self.on_user_review is None:
    raise ValueError("on_user_review callback is required when guided=True")
```

---

### n2. [minor] `ask(mode="refine")` thin dispatch 不支援 config，限制未文件化

**問題**

`ask(mode="refine")` 呼叫 `self.refine(query)`（不帶 config），永遠使用 `RefineConfig()` 預設值。使用者若透過 `ask()` 呼叫，無法自訂 `max_rounds`、`guided`、`max_budget_usd` 等參數。此限制在 §2 或 §13 CLI integration 均未說明。

**建議**

在 §2 API Design 或 §13 Implementation Priority 補一句：
> `ask(mode="refine")` 僅支援預設 RefineConfig。需自訂配置請直接呼叫 `engine.refine(query, config=RefineConfig(...))`。

---

### n3. [minor] `on_user_review` 型別標注過寬（`Callable` 而非 `UserReviewCallback`）

**問題**

`RefineConfig.on_user_review: Callable | None = None`，但 `UserReviewCallback` Protocol 已在同節定義（§2），且為 async。使用 `Callable` 無法在靜態分析時偵測傳入同步函數的錯誤。

**建議**

改為：
```python
on_user_review: UserReviewCallback | None = None
```

---

## 架構評估摘要

| 維度 | 評分 | 說明 |
|------|------|------|
| Architecture Fit | ✅ | 完整整合，無破壞性變更 |
| Protocol Correctness | ✅ | 流程邏輯正確，無 deadlock 風險 |
| State Machine | ✅ | 4 態完整，transition table 清晰 |
| Collator Design | ⚠️ | key management 邊界未定義（M1） |
| GUIDED Design | ✅ | ON/OFF gate 設計清晰 |
| UI Contract | ✅ | terminal_status 完整，燈號邏輯正確 |
| Data Model | ✅ | serialization-safe，欄位完整 |
| Test Coverage | ✅ | 43 tests，邊界條件覆蓋充分 |

---

## 整體評價

V3r3 相比 V3r2 有顯著結構改善：

1. **仲裁移除正確**：真實工作流中分歧靠迭代解決，arbitration judge 只增加複雜度而無收益
2. **Collator 角色清晰**：無狀態、不裁決、只去重，職責邊界明確
3. **GUIDED P0 設計合理**：ON/OFF toggle + 三種 UserAction，實作門檻低
4. **狀態機簡化有效**：4 態 + partial_resolved 計入 active_issues，偽收斂問題解決
5. **UI contract 完整**：terminal_status 徹底取代 approveCount >= 2 誤判

2 個 major finding（M1、M2）均屬**實作合約缺口**，不是架構錯誤。M1 需在實作前確定 Collator key 合併策略；M2 需定義 collator_model fallback 邏輯。兩者皆有明確可行的修復方案，不需要再啟動 proposal revision 輪次。

**建議直接進入實作，M1/M2 在 `refine_types.py` + Collator prompt 設計時一併解決，3 個 minor 可在 code review 時補齊。**
