# 改善建議報告（2026-09-10）

> ⚠️ **這份文件只給人看，不要放進任何子智能體的閱讀路徑。** 它有 39,000 多字元，
> 比其他所有 `docs/` 加起來還大——讓一個 agent 讀它，等於一次燒掉一個 task 的
> 固定 token 開銷（見 `docs/token-strategy.md`）。它是歷史審視紀錄，
> 不是流程文件。
>
> 審視對象：`harness-template` 全部檔案（CLAUDE.md、`.claude/`、`scripts/`、
> `docs/`、`templates/`）。所有標記為【已驗證】的問題，都是實際餵模擬的
> PreToolUse payload 給 `scripts/guard-hidden-tests.py`、觀察 exit code 得到的結果，
> 不是只看程式碼推論。重現方式見附錄 A。

---

## 實作進度（2026-09-10 更新）

| 項目 | 狀態 | 落地內容 |
|---|---|---|
| P0-1 路徑正規化統一 | ✅ 已修正 | `guard-hidden-tests.py` 全部分支改走 `_to_repo_relative()` |
| P0-5 fail-closed + hook 掛法 | ✅ 已修正 | 腳本 fail-closed、`$CLAUDE_PROJECT_DIR` + timeout、新增 `guard-selfcheck.py` |
| P1-2 鎖定清單雜湊稽核 | ✅ 已修正 | `lock-tests.py` 寫入 sha256、新增 `verify-locks.py` 與 `test-locks.py` |
| P2-4 hook matcher 明確列全 | ✅ 順手修正 | 改 hook 指令時一併把 `MultiEdit`/`NotebookEdit` 寫進 matcher |
| P1-1 隱藏測試移出工作目錄 | ✅ 已修正 | 新增 `hidden_vault.py` / `seal-hidden-tests.py` / `run-hidden-tests.py`：加密封存到 repo 之外 + 執行權杖 |
| P0-2 全 repo 搜尋撈到隱藏測試 | ✅ 由 P1-1 消解 | 檔案已不在工作目錄裡，搜尋範圍內根本沒有它 |
| P0-4 把隱藏測試當 oracle 執行 | ✅ 由 P1-1 消解 | 解密需要權杖，implementer 的上下文裡沒有 |
| P0-3 Bash 包裝繞過 | 🟡 降級 | `tests/hidden/`（暫存區）仍可能被繞過；但封存後那裡是空的，封存庫拿到的是密文 |
| P3-3 沒有 CI | ✅ 已修正 | 新增 `.github/workflows/ci.yml`：3 平台 × 2 個 Python 版本跑全部回歸測試 |
| P3-1 env-guard 在容器每次誤報 | ✅ 已修正 | 指紋改成「身分／能力」分開比對、新增 `--update`，並抽出 `machine_facts.py` 與 `env_fingerprint.py` |
| P1-4 保護路徑寫死 | ✅ 已修正 | 新增 `harness.config.json` 與 `harness_config.py`；設定錯誤 fail-closed；selfcheck 會警告「有測試但沒被保護」 |
| 其餘 P1-3 / P2 / P3-2 / P3-4 / P3-5 | ⬜ 未動 | 見下方各節 |

已修正的項目在小節標題標上「✅ 已修正」，內文保留原本的問題描述當作紀錄。
回歸測試：`test-guards.py`（59）、`test-locks.py`（12）、`test-vault.py`（19）、
`test-env-guard.py`（16）、`test-config.py`（24），共 **130 案例**，
並由 CI 在三個平台 × 兩個 Python 版本上自動執行。

---

## 摘要：目前最大的問題

這個模板的核心賣點是「隱藏測試 implementer 看不到，所以沒辦法針對測試作弊」，
而這條防線在文件裡被明確描述為「不只是口頭約定，是工具層級的技術強制力」
（`docs/implementer-verifier-workflow.md` §「權限保證」）。

**實測結果：這條技術強制力目前幾乎不存在。** 隱藏測試可以被讀到、可以被跑、
可以被刪掉，最短的一條路徑只需要用「絕對路徑」——而 Claude Code 的 `Read` 工具
本來就規定 `file_path` 必須是絕對路徑，也就是說**照工具的正常用法讀隱藏測試，
100% 不會被擋**。

流程設計、角色分工、文件密度這些部分其實寫得相當完整（比多數同類模板嚴謹），
問題集中在「機制的實作」與「文件宣稱的保護強度」之間的落差。以下依嚴重度排序。

> **2026-09-10 更新**：本段描述的是修正前的狀態，保留作為紀錄。P0-1、P0-5、P1-2
> 已修正，接著 P1-1 把隱藏測試整個搬出工作目錄並加密封存——**這才是結構性的解**：
> 防線不再建立在「攔得住每一種路徑寫法」上，而是建立在「檔案不在那裡、內容是密文、
> 解密要權杖」。P0-2 與 P0-4 因此一併消解，P0-3 降級成只影響封存前的暫存區。
> 殘留限制見 P1-1 該節與 `scripts/run-hidden-tests.py` 的模組說明，主要是：
> 測試執行的短暫期間，解密後的明文確實存在於一個暫存目錄。

---

## P0 — 防作弊機制實際失效（建議優先處理）

### P0-1. 絕對路徑與 `./`、`//`、`..` 完全繞過路徑比對【已驗證】✅ 已修正

`_check_file_path()` 與 `_check_read_tool()` 拿到路徑後，只做 `_normalize()`
（把反斜線換成正斜線）就丟給 `_is_hidden_tests_path()` 做**字串前綴比對**，
沒有經過 Bash 分支才會呼叫的 `_resolve()` 正規化。同一個檔案換個寫法就擋不到：

