# 四輪審視的決策摘要

這份是主線上唯一保留的審視紀錄。四輪完整報告（約 114 KB）**已移出主線**——
它們是推理過程，只給人看，留在 `docs/` 裡只會讓每個 clone 的人多一份不會讀的檔案，
還會變成一份逐漸跟程式碼脫節的「活文件」。下面 200 字左右的摘要，是絕大多數人
需要知道的全部。

| 輪次 | 問的問題 | 結論 |
|---|---|---|
| 第一輪 | 規則寫在文件裡，有東西讓它成立嗎？ | 沒有。18 項全部落地：隱藏測試加密封存到 repo 之外、guard hook fail-closed、SessionStart 自我檢查、CI。 |
| 第二輪 | 第一輪引入的新信任根（manifest、鎖定清單、keystream）自己可靠嗎？ | 不可靠。manifest 欄位改得掉、每檔共用 keystream 可以 two-time pad 還原。改成逐項簽章 + 每檔獨立金鑰。 |
| 第三輪 | 簽章保護的是 runner 信任的**資料**，那 runner 自己是誰寫的？ | implementer 改得到。改成驗收程式碼一起封存、由封存版執行、sha256 進簽章範圍。 |
| 第四輪 | 再加一層還有報酬嗎？ | 沒有。第三輪自己記下的循環（「換掉檢查自己那一份」就沒人執行那段檢查）是自我檢查的結構性終點。所以換軸：驗收搬到 GitHub Actions（`docs/ci-verification.md`），並把「全有或全無」拆成可選的保護等級（`docs/protection-levels.md`）。 |

一句話總結這四輪：**每一輪都在問「這條規則靠什麼成立」，而每一輪的答案都是
「靠下面那一層」——直到第四輪發現下面已經沒有層了，只剩另一台機器。**

## 完整報告去哪裡了

完整報告不再放在主線，但沒有刪除——它們留在 git 歷史裡，需要時取得回來：

```bash
git show 232f3d5:docs/history/improvement-suggestions.md         # 第一輪（18 項）
git show 232f3d5:docs/history/improvement-suggestions-round2.md  # 第二輪
git show 232f3d5:docs/history/improvement-suggestions-round3.md  # 第三輪
```

（`232f3d5` 是它們最後一次同時存在於主線的 commit。）

各支腳本的模組說明裡還留著「對應 improvement-suggestions.md 的 P1-4」這類 provenance
麵包屑——那裡的 item ID（P1-4、P0-5…）是穩定標籤，指的就是上面那幾份報告裡的條目，
用上面的 `git show` 取回原文即可。

> 第四輪沒有獨立的報告檔案，這是刻意的：改動與它的誠實記載寫在
> `docs/ci-verification.md` §5 與 `docs/protection-levels.md` §3，那兩份是
> **使用說明**，不是推理過程。之後每一輪都該像第四輪這樣——產出使用說明，
> 而不是再長一份只給人看、又會跟程式碼脫節的報告。
