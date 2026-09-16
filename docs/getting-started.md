<!-- 語言 / Language: **繁體中文** · [English](getting-started.en.md) -->

# 從零開始套用這個模板

這份文件的讀者是**手上什麼都還沒有**的人。目標是：從一台乾淨的機器開始，
一路走到「模板真的裝好了、防護真的生效了、跑完第一個任務」。

每一步都有**怎麼確認這一步成功了**——這個模板最大的風險就是「看起來裝好了，
其實保護根本沒生效」，所以驗證比安裝更重要。

> **趕時間？** 裝好 Python + git + Claude Code（見 §0）之後，`python3 scripts/setup.py`
> 會把 §2～§4（產生設定、選保護等級、建版本號）一次做完，並印出接下來的指令。
> 這份文件是給想理解**每一步在做什麼、怎麼確認它成功**的人——精靈幫你跑，
> 這裡幫你懂。

> **覺得東西很多？** 這個模板腳本多、機制多，但你**只需要確認三件事**，
> 其餘都是它自己在跑：
>
> 1. **裝好了嗎** → `python3 scripts/init.py`（§2）
> 2. **防護真的生效了嗎**（最重要）→ `python3 scripts/guard-selfcheck.py`
>    看到「防護正常：N/N」就對了（§3）。這一步是本模板唯一真正致命的失誤來源
>    ——「看起來裝好、其實保護沒生效」——的唯一防線
> 3. **整套健康嗎**（改過機制或想全面確認時）→ 跑 §8 那一行回歸測試
>
> 這三行以外的章節都是「幫你懂為什麼」，不是每次都得做的步驟。

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

3. **建議再加上這組 `deny`**（第三輪 P0-8）。強制力本體——驗收 runner、簽章與
   加密、事後稽核、guard 自己、hook 設定、檢驗者的行為準則——如果 implementer
   改得到，驗收結果就可以被偽造：實測覆寫 `scripts/run-hidden-tests.py` 之後，
   用**完全正確的權杖**執行驗收會得到 exit 0 與「隱藏測試全部通過」，
   而三項事後稽核全部回報正常。

   ```json
   "deny": [
     "Write(./scripts/**)", "Edit(./scripts/**)",
     "Write(./.claude/**)", "Edit(./.claude/**)",
     "Write(./CLAUDE.md)",  "Edit(./CLAUDE.md)",
     "Write(./harness.config.json)", "Edit(./harness.config.json)"
   ]
   ```

   guard 從第三輪起也會擋同一組路徑，但**只在這個 repo 封存過 task 之後**
   （否則維護 harness 本身的人會被自己的 hook 擋住）。這組 deny 是第二層，
   兩層都只是縱深防禦：拿得到 Bash 的子智能體寫一支腳本去改仍然穿得過去。
   根本解是不要執行 repo 裡的程式碼——第三輪封存驗收程式碼（P0-8 (a)，記錄在
   git 歷史，見 `docs/history/README.md`），第四輪更進一步把驗收搬到 CI
   （見 `docs/ci-verification.md`）。

   > 模板自己的 repo **刻意沒有**這組 deny——它就是模板本身，`scripts/` 是它的
   > 產品程式碼。你的專案不是這種情況，請加上。

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

### 想輕量一點：minimal 模式

上面第 3 步那套 hook 覺得太重的話，可以只開第 1 層（實體隔離）：

```json
{ "protection": { "level": "minimal" } }
```

這樣就不需要 `.claude/settings.json` 的 hook，三支腳本（封存、執行、作廢）
就跑得動，而「implementer 看不到隱藏測試」這件事仍然成立。
代價是**公開測試被改不會有人抓到**，還有一條操作紀律：測試寫完立刻封存，
不要留在暫存區過夜。完整的取捨表見 [`protection-levels.md`](protection-levels.md)。

關掉的那幾層每一次都會被講出來（封存時、驗收時、每個 session），
所以不會發生「以為四層都在、其實只有一層」。

### 想讓驗收跑在 implementer 碰不到的地方：CI 驗收

這個模板擋不住「決心繞過、而且跟 verifier 共用同一個 OS 使用者」的子智能體——
那不是還沒修好的一項，是本機自我檢查的結構性終點。要跨過它，驗收得搬到
GitHub Actions：