| payload | 結果 |
|---|---|
| `Read {"file_path": "tests/hidden/test_x.py"}` | 擋下 ✅ |
| `Read {"file_path": "/abs/path/repo/tests/hidden/test_x.py"}` | **放行** ❌ |
| `Read {"file_path": "./tests/hidden/test_x.py"}` | **放行** ❌ |
| `Read {"file_path": "tests//hidden/test_x.py"}` | **放行** ❌ |
| `Read {"file_path": "tests/../tests/hidden/test_x.py"}` | **放行** ❌ |
| `Write {"file_path": "/abs/path/repo/tests/public/test_p.py"}`（已鎖定） | **放行** ❌ |

第二列不是刻意繞過的攻擊手法，而是 `Read` 工具的**正常用法**（參數說明明講
「must be an absolute path」）。同理，`Write`/`Edit` 對已鎖定公開測試的保護
也一起失效。這是整份報告裡最該先修的一條。

**建議修法**：所有分支統一走同一個正規化函式，並支援絕對路徑還原成 repo 相對路徑。

```python
REPO_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()

def _to_repo_relative(raw: str, cwd: str = "") -> str:
    """統一入口：吸收 \\ 、./ 、// 、.. 與絕對路徑，一律轉成 repo 根目錄相對路徑。
    落在 repo 之外回傳空字串（不歸本 hook 管）。"""
    p = Path(_normalize(raw))
    if not p.is_absolute():
        p = REPO_ROOT / cwd / p
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return ""
```

`_check_file_path` / `_check_read_tool` / Bash 分支全部改用它，
並把上表六個案例加進 `scripts/test-guards.py`。

> **實際落地**：已照此修正。實作與上面草稿有一處差異——落在 repo 之外時回傳
> `None` 而不是空字串，因為空字串代表「repo 根目錄本身」，兩者必須分得開
> （`cd` 切到 repo 之外後，其下的相對路徑才不會被誤判成 repo 內的路徑）。
> 對應測試：`scripts/test-guards.py` 的 `P0-1 *` 案例（含「repo 之外應放行」的反向案例）。

---

### P0-2. 不指定 path 的搜尋會把隱藏測試內容撈進上下文【已驗證】✅ 由 P1-1 消解

`_check_read_tool()` 只檢查 `path` / `glob` / `pattern` 欄位「是不是指到
`tests/hidden/`」。但搜尋類工具**不指定 path 時預設搜整個 repo**：

| payload | 結果 | 後果 |
|---|---|---|
| `Grep {"pattern": "assert"}`（無 path） | **放行** ❌ | 隱藏測試的內容行直接進 implementer 上下文 |
| `Bash {"command": "grep -r assert ."}` | **放行** ❌ | 同上 |
| `Bash {"command": "rg assert tests/hidden"}` | **放行** ❌ | `rg` 根本不在 `READ_VIEW_VERBS` 裡 |
| `Bash {"command": "sed -n 1,50p tests/hidden/test_x.py"}` | **放行** ❌ | 沒有 `-i` 的 `sed` 只被當成「非寫入」，但它會印出內容 |

這比 P0-1 更難防，因為「搜尋全 repo」本身是完全正當的行為，不能一律擋。

**建議修法**（三選一或併用）：

1. **輸出層過濾**：改用 `PostToolUse` hook 檢查 `tool_response`，結果內容一旦出現
   `tests/hidden/` 的檔名就整包遮蔽（PreToolUse 看不到結果，做不到這件事）。
2. **要求顯式表態**：Pre 階段對沒有 `path` 的 `Grep`/`Glob` 直接擋下，
   錯誤訊息要求呼叫方明確指定搜尋範圍（例如 `path: "src"`）。
3. **釜底抽薪**：走 P1-1，讓 `tests/hidden/` 根本不在工作目錄裡，全 repo 搜尋自然搜不到。

同時把 `rg`、`fd`、`diff`、`sort`、`uniq`、`wc`、`cut`、`md5sum`、`sha256sum`
以及**沒有 `-i` 的 `sed`** 補進 `READ_VIEW_VERBS`。

---

### P0-3. Bash 分支有一整排結構性繞過【已驗證】🟡 影響範圍已由 P1-1 降級

`_check_bash_command()` 只看每個 segment 的**第一個 token** 是不是已知動詞，
所以換一種包裝就能穿過去：

| 指令 | 結果 | 原因 |
|---|---|---|
| `bash -c 'cat tests/hidden/test_x.py'` | **放行** ❌ | `bash`/`sh`/`zsh` 不在 `INLINE_INTERPRETER_VERBS`（只有 python/node/perl/ruby） |
| `find tests/hidden -name '*.py' -delete` | **放行** ❌ | `find` 既不是寫入動詞也不是讀取動詞 |
| `ls tests/hidden \| xargs rm` | **放行** ❌ | `xargs` 後面的真正動詞看不到 |
| `tar cf out.tar tests/hidden` | **放行** ❌ | `tar` 不在任何清單 |
| `echo $(cat tests/hidden/test_x.py)` | **放行** ❌ | command substitution 沒有被拆解 |
| `D=tests/hidden; cat $D/test_x.py` | **放行** ❌ | 變數展開沒被追蹤（`_strip_prefixes` 反而把 `D=...` 當前綴丟掉） |
| `git checkout HEAD -- tests/public/test_p.py` | **放行** ❌ | 只擋了 `git rm`，沒擋 `checkout`/`restore`/`stash`/`clean`/`apply` |

