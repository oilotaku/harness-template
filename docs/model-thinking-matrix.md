# 模型 / 思考層級分配對照表

> 說明：`model` 欄位對應子智能體定義檔 frontmatter 的 `model` 欄位
> （依你實際可用的模型調整，例如 haiku / sonnet / opus 或其他供應商對應等級）。
> `thinking` 為本模板內部的思考力度慣例（透過提示詞要求該子智能體展開多少步驟的
> 推理與自我檢查，實際強度仍取決於底層模型與介面支援程度）。

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
| verifier-reviewer | 恆定較高（建議 opus / high） | 抓作弊需要比實作更深的推理與懷疑視角 |
| verifier-security | 恆定 opus / high | 安全問題的漏判成本極高 |

## 判斷等級的簡易問卷（Orchestrator 拆 task 時填）

1. 這個 task 出錯會不會影響資料正確性或安全性？→ 是則至少 L3，牽涉憑證/權限/金流則 L4
2. 這個 task 需不需要理解跨模組的既有邏輯才能正確實作？→ 是則至少 L3
3. 這個 task 是不是重複性高、規則清楚的樣板工作？→ 是則 L1
4. 以上皆非 → L2（預設）

## 平行度與機器效能的關係

Orchestrator 在指派模型/思考層級時，也要參考 `scripts/machine-profile.py` 的結果：
- 記憶體/CPU 有限時，優先**減少同時執行的 L3/L4 高強度任務數量**，
  而不是把所有任務都降級成 L1（降級會犧牲品質，減少平行度才是正確做法）。
