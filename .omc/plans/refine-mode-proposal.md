# REFINE Mode Design Proposal for MAGI

**Date:** 2026-03-31
**Status:** Draft — Pending Cross-Model Review

---

## 一、核心概念

### REFINE vs 現有協議的本質差異

| 特性 | Vote | Critique (ICE) | **REFINE** |
|------|------|----------------|------------|
| 角色關係 | 三方平等投票 | 三方互相糾錯 | **主從式：1主 + N審** |
| 產出物 | 選出多數答案 | 辯論後綜合答案 | **主模型迭代精煉的方案** |
| 收斂條件 | 多數決 | agreement_score > 0.8 | **審閱者無新異議** |
| 互動方向 | 單輪 | 多輪雙向批判 | **單向審→主模型反省→再審** |
| 使用者角色 | 無 | 無 | **GUIDED 模式下每輪介入** |

### 核心流程

```
使用者提問
    ↓
主模型(Melchior)產出初始方案
    ↓
┌─→ 審閱模型(Balthasar, Casper)並行審閱，各自提出具體異議
│       ↓
│   主模型逐點反省：接受/拒絕每個異議，說明理由
│       ↓
│   主模型產出修訂方案
│       ↓
│   [GUIDED模式] 使用者審閱修訂方案 → 微調/調整方向 → 核可
│       ↓
│   審閱模型再次審閱修訂方案
│       ↓
│   收斂檢查：還有新異議嗎？
│       ↓
└── 有 → 回到審閱（最多 max_rounds）
    ↓
    無 → 收斂，交由使用者最終裁決
```

## 二、協議函數簽名

```python
# magi/protocols/refine.py

async def refine(
    query: str,
    nodes: list,                    # nodes[0] = 主模型, nodes[1:] = 審閱者
    max_rounds: int = 5,            # 最大迭代輪數
    convergence_threshold: int = 0, # 允許的殘餘異議數（0=完全收斂）
    guided: bool = False,           # GUIDED REFINE 子模式
    on_user_review: Callable | None = None,  # GUIDED 模式的使用者回調
    on_round_event: Callable | None = None,  # 事件串流回調
) -> Decision:
```

## 三、資料結構

### 異議點 (Objection)

```python
@dataclass
class Objection:
    id: str                    # "R1-B-01" (Round1-Balthasar-01)
    reviewer: str              # 審閱者節點名稱
    category: str              # "error" | "risk" | "gap" | "improvement"
    severity: str              # "critical" | "major" | "minor"
    target: str                # 針對方案的哪個部分
    description: str           # 具體異議內容
    suggestion: str | None     # 建議的修改方式
```

### 反省回應 (Reflection)

```python
@dataclass
class Reflection:
    objection_id: str          # 對應的異議 ID
    accepted: bool             # 主模型是否接受
    reasoning: str             # 接受/拒絕的理由
    action: str                # "incorporated" | "rejected" | "partially_adopted"
```

### REFINE Decision 擴展

```python
# Decision 增加的欄位
class Decision:
    # ... 現有欄位 ...
    refine_rounds: list[RefineRound] = field(default_factory=list)

@dataclass
class RefineRound:
    round_num: int
    proposal: str              # 該輪主模型方案
    objections: list[Objection]
    reflections: list[Reflection]
    user_feedback: str | None  # GUIDED 模式下的使用者意見
    remaining_issues: int      # 未解決異議數
```

## 四、Prompt 設計

### 主模型初始產出

```
You are the PRIMARY author. Produce a comprehensive proposal for:
{query}

Structure your response clearly with numbered sections.
Be thorough — reviewers will challenge every weak point.
```

### 審閱者 Prompt

```
You are a REVIEWER examining a proposal. Your job is NOT to rewrite it,
but to find specific issues that need addressing.

Original question: {query}
Current proposal (by {primary_node}):
{proposal}

{previous_round_context}

For each issue found, respond in EXACTLY this format:

OBJECTION [category] [severity]:
TARGET: <which section/point this targets>
ISSUE: <specific problem description>
SUGGESTION: <concrete fix recommendation>
---

Categories: error | risk | gap | improvement
Severity: critical | major | minor

If the proposal is sound and you have no objections, respond:
NO_OBJECTIONS

Be rigorous but fair. Only raise genuine issues, not stylistic preferences.
```

