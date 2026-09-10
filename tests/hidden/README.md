# tests/hidden/ — 隱藏測試的「暫存區」，不是存放區

這個目錄只是 `verifier-test-writer` 寫隱藏測試的**工作檯面**。
寫完之後必須執行封存，把檔案搬出去：

```bash
python3 scripts/seal-hidden-tests.py --task-id <task_id>
```

封存後這個目錄會是空的——隱藏測試被加密搬到 repo 之外的封存庫，
只有拿著執行權杖的 `verifier-reviewer` 能透過
`scripts/run-hidden-tests.py` 解密執行。

## 為什麼要搬出去

留在這裡的話，唯一的防線是 `guard-hidden-tests.py` 逐一列舉路徑寫法去攔截，
而 implementer 手上有 Bash——可以用絕對路徑、`bash -c`、變數展開，
甚至自己寫一支腳本去讀。攻擊面是無限的，防守面是列舉的，這場仗結構上贏不了。

搬出去之後：

1. Claude Code 的 Read/Edit/Write/Grep/Glob 本來就以專案目錄為界 → 碰不到
2. 內容是密文 → 就算用腳本讀到檔案，拿到的也不是題目
3. 解密要權杖，權杖只在派工 `verifier-reviewer` 時交出去 → 也不能拿來當 oracle

細節見 `scripts/hidden_vault.py` 的模組說明與 `docs/improvement-suggestions.md` 的 P1-1。

## 寫隱藏測試時的注意事項

測試執行時**不在這個目錄底下**，而是在一個隨機命名的暫存目錄，
所以**不可以**用 `Path(__file__).resolve().parents[n]` 這種相對位置去找實作程式碼。

`run-hidden-tests.py` 保證：

- 工作目錄（cwd）是 repo 根目錄
- `PYTHONPATH` 含 repo 根目錄
- 環境變數 `HARNESS_REPO_ROOT` 指向 repo 根目錄

要找實作就用這三者其中之一，例如直接 `from src.foo import bar`。

另外**請平鋪在這個目錄的第一層**：解密後 `unittest discover` 不會遞迴進沒有
`__init__.py` 的子目錄，放巢狀會變成「一個測試都沒跑到」。

## 這個目錄的位置可以改

`tests/hidden` 只是預設值。非 Python 專案請在專案根目錄的 `harness.config.json`
用 `hidden_test_paths` 指定自己的慣例（見 `docs/multi-language-support.md`）。
