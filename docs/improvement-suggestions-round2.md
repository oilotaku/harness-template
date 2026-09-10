# 改善建議報告——第二輪（2026-09-10）

> ⚠️ **這份文件只給人看，不要放進任何子智能體的閱讀路徑**（理由同第一輪：
> 它是歷史審視紀錄，不是流程文件，見 `docs/token-strategy.md` §1）。
>
> 審視對象：第一輪 18 項全部結案之後的 `harness-template`（`CLAUDE.md`、`.claude/`、
> `scripts/`、`docs/`、`.github/`）。所有標記為【已驗證】的問題，都是在一個
> 臨時 repo 裡實際執行 `seal-hidden-tests.py` / `run-hidden-tests.py` /
> `verify-locks.py`，或把模擬的 PreToolUse payload 餵給 `guard-hidden-tests.py`
> 觀察 exit code 得到的結果，不是只看程式碼推論。重現腳本見附錄 A。
>
> 第一輪的 253 個回歸案例在本輪審視時全數通過。下面的問題全部**不在**
> 那些測試的涵蓋範圍內——這正是它們還在的原因。

---

## 摘要：這一輪最大的問題

第一輪把防線從「攔得住每一種路徑寫法」翻轉成「檔案不在那裡、內容是密文、
解密要權杖」。那是對的方向，但它引入了一個新的信任根：**`.harness/hidden-manifest.json`**。
runner 完全信任這份 manifest 裡的 `test_command`、`files`、`task_dir`，
而 manifest 放在 repo 裡、`.harness/` 只擋「工具層」的寫入——implementer
用 Write 工具寫一支 `x.py`、再用 Bash 跑 `python3 x.py`（指令字串裡沒有任何受保護路徑），
就能改寫 manifest。第一輪的模組說明自己就寫著「implementer 手上有 Bash，
可以自己寫一支 `leak.py` 去讀任何路徑，這是攔不完的」——同一句話對**寫**也成立。

結果是（實測）：**把 manifest 的 `test_command` 換成一支印出「OK」的假腳本，
verifier-reviewer 用正確的權杖執行 `run-hidden-tests.py`，會得到 exit 0 與
「隱藏測試全部通過」**。權杖、加密、封存位置全部沒被碰，機制看起來完好，
但驗收結果是假的。

同一個根因（`.harness/` 的內容沒有完整性保護）還讓鎖定清單可以被整份刪掉，
之後 `verify-locks.py` 只會回 exit 2「沒有鎖定清單」——跟「從來沒鎖過」無法區分，
而 verifier-reviewer 的指示是「exit 2 可以繼續驗收，註記即可」。

修法都是同一件事：**凡是 runner 或稽核腳本會信任的 `.harness/` 內容，
都要用權杖做 HMAC 簽章**。權杖是整套機制裡唯一 implementer 拿不到的東西，
所以也是唯一能當簽章金鑰的東西。標準函式庫的 `hmac` 就夠，不需要新依賴。

---

## P0 — 驗收結果可被偽造

### P0-6. manifest 沒有完整性保護：換掉 `test_command` 就能假通過【已驗證】

**重現**：封存後，用一支不在 Bash 指令字串裡提到 `.harness/` 的腳本改寫
`.harness/hidden-manifest.json`：

```json
"test_command": "{python} /repo/fake.py {dir}"
```

其中 `fake.py` 印出一段長得像 unittest 的輸出（`Ran 2 tests … OK`）並 exit 0。
接著用**正確的權杖**執行 `run-hidden-tests.py --task-id T1 --token …`：

| 結果 | 值 |
|---|---|
| exit code | **0** |
| 狀態列 | 「隱藏測試全部通過。」 |
| `attempts.json` | 記了一筆 `passed: true` |

runner 有印出「指令：…」這一行，所以一個非常仔細的 verifier-reviewer
**有機會**注意到指令不對——但那是靠人眼，不是機制；而且假腳本可以把輸出做得
跟真的一模一樣。第一輪反覆強調「靠自律的規則要換成機制」，這裡正好還是自律。

