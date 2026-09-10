# Task Spec — <task 編號與名稱>

## 基本資訊

- **task_id**：
- **layer**：（資料層 / API 層 / UI 層 / 腳本 / 其他）
- **language**：
- **framework**：
- **depends_on**：（依賴的其他 task_id，沒有則寫「無」）
- **model**：（依 docs/model-thinking-matrix.md 填寫）
- **thinking**：（low / medium / high）
- **security_review**：（true / false，是否需要 verifier-security）
- **estimate_class**：（doc / wiring / module / structural，見 docs/time-estimation.md）
- **estimated_minutes**：（由 `python3 scripts/estimate-time.py` 產出的區間，例如 15–25）
- **actual_minutes**：（驗收後由 Orchestrator 回填；不回填的話估時永遠不會變準）

## 一句話目標

> 這個 task 完成後，使用者/系統能多做到什麼事？

## 詳細需求敘述

（用繁體中文完整描述功能行為，包含正常路徑與已知的例外情況）

## 驗收標準（每條都要可被寫成測試）

1.
2.
3.

## 範圍邊界

- **可以改動的檔案/模組**：
- **絕對不能改動的檔案/模組**：
- **可以新增的依賴套件**（若有限制）：

## 禁止事項

- 不可以做的事（防止實作者過度發揮/幻覺出未要求的功能）：

## 環境限制（由 Orchestrator 依機器掃描結果填寫）

- **可用連接埠**：
- **需避開的既有服務**：
- **本 task 是否可與其他 task 平行執行**：

## 未決問題（若有不明確之處，先列在這裡並向使用者確認，不可留白繼續往下做）

-
