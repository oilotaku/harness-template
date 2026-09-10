# 從零開始套用這個模板

這份文件的讀者是**手上什麼都還沒有**的人。目標是：從一台乾淨的機器開始，
一路走到「模板真的裝好了、防護真的生效了、跑完第一個任務」。

每一步都有**怎麼確認這一步成功了**——這個模板最大的風險就是「看起來裝好了，
其實保護根本沒生效」，所以驗證比安裝更重要。

---

## 0. 前置需求

| 需要 | 為什麼 | 怎麼確認 |
|---|---|---|
| **Python 3.9 以上** | 所有腳本都是 Python，不需要任何第三方套件 | `python3 --version`（Windows：`py --version`） |
| **git** | 取得模板、以及「每個 task 做完就 commit」的工作方式 | `git --version` |
| **Claude Code** | 派工流程需要 sub-agent 與 hook 支援 | 在終端機輸入 `claude` 能進入 | 

沒有 Python 的話：到 [python.org](https://www.python.org/downloads/) 裝，
Windows 安裝時記得勾「Add python.exe to PATH」。

**不需要**安裝任何套件。`psutil` 是選用的——裝了服務掃描會更準確
（`pip install psutil`），不裝也能跑，只是連接埠清單會標記為不完整。

> 沒有 Claude Code 也能用嗎？腳本本身（掃描、封存、稽核、估時）都是獨立的
> Python 程式，任何環境都跑得起來。但「實作者/檢驗者分離」需要能開出
> 互不共用上下文的 sub-agent，那部分要靠 Claude Code 或同等能力的工具。

---

## 1. 取得模板

### 路線 A：全新專案

```bash
git clone https://github.com/oilotaku/harness-template.git my-project
cd my-project
```

### 路線 B：套用到既有專案

把這些複製進你的專案根目錄：

```
CLAUDE.md          .claude/          scripts/          docs/          templates/
```

然後有兩件**很容易漏掉**的事：

1. **`.gitignore` 要加上 `.harness/`**。那個目錄放環境指紋、鎖定清單、
   隱藏測試 manifest、進度檢查點、掃描快取——全是本機狀態，不該進版控。

2. **`.claude/settings.json` 要「合併」而不是覆蓋**。你原本可能已經有自己的
   permissions 或 hooks。模板需要的是這兩個 hook：

   - `SessionStart` → `scripts/guard-selfcheck.py`
   - `PreToolUse` → `scripts/guard-hidden-tests.py`

   **少了 PreToolUse 那個，事前攔截整層就不存在**，而且不會有任何錯誤訊息——
   下一步就是要抓這種情況。

---

## 2. 初始化

```bash
python3 scripts/init.py
```

它依序跑三件事，結果只會印出來，不會自動幫你做任何決定：

| 區段 | 你該看什麼 |
|---|---|
| 機器效能掃描 | `這台機器最多撐得住的平行子智能體數` —— 那是**容量上限，不是建議值**。預設請序列執行 |
| 既有服務／連接埠掃描 | 建議可用的連接埠區間。如果寫「無法給出」，代表掃描不完整（通常是沒裝 psutil），**不要自己挑一個埠號就開下去** |
| 環境指紋 | 第一次執行會印「已將目前環境設為基準」，這是正常的 |

exit code：`0` 正常，`1` 代表環境指紋不符（換機器了）。

要給 Orchestrator 讀的機器可讀版本：

```bash
python3 scripts/init.py --json
```

---

## 3. 確認防護真的生效（最重要的一步）

```bash
python3 scripts/guard-selfcheck.py
```

**成功長這樣**：

```
===== 防作弊機制自我檢查（SessionStart）=====
防護正常：6/6 項檢查符合預期（隱藏測試讀寫皆被擋、一般檔案不受影響）。
受保護路徑（來源：預設值（沒有 harness.config.json））：
  ...
===== 結束 =====
```

它會拿「已知該被擋」的 payload 真的跑一次 guard，所以 6/6 代表**這一次**
攔截確實有作用，而不是「設定看起來對」。

如果看到這些，先修好再往下走：

| 訊息 | 意思 | 怎麼修 |
|---|---|---|
| 找不到 `scripts/guard-hidden-tests.py` | 腳本沒複製進來 | 補上 `scripts/` |
| 某幾項檢查不符預期 | guard 被改壞了 | 跑 `python3 scripts/test-guards.py` 看是哪一條 |
| 這些目錄看起來是隱藏測試，但不在受保護路徑內 | 有第二個隱藏測試目錄（monorepo 常見） | 見下一步的設定，或在該目錄放 README 寫明它不受保護 |

還有一種情況它抓不到：**hook 根本沒被 Claude Code 掛上**（`settings.json`
合併錯了）。判斷方式是在 Claude Code 裡開一個新 session——正常的話上面那段
自我檢查會自動出現在 session 開頭。**完全沒有輸出＝ hook 沒生效。**

---

## 4. 非 Python 專案：一定要設 `harness.config.json`

模板預設你的測試在 `tests/public` 與 `tests/hidden`。Go、Rust、TypeScript
的慣例都不長這樣，**不設定的話防護會找不到你的測試，等於完全沒有保護**。

在專案根目錄建立：

```json
{
  "public_test_paths": ["src/**/__tests__"],
  "hidden_test_paths": [".harness-hidden-staging"],
  "hidden_test_command": "npx vitest run --dir {dir}"
}
```

設定寫錯時腳本會**直接報錯**，不會靜靜退回預設值——用一個 typo 就能關掉保護
而且沒有訊號，是這個模板最不能接受的失效方式。完整欄位見
`docs/multi-language-support.md`。

---

## 5. 走一次完整流程，親眼看到機制動起來

### 5.1 讀範例（不會動到你的專案）

```bash
cd templates/examples/demo-fizzbuzz
python3 -m unittest discover -s tests/public -p 'test_*.py'
python3 -m unittest discover -s tests/hidden -p 'test_*.py'
```

兩組都會通過。這個範例的隱藏測試是**明文**，因為它的用途是讓你看到
「一份好的隱藏測試長什麼樣」——正式流程不是這樣，往下看。

### 5.2 真的封存一次

回到專案根目錄，在隱藏測試暫存區（預設 `tests/` 底下的 `hidden/`）寫一個測試檔，
然後：

```bash
python3 scripts/seal-hidden-tests.py --task-id T-001
```

它會印出一串**只出現這一次**的執行權杖。接著自己確認三件事：

1. 暫存區裡已經沒有明文了（檔案被搬走）
2. 封存庫在 repo 之外，而且內容是密文
3. 用權杖才跑得動：

   ```bash
   python3 scripts/run-hidden-tests.py --task-id T-001 --token <權杖>
   ```

   故意打錯權杖，它會拒絕——**沒有救援路徑是刻意的**，權杖遺失只能重寫一份
   隱藏測試再封存一次。留後門等於留繞過方式。

> **在 Claude Code 裡跑會被擋，在一般終端機不會。**
> `guard-hidden-tests.py` 是 Claude Code 的 PreToolUse hook，只在 Claude Code
> 呼叫工具時生效。所以上面這些「指令字串裡有暫存區路徑」的操作，
> 你在自己的終端機直接跑沒問題，但讓 Claude Code 幫你跑會被拒絕——
> **那正是它該做的事**（實作者不能碰暫存區）。

---

## 6. 你的第一個真實任務

1. 在 Claude Code 打開專案，用 `/task-plan` 丟出你的需求（一句話就行）。
   Orchestrator 會做：釐清 → 掃描 → 拆解 → 指派模型 → **估時** → **決定要不要裝 skill**，
   產出一份待你核准的計畫。
2. 你核准之後用 `/task-dispatch` 派工。順序固定：
   **檢驗者先寫測試並封存 → 實作者才開始 → 檢驗者驗收**。
3. 驗收不通過時，先看客觀次數再決定要不要再派一輪：

   ```bash
   python3 scripts/show-attempts.py --task-id T-001
   ```

   連續失敗通常代表**規格不清楚**，不是實作者不夠努力。

4. 每個 task 做完就 commit。中斷時最多只損失一個 task 的進度
   （原因見 `docs/token-strategy.md` §3）。

---

## 7. 常見卡關

| 症狀 | 原因 | 怎麼處理 |
|---|---|---|
| 新 session 開頭沒有自我檢查輸出 | hook 沒掛上（`settings.json` 合併錯） | 對照 §1 路線 B 第 2 點 |
| `拒絕：指令裡出現了隱藏測試暫存區的路徑` | **預期行為**，不是壞掉 | 要把路徑寫進檔案內容就用 Write/Edit 工具；要執行隱藏測試就用 `run-hidden-tests.py` |
| 每次都說環境指紋不符 | 真的換機器了 | 確認後 `python3 scripts/env-guard.py --update`。容器/CI 的隨機主機名**不會**造成誤報，所以它一響就代表真的變了 |
| Windows 主控台印中文崩潰 | 舊版問題 | 現在所有腳本啟動時會自己切 UTF-8，不必設 `PYTHONUTF8`。仍有問題請確認 Python ≥ 3.9 |
| 連接埠建議是「無法給出」 | 掃描不完整 | `pip install psutil` 後重掃；在那之前人工確認，不要猜 |
| 權杖弄丟了 | 沒有救援路徑（刻意） | 重寫一份隱藏測試再封存一次 |
| 想用中文當 `task_id` | 在 `LC_ALL=C` 這類 locale 下，非 ASCII 參數在作業系統層就編不出去 | `task_id` 用 `T-001` 這種形式，中文寫在 task-spec 標題裡 |

---

## 8. 確認整套都健康

改動任何機制之後（或想確認安裝完整時）：

```bash
python3 scripts/test-guards.py && python3 scripts/test-locks.py \
  && python3 scripts/test-vault.py && python3 scripts/test-env-guard.py \
  && python3 scripts/test-config.py && python3 scripts/test-scan-json.py \
  && python3 scripts/test-timing.py && python3 scripts/test-skills.py \
  && python3 scripts/test-attempts.py
```

九組、253 個案例應該全數通過。這也是最快的「我裝完整了嗎」檢查。

---

## 9. 想拆掉的時候

把 `.claude/settings.json` 裡那兩個 hook 拿掉就停用了防護。但請注意
**不要只留一半**：

- 只留 `PreToolUse` 不留 `SessionStart` → 機制壞掉時你不會知道
- 只留攔截不做封存（不跑 `seal-hidden-tests.py`）→ 隱藏測試就明文躺在工作目錄裡，
  而攔截是列舉式的，擋不完

四層的關係見 `README.md` 的「防作弊機制的組成」。
