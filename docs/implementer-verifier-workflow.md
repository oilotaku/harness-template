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
   → 產出 tests/hidden/*，然後執行 seal-hidden-tests.py 封存
     （加密搬到 repo 之外，工作目錄不留明文，並拿到一次性「執行權杖」）
   → 產出「驗收標準對照表」
   → 把權杖交回 Orchestrator（只有 verifier-reviewer 會拿到）
        │
        ▼（執行 `python3 scripts/lock-tests.py`，鎖定 tests/public 並把
            「路徑 + sha256」寫入 .harness/locked-tests.list——這一步是實際的
            技術強制力來源，不是單純的文件約定）
3. implementer-* 讀 task-spec + tests/public/*
   → 只能新增/修改功能程式碼，不能碰 tests/
   → 完成後產出「實作說明」+ 自我聲明「未修改任何測試檔案」
        │
        ▼
4. verifier-reviewer 讀 task-spec + 實作者變更 + tests/public
   → 先跑 verify-locks.py 稽核公開測試有沒有被竄改
   → 用 run-hidden-tests.py + 權杖執行隱藏測試（讀不到原始碼，只拿得到結果）
   → 對照「驗收標準對照表」逐條確認
   → 檢查作弊模式清單（見 verifier-reviewer.md）
   → 產出 verification-report
        │
        ├─ 通過 → Orchestrator 標記 task 完成
        └─ 不通過 → 退回 implementer-*（帶著具體修正要求），
                     回到步驟 3，**不重新執行步驟 2**
                     （除非 verifier-reviewer 明確判定是測試本身設計錯誤）
```

## 「先寫測試防止作弊」的具體機制

1. **時間序保證**：測試先於實作存在，實作者無法回頭修改測試來遷就自己的程式碼。
2. **資訊不對稱保證**：隱藏測試對實作者不可見，實作者無法針對已知測試案例作弊，
   只能老實依 task-spec 的文字敘述去實作正確邏輯。這不只是流程約定，
   而且不是靠「攔截」達成的——`seal-hidden-tests.py` 會把隱藏測試**加密搬到
   repo 之外**（見 `scripts/hidden_vault.py`）：

   - Claude Code 的 Read/Edit/Write/Grep/Glob 本來就以專案目錄為界 → 碰不到；
     連「不指定 path 的全 repo 搜尋」也搜不到
   - 內容是密文 → 就算 implementer 自己寫一支腳本去讀封存檔案，拿到的也不是題目
   - 解密要權杖，權杖只交給 verifier-reviewer → 也不能拿來當「反覆查詢的 oracle」

   `guard-hidden-tests.py` 仍然保護 `tests/hidden/`（封存前的暫存區）與封存庫路徑，
   但它的角色已經從「唯一防線」降級成「縱深防禦的其中一層」。
3. **權限保證**：透過 `.claude/settings.json` 的 hook（`guard-hidden-tests.py`）
   在工具層級直接擋下對測試檔案的寫入與讀取，不只是靠「口頭約定」。此 hook 掛在
   `Edit|Write|Bash|Read|Grep|Glob`——只擋 Edit/Write 不夠，implementer 仍握有
   Bash 工具，可以直接用 `rm`/`sed -i`/重導向、或切換工作目錄後用相對路徑等方式
   繞過單純的路徑字串比對，因此 Bash 指令會先切成多個簡單指令、追蹤 `cd` 造成的
   虛擬工作目錄後再逐一比對（細節與已知殘留限制見腳本內的模組說明）。
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