順帶一提，把 `files` 清空（`"files": []`）這條路**有**被擋住：解密 0 個檔案 →
測試指令跑在空目錄 → 零測試偵測判定不通過。所以問題不在「所有欄位都能亂改」，
而在「`test_command` 這一個欄位就足夠」。

**建議修法**：封存時對每個 task 的 manifest 項目做 HMAC，runner 先驗簽再信任任何欄位。

```python
# hidden_vault.py
import hmac
MANIFEST_MAC_INFO = b"harness-manifest-mac-v1"

def mac_key(token: str) -> bytes:
    # 跟加密金鑰分開推導，不共用同一把
    return hashlib.sha256(MANIFEST_MAC_INFO + token.encode("utf-8")).digest()

def sign_entry(entry: dict, token: str) -> str:
    body = {k: v for k, v in entry.items() if k != "signature"}
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(mac_key(token), payload, "sha256").hexdigest()
```

- `seal-hidden-tests.py`：寫 manifest 前 `entry["signature"] = sign_entry(entry, token)`。
- `run-hidden-tests.py`：權杖指紋比對通過之後、讀任何其他欄位之前，
  `hmac.compare_digest(sign_entry(info, token), info.get("signature", ""))`，
  不符一律 exit 2，訊息寫明「manifest 在封存之後被改過，這本身就是可疑訊號」
  （跟現有「封存檔案被竄改」同一級）。
- 沒有 `signature` 欄位的舊版 manifest：拒絕執行並要求重新封存，
  不要相容——相容等於留一條「把 signature 刪掉就回到沒簽章」的路。
- 回歸測試：改 `test_command` / 改 `task_dir` / 刪 `signature` / 換順序重排 JSON
  （簽章要對重排不敏感，所以用 `sort_keys=True`）各一個案例。

`attempts.json` 也是同一類問題（implementer 可以把自己的失敗紀錄清掉），
但它只影響停損判斷、不影響驗收結果，所以列在 P3-9，不在這裡。

### P0-7. 鎖定清單可被整份刪掉，事後稽核分不出「被刪」與「從未鎖定」【已驗證】

**重現**：用腳本 `unlink` 掉 `.harness/locked-tests.list`，然後：

| 步驟 | 結果 |
|---|---|
| `Write` 到已鎖定的 `tests/public/test_p.py` | guard **放行**（exit 0）——清單不在了，它就沒有東西可比對 |
| `verify-locks.py` | exit **2**「找不到鎖定清單」 |
| `verifier-reviewer.md` 對 exit 2 的指示 | 「可以繼續驗收，但要在報告裡明確註記」 |

也就是說 implementer 只要先刪清單，就能自由改公開測試，而驗收流程對這件事的
反應是「註記一下」。第一輪 P1-2 加雜湊稽核的用意是「不依賴攔截是否成功」，
但稽核本身依賴的那份清單跟被攔截的東西放在同一個地方、受同一層（不完整的）保護。

**建議修法**（跟 P0-6 用同一把簽章）：

1. `seal-hidden-tests.py` 把當下 `locked-tests.list` 的 sha256（或 `null`，
   代表封存當下確實沒有清單）寫進**簽過章的** manifest 項目：`"locked_tests_sha256": "…"`。
2. `run-hidden-tests.py` 驗完簽章後重算清單的 sha256 比對：
   - 封存時有、現在沒有 → exit 2「鎖定清單在封存之後被刪除」
   - 封存時有、內容不同 → exit 2「鎖定清單在封存之後被改寫」
   - 封存時就沒有 → 照舊，只提醒。
3. `verifier-reviewer.md` 第 0 步改成：`verify-locks.py` 回 exit 2 時，
   **以 `run-hidden-tests.py` 的判定為準**——只有 manifest 證明「封存當下就沒有清單」
   才算「沒有保護」，否則是竄改。
4. 這會要求 `lock-tests.py` 在 `seal-hidden-tests.py` **之前**執行。
   目前 `docs/implementer-verifier-workflow.md` 的流程圖是先封存再鎖定，
   `verifier-test-writer.md` 則沒規定順序。最省事的做法是讓 `seal-hidden-tests.py`
   自己呼叫鎖定邏輯（一個指令做完「鎖定 + 封存 + 簽章」），順序問題就消失了。

