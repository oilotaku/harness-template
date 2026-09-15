<!-- 語言 / Language: **繁體中文** · [English](ci-verification.en.md) -->

# CI 驗收：把驗收搬出 implementer 的執行環境

> 對應第四輪 P0。這是本模板目前唯一一個**不在加層**的改動——
> 前三輪都在同一條軸線上（再簽一層章、再封一層存），這一輪換軸。

## 1. 為什麼加層已經沒有報酬了

第三輪的誠實記載寫著這句話：

> 如果被換掉的是封存版的 `run-hidden-tests.py` 自己、而且換掉的版本把自我比對
> 整段拿掉，就沒有人會執行那段檢查——任何自我檢查都有這個循環。

這不是「還沒修好的一項」，是**那條軸線的結構性終點**。只要驗收與被驗收跑在
同一台機器、同一個 OS 使用者底下，最後一定會停在同一個地方：
負責檢查的那段程式碼，跟被檢查的那一方在同一個可寫的世界裡。

所以第四輪不再加第五層。它把驗收移到一個 implementer 的工具構不到的地方：
**GitHub Actions**。不同的機器、不同的使用者、沒有共用的 transcript，
而且 workflow 檔案取自預設分支——這一次，「換掉檢查自己那一份」這個動作
從一開始就不存在。

## 2. 它怎麼運作

```
verifier-test-writer
  └─ seal-hidden-tests.py            ← 加密封存（跟以前一樣）
       └─ export-sealed-task.py      ← 匯出密文包到 ci/sealed/<task_id>/
            │                           （*.enc + 簽過章的 manifest 項目）
            ▼
       人：把 ci/sealed/ commit 進**預設分支**
       人：把權杖存進 repository secret `HARNESS_VERIFY_TOKENS`
            │
            ▼
implementer 在自己的分支上實作 → 開 PR
            │
            ▼
.github/workflows/verify-hidden-tests.yml（**預設分支上那一份**）
  ├─ checkout base（預設分支）→ base/   ← 可信：密文包與驗收腳本
  ├─ checkout head（PR 分支）→ work/    ← 受測對象，不帶 checkout 憑證
  └─ python base/scripts/ci-verify.py --repo base --work-dir work
       ├─ 驗權杖指紋、驗 manifest 簽章
       ├─ 解密到 work/ 之外的暫存目錄
       ├─ 執行測試（子行程拿不到權杖與 CI 的 token）
       ├─ job log：只有 exit code 與數量摘要
       └─ report.enc：完整明細，用權杖加密成 artifact
            │
            ▼
verifier-reviewer：下載 artifact → read-ci-report.py --token <權杖>
```

三件事決定了它的強度，缺一不可：

1. **`pull_request_target`**：GitHub 執行的是預設分支上的 workflow 檔案，
   不是 PR 分支上的那一份。改成 `pull_request` 整個設計就垮了——那會讓
   implementer 用一行 `run: exit 0` 通過驗收。
2. **密文包與驗收腳本都取自 base checkout**。PR 的程式碼只被當成受測對象，
   不提供任何一段會被執行的驗收邏輯。
3. **權杖是 repository secret，而且不流進被測程式碼的環境變數**
   （`scripts/ci-verify.py` 的 `scrub_env`，有回歸測試守著）。

## 3. 怎麼設定

### 3.1 宣告

`harness.config.json`：

```json
{
  "protection": { "ci_verification": true }
}
```

宣告之後，`seal-hidden-tests.py` 會在封存後順手匯出密文包，
而 `guard-selfcheck.py` 會在「宣告了 CI 驗收但密文包是空的」時警告——
那個漂移的可怕之處在於它安靜：CI 上會因為「沒東西可驗」而回傳成功，
跟「全部通過」長得一模一樣。

### 3.2 每個 task

```bash
# 1. 封存（宣告過 ci_verification 的話會自動做第 2 步）
python3 scripts/seal-hidden-tests.py --task-id T-001

# 2. 匯出密文包（手動時）
python3 scripts/export-sealed-task.py --task-id T-001 --token <權杖>

# 3. 把密文包 commit 進預設分支
git add ci/sealed/T-001 && git commit -m "封存 T-001 的隱藏測試密文包"
```

### 3.3 權杖

存成 repository secret，名稱 `HARNESS_VERIFY_TOKENS`，每行一個 task：

```
T-001 3f9a...c2
T-002 8b1d...07
```

位置：Settings → Secrets and variables → Actions → New repository secret。
更新是**整份覆寫**，記得把其他 task 那幾行一起帶上。

