# 範例：走一次完整的實作者/檢驗者分離流程

這個目錄不是正式任務，是一組**填好的範例**，示範 `task-spec-template.md` →
`verifier-test-writer` 產出的公開/隱藏測試 → `implementer-*` 的實作 →
`verifier-reviewer` 產出的 `verification-report-template.md`，實際走一遍會長什麼樣子。

在補齊這套模板的過程中發現，整個 repo 一直沒有任何真正跑過一輪流程的痕跡
（所有範本都是空白骨架），這組範例就是補上「至少示範過一次」的證據，
之後也可以當新手理解流程的教材。**不要把這個目錄當成正式的 `tests/` 目錄使用**
——它獨立於 `tests/public/`、`tests/hidden/`，不會被 `guard-hidden-tests.py`
或 `scripts/lock-tests.py` 處理到。

## 目錄結構

- `task-spec.md`：填好的任務規格（對應 `templates/task-spec-template.md`）
- `tests/public/test_fizzbuzz.py`：verifier-test-writer 產出、會交給 implementer 的公開測試
- `tests/hidden/test_fizzbuzz_hidden.py`：verifier-test-writer 產出、implementer 看不到的隱藏測試
  （刻意涵蓋公開測試沒測到的 n，用來戳破「查表寫死 1~15」這種取巧實作）

  > ⚠️ 這裡是**明文**，是為了讓讀者看得到「一份好的隱藏測試長什麼樣」。
  > 正式流程不是這樣：真正的隱藏測試寫在 `tests/hidden/` 之後必須執行
  > `scripts/seal-hidden-tests.py` 加密封存到 repo 之外，工作目錄不留明文
  > （見 `docs/implementer-verifier-workflow.md`）。這個範例目錄不受
  > `guard-hidden-tests.py` 保護，也不會被封存腳本處理到。
  > 另外這份範例用 `Path(__file__).parents[2]` 找實作，正式的隱藏測試**不可以**
  > 這樣寫——封存後測試在暫存目錄執行，要改用 cwd／PYTHONPATH／HARNESS_REPO_ROOT。
- `implementation/fizzbuzz.py`：implementer 產出的實作
- `verification-report.md`：verifier-reviewer 產出的驗收報告（對應
  `templates/verification-report-template.md`）

## 如何自己重跑一次

```bash
cd templates/examples/demo-fizzbuzz
python3 -m unittest discover -s tests/public -p 'test_*.py' -v
python3 -m unittest discover -s tests/hidden -p 'test_*.py' -v
```

兩組測試都應該全部通過（`implementation/fizzbuzz.py` 是正確實作）。
