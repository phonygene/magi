# MAGI `single-source-mode.md` 第二輪審批與修正建議

**審閱日期:** 2026-03-30  
**審閱對象:** `C:\Projects\magi\.omc\plans\single-source-mode.md`  
**版本:** v4

## 審批結論

**結論: 有條件通過，但需先修正數個實作級錯誤與設計缺口**

v4 相比前版已經有實質進步：
- 補上 hidden node contract
- 改成 adapter pattern
- 把 Phase 1 縮成 stateless
- 納入 workspace isolation 與 cost_mode

這些方向是對的，已經從「概念草案」進到「可落地設計」。  
但目前仍有數個會直接讓實作失敗或讓設計偏掉的點，尤其集中在：
- 對外部論文結論的描述仍不精確
- `codex` 的隔離與 prompt 傳遞策略存在明顯錯誤
- `CliOutputCleaner` 替代 structured output 的選擇不夠理想
- cost / analytics / hybrid aggregation 的 schema 尚未收斂

在修正這些點之前，不建議直接按 v4 實作。

## 評分

**整體評分: 7.5 / 10**

## v3→v4 改進評估

### 有效修改

- `NodeBase` 的引入是正確修正，至少不再把整個系統錯誤簡化成只依賴 `query()`
- Adapter pattern 比統一 `_run_cli()` 健全得多，方向正確
- 把 Phase 1 改為 stateless，避免 memory 與 critique round state 重疊，是好決策
- 把 workspace/session isolation 提升為一級風險，這是必要修正
- `cost_mode` 的引入比單純 `cost_usd=0` 更誠實，也更有未來延展性

### 仍不足之處

- 研究依據仍混入了論文未明講的 `ICE error-detection framing 優於 persuasion` 敘述
- `codex` adapter 的 temp-file / isolation 設計存在具體執行錯誤
- `CliOutputCleaner` 雖然補洞，但仍像 workaround，不是最佳主設計
- `cost_mode` 雖然新增，但 downstream analytics / hybrid aggregation 仍未完全設計

## 優點

- 計畫書已經能清楚對齊現有 codebase，而不是停留在抽象設計。
- 風險章節比前版成熟，能分辨 CLI-specific 風險與系統性風險。
- 隔離策略與 limitations 有誠實揭露，不再把 CLI backend 說成完全等價於 API backend。
- 檔案變更清單比前版更接近實際需要。
- 對 `cli_multi` / `cli_single` 的產品切分仍然合理。

## 問題與風險

### HIGH 1. `Debate or Vote` 的描述仍然有過度延伸

v4 雖然把論文與 repo benchmark 分開了，但仍寫：

- `ICE error-detection framing 優於 persuasion-based debate。MAGI 採用了 ICE framing 的核心思路。`

這個說法目前仍不像是該論文的明確結論。從論文摘要與公開介紹可確認的是：
- majority voting 是強 baseline
- debate 本身不保證提升 expected correctness
- targeted interventions 可能改善 debate effectiveness

但不等於該論文直接背書 MAGI 使用的 ICE prompt 形式。

**建議修正**
- 改成更保守的說法：
  - `The paper supports majority voting as a strong baseline and suggests that targeted interventions may improve debate. MAGI's ICE-style framing is an implementation choice inspired by that direction, not a direct reproduction of the paper.`

### HIGH 2. `CodexAdapter` 的 temp file 策略目前是錯的，實作後會壞

計畫書中的：

```python
prompt_arg = f"$(cat {temp_file})"
return ["codex", "exec", prompt_arg, ...]
```

這在 `asyncio.create_subprocess_exec()` 下不會做 shell substitution，而且：
- `$(cat ...)` 是 shell syntax，不是 argument API syntax
- Windows 上也不是穩定可用語法
- 即便真的展開成字串，也沒有解決 argument length limit

更關鍵的是，本地 `codex exec --help` 已明確支援：
- 不給 `PROMPT` 時從 stdin 讀
- 或用 `-` 從 stdin 讀

**建議修正**
- `CodexAdapter` 改成雙模式：
  - 短 prompt: argument
  - 長 prompt: 不給 prompt argument，改走 stdin
- 刪掉 `$(cat ...)` 設計

### HIGH 3. `cwd=tmpdir` 的 isolation 會直接撞上 `codex` 的 git repo 檢查

v4 的隔離策略把所有 CLI 都丟到 temp dir 執行。這對 `codex exec` 是高風險，因為它預設會檢查是否在 git repo 中；本地 help 已顯示需要 `--skip-git-repo-check` 才能在 repo 外執行。

因此目前這套設計如果不額外處理，`CodexAdapter` 很可能在 isolation mode 下直接失敗。

**建議修正**
- `CodexAdapter` command builder 必須明確加入：
  - `--skip-git-repo-check`
  - 必要時 `--sandbox read-only` 或等價保守設定
- 或者重新定義 isolation：
  - 不切到 tempdir，而是保留 repo cwd，但顯式禁用 auto tools / auto context

### HIGH 4. `CliOutputCleaner` 不應成為主方案，應優先使用 structured output

