# Harness 工程模板

[![CI](https://github.com/oilotaku/harness-template/actions/workflows/ci.yml/badge.svg)](https://github.com/oilotaku/harness-template/actions/workflows/ci.yml)

一個給 Claude Code（或任何支援 sub-agent 的 AI 開發流程）使用的**基本開發框架**，
用來把「一個模糊的需求」變成「多個可平行執行、互相制衡的子任務」。

## 這個模板解決什麼問題

- 避免 AI 自己既是球員又是裁判（實作者/檢驗者混在一起 → 容易「自己給自己過」）
- 避免 AI 為了讓測試通過而作弊（寫死回傳值、跳過測試、mock 掉核心邏輯）
- 避免 AI 在不知道機器實際效能、既有服務的情況下亂開一堆平行任務或撞埠號
- 避免 AI 在需求不清楚時自己腦補、發散實作

## 快速開始

1. `git clone` 這個 repo（或把 `harness-template/` 內容複製到你的專案根目錄）。
2. 執行初始化（依序跑機器效能掃描、既有服務掃描、環境指紋建立/比對三步）：
   ```bash
   python3 scripts/init.py
   ```
   （等同手動依序執行 `scripts/machine-profile.py`、`scripts/service-scan.py`、
   `scripts/env-guard.py` 這三支，合併成一個入口方便 `git pull` 後直接跑；
   結果只會印出來，不會自動幫你做任何決定。）

   三支腳本與 `init.py` 都支援 `--json`（stdout 只有 JSON），給 Orchestrator 讀：
   `python3 scripts/init.py --json`。裡面已經算好 `max_parallel_agents`
   與 `suggested_port_range`，不必再從中文散文裡自己換算。
   若之後換了機器、確認過確實是刻意更換，用
   `python3 scripts/env-guard.py --update` 把目前環境設為新的基準指紋。
3. 在 Claude Code 中打開專案，讓 `Orchestrator`（見 `.claude/agents/orchestrator.md`）
   依 `docs/task-decomposition-guide.md` 拆解你的需求。
4. **非 Python 專案**：在專案根目錄建立 `harness.config.json`，指定你的測試目錄
   與測試指令。不設定的話防作弊機制會找不到你的測試，等於完全沒有保護
   （`guard-selfcheck.py` 會在 session 開始時警告你）。範例見
   `docs/multi-language-support.md`。
5. 依照 `docs/implementer-verifier-workflow.md` 的順序執行：
   **檢驗者先寫測試 → 實作者才開始寫程式 → 檢驗者驗收**。

## 目錄導覽

| 路徑 | 用途 |
|---|---|
| `CLAUDE.md` | 全域規則（黃金法則、工作流程總覽） |
| `.claude/agents/` | 子智能體定義（實作者 3 個、檢驗者 3 個） |
| `.claude/commands/` | `/task-plan` `/task-dispatch` `/machine-check` 斜線指令 |
| `scripts/` | `init.py`（一鍵初始化）+ 機器效能、既有服務、環境指紋掃描腳本；防作弊機制本體（見下表） |
| `docs/` | 任務拆解、模型/思考分配、實作/檢驗分離、多語言支援、記憶管理、token 成本策略、**根因分析與修正流程** |
| `templates/` | task-spec 與驗收報告範本 |

## 防作弊機制的組成

| 腳本 | 角色 | 什麼時候跑 |
|---|---|---|
| `scripts/seal-hidden-tests.py` | **實體隔離**：把 `tests/hidden/` 加密搬到 repo 之外，並產生一次性執行權杖 | verifier-test-writer 寫完隱藏測試後 |
| `scripts/run-hidden-tests.py` | 隱藏測試的**唯一執行入口**，需要權杖才解得開 | verifier-reviewer 驗收時 |
| `scripts/hidden_vault.py` | 上面兩支共用的封存庫邏輯（加密、manifest、路徑規則） | 被 import，不直接執行 |
| `scripts/harness_config.py` | 讀 `harness.config.json`：這個專案的測試路徑慣例（非 Python 專案一定要設） | 被 import，不直接執行 |
| `scripts/guard-hidden-tests.py` | **事前攔截**：PreToolUse hook，擋下對 `tests/hidden/`（暫存區）、封存庫路徑、`.harness/`（`progress/` 除外）與已鎖定公開測試的存取 | 每次工具呼叫（由 `.claude/settings.json` 掛上） |
| `scripts/lock-tests.py` | 把 `tests/public/` 的**路徑 + sha256** 寫進 `.harness/locked-tests.list` | verifier-test-writer 寫完公開測試後 |
| `scripts/verify-locks.py` | **事後稽核**：重算雜湊比對，抓出「事前攔截被繞過」的竄改 | verifier-reviewer 驗收的第一步 |
| `scripts/guard-selfcheck.py` | **自我檢查**：用已知該被擋的 payload 實跑一次，確認防護這次真的生效 | SessionStart hook |
| `scripts/capacity.py` | 由 CPU/記憶體算出平行度上限、挑出沒被佔用的連接埠區間（純函式） | 被 import，不直接執行 |
| `scripts/scan_cache.py` | 掃描結果快取（`.harness/last-scan.json`），能力指紋不符就不給重用 | 被 import，不直接執行 |
| `scripts/test-guards.py` / `test-locks.py` / `test-vault.py` / `test-env-guard.py` / `test-config.py` / `test-scan-json.py` | 上述機制各自的回歸測試 | 改動機制之後 |

四層各擋不同的東西，缺一不可：

1. **實體隔離**（封存）：檔案不在工作目錄裡、內容是密文 —— 這是主防線
2. **事前攔截**（hook）：擋暫存區與封存庫路徑，讓誤觸得到明確訊息
3. **事後稽核**（雜湊）：不依賴攔截是否成功，被改過就查得出來
4. **自我檢查**（SessionStart）：機制壞掉時至少會有訊號，而不是默默全開

改動任何一支之後，請執行：

```bash
python3 scripts/test-guards.py && python3 scripts/test-locks.py \
  && python3 scripts/test-vault.py && python3 scripts/test-env-guard.py \
  && python3 scripts/test-config.py && python3 scripts/test-scan-json.py
```

這六組（共 162 個案例）也會由 CI 在 **Linux / macOS / Windows × Python 3.9 / 3.13**
六種組合上自動執行（見 `.github/workflows/ci.yml`）。這個 repo 特別需要 CI，
因為機制退化是無聲的——guard 少擋一種路徑寫法、封存腳本少刪一個檔案，
功能看起來都還正常，只有測試會發現。CI 一開就立刻抓到一個一直存在、
但從沒被發現的 bug：所有腳本在 Windows 主控台上一印中文就崩潰（見下）。

支援的 Python 版本：**3.9 以上**，且不需要任何第三方套件
（`psutil` 是選用的，裝了會讓服務掃描更準確）。

Windows 使用者不需要另外設定 `PYTHONUTF8`：所有腳本啟動時會自己把 stdout/stderr
切成 UTF-8（見 `scripts/utf8_output.py`），否則系統 ANSI 代碼頁編不出繁體中文，
腳本會直接崩潰。

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
