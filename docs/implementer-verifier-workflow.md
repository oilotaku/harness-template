# 實作者 / 檢驗者分離工作流程（防作弊設計）

## 為什麼要嚴格分離

如果同一個智能體（或同一個 session）既寫程式又寫測試，很容易出現：
- 針對自己寫的程式碼「量身訂做」測試，測試變得毫無鑑別力
- 遇到不確定的地方，選擇「讓測試好過」而不是「讓實作正確」
- 沒有人真的用懷疑的角度去檢查有沒有取巧

因此本模板規定：**實作者與檢驗者必須是不同的 sub-agent session，
且順序上檢驗者一定先於實作者**。

## 完整順序

```
1. Orchestrator 產出 task-spec
        │
        ▼
2. verifier-test-writer 讀 task-spec
   → 產出 tests/public/*（會交給實作者）
   → 產出隱藏測試到暫存區（預設 tests/hidden/），然後執行 seal-hidden-tests.py：
     一個指令做完「鎖定公開測試（路徑 + sha256 寫入 .harness/locked-tests.list）
     → 逐檔加密搬到 repo 之外 → 用權杖簽章 manifest」，並拿到一次性「執行權杖」
   → 立刻執行 run-hidden-tests.py --baseline：證明隱藏測試在沒有實作時是紅的
     （結果寫進簽過章的 manifest，不計入停損次數）
   → 產出「驗收標準對照表」
   → 把權杖交回 Orchestrator（只有 verifier-reviewer 會拿到）
        │
        ▼
3. implementer-* 讀 task-spec + tests/public/*
   → 只能新增/修改功能程式碼，不能碰 tests/
   → 完成後產出「實作說明」+ 自我聲明「未修改任何測試檔案」
        │
        ▼
4. verifier-reviewer 讀 task-spec + 實作者變更 + tests/public
   → 先跑 guard-selfcheck.py --strict 確認防護還活著，再跑 verify-locks.py 稽核公開測試
   → 用 run-hidden-tests.py + 權杖執行隱藏測試（讀不到原始碼，只拿得到結果）：
     runner 先驗 manifest 簽章、再比對鎖定清單的 sha256，都過了才解密執行
   → 對照「驗收標準對照表」逐條確認
   → 檢查作弊模式清單（見 verifier-reviewer.md）
   → 產出 verification-report
        │
        ├─ 通過 → Orchestrator 標記 task 完成
        └─ 不通過 → 退回 implementer-*（帶著具體修正要求），
                     回到步驟 3，**不重新執行步驟 2**
                     （除非 verifier-reviewer 明確判定是測試本身設計錯誤）
```

## 開了 CI 驗收之後，這張圖哪裡不一樣

`harness.config.json` 的 `protection.ci_verification` 設成 `true` 時，
步驟 2 與步驟 4 各多一段：

- **步驟 2 之後**：封存會順手把密文包匯出到 `ci/sealed/<task_id>/`。
  在「密文包 commit 進**預設分支**」與「權杖存進 repository secret」這兩件事
  做完之前，**不要進入步驟 3**——CI 上的驗收會因為「沒東西可驗」而回傳成功，
  那跟「全部通過」在 check 狀態上長得一模一樣。
- **步驟 4**：正式驗收是 PR 上那個 check，不是 verifier-reviewer 在本機跑的那一跑。
  本機那一跑降為「第二意見」；兩邊不一致要當成可疑訊號記進報告。
  失敗明細在加密的 artifact 裡，用 `scripts/read-ci-report.py` 加權杖解開，
  **不可以**原樣貼進驗收報告。

為什麼要多這一段：步驟 4 的所有保護在本機都有同一個上限——驗收與被驗收跑在
同一個 OS 使用者底下。CI 上那一跑沒有這個前提。完整說明與它自己的信任根見
[`ci-verification.md`](ci-verification.md)。

## 「先寫測試防止作弊」的具體機制