三個 CLI 都已具備結構化輸出能力：
- `claude -p --output-format json`
- `codex exec --json`
- `gemini -o json`

v4 卻仍以 plain text + regex cleaner 為核心，這代表：
- protocol 輸入仍容易被 noise 汙染
- per-provider parsing 會變脆
- 後續 debug/trace 難度升高

Cleaner 應該是 fallback，不該是主設計。

**建議修正**
- 優先策略改為：
  - provider-specific structured output mode
  - cleaner 僅作 fallback 或最後一道 sanitation
- 在 plan 裡明寫：
  - `prefer JSON output when supported; use regex cleaner only for non-structured providers or malformed output`

### MEDIUM 5. `>8KB` 的 prompt threshold 缺乏依據，且混淆了 argument 與 pipe 的限制

v4 寫：
- `>8KB 的 prompt 改用 temp file 避免 Windows pipe 限制`

這個 framing 不準確。Windows 常見限制主要是 command-line / shell 路徑長度，而不是 Python `communicate()` 對 stdin pipe 的 8KB 限制。對 stdin-based provider，8KB temp file fallback 的必要性不足；真正需要處理的是 argument-based provider。

**建議修正**
- 改成：
  - `Only argument-based prompt delivery needs command-length fallback.`
  - `stdin-based providers should prefer direct stdin unless provider docs show concrete limits.`
- 若要保留 threshold，明確標注依據或改成 configurable

### MEDIUM 6. `cost_mode` 還不夠，尤其對 hybrid mode 不足

目前設計是：

```python
cost_usd: float = 0.0
cost_mode: str = "measured"
```

這對純 API 或純 CLI 可勉強成立，但一旦之後支援 hybrid mode，就會遇到：
- 一部分 node 有 measured cost
- 一部分 node unavailable

單一 `Decision.cost_mode` 很難表達「部分可量測」。

**建議修正**
- 至少預留：
  - `partial`
  - 或 `cost_breakdown: dict[node_name, {...}]`
- 若不想一次做大，至少在文件中明講：
  - `current schema is sufficient for pure API or pure CLI, but not final for hybrid`

### MEDIUM 7. 檔案變更面仍略低估，analytics 未被列入

引入 `cost_mode` 後，真正受影響的不只：
- `engine.py`
- `decision.py`
- `web/server.py`

還包括：
- `magi/commands/analytics.py`
- 可能的 trace/report formatting

因為現在 analytics 會把 `cost_usd` 當成可直接聚合的數值處理。

**建議修正**
- File Change Summary 加入：
  - `magi/commands/analytics.py`
- 決定 analytics 在 `cost_mode="unavailable"` 時要如何顯示

### LOW 8. NodeBase 與檔案責任分配有描述衝突

文件一處寫：
- `magi/core/cli_node.py` 含 `NodeBase`

另一處又寫：
- `magi/core/node.py` 修改以抽出 `NodeBase`

這會讓實作者不清楚 `NodeBase` 最終該放哪裡。

**建議修正**
- 二選一，文件中統一：
  - 要嘛 `NodeBase` 放 `magi/core/node.py`
  - 要嘛抽成 `magi/core/node_base.py`

## 建議修改

### 一、把 structured output 升格為主設計

建議把 v4 的核心資料流改成：

1. provider-specific adapter 產生命令
2. 優先要求 JSON / structured output
3. parser 讀 structured payload
4. 若 provider 回傳 malformed plain text，再進 cleaner fallback

這會比先用 cleaner 穩得多。

### 二、重寫 `CodexAdapter`

建議明確定義：

```python
class CodexAdapter:
    # short prompt -> argument
    # long prompt -> stdin
    # isolated mode -> add --skip-git-repo-check
```

並額外決定：
- 是否強制 `--sandbox read-only`
- 是否強制 `--ephemeral`

### 三、修正文獻段落

把「論文支持 ICE framing」降級為：
- `implementation inspiration`
- 而非 `paper-backed conclusion`

### 四、補完 hybrid / analytics 的 schema 設計

至少在計畫中加一句：
- `Decision.cost_mode is provisional; hybrid mode will require cost breakdown or partial availability semantics.`

### 五、重新表述 large prompt fallback

不要再寫成「避免 Windows pipe 限制」，而要分成：
- argument limit 問題
- stdin path 問題

這兩者是不同層級的限制。

## 替代方案

若你想讓第一版更穩，可以考慮一個更保守的 v4.5：

- Phase 1 只支援 `claude` + `codex`
- `gemini` 降為實驗性或 optional
- 強制 structured output
- 不做 temp file fallback，先只做：
  - stdin for claude/gemini
  - argument/stdin dual-mode for codex

這樣會比現在的三家全包更容易做出可靠第一版。

## 最終審批

**審批結果: 有條件通過**

## 通過條件

- 修正 `CodexAdapter` 的 temp file / stdin 設計
- 在 isolation mode 下補上 `codex` repo-check 對策
- 把 structured output 升為主方案，Cleaner 改為 fallback
- 修正文獻敘事中對 ICE framing 的過度延伸
- 補列 analytics / hybrid cost schema 的影響面

達成以上條件後，這份 plan 就可以進入實作階段。
