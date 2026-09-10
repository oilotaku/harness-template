# 模型 / 思考層級分配對照表

> 說明：`model` 欄位對應子智能體定義檔 frontmatter 的 `model` 欄位
> （依你實際可用的模型調整，例如 haiku / sonnet / opus 或其他供應商對應等級）。
> `thinking` 為本模板內部的思考力度慣例（透過提示詞要求該子智能體展開多少步驟的
> 推理與自我檢查，實際強度仍取決於底層模型與介面支援程度）。
>
> ⚠️ **frontmatter 的 `thinking:` 欄位不會被 Claude Code 讀取**——它認得的是
> `name` / `description` / `tools` / `model`，其餘會被當成未知欄位忽略。
> 所以這個欄位純粹是文件標註，**真正讓思考層級生效的是每個 agent 定義檔裡的
> 「推理強度」章節**（提示詞本文）。兩邊必須一致：`scripts/test-guards.py`
> 有一個案例會檢查「宣告了 thinking: X 的 agent，本文有沒有對應的指示」，
> 漂移的話會直接紅。P2-3 之前的狀況是只有欄位、沒有本文指示，
> 等於模板宣稱了一個從來沒有實際效果的設定。

## 任務複雜度分級

| 等級 | 特徵 | model | thinking |
|---|---|---|---|
| L1 機械型 | 格式調整、重複性樣板、簡單 CRUD、單純資料轉換 | 輕量模型（如 haiku 等級） | low |
| L2 標準型 | 一般功能開發、常見 API 串接、標準元件實作 | 中量模型（如 sonnet 等級） | medium |
| L3 複雜型 | 跨模組邏輯、效能敏感、既有系統相容性高、演算法設計 | 重量模型（如 opus 等級） | high |
| L4 高風險型 | 安全/權限/金流/資料遷移，出錯代價高 | 重量模型（如 opus 等級） | high（並強制走 verifier-security） |

## 角色 × 等級 對照

| 角色 | 預設等級 | 理由 |
|---|---|---|
| Orchestrator | 恆定 opus / high | 決策影響全局，錯誤成本最高 |
| implementer-generic / backend / frontend | 依任務等級（L1~L4）動態調整 | 大部分任務落在 L2，複雜任務才升級 |
| verifier-test-writer | 恆定較高（至少等同該任務等級，且不低於 medium） | 測試設計得草率，整條防線就失效 |
| verifier-reviewer | L3/L4 恆定 opus / high；L1/L2 可先小後大（見下） | 抓作弊需要比實作更深的推理與懷疑視角 |
| verifier-security | 恆定 opus / high | 安全問題的漏判成本極高 |

## 判斷等級的簡易問卷（Orchestrator 拆 task 時填）

1. 這個 task 出錯會不會影響資料正確性或安全性？→ 是則至少 L3，牽涉憑證/權限/金流則 L4
2. 這個 task 需不需要理解跨模組的既有邏輯才能正確實作？→ 是則至少 L3
3. 這個 task 是不是重複性高、規則清楚的樣板工作？→ 是則 L1
4. 以上皆非 → L2（預設）

## 先小後大：低風險任務的驗收升級規則

驗收其實是兩件性質不同的事：**跑測試**（機械執行，不需要大模型）與
**抓作弊**（需要懷疑視角，需要大模型）。所以 L1/L2 任務可以先用中量模型
跑第一輪驗收，出現下列任一訊號才升級到重量模型重驗：

- `scripts/verify-locks.py` 或 `scripts/run-hidden-tests.py` 回報異常
- 靜態審查發現任何可疑／取巧模式
- 實作變更超出 task-spec 宣告的範圍邊界
- task 標記 `security_review: true`

**L3/L4 不適用這條**——跨模組、安全、金流、資料遷移這類任務，
漏判一次的代價遠高於省下的模型差價。理由與完整成本分析見
`docs/token-strategy.md`。

## 平行度與機器效能的關係

Orchestrator 在指派模型/思考層級時，也要參考 `scripts/machine-profile.py` 的結果：
- 記憶體/CPU 有限時，優先**減少同時執行的 L3/L4 高強度任務數量**，
  而不是把所有任務都降級成 L1（降級會犧牲品質，減少平行度才是正確做法）。

> 注意：平行度是**時間**的最佳化，不是 token 的。三個 task 平行跑跟序列跑，
> 總 token 一樣多。要省 token 請見 `docs/token-strategy.md`。
