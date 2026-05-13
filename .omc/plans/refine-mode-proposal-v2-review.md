# REFINE Mode Proposal V2 — 審批報告

**審批日期**: 2026-04-01
**審批結論**: ⚠️ **REVISE** (需修訂後重審)
**來源提案**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md`

---

## 統計摘要

| 嚴重度 | 數量 |
|--------|------|
| Critical | 3 |
| Major | 6 |
| Minor | 5 |
| 缺失項 | 6 |

---

## Critical Findings

### C1. `ask()` 的 mode 參數擴展與現有分發邏輯矛盾

- **類別**: API 設計 / 架構相容性
- **問題**: 提案將 `"refine"` 和 `"guided-refine"` 加入 `ask()` 的 mode 參數。但現有 `engine.py` 的 `ask()`，所有協議都接受 `(query, nodes)` 並回傳 `Decision`。REFINE 的函數簽名需要 `max_rounds`, `convergence_threshold`, `guided`, `on_user_review`, `on_round_event`, `max_budget_usd` 六個額外參數——這些無法透過單一 `mode: str` 傳遞。
- **風險**: 實作者將面臨：(a) 在 `ask()` 加 `**kwargs` 破壞型別安全，或 (b) 為 refine 建立完全不同的入口點，使 `mode="refine"` 成為空殼。
- **建議**: 明確選擇一個方案：`ask()` 接受 `RefineConfig` dataclass 作為 optional 參數（`config: RefineConfig | None = None`），或 REFINE 不經過 `ask()` 而是獨立方法 `engine.refine(query, **kwargs)`。不要兩邊都宣稱支援。

### C2. 主模型失效「提升審閱者」與節點架構根本不相容

- **類別**: 容錯 / 實作可行性
- **問題**: 提案稱主模型失效時「提升審閱者」為新主模型。但在 `cli-multi` 模式下三個節點是**不同 CLI 工具**（Claude/Codex/Gemini），提升 Codex 節點為「主模型」意味著它要執行原本為 Claude 設計的 reflection prompt——不同模型對 JSON 結構化輸出的遵循度差異巨大。
- **風險**: 提升後的節點可能完全無法解析 reflection 所需的結構化 JSON 格式，導致整個流程崩潰而非優雅降級。
- **建議**: (1) 限制「提升」僅在同質節點模式下可用，(2) 異質模式下主模型失效應直接降級為 critique 協議，(3) 為每種 adapter 定義 JSON 輸出可靠度等級。

### C3. `Decision` dataclass 擴展方案未定義

- **類別**: 資料結構 / 架構相容性
- **問題**: 提案稱 Decision 要新增 `refine_rounds` 和 `refine_summary` 欄位，但現有 `Decision` 是 frozen-style dataclass，`to_jsonl()` 用 `asdict(self)` 直接序列化。新增複雜巢狀物件可能無法直接 JSON 序列化。
- **風險**: 破壞現有 trace logger 和任何讀取 JSONL 的下游工具。
- **建議**: (1) 給出完整欄位定義含型別，(2) 新欄位必須有 `default_factory` 確保向後相容，(3) 嵌套結構必須實作 `asdict` 相容的序列化。

---

## Major Findings

### M1. `staged()` API 與 `ask()` 的關係模糊

- **類別**: API 設計
- **問題**: 同時引入 `ask(mode="refine")` 和獨立的 `staged()` 方法，未說明何時該用哪個、Staged 內部是否呼叫 refine、兩者的 WebSocket 事件流是否共用。
- **風險**: 使用者困惑，長期維護成本翻倍。
- **建議**: 明確定義 `staged()` 是 `refine` 的多階段編排層，或砍掉 `ask(mode="refine")`。

### M2. 收斂機制「無新異議」定義不精確

- **類別**: 邏輯正確性
- **問題**: 「無新異議」和「0 個未解決異議」是不同概念。審閱者可能每輪提出新異議同時主模型解決舊異議，永遠達不到 0。另外 `rejected_count >= 2` 觸發仲裁——如果主模型合理拒絕同一異議兩次，強制仲裁反而破壞主模型自主權。
- **風險**: 合理拒絕被錯誤升級為仲裁；收斂條件可能永遠達不到。
- **建議**: (1) 區分「新異議數 = 0」和「未解決異議數 <= threshold」，(2) rejected 升級應要求審閱者附加新證據否則視為已解決，(3) 增加「連續兩輪無新異議」作為替代收斂條件。

### M3. 成本模型低估 judge 呼叫

- **類別**: 效能 / 成本
- **問題**: 提案稱「基礎 REFINE 3 輪: ~10 calls」，但未明確 REFINE 是否使用 LLM judge（如同 critique 的 `estimate_agreement`）。若使用 judge，每輪多 1 call，3 輪 ≈ 15 calls 而非 10。
- **風險**: 預算控制基於錯誤估算，用戶可能超支。
- **建議**: 列出每輪精確呼叫明細，明確 REFINE 是否使用 LLM judge 還是純 issue_key 收斂。

### M4. GUIDED 模式 300 秒超時過於武斷

- **類別**: 設計品質 / UX
- **問題**: 使用者可能正在認真閱讀複雜架構方案，5 分鐘不夠。
- **風險**: 使用者正在思考時被強制 auto_approve 或 terminate。
- **建議**: (1) 預設行為應為 `pause` 而非 `auto_approve`，(2) 用 heartbeat 取代固定超時，(3) 預設至少 600 秒。

### M5. `on_user_review` callback 介面未定義

- **類別**: API 設計 / 實作可行性
- **問題**: GUIDED 模式的核心互動機制，但參數簽名、回傳值、是否 async、在 CLI vs WebSocket 模式下如何實作，全部未定義。
- **風險**: 實作者無法開始寫 GUIDED 模式。
- **建議**: 定義 `on_user_review: Callable[[RoundResult], Awaitable[UserFeedback]] | None`，明確 `RoundResult` 和 `UserFeedback` 的結構。

### M6. WebSocket 事件流過度膨脹

- **類別**: 設計品質 / 維護性
- **問題**: 現有協議 ~8 種事件，REFINE 新增 14 種。前端是 vanilla HTML/JS，每個事件需手寫 DOM 操作。
- **風險**: 前端實作成為瓶頸。
- **建議**: 合併語義相近事件，目標 6-8 個。考慮通用事件格式 `{event: "refine_phase", phase: "review|reflect|revise|converge", data: {...}}`。

---

## Minor Findings

### m1. `nodes[0] = 主模型` 的隱式約定

REFINE 對主模型依賴更重，應顯式化：在 `refine()` 簽名中分離 `primary_node` 和 `reviewers`，或在 docstring 中強制說明。

### m2. `_rounds.jsonl` 缺乏清理機制

REFINE 每次可能產生 5 輪完整 round 資料，無清理策略 = 磁碟膨脹。

### m3. Staged DAG 拓撲排序的實作複雜度被低估

未定義 DAG 資料結構、依賴宣告方式、失敗模塊的 DAG 重排策略。本身是完整子系統。

### m4. `UNTRUSTED_CONTENT` 防注入標籤不足

方向正確但 LLM 不可靠地遵守標籤邊界。應搭配結構化輸出（JSON mode）和 output validation。

### m5. `max_budget_usd` 檢查時機未定義

每次 LLM 呼叫前？每輪結束？超預算時立即中止還是完成當前輪？

---

## 缺失項

1. **Rollback 策略**: 若第 3 輪方案比第 1 輪更差，沒有「回退到最佳輪」的機制。
2. **並行安全**: Staged Phase 3 多個並行 refine session 共用同一組 nodes，node 狀態可能互相污染。
3. **取消/中斷機制**: 長時間 REFINE 可能跑 10+ 分鐘，CancelledError 處理路徑未定義。
4. **Reflection 解析全失敗的 fallback**: 提案只說 repair 最多 1 次，但「repair 也失敗且視為全部 reject」是否合理——等於審閱者白審了。
5. **Issue key 生成規則**: 由誰生成？不同審閱者對同一問題用不同 key 怎麼辦？
6. **REFINE vs Critique 品質對比**: 未提供任何 benchmark 或 A/B 測試計畫來驗證 REFINE 優於 critique。

---

## 歧義風險

| 歧義點 | 解讀 A | 解讀 B | 影響 |
|--------|--------|--------|------|
| 收斂是否需要 judge | 用 issue_key 狀態機取代 agreement_score（不需 judge） | issue_key 與 agreement_score 並行（需 judge） | 成本差異 30-50% |
| `guided: bool` vs `mode: "guided-refine"` | 後者是前者的語法糖 | 兩者是不同的協議實作 | 若 B 則代碼重複 |

---

## 升級為 ACCEPT 的條件

1. 解決 C1-C3（具體 API 簽名、節點提升限制、Decision 欄位定義）
2. 解決 M2（收斂條件精確化）和 M5（callback 介面定義）
3. 資料結構給出完整 Python dataclass 定義
4. 明確 issue_key 收斂 vs agreement_score 的關係
5. Staged Pipeline 考慮拆為獨立提案或降為 P2+
