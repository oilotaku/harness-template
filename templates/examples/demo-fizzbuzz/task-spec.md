# Task Spec — DEMO-001 FizzBuzz 序列產生器

> 這是 `templates/task-spec-template.md` 的填寫範例，僅供示範，非正式任務。

## 基本資訊

- **task_id**：DEMO-001
- **layer**：腳本
- **language**：Python
- **framework**：無（僅限標準函式庫）
- **depends_on**：無
- **model**：sonnet
- **thinking**：low
- **security_review**：false

## 一句話目標

> 提供一個 `fizzbuzz(n)` 函式，回傳 1 到 n（含）的 FizzBuzz 字串序列。

## 詳細需求敘述

在 `implementation/fizzbuzz.py` 實作函式 `fizzbuzz(n: int) -> list[str]`：

- 回傳長度為 `n` 的字串列表，索引 `i`（從 0 起算）對應數字 `i + 1`。
- 該數字同時為 3 與 5 的倍數 → `"FizzBuzz"`。
- 只為 3 的倍數 → `"Fizz"`。
- 只為 5 的倍數 → `"Buzz"`。
- 其餘情況 → 該數字轉成字串（例如 `"7"`），不可回傳 `int`。
- `n == 0` → 回傳空 list。
- `n < 0` → 拋出 `ValueError`。

## 驗收標準（每條都要可被寫成測試）

1. `fizzbuzz(15)` 回傳的序列與標準 FizzBuzz 規則逐項相符（含第 15 項為 `"FizzBuzz"`）。
2. 序列中每一項的型別皆為 `str`（不可以是 `int`）。
3. `fizzbuzz(0)` 回傳空 list。
4. `fizzbuzz(-1)` 拋出 `ValueError`。
5. 對公開測試沒有涵蓋到的較大 `n`（例如 17、1000），規則依然正確套用
   ——用來排除「查表寫死 1~15」這種取巧實作。

## 範圍邊界

- **可以改動的檔案/模組**：`implementation/fizzbuzz.py`
- **絕對不能改動的檔案/模組**：`tests/public/*`、`tests/hidden/*`
- **可以新增的依賴套件**：不可，僅限 Python 標準函式庫

## 禁止事項

- 不可以用 `input()`、`print()` 等有副作用的呼叫。
- 不可以針對測試裡出現過的具體 `n` 值查表回傳固定結果（必須是能處理任意
  非負整數 `n` 的通用邏輯）。

## 環境限制（由 Orchestrator 依機器掃描結果填寫）

- **可用連接埠**：不適用（無網路服務）
- **需避開的既有服務**：不適用
- **本 task 是否可與其他 task 平行執行**：是

## 未決問題

- 無（此為示範用途，格式與範圍已預先確認）。