---

## P1 — 機制設計層級

### P1-5. 所有隱藏測試共用同一條 keystream（two-time pad）【已驗證】

`hidden_vault.transform()` 對每個檔案都用 `keystream(derive_key(token), n)`，
金鑰只跟權杖有關、不跟檔案有關，所以同一個 task 的每個 `.enc` 檔在同一個
offset 用的是**同一個 keystream byte**。實測：

```
C1 xor C2 == P1 xor P2 ?  True
用「import unittest\nfrom fizzbuzz import fizzbuzz\n\nclass Hidden」當 crib，
從第 2 個檔案還原出的內容：b'import unittest\nfrom fizzbuzz import fizzbuzz\n\nclass Hidden'
```

這不是密碼分析——是 XOR 串流加密教科書第一頁的 two-time pad。而且這裡的明文
特別好猜：隱藏測試跟公開測試（implementer 手上有）通常共用同一個檔頭、
同一個 import、同一個 class 樣板，crib 幾乎是現成的；一個 LLM 做 crib-dragging
的能力也遠高於一般人。模組說明寫「威脅模型是一個沒有權杖的子智能體，不是密碼分析者」，
但一個沒有權杖的子智能體**做得到**這件事。

單一檔案的情況安全（沒有第二段密文可以 XOR），所以只有一個隱藏測試檔的 task 不受影響；
但 `verifier-test-writer.md` 鼓勵多檔（邊界、隨機化、行為一致性分開寫）。

**建議修法**：每個檔案用不同的金鑰。最簡單的是把檔案路徑混進金鑰推導：

```python
def derive_key(token: str, relative_path: str) -> bytes:
    return hashlib.sha256(
        KEYSTREAM_INFO + token.encode("utf-8") + b"\0" + relative_path.encode("utf-8")
    ).digest()
```

`transform(data, token, relative_path)` 兩處呼叫端（seal / run）一起改，
`MANIFEST_VERSION` 升到 2，runner 遇到版本 1 一律要求重新封存。
回歸測試：兩個檔案的密文 XOR **不等於**明文 XOR；同一內容在不同路徑下密文不同。

### P1-6. verifier-test-writer 無法在封存前執行自己寫的隱藏測試；基線執行會污染停損計數【已驗證】

第一輪 P0-3 收尾把「指令字串裡提到暫存區路徑」一律擋下，並明確把
「用 `unittest discover` 直接跑暫存區」翻轉成預期被擋。這對 implementer 是對的，
但 hook 分不出呼叫者，所以 **verifier-test-writer 也跑不了**——它寫完隱藏測試後，
唯一能確認「這些測試至少跑得起來、而且在沒有實作時是紅的」的方式，是封存之後
用權杖跑 `run-hidden-tests.py`。而那一跑：

| 觀察 | 值 |
|---|---|
| `attempts.json` 多了一筆 `passed: false` | 是（實測） |
| 這筆會被算進 implementer 的連續失敗 | 是——`summary()` 從最後一筆往回數 |
| `run-hidden-tests.py` 有沒有「不記錄」的選項 | 沒有 |

所以現況是三選一：test-writer 不驗證自己的測試（一個語法錯誤要等到驗收時才發現，
而那時明文已經沒了，只能憑記憶重寫再封存）、驗證但污染停損計數、
或違反流程直接在終端機跑。三個都不好。

更重要的是這暴露了一個沒被任何機制守住的假設：**隱藏測試在實作前應該是紅的**。
一份全部 `assert True` 的隱藏測試會安靜地通過每一輪驗收——verifier-reviewer
讀不到內容，只看得到「全過」。黃金法則第 2 條「先寫測試」跟
`docs/root-cause-and-fix.md`「先讓測試變紅」是同一個原則，但沒有東西讓它成立。

**建議修法**：給 runner 一個 `--baseline` 模式，由 test-writer 在封存後立刻執行：

- 不寫 `attempts.json`。
- 預期測試指令**非零**結束；若全綠，直接報「這份隱藏測試在沒有實作時就通過了，
  沒有鑑別力」並 exit 1。
