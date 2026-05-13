# MAGI CLI-Native Mode 審批與修正建議

**審閱日期:** 2026-03-30  
**審閱對象:**
- `C:\Projects\magi\.omc\plans\single-source-mode.md`
- `C:\Projects\magi\.omc\plans\single-source-review-prompt.md`

## 審批結論

**結論: 需重大修改後再實作**

這份提案方向可行，但目前存在數個會直接影響實作品質與安全性的問題：
- 抽象層設計低估既有系統對 node 物件的隱性依賴
- CLI backend 的安全與 session 隔離尚未定義清楚
- 文獻依據、repo benchmark 與產品敘事混用
- 記憶體與上下文注入策略容易造成 prompt 膨脹與自我污染

在現狀下，不建議按原計畫直接進入實作。

## 評分

**整體評分: 5.5 / 10**

## 優點

- 問題定義清楚，明確鎖定「沒有 API Key 時仍可使用 MAGI」這個真實需求。
- `cli_multi` 與 `cli_single` 的產品切分方向合理，能對應不同訂閱與成本情境。
- 嘗試維持現有 protocol 不變，方向上是對的，代表提案有意識地降低侵入性。
- 有明確 Phase 分段與測試章節，代表提案者具備落地意圖，不只是概念草案。
- 有注意到 timeout、auth 過期、CLI 不存在等典型操作風險。

## 問題與風險

### HIGH 1. `query()` 並不是目前唯一的 node contract

提案宣稱所有 protocol 只依賴 `query()`，因此可零修改接入 CLI node。這與現況不符。

目前程式除 `query()` 外，還依賴下列欄位：
- `node.name`
- `node.model`
- `node.persona`
- `node.last_cost_usd`

影響範圍不只 `magi/core/engine.py`，還包含：
- `magi/web/server.py`
- `magi/cli.py`
- `magi/commands/analytics.py`
- dashboard 前端顯示邏輯

**建議修正**
- 先引入正式 `NodeProtocol` 或 `ABC`
- 明確定義最小必要欄位：
  - `name: str`
  - `backend_label: str`
  - `persona: Persona`
  - `last_cost_usd: float | None`
  - `async query(prompt: str) -> str`
- 不要再用「protocol 不變 = 只看 query()」這種敘述

### HIGH 2. CLI backend 不能當成單純的 stdin/stdout wrapper

提案把三個 CLI 都建模成相同模式：

```python
create_subprocess_exec(...)
proc.communicate(input=prompt)
stdout -> answer
```

這在概念上太粗。`claude`、`codex exec`、`gemini` 雖然都可非互動執行，但在下列面向並不等價：
- session persistence
- JSON / stream output
- sandbox / approval model
- 工具使用能力
- model flag
- 錯誤輸出格式

尤其 `codex exec` 與 `claude`/`gemini` 都可能帶有 agentic/tooling 行為。若不先約束，三個 node 會從「回答問題」變成「執行環境操作」，對 MAGI 而言是錯誤預設。

**建議修正**
- 改成 `CliAdapter` 設計，不要只用一個通吃的 `CliNode._run_cli()`
- 每個 provider 明確定義：
  - command builder
  - output parser
  - error parser
  - safety flags
  - session policy
- 預設強制安全模式：
  - 禁用工具或限制工具
  - 使用 ephemeral / no-session-persistence
  - 使用結構化輸出模式

### HIGH 3. 研究依據與 repo benchmark 被混在一起

提案與 review prompt 都把：
- repo 內部 benchmark 的 `88% vs 76%`
- NeurIPS 2025 `Debate or Vote`

混成同一組論據。這是不精確的。

`Debate or Vote` 的主結論是：
- 多數增益來自 majority voting
- debate 本身不會系統性提升 expected correctness
- heterogeneous agents 有少數 task-specific gains，但不是對「多模型辯論一定更好」的普遍背書

而 `88% vs 76%` 是 repo 自己 25 題 benchmark 的結果，不能寫成論文結論。

**建議修正**
- 文案拆開：
  - `paper supports vote as a strong baseline`
  - `repo benchmark suggests ICE-style critique may still be useful in this implementation`
- 所有 `88% vs 76%` 相關表述都改為「內部實驗」或「repo benchmark」
- `single-source-review-prompt.md` 應修正背景描述，避免讓外部 reviewer 建立錯誤前提

### HIGH 4. 工具使用與 sandbox 竟然仍是 open question

提案第 7 節把 CLI tool use 是否允許列為 open question，但這其實是設計前置決策，不是實作後再看。

對 MAGI 而言，三個 node 的職責是「生成獨立觀點」，不是操作 workspace。若允許工具，會出現：
- 某節點偷讀 repo 更多檔案，破壞獨立性
- 某節點執行命令造成副作用
- 不同 CLI 的權限模型不一致，導致結果不可比

**建議修正**
- 在 MVP 明確規定：
  - CLI node 預設為 non-agentic backend
  - 不允許寫檔
  - 不允許 shell / edit 類工具
  - 只允許純 prompt-response 模式
- 真要做 agentic mode，另開新模式，不與 `cli_single` / `cli_multi` 混在第一版

### MEDIUM 5. 記憶注入策略會造成 prompt 膨脹與內容重複

