---
description: 依使用者需求，由 Orchestrator 執行完整的釐清→掃描→拆解→指派流程
---

請以 `orchestrator` 子智能體的身份，依 `CLAUDE.md` 定義的順序處理以下需求：

$ARGUMENTS

執行順序：
1. 需求釐清（不清楚就發問，不要假設）
2. 執行 `scripts/machine-profile.py`、`scripts/service-scan.py`、`scripts/env-guard.py`
3. 依 `docs/task-decomposition-guide.md` 拆解成多個 task，
   每個 task 用 `templates/task-spec-template.md` 填寫
4. 依 `docs/model-thinking-matrix.md` 標註每個 task 要用的模型與思考層級
5. 列出派工順序（一律檢驗者先於實作者），輸出成一份任務計畫表給使用者確認後再開始執行
