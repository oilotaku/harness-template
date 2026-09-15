# 四輪審視的決策摘要

這個資料夾放的是完整的審視報告，**只給人看**：它們比其他所有 `docs/` 加起來還大，
不要放進任何子智能體的閱讀路徑（見 `docs/token-strategy.md` §1）。

下面 200 字左右的摘要，是絕大多數人需要知道的全部。

| 輪次 | 問的問題 | 結論 |
|---|---|---|
| [第一輪](improvement-suggestions.md) | 規則寫在文件裡，有東西讓它成立嗎？ | 沒有。18 項全部落地：隱藏測試加密封存到 repo 之外、guard hook fail-closed、SessionStart 自我檢查、CI。 |
| [第二輪](improvement-suggestions-round2.md) | 第一輪引入的新信任根（manifest、鎖定清單、keystream）自己可靠嗎？ | 不可靠。manifest 欄位改得掉、每檔共用 keystream 可以 two-time pad 還原。改成逐項簽章 + 每檔獨立金鑰。 |
| [第三輪](improvement-suggestions-round3.md) | 簽章保護的是 runner 信任的**資料**，那 runner 自己是誰寫的？ | implementer 改得到。改成驗收程式碼一起封存、由封存版執行、sha256 進簽章範圍。 |
| 第四輪（本次） | 再加一層還有報酬嗎？ | 沒有。第三輪自己記下的循環（「換掉檢查自己那一份」就沒人執行那段檢查）是自我檢查的結構性終點。所以換軸：驗收搬到 GitHub Actions（`docs/ci-verification.md`），並把「全有或全無」拆成可選的保護等級（`docs/protection-levels.md`）。 |

一句話總結這四輪：**每一輪都在問「這條規則靠什麼成立」，而每一輪的答案都是
「靠下面那一層」——直到第四輪發現下面已經沒有層了，只剩另一台機器。**

> 第四輪沒有獨立的報告檔案。改動本身與它的誠實記載寫在
> `docs/ci-verification.md` §5 與 `docs/protection-levels.md` §3，
> 那兩份是**使用說明**，不是推理過程——推理過程對使用者沒有價值。
> 前三輪的報告留在這裡是歷史，不是慣例：之後每一輪都該像第四輪這樣，
> 產出使用說明，而不是再長一份報告。