目前 proposal 的 memory 設計是每次 query 後把 Q&A append 到檔案，下次再注入 prompt。這和現有 critique protocol 本身已經會注入：
- 原問題
- 自己前一輪答案
- 其他節點答案

若兩者疊加，很容易造成：
- 同一資訊重複進 prompt
- token / latency 惡化
- 節點被自己先前措辭綁住，降低修正意願

**建議修正**
- Phase 1 先做 stateless backend
- session memory 延後到 Phase 2
- 若真的要做，使用 JSONL + summary/compaction，不要整段 markdown transcript 反覆灌回 prompt

### MEDIUM 6. 檔案變更面低估，且 `.env` 不應列為修改檔

計畫列出的變更面不足。除了 `engine.py` / `cli.py` 外，實際上至少還應評估：
- `magi/web/server.py`
- `README.md`
- `.env.example`
- trace / analytics 對 cost 欄位的呈現

另外 `.env` 應視為使用者本地檔，不應作為正式變更清單的一部分。

**建議修正**
- 變更清單改成 tracked files only
- 把 `.env` 改成 `.env.example`
- 若 web dashboard 暫不支援 CLI backend，就在 Phase 1 明講，不要假設自然相容

### MEDIUM 7. `cost_usd = 0` 不是好設計

CLI 模式不一定能取得真實 token/cost metadata，把 `cost_usd` 固定設為 `0` 會污染 analytics。

**建議修正**
- 改成：
  - `cost_usd: float | None`
  - `cost_mode: "measured" | "estimated" | "unavailable"`
- analytics 顯示時區分：
  - 可量測成本
  - 不可量測成本

### LOW 8. Pseudocode 有實作級問題

提案中的：
- `model_flag=[]`
- `context_files=[]`

是 mutable default argument，不應直接照抄進正式程式。

**建議修正**
- 全部改成 `None`
- 在 `__init__` 內再做初始化

## 建議修改

### 建議的實作順序

#### Phase 1: 只做可控 MVP

只支援：
- `magi ask --backend cc-single`
- `magi ask --backend cli-multi`

先不要動：
- `diff`
- `judge`
- `bench`
- `dashboard`
- session memory
- hybrid mode

#### Phase 2: 抽正式 backend contract

新增：
- `NodeProtocol`
- `ApiNode`
- `CliNodeAdapter`

目標是讓 engine 不再硬綁 `MagiNode`。

#### Phase 3: 安全與輸出格式

每個 CLI backend 都要有：
- 明確 command builder
- 結構化輸出
- 明確 sandbox/session policy
- provider-specific error normalization

#### Phase 4: 擴充其他入口

確認 `ask` 穩定後，再逐步支援：
- `diff`
- `judge`
- `bench`
- web dashboard

### 建議的 CLI 介面

不要直接上 `--source api|cli-multi|cli-single`，建議改為：

```bash
magi ask "question" --backend api
magi ask "question" --backend cc-single
magi ask "question" --backend cli-multi
```

理由：
- `source` 容易和 model source/provider source 混淆
- `cc-single` 比 `cli-single` 更符合實際需求描述

### 建議的 Node 抽象

```python
class NodeProtocol(Protocol):
    name: str
    backend_label: str
    persona: Persona
    last_cost_usd: float | None

    async def query(self, prompt: str) -> str:
        ...
```

這樣 API backend 與 CLI backend 才能在相同 contract 下共存。

### 建議的成本欄位設計

```python
@dataclass
class Decision:
    ...
    cost_usd: float | None = None
    cost_mode: str = "unavailable"
```

## 對 `single-source-review-prompt.md` 的修正建議

目前 review prompt 的背景敘述有偏誤，建議至少補三項問題給外部 reviewer：

1. 目前 MAGI 除了 `query()` 之外，還隱性依賴哪些 node attributes？
2. CLI backend 應否強制禁止工具使用與 session persistence？
3. 若 CLI 模式無法提供 cost metadata，Decision/analytics 應如何建模？

另外建議把這段改掉：

- 原本傾向寫法：
  - `已應用 NeurIPS 2025 論文優化：ICE 錯誤偵測框架（88% vs 76%）`

- 建議改成：
  - `現有 repo 內部 benchmark 顯示，ICE-style critique 在小型基準上優於單一模型；NeurIPS 2025 Debate or Vote 則指出 majority voting 是強基線，debate 本身未必帶來穩定提升。`

## 建議的修正版決策

### 建議採納項目

- 支援 CLI backend 的大方向
- 保留 `cli_multi` 與 `cc-single` 的產品分流
- 保留 provider-specific adapter 思路

### 建議延後項目

- 外部記憶
- hybrid mode
- agentic tool use
- dashboard 全量支援
- token/cost estimation

### 建議刪除或改寫項目

- 「protocol 零修改且只依賴 query()」這種敘述
- 把 `.env` 列入正式變更清單
- 把 repo benchmark 寫成論文結論的表述

## 最終審批

**審批結果: 不通過現版本，需重大修改後再審**

**通過條件**
- 補正式 `NodeProtocol`
- 明確 CLI 安全/權限/session 政策
- 修正文獻與 benchmark 敘事
- 把 MVP 範圍縮小到 `ask` 指令
- 修正成本欄位與 tracked file 清單

達成以上條件後，可進入第二輪審批。
