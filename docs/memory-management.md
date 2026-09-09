# 記憶管理（Memory）

## 1. 這是什麼、跟 `.harness/` 有什麼不同

Claude Code 本身有一套跨 session 的持久記憶機制：以「目前工作目錄（也就是這個
被套用 harness 的專案）」為單位，把學到的事情寫成檔案，存在 repo **之外**
（不會被 git 追蹤、不會被複製到別的專案），下次在同一個專案目錄開新的
Claude Code session 時會自動載入。

這跟 `.harness/`（`env-fingerprint.json`、`locked-tests.list`）性質完全不同：

| | `.harness/`（repo 內，已 `.gitignore` 排除） | Claude Code 記憶（repo 外） |
|---|---|---|
| 生命週期 | 每台機器/每次任務可能改變，是「當下狀態」 | 跨多次任務、跨多個 session 累積的「學到的事」 |
| 內容 | 環境指紋、已鎖定的公開測試清單 | 使用者背景、專案脈絡、Orchestrator 拆解任務時吃過的虧 |
| 寫入者 | `scripts/env-guard.py`、`scripts/lock-tests.py` | 只有 Orchestrator（見 §2） |
| 用途 | 技術強制力（hook 讀它來擋動作） | 決策參考，不具強制力，只是讓下次判斷更好 |

## 2. 誰可以寫記憶

**只有 Orchestrator 可以寫記憶。** 理由跟黃金法則第 2、3 條的精神一致：
`verifier-*` 系列 agent 除了 `verifier-test-writer`（例外用途是寫測試檔，
不是寫記憶）之外都沒有 `Write` 工具，維持「檢驗者只讀不寫治理性資訊」的原則。
verifier 若在驗收過程中發現值得記錄的事（例如同一種作弊模式反覆出現），
**回報給 Orchestrator，由 Orchestrator 決定是否寫入記憶**，不可自己動手寫。

implementer 完全不碰記憶——它不該知道「這個專案過去發生過什麼」以外的事，
避免記憶內容變相洩漏隱藏測試相關線索，或干擾它「只依 task-spec 實作」的原則
（黃金法則第 1、4 條）。

## 3. 什麼時候該存、存哪一種

| 類型 | 什麼情況下存 | 範例 |
|---|---|---|
| `project` | §7 需求釐清（黃金法則第 7 條）過程中，使用者透露了會影響任務拆解的專案脈絡 | 「這個功能有截止日期壓力」「這個 repo 之後還會有其他人接手」 |
| `feedback` | 使用者對某次拆解結果、模型指派、或某個 task 的處理方式明確糾正或肯定 | 「這類任務應該用 opus 不是 sonnet」「不要把 UI 跟 API 拆在同一個 task」 |
| `feedback` | 同一種 blocked 根因（見 `docs/task-decomposition-guide.md` §4）在不同 task 反覆出現 ≥2 次 | 「這個專案的 XX 模組 task-spec 常常沒寫清楚錯誤處理邊界，之後拆這類 task 要多問一輪」 |
| `feedback` | `verifier-reviewer` 回報同一種作弊/取巧模式在多個 task 反覆被抓到 | 「這個專案的 implementer session 常常用 mock 掉核心邏輯的方式取巧，之後 verifier-test-writer 寫測試時要更提防」 |
| `user` | 需求釐清時了解到使用者的背景、對這個專案的角色 | 「使用者不熟悉這個語言，拆解時說明要更詳細」 |
| `reference` | 使用者提到外部系統（issue tracker、CI dashboard、規格文件位置） | 「這個專案的 bug 回報在某個 Linear 專案」 |

## 4. 什麼時候不該存

- **不要**把 task-spec、驗收報告的內容複製進記憶——記憶只該存「文件裡沒寫、
  但下次拆解還用得到的判斷依據」，不是任務產出本身的備份。
- **不要**把 `.harness/` 底下的內容存進記憶（見 §1 對照表）——性質不同，
  重複存等於讓同一份資訊分裂成兩個可能不同步的來源。
- **不要**存單次任務結束就沒用的暫時性細節（例如「這次跑在 8090 port」這種
  純粹當下的執行細節，`scripts/service-scan.py` 本身就會重新偵測）。
- implementer 與除 `verifier-test-writer` 外的 verifier 不可寫記憶（見 §2）。

## 5. 什麼時候該讀

Orchestrator 在**需求釐清**與**任務拆解**這兩步（見 `CLAUDE.md` 整體工作流程
第 1、4 步）開始之前，應先確認記憶庫裡有沒有跟這次需求相關的既有記錄，
納入判斷，不要每次都從零開始問一輪使用者已經講過的事。若記憶內容與目前
觀察到的事實矛盾（例如記得的技術棧慣例已經被換掉），以目前實際狀態為準，
並更新/移除過期的記憶。

## 備註：這是 Claude Code 平台功能，不是本模板自己實作的機制

跟 `.harness/` 的掃描腳本、hook 不同，記憶的儲存/載入是 Claude Code 本身
提供的能力，本模板不需要（也不能）用程式碼去實作它，只需要在**規則層面**
規範「Orchestrator 什麼時候該用它」。這也是為什麼記憶管理只出現在文件裡，
沒有對應的 `scripts/*.py`。
