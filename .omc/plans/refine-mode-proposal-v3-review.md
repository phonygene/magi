# REFINE Mode Proposal V3r1 — 第二輪審批報告

**審批日期**: 2026-04-01
**審批結論**: ✅ **ACCEPT-WITH-RESERVATIONS**（可進入實作，4 個 minor 實作時處理）
**來源提案**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v3.md`
**前次審批**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v2-review.md`

---

## 統計摘要

| 嚴重度 | 數量 |
|--------|------|
| Critical | 0 |
| Major | 0 |
| Minor | 4 |
| 缺失項 | 2（非阻塞）|

---

## V2 Findings 解決狀態

| V2 Finding | 狀態 | 評語 |
|------------|------|------|
| C1: ask() mode 無法傳遞 config | ✅ 已解決 | D1: engine.refine() 獨立方法 + ask(mode="refine") thin dispatch |
| C2: promote reviewer 不相容 | ✅ 已解決 | D2: 移除 promote，retry → abort degraded |
| C3: Decision nested dataclass 序列化 | ✅ 已解決 | refine_summary: dict + refine_trace_id: str，全 primitive |
| M1: staged vs ask 關係不清 | ✅ 已解決 | D6: Staged 延後 P2 |
| M2: 收斂條件不精確 | ✅ 已解決 | §6 五個精確收斂條件 + escalation 加入 distinct_reviewers >= 2 |
| M3: cost model 低估 | ✅ 已解決 | §9: 17 calls, $0.51-$3.00 |
| M4: GUIDED 300s timeout | ✅ 已解決 | GUIDED 延後 P1 |
| M5: on_user_review 未定義 | ✅ 已解決 | P1 僅定義 Protocol type |
| M6: 14 WS events 太多 | ✅ 已解決 | 縮減為 4 callback events |
| m1-m5, Missing 1-6 | ✅ 全部已解決或合理 defer | Review Response Matrix 逐條回應 |

**結論：V2 的 3 Critical + 6 Major + 5 Minor + 6 Missing 全部充分解決。**

---

## V3r1 新發現

### n1. `canonicalize_key` 移除非 ASCII 字元可能產生空 key

- **嚴重度**: minor
- **類別**: 邊界條件
- **問題**: `re.sub(r'[^a-z0-9_:]', '', key)` 移除所有非 ASCII 字元。若異質模型（Gemini）在 candidate_key 中混入 unicode，key 可能被清空。
- **風險**: 低（Prompt 已要求 lowercase + underscore 格式）
- **建議**: `canonicalize_key` 加入 fallback：正規化後長度 < 3 → 回傳 `"unknown_issue_{seq}"`。

### n2. `_resolve_cost_mode()` 抽取後需確保 ask/refine 行為一致

- **嚴重度**: minor
- **類別**: 實作細節
- **問題**: `engine.py:134-144` inline 邏輯抽取為方法時，需確保 `ask()` 和 `refine()` 呼叫後行為一致。
- **風險**: 極低，技術上可行
- **建議**: 實作時加入 unit test 驗證兩條路徑的 cost_mode 一致。

### n3. `best_round` 回退僅為 minority_report 提示，未自動回退

- **嚴重度**: minor
- **類別**: 設計品質
- **問題**: 最終輪非最佳輪時僅附文字提示，不自動回退。
- **風險**: 使用者可能期望自動回退
- **建議**: 可接受。後續迭代可加 `auto_rollback: bool = False` config。

### n4. `on_round_event` callback 非 async

- **嚴重度**: minor
- **類別**: API 設計
- **問題**: 同步 callback，P1 GUIDED 整合 WebSocket 時可能需 async。
- **風險**: 低，P0 階段不受影響
- **建議**: 考慮改為 `Callable[[str, dict], None | Awaitable[None]]`，或 P1 時再調整。

---

## 缺失項（非阻塞）

1. **Reviewer 掛掉後其 open issues 的處理**: 未說明已掛 reviewer 的 open issues 是否降權。影響低，可在實作時決定。
2. **nodes: list 無型別標注**: 既有技術債（vote/critique 也是如此），非 V3 新引入。

---

## 整體評價

V3r1 相比 V2 有質的提升：

1. **Decision Drivers + Options Table** — 每個關鍵決策有明確的替代方案比較和選擇理由
2. **完整 Python dataclass 定義** — 所有欄位型別明確，可直接轉為實作
3. **成本模型修正** — 數字合理，已識別 `sum(n.last_cost_usd)` 覆寫問題
4. **Review Response Matrix** — 逐條回應三份報告的每個 finding
5. **範圍收斂** — 從「Core + GUIDED + Staged + WS」→「Core only」，大幅降低交付風險
6. **V3r1 追加修正** — 成本聚合 bug、序列化問題、thin dispatch 等 Architect 發現均已處理

**可進入實作。4 個 minor findings 作為實作時的注意事項即可。**
