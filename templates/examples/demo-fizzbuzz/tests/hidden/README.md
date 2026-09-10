# 這個目錄的隱藏測試「不受保護」——刻意的

裡面的 `test_fizzbuzz_hidden.py` 是**明文**，而且任何人都讀得到。這是示範用途：
讓讀者看得到「一份好的隱藏測試長什麼樣」（見上層 `README.md`）。

**正式流程不是這樣。** 真正的隱藏測試寫在受保護的暫存區（預設 `tests/hidden/`，
由 `harness.config.json` 的 `hidden_test_paths` 決定）之後，必須執行

```bash
python3 scripts/seal-hidden-tests.py --task-id <task_id>
```

加密封存到 repo 之外，工作目錄不留明文。

## 為什麼要特別寫這一份 README（P1-3）

`guard-hidden-tests.py` 只保護**設定裡列出的**路徑。這個目錄不在裡面，
所以鎖定、封存、事前攔截三樣都碰不到它。

問題不在這個範例本身，而在**它示範了一個看起來受保護、實際不受保護的目錄結構**：
使用者照這個結構套進自己的子專案（例如 monorepo 的 `packages/api/tests/hidden/`），
會以為有保護，其實沒有。

`scripts/guard-selfcheck.py` 現在會在 SessionStart 掃出這類目錄並警告。
它看到目錄裡有 README 寫明「不受保護」就不再警告——**修正動作與承認動作是同一件事**：
把這件事寫下來給下一個讀到的人看。

自己的專案要保護巢狀路徑的話，在 `harness.config.json` 這樣寫：

```json
{ "hidden_test_paths": ["tests/hidden", "packages/*/tests/hidden"] }
```

詳見 `docs/multi-language-support.md`。
