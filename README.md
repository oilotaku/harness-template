# Harness 工程模板

[![CI](https://github.com/oilotaku/harness-template/actions/workflows/ci.yml/badge.svg)](https://github.com/oilotaku/harness-template/actions/workflows/ci.yml)

一個給 Claude Code（或任何支援 sub-agent 的 AI 開發流程）使用的**基本開發框架**，
用來把「一個模糊的需求」變成「多個可獨立驗收、互相制衡的子任務」。

## 這個模板解決什麼問題

**AI 自我驗證的問題**

- 避免 AI 自己既是球員又是裁判（實作者/檢驗者混在一起 → 容易「自己給自己過」）
- 避免 AI 為了讓測試通過而作弊（寫死回傳值、跳過測試、mock 掉核心邏輯）
- 避免 AI 在需求不清楚時自己腦補、發散實作

**工程紀律的問題**

- 避免在不知道機器實際效能、既有服務的情況下亂開平行任務或撞埠號
- 避免不知道成本花在哪（尤其訂閱制方案下**卡住你的是消耗速率，不是花費**）
- 避免「寫了規則但沒有任何東西讓它成立」——這是本模板最核心的一條線

## 核心設計：把「靠自律的規則」換成機制

規則寫在文件裡，只有在**有東西讓它成立**時才算數。這個模板的每一條規則
都對應一個機制，而不是一句叮嚀：

| 規則 | 不靠什麼 | 靠什麼 |
|---|---|---|
| 實作者看不到隱藏測試 | 提示詞叮嚀 | 加密封存到 repo 之外，解密需要一次性權杖 |
| 公開測試不可被竄改 | 事前攔截有沒有成功 | 事後 sha256 稽核，被改過就查得出來 |
| 連續失敗 ≥3 次要停損 | implementer 自我申報 | 由執行測試的一方寫入的客觀次數 |
| 檢驗者不動手改實作 | 提示詞叮嚀 | 它沒有 `Write` 工具；skill 政策也擋掉會產生程式碼的 skill |
| 文件、設定、CI 保持一致 | 記得同步 | 漂移測試——少同步一處就直接紅 |
| 環境變了要停下來確認 | 使用者自己注意 | 環境指紋比對，且刻意**不把容器的隨機主機名當成環境變更**（總是誤報的守門等於沒有守門） |

## 快速開始

> **完全從零開始**（還沒裝 Python / Claude Code、或要套用到既有專案）請看
> **[`docs/getting-started.md`](docs/getting-started.md)**——那份文件每一步都附
> 「怎麼確認這一步成功了」，因為這個模板最大的風險是**看起來裝好了、
> 但保護其實沒生效**。下面這段假設你已經有可用的環境。

1. `git clone` 這個 repo（或把 `harness-template/` 內容複製到你的專案根目錄）。

2. 執行初始化（機器效能掃描 → 既有服務掃描 → 環境指紋建立/比對）：

   ```bash
   python3 scripts/init.py
   ```

   結果只會印出來，不會自動幫你做任何決定。三支掃描腳本與 `init.py` 都支援
   `--json`（stdout 只有 JSON），給 Orchestrator 讀：

   ```bash
   python3 scripts/init.py --json
   ```

   裡面已經算好 `max_parallel_agents` 與 `suggested_port_range`，不必再從中文
   散文裡自己換算。換了機器且確認過是刻意更換，用
   `python3 scripts/env-guard.py --update` 把目前環境設為新的基準指紋。

3. **非 Python 專案**：在專案根目錄建立 `harness.config.json`，指定你的測試目錄
   與測試指令。不設定的話防作弊機制會找不到你的測試，等於完全沒有保護
   （`guard-selfcheck.py` 會在 session 開始時警告你）。範例見
   `docs/multi-language-support.md`。

4. 在 Claude Code 中打開專案，讓 `Orchestrator`（見 `.claude/agents/orchestrator.md`）
   依 `docs/task-decomposition-guide.md` 拆解你的需求。拆解完、派工前它還會做兩件事：

   ```bash
   python3 scripts/estimate-time.py --tasks doc:3,module:1 --rounds 2   # 估時
   python3 scripts/suggest-skills.py --role all --deliverable pdf       # 要不要裝 skill
   ```

5. 依照 `docs/implementer-verifier-workflow.md` 的順序執行：
   **檢驗者先寫測試 → 實作者才開始寫程式 → 檢驗者驗收**。

6. 驗收不通過時，先看客觀次數再決定要不要再派一輪：

   ```bash
   python3 scripts/show-attempts.py --task-id <task_id>
   ```

   連續失敗通常代表**規格不清楚**，不是實作者不夠努力。

## 目錄導覽

| 路徑 | 用途 |
|---|---|
| `CLAUDE.md` | 全域規則（黃金法則、工作流程總覽） |
| `.claude/agents/` | 子智能體定義（實作者 3 個、檢驗者 3 個） |
| `.claude/commands/` | `/task-plan` `/task-dispatch` `/machine-check` 斜線指令（用法見下） |
| `scripts/` | 掃描、防作弊機制本體、決策輔助工具（見下面兩張表） |
| `docs/` | **從零開始套用**、任務拆解、模型/思考分配、實作/檢驗分離、多語言支援、記憶管理、token 成本策略、根因分析與修正流程、執行時間預測、skill 安裝決策 |
| `templates/` | task-spec、驗收報告、驗收標準對照表範本（含 `examples/` 的完整範例） |
| `reports/` | 驗收報告落檔處（檢驗者沒有 `Write` 工具，由 Orchestrator 落檔） |
| `harness.config.json` | （選用）這個專案的測試路徑慣例；非 Python 專案要設 |
| `skills.catalog.json` | （選用）這個專案要裝哪些 skill、給誰 |

### 三個斜線指令的使用時機

| 指令 | 什麼時候用 | 會做什麼 |
|---|---|---|
| `/task-plan` | **還沒有計畫時**——手上只有一句需求 | 釐清 → 掃描 → 拆解 → 指派模型、估時、決定 skill，產出待核准的計畫 |
| `/task-dispatch` | **計畫已經核准之後** | 依計畫派工：檢驗者先寫測試並封存，實作者才開始 |
| `/machine-check` | 只想看機器狀態，不想觸發拆解 | 單獨跑機器效能、既有服務、環境指紋三項掃描 |

順序是固定的：`/task-plan` 的產出是 `/task-dispatch` 的輸入。跳過前者直接派工，
等於讓實作者拿著一份沒有範圍邊界的規格開工——那是全流程最貴的失敗方式
（見 `docs/token-strategy.md` §2.1）。

## 防作弊機制的組成

| 腳本 | 角色 | 什麼時候跑 |
|---|---|---|
| `scripts/seal-hidden-tests.py` | **實體隔離**：把隱藏測試暫存區加密搬到 repo 之外，並產生一次性執行權杖 | verifier-test-writer 寫完隱藏測試後 |
| `scripts/run-hidden-tests.py` | 隱藏測試的**唯一執行入口**，需要權杖才解得開；順帶記錄驗收嘗試次數 | verifier-reviewer 驗收時 |
| `scripts/hidden_vault.py` | 上面兩支共用的封存庫邏輯（加密、manifest、路徑規則） | 被 import，不直接執行 |
| `scripts/harness_config.py` | 讀 `harness.config.json`：這個專案的測試路徑慣例（非 Python 專案一定要設） | 被 import，不直接執行 |
| `scripts/guard-hidden-tests.py` | **事前攔截**：PreToolUse hook，擋下對暫存區、封存庫、`.harness/`（`progress/` 除外）與已鎖定公開測試的存取 | 每次工具呼叫（由 `.claude/settings.json` 掛上） |
| `scripts/lock-tests.py` | 把公開測試的**路徑 + sha256** 寫進 `.harness/locked-tests.list` | verifier-test-writer 寫完公開測試後 |
| `scripts/verify-locks.py` | **事後稽核**：重算雜湊比對，抓出「事前攔截被繞過」的竄改 | verifier-reviewer 驗收的第一步 |
| `scripts/guard-selfcheck.py` | **自我檢查**：用已知該被擋的 payload 實跑一次；另外掃出「看起來是隱藏測試、卻不在受保護路徑內」的目錄 | SessionStart hook |

四層各擋不同的東西，缺一不可：

1. **實體隔離**（封存）：檔案不在工作目錄裡、內容是密文 —— 這是主防線
2. **事前攔截**（hook）：Bash 指令只要**解析得出**暫存區或封存庫路徑就擋，不分動詞
   （`bash -c`、`find`、`xargs`、`tar`、`$(…)`、變數展開都涵蓋）；
   完全不寫出路徑的混淆不在這一層的範圍內，那由第 1 層擋
3. **事後稽核**（雜湊）：不依賴攔截是否成功，被改過就查得出來
4. **自我檢查**（SessionStart）：機制壞掉時至少會有訊號，而不是默默全開

> **維護這個 repo 的人會踩到的一件事**：第 2 層是「指令字串裡提到那個路徑就擋」，
> 所以連「想把路徑寫進某個檔案內容」的 Bash heredoc 也會被擋。
> 那條路請改用 Write/Edit 工具——它只看目標檔案，不看內容。
> 這是刻意的取捨：多擋一點是安全的，少擋一點才危險。

## 決策輔助工具

這些不是防護，是讓 Orchestrator 用數字而不是感覺做決定：

| 腳本 | 角色 | 什麼時候跑 |
|---|---|---|
| `scripts/capacity.py` | 由 CPU/記憶體算出平行度**上限**、挑出沒被佔用的連接埠區間（純函式） | 被 import，不直接執行 |
| `scripts/scan_cache.py` | 掃描結果快取（`.harness/last-scan.json`），能力指紋不符就不給重用 | 被 import，不直接執行 |
| `scripts/timing.py` / `scripts/estimate-time.py` | 執行時間預測：分類單價 + 週期開銷 + CI 重試，再用專案歷史校準 | 拆解完估時、驗收後回填實際值 |
| `scripts/skill_policy.py` / `scripts/suggest-skills.py` | 依專案目標決定要安裝哪些 skill；強制「檢驗者拿不到會產生程式碼的 skill」 | 派工前 |
| `scripts/attempts.py` / `scripts/show-attempts.py` | 驗收嘗試次數與停損判斷；次數由 runner 寫入，不靠 implementer 自我申報 | 驗收後、決定是否再派一輪時 |

三個共通的設計原則（三支腳本各自的說明裡都有詳述）：

- **一律給區間或上限，不給看起來很有把握的單一數字。**
  `max_parallel_agents` 是機器容量上限、不是建議值（預設序列執行）；
  估時一律回區間。
- **資訊不完整時說「不知道」，不說「沒問題」。** 連接埠掃描不完整時
  `suggested_port_range` 是 `null`；嘗試次數的紀錄檔壞掉時 `should_stop`
  是 `null` 而不是 `false`——「讀不到」跟「沒有失敗過」是兩件事。
- **設定壞掉一律報錯，不靜靜退回預設值。** 用一個 typo 就能關掉保護，
  而且沒有任何訊號，是這個 repo 最不能接受的失效方式。

## 測試與 CI

改動任何一支之後，請執行：

```bash
python3 scripts/test-guards.py && python3 scripts/test-locks.py \
  && python3 scripts/test-vault.py && python3 scripts/test-env-guard.py \
  && python3 scripts/test-config.py && python3 scripts/test-scan-json.py \
  && python3 scripts/test-timing.py && python3 scripts/test-skills.py \
  && python3 scripts/test-attempts.py
```

這九組（共 **253 個案例**）也會由 CI 在 **Linux / macOS / Windows × Python 3.9 / 3.13**
六種組合上自動執行，Linux 另外多跑一輪 `LC_ALL=C`（非 UTF-8 locale）
（見 `.github/workflows/ci.yml`）。

這個 repo 特別需要 CI，因為**機制退化是無聲的**——guard 少擋一種路徑寫法、
封存腳本少刪一個檔案，功能看起來都還正常，只有測試會發現。CI 一開就立刻抓到
一個一直存在、但從沒被發現的 bug：所有腳本在 Windows 主控台上一印中文就崩潰。

支援的 Python 版本：**3.9 以上**，且不需要任何第三方套件
（`psutil` 是選用的，裝了會讓服務掃描更準確）。

Windows 使用者不需要另外設定 `PYTHONUTF8`：所有腳本啟動時會自己把 stdout/stderr
切成 UTF-8（見 `scripts/utf8_output.py`），否則系統 ANSI 代碼頁編不出繁體中文，
腳本會直接崩潰。

> 一個跨平台的實務限制：`task_id` 請用 ASCII（例如 `T-012`）。在 `LC_ALL=C`
> 這類 locale 下，非 ASCII 的命令列參數在作業系統層就編不出去，跟本模板無關。
> 中文寫在 task-spec 的標題裡即可。

## Token 成本

這個模板一個 task 的固定開銷大約 5.6 萬字元——三個不共用上下文的子智能體
session 各自重載 `CLAUDE.md` 與自己的定義檔。那是「獨立驗證鏈」的必要代價，
但知道錢花在哪，才知道該省哪裡。

最高 CP 值的三件事：**把 task-spec 的範圍邊界寫清楚**（不寫的話 implementer
得自己搜尋程式碼，那是最不可控的成本）、**低風險任務的驗收先小後大**、
**`CLAUDE.md` 只寫規則與指向**（它的乘數最高）。

訂閱制方案（例如 Pro）還要多顧一件事：**卡住你的是消耗速率，不是花費**。
撞到用量上限的代價是複利的——等待、session 斷掉、重載一次固定開銷、
然後更快撞到下一次。所以那類方案下預設**序列執行**、每個 task 做完就 commit、
把進度寫進 `.harness/progress/<task_id>.md`，讓被打斷之後的恢復成本
是「讀一個小檔案」而不是「重建整段推理」。

量測數據、訂閱制下的完整策略、不該省的清單、以及「平行度不省 token」
這個常見誤解，見 `docs/token-strategy.md`。

## 設計依據

本模板參考 [claude-code-ultimate-guide-zh](https://github.com/JAYcodr/claude-code-ultimate-guide-zh)
中「智能體團隊 (Agent Teams)」的拓樸決策框架，以及該指南提及的
**SE-CoVe（獨立驗證鏈，Meta AI, ACL 2024）**概念，將「產生答案」與「驗證答案」
拆成兩條完全獨立的鏈路。

`docs/improvement-suggestions.md` 是這個模板的一份完整審視報告（18 項，全部結案），
記錄了每一項的問題、修法、以及**實際落地時偏離原始草案的地方與理由**。
它只給人看——那份文件比其他所有 `docs/` 加起來還大，不要放進任何子智能體的
閱讀路徑（見 `docs/token-strategy.md` §1）。
第二輪審視 `docs/improvement-suggestions-round2.md`（同樣只給人看）針對第一輪
引入的新信任根——manifest、鎖定清單、keystream——做了實測，並列出尚未處理的項目。
