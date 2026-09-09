---
description: 依已核准的任務計畫，依序派工給檢驗者與實作者子智能體
---

依已核准的任務計畫（`task-spec` 清單），對每個 task 執行以下派工順序，
不可跳過或調換順序：

1. 指派 `verifier-test-writer`：依該 task 的 task-spec 撰寫公開測試與隱藏測試，
   公開測試鎖定後才能進到下一步
2. 指派對應的實作者（`implementer-backend` / `implementer-frontend` /
   `implementer-generic`，依 task-spec 的 `language`/`framework`/`layer` 欄位決定）：
   只交付 task-spec + 公開測試，不交付隱藏測試
3. 指派 `verifier-reviewer`（牽涉安全性時再加 `verifier-security`）：
   跑隱藏測試、審查、產出驗收報告
4. 若驗收不通過：把驗收報告中的修正要求交回對應實作者，回到步驟 2（不可回到步驟 1
   重寫測試，除非 verifier-reviewer 判定是測試設計本身有誤）
5. 全部 task 完成後，彙整所有驗收報告，產出整體專案完成度摘要（繁體中文）
