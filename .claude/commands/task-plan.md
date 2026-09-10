---
description: 依使用者需求，由 Orchestrator 執行完整的釐清→掃描→拆解→指派流程
---

請以 `orchestrator` 子智能體的身份，**依 `.claude/agents/orchestrator.md` 的執行順序**
處理以下需求，最後輸出一份任務計畫表給使用者確認，確認之前不要開始派工：

$ARGUMENTS

這份指令檔刻意不重抄步驟清單（第二輪 P2-8）：清單只有一份，在 agent 定義檔裡。
之前這裡抄了一份五步版本，agent 定義檔已經長到十一步（多了估時、決定 skill、
進度檢查點、停損判斷），而斜線指令是使用者最常走的入口——它一落後，大部分執行就會
少做那幾步。`scripts/test-guards.py` 有一個漂移測試守住這件事：本檔不得再出現編號步驟。