1. **時間序保證**：測試先於實作存在，實作者無法回頭修改測試來遷就自己的程式碼。
2. **資訊不對稱保證**：隱藏測試對實作者不可見，實作者無法針對已知測試案例作弊，
   只能老實依 task-spec 的文字敘述去實作正確邏輯。這不只是流程約定，
   而且不是靠「攔截」達成的——`seal-hidden-tests.py` 會把隱藏測試**加密搬到
   repo 之外**（見 `scripts/hidden_vault.py`）：

   - Claude Code 的 Read/Edit/Write/Grep/Glob 本來就以專案目錄為界 → 碰不到；
     連「不指定 path 的全 repo 搜尋」也搜不到
   - 內容是密文，而且**每個檔案各自一把金鑰**（第二輪 P1-5 之前所有檔案共用一條
     keystream，拿公開測試的檔頭當 crib 就能還原隱藏測試開頭）
   - 解密要權杖，權杖只交給 verifier-reviewer → 也不能拿來當「反覆查詢的 oracle」
   - runner 信任的每個 manifest 欄位都用權杖簽過章（第二輪 P0-6 之前把 `test_command`
     換成假腳本就能讓 verifier 拿到「全部通過」）

   `guard-hidden-tests.py` 仍然保護暫存區與封存庫路徑，
   但它的角色已經從「唯一防線」降級成「縱深防禦的其中一層」。
3. **權限保證**：透過 `.claude/settings.json` 的 hook（`guard-hidden-tests.py`）
   在工具層級直接擋下對測試檔案的寫入與讀取，不只是靠「口頭約定」。此 hook 掛在
   `Edit|Write|Bash|Read|Grep|Glob`——只擋 Edit/Write 不夠，implementer 仍握有
   Bash 工具，因此 Bash 指令會先切成多個簡單指令、追蹤 `cd` 造成的虛擬工作目錄後
   再逐一比對；對暫存區與封存庫是「指令裡出現路徑就擋、不分動詞」，對已鎖定的
   公開測試則只擋明確的寫入動詞（`rm`/`sed -i`/`perl -pi`/`git checkout|restore|…`），
   因為 implementer 有正當理由**讀**公開測試（細節與已知殘留限制見腳本內的模組說明）。

   **這套機制擋的是什麼、不擋什麼**（誠實版本，第二輪 P1-8、第三輪 P0-8 修訂）：
   它擋的是「順手看一眼」「習慣性搜整個 repo」「用工具的正常用法碰到」這類
   **非刻意**的洩題。它**不擋**一個「決心繞過、而且跟 verifier 共用同一個 OS
   使用者」的子智能體：權杖會經過 Claude Code 的 transcript
   （`~/.claude/projects/…jsonl`，同一個使用者讀得到），解密後的明文在測試執行
   期間短暫存在於暫存目錄。那需要獨立的使用者或容器，超出本模板範圍。

   > ⚠️ 這裡原本還寫著「並讓**刻意**的繞過留下痕跡——簽章不符、稽核不符、
   > 嘗試次數對不上」。**第三輪的實測證否了那句話**：改寫
   > `scripts/run-hidden-tests.py` 之後，用完全正確的權杖執行驗收會得到 exit 0
   > 與「隱藏測試全部通過」，權杖同時被送進被改過的程式碼的 argv，而
   > `verify-locks.py` 與 `guard-selfcheck.py --strict` 都回報正常——一個痕跡都沒有。
   >
   > 第三輪關上了那條路，而關上它的**是派工方式**：驗收用的程式碼連同隱藏測試一起
   > 封存到 `<封存庫>/_runner/`，每個檔案的 sha256 進簽章範圍，Orchestrator 給
   > `verifier-reviewer` 的是那個絕對路徑。改 repo 裡的副本不影響驗收；改封存版的
   > 相依模組會 exit 2。事前層（guard 擋強制力本體的寫入）是額外的一道，不是主防線。
   >
   > 仍然不擋：換掉封存版的 `run-hidden-tests.py` **自己**、而且把自我比對拿掉——
   > 任何自我檢查都有這個循環。第四輪把它承接到 CI 驗收，見
   > `docs/ci-verification.md`（第三輪當時的 P0-8 記錄在歷史，見 `docs/history/README.md`）。