腳本模組說明有誠實寫「不是完整的 shell parser，目的是提高繞過成本」，這個取捨本身合理；
但目前的成本低到**一個沒有惡意、只是習慣用 `bash -c` 的子智能體就會自然穿過去**，
這就不只是取捨問題了。

**建議修法**：把邏輯從「動詞黑名單」翻轉成「路徑優先的保守判斷」——

- 只要 segment 的**任何一個 token**（含引號內字串）正規化後落在受保護路徑下，
  除非動詞在一份極短的白名單裡（例如專用的測試 runner），否則一律擋。
  這樣新增的指令預設是安全的，不必每次補黑名單。
- 至少先補上 `bash`/`sh`/`zsh`/`env`/`xargs`/`find`/`tar`/`zip`/
  `git checkout|restore|stash|clean|apply`，並在偵測到 `$(...)`、反引號、`${VAR}`
  時採保守擋下。

---

### P0-4. implementer 可以直接「執行」隱藏測試，把它當成 oracle【已驗證】✅ 由 P1-1 消解

```
Bash {"command": "python3 -m pytest tests/hidden -q"}   → 放行
```

這是目前設計的必然結果：`verifier-reviewer` 被明確要求「不要開檔，改用執行測試來
驗收」（`.claude/agents/verifier-reviewer.md`），而 hook **無法區分呼叫者**，
所以 implementer 也拿得到同一條路徑。

後果是：implementer 根本不需要「看到」隱藏測試，只要反覆執行、讀失敗訊息，
就能一步步把實作往隱藏測試的斷言逼近——這正是黃金法則第 1 條要防的事，
只是換成 query 形式。而且失敗訊息通常會直接印出期望值與實際值，等於逐行洩題。

**建議修法**：見 P1-1。這個問題無法靠 hook 的路徑比對解決，
必須改變隱藏測試的存放與執行方式。

---

### P0-5. hook 是 fail-open，而且掛法依賴 cwd ✅ 已修正

`.claude/settings.json` 的 hook 指令是相對路徑：

```json
"command": "python3 scripts/guard-hidden-tests.py"
```

只要發生以下任一情況，hook 會以非 2 的 exit code 結束，被視為 non-blocking error，
**工具照樣執行**：

- hook 的工作目錄不是 repo 根（找不到腳本，exit 127）
- 環境裡只有 `python` 沒有 `python3`（Windows 常見，exit 127）
- stdin 不是合法 JSON（腳本自己 `sys.exit(0)`）
- 腳本任何未預期例外（exit 1）

也就是說，整條防作弊機制**在故障時是全開的，而且沒有任何訊號告訴使用者它壞了**。

**建議修法**：

1. 指令改成 `python3 "$CLAUDE_PROJECT_DIR/scripts/guard-hidden-tests.py"`，並加 `timeout`。
2. 腳本內部用 `try/except` 包住 `main()`，**任何未預期例外一律 exit 2（fail-closed）**，
   訊息說明「防護腳本異常，為安全起見擋下本次操作」。
3. 補一個 `SessionStart` hook 自我檢查：確認 guard 腳本存在、可執行，
   並用一個已知該被擋的 payload 跑一次自我測試，失敗就在 session 一開始警告。
4. fail-open 無法完全消滅，務必同時做 P1-2 的事後雜湊稽核當第二道防線。

> **實際落地**：四點都已照做。第 3 點成為 `scripts/guard-selfcheck.py`，
> 掛在 `SessionStart`，實跑 6 個 payload（4 個該被擋、1 個 fail-closed、
> 1 個不該誤擋）並順帶回報鎖定稽核狀態；它一律以 exit 0 結束，只負責回報，
> 不阻止 session 開始。殘留限制：`python3` 根本不存在時 hook 不會被執行，
> 這種情況只有 P1-2 的事後稽核抓得到。

---

## P1 — 機制設計層級

### P1-1. 隱藏測試不該放在工作目錄裡（根本解）✅ 已修正

P0-1 到 P0-4 有同一個根因：**隱藏測試就躺在 implementer 的工作目錄裡，
只靠一支 hook 在幾十種路徑寫法之間攔截**。攻擊面是無限的，防守面是列舉的。

**建議改成實體隔離**：

- 隱藏測試存到 **repo 之外**（例如 `$CLAUDE_PROJECT_DIR/../.harness-hidden/<task_id>/`，
  或由 Orchestrator 指定的暫存目錄）。子智能體的檔案工具本來就以專案目錄為界，
  拿不到就是拿不到，不需要靠字串比對。
- 提供 `scripts/run-hidden-tests.py <task_id>` 作為唯一執行入口，且要求
  `HARNESS_VERIFY_TOKEN`（環境變數或 `--token`）；**權杖只由 Orchestrator 在派工
  `verifier-reviewer` 時放進該 session 的提示詞**，implementer 的提示詞裡沒有。
- runner 對外只輸出「通過數／失敗數／失敗的測試名稱」，**不輸出 assertion 的期望值
  與實際值**（完整版另存成只給 verifier 的報告檔）——即使權杖外洩，
  oracle 的資訊量也被壓到最低。
- `tests/hidden/` 在 repo 內只留一個 README，說明「這裡不放東西，見上述機制」。

這一步做完，P0-1／P0-2／P0-4 大部分自然消失，hook 退化成單純的第二道防線。