- 把結果寫進簽過章的 manifest：`"baseline": {"ran_at": …, "red": true, "tests_seen": N}`
  （`tests_seen` 從輸出解析，解析不到就 `null`）。
- `run-hidden-tests.py` 正式模式若發現 manifest 沒有 `baseline`，印一行警告
  「這份隱藏測試從未被證明過會紅」；`verifier-reviewer.md` 要求把這件事寫進報告。
- `verifier-test-writer.md` 的交付清單加一項：「已執行 `--baseline` 且為紅」。

### P1-7. 隱藏測試暫存區沒有被 `.gitignore` 排除，一旦進了 git 歷史就永遠讀得到【已驗證】

`git check-ignore tests/hidden/test_x.py` → 不被忽略。流程上 test-writer 應該
「寫完就封存」，但 `CLAUDE.md` §5 同時要求「每個 task 做完就 commit」，
Orchestrator 在封存前 `git add -A` 一次（例如先 commit 公開測試），明文就進歷史了。
之後 implementer 用 `git log -p`、`git show <sha>`（指令字串裡完全不需要出現暫存區路徑）
就能讀到。封存腳本刪掉工作目錄裡的明文對歷史沒有作用。

**建議修法**：

1. `.gitignore` 加：
   ```
   tests/hidden/*
   !tests/hidden/README.md
   !tests/hidden/.gitkeep
   ```
   自訂 `hidden_test_paths` 的專案要自己加對應規則——`guard-selfcheck.py` 對每個
   `hidden_test_paths` 跑 `git check-ignore`，沒被忽略就警告（跟現有的
   「有測試但沒被保護」警告同一級）。
2. `seal-hidden-tests.py` 封存前跑 `git ls-files --error-unmatch <暫存區>` 與
   `git log --all --oneline -- <暫存區>`：有被追蹤或歷史裡出現過，就拒絕封存並說明
   「這批測試已經在 git 歷史裡，封存沒有意義，請換一批測試」。

### P1-8. 執行權杖會留在 Claude Code 的 transcript 裡，同一個 OS 使用者讀得到

`seal-hidden-tests.py` 把權杖印在 stdout；那段輸出成為 test-writer 的工具結果、
再成為它回給 Orchestrator 的訊息、再成為 Orchestrator 派工 verifier-reviewer 的提示詞。
每一站都會被 Claude Code 寫進 `~/.claude/projects/<專案>/…jsonl`（本機實測該目錄存在、可讀）。
implementer 的 Bash 跑在同一個 OS 使用者下，`grep -r` 一下家目錄就找得到。

這**不是**新的漏洞——`run-hidden-tests.py` 的模組說明已經承認「同一個使用者身分的行程
掃得到解密後的暫存目錄」，權杖在 transcript 裡只是同一條邊界的另一個面向，而且
**沒有模板內的修法**：權杖不管放檔案還是放提示詞，都在同一個使用者的讀取範圍內。
把它從 stdout 拿掉只是換個地方放。

**建議**：把威脅模型邊界寫清楚，而不是修。目前 `seal-hidden-tests.py` 印的
「implementer 用任何工具、任何路徑寫法都讀不到，即使自己寫腳本去讀封存檔案，
拿到的也是密文」、以及 README 那張「不靠什麼／靠什麼」的表，在 P1-5 + P1-8 之下都
說得太滿。誠實的版本是：

> 這套機制擋的是「順手看一眼」「習慣性搜整個 repo」「用工具的正常用法碰到」這類
> **非刻意**的洩題，加上 hook 與稽核讓**刻意**的繞過留下痕跡。它不擋一個
> 「決心繞過、而且跟 verifier 共用同一個 OS 使用者」的子智能體——那需要獨立的
> 使用者或容器，超出本模板範圍。

寫進 `docs/implementer-verifier-workflow.md` 的「權限保證」一節與 README。

---

## P2 — 流程與文件一致性

### P2-6. 事前攔截對「已鎖定公開測試」仍有一整排缺口，而第一輪把 P0-3 標成全部修正【已驗證】