4. **獨立驗證鏈**：`verifier-reviewer` 與 `implementer-*` 是不同 session，
   不共用上下文，驗收時是「從零重新審視」而不是延續實作者的思路
   （對應 SE-CoVe 獨立驗證鏈的精神）。
5. **事後稽核（不依賴攔截是否成功）**：上面第 3 點是「事前攔截」，而事前攔截
   有兩個先天弱點——它靠列舉路徑寫法來判斷，總會有沒想到的繞法；而且它是 hook，
   只要沒被執行到（找不到 `python3`、hook 設定被改掉、工作目錄不對），防護就等於
   不存在。因此 `lock-tests.py` 會把每個公開測試的 **sha256** 一起寫進清單，
   `verifier-reviewer` 驗收的第一步跑 `scripts/verify-locks.py` 重算比對：
   不管前面有沒有被繞過，只要公開測試在鎖定之後被動過，這裡就會發現。
   另外 `guard-hidden-tests.py` 本身是 **fail-closed** 的（腳本自己出錯或收到
   看不懂的輸入時擋下操作而不是放行），`guard-selfcheck.py` 則在每個 session
   開始時實際跑幾個「已知該被擋」的 payload，確認防護這次真的有生效。

## 邊界情況處理

- **測試本身有誤**（例如驗收標準理解錯誤）：verifier-reviewer 發現後，
  回報給 Orchestrator，由 Orchestrator 決定是否重新指派 verifier-test-writer
  修正測試——但這個決定不能由 implementer 自己主張。
- **task-spec 本身有歧義**：任何一方（implementer 或 verifier）發現都應該
  停下來回報 Orchestrator 向使用者確認，不要各自猜一個版本繼續做下去。
- **執行權杖遺失**（最常見的原因：`verifier-reviewer` 執行隱藏測試時撞到用量上限，
  那個 session 連同權杖一起沒了）：沒有救援路徑，manifest 只存指紋，密文永遠解不開。
  處理方式是**作廢重來**，不是想辦法解密：

  1. `python3 scripts/discard-sealed-task.py --task-id <id> --token-lost --confirm`
     —— 刪掉再也用不到的密文，在 manifest 留下一塊墓碑
  2. Orchestrator 帶著**同一份 task-spec** 重新派工 `verifier-test-writer` 重寫隱藏測試
     （task-spec 不需要改；要改是另一回事）
  3. 重新 `seal-hidden-tests.py` → 新權杖立刻做一次 `--baseline`
  4. **implementer 不需要重做**：作廢的是測試，不是實作

  作廢不會把歷史洗白：`discard_count` 會帶進重新封存後**簽過章**的項目，
  `attempts_recorded` 也原封不動帶過去（改小它會被 `show-attempts` 判成「有紀錄被刪」）。
  `verifier-reviewer` 要把「這個 task 曾經作廢過幾次」寫進驗收報告。

  > 墓碑本身沒有簽章——簽章金鑰由權杖推導，而權杖正是遺失的那個東西。這不構成
  > 新的攻擊面：墓碑的唯一效果是讓 runner **拒絕執行**，永遠不會產生假的「全部通過」。
  > 偽造一個墓碑換到的是「驗收無法進行」，對 implementer 沒有好處，而且
  > `guard-selfcheck.py` 每個 session 開始都會把墓碑列出來。
- **解密後的明文殘留**：runner 把隱藏測試解密到**封存庫底下**（不是 `/tmp`），
  正常結束、例外、可攔截的中止訊號都會刪掉它。行程被作業系統直接砍掉時
  （撞上限、容器被回收）刪不成，明文會留在磁碟上——下一次執行、`--sweep`、
  以及每個 session 開始的 `guard-selfcheck.py` 都會清掉並警告。
  選擇封存庫而不是 `/tmp` 的理由就是這個殘留期間：封存庫在 `guard-hidden-tests.py`
  的保護範圍內（不分動詞、指令字串裡出現就擋），`/tmp` 完全不受保護。
