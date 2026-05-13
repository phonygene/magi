# MAGI `single-source-review-prompt.md` 審批與修正建議

**審閱日期:** 2026-03-30  
**審閱對象:** `C:\Projects\magi\.omc\plans\single-source-review-prompt.md`  
**版本:** v3

## 審批結論

**結論: 需重大修改**

這一版比前一版明顯進步，已補上：
- CLI flags 實測資訊
- provider-specific quirks
- 雙層 effort 設計

但作為「交給其他模型審閱提案」的 prompt，核心前提仍有幾個地方不夠準確，會直接影響外部 reviewer 的判斷品質：
- 文獻依據與 repo benchmark 仍然混用
- 仍把相容性描述成「只靠 `query()` 就能零修改」
- 尚未把 session / hooks / extensions / auto-memory 隔離視為一級風險
- memory 設計仍把 reviewer 引向高風險方向

在這些前提修正前，不建議把這份 prompt 當作最終版對外使用。

## 評分

**整體評分: 6.5 / 10**

## 優點

- 已補充具體 CLI 版本、flags、模型清單，讓 reviewer 能針對真實介面而非抽象概念給意見。
- 新增「已知行為怪癖」很有價值，能讓 reviewer 直接討論穩定性與工程風險。
- 雙層 effort 的拆分比前一版清楚，至少讓問題被明確化了。
- 問題面向已涵蓋 subprocess、effort、memory、observability、替代方案，結構完整。
- 把 Gemini 的穩定性拉出來單獨問，是合理且必要的風險聚焦。

## 問題與風險

### HIGH 1. 文獻敘事仍然錯位

目前背景仍寫成：

- `已應用 NeurIPS 2025 "Debate or Vote" 論文優化：ICE 錯誤偵測框架（88% vs 76%）`

這是不精確的。`88% vs 76%` 是 repo 內部 benchmark 結果，不是 `Debate or Vote` 論文本身的結論。若外部 reviewer 接受了這個前提，後續對 proposal 的評論會建立在錯的研究基礎上。

**建議修正**
- 改寫成兩段獨立敘事：
  - `Debate or Vote`：指出 majority voting 是強 baseline，debate 本身不保證穩定提升
  - MAGI repo benchmark：在你們自己的小型 benchmark 上，ICE-style critique 表現優於單一模型

### HIGH 2. 仍把 protocol 相容性過度簡化成 `query()` interface

prompt 目前仍寫：

- `所有現有 protocol 只呼叫 node.query() → 零修改`

這會誤導 reviewer。依目前程式現況，系統不只依賴 `query()`，還依賴：
- `name`
- `model`
- `persona`
- `last_cost_usd`

而且這些依賴不只存在於 protocol，也存在於：
- engine
- web server
- dashboard event payload
- cost aggregation

**建議修正**
- 在背景中明講：`protocol mostly depends on query(), but the wider system also relies on node metadata`
- 在審閱問題中新增一題，直接要求 reviewer 檢查 hidden node contract

### HIGH 3. session / hooks / extensions / auto-memory 隔離仍未被提升到核心問題

目前 prompt 對 `codex exec` 有問 sandbox，但沒有系統性要求 reviewer 審查：
- `claude` 的 session persistence / project context
- `gemini` 的 MCP / extensions / session 行為
- 各 CLI 是否會自動讀取工作目錄上下文
- 各 CLI 是否會共享本地歷史、設定、hooks

這是 CLI 模式最大的不確定性之一。若三個 node 會共享 session 或 workspace-level augmentation，就不是真正的獨立辯證。

**建議修正**
- 新增明確問題：
  - `How should MAGI isolate session state, hooks, extensions, MCP, and workspace augmentation across CLI-backed nodes?`
- 把這題放到 architecture / subprocess 區塊的前段，而不是留在旁支風險

### MEDIUM 4. prompt 自己對實作模型的描述不一致

前面的表格用的是：
- `claude -p "prompt"`
- `gemini -p "prompt"`

後面的 pseudocode 卻統一成：

```python
proc.communicate(input=prompt.encode("utf-8"))
```

這會讓 reviewer 不清楚你實際準備採用：
- argument-based prompt
- stdin-based prompt
- 或 provider-specific strategy

**建議修正**
- 把 pseudocode 改成 provider-specific adapter
- 不要再用單一 `_run_cli(prompt)` 假裝三家完全等價

### MEDIUM 5. 缺少顯式問題去逼 reviewer 找出 hidden deps 與實際變更面

目前問題設計雖然廣，但還不夠「尖」。如果不直接問，外部 reviewer 很可能只評論抽象設計是否優雅，而不會主動發現：
- `engine.py`
- `web/server.py`
- analytics / dashboard payload
- cost handling

這類實際 integration surface。

**建議修正**
- 在「架構合理性」新增一題：
  - `Besides query(), what node attributes are implicitly required by the current codebase, and which files must change to support CLI backends cleanly?`

