# Harness 工程模板

[![CI](https://github.com/oilotaku/harness-template/actions/workflows/ci.yml/badge.svg)](https://github.com/oilotaku/harness-template/actions/workflows/ci.yml)

**版本**：0.1.0

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

## 先講清楚：它擋什麼、不擋什麼

這一段放在最前面是刻意的。這個模板最容易造成的傷害不是「保護不夠」，
是**讓人以為保護比實際更強**——而前三輪修掉的，正是「宣稱與實測相反」這件事。

**擋得住的**：「順手看一眼」「習慣性搜整個 repo」「用工具的正常用法碰到」
這類**非刻意**的洩題。封存之後隱藏測試不在工作目錄裡、內容是密文、
解密需要一把只交給檢驗者的權杖。這一層很硬，而且不需要任何人自律。

**擋不住的**（預設的本機設定）：一個**決心繞過、而且跟 verifier 共用同一個
OS 使用者**的子智能體。權杖會經過 Claude Code 的 transcript；解密後的明文在
測試執行期間存在於磁碟上；而任何「驗收程式碼的自我檢查」都可以被
「連檢查自己那一段一起換掉」繞過——那是自我檢查的結構性終點，不是還沒修好的一項。

**要擋住那一類，就不能再加一層。** 得讓驗收根本不在 implementer 的執行環境裡跑：
把隱藏測試的執行搬到 GitHub Actions，workflow 與密文包都取自預設分支，
權杖是 repository secret，他端只拿得到 exit code 與加密的明細。
設定方式、以及**這個作法自己的新信任根與殘留限制**（誰能改預設分支、
誰讀得到 CI log、被測程式碼本來就跑得到明文），見
[`docs/ci-verification.md`](docs/ci-verification.md)——一樣沒有粉飾。

**強度是可選的**：預設四層全開（`full`），但只想要「隱藏測試 implementer 看不到」
這件事的話，`minimal` 用三支腳本與三行設定就跑得動，關掉的那幾層每一次都會被
講出來。見 [`docs/protection-levels.md`](docs/protection-levels.md)。

## 核心設計：把「靠自律的規則」換成機制

規則寫在文件裡，只有在**有東西讓它成立**時才算數。這個模板的每一條規則
都對應一個機制，而不是一句叮嚀：

| 規則 | 不靠什麼 | 靠什麼 |
|---|---|---|
| 實作者看不到隱藏測試 | 提示詞叮嚀 | 加密封存到 repo 之外（每檔獨立金鑰），解密需要一次性權杖 |
| 驗收結果不可被偽造 | 「`.harness/` 有 hook 擋著」 | runner 信任的每個 manifest 欄位都用權杖簽過章；**驗收用的程式碼本身也一起封存到 repo 之外**，每個檔案的 sha256 同樣進簽章範圍——改了 repo 裡的副本不影響驗收，改了封存版就跑不動 |
| 公開測試不可被竄改 | 事前攔截有沒有成功 | 事後 sha256 稽核；清單本身的 sha256 也在簽過章的 manifest 裡，整份刪掉也查得出來 |
| 隱藏測試真的有鑑別力 | 「檢驗者會寫好」 | 封存後強制做基線執行：沒有實作時必須是紅的，結果簽進 manifest |
| 連續失敗 ≥3 次要停損 | implementer 自我申報 | 由執行測試的一方寫入、逐筆簽章的客觀次數；筆數也簽進 manifest，砍掉紀錄查得出來 |
| bug 修的是根因不是症狀 | 「修完記得找同類」 | 公開放被回報的最小重現、隱藏放同類變體：公開綠 + 隱藏紅 = 只修了那一個 case |
| 版本號不會漂 | 記得每個地方一起改 | README／程式／manifest 的版本宣告成 `mirrors`，一起更新、一起驗一致 |
| 畫面不會每個 task 一套 | 「照設計稿做」 | 顏色/間距/字級由專案宣告成角色化 token，實作者取用而不是發明 |
| 可及性真的被驗到 | 「a11y 是隱性驗收標準」 | 對比度對明暗兩套逐組算 WCAG AA——預設色票第一版的深色邊框是 2.99、門檻 3.00，眼睛看不出來 |
| 設計不會被默默套用 | 模板附什麼就用什麼 | 預設標記成「未經這個專案確認」，每個 session 提醒到有人真的問過使用者為止 |
| 檢驗者不動手改實作 | 提示詞叮嚀 | 它沒有 `Write` 工具；skill 政策也擋掉會產生程式碼的 skill |
| 文件、設定、CI 保持一致 | 記得同步 | 漂移測試——少同步一處就直接紅 |
| 驗收不在被驗的那一方手上 | 「驗收程式碼有自我檢查」 | 正式驗收跑在 GitHub Actions：workflow 與密文包都取自**預設分支**，權杖是 repository secret，implementer 端只拿得到 exit code 與加密的明細 |
| 保護強度是選擇，不是預設 | 「四層全開，不然別用」 | `protection.level` 分 `minimal`／`full`；關掉的層在封存、驗收、每個 session 都會被講出來 |
| 環境變了要停下來確認 | 使用者自己注意 | 環境指紋比對，且刻意**不把容器的隨機主機名當成環境變更**（總是誤報的守門等於沒有守門）。首次執行會明講「本次沒有比對任何東西」，基準被 gitignore 時另外警告它在重新 clone 的環境裡等同停用 |

### 上面那段話的由來

這一段是實測紀錄，不是設計說明。本機這套機制擋的是「順手看一眼」「習慣性搜整個 repo」
「用工具的正常用法碰到」這類**非刻意**的洩題。它**不擋**一個「決心繞過、而且跟 verifier
共用同一個 OS 使用者」的子智能體——權杖會經過 Claude Code 的 transcript，解密後的明文
在測試執行期間短暫存在於磁碟上。那需要獨立的使用者或容器，超出本模板範圍。

⚠️ **這裡曾經還寫著「刻意的繞過會留下痕跡（簽章不符、稽核不符、嘗試次數對不上）」，
那句話被第三輪的實測證否，所以拿掉了。** 當時改寫 `scripts/run-hidden-tests.py`
這條路一個痕跡都沒留：驗收 exit 0、印「隱藏測試全部通過」、權杖被送進被改過的程式碼的
argv，而 `verify-locks.py` 與 `guard-selfcheck.py --strict` 都回報正常。

那條路現在關上了，但**關上它的是派工方式，不是某一支程式**：驗收用的程式碼會連同
隱藏測試一起封存到 repo 之外（`<封存庫>/_runner/`），每個檔案的 sha256 進簽章範圍，
而 Orchestrator 派工時給 `verifier-reviewer` 的是**那個絕對路徑**。所以：

- 改 repo 裡的 `scripts/run-hidden-tests.py` → 不影響驗收（驗收跑的不是它）
- 改封存版的任何相依模組 → sha256 對不上，直接 exit 2
- 事前層另外擋著：guard 在「這個 repo 封存過 task 之後」擋下對強制力本體
  （`scripts/`、`.claude/`、`CLAUDE.md`、`harness.config.json`）的寫入；
  套用到你自己的專案時還建議加一組 `deny`（見 `docs/getting-started.md`）

**仍然不擋的**（誠實記載）：如果被換掉的是封存版的 `run-hidden-tests.py` 自己、
而且換掉的版本把自我比對整段拿掉，就沒有人會執行那段檢查——任何自我檢查都有這個
循環。封存版買到的是「攻擊者得寫到 repo 之外（guard 對封存庫不分動詞一律擋），
而且動其他四個檔案都會被抓到」。要完全消除需要獨立的使用者或容器，超出本模板範圍。
細節見 `docs/ci-verification.md`（第四輪把這個循環的殘留限制承接過去）；
第三輪當時的實測記錄在歷史裡的 round3 報告 P0-8（取得方式見 `docs/history/README.md`）。

明文解在**封存庫底下**而不是 `/tmp`，所以萬一行程被強制中斷（撞到用量上限、
容器被回收）沒清乾淨，殘留至少落在 guard 擋得住的路徑裡；下一次執行、`--sweep`、
以及每個 session 的自我檢查都會清掉並出聲。這是事後補救，不是預防——
從被砍到下一次執行之間，明文確實還在。

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

4. **建立版本號**（`guard-selfcheck.py` 會在 session 開始時提醒你還沒有）：

   ```bash
   python3 scripts/version.py --init
   ```

   模板產出的程式要有版本號——沒有的話，使用者回報「壞掉了」時沒有任何東西
   可以定位是哪一版。版本要放哪、還寫在哪些地方（README、程式的 `--version`、
   套件 manifest）都由 `harness.config.json` 宣告，見 `docs/versioning.md`。

5. 在 Claude Code 中打開專案，讓 `Orchestrator`（見 `.claude/agents/orchestrator.md`）
   依 `docs/task-decomposition-guide.md` 拆解你的需求。拆解完、派工前它還會做兩件事：

   ```bash
   python3 scripts/estimate-time.py --tasks doc:3,module:1 --rounds 2   # 估時
   python3 scripts/suggest-skills.py --role all --deliverable pdf       # 要不要裝 skill
   ```

6. 依照 `docs/implementer-verifier-workflow.md` 的順序執行：
   **檢驗者先寫測試 → 實作者才開始寫程式 → 檢驗者驗收**。

7. 驗收不通過時，先看客觀次數再決定要不要再派一輪：

   ```bash
   python3 scripts/show-attempts.py --task-id <task_id>
   ```

   連續失敗通常代表**規格不清楚**，不是實作者不夠努力。

## 目錄導覽

| 路徑 | 用途 |
|---|---|
| `CLAUDE.md` | 全域規則（黃金法則、工作流程總覽） |
| `.claude/agents/` | 子智能體定義（Orchestrator 1 個、實作者 3 個、檢驗者 3 個） |
| `.claude/commands/` | `/task-plan` `/task-dispatch` `/machine-check` 斜線指令（用法見下） |
| `scripts/` | 掃描、防作弊機制本體、決策輔助工具（見下面兩張表） |
| `docs/` | **從零開始套用**、**CI 驗收**、**保護等級**、任務拆解、模型/思考分配、實作/檢驗分離、多語言支援、記憶管理、token 成本策略、根因分析與修正流程、**使用者回報處理**、**版本號**、**前端/GUI 預設設計**、執行時間預測、skill 安裝決策 |
| `templates/` | task-spec、驗收報告、驗收標準對照表、**使用者回報單**範本（含 `examples/` 的完整範例） |
| `reports/` | 驗收報告落檔處（檢驗者沒有 `Write` 工具，由 Orchestrator 落檔） |
| `releases/` | 每個版本一個資料夾，存放那一版的完整原始碼（升版時自動產生，可關掉；見 `docs/versioning.md` §4.5） |
| `VERSION` | 這個專案的版本號（位置可由 `harness.config.json` 改；只能由 `scripts/version.py` 寫入） |
| `design.tokens.json` | 前端／GUI 的設計基準：角色化的顏色、圓角、間距、字級、過渡（沒有圖形介面的專案可用 `"design": false` 關掉） |
| `.github/workflows/` | CI：`ci.yml` 跑模板自己的回歸測試；`verify-hidden-tests.yml` 是**隱藏測試的正式驗收**（見 `docs/ci-verification.md`） |
| `ci/sealed/` | CI 驗收用的密文包（每個 task 一個資料夾，內容是密文 + 簽過章的 manifest 項目）。會進版控，而且要 commit 進**預設分支**才生效 |
| `harness.config.json` | （選用）這個專案的測試路徑慣例、版本號來源與顯示位置、**保護等級**；非 Python 專案要設 |
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
| `scripts/seal-hidden-tests.py` | **實體隔離**：鎖定公開測試 → 把暫存區的隱藏測試逐檔加密（每檔獨立金鑰）搬到 repo 之外 → **把驗收用的程式碼一起複製進封存庫並記下 sha256** → 用權杖簽章 manifest，並產生一次性執行權杖。已進 git 歷史的檔案拒絕封存 | verifier-test-writer 寫完測試後 |
| `scripts/run-hidden-tests.py` | 隱藏測試的**唯一執行入口**：驗 manifest 簽章 → 比對封存版程式碼的 sha256 → 比對鎖定清單 sha256 → 解密執行。**驗收要跑封存庫裡那一份**（`<封存庫>/_runner/`，由 seal 複製、絕對路徑由 Orchestrator 轉交），repo 裡這份 implementer 改得到。`--baseline` 證明測試在沒有實作時是紅的；正式模式記錄簽過章的嘗試次數 | test-writer 封存後（`--baseline`）、verifier-reviewer 驗收時 |
| `scripts/discard-sealed-task.py` | **權杖遺失後的唯一出路**：把再也解不開的密文移進封存庫的 `_discarded/`（不是刪掉——它讓「這個 task 存在過」留下要另外動手才抹得掉的證據）、清掉殘留明文、在 manifest 留下墓碑、往 repo 之外的封存流水帳追加一行、指出重寫流程。不是救援路徑——作廢次數會帶進重新封存後簽過章的項目，歷史洗不白 | 權杖連同 session 一起消失時（最常見：執行測試時撞到用量上限） |
| `scripts/export-sealed-task.py` | **把驗收搬出這台機器的第一步**：把封存庫裡的密文複製一份到 repo 內的 `ci/sealed/<task_id>/`，附上簽過章的 manifest 項目。不解密、不重新簽章——所以匯出這個動作本身不需要被信任 | 封存後（宣告 `ci_verification` 時由 seal 自動執行） |
| `scripts/ci-verify.py` | **CI 上的驗收入口**：驗簽 → 解密到受測目錄之外 → 執行測試。job log 只有 exit code 與數量摘要，**失敗明細一律不進 log**（log 誰都看得到，印出來等於把隱藏測試變成可反覆查詢的 oracle）；完整明細加密成 artifact。子行程拿不到權杖與 CI 的 token | GitHub Actions，由 `verify-hidden-tests.yml` 啟動 |
| `scripts/read-ci-report.py` | 用權杖解開 CI 產生的加密明細。權杖不對、或明細被動過，都會被拒絕而不是給一份假的報告 | verifier-reviewer 驗收時 |
| `scripts/hidden_vault.py` | 上面幾支共用的封存庫邏輯（加密、manifest、路徑規則、CI 密文包、零測試偵測） | 被 import，不直接執行 |
| `scripts/harness_config.py` | 讀 `harness.config.json`：這個專案的測試路徑慣例與**版本號來源**（非 Python 專案一定要設） | 被 import，不直接執行 |
| `scripts/version.py` | **產出專案的版本號**：讀寫 semver，支援純文字 / `package.json` / `pyproject.toml` 三種來源，寫回去不破壞檔案其他內容。`mirrors` 讓 README / 程式 / manifest 上的版本一起更新、一起驗一致。升版時把原始碼另存成 `releases/<版本>/`。只有 Orchestrator 能用，implementer 對版本檔與歸檔的寫入會被 guard 擋下（讀不擋） | 拆解任務前確認版本、一批需求驗收完成後升版 |
| `scripts/release_archive.py` | 版本歸檔本體：每個版本各開一個資料夾存放完整原始碼。**隱藏測試暫存區一律排除**（進去等於從歸檔洩題），歸檔目錄自己也排除以免遞迴 | 被 `version.py` 呼叫，不直接執行 |
| `scripts/guard-hidden-tests.py` | **事前攔截**：PreToolUse hook，擋下對暫存區、封存庫、`.harness/`（`progress/` 除外）的存取；對已鎖定公開測試、版本檔、`releases/`，以及**封存過 task 之後**的強制力本體（`scripts/`、`.claude/`、`CLAUDE.md`、`harness.config.json`）則**只擋寫入不擋讀取**（讀它們是正當用途） | 每次工具呼叫（由 `.claude/settings.json` 掛上） |
| `scripts/lock-tests.py` | 把公開測試的**路徑 + sha256** 寫進 `.harness/locked-tests.list`；清單的 sha256 會被 seal 簽進 manifest | 由 `seal-hidden-tests.py` 自動呼叫；封存後修了公開測試要重新封存，不是只重跑這支 |
| `scripts/verify-locks.py` | **事後稽核**：重算雜湊比對，抓出「事前攔截被繞過」的竄改。`guard-selfcheck.py` 每個 session 會用已知情境**實跑**它一次，確認它自己沒被換掉 | verifier-reviewer 驗收的第一步 |
| `scripts/guard-selfcheck.py` | **自我檢查**：用已知該被擋的 payload 實跑一次；掃出「看起來是隱藏測試、卻不在受保護路徑內」的目錄；清掉上次被強制中斷留下的解密殘留；報告目前版本號與各處是否一致，以及有沒有因權杖遺失而作廢的 task | SessionStart hook |

四層各擋不同的東西（`protection.level` 可以只開第 1 層，見
[`docs/protection-levels.md`](docs/protection-levels.md)）：

1. **實體隔離**（封存）：檔案不在工作目錄裡、內容是密文 —— 這是主防線
2. **事前攔截**（hook）：Bash 指令只要**解析得出**暫存區或封存庫路徑就擋，不分動詞
   （`bash -c`、`find`、`xargs`、`tar`、`$(…)`、變數展開都涵蓋）；
   完全不寫出路徑的混淆不在這一層的範圍內，那由第 1 層擋
3. **事後稽核**（雜湊）：不依賴攔截是否成功，被改過就查得出來
4. **自我檢查**（SessionStart）：機制壞掉時至少會有訊號，而不是默默全開

**CI 驗收不是第 5 層，它是另一條軸。** 上面四層都在問「怎麼在這台機器上
讓繞過變難」，而它們共同的上限是：繞過的人跟驗收跑在同一個 OS 使用者底下。
把驗收搬到 GitHub Actions 之後那個前提就不成立了——workflow 與密文包取自
預設分支，implementer 在自己分支上能改的東西一件都不參與驗收。
它自己的新信任根（分支保護、GitHub 的 secret）與殘留限制寫在
[`docs/ci-verification.md`](docs/ci-verification.md)。

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
| `scripts/design_tokens.py` / `scripts/check-design-tokens.py` | 前端／GUI 的設計基準：驗 token 結構，並對明暗兩套逐組算 WCAG AA 對比度。設計好不好看驗不了，但「讀不讀得到」算得出來 | 前端 task 驗收時、改過任何顏色之後 |

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
  && python3 scripts/test-attempts.py && python3 scripts/test-version.py \
  && python3 scripts/test-design.py && python3 scripts/test-ci-verify.py \
  && python3 scripts/test-protection-levels.py
```

這十三組（共 **452 個案例**）也會由 CI 在 **Linux / macOS / Windows × Python 3.9 / 3.13**
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

這個模板做過四輪對抗式審視，每一輪都問「上一輪的保證靠什麼成立」。四份完整報告
（約 114 KB，只給人看）**已移出主線**——留在 `docs/` 裡只會讓每個 clone 的人多一份
不會讀的推理過程，還會變成一份逐漸跟程式碼脫節的活文件（這正是 `docs/token-strategy.md`
§1 那條規則的最終應用）。200 字的決策摘要與「完整報告怎麼從 git 歷史取回」在
`docs/history/README.md`。

第四輪的結論是**這條軸線該停了**：第三輪自己記下的那個循環（「換掉檢查自己那一份」
就沒有人會執行那段檢查）不是還沒修好的一項，是自我檢查的結構性終點。
所以這一輪不加第五層，改把驗收搬到 GitHub Actions
（[`docs/ci-verification.md`](docs/ci-verification.md)），
並把「四層全開或整套不用」拆成可選的保護等級
（[`docs/protection-levels.md`](docs/protection-levels.md)）。
殘留限制與新的信任根都寫在那兩份文件裡，一樣沒有粉飾。
