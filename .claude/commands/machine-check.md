---
description: 單獨執行機器效能、既有服務、環境指紋掃描，不觸發任務拆解
---

依序執行並完整輸出結果（繁體中文摘要在最後）：

1. `python3 scripts/machine-profile.py`
2. `python3 scripts/service-scan.py`
3. `python3 scripts/env-guard.py`

若 `env-guard.py` 回報指紋不符，停在這裡並詢問使用者是否要：
(a) 將目前環境設為新的預期指紋，或
(b) 提供這其實是哪一台機器的相關資訊（用途、資源限制、是否有其他人在用）。
