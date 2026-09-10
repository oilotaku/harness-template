# 多語言／多框架支援指南

本模板不綁定任何單一語言，語言/框架的選擇由 **Orchestrator 依任務內容決定**，
並寫入 task-spec 的 `language` 與 `framework` 欄位，實作者依這兩個欄位切換工具鏈。

## 選型判斷順序

1. **專案已有慣例優先**：若專案已存在對應語言/框架的程式碼，一律沿用，
   不可因為個人偏好更換技術棧。
2. **需求明確指定**：使用者若明確指定語言/框架，直接採用。
3. **依任務性質判斷**（無既有慣例、使用者未指定時的預設建議）：

| 任務性質 | 常見選擇（僅供參考，非強制） |
|---|---|
| Web 後端 API | Node.js/TypeScript、Python、Go、Java |
| Web 前端 | TypeScript + React/Vue/Svelte |
| 資料處理/腳本 | Python、Bash |
| CLI 工具 | Go、Rust、Python |
| 系統/效能敏感元件 | Rust、Go、C++ |
| 行動端 | Kotlin（Android）、Swift（iOS）、或跨平台框架（依需求指定） |

## 測試路徑：一定要設定 `harness.config.json`

本模板的防作弊機制（鎖定公開測試、封存隱藏測試）預設看的是 `tests/public`
與 `tests/hidden`——那是 Python 的慣例。**其他語言不會長這樣**，
而路徑對不上時保護就完全不會生效。

> 2026-09-10 之前這兩個路徑是寫死在程式碼裡的，所以上面整份「支援多語言」的
> 說明其實只對 Python 成立，而且使用者不會收到任何錯誤訊息（見
> `docs/improvement-suggestions.md` 的 P1-4）。現在改成讀專案根目錄的
> `harness.config.json`，並且 `guard-selfcheck.py` 會在偵測到
> 「專案有測試、但都不在受保護路徑裡」時明確警告。

```json
{
  "$comment": "以 $ 開頭的欄位會被當成註解忽略",
  "public_test_paths": ["tests/public"],
  "hidden_test_paths": ["tests/hidden"],
  "hidden_test_command": "{python} -m unittest discover -s {dir} -p 'test_*.py' -v"
}
```

| 欄位 | 意義 |
|---|---|
| `public_test_paths` | 公開測試目錄（會被 `lock-tests.py` 鎖定、事後稽核） |
| `hidden_test_paths` | 隱藏測試的**暫存區**（`seal-hidden-tests.py` 從這裡封存出去） |
| `hidden_test_command` | 執行隱藏測試的指令。`{dir}` = 解密後的暫存目錄、`{repo}` = repo 根、`{python}` = 執行 runner 的直譯器 |

路徑支援 `*` 萬用字元（monorepo 用），例如 `packages/*/tests/hidden`。

### 各語言的設定範例

```json
// Node / TypeScript（vitest）
{
  "public_test_paths": ["tests/public"],
  "hidden_test_paths": ["tests/hidden"],
  "hidden_test_command": "npx vitest run --dir {dir}"
}
```

```json
// Go（Go 要求測試跟被測程式同 package，所以暫存區另外開一個目錄）
{
  "public_test_paths": ["internal/testsuite/public"],
  "hidden_test_paths": ["internal/testsuite/hidden"],
  "hidden_test_command": "go test {dir}/..."
}
```

```json
// monorepo：每個 package 各有一組
{
  "public_test_paths": ["packages/*/tests/public"],
  "hidden_test_paths": ["packages/*/tests/hidden"],
  "hidden_test_command": "npx vitest run --dir {dir}"
}
```

### 兩個容易踩到的地方

1. **設定錯誤是 fail-closed 的**：欄位名打錯、路徑寫成字串、指到 repo 之外，
   `guard-hidden-tests.py` 會擋下所有工具呼叫並說明原因。這是刻意的——
   如果打錯字只是靜靜退回預設值，等於讓一個 typo 就能關掉防護。
2. **隱藏測試要平鋪在暫存區第一層**。解密後的檔案會被放進一個暫存目錄，
   而 `unittest discover` 不會遞迴進沒有 `__init__.py` 的子目錄——放巢狀會變成
   「一個測試都沒跑到」。runner 會偵測這種情況並判定不通過（不會誤報成通過），
   但最好一開始就別踩。

## implementer 如何依欄位切換

在 task-spec 中，`language` / `framework` 欄位是實作者的**唯一依據**，
禁止實作者自行覆蓋這兩個欄位。實作者收到 task-spec 後，依語言慣例決定：

- 套件/依賴管理工具（例如 npm/pnpm、pip/poetry、cargo、go mod）
- 測試框架（verifier-test-writer 也需要依此欄位選擇對應語言的測試框架）
- Lint / 格式化規則（優先沿用專案既有設定檔）

## 混合技術棧的專案

若一個需求同時牽涉多種語言（例如 TypeScript 前端 + Python 後端），
Orchestrator 在拆解 task 時必須把它們拆成**不同的 task**，
分別指派給 `implementer-frontend`（TypeScript）與 `implementer-backend`（Python），
不可以要求同一個實作者子智能體同時處理兩種語言的 task。