### 3.4 分支保護

**這一步不是選用的。** 整個設計的信任根是「預設分支上的 workflow 與密文包，
implementer 動不到」。如果預設分支誰都能直接推，那句話就不成立。
至少要開：預設分支不可直接 push、合併前必須通過 PR。

## 4. 新的信任根是什麼

老實說出來比較好：這個作法沒有消滅信任，它把信任**換了一個對象**。

| 以前信任 | 現在信任 |
|---|---|
| 封存版 runner 的 sha256 自我比對 | GitHub 會執行預設分支上的 workflow |
| 「implementer 寫不到 repo 之外」 | 「implementer 推不進預設分支」（分支保護） |
| 本機檔案系統的權限 | GitHub 的 secret 儲存 |

換得值不值得？值得，理由是**這些信任對象都不在 implementer 的工具範圍內**，
而前一輪那些是。他有 Bash，就寫得到本機任何路徑；他沒有預設分支的推送權，
也沒有 repo 的 admin 權限。

## 5. 它擋什麼、不擋什麼（誠實版本）

### 擋得住的

- **改寫驗收程式碼**：驗收跑的是 base checkout 的 `scripts/ci-verify.py`。
  PR 分支上那一份改成什麼樣子都不參與。
- **改寫 workflow**：`pull_request_target` 讀的是預設分支那一份。
- **改寫密文包或 manifest 項目**：簽章由權杖推導，implementer 沒有權杖。
- **從 job log 反推題目**：log 只有 exit code 與數量；明細加密成 artifact。
  這一條有回歸測試盯著（失敗的 assert 訊息不可以出現在 stdout/stderr）。
- **fork PR 偷權杖**：`pull_request_target` 對 fork 的 PR 一樣給得到 secret，
  所以 workflow 限定同源分支，fork 的 PR 直接不跑驗收。

### 擋不住的（不要當成已解決）

- **被測的程式碼跟解密後的明文在同一個 job 裡。** 它本來就得跑得到那些測試，
  所以它讀得到、也可以把內容送到外面去。CI runner 有完整的對外網路。

  這一條買到的不是「做不到」，是**「做了會留下 commit」**：那段外洩程式碼必須
  存在於 PR 的 diff 裡，被 review 看到、被 git 永久記錄。本機驗收沒有這個性質——
  在本機，同樣的事情做完之後檔案可以刪掉，什麼都不剩。

- **能改預設分支的人 = 能拿到權杖。** 加一支 workflow 把 secret 印出來就行了。
  這不是這套機制的漏洞，是 GitHub Actions 的基本性質：**secret 的安全性等於
  預設分支的寫入權限的安全性**。所以 §3.4 的分支保護是必要步驟，不是建議。

- **repo 的 admin 同樣拿得到權杖。** 他可以改分支保護。如果你的威脅模型包含
  「repo 擁有者自己作弊」，那 CI 驗收解決不了——那需要一個他控制不了的第三方。

- **驗收變慢。** 從「跑一個指令、幾秒鐘」變成「推一次、等 CI、下載 artifact、
  解密」。一輪大概多幾分鐘。這是真實成本，不要假裝沒有。

- **secret 要管理。** 每個 task 一行，task 做完要記得清掉。忘了清不會造成
  安全問題（那個 task 的密文包還在也只是佔空間），但會愈積愈長。

- **本機那一份 `run-hidden-tests.py` 仍然可以被改寫。** 它現在的定位是
  「verifier 的第二意見」，不是驗收依據。第三輪的所有保護（封存版執行、
  sha256 比對）都還在，但驗收的權威來源已經是 CI。

## 6. 跟本機驗收的關係

開了 CI 驗收之後，本機的 `run-hidden-tests.py` 不會消失，它換了角色：

| | 本機 | CI |
|---|---|---|
| 誰跑 | verifier-test-writer（基線）、verifier-reviewer（第二意見） | GitHub Actions |
| 算不算正式驗收 | 否 | **是** |
| implementer 能不能影響 | 能（同一台機器、同一個使用者） | 不能 |
| 回饋速度 | 秒 | 分鐘 |

基線執行（`--baseline`）仍然在本機做：它證明的是「這份隱藏測試在沒有實作時是紅的」，
那時還沒有 implementer，不存在對抗關係。

驗收報告要寫的是 CI 那一跑的結果，並註明 workflow 的 run id。
`read-ci-report.py` 解出來的明細**不可以**貼進報告——那裡面有隱藏測試的
失敗訊息，而驗收報告是 implementer 讀得到的。
