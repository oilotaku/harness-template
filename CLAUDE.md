# Harness 工程模板 — 主控框架 (CLAUDE.md)

> 本檔案是整個工程模板的「憲法」。所有子智能體 (sub-agent)、指令 (command)、
> 腳本 (script) 都必須遵守本檔案定義的規則。語言一律使用**繁體中文**。

參考來源：[JAYcodr/claude-code-ultimate-guide-zh](https://github.com/JAYcodr/claude-code-ultimate-guide-zh)
（本模板套用其中「智能體團隊 (Agent Teams)」「威脅建模」「SE-CoVe 獨立驗證鏈」等章節的精神，
並依照使用者的 14 項需求重新設計。）

---

## 0. 黃金法則（不可違反）

1. **實作者絕不能修改或看到「隱藏驗收測試」**，只能看到任務規格 (task-spec) 與公開測試。
2. **檢驗者必須先寫測試，才可以讓實作者開始實作**（防止先射箭再畫靶）。
3. **實作者與檢驗者是不同的子智能體 session，禁止共用上下文**，避免互相污染判斷。
4. **實作者嚴禁超出任務規格範圍發揮**（禁止幻覺出未要求的功能、禁止「順手重構」不相關程式碼）。
5. **任何任務開始前，主導智能體 (Orchestrator) 必須先執行機器效能與既有服務掃描**，
   才能決定平行子智能體數量與可用的連接埠/服務。
6. **偵測到目前機器可能不是預期的執行環境（例如 hostname、OS 指紋不符）時，
   必須停下來向使用者確認，不可自行假設。**
7. 需求不明確時，**優先提出釐清問題**，而不是自行腦補。

---

## 1. 整體工作流程

```
使用者需求
   │
   ▼
[Orchestrator 主導智能體]
   ├─ 1. 需求釐清（不明確就發問，見 §7）
   ├─ 2. 執行 scripts/machine-profile.py + scripts/service-scan.py
   ├─ 3. 執行 scripts/env-guard.py（確認機器指紋，見 docs 說明）
   ├─ 4. 任務拆解（依 docs/task-decomposition-guide.md）
   ├─ 5. 依 docs/model-thinking-matrix.md 指派模型/思考層級
   └─ 6. 依「檢驗者先行」原則派工
          │
          ▼
   [檢驗者 verifier-test-writer]  ──先──►  依 task-spec 撰寫測試（含隱藏測試）
          │
          ▼
   [實作者 implementer-*]        ──後──►  只依 task-spec + 公開測試實作
          │
          ▼
   [檢驗者 verifier-reviewer]     ──驗收──►  比對隱藏測試 + 檢查作弊模式
          │
          ▼
   [Orchestrator] 彙整結果、產出驗收報告 (templates/verification-report-template.md)
```

> 需求釐清（步驟 1）與任務拆解（步驟 4）之前，Orchestrator 應先查閱既有記憶；
> 彙整（步驟 6 之後）若有值得留給下次的判斷依據，應寫入記憶。
> 詳細規則見 `docs/memory-management.md`（§5. 記憶管理）。

---

## 2. 子智能體分類（必須二分，禁止混合職責）

| 分類 | 定義檔位置 | 職責 |
|---|---|---|
| **實作者 (Implementer)** | `.claude/agents/implementer-*.md` | 只做「照著規格寫程式碼」這件事 |
| **檢驗者 (Verifier)** | `.claude/agents/verifier-*.md` | 只做「定義驗收標準、寫測試、審查、抓作弊」這件事 |

> 兩者**不可以是同一個 sub-agent**，也不可以在同一個 session 裡先後扮演兩種角色。
> 若專案語言/框架改變，實作者可以替換（見 `implementer-generic.md`），
> 但檢驗者的「先寫測試、找作弊模式」流程不可省略。

---

## 3. 多語言／多框架支援原則

- 本模板本身**不綁定任何特定語言**。
- Orchestrator 在任務拆解時，依需求判斷語言/框架，並在 task-spec 中明確寫出
  （例如：`language: TypeScript`, `framework: Next.js`）。
- 實作者子智能體讀取 task-spec 裡的 `language` / `framework` 欄位來決定用什麼工具鏈，
  細節見 `docs/multi-language-support.md`。

---

## 4. 目錄結構

```
harness-template/
├── CLAUDE.md                     ← 本檔案
├── README.md                     ← 使用說明
├── .claude/
│   ├── agents/                   ← 子智能體定義（實作者 / 檢驗者）
│   ├── commands/                 ← 斜線指令
│   └── settings.json             ← 權限與 hook 設定範例
├── scripts/                      ← 機器效能 / 服務掃描 / 環境守門腳本
├── docs/                         ← 方法論文件
└── templates/                    ← task-spec 與驗收報告範本
```

---

## 5. 記憶管理（Memory）

- Claude Code 有一套跨 session 的持久記憶機制（存在 repo 之外），
  跟 `.harness/`（repo 內、每台機器/每次任務的當下狀態）性質不同，
  用途是讓 Orchestrator 下次拆解同一個專案的任務時不用從零問起。
- **只有 Orchestrator 可以寫記憶**；verifier 發現值得記錄的事要回報
  Orchestrator 由它決定，implementer 完全不碰記憶。
- 什麼時候該存、該存哪一種、什麼時候不該存，完整規則見
  `docs/memory-management.md`，不在這裡重複。
