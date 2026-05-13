# REFINE Mode Proposal V3r2 — 第三輪審批報告（Claude Round 2）

**審批日期**: 2026-04-01  
**審批結論**: ✅ **ACCEPT-WITH-RESERVATIONS**（可進入實作，以下 minor findings 於實作時處理）  
**來源提案**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md`（V3r2 版本）  
**前次審批**:
- `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3-review.md`（Round 1，Claude）
- `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3-review-round1-Codex.md`（Round 1，Codex）

---

## 審批結論

**V3r2 全面且精準地解決了 Codex Round 1 的 6 項關鍵 findings。** 所有 critical 和 major 級別的 findings 都已充分閉合，剩餘 4 個 minor 實作注意事項不阻塞交付。

**審批建議：可進入實作階段。**

---

## 統計摘要

| 嚴重度 | 數量 |
|---|---|
| Critical | 0 |
| Major | 0 |
| Minor | 4（n1, n2, n4 延續 + n5 新增）|
| 缺失項 | 1（n6 語義矛盾，非阻塞）|

---

## Codex Round 1 Findings 解決狀態

| Codex Finding | 嚴重度 | 狀態 | 評語 |
|---|---|---|---|
| #1: IssueState 缺 severity 欄位 | Critical | ✅ **已解決** | V3r2 新增 `severity` / `category` / `latest_description` 三欄位，`upsert()` 加入對應參數，保守策略（歷史最高值）清楚文件化 |
| #2: issue_key 跨輪穩定性 | Critical | ✅ **已解決**（附 minor caveat） | 新增 `reconcile_cross_round()` 函數，以 (category, target, description) 三元組加權相似度 > 0.80 比對；Primary/Reviewer prompt 加入 `SECTION_ID` 穩定性指引。有一個隱含 category 硬約束需注意（見 n5） |
| #3: Decision.confidence 和 votes 未定義 | Critical | ✅ **已解決** | `compute_refine_confidence()` 公式明確（open critical/major 扣分 + max_rounds + degraded 狀態降分）；`compute_refine_votes()` 映射保持 UI 相容（primary → ruling，reviewer 0 open → ruling，有 open → dissent 摘要 != ruling） |
| #4: resolve() 狀態機不完整 | Major | ✅ **已解決** | 完整 6 狀態轉換表（open/resolved/partial_resolved/escalated/wontfix/reopened）；sustain/override/compromise 三種仲裁結果均有明確定義；reopen 路徑清楚文件化 |
| #5: round trace 缺 proposal 原文 | Major | ✅ **已解決** | `RefineRound.proposal_text` 欄位新增；兩層 trace 策略明確文件化（round JSONL = 完整 audit，daily JSONL = 輕量索引）；ADR 已標記 trace 大小需監控 |
| #6: best_round note 塞入 minority_report | Major | ✅ **已解決** | `best_round_note` 移至 `refine_summary`；`compute_refine_minority_report()` 只包含 reviewer 實際未解決異議，不含系統備註；UI 語義正確 |

---

## 前次 Minor Findings 狀態（V3r1 review n1-n4）

| Finding | 狀態 | 評語 |
|---|---|---|
| n1: canonicalize_key 可能產生空 key | 🔲 **未處理** | `re.sub(r'[^a-z0-9_:]', '', key)` 仍存在。建議 fallback: 正規化後 len < 3 → `"unknown_issue_{seq}"` |
| n2: _resolve_cost_mode() 需 unit test | 🔲 **提及未測試** | §11 P0 第 8 項有列出抽取步驟，但 test matrix 無對應測試案例 |
| n3: best_round 僅提示不自動回退 | ✅ **設計確認** | `best_round_note` 在 `refine_summary`，符合設計意圖，接受 |
| n4: on_round_event callback 非 async | 🔲 **未處理** | 維持 `Callable[[str, dict], None]`，P1 GUIDED 整合 WebSocket 時可能需要調整 |

---

## V3r2 新發現

### n5. reconcile_cross_round() 隱含 category 硬約束未文件化

**嚴重度**: minor  
**類別**: 實作細節 / 文件品質  
**影響範圍**: 跨輪 issue deduplication 邏輯

**問題描述**

`reconcile_cross_round()` 的加權設計為：

```
score = (category_match: 0.3 or 0)
      + (target_similarity * 0.3)
      + (description_similarity * 0.4)
