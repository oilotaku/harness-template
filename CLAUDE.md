# Harness 工程模板 — 主控框架 (CLAUDE.md)

> 本檔案是整個工程模板的「憲法」。所有子智能體 (sub-agent)、指令 (command)、
> 腳本 (script) 都必須遵守本檔案定義的規則。語言一律使用**繁體中文**。

參考來源：[JAYcodr/claude-code-ultimate-guide-zh](https://github.com/JAYcodr/claude-code-ultimate-guide-zh)
（本模板套用其中「智能體團隊 (Agent Teams)」「威脅建模」「SE-CoVe 獨立驗證鏈」等章節的精神，
並依照使用者的 14 項需求重新設計。）

---

## 0. 黃金法則（不可違反）

1. **實作者絕不能修改或看到「隱藏驗收測試」**，只能看到任務規格 (task-spec) 與公開測試。
   這條規則不是靠自律，而是靠機制落實：隱藏測試寫完後會被**加密封存到 repo 之外**
   （`scripts/seal-hidden-tests.py`），解密執行需要只交給檢驗者的權杖。
   細節見 `docs/implementer-verifier-workflow.md`。
2. **檢驗者必須先寫測試，才可以讓實作者開始實作**（防止先射箭再畫靶）。
3. **實作者與檢驗者是不同的子智能體 session，禁止共用上下文**，避免互相污染判斷。
4. **實作者嚴禁超出任務規格範圍發揮**（禁止幻覺出未要求的功能、禁止「順手重構」不相關程式碼）。
5. **任何任務開始前，主導智能體 (Orchestrator) 必須先執行機器效能與既有服務掃描**，
   才能決定平行子智能體數量與可用的連接埠/服務。
   （同一個 session、且 `env-guard.py` 判定指紋相符時，可重用上一輪的掃描結果，
   不必每個 task 重掃；跨 session 或指紋不符一律重掃。見 `docs/token-strategy.md`。）
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
          │                                   → 鎖定公開測試、封存隱藏測試
          │                                   → 交回一次性「執行權杖」
          ▼
   [實作者 implementer-*]        ──後──►  只依 task-spec + 公開測試實作
          │
          ▼
   [檢驗者 verifier-reviewer]     ──驗收──►  稽核鎖定 + 用權杖執行隱藏測試
          │                                   + 檢查作弊模式
          │
          ▼
   [Orchestrator] 彙整結果、產出驗收報告 (templates/verification-report-template.md)
```

> 驗收不通過、測試變紅、CI 掛掉、防護機制誤擋或漏擋時，一律走
> `docs/root-cause-and-fix.md` 的步驟（先重現、先定位、先讓測試變紅，才修）。
> 「一路試到綠燈為止」不是修正。

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
├── harness.config.json           ← （選用）專案自己的測試路徑慣例；非 Python 專案要設
├── .github/workflows/ci.yml      ← CI：三平台 × 兩個 Python 版本跑全部回歸測試
├── scripts/                      ← 機器效能 / 服務掃描 / 環境守門腳本
│                                    + 防作弊機制本體（封存、鎖定、稽核、hook）
├── tests/                        ← 預設的測試位置，可由 harness.config.json 覆寫
│   ├── public/                   ← 公開測試（交給實作者，鎖定後不可修改）
│   └── hidden/                   ← 隱藏測試的**暫存區**；封存後這裡是空的
├── docs/                         ← 方法論文件（含 token-strategy.md：成本策略）
└── templates/                    ← task-spec 與驗收報告範本（含 examples/ 範例）
```

> `.harness/`（環境指紋、鎖定清單、隱藏測試 manifest、進度檢查點）與封存庫（repo 之外）
> 都不會進版控，前者已在 `.gitignore`，後者根本不在 repo 裡。

---

## 5. Token 成本

本模板一個 task 的固定開銷約 5.6 萬字元（3 個不共用上下文的 session
各自重載 `CLAUDE.md` 與自己的定義檔）。這是「獨立驗證鏈」的必要代價，
不是浪費——但知道錢花在哪，才知道該省哪裡、以及**哪些絕對不能省**。

三條最高 CP 值的原則：

1. **task-spec 寫自足**：範圍邊界沒寫清楚，implementer 就得自己搜尋程式碼，
   那是全流程最不可控的成本。
2. **模型先小後大**：低風險任務先用中小模型跑第一輪驗收，出現可疑訊號才升級；
   L3/L4 不適用。
3. **`CLAUDE.md` 只寫規則與指向**：它的乘數最高，細節一律放 `docs/`。
4. **訂閱制方案（例如 Pro）要多顧一件事：消耗速率**。撞到用量上限的代價是複利的
   （等待 → session 斷掉 → 重載固定開銷 → 更快撞下一次），所以在這類方案下
   **預設序列執行**（`machine-profile.py` 給的是機器容量上限，不是建議值），
   每個 task 做完就 commit，並把跨 session 需要的進度寫進
   `.harness/progress/<task_id>.md`，讓恢復成本是「讀一個小檔案」。

完整的量測數據、訂閱制下的速率與可恢復性策略、不該省的清單、
以及「平行度不省 token」這個常見誤解，見 `docs/token-strategy.md`。

---

## 6. 記憶管理（Memory）

- Claude Code 有一套跨 session 的持久記憶機制（存在 repo 之外），
  跟 `.harness/`（repo 內、每台機器/每次任務的當下狀態）性質不同，
  用途是讓 Orchestrator 下次拆解同一個專案的任務時不用從零問起。
- **只有 Orchestrator 可以寫記憶**；verifier 發現值得記錄的事要回報
  Orchestrator 由它決定，implementer 完全不碰記憶。
- 什麼時候該存、該存哪一種、什麼時候不該存，完整規則見
  `docs/memory-management.md`，不在這裡重複。
