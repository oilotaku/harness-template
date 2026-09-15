<!-- 語言 / Language: **繁體中文** · [English](protection-levels.en.md) -->

# 保護等級：minimal 與 full

> 對應第四輪 P1。這份文件講的是 `harness.config.json` 的 `protection` 區塊：
> 這個專案要開幾層防護、驗收跑在哪裡。

## 1. 為什麼要有這個選項

在此之前這套機制是**全有或全無**：四層一起開，而四層對大多數專案過重——
要裝 hook、要維持「公開測試鎖定後不能改」的紀律、每個 session 會多出一段自我檢查。
於是實際發生的事情是：覺得太重的人整套不用，而不是用其中一層。

但四層裡真正不可替代的只有第 1 層。**實體隔離**（加密封存到 repo 之外 + 執行權杖
+ manifest 簽章）本身就足以讓「implementer 看不到隱藏測試」這件事成立，
而且它不需要 hook、不需要任何 Claude Code 設定——三支腳本就跑得動。

其餘三層買的是**防竄改**與**訊號**，不是隔離本身。值得，但不是每個專案都需要，
而且它們的成本是紀律成本，不是安裝成本——紀律成本是會被繞過的那一種。

## 2. 兩個等級

```json
{
  "protection": { "level": "minimal" }
}
```

| | `minimal` | `full`（預設） |
|---|---|---|
| 1. 實體隔離（封存 + 加密 + 權杖 + 簽章） | ✅ | ✅ |
| 2. 事前攔截（PreToolUse hook） | ❌ | ✅ |
| 3. 公開測試鎖定 + 事後稽核 | ❌ | ✅ |
| 4. SessionStart 自我檢查的探測 | ❌ | ✅ |
| 要改的設定 | `harness.config.json` 三行 | 再加 `.claude/settings.json` 的 hook |
| 會用到的腳本 | `seal-hidden-tests.py`、`run-hidden-tests.py`、`discard-sealed-task.py` | 全部 |

`minimal` 仍然會做的事，一項都沒有少：

- 隱藏測試封存到 repo 之外，每檔獨立金鑰加密
- 一次性執行權杖；權杖錯誤就解不開
- manifest 項目用權杖簽章，`test_command` 等欄位被改就跑不動
- 驗收用的程式碼一起封存，sha256 進簽章範圍
- 基線執行（證明隱藏測試在沒有實作時是紅的）
- 零測試假通過的偵測
- 驗收嘗試次數與停損判斷

## 3. minimal 放棄了什麼（誠實版本）

**公開測試被改不會有人抓到。** 這是最主要的那一項。第 3 層的事後稽核
（`verify-locks.py`）是靠「封存當下把公開測試的 sha256 記進簽過章的 manifest」
運作的；minimal 不鎖定，所以那個基準不存在。implementer 把一個公開測試改成
`assert True` 不會有任何訊號。

**沒有事前攔截。** 沒有 hook，所以「順手 `cat tests/hidden/...`」不會被擋——
但那個目錄在封存後是空的，所以他看到的是「沒有檔案」。真正的差別在封存**之前**：
verifier-test-writer 寫測試、還沒執行 seal 的那段時間，明文就在工作目錄裡。
**minimal 的操作紀律因此多一條：測試寫完立刻封存，不要留在暫存區過夜。**

**沒有自我檢查的探測。** 機制壞掉時不會有訊號。不過 minimal 少了大部分會壞的
東西——第 1 層壞掉的方式是「封存腳本執行失敗」，那是看得見的。

## 4. 少了哪幾層，每一次都會被講出來

這是 minimal 的設計裡最重要的一條：**它不是靜靜少跑幾個檢查。**

- `seal-hidden-tests.py` 封存時印出「這次不會鎖定公開測試」
- `run-hidden-tests.py` 驗收時印出「這次沒有公開測試鎖定與事後稽核」，
  並要求把這件事寫進驗收報告
- `guard-selfcheck.py` 每個 session 印出保護等級與沒有開的那三層

理由跟這個模板其他地方一樣：保護少一層而**沒有訊號**，比沒有保護更危險——
後者至少沒有人誤以為自己受保護。

`minimal` 模式下 SessionStart 的探測**不會照跑**，這是刻意的：沒裝 hook 時探測
一定全部失敗，每個 session 印一整片紅字只會訓練出「忽略警告」的習慣，
而那比少一層保護更難修。

## 5. 什麼時候該用哪一個

用 `minimal`：

- 你自己一個人的專案，implementer 是你自己派的 sub-agent，沒有對抗性動機
- 你想先試試看這套流程，還不想動 `.claude/settings.json`
- 專案本身是探索性的，公開測試每天都在改

用 `full`：

- 有人會依據驗收結果做決定（交付、計費、合併）
- bug 修正的驗收（`--kind bugfix`）——那一類最常見的假修正正是「只讓被回報的
  那個 case 過」，而抓它需要公開測試的鎖定
- 你打算長期用同一份規格反覆派工

升級隨時可以：把 `level` 改成 `full`（或整個 `protection` 區塊刪掉）、
裝上 hook、重新封存一次即可。重新封存會鎖定當下的公開測試，基準就建立起來了。

## 6. CI 驗收是另一個軸，不是第三個等級

`protection.ci_verification` 跟 `level` 是**兩個獨立的選擇**：

```json
{
  "protection": { "level": "minimal", "ci_verification": true }
}
```

`level` 決定「本機開幾層」，`ci_verification` 決定「正式驗收在哪裡跑」。
`minimal` + CI 驗收是一個合理、甚至相當強的組合：本機只做隔離，
真正的驗收跑在 implementer 完全碰不到的地方。見
[`ci-verification.md`](ci-verification.md)。