threshold: > 0.80
```

由於 target 和 description 的最高 similarity 各為 0.3 和 0.4（總和最多 0.70），當 category 不匹配時，最高可能得分為 0.70，永遠無法達到 0.80 threshold。

**實際含義**：category 在實效上成為一個 **硬性匹配條件**，而非權重之一。

**真實場景**

若 reviewer 在第 2 輪將某問題從 `"risk"` 重新分類為 `"error"`（同一實質問題），`reconcile_cross_round()` 將無法識別為同一 issue，導致建立重複的 `issue_key`。

**建議**

在函數 docstring 中顯式說明「**category 必須完全匹配才能觸發 reconciliation**」，或改為：
- 將 threshold 調整為 0.65（讓 category 不匹配時仍可靠 target + description 相似度通過）
- 明確文件化此設計決策與理由

---

### n6. IssueState.severity 降級語義矛盾

**嚴重度**: minor  
**類別**: 設計一致性  
**影響範圍**: partial verdict 與 severity 更新邏輯

**問題描述**

`IssueState.severity` 採「保守策略：取歷史最高值」，文件明說「一旦被標為 critical，除非 resolved，否則不會降級」。

但 `Reflection.verdict = "partial"` 時，primary 可提供 `severity_after` 欄位，且 `resolve()` docstring 說 "partial_resolved (severity 可降級, issue 持續追蹤)"。

這兩個說法產生矛盾：
- **路徑 A**: `upsert()` 保守策略永不降級，partial 的 `severity_after` 被忽略
- **路徑 B**: partial 允許透過 `severity_after` 降級，`upsert()` 只在初始 upsert 時取最高

**建議**

明確說明：
1. partial verdict 是否**允許** severity 降級
2. 若允許，由哪個函數（`resolve()` 還是 `upsert()`）負責更新 `IssueState.severity`
3. 在 `Reflection` 和 `IssueState` 的交互說明中補充此邏輯

---

## 整體評價

### V3r2 的質量提升

V3r2 全面且精準地解決了 Codex Round 1 的 6 項關鍵 findings：

1. **IssueState schema 完整化**  
   新增 `severity` / `category` / `latest_description`，提供收斂與最佳輪評分演算法的可靠基礎

2. **跨輪 issue_key 穩定性**  
   `reconcile_cross_round()` 提供語義匹配機制，配合 prompt 中的 `SECTION_ID` 指引，從兩個層面降低 key 漂移

3. **Decision 相容性**  
   `confidence` 和 `votes` 定義明確，UI 映射清晰，可直接轉為實作

4. **完整狀態機**  
   6 狀態 + 全轉換路徑，無歧義地覆蓋 partial / reopen / escalation 情境

5. **Round trace 可審計性**  
   `proposal_text` 保存完整 proposal，兩層策略平衡 audit 需求與儲存成本

6. **minority_report 語義正確**  
   不再混入系統備註，UI 渲染行為符合預期

### 剩餘工作項

剩餘 4 個 minor findings（n1, n2, n4 延續 + n5 新增）均不阻塞實作，可在實作時作為注意事項處理：

- **n1**: `canonicalize_key()` 空值 fallback
- **n2**: `_resolve_cost_mode()` 單元測試補齊
- **n4**: `on_round_event` async 升級（P1 考慮）
- **n5**: `reconcile_cross_round()` category 約束文件化
- **n6**: partial verdict 的 severity 降級語義澄清

---

## 建議的實作優先次序

| 優先級 | 項目 | 理由 |
|---|---|---|
| P0 | n6 語義澄清 | 影響 `resolve()` 邏輯正確性 |
| P1 | n5 文件化 | 防止跨輪 deduplication 隱患 |
| P2 | n1, n2, n4 | 實作細節，技術可行 |

---

## 元資訊

**審批者**: Claude（Writer Agent）  
**審批深度**: 架構級審批，對標 Codex Round 1 findings  
**驗證範圍**: 所有 6 項 critical/major findings 解決狀態、前次 4 項 minor findings 現況、新發現 2 項 minor findings  
**關鍵文件**:
- 提案本體：`C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md`
- 前次審批：`C:\Projects\magi\.omc\plans\refine-mode-proposal-v3-review.md`（V3r1）
- Codex Round 1：`C:\Projects\magi\.omc\plans\refine-mode-proposal-v3-review-round1-Codex.md`

**審批結論**: ✅ **ACCEPT-WITH-RESERVATIONS** — 可進入實作階段，上述 minor findings 作為實作時的注意事項處理。
