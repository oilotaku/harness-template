# 依專案目標決定要安裝哪些 skill

Claude Code 的 skill 可以大幅改善特定領域的產出品質（PDF、試算表、MCP server…），
但**裝 skill 不是免費的**，而且裝錯對象會直接破壞這個模板的核心保證。
這份文件定義的是「哪些該裝、給誰、為什麼」的決策程序。

工具：`scripts/suggest-skills.py`（決策）＋ `skills.catalog.json`（這個專案的判斷）。

> **邊界先講清楚**：那支腳本**不會替你安裝任何東西**。安裝 skill 是把檔案放進
> `.claude/skills/<name>/SKILL.md` 或安裝對應的 plugin，那是人（或 Orchestrator
> 明確執行）的動作。腳本只做「決定」與「留下決定的理由」。

---

## 1. 為什麼需要決策程序，而不是「有用就裝」

### 1.1 skill 的成本是每個 session 都要付的

每個已啟用 skill 的名稱與描述都會進入**每一個 session 的上下文**。
以本模板的結構（一個 task 至少 3 個不共用上下文的 session）來說，
它的乘數跟 `CLAUDE.md` 同一級：裝十個「看起來可能有用」的 skill，
等於每個 task 都替它們付十次錢（見 `token-strategy.md` §1 的成本公式）。

所以預設答案是**不裝**。要裝的那個，得說得出這個專案為什麼需要它。

### 1.2 skill 會誘導行為，給錯對象等於拆掉防線

skill 是**指令**，不是工具權限——它不能繞過 `guard-hidden-tests.py`
（那是 PreToolUse hook，攔的是工具呼叫）。但它會改變 agent 的傾向。

給 verifier 裝一個會產生程式碼的 skill，它就有理由「順手把問題修好」。
而檢驗者一旦動手改實作，獨立驗證鏈就斷了——**變成自己驗自己**
（黃金法則第 3 條）。

這件事不靠自律。`skill_policy.py` 裡有一條寫死的規則：

> **檢驗者角色永遠拿不到 `produces_code: true` 的 skill。**
> 即使 catalog 明確把它列給 verifier 也一樣——那是設定寫錯，不是需求。

---

## 2. 四個判準

決定要不要把某個 skill 寫進 catalog 之前，逐條回答：

| # | 問題 | 答案不對就不要裝 |
|---|---|---|
| 1 | 它對應的是這個專案的**交付物或領域**，還是只是「通用好用」？ | 通用好用 → 不裝 |
| 2 | 這個專案**每輪都會用到**它嗎？ | 只用一次 → 那次直接給指令就好 |
| 3 | 有沒有更便宜的替代？（一段 task-spec 說明 vs 一個常駐 skill） | 有 → 用便宜的 |
| 4 | **誰**需要它？implementer？verifier？還是只有 Orchestrator？ | 說不出來 → 還沒想清楚 |

第 2 條特別容易被跳過。一次性的需求用一次性的手段解決，
不要為了一個 task 讓後面每個 session 都付成本。

---

## 3. `skills.catalog.json`

放在專案根目錄，**會進版控**——它記錄的是這個專案的判斷，不是某台機器的狀態。
範例見 `templates/skills.catalog.example.json`。

```json
{
  "skills": [
    {
      "name": "pdf",
      "why": "本專案要產出 PDF 報表；沒有它就得自己拼 PDF 產生程式碼",
      "when": { "deliverables": ["pdf"], "keywords": ["報表"] },
      "roles": ["implementer"],
      "produces_code": true
    }
  ]
}
```

| 欄位 | 意義 |
|---|---|
| `name` | skill 名稱 |
| `why` | **為什麼這個專案需要它**。沒寫理由的 skill 之後沒人敢移除——因為沒人知道當初為什麼裝 |
| `when` | 什麼情況下該考慮它：`deliverables` / `languages` / `frameworks` / `keywords`，任一欄有交集就算命中 |
| `roles` | 可以給誰：`orchestrator` / `implementer` / `verifier` |
| `produces_code` | 它會不會產生程式碼。**這個欄位決定它會不會被擋在檢驗者之外，不可以省略** |

幾個刻意的設計：

- **`when` 四欄全空的 skill 永遠不會被自動建議。** 避免「反正裝著也不會怎樣」
  的東西悄悄變成常駐成本。
- **`why` 與 `produces_code` 都是必填。** 少了就直接報錯。
- **catalog 壞掉一律報錯，不退回空清單。** 空清單看起來像「這個目標不需要 skill」，
  那是最糟的失效方式——跟 `harness_config.py` 的 fail-closed 是同一條原則。

---

## 4. 用法

```bash
# 這個 task 的交付物是 PDF、用 Python，implementer 該裝什麼？
python3 scripts/suggest-skills.py --role implementer --deliverable pdf --language python

# 三個角色一次看完（Orchestrator 規劃時用），機器可讀
python3 scripts/suggest-skills.py --role all --deliverable pptx --keyword 簡報 --json
```

輸出分三塊：**建議安裝**（含命中的欄位與理由）、**明確排除**（含排除原因）、
**警告**（例如找不到 catalog）。

「建議安裝：無」是正常結果，不是失敗。

---

## 5. 誰決定、誰執行

| 角色 | 可以做 |
|---|---|
| **Orchestrator** | 跑決策、更新 catalog、實際安裝 skill、把決定寫進 task-spec 的 `skills` 欄位 |
| **implementer** | **不可以**自行安裝或啟用任何 skill——那是範圍外變更（黃金法則第 4 條） |
| **verifier** | 可以回報「這個 task 缺某個 skill」，由 Orchestrator 決定；自己不安裝 |

---

## 6. 禁止事項

- 禁止因為「看起來有用」就裝。預設答案是不裝。
- 禁止裝了不寫 `why`。沒有理由的 skill 會永久留在成本裡，因為沒人敢移除它。
- 禁止給檢驗者任何會產生程式碼的 skill（腳本會擋，但不要試圖繞過設定去達成）。
- 禁止 implementer 自行安裝 skill。
- 禁止把 catalog 當成「可用 skill 清單」。它是**這個專案的決定**，
  不是別人有什麼的目錄。

---

## 7. 檢查清單

規劃一輪任務時：

- [ ] 這輪的交付物、語言、領域關鍵字有沒有填進 `--deliverable` / `--language` / `--keyword`？
- [ ] 建議清單裡每一項都通過第 2 節的四個判準了嗎？
- [ ] 有沒有哪個 skill 是「只有這個 task 需要」？（那就不要裝，直接寫進 task-spec）
- [ ] 排除清單裡有沒有你其實想給 verifier 的東西？（先確認它真的不產生程式碼）
- [ ] 決定寫進 task-spec 的 `skills` 欄位了嗎？
