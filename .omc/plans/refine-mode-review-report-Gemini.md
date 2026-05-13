# Review Report: MAGI REFINE Mode Proposal

**Date:** 2026-04-01
**Reviewer:** Gemini CLI (Cross-Model Review)
**Status:** Approved with Required Changes

---

### [Section 1] Architecture Fit
**Verdict:** APPROVE

**Strengths:**
- `async def refine(...) -> Decision` 模式與現有協定高度一致。
- 使用 `on_user_review` 回調處理 `GUIDED` 模式，有效分離了通訊層與核心邏輯。

**Issues:**
- [severity: minor] **向下相容性**: `Decision` 類增加 `refine_rounds` 欄位需確保舊資料反序列化不報錯。
  → **建議**: 在 `Decision` 定義中使用 `field(default_factory=list)` 並在 UI 增加容錯。

---

### [Section 2] Protocol Design
**Verdict:** NEEDS_REVISION

**Issues:**
- [severity: major] **順從性陷阱 (Sycophancy)**: LLM 作為主模型時，容易為了快速結案而盲目接受審閱者意見。
  → **建議**: 在 Prompt 中明確指令主模型捍衛合理設計；若連續兩輪接受率 100% 且仍有異議，應觸發警告。
- [severity: major] **拒絕死結 (Rejection Deadlock)**: 若主模型重複拒絕 `critical` 異議會導致無限循環。
  → **建議**: 實作「升級機制」，將重複被拒絕的異議轉交 `judge.py`（第三方仲裁）或使用者裁決。

---

### [Section 3] Prompt Engineering
**Verdict:** YES_WITH_CHANGES

**Issues:**
- [severity: minor] **Context 指數增長**: 多輪迭代會造成 Token 浪費與模型混亂。
  → **建議**: 對 `previous_round_context` 進行「狀態化摘要」，僅保留尚未解決與剛修正的異議。
- [severity: minor] **震盪檢測魯棒性**: 字串全比對難以捕捉語義相同的異議。
  → **建議**: 比對 `Objection.target` + `Objection.category` 的組合而非字串。

---

### [Section 4] Staged Pipeline (階段整合流程)
**Verdict:** RETHINK

**Issues:**
- [severity: critical] **模塊拆分無核可點**: Phase 2 直接銜接 Phase 3 風險極大。若拆分架構錯誤，後續精煉全是白費。
  → **建議**: Phase 2 產出模塊清單後，必須加入強制的使用者核可點 (User Checkpoint)。
- [severity: major] **依賴順序管理**: 模塊精煉應具備依賴性意識（DAG）。
  → **建議**: 實作 DAG 調度，確保底層模塊先精煉完成，並傳遞結論給依賴者。

---

### [Section 5] Practical Concerns & Missing Pieces
- **失敗退出機制**: 若達 `max_rounds` 未收斂，應產出「剩餘風險清單」。
- **節點失效處理**: 實作「最小存活審閱者」機制，確保部分節點故障時流程不中斷。
- **預算控管**: 增加 `max_budget_usd` 參數。

---

### Overall Assessment

**Ready for implementation:** **YES_WITH_CHANGES**

**Top 3 Required Changes:**
1. **強化主模型立場與仲裁機制**：優化 Prompt 防止盲從，並建立重複拒絕關鍵異議時的升級流程。
2. **階段性管線 Checkpoint**：在架構拆分後強制使用者審核。
3. **上下文摘要管理**：針對多輪迭代進行結構化狀態追蹤，減少 Token 消耗。

**Top 3 Strengths:**
1. **主從式架構設計**：產出物具備極高的一致性與深度，適合技術架構。
2. **結構化異議模型**：有利於 UI 監控與指標分析。
3. **靈活的 Guided 模式**：有效平衡 AI 自動化與專家決策。