> **實際落地**：已照此修正，但有兩處刻意的差異：
>
> 1. **多了「加密」這一層**。原本的草稿只講「搬出工作目錄」，實作時發現這樣不夠——
>    implementer 手上有 Bash，可以自己寫一支 `leak.py` 去讀任何絕對路徑，這是攔不完的。
>    所以封存時內容會用權杖推導的金鑰加密（SHA-256 keystream XOR，只用標準函式庫），
>    直接讀到的是密文。**路徑因此不需要是秘密**，整個機制不再依賴 shell 指令解析。
>    這也是為什麼 P0-3 只能算「降級」而不是「解決」：它現在只影響封存前的暫存區。
> 2. **暫存區保留在 `tests/hidden/`**。verifier-test-writer 仍然把隱藏測試寫在原處，
>    寫完執行 `seal-hidden-tests.py` 才搬走——因為子智能體的 Write 工具同樣只能寫在
>    專案目錄內，沒有暫存區的話它根本沒地方產出檔案。
>
> 另外三點（runner 為唯一入口、權杖只給 verifier-reviewer、`tests/hidden/` 留 README）
> 都照做了。runner 的輸出沒有做「只給摘要」的裁切——verifier-reviewer 需要失敗訊息
> 才能寫驗收報告，而沒有權杖的人根本跑不動，裁切輸出保護不到任何東西。
>
> **殘留限制（誠實記載）**：解密後的明文在測試執行的短暫期間確實存在於一個
> 權限 0700 的暫存目錄，同一個使用者身分的行程掃得到。要完全消除需要獨立的
> 使用者/容器，超出本模板範圍。
>
> 對應測試：`scripts/test-vault.py`（19 案例）與 `scripts/test-guards.py` 的 `P1-1 *` 案例。

### P1-2. `lock-tests.py` 只記路徑，沒記內容雜湊 ✅ 已修正

`.harness/locked-tests.list` 一行一個路徑，沒有任何內容指紋。只要有一條繞過路徑
成功（P0-1、P0-3 已證明存在多條），公開測試被改了就**沒有任何人會發現**——
verifier-reviewer 無從知道它跑的公開測試還是不是原本那份。

**建議修法**：

- 鎖定清單改成 `<sha256>  <路徑>` 格式（或直接寫 JSON）。
- 新增 `scripts/verify-locks.py`：重算雜湊並比對，不符就以非 0 exit code 回報
  「公開測試在實作期間被竄改」。
- 在 `.claude/agents/verifier-reviewer.md` 的驗收步驟第 1 步之前加一條：
  「先跑 `verify-locks.py`，不符直接判定不通過」。

這是成本最低、CP 值最高的補強：不需要攔截任何東西，只在事後比對，
而且能兜住 hook fail-open（P0-5）造成的所有漏網之魚。

> **實際落地**：清單改為 `<sha256>  <路徑>`（比照 `sha256sum` 格式），
> `guard-hidden-tests.py` 兩種格式都讀得懂，舊清單仍能擋寫入。
> `verify-locks.py` 的 exit code：0 相符、1 竄改或遺失（驗收直接判不通過）、
> 2 無法驗證（沒有清單或是舊格式，要在報告裡註記）。
> 對應測試：`scripts/test-locks.py`（12 案例）。

### P1-3. 範例目錄下的 `tests/hidden/` 完全不受保護

`templates/examples/demo-fizzbuzz/tests/hidden/test_fizzbuzz_hidden.py` 被 commit 進 repo，
而 guard 的路徑比對只認 **repo 根目錄下的** `tests/hidden`，
所以這份範例隱藏測試任何人都讀得到（實測放行）。

問題不在範例本身（它就是要給人看的），而在**它示範了一個看起來受保護、
實際不受保護的目錄結構**——使用者照這個結構套進自己的子專案
（例如 monorepo 的 `packages/api/tests/hidden/`）時，會誤以為有保護。

**建議修法**：範例 README 明確標註「本目錄的 hidden 測試僅供閱讀示範，不受 hook 保護」；
同時把 P1-4 的「受保護路徑可設定」做出來，讓 monorepo 也能正確保護。

### P1-4. 受保護路徑寫死，與「不綁定語言」的宣稱衝突 ✅ 已修正

`HIDDEN_TESTS_PREFIX = "tests/hidden"` 是硬編碼的。但 `docs/multi-language-support.md`
說本模板支援 Go / Rust / TypeScript / Java——這些語言的測試慣例都不是 `tests/public`
與 `tests/hidden`（Go 的 `*_test.go` 放在同一個 package、JS 常見 `__tests__/`
或 `*.spec.ts`）。使用者一旦照自己語言的慣例放測試，保護就整個落空，
而且**不會有任何錯誤訊息告訴他**。

**建議修法**：新增可設定檔（例如 `harness.config.json`）：

```json
{
  "hidden_test_paths": ["tests/hidden", "packages/*/tests/hidden"],
  "public_test_paths": ["tests/public"],
  "test_command": "python3 -m pytest"
}
```

`guard-hidden-tests.py`、`lock-tests.py`、runner 都讀同一份設定；
找不到時退回目前預設值，並由 `init.py` 提示使用者依語言調整。