```json
{ "protection": { "ci_verification": true } }
```

設定步驟（密文包要 commit 進**預設分支**、權杖存成 repository secret、
以及為什麼分支保護是必要而不是建議）見 [`ci-verification.md`](ci-verification.md)。

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

它會先鎖定公開測試、再逐檔加密搬走、再用權杖簽章 manifest，最後印出一串
**只出現這一次**的執行權杖。接著自己確認四件事：

1. 暫存區裡已經沒有明文了（檔案被搬走）
2. 封存庫在 repo 之外，而且內容是密文
3. 基線執行——證明這份隱藏測試在沒有實作時是紅的：

   ```bash
   python3 scripts/run-hidden-tests.py --task-id T-001 --token <權杖> --baseline
   ```

   紅了它會把結果簽進 manifest；綠了它會直接告訴你「沒有鑑別力」。
   這一跑不計入停損次數。
4. 用權杖才跑得動正式驗收：

   ```bash
   python3 scripts/run-hidden-tests.py --task-id T-001 --token <權杖>
   ```

   故意打錯權杖，它會拒絕；用一支腳本改 `.harness/hidden-manifest.json` 裡的
   `test_command`，它也會拒絕（簽章不符）——**沒有救援路徑是刻意的**，
   權杖遺失只能重寫一份隱藏測試再封存一次。留後門等於留繞過方式。

   「遺失」在實務上最常見的原因不是打錯字，是**執行測試時撞到用量上限，
   握有權杖的 session 被回收**。那時候不要對著解不開的密文反覆試，
   走作廢重來的流程（見 §7 與 `docs/implementer-verifier-workflow.md`）：

   ```bash
   python3 scripts/discard-sealed-task.py --task-id T-001 --token-lost --confirm
   ```

   它會刪掉再也用不到的密文、在 manifest 留下一塊墓碑，然後告訴你怎麼重寫。
   作廢**不會**把歷史洗白：重新封存時作廢次數會進到簽過章的新項目裡。

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
| 權杖弄丟了（最常見：執行測試時撞到用量上限，session 被回收） | 沒有救援路徑（刻意） | `python3 scripts/discard-sealed-task.py --task-id <id> --token-lost --confirm` 作廢，再依同一份 task-spec 重寫一份隱藏測試重新封存 |
| session 開頭出現「清掉了 N 個先前留下的解密目錄」 | 上一次執行是被強制中斷的（撞上限、被 kill），明文從那時起一直留在磁碟上 | 已經自動清掉了。把這件事寫進驗收報告；明文當時落在封存庫裡，是 guard 擋得住的路徑 |
| 想用中文當 `task_id` | 在 `LC_ALL=C` 這類 locale 下，非 ASCII 參數在作業系統層就編不出去 | `task_id` 用 `T-001` 這種形式，中文寫在 task-spec 標題裡 |

---

## 8. 確認整套都健康

改動任何機制之後（或想確認安裝完整時）：

```bash
python3 scripts/test-guards.py && python3 scripts/test-locks.py \
  && python3 scripts/test-vault.py && python3 scripts/test-env-guard.py \
  && python3 scripts/test-config.py && python3 scripts/test-scan-json.py \
  && python3 scripts/test-timing.py && python3 scripts/test-skills.py \
  && python3 scripts/test-attempts.py && python3 scripts/test-version.py \
  && python3 scripts/test-design.py && python3 scripts/test-ci-verify.py \
  && python3 scripts/test-protection-levels.py && python3 scripts/test-setup.py
```

十四組、466 個案例應該全數通過。這也是最快的「我裝完整了嗎」檢查。

---

## 9. 想拆掉的時候

把 `.claude/settings.json` 裡那兩個 hook 拿掉就停用了防護。但請注意
**不要只留一半**：

- 只留 `PreToolUse` 不留 `SessionStart` → 機制壞掉時你不會知道
- 只留攔截不做封存（不跑 `seal-hidden-tests.py`）→ 隱藏測試就明文躺在工作目錄裡，
  而攔截是列舉式的，擋不完

四層的關係見 `README.md` 的「防作弊機制的組成」。
