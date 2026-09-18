<!-- 語言 / Language: **繁體中文** -->

# 變更紀錄（Changelog）

本檔案記錄 **harness-template 本身**的版本演進——不是模板產出的專案。
產出專案的版本由 `scripts/version.py` 管理，規則見 [`docs/versioning.md`](docs/versioning.md)。

格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，
版本號遵循[語意化版本](https://semver.org/lang/zh-TW/)。

## [未發佈]

（尚無變更）

## [0.2.0] - 2026-09-18

### 新增

- **依用量重置時間的停止／恢復閘門**（`scripts/usage-gate.py`）：Orchestrator 在派
  下一個 task 前查一次，任一用量視窗（5h／7d）接近門檻（預設 90%）就**停在乾淨的
  任務邊界**（上一個 task 已 commit），把 `resets_at` 記成 `resume_at`；到重置時間
  被動自動放行，另附 `--resume-at` 給主動喚醒（cron／at／代管排程）。
- **用量狀態列腳本**（`scripts/statusline-usage.py`）：把 Claude Code 餵給狀態列的
  `rate_limits` 寫進 `~/.claude/usage-snapshot.json` 當閘門的資料來源，`rate_limits`
  缺席時回退讀快取。
- 用量閘門的 15 案回歸測試（`scripts/test-usage-gate.py`）；接線寫進 `CLAUDE.md` §5、
  `.claude/agents/orchestrator.md` 派工步驟、`docs/token-strategy.md` §3.7。

### 設計取捨（誠實記載）

- 用量閘門是**最佳化不是安全控制**：拿不到用量資料時 **fail-open（放行並出聲）**，
  與隱藏測試的 fail-closed 明確區分。超門檻但缺 `resets_at` 的視窗不據此暫停
  （無法自動恢復）。

## [0.1.0] - 2026-09-16

首個公開版本。四輪審視全部落地，防作弊機制以「機制取代自律」為核心
（見 `README.md` 的「核心設計」與 `docs/history/README.md`）。

### 新增

- 實作者／檢驗者分離的獨立驗證鏈：不共用上下文的 sub-agent，檢驗者先寫測試才准實作
- 隱藏測試加密封存（逐檔獨立金鑰）＋一次性執行權杖，暫存區封存後即清空
- 公開測試鎖定與事後 sha256 稽核；清單本身的 sha256 也進簽章範圍
- 驗收用的程式碼一起封存到 repo 之外，每個檔案 sha256 進簽章——改 repo 裡的副本不影響驗收
- CI 驗收：把隱藏測試執行搬到 GitHub Actions（`protection.ci_verification`），跨越同 OS 使用者的結構性上限
- 保護等級可選：`full`（四層）／`minimal`（只留實體隔離）（`protection.level`）
- 產出專案的版本號管理與 `mirrors` 一致性檢查、版本歸檔 `releases/<x.y.z>/`（`scripts/version.py`）
- 機器效能／既有服務／環境指紋掃描（`scripts/init.py`、`--json`）
- 執行時間預測、skill 安裝決策、嘗試次數簽章等決策輔助工具
- 一鍵設定精靈與 60 秒 quickstart（`scripts/setup.py`）
- 中英雙語 README 與上手文件
- 十四組回歸測試守著各機制不被無聲改壞

### 已知限制（誠實記載）

- 本機驗收**擋不住**「決心繞過、且與 verifier 共用同一個 OS 使用者」的子智能體；
  要跨過這個結構性上限請開 CI 驗收（見 `docs/ci-verification.md`）。
- 封存加密為 SHA-256 keystream XOR（**非 AES**），屬「防非刻意洩題」等級、
  **不是機密等級**——勿在隱藏測試中放真正敏感資料（見 `README.md`「它擋什麼、不擋什麼」）。
- 實作者與檢驗者同為 Claude 時，對規格的相同誤解可能是相關的；隱藏測試防作弊、
  但防不了雙方共有的誤解。

## 如何發佈新版本

harness-template 自己的版本號放在 `VERSION`，並鏡像於 `README.md`
（由 `scripts/version.py` 一起更新、一起驗一致）。發佈一個新版本的步驟：

1. 把本檔案「未發佈」段落的內容移到新的版本標題底下，補上日期。
2. 升版並同步所有顯示位置：

   ```bash
   python3 scripts/version.py --bump <patch|minor|major>
   ```

3. commit 之後打 tag 並在 GitHub 開 Release（tag 內容取自本檔案對應段落）：

   ```bash
   git tag -a v<x.y.z> -m "harness-template v<x.y.z>"
   git push origin v<x.y.z>
   ```

   接著在 GitHub 的 Releases 頁面依此 tag 建立 Release。

> 這裡刻意用「原始碼裡就寫著自己是哪一版」而非從 tag 反推版本——理由同
> `docs/versioning.md` §6：使用者手上那份不一定是從 tag 裝的。tag 與 Release
> 是發佈動作的產物，不是版本的真相來源。

[未發佈]: https://github.com/oilotaku/harness-template/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/oilotaku/harness-template/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/oilotaku/harness-template/releases/tag/v0.1.0
