# Harness 工程模板

一個給 Claude Code（或任何支援 sub-agent 的 AI 開發流程）使用的**基本開發框架**，
用來把「一個模糊的需求」變成「多個可平行執行、互相制衡的子任務」。

## 這個模板解決什麼問題

- 避免 AI 自己既是球員又是裁判（實作者/檢驗者混在一起 → 容易「自己給自己過」）
- 避免 AI 為了讓測試通過而作弊（寫死回傳值、跳過測試、mock 掉核心邏輯）
- 避免 AI 在不知道機器實際效能、既有服務的情況下亂開一堆平行任務或撞埠號
- 避免 AI 在需求不清楚時自己腦補、發散實作

## 快速開始

1. 把 `harness-template/` 內容複製到你的專案根目錄。
2. 先執行環境掃描：
   ```bash
   python3 scripts/machine-profile.py
   python3 scripts/service-scan.py
   python3 scripts/env-guard.py
   ```
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
| `scripts/` | 機器效能、既有服務、環境指紋掃描腳本 |
| `docs/` | 任務拆解、模型/思考分配、實作/檢驗分離、多語言支援方法論 |
| `templates/` | task-spec 與驗收報告範本 |

## 設計依據

本模板參考 [claude-code-ultimate-guide-zh](https://github.com/JAYcodr/claude-code-ultimate-guide-zh)
中「智能體團隊 (Agent Teams)」的拓樸決策框架，以及該指南提及的
**SE-CoVe（獨立驗證鏈，Meta AI, ACL 2024）**概念，將「產生答案」與「驗證答案」
拆成兩條完全獨立的鏈路。