> **實際落地**：設定檔叫 `harness.config.json`（放專案根目錄、**會進版控**，
> 因為它描述的是這個專案的慣例，跟 `.harness/` 的當下狀態性質不同），
> 欄位是 `public_test_paths` / `hidden_test_paths` / `hidden_test_command`，
> 路徑支援 monorepo 用的 `*` 萬用字元。另外多做了三件草稿沒提到、但更關鍵的事：
>
> 1. **設定錯誤 fail-closed，不退回預設值**。欄位名打錯、路徑寫成字串、
>    指到 repo 之外、把整個 repo 設成受保護路徑——全部擋下並說明原因。
>    「靜靜退回預設值」等於讓一個 typo 就能關掉防護，那正是本項要修的問題本身。
> 2. **偵測「有測試但沒被保護」**。`guard-selfcheck.py` 在設定的測試目錄一個都
>    不存在時，會掃描專案裡常見的測試檔名慣例（`*_test.go`、`*.spec.ts`、
>    `test_*.py`…），發現就明確警告「這些測試完全不受保護」。
>    P1-4 真正的痛點是**沉默失效**，光是「可以設定」不夠，還要在沒設定時會叫。
> 3. **修掉一個順帶抓到的真 bug**：`unittest discover` 不會遞迴進沒有
>    `__init__.py` 的子目錄，遇到就會「Ran 0 tests」但 exit 0——而
>    `verifier-reviewer` 會把 exit 0 讀成「隱藏測試全過」，等於一個什麼都沒驗到的
>    實作直接通過驗收。runner 現在會偵測各家 runner 的零測試輸出並判定不通過，
>    並印出解密的檔案數供對照。（Python 3.13 起 unittest 自己會回非 0，
>    但 3.12 以前不會，所以這個兜底有必要。）
>
> 為了讓設定只有一份驗證邏輯，`guard-hidden-tests.py` 這次選擇 import
> `harness_config` 而不是內嵌——但過程中發現 module 層的例外跑在頂層 try/except
> **之外**，會變成 exit 1（放行）。這個 fail-open 是 `test-config.py` 的
> fail-closed 案例當場抓出來的，已改成把啟動錯誤記下來、在 `main()` 開頭轉成 exit 2。
>
> 對應測試：`scripts/test-config.py`（24 案例）。

---

## P2 — 流程與文件一致性

### P2-1. `verifier-reviewer` 沒有 `Write` 工具，卻被要求「填寫範本」

`verifier-reviewer.md` 與 `verifier-security.md` 的「產出」章節都寫
「填寫 `templates/verification-report-template.md`」，但兩者的 `tools` 只有
`Read, Glob, Grep, Bash`——它**寫不了檔案**。`docs/memory-management.md` §2 明講
這是刻意設計（檢驗者只讀不寫治理性資訊），所以不是工具設定漏掉，而是**文件用詞誤導**。

**建議修法**：改成「**依範本格式把驗收報告寫在你的回覆裡**，交回 Orchestrator，
由 Orchestrator 落檔到 `reports/<task_id>-verification.md`」，
並在 `orchestrator.md` §7 補上「落檔」這個動作（目前只說「收集」）。

### P2-2. 缺少「驗收標準對照表」範本

`verifier-test-writer.md` 要求交付三樣東西，第 3 樣是「驗收標準對照表」，
而 `verifier-reviewer` 的驗收**完全依賴這份表**（因為它讀不到隱藏測試原始碼）。
但 `templates/` 底下只有 task-spec 與 verification-report 兩個範本，
這份關鍵文件沒有格式規範，每次產出的長相都會不一樣。

**建議修法**：新增 `templates/acceptance-mapping-template.md`，
欄位至少包含：驗收標準編號、標準內容（引自 task-spec）、對應公開測試、
對應隱藏測試「名稱」（不寫內容）、判定方式；另加兩節——
「不可被 mock 繞過的測試」與「本表刻意不記載的資訊（斷言內容、期望值）」，
避免對照表本身變成洩題管道。

### P2-3. 子智能體 frontmatter 的 `thinking:` 欄位不會生效

七個 agent 定義檔都有 `thinking: high|medium`。Claude Code 的 subagent frontmatter
認得的是 `name` / `description` / `tools` / `model`，`thinking` 會被當成未知欄位忽略。
`docs/model-thinking-matrix.md` 已經誠實註明「這是本模板內部的思考力度慣例」，
但**沒有任何機制把這個慣例變成實際效果**——agent 的提示詞本文裡也沒有任何一句
要求展開推理。

**建議修法**：保留 frontmatter 欄位當文件標註，但在每個 agent 的**提示詞本文**
補上對應強度的實際指示。例如 verifier-reviewer 開頭加：

> 這是高風險的驗收任務，下結論前請**逐條展開推理**：先列出所有可疑點，
> 對每一點分別論證「這是作弊」與「這是合理實作」兩種可能，再做判定。

L1 機械型任務則相反，明講「不需要冗長推理，直接依規格產出」。

### P2-4. hook matcher 靠 regex 巧合命中，不夠明確 ✅ 已修正

`"matcher": "Edit|Write|Bash|Read|Grep|Glob"` 目前擋得到 `MultiEdit` 與 `NotebookEdit`
（實測有擋），但那是因為這兩個名字**剛好包含子字串 `Edit`**。
`guard-hidden-tests.py` 裡明確寫了 `NotebookEdit` 的處理邏輯，代表作者是打算涵蓋它的，
卻沒寫進 matcher——這個涵蓋是意外達成的，哪天 matcher 改成錨定形式
（`^(Edit|Write)$`）就會無聲失效。

**建議修法**：matcher 明確列全
`"Edit|MultiEdit|Write|NotebookEdit|Bash|Read|Grep|Glob"`，
並在 `scripts/test-guards.py` 補一個案例，直接讀 settings.json 的 matcher
去比對腳本涵蓋的工具清單，確保兩邊不再漂移。

### P2-5. 文件與實際檔案結構不同步

- `CLAUDE.md` §4 目錄結構沒列出 `tests/`（public/hidden 是整個機制的核心目錄），
  也沒有 `templates/examples/`。
