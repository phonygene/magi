# REFINE Mode Proposal V2 — Round 2 審核報告（Codex）

**審核日期**: 2026-04-01  
**審核對象**: `C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md`  
**審核方式**: 文件審核 + 對照現有 MAGI 程式碼  
**結論**: `REVISE`

---

## 核心結論

V2 確實比 V1 完整很多，尤其是 JSON 輸出、IssueTracker、Phase 2 checkpoint、測試矩陣，這些方向都對。但這份提案仍然有幾個會直接影響實作正確性或落地可行性的結構性問題，還不能視為可直接開工。

以下只列仍然需要修正的高優先問題，不重複 V2 已經修正的部分。

---

## Findings

### 1. [critical] `issue_key = hash(target + category)` 會讓收斂與仲裁機制失真

**問題**

V2 把 `issue_key` 定義成 `hash(target + category)`，並讓整個 `IssueTracker`、收斂判定、升級仲裁都依賴這個 key。這個設計同時有「碰撞」與「不穩定」兩個問題：

- 同一 section / 同一 category 下可以有多個完全不同的問題，卻會被合併成同一個 `issue_key`
- `target` 又允許是「section ID 或引用文字」，一旦 proposal 文字改寫、diff 調整、section 重排，同一個問題可能產生新的 key

這表示：

- 不同問題可能被錯誤當成同一議題
- 同一問題可能跨輪被錯誤當成新議題
- `rejected_count`、`resolution`、`escalation` 會被污染

**證據**

- 提案定義 `issue_key`: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L89)
- `IssueTracker` 完全依賴 `issue_key`: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L117)
- reviewer prompt 的 `target` 可以只是引用文字: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L274)
- 收斂與升級邏輯依賴 tracker 狀態: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L363), [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L374)

**建議**

- 不要把 `issue_key` 定義成單純 `hash(target + category)`
- 改成「系統端 canonicalization 後指派 stable key」
- 至少納入：`normalized_target`, `category`, `normalized_issue_summary`
- reviewer 可先回傳 `candidate_key` 或純 issue 內容，真正的 `issue_key` 由協議層去做 canonical merge
- 若同輪多 reviewer 提到疑似同一問題，需要一個 merge step，而不是直接依賴 hash

---

### 2. [critical] 主模型反省解析失敗被視為「全部 reject」會把格式錯誤誤判成語義拒絕

**問題**

V2 規定主模型反省 JSON 若 repair 後仍失敗，就「視為全部 reject」。這會讓純解析問題直接污染議題狀態機：

- `rejected_count` 被錯誤增加
- 同一議題可能因格式錯誤而被升級仲裁
- reviewer 會以為 primary 明確拒絕，但其實 primary 可能只是輸出格式壞掉

這不是單純的 fallback 問題，而是把「transport / parsing failure」錯誤地映射成「semantic decision」。

**證據**

- 反省失敗 fallback: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L600)
- 升級條件依賴 `rejected_count >= 2`: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L374)

**建議**

- parse failure 應標記為 `reflection_parse_error`，不能直接轉成 `reject`
- 合理策略應是：
  1. repair 一次
  2. 再失敗則重試主模型一次
  3. 仍失敗則本輪標記 invalid / degraded / abort
- 只有在成功解析出 `verdict=reject` 時，才增加 `rejected_count`

---

### 3. [critical] trace / logger 相容性仍未真正解決，V2 低估了儲存層改動

**問題**

V2 宣稱：

- `Decision` 只加 optional 欄位即可
- `StagedResult.as_decision()` 可供現有 logger/CLI 使用
- 完整輪次資料寫到 `{trace_id}_rounds.jsonl`
- 現有消費者不受影響

但目前實作不是這樣：

- `Decision.to_jsonl()` 直接 `json.dumps(asdict(self))`
- `TraceLogger.log()` 只會寫每日單一 `YYYY-MM-DD.jsonl`
- `MAGI.ask()` 才會呼叫 `_logger.log(decision)`
- 現有程式根本沒有 `trace_id` 對應的獨立 round log API
- `staged()` 作為獨立 API，也沒有既有 logging path

所以 V2 不是「append-only 小改」，而是需要明確重寫 trace contract。

**證據**