P0-3 收尾的「不分動詞」規則只套在**隱藏測試暫存區**；已鎖定的公開測試仍然走
舊的動詞清單（`WRITE_VERBS` + `sed -i` + `dd` + `git rm`）。第一輪 P0-3 的表格裡
有一列 `git checkout HEAD -- tests/public/test_p.py`，該節標題卻是「✅ 已修正」。實測：

| 指令（目標是已鎖定的公開測試） | guard |
|---|---|
| `git checkout HEAD~1 -- tests/public/test_p.py` | 放行 |
| `git restore --source=HEAD~1 tests/public/test_p.py` | 放行 |
| `git apply fix.patch` | 放行 |
| `perl -i -pe 's/assertEqual/assertTrue/' tests/public/test_p.py` | 放行（`-pe` 不是 `-e`） |
| `python3 rewrite.py` | 放行（預期，見 P0-6） |

這些**都會被** `verify-locks.py` 的事後稽核抓到（只要清單還在，見 P0-7），
所以是 P2 不是 P0。但兩件事該修：

1. 文件：P0-3 那節的「已修正」只涵蓋隱藏測試，公開測試那列要明講「事前層不擋，
   靠事後稽核」。
2. 機制：把「不分動詞」規則也套到已鎖定的公開測試上——`implementer` 沒有任何正當理由
   在 Bash 指令裡指名一個已鎖定的檔案做**寫入**……但它有正當理由**讀**它
   （`cat`、`python -m pytest tests/public`）。所以這裡不能照抄暫存區的規則，
   可行的折衷是：`git` 動詞底下的 `checkout` / `restore` / `apply` / `stash` /
   `clean` / `reset` 一律把非旗標參數當寫入候選，`perl`/`ruby`/`sed` 只要有任何
   以 `-i` 開頭或含 `i` 的合併短旗標（`-pi`、`-pie`）就當 in-place。
   這仍是列舉式的，所以文件要繼續寫明「主防線是事後稽核」。

### P2-7. `docs/improvement-suggestions.md` 的進度表過時，跟 README 矛盾

該檔第 30 行的進度表寫「其餘 P1-3 / P2 / P3-2 / P3-4 / P3-5 ⬜ 未動」，
但底下各節全部標了 ✅，README 也說「18 項，全部結案」。讀者會相信表格。
把那一列改成 ✅ 並註明落地的 PR 即可。

### P2-8. `/task-plan` 指令與 `orchestrator.md` 漂移

`.claude/commands/task-plan.md` 列了 5 個步驟、要求分別執行三支掃描腳本；
`orchestrator.md` 已經是 11 個步驟、明講「一律用 `init.py --json`」，多了估時、
決定 skill、進度檢查點。斜線指令是使用者最常走的入口，它落後代表大部分執行
會少做那幾步。第一輪 P2-5 對 `CLAUDE.md` 的處理方式（不重複步驟、只指向）
同樣適用：指令檔改成「以 `orchestrator` 身份、依 `.claude/agents/orchestrator.md`
的執行順序處理以下需求」，不要再抄一份清單。順便補一個漂移測試：
指令檔裡出現的步驟數不得多於 agent 定義檔（或乾脆檢查它不含編號清單）。

### P2-9. `hidden_test_command` 含字面大括號時 runner 以 traceback 結束，exit 1 而非 2【已驗證】

`argv = [token.format(**substitutions) …]`，測試指令若含 `{`（例如 Go 的
`-run '^Test{Foo}$'`、或 JS 專案的 `--grep '{tag}'`）會 `KeyError: 'Foo'`，
未被接住，exit 1。runner 的契約是「1 = 有測試失敗、2 = 無法執行」，
verifier-reviewer 會把這個 1 讀成「有隱藏測試失敗」。修法：只代換三個已知的
placeholder（`str.replace` 三次，或 `string.Template`），不用 `format`；
`harness_config.load()` 也一併驗證指令裡沒有未知的 `{…}`。

---

## P3 — 可用性與工程基礎建設

### P3-6. verifier-reviewer 看不到 SessionStart 的自我檢查結果