- `CLAUDE.md` §1 的步驟編號對不上：內文說「步驟 6 之後彙整」，
  但 `orchestrator.md` 的彙整是第 7 步。
- `scripts/test-guards.py` 是這個 repo 唯一的自動化測試，`README.md` 的目錄導覽
  與 `.claude/settings.json` 的 allow 清單**都沒提到它**，等於預設沒人會跑它。
- `README.md` 沒說明 `/task-plan` 與 `/task-dispatch` 的使用時機差異，建議各補一行。

---

## P3 — 可用性與工程基礎建設

### P3-1. `env-guard.py` 在容器／雲端環境會每次誤報 ✅ 已修正

指紋只有 `hostname` / `os` / `arch`。Docker、Kubernetes 以及 Claude Code 的遠端執行環境，
**每次啟動 hostname 都是隨機的**——代表在這些環境下 `env-guard.py` 每次都回報「不符」、
`init.py` 每次都以 exit 1 收場，而黃金法則第 6 條要求 Orchestrator 一律停下來問使用者。

結果會是：使用者被問到麻木，養成「忽略這個警告」的習慣，
守門機制實質失效——比沒有這個機制更糟。

**建議修法**：

- `machine-profile.py` 已經有 `get_container_hint()`，把結果一併寫進指紋。
- 判定為容器環境時，**hostname 不參與比對**，改比對 CPU 核心數級距、
  總記憶體級距、`os`/`arch` 這些不隨容器重建而變的特徵。
- 加 `--update` 旗標，讓使用者確認後可用一行指令更新基準指紋，
  不必手動編輯 `.harness/env-fingerprint.json`（該檔目前還被 hook 擋著不能編輯）。

> **實際落地**：三點都照做了，另外把判斷規則講得更精確：
>
> - **指紋分成「身分」與「能力」兩類**。身分類只有 `hostname`，在容器／K8s／
>   Codespaces／CI 這類環境不參與比對（只記錄下來當參考）；能力類是
>   `os`／`arch`／`container`／CPU 級距／記憶體級距，一律比對——**那才是黃金法則
>   第 5 條真正在意的東西**，Orchestrator 要靠它們決定平行度。
> - **CPU 與記憶體用級距而不是精確值**。雲端同規格機器的記憶體回報值會浮動
>   （7.8GB / 8.0GB），精確比對只會變成另一種誤報來源；跨級距才代表能力真的變了。
> - **容器判斷放寬**成「hostname 是否穩定」：除了 `/.dockerenv`、`/run/.containerenv`
>   與 cgroup 標記，另外看 `KUBERNETES_SERVICE_HOST`、`CODESPACES`、`GITHUB_ACTIONS`
>   等環境變數——K8s 與 Codespaces 不一定命中 `/.dockerenv`。
> - 順帶把 `env-guard.py` 的路徑基準改成 `CLAUDE_PROJECT_DIR`（原本是相對路徑，
>   工作目錄不對就會讀寫到別的地方）。
>
> 為了讓 `env-guard.py` 拿得到 CPU／記憶體／容器這些事實，把 `machine-profile.py`
> 裡的事實蒐集函式抽成 `scripts/machine_facts.py`；比對規則另外抽成純函式模組
> `scripts/env_fingerprint.py`，測試才能直接用合成資料測邊界，不必想辦法偽造機器。
>
> 對應測試：`scripts/test-env-guard.py`（16 案例），兩個方向都測——
> **該擋的要擋**（換 OS／架構、跨級距、實體機搬進容器）、
> **不該吵的不能吵**（容器換 hostname、記憶體同級距浮動、舊版指紋檔）。

### P3-2. 掃描腳本應提供機器可讀輸出

三支掃描腳本目前只印中文文字，Orchestrator 得靠讀自然語言決定平行度，
每次判斷可能不一致。特別是 `machine-profile.py` 只給了一句經驗法則
（「平行數 <= CPU 核心數，每個重型任務預留 1-2GB」），**沒有把數字算出來**。

**建議修法**：三支腳本都加 `--json`，由 `machine-profile.py` 直接算出結論：

```json
{
  "cpu_count": 8,
  "memory_mb": 16384,
  "container": true,
  "recommended_parallel_agents": 4,
  "recommended_parallel_heavy_tasks": 2,
  "occupied_ports": [5432, 6379],
  "suggested_port_range": [8100, 8199]
}
```

Orchestrator 直接讀欄位，不用解析中文；順便寫一份 `.harness/last-scan.json` 當快取，
避免黃金法則第 5 條「每個任務前都要掃描」在多 task 專案裡重複跑 N 次。

### P3-3. 沒有 CI ✅ 已修正

`scripts/test-guards.py` 寫得很完整（30 個案例、涵蓋歷史繞過手法），
但**沒有任何自動化在跑它**。這對一個「防作弊機制」的 repo 特別危險：
機制退化了不會有人知道——本報告 P0 的一整批繞過就是活生生的例子。

**建議修法**：加 `.github/workflows/ci.yml`：

```yaml
name: CI
on: [push, pull_request]
jobs:
  guards:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.9", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ matrix.python-version }}" }
      - run: python3 scripts/test-guards.py
      - run: python3 scripts/machine-profile.py
      - run: python3 scripts/service-scan.py
```

三支掃描腳本都宣稱「Win/Linux/Mac 通用」，但目前沒有任何一次實際在 Windows 上被跑過；
若要維持這個宣稱，`runs-on` 應再開 `windows-latest` / `macos-latest`。

