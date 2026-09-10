# Harness 工程模板

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
3. 在 Claude Code 中打開專案，讓 `Orchestrator`（見 `.claude/agents/orchestrator.md`）
   依 `docs/task-decomposition-guide.md` 拆解你的需求。
4. 依照 `docs/implementer-verifier-workflow.md` 的順序執行：
   **檢驗者先寫測試 → 實作者才開始寫程式 → 檢驗者驗收**。

## 目錄導覽

| 路徑 | 用途 |
|---|---|
| `CLAUDE.md` | 全域規則（黃金法則、工作流程總覽） |
| `.claude/agents/` | 子智能體定義（實作者 3 個、檢驗者 3 個） |
| `.claude/commands/` | `/task-plan` `/task-dispatch` `/machine-check` 斜線指令 |
| `scripts/` | `init.py`（一鍵初始化）+ 機器效能、既有服務、環境指紋掃描腳本；防作弊機制本體（見下表） |
| `docs/` | 任務拆解、模型/思考分配、實作/檢驗分離、多語言支援、記憶管理方法論 |
| `templates/` | task-spec 與驗收報告範本 |

## 防作弊機制的組成

| 腳本 | 角色 | 什麼時候跑 |
|---|---|---|
| `scripts/seal-hidden-tests.py` | **實體隔離**：把 `tests/hidden/` 加密搬到 repo 之外，並產生一次性執行權杖 | verifier-test-writer 寫完隱藏測試後 |
| `scripts/run-hidden-tests.py` | 隱藏測試的**唯一執行入口**，需要權杖才解得開 | verifier-reviewer 驗收時 |
| `scripts/hidden_vault.py` | 上面兩支共用的封存庫邏輯（加密、manifest、路徑規則） | 被 import，不直接執行 |
| `scripts/guard-hidden-tests.py` | **事前攔截**：PreToolUse hook，擋下對 `tests/hidden/`（暫存區）、封存庫路徑、`.harness/` 與已鎖定公開測試的存取 | 每次工具呼叫（由 `.claude/settings.json` 掛上） |
| `scripts/lock-tests.py` | 把 `tests/public/` 的**路徑 + sha256** 寫進 `.harness/locked-tests.list` | verifier-test-writer 寫完公開測試後 |
| `scripts/verify-locks.py` | **事後稽核**：重算雜湊比對，抓出「事前攔截被繞過」的竄改 | verifier-reviewer 驗收的第一步 |
| `scripts/guard-selfcheck.py` | **自我檢查**：用已知該被擋的 payload 實跑一次，確認防護這次真的生效 | SessionStart hook |
| `scripts/test-guards.py` / `test-locks.py` / `test-vault.py` | 上述機制各自的回歸測試 | 改動機制之後 |

四層各擋不同的東西，缺一不可：

1. **實體隔離**（封存）：檔案不在工作目錄裡、內容是密文 —— 這是主防線
2. **事前攔截**（hook）：擋暫存區與封存庫路徑，讓誤觸得到明確訊息
3. **事後稽核**（雜湊）：不依賴攔截是否成功，被改過就查得出來
4. **自我檢查**（SessionStart）：機制壞掉時至少會有訊號，而不是默默全開

改動任何一支之後，請執行：

```bash
python3 scripts/test-guards.py && python3 scripts/test-locks.py && python3 scripts/test-vault.py
```

## 設計依據

本模板參考 [claude-code-ultimate-guide-zh](https://github.com/JAYcodr/claude-code-ultimate-guide-zh)
中「智能體團隊 (Agent Teams)」的拓樸決策框架，以及該指南提及的
**SE-CoVe（獨立驗證鏈，Meta AI, ACL 2024）**概念，將「產生答案」與「驗證答案」
拆成兩條完全獨立的鏈路。