`guard-selfcheck.py` 的輸出只進**主 session** 的上下文；子智能體是新 session，
不會再跑一次 SessionStart hook（也不該跑，那會讓每個子智能體多付一次成本）。
所以 hook 若在派工中途失效（settings.json 被改、`python3` 消失），
verifier-reviewer 不會知道。建議給 `guard-selfcheck.py` 加 `--strict`
（任何一項不符就非零），`verifier-reviewer.md` 第 0 步跟 `verify-locks.py`
一起跑，非零就跟 exit 1 同樣處理。

### P3-7. CI 小項

- `actions/checkout@v4`、`actions/setup-python@v5` 用 tag 不是 SHA；對一個「防作弊」
  repo 來說 supply chain 也是威脅面之一，建議 pin 到 commit SHA 並加 Dependabot。
- 沒有 `concurrency` 群組，同一個 PR 連推兩次會跑兩套 6 個 job。
- 沒有任何靜態檢查：`python -m compileall scripts`（抓語法錯，尤其是只在 3.9
  才會出現的）或 `ruff check` 各一步，成本極低。

### P3-8. `docs/improvement-suggestions.md` 放在 `docs/` 跟「細節一律放 `docs/`」的規則衝突

`CLAUDE.md` §5 說「`CLAUDE.md` 只寫規則與指向，細節一律放 `docs/`」，
等於告訴每個子智能體「答案在 `docs/`」；同一個目錄裡躺著一份 39,000 字元、
標題寫「不要讀我」的檔案（本檔也是）。`token-strategy.md` 用三處警告在補這件事，
但目錄結構本身就在跟警告作對。建議搬到 `docs/history/`（或根目錄 `REVIEWS/`），
`README` 的連結跟著改。

### P3-9. `attempts.json` 沒有完整性保護

implementer 可以用腳本清掉自己的失敗紀錄，讓 `should_stop` 永遠不會變 true。
它不影響驗收結果，所以只是 P3；但既然 P0-6 要做簽章，順手讓 `run-hidden-tests.py`
在每筆紀錄上附 HMAC（同一把 `mac_key`）、`show-attempts.py` 有權杖時可驗、
沒權杖時把「無法驗證」跟「沒有紀錄」分開報，就跟第一輪 `should_stop = None`
的設計一致。

---

## 建議的處理順序

| 順序 | 項目 | 理由 |
|---|---|---|
| 1 | P0-6 manifest 簽章 | 驗收結果可被偽造，而且修法（`hmac`）小、無新依賴 |
| 2 | P0-7 鎖定清單 sha256 進簽章 + 封存前先鎖定 | 跟 1 用同一把金鑰、同一次改動 |
| 3 | P1-5 每檔獨立金鑰 | 一行推導函式的改動，manifest 版本一起升到 2 |
| 4 | P1-6 `--baseline` | 讓「先讓測試變紅」變成機制；同時解掉 test-writer 無法自我驗證的死結 |
| 5 | P1-7 gitignore + 封存前檢查 git 歷史 | 小改動，堵住一條完全不需要繞 hook 的路 |
| 6 | P1-8 / P2-6 / P2-7 文件校準 | 不改機制，但讓宣稱與實際強度一致 |
| 7 | P2-8 / P2-9 / P3 系列 | 可用性 |

1～3 建議做成同一個 PR：三者都要動 manifest 格式與 `hidden_vault.py`，分開做會升三次版本。

---

## 附錄 A：重現方式

以下腳本在臨時目錄建立最小情境，逐項印出結果。**請在一般終端機執行，不要讓
Claude Code 幫你跑**——腳本內容提到暫存區路徑，經過 Bash 工具會被 guard 擋下
（那正是它該做的事）。