> **實際落地**：已新增 `.github/workflows/ci.yml`，比上面的草稿再往前一步：
>
> - **開了 OS matrix**（ubuntu / windows / macos）。既然文件宣稱三平台通用，
>   就該真的跑過，否則那句宣稱只是願望。`defaults.run.shell: bash` 讓三個平台
>   共用同一組指令（Windows runner 內建 Git Bash）。
> - **Python 版本取 3.9 與 3.13 兩個邊界**；3.10–3.12 已在本地逐版驗證過。
> - **`fail-fast: false`**：一個組合壞掉時其他組合仍要跑完，才看得出來是單一平台
>   的問題還是全面性的問題。
> - 除了三組回歸測試，另外跑 `init.py` 與 `guard-selfcheck.py` 的 smoke，
>   以及範例流程的重跑。
>
> 為了讓 CI 能在 Windows 上跑，順手修掉一個真正的可攜性 bug：隱藏測試的預設
> 執行指令原本寫死 `python3`，而 Windows 上常常只有 `python.exe`。現在改成
> `{python}` placeholder（代換成 `sys.executable`），而且是**先斷詞再代換**——
> 反過來的話，路徑裡只要有空白（`C:\Program Files\...`）就會被 `shlex` 拆成兩個參數。
>
> 同一輪也補上兩個「設定與程式碼漂移」的測試案例（P2-4 的後續）：
> matcher 是否涵蓋 guard 實際處理的每一種工具、hook 指令是否都用了
> `$CLAUDE_PROJECT_DIR` 絕對路徑。
>
> **CI 開起來的第一次執行就抓到一個真 bug**，正好證明這一項的價值：
> Linux 與 macOS 全綠，**兩個 Windows job 立刻紅**，錯誤是
> `UnicodeEncodeError: 'charmap' codec can't encode characters`。
> 原因是本模板所有腳本都印繁體中文，而 Python 在 Windows 上預設用系統 ANSI
> 代碼頁（英文 cp1252、繁中 cp950）編 stdout——**每一支腳本一 print 就崩潰**。
> 這個 bug 從第一天就存在，只是 README 與各腳本都寫著「Win/Linux/Mac 通用」，
> 卻從來沒有真的在 Windows 上跑過。
>
> 其中最危險的是 `guard-hidden-tests.py`：它擋下動作時要先印理由再 exit 2，
> 如果 print 先崩潰，exit code 會變成 1——而 Claude Code 只把 2 當成 blocking
> error，1 是 non-blocking，**工具照樣執行**。換句話說在 Windows 上，
> 「擋下」會悄悄變成「放行」。
>
> 修法：新增 `scripts/utf8_output.py`，所有入口腳本啟動時把 stdout/stderr
> 切成 UTF-8；`guard-hidden-tests.py` 因為是 hook（多一個 import 就多一個
> 失敗點），改成內嵌同樣的邏輯，並讓 `_block()` 在連理由都印不出來時退回
> 純 ASCII 訊息——但無論如何都還是 exit 2。
>
> 對應測試：`scripts/test-guards.py` 用 `PYTHONIOENCODING=cp1252` 在任何平台
> 重現這個情境，斷言 (1) guard 在舊代碼頁下擋人時仍然 exit 2、
> (2) 所有入口腳本都不會因為印中文而崩潰。

### P3-4. `.claude/settings.json` 的 deny 規則說服力不足

```json
"deny": ["Bash(rm -rf *)", "Bash(git push --force*)", "Bash(*DROP TABLE*)"]
```

這幾條是前綴／萬用字元比對，`rm -fr`、`cd x && rm -rf .`、`git push -f`、
小寫 `drop table` 都不會命中。當作「宣示意圖」沒問題，但不該被當成安全機制——
而 `verifier-security.md` 檢查清單第 5 條（「是否有防止 `rm -rf`、強制推送、
清空資料庫等破壞性操作」）正好會讓人以為這裡是有效的。

**建議修法**：在 `settings.json` 的 `$comment` 或 README 標註
「deny 清單是意圖宣示與第一層過濾，不是完整防護；真正的破壞性操作防護
靠 permission mode 與人工核准」，並把 `verifier-security.md` 第 5 條改成
「檢查程式碼**本身**有沒有發出破壞性指令」，而不是檢查有沒有被 deny 清單擋住。

### P3-5. 其他小項

- `lock-tests.py` / `env-guard.py` 的 `mkdir(exist_ok=True)` 沒有 `parents=True`，
  目前因為 `.harness` 只有一層而正常，一旦 P1-4 支援巢狀路徑就會壞。
- `.harness/` 被 `.gitignore` 排除是對的，但代表 **CI 與新 clone 的環境沒有鎖定清單**，
  guard 只剩 `tests/hidden` 保護。建議 `verify-locks.py`（P1-2）找不到清單時
  明確警告「本次驗收沒有公開測試鎖定保護」，而不是靜默略過。
- `service-scan.py` 的 fallback 在 Linux 非 root 下 `ss -tulpn` 拿不到 pid，
  輸出會缺行程名稱；建議偵測到後明確提示「建議以 sudo 重跑以取得完整資訊」。
- `implementer-*.md` 的停損規則（同一條驗收標準連續失敗 ≥3 次回報 blocked）寫得很好，
  但**沒有任何機制記錄「失敗了幾次」**，實務上靠子智能體自己數。建議由 runner（P1-1）
  把每個 task 的執行次數寫進 `.harness/attempts.json`，讓 Orchestrator 有客觀依據，
  而不是相信 implementer 的自我申報。

---

## 建議的處理順序

