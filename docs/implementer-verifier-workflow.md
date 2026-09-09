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
   → 產出 tests/hidden/*（不會交給實作者）
   → 產出「驗收標準對照表」
        │
        ▼（執行 `python3 scripts/lock-tests.py`，鎖定 tests/public 並寫入
            .harness/locked-tests.list——這一步是實際的技術強制力來源，
            不是單純的文件約定）
3. implementer-* 讀 task-spec + tests/public/*
   → 只能新增/修改功能程式碼，不能碰 tests/
   → 完成後產出「實作說明」+ 自我聲明「未修改任何測試檔案」
        │
        ▼
4. verifier-reviewer 讀 task-spec + 實作者變更 + tests/public + tests/hidden
   → 執行全部測試（含隱藏測試）
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
   只能老實依 task-spec 的文字敘述去實作正確邏輯。
3. **權限保證**：透過 `.claude/settings.json` 的 hook（`guard-hidden-tests.py`）
   在工具層級直接擋下對測試檔案的寫入，不只是靠「口頭約定」。此 hook 同時掛在
   `Edit|Write|Bash`——只擋 Edit/Write 不夠，implementer 仍握有 Bash 工具，
   可以直接用 `rm`/`sed -i`/重導向等指令繞過路徑層級保護，因此 Bash 指令也會被
   同一支腳本掃描比對。
4. **獨立驗證鏈**：`verifier-reviewer` 與 `implementer-*` 是不同 session，
   不共用上下文，驗收時是「從零重新審視」而不是延續實作者的思路
   （對應 SE-CoVe 獨立驗證鏈的精神）。

## 邊界情況處理

- **測試本身有誤**（例如驗收標準理解錯誤）：verifier-reviewer 發現後，
  回報給 Orchestrator，由 Orchestrator 決定是否重新指派 verifier-test-writer
  修正測試——但這個決定不能由 implementer 自己主張。
- **task-spec 本身有歧義**：任何一方（implementer 或 verifier）發現都應該
  停下來回報 Orchestrator 向使用者確認，不要各自猜一個版本繼續做下去。