```python
import json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(".").resolve()          # 在模板根目錄執行
SCRIPTS = REPO / "scripts"
GUARD = SCRIPTS / "guard-hidden-tests.py"

def run(cmd, cwd, env=None, inp=None):
    e = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if env: e.update(env)
    return subprocess.run(cmd, cwd=str(cwd), env=e, input=inp, text=True, capture_output=True)

def guard(payload, cwd):
    return run([sys.executable, str(GUARD)], cwd,
               {"CLAUDE_PROJECT_DIR": str(cwd)}, json.dumps(payload)).returncode

tmp = Path(tempfile.mkdtemp()); repo = tmp / "repo"
(repo / "tests/hidden").mkdir(parents=True); (repo / "tests/public").mkdir()
shutil.copytree(SCRIPTS, repo / "scripts")
(repo / "tests/public/test_p.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertEqual(1, 1)\n")
run(["git", "init", "-q"], repo)
run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A"], repo)
run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"], repo)
ENV = {"CLAUDE_PROJECT_DIR": str(repo), "HARNESS_HIDDEN_DIR": str(tmp / "vault")}
run([sys.executable, "scripts/lock-tests.py"], repo, ENV)

print("== P2-6 已鎖定公開測試，事前層 (2 = 擋下)")
for cmd in ["git checkout HEAD~1 -- tests/public/test_p.py",
            "git restore --source=HEAD~1 tests/public/test_p.py",
            "git apply fix.patch",
            "perl -i -pe 's/a/b/' tests/public/test_p.py"]:
    print(" ", guard({"tool_name": "Bash", "tool_input": {"command": cmd}}, repo), cmd)

print("== P1-5 two-time pad")
p1 = b"import unittest\nfrom fizzbuzz import fizzbuzz\n\nclass Hidden(unittest.TestCase):\n    def test_15(self): pass\n"
p2 = b"import unittest\nfrom fizzbuzz import fizzbuzz\n\nclass Hidden2(unittest.TestCase):\n    def test_neg(self): pass\n"
(repo / "tests/hidden/test_h1.py").write_bytes(p1); (repo / "tests/hidden/test_h2.py").write_bytes(p2)
out = run([sys.executable, "scripts/seal-hidden-tests.py", "--task-id", "T1"], repo, ENV).stdout
token = re.search(r"執行權杖（只會出現這一次）：([0-9a-f]+)", out).group(1)
c1 = (tmp / "vault/T1/test_h1.py.enc").read_bytes(); c2 = (tmp / "vault/T1/test_h2.py.enc").read_bytes()
n = min(len(c1), len(c2))
print("  C1^C2 == P1^P2:", bytes(a ^ b for a, b in zip(c1[:n], c2[:n])) == bytes(a ^ b for a, b in zip(p1[:n], p2[:n])))

print("== P0-6 manifest 竄改")
mpath = repo / ".harness/hidden-manifest.json"; m = json.loads(mpath.read_text())
(repo / "fake.py").write_text("print('Ran 2 tests in 0.001s\\n\\nOK')\n")
m["tasks"]["T1"]["test_command"] = "{python} " + str(repo / "fake.py") + " {dir}"
mpath.write_text(json.dumps(m))
r = run([sys.executable, "scripts/run-hidden-tests.py", "--task-id", "T1", "--token", token], repo, ENV)
print("  exit =", r.returncode, "| 狀態 =", [l for l in r.stdout.splitlines() if l.startswith("狀態")])

print("== P0-7 刪鎖定清單")
(repo / ".harness/locked-tests.list").unlink()
print("  Write 公開測試 guard exit =", guard({"tool_name": "Write", "tool_input": {"file_path": str(repo / "tests/public/test_p.py")}}, repo))
print("  verify-locks exit =", run([sys.executable, "scripts/verify-locks.py"], repo, ENV).returncode)

print("== P1-6 基線執行是否計入 attempts")
print("  attempts:", len(json.loads((repo / ".harness/attempts.json").read_text())["tasks"]["T1"]))

print("== P1-7 gitignore")
print("  check-ignore exit (1 = 未忽略):", run(["git", "check-ignore", "tests/hidden/x.py"], REPO).returncode)
shutil.rmtree(tmp, ignore_errors=True)
```

2026-09-10 實測結果：P2-6 四條全部放行；`C1^C2 == P1^P2` 為 True；
P0-6 exit 0、狀態「隱藏測試全部通過」；P0-7 guard 放行（0）、verify-locks 回 2；
P1-6 attempts 已記 1 筆；P1-7 未忽略。第一輪的 253 個回歸案例在同一時間點全數通過。