- 提案的 `Decision` 擴展: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L152)
- `StagedResult.as_decision()`: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L182)
- 新 trace 設計: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L582)
- 現有 `Decision.to_jsonl()`: [decision.py](C:\Projects\magi\magi\core\decision.py#L21)
- 現有 `TraceLogger.log()` 只寫日檔: [logger.py](C:\Projects\magi\magi\trace\logger.py#L12)
- 現有 `ask()` 的 logging 入口: [engine.py](C:\Projects\magi\magi\core\engine.py#L146)

**建議**

- 先把 trace contract 明文化，再寫 REFINE
- 建議新增明確 API：
  - `log_decision(decision)`
  - `log_round(trace_id, round_record)`
  - `resume_trace(trace_id)`
- `staged()` 若保留獨立入口，就必須明確定義它如何記錄 summary 與 phase artifacts

---

### 4. [major] 主模型失效時「提升最高信心審閱者」在現有架構下沒有可計算依據

**問題**

提案說 primary 掛掉時，提升「最高信心審閱者」為新 primary。但現有 MAGI 並沒有 reviewer-level confidence：

- `Decision.confidence` 是整體決策信心，不是單一 reviewer 的分數
- `judge.py` 只會估整體答案間 agreement，不會輸出哪一位 reviewer 比較可信
- `cli-multi` 還是異質節點（Claude / Codex / Gemini），不同 adapter 的結構化輸出穩定度也不同

因此「最高信心審閱者」目前只是口號，不是可實作規則。

**證據**

- failover 提案: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L421)
- 異質節點組成: [engine.py](C:\Projects\magi\magi\core\engine.py#L63)
- 現有 judge 只估 agreement: [judge.py](C:\Projects\magi\magi\protocols\judge.py#L247)
- `Decision.confidence` 是整體欄位: [decision.py](C:\Projects\magi\magi\core\decision.py#L9)

**建議**

- 刪掉「最高信心」這個說法，改成可落地規則
- 例如：
  1. 同質節點模式才允許 promotion
  2. 異質模式下 primary 掛掉就直接 `degraded_abort`
  3. 或預先配置 `promotion_order`
- 若真的要做 confidence-based promotion，必須先定義 reviewer scoring protocol

---

### 5. [major] GUIDED 模式的預設超時自動核可，與「使用者每輪介入」的設計目標衝突

**問題**

提案前面把 GUIDED 定義成使用者每輪介入的重要子模式，但子協議又規定預設等待 300 秒後自動以「核可」續行。這會讓：

- 使用者只是暫離，就被默默推進到下一輪
- 架構審核或模組拆分 checkpoint 失去人類把關意義
- 成本與方向可能在使用者未同意下繼續消耗

如果 GUIDED 真的是產品賣點，預設行為不應是 auto-approve。

**證據**

- GUIDED 核心定位: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L19), [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L34)
- 子協議 timeout 預設 auto approve: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L467)

**建議**

- 預設改成 `pause`
- `auto_approve` 只能是明確 opt-in
- UI 上要顯示目前 timeout policy
- 若要保留自動續行，至少限定在非架構決策類的低風險 round

---

### 6. [major] 「所有審閱者掉線即直接收斂」會把無審閱結果誤標成已收斂

**問題**

V2 規定當所有 reviewers 掉線，就以最後一版 proposal 直接收斂並標記 degraded。這在系統上雖然能結束流程，但在語義上不是「收斂」，而是「審閱中止」。

若沿用 `refine_converged` / `decision` 這類成功語意，前端、trace、使用者都會誤以為這是完整 REFINE 結果。

**證據**

- reviewer failure 策略: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L435)
- 錯誤處理表格: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L602)

**建議**

- 所有 reviewers 掉線時，終態應是 `incomplete` 或 `aborted_degraded`
- 不應送出一般 `refine_converged`
- 應要求：
  - GUIDED 模式下由使用者明確接受
  - 自動模式下以 `degraded_decision` 單獨事件與狀態表示

---

### 7. [major] WebSocket 重連 / resume 設計仍然缺少 session owner 與執行中狀態容器

**問題**

V2 新增了 `trace_id` 持久化、`refine_resume`、GUIDED 子協議，看起來已處理狀態機問題，但目前 server 架構仍是單一 handler 內部流程：

- 先收第一個 request
- 一路跑到 `decision`
- 最後才進入 retry loop

現況沒有：

- session registry
- in-flight task ownership
- 根據 `trace_id` 重新 attach 的入口
- resume 後如何重新取得 pending prompt / pending round 的資料模型

所以目前提案其實還是「列出事件名稱」，不是完整可實作的 resume 設計。

**證據**

- resume 方案: [refine-mode-proposal-v2.md](C:\Projects\magi\.omc\plans\refine-mode-proposal-v2.md#L473)
- 現有 WebSocket handler 是單次流程: [server.py](C:\Projects\magi\magi\web\server.py#L122)
- handler 只在 final decision 後等待 retry: [server.py](C:\Projects\magi\magi\web\server.py#L408)

**建議**

- 若要支援 resume，先定義 `RefineSession` 物件
- 內容至少要有：
  - `trace_id`
  - current round / step
  - pending user action
  - active task handles
  - latest artifacts
- 沒有 session layer 前，不應在提案裡承諾 `trace_id` 重連續行

---

## Strengths

- V2 已經把 V1 最脆弱的純文字 parser 風險，改成 JSON + repair fallback，方向正確。
- `IssueTracker`、`change_summary`、`conflict_check` 讓 REFINE 比 ICE 更接近真正的 design review workflow。
- `Phase 2` 強制 checkpoint 和 `ModuleSpec` 補完整後，staged pipeline 已經比 V1 可討論得多。
- 有補上測試矩陣，這對後續實作很重要。

---

## 建議的升級條件

這份提案要升級成可實作版本，至少需要先補齊下面四件事：

1. 重寫 `issue_key` 規則，不要用 `hash(target + category)` 作為核心識別。
2. 把 parse failure 與 semantic reject 徹底分離。
3. 明確定義 trace / round-log / staged-log 的儲存契約，而不是只在提案中宣稱「相容」。
4. 把 primary failover 與 GUIDED resume 改成真正可執行的狀態機規則，而不是描述性口號。

---

## 最終判定

`V2 = 明顯進步，但仍未達到可直接開工`

如果只問「比 V1 好很多嗎？」答案是 `是`。  
如果問「現在能不能直接實作？」答案仍然是 `不能，還需要再修一版`。