### 主模型反省 Prompt

```
You are the PRIMARY author. Reviewers have raised the following objections
to your proposal. For EACH objection, you must:

1. Genuinely consider whether they have a valid point
2. Decide: ACCEPT, REJECT, or PARTIALLY_ADOPT
3. Explain your reasoning honestly

{formatted_objections}

Respond for each objection in EXACTLY this format:

REFLECTION [objection_id]:
VERDICT: accept | reject | partially_adopt
REASONING: <honest explanation>
---

Then provide your REVISED PROPOSAL incorporating accepted changes:

REVISED_PROPOSAL:
<full updated proposal>
```

### GUIDED 模式 — 使用者裁決提示

```
=== Round {n} 完成 ===

主模型修訂方案:
{revised_proposal}

異議處理摘要:
- 接受: {n_accepted} 項
- 拒絕: {n_rejected} 項 (理由已列出)
- 部分採納: {n_partial} 項

請審閱並選擇:
[1] 核可 → 送交下一輪審閱
[2] 微調 → 輸入您的修改意見，主模型將據此調整
[3] 調整方向 → 輸入新的方向指示
[4] 終止 → 以目前方案作為最終結果
```

## 五、WebSocket 事件流

| 事件 | 欄位 | 時機 |
|------|------|------|
| `refine_start` | query, primary_node, reviewers[], max_rounds, guided | 開始 |
| `refine_proposal` | round, proposal, node | 主模型產出/修訂方案 |
| `refine_review_start` | round, reviewer | 審閱者開始審閱 |
| `refine_objections` | round, reviewer, objections[], count | 審閱者提出異議 |
| `refine_reflection` | round, reflections[], accepted, rejected, partial | 主模型反省結果 |
| `refine_revised` | round, proposal | 修訂後方案 |
| `refine_user_wait` | round, proposal, summary | GUIDED: 等待使用者裁決 |
| `refine_user_input` | round, feedback, action | GUIDED: 使用者回饋 |
| `refine_converged` | round, remaining_issues | 收斂判定 |
| `decision` | (標準欄位) | 最終結果 |

## 六、收斂機制

```python
def _check_convergence(
    current_objections: list[Objection],
    previous_objections: list[Objection] | None,
    threshold: int = 0,
) -> tuple[bool, str]:
    """
    收斂條件（滿足任一即收斂）:
    1. 所有審閱者回覆 NO_OBJECTIONS
    2. 僅剩 minor 級異議且數量 ≤ threshold
    3. 連續兩輪的異議完全相同（震盪檢測 → 強制收斂）
    4. 達到 max_rounds（由外層控制）

    Returns: (converged: bool, reason: str)
    """
```

## 七、階段整合流程 (Staged Pipeline)

```
PHASE 1: 架構設計 (GUIDED REFINE)
    使用者 + 主模型討論 → 審閱者審批 → 迭代 → 使用者拍板架構

PHASE 2: 模塊拆分 (REFINE)
    主模型根據架構拆分模塊 → 審閱者檢查拆分合理性
    → 自動收斂 → 產出優先排序的模塊清單

PHASE 3: 逐模塊精煉 (REFINE × N)
    For each module in priority_order:
        REFINE(module_spec) → 收斂 → 產出該模塊的最終方案
```

### 函數簽名

```python
# magi/protocols/staged.py

async def staged_refine(
    query: str,
    nodes: list,
    on_user_review: Callable | None = None,
    on_round_event: Callable | None = None,
) -> StagedDecision:
```

### StagedDecision

```python
@dataclass
class StagedDecision:
    query: str
    architecture: Decision        # Phase 1 結果
    modules: list[ModuleSpec]     # Phase 2 拆分結果
    module_decisions: list[Decision]  # Phase 3 各模塊結果
    total_rounds: int
    total_cost_usd: float
```

### WebSocket 事件（階段層級）