### MEDIUM 6. memory 設計仍然容易把 reviewer 帶往高風險方案

prompt 目前預設的 memory 說法仍是：
- 每次 query 後 append Q&A
- 下次 query 注入先前對話

這會使 reviewer 預設接受「raw transcript 回灌 prompt」作法，但這恰好是最容易造成：
- prompt 膨脹
- 重複上下文
- 自我污染
- 與現有 critique round state 重疊

**建議修正**
- 把問題改為：
  - `Should Phase 1 stay fully stateless and defer cross-turn memory?`
  - `If memory is needed later, should it be summary-based JSONL rather than raw transcript injection?`

### LOW 7. 實測資訊還缺少環境邊界註記

目前 `gemini` 慢啟動、`claude` stdin warning、`codex` 乾淨等描述有價值，但缺少背景條件，例如：
- 是否首次啟動
- 是否已登入
- 是否啟用 MCP/extensions
- 是否在特定 workspace 中

缺少邊界條件時，reviewer 可能把單機觀察誤當作產品級事實。

**建議修正**
- 在「已知行為怪癖」前補一句：
  - `Observed on one local machine under the current project/workspace configuration; may vary with auth state, plugins, MCP config, and first-run conditions.`

### LOW 8. 用詞小問題會影響專業感

- `NeurIPS 2025 DOWN 論文` 看起來像 typo
- `API Keys = 0` 也不夠精確，因為 CLI 仍然需要登入或訂閱，不是完全零憑證依賴

**建議修正**
- `DOWN` 改回正確名稱
- 把表格中的 `API Keys` 改成：
  - `Provider API Keys`
  - 或 `Direct API keys`

## 建議修改

### 一、先修正背景段落

建議把這段：

- `已應用 NeurIPS 2025 "Debate or Vote" 論文優化：ICE 錯誤偵測框架（88% vs 76%）...`

改成：

- `現有系統參考 NeurIPS 2025 "Debate or Vote" 對 voting/debate 的分析，將 majority vote 視為強 baseline。另在 MAGI repo 自有 benchmark 中，ICE-style critique 在小型測試集上優於單一模型與 vote-only。`

### 二、在審閱請求中新增 3 個高價值問題

建議新增：

1. `現有程式除 query() 外，還隱性依賴哪些 node attributes？這些依賴會影響哪些檔案與入口？`
2. `如何隔離各 CLI backend 的 session state、hooks、extensions、MCP、workspace augmentation，確保三個 node 真正獨立？`
3. `Phase 1 是否應保持完全 stateless，將 cross-session memory 延後？`

### 三、把 pseudocode 改成 provider-specific adapter

建議不要再用：

```python
class CliNode:
    async def query(self, prompt: str) -> str:
        ...
        proc.communicate(input=prompt.encode("utf-8"))
```

建議改成：

```python
class CliAdapter(Protocol):
    def build_command(self, prompt: str) -> list[str]: ...
    def parse_output(self, stdout: bytes, stderr: bytes, code: int) -> str: ...

class ClaudeAdapter: ...
class CodexAdapter: ...
class GeminiAdapter: ...
```

這樣 reviewer 才會自然去評估 provider 差異，而不是被 prompt 誘導成「三家其實都一樣」。

### 四、重新設計 memory 問題

把目前偏向實作細節的問題：
- markdown vs json vs jsonl

前移到次要位置，先問：
- Phase 1 要不要完全不要 memory
- intra-decision 狀態與 cross-session memory 是否應分離
- 若要 memory，是否只能存 summary 而非 raw transcript

### 五、把成本欄位問題問得更具體

目前只問：
- `cost_usd 欄位設為 0？估算值？`

建議改成：
- `Should CLI mode set cost_usd to null and introduce a separate cost_mode field (measured / estimated / unavailable)?`

這會比單純問 `0 還是估算` 更容易得到可落地的 schema 建議。

## 建議的整體方向

若這份 prompt 的目的是拿去請其他模型做高品質批判審閱，最佳做法不是再堆更多背景資訊，而是：
- 修正錯誤前提
- 暴露 hidden constraints
- 把 reviewer 的注意力導向真正會踩雷的 integration 與 isolation 問題

目前最大價值缺口不是「資訊不足」，而是「資訊排序與 framing 仍然偏掉」。

## 最終審批

**審批結果: 不通過現版本，需重大修改後再送審**

## 通過條件

- 修正 `Debate or Vote` 與 repo benchmark 的敘事分離
- 刪除或改寫 `query() → 零修改` 的過度簡化說法
- 顯式加入 hidden node contract 問題
- 顯式加入 session / hooks / extensions / MCP 隔離問題
- 把 memory 問題改成「是否應延後與 summary-based」而非預設接受 transcript injection

達成以上條件後，這份 review prompt 才適合拿去做第二輪跨模型審閱。
