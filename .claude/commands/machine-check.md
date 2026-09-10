---
description: 單獨執行機器效能、既有服務、環境指紋掃描，不觸發任務拆解
---

依序執行並完整輸出結果（繁體中文摘要在最後）：

1. `python3 scripts/machine-profile.py`
2. `python3 scripts/service-scan.py`
3. `python3 scripts/env-guard.py`

若 `env-guard.py` 回報指紋不符，停在這裡並詢問使用者是否要：
(a) 將目前環境設為新的預期指紋 —— 使用者確認後執行
    `python3 scripts/env-guard.py --update`（不要手動改 `.harness/` 底下的檔案，
    那個目錄被 guard hook 擋著，`--update` 才是正當管道），或
(b) 提供這其實是哪一台機器的相關資訊（用途、資源限制、是否有其他人在用）。

注意：容器／K8s／Codespaces／CI 這類環境的主機名稱每次重建都不同，
`env-guard.py` 已經不會把它當成環境變更（見 `scripts/env_fingerprint.py`）。
所以現在如果它回報不符，代表**真的有東西變了**（作業系統、架構、
是否容器、CPU 或記憶體級距），不要當成慣例雜訊跳過。
