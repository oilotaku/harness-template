# 多語言／多框架支援指南

本模板不綁定任何單一語言，語言/框架的選擇由 **Orchestrator 依任務內容決定**，
並寫入 task-spec 的 `language` 與 `framework` 欄位，實作者依這兩個欄位切換工具鏈。

## 選型判斷順序

1. **專案已有慣例優先**：若專案已存在對應語言/框架的程式碼，一律沿用，
   不可因為個人偏好更換技術棧。
2. **需求明確指定**：使用者若明確指定語言/框架，直接採用。
3. **依任務性質判斷**（無既有慣例、使用者未指定時的預設建議）：

| 任務性質 | 常見選擇（僅供參考，非強制） |
|---|---|
| Web 後端 API | Node.js/TypeScript、Python、Go、Java |
| Web 前端 | TypeScript + React/Vue/Svelte |
| 資料處理/腳本 | Python、Bash |
| CLI 工具 | Go、Rust、Python |
| 系統/效能敏感元件 | Rust、Go、C++ |
| 行動端 | Kotlin（Android）、Swift（iOS）、或跨平台框架（依需求指定） |

## implementer 如何依欄位切換

在 task-spec 中，`language` / `framework` 欄位是實作者的**唯一依據**，
禁止實作者自行覆蓋這兩個欄位。實作者收到 task-spec 後，依語言慣例決定：

- 套件/依賴管理工具（例如 npm/pnpm、pip/poetry、cargo、go mod）
- 測試框架（verifier-test-writer 也需要依此欄位選擇對應語言的測試框架）
- Lint / 格式化規則（優先沿用專案既有設定檔）

## 混合技術棧的專案

若一個需求同時牽涉多種語言（例如 TypeScript 前端 + Python 後端），
Orchestrator 在拆解 task 時必須把它們拆成**不同的 task**，
分別指派給 `implementer-frontend`（TypeScript）與 `implementer-backend`（Python），
不可以要求同一個實作者子智能體同時處理兩種語言的 task。