| 事件 | 欄位 |
|------|------|
| `staged_start` | query, phases[] |
| `staged_phase` | phase_num, phase_name, status |
| `staged_modules` | modules[] (名稱+優先度+依賴) |
| `staged_module_start` | module_index, module_name |
| `staged_module_done` | module_index, decision |
| `staged_complete` | total_rounds, total_cost |

## 八、Dashboard UI 設計

Mode dropdown 增加：
- `refine` — 自動精煉
- `guided-refine` — 使用者引導精煉
- `staged` — 階段整合流程

### REFINE 視覺化

```
┌─────────────────────────────────────────────┐
│  Round 1                                     │
│  ┌──────────────────────────────────┐       │
│  │ Melchior (Primary)               │       │
│  │ [初始方案全文]                     │       │
│  └──────────────────────────────────┘       │
│                                              │
│  ┌──────────────┐  ┌──────────────┐         │
│  │ Balthasar    │  │ Casper       │         │
│  │ ⚠ 3 異議     │  │ ⚠ 2 異議     │         │
│  │ 🔴 1 critical│  │ 🟡 1 major   │         │
│  │ 🟡 1 major   │  │ 🟢 1 minor   │         │
│  │ 🟢 1 minor   │  │              │         │
│  └──────────────┘  └──────────────┘         │
│                                              │
│  ┌──────────────────────────────────┐       │
│  │ Melchior 反省                     │       │
│  │ ✅ 接受 3  ❌ 拒絕 1  🔄 部分 1   │       │
│  │ [修訂方案]                        │       │
│  └──────────────────────────────────┘       │
│                                              │
│  [GUIDED: 使用者輸入框 + 核可/微調/終止]     │
├─────────────────────────────────────────────┤
│  Round 2  ...                                │
├─────────────────────────────────────────────┤
│  ✅ 收斂 (Round 3, 0 remaining issues)       │
│  [最終裁決: 拍板定案 / 繼續迭代]              │
└─────────────────────────────────────────────┘
```

## 九、實作優先順序

| 階段 | 內容 | 依賴 |
|------|------|------|
| **P0** | `Objection`/`Reflection` 資料結構 | 無 |
| **P0** | `refine()` 協議函數（不含 guided） | Decision, Node |
| **P1** | Prompt 解析器（異議/反省提取） | P0 |
| **P1** | 收斂檢測（含震盪偵測） | P0 |
| **P2** | WebSocket 事件串流 | P0, server.py |
| **P2** | GUIDED 模式（on_user_review 回調） | P0 |
| **P3** | Dashboard UI（REFINE 視覺化） | P2 |
| **P4** | `staged_refine()` 階段整合 | P0-P3 |
| **P4** | Dashboard staged 視覺化 | P3 |

## 十、與現有架構的整合點

```
magi/
├── core/
│   ├── decision.py      ← 增加 refine_rounds, RefineRound
│   ├── engine.py         ← ask() 增加 mode="refine"|"guided-refine"|"staged"
│   └── (node/cli 不動)
├── protocols/
│   ├── refine.py         ← 新檔案：refine(), _parse_objections(), _parse_reflections()
│   ├── staged.py         ← 新檔案：staged_refine()
│   ├── critique.py       ← 不動
│   └── judge.py          ← refine 用來評估剩餘分歧（複用）
└── web/
    ├── server.py          ← 增加 refine/staged 事件處理
    └── static/index.html  ← 增加 REFINE UI 區塊
```

## 十一、關鍵設計決策

1. **主從式而非對等式** — 與 critique 的本質區別。主模型擁有方案所有權，審閱者只提異議不改寫。
2. **結構化異議** — 不是自由文本批評，而是有 category/severity/target 的格式化異議，方便追蹤和視覺化。
3. **強制反省** — 主模型必須逐點回應，不能忽略異議，但有權拒絕並說明理由。
4. **震盪檢測** — 避免主模型和審閱者在同一問題上無限循環。
5. **GUIDED 作為回調** — 不是另一個協議，而是 refine() 的一個參數，通過 on_user_review 回調實現，WebSocket 層負責等待使用者輸入。
6. **Staged 建立在 REFINE 之上** — staged_refine() 是 refine() 的編排層，不重複實作。