| 順序 | 項目 | 理由 |
|---|---|---|
| 1 | P1-2 雜湊稽核 + P0-5 fail-closed | 成本最低，且立刻讓「機制壞掉」變成看得見 |
| 2 | P0-1 路徑正規化統一 | 一個函式修掉六種繞過，改動範圍小 |
| 3 | P3-3 CI | 讓後續每一次修補都有回歸保護 |
| 4 | P1-1 隱藏測試移出工作目錄 | 根本解，但改動大，需要先有 CI 兜底 |
| 5 | P0-2 / P0-3 / P0-4 剩餘繞過 | P1-1 做完後範圍會大幅縮小，屆時再收尾 |
| 6 | P2 系列文件一致性 | 不影響安全性，但影響使用者對機制強度的信任校準 |
| 7 | P3-1 / P3-2 可用性 | 讓機制在真實環境（容器）裡不會被使用者習慣性忽略 |

---

## 附錄 A：重現方式

以下腳本建立一個最小情境並逐一送出 payload，印出每個案例是「擋下」還是「放行」。
修好 P0 之後這些案例應該全部變成「擋下」，並整批移進 `scripts/test-guards.py`。

```python
import json, subprocess, sys, tempfile, os
from pathlib import Path

GUARD = Path("scripts/guard-hidden-tests.py").resolve()
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "tests/hidden"), exist_ok=True)
os.makedirs(os.path.join(tmp, "tests/public"), exist_ok=True)
os.makedirs(os.path.join(tmp, ".harness"), exist_ok=True)
Path(tmp, "tests/hidden/test_x.py").write_text("assert 1")
Path(tmp, "tests/public/test_p.py").write_text("assert 1")
Path(tmp, ".harness/locked-tests.list").write_text("tests/public/test_p.py\n")

def run(payload):
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          text=True, capture_output=True, cwd=tmp).returncode

PROBES = [
    ("P0-1 Read 絕對路徑",        {"tool_name": "Read", "tool_input": {"file_path": tmp + "/tests/hidden/test_x.py"}}),
    ("P0-1 Read ./ 前綴",         {"tool_name": "Read", "tool_input": {"file_path": "./tests/hidden/test_x.py"}}),
    ("P0-1 Read 雙斜線",          {"tool_name": "Read", "tool_input": {"file_path": "tests//hidden/test_x.py"}}),
    ("P0-1 Read ../ 迂迴",        {"tool_name": "Read", "tool_input": {"file_path": "tests/../tests/hidden/test_x.py"}}),
    ("P0-1 Write 絕對路徑改鎖定", {"tool_name": "Write", "tool_input": {"file_path": tmp + "/tests/public/test_p.py"}}),
    ("P0-2 Grep 無 path",         {"tool_name": "Grep", "tool_input": {"pattern": "assert"}}),
    ("P0-2 grep -r .",            {"tool_name": "Bash", "tool_input": {"command": "grep -r assert ."}}),
    ("P0-2 rg 搜 hidden",         {"tool_name": "Bash", "tool_input": {"command": "rg assert tests/hidden"}}),
    ("P0-2 sed -n 印出",          {"tool_name": "Bash", "tool_input": {"command": "sed -n 1,50p tests/hidden/test_x.py"}}),
    ("P0-3 bash -c",              {"tool_name": "Bash", "tool_input": {"command": "bash -c 'cat tests/hidden/test_x.py'"}}),
    ("P0-3 find -delete",         {"tool_name": "Bash", "tool_input": {"command": "find tests/hidden -name '*.py' -delete"}}),
    ("P0-3 xargs rm",             {"tool_name": "Bash", "tool_input": {"command": "ls tests/hidden | xargs rm"}}),
    ("P0-3 tar 打包",             {"tool_name": "Bash", "tool_input": {"command": "tar cf out.tar tests/hidden"}}),
    ("P0-3 command substitution", {"tool_name": "Bash", "tool_input": {"command": "echo $(cat tests/hidden/test_x.py)"}}),
    ("P0-3 變數展開",             {"tool_name": "Bash", "tool_input": {"command": "D=tests/hidden; cat $D/test_x.py"}}),
    ("P0-3 git checkout 還原",    {"tool_name": "Bash", "tool_input": {"command": "git checkout HEAD -- tests/public/test_p.py"}}),
    ("P0-4 直接跑隱藏測試",       {"tool_name": "Bash", "tool_input": {"command": "python3 -m pytest tests/hidden -q"}}),
]

for name, payload in PROBES:
    print(("擋下  " if run(payload) == 2 else "放行!!") + " | " + name)
```

2026-09-10 修正前的實測結果：**上述 17 個案例全部放行**。
對照組（`Read {"file_path": "tests/hidden/test_x.py"}` 這種相對路徑寫法）有正確擋下，
`scripts/test-guards.py` 既有的 30 個案例也全部通過——
問題不在既有測試寫錯，而在測試涵蓋的攻擊面不夠寬。

**修正後（P0-1／P0-5 落地）再跑一次**：

- P0-1 的 5 個案例：全部擋下 ✅（另加 `Grep` 用絕對路徑指 `path`，也擋下）
- P0-5 的 3 種看不懂的輸入（非 JSON、空、`null`）：全部擋下 ✅
- P0-2 / P0-3 / P0-4 的案例：**仍然放行**，屬預期範圍（尚未實作）
- 反向對照（讀 `README.md`、讀已鎖定的公開測試、讀 repo 之外的路徑）：正確放行 ✅

這批案例已經整批移進 `scripts/test-guards.py`（目前 52 案例）、
`scripts/test-locks.py`（12 案例）與 `scripts/test-vault.py`（19 案例），
改動機制後直接跑這三支即可（CI 也會跑）。
