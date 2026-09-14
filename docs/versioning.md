# 產出專案的版本號

> 這份文件講的是**模板產出的那個專案**的版本號，不是 harness-template 自己的。

## 1. 為什麼模板要管這件事

使用者回報「壞掉了」的時候，第一個要問的就是**哪一版**。沒有版本號：

- 回報無法定位——不知道是哪一版開始壞的、也不知道回報者跑的是不是最新的
- 修好之後無法交代——不知道從哪一版起沒事，使用者也不知道該不該更新
- 「這個我上週修過了」變成無法查證的一句話

而版本號如果只靠人記得更新，它一定會漂。**漂掉的版本號比沒有版本號更糟**，
因為它看起來是可信的。所以這裡把它做成機制，而不是寫一條規則請大家遵守。

## 2. 版本住哪：由專案自己宣告

版本號存在哪裡是**語言慣例**，跟測試路徑一樣不能寫死（理由見
`scripts/harness_config.py` 的模組說明：寫死路徑會讓非 Python 專案悄悄失效）。
所以在 `harness.config.json` 裡宣告：

```json
{
  "version": { "file": "VERSION", "format": "plain" }
}
```

| `format` | 讀寫對象 | 額外欄位 | 適合 |
|---|---|---|---|
| `plain` | 整個檔案就是版本字串 | — | Go / Rust / 腳本專案，或任何不想動既有設定檔的情況 |
| `json` | JSON 檔的某個鍵 | `key`（可用點號指巢狀路徑，預設 `version`） | `package.json` |
| `toml` | `version = "x.y.z"` 那一行 | `section`（限定段落，例如 `project`） | `pyproject.toml`、`Cargo.toml` |

沒有 `harness.config.json` 時就是預設的 `VERSION` 純文字檔——對任何語言都成立，
而且不需要專案先有其他設定檔。

寫回去時**不會破壞檔案的其他內容**：JSON 的其他鍵、TOML 的註解與其他段落都留著
（`scripts/test-version.py` 有回歸測試守著這件事）。

## 3. 誰可以改

**只有 Orchestrator，而且只能透過 `scripts/version.py`。**

`guard-hidden-tests.py` 會擋下 implementer 對版本檔的寫入——比照已鎖定的公開測試：
**只擋寫入、不擋讀取**。implementer 有正當理由讀版本號（例如實作一個 `--version`
旗標），但沒有正當理由改它。版本是治理資訊，不是實作的一部分。

```
Write / Edit 版本檔          → 擋
echo x > 版本檔 / rm / sed -i → 擋
Read / cat 版本檔             → 放行
python3 scripts/version.py    → 放行
```

設定改指到 `package.json` 之後，guard 會跟著保護新的路徑、不再擋舊的。

## 4. 什麼時候升、升哪一位

**時機**：一批需求（不是單一 task）全部驗收通過、commit 之後，由 Orchestrator 升一次。
逐個 task 升版會讓版本號跳得毫無意義——使用者看到的是一個發布，不是你的任務拆解。

**層級**：

| 位 | 什麼情況 | 例子 |
|---|---|---|
| MAJOR | 對外介面 breaking | API 合約改了、CLI 參數改名或移除、設定檔/資料格式不相容 |
| MINOR | 新增功能，既有用法不受影響 | 多一個端點、多一個選項、多一個支援的格式 |
| PATCH | bug 修正，沒有介面變動 | `seal-hidden-tests.py --kind bugfix` 的 task **天然落在這裡** |

判斷「算不算 breaking」看的是**使用者那一側**：既有的呼叫方不改任何東西，
還能照舊運作嗎？不能，就是 MAJOR。內部重構再大，只要外面看不出來，就不是。

```bash
python3 scripts/version.py --bump patch    # 0.4.2 -> 0.4.3
python3 scripts/version.py --bump minor    # 0.4.3 -> 0.5.0
python3 scripts/version.py --bump major    # 0.5.0 -> 1.0.0
python3 scripts/version.py --set 2.0.0     # 直接指定
```

## 5. 跟其他流程的銜接

- **每個 session 開始**：`guard-selfcheck.py` 會報告目前版本；沒有版本號時明講
  缺什麼、怎麼補。「這個專案沒在管版本」不該是無聲的。
- **任務拆解前**：`init.py --json` 的 `sections.version` 帶出目前版本，
  Orchestrator 不必另外問。
- **驗收報告**：記下這次驗收對應的版本（`templates/verification-report-template.md`），
  之後查「這個修正在哪一版」才有依據。
- **使用者回報**：回報要帶版本號，這份文件是那件事的前提。

## 6. 刻意不做的事

**不做版本號的事後雜湊稽核**（像 `verify-locks.py` 對公開測試那樣）。

理由是失效的性質不同。這個模板真正的敵人是**沉默失效**（見
`docs/root-cause-and-fix.md` §0 原則 3）：隱藏測試被看到不會有任何訊號，
所以需要事後稽核。版本號被偷改則是**吵鬧失效**——`git diff` 直接看得到，
下一次 `--bump` 從當前值算出來的結果也會不對勁。多做一層在這裡換不到對應的價值，
只會多一個要維護的東西。

**不自動從 git tag 推導版本**。tag 是發布動作的產物，而這裡要的是「原始碼裡
就寫著自己是哪一版」——使用者手上那份程式不一定是從 tag 裝的。

## 7. 常見情況

| 症狀 | 原因 | 怎麼處理 |
|---|---|---|
| session 開頭說「這個專案還沒有版本號」 | 新專案，還沒建立 | `python3 scripts/version.py --init`（建立 0.1.0） |
| `「v1.2」不是合法的 semver 版本` | 版本檔裡不是 `x.y.z` | 改成三位數字；`v` 前綴是 tag 的慣例，不是版本字串本身 |
| implementer 說它改不了版本檔 | **預期行為**，不是壞掉 | 版本由 Orchestrator 升，見 §3 |
| `version` 有無法辨識的欄位 | 設定檔打錯欄位名 | 照訊息改。這裡刻意 fail-closed——打錯字如果只是被忽略，等於「其實沒在管版本」而且沒有訊號 |
| TOML 找不到 `version = "..."` | 檔案裡本來就沒有那一行 | 先手動加一行再重試。腳本不會自己加——那會產出一份缺了其他必要欄位的殘檔 |
