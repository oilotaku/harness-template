#!/usr/bin/env python3
"""timing.py — 執行時間預測與校準（純函式 + 一份小紀錄檔）

為什麼要有這支：Orchestrator 拆解任務時必須決定「這輪做幾個」「要不要分批」，
而在此之前那個決定完全憑感覺。憑感覺的估時有兩個典型failure mode：

1. 用「一切順利」當基準——實際上 CI 來回、規格澄清、失敗重試都是常態。
2. 估完就丟掉，沒有人回頭對照實際值，所以永遠不會變準。

這裡的作法是：**先分類，再用單價表估區間，最後用這個專案自己的歷史校準。**
單價來自本 repo 的實測（見 docs/time-estimation.md），換一個專案就該重新校準——
所以 `calibration_factor()` 存在的意義不是微調數字，而是承認預設值一定不準。

刻意的保守設計：
- 一律回**區間**（low/high），不回單一數字。單一數字會被當成承諾。
- 樣本少於 MIN_CALIBRATION_SAMPLES 筆時不套用校準（回 1.0）——
  用一兩筆樣本去調整係數，只是把雜訊放大成偏見。
- 無界任務（列舉式防守那種沒有明確終點的）回 None 而不是硬給一個數字，
  因為那不是估不準的問題，是**題目還沒被界定**。
"""
import json
import os
import statistics
import time
from pathlib import Path

# 每個分類的單人單價（分鐘），來自本 repo 的實測。欄位是 (low, high)。
TASK_CLASSES = {
    # 純文件／規則調整，不動程式碼
    "doc": (5, 10),
    # 改幾個既有檔案的接線，附帶 0-2 個測試
    "wiring": (10, 15),
    # 新模組 + 專屬測試套件 + CI 接線
    "module": (15, 25),
    # 結構性改動：新機制、跨多個檔案、預期會有來回
    "structural": (40, 90),
    # 沒有明確終點的題目（例如「把所有繞過寫法都擋掉」）
    "unbounded": None,
}

# 每個 PR 週期的固定開銷（push、等 CI、寫 PR 描述）
CYCLE_OVERHEAD_MIN = 3
CYCLE_OVERHEAD_MAX = 5

# CI 紅一輪要付的代價
CI_RETRY_MIN = 5
CI_RETRY_MAX = 15

MIN_CALIBRATION_SAMPLES = 3
RECORD_FILENAME = "timing.json"


class UnboundedTask(Exception):
    """題目沒有明確終點，不該給估時——先界定範圍再來估。"""


def _repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


def record_path(root=None) -> Path:
    return (Path(root) if root else _repo_root()) / ".harness" / RECORD_FILENAME


def estimate(task_classes, rounds=1, ci_risk=0.3, calibration=1.0) -> dict:
    """估算一批任務的執行時間區間（分鐘）。

    task_classes: 分類名稱的清單，例如 ["doc", "doc", "module"]
    rounds:       打算分成幾個 PR 週期送出
    ci_risk:      預期有多少比例的週期會 CI 紅一次（0.0-1.0）
    calibration:  這個專案的校準係數（見 calibration_factor）

    回傳 low/high 都是**已含**週期開銷與 CI 重試期望值的區間。
    """
    if not 0.0 <= ci_risk <= 1.0:
        raise ValueError(f"ci_risk 必須介於 0 與 1 之間，收到 {ci_risk}")
    if rounds < 1:
        raise ValueError(f"rounds 至少是 1，收到 {rounds}")

    low = high = 0.0
    for name in task_classes:
        if name not in TASK_CLASSES:
            raise ValueError(f"未知的任務分類：{name}（可用：{sorted(TASK_CLASSES)}）")
        price = TASK_CLASSES[name]
        if price is None:
            raise UnboundedTask(
                f"「{name}」沒有明確終點，不可估時。請先把題目界定成有界的範圍"
                "（例如只處理列舉出來的 N 種情況，並寫明這層是縱深防禦而非主防線）。"
            )
        low += price[0]
        high += price[1]

    low += rounds * CYCLE_OVERHEAD_MIN
    high += rounds * CYCLE_OVERHEAD_MAX
    low += rounds * ci_risk * CI_RETRY_MIN
    high += rounds * ci_risk * CI_RETRY_MAX

    return {
        "low_minutes": round(low * calibration, 1),
        "high_minutes": round(high * calibration, 1),
        "task_count": len(task_classes),
        "rounds": rounds,
        "ci_risk": ci_risk,
        "calibration": calibration,
        # 這兩件事不算在 agent 的工作時間裡，但會影響「什麼時候拿得到結果」。
        "excludes": [
            "人審與合併決策的等待時間",
            "撞到訂閱制用量上限之後的等待與恢復（見 docs/token-strategy.md §3.1）",
        ],
    }


def calibration_factor(records) -> float:
    """由歷史紀錄算出這個專案的校準係數＝median(實際 / 估計)。

    樣本不足時回 1.0。用一兩筆樣本調係數只是把雜訊放大成偏見，
    而且會讓人以為估時「有根據」——那比明講「還沒校準」更糟。
    """
    ratios = []
    for item in records or []:
        try:
            estimated = float(item["estimated_minutes"])
            actual = float(item["actual_minutes"])
        except (KeyError, TypeError, ValueError):
            continue
        if estimated > 0 and actual > 0:
            ratios.append(actual / estimated)

    if len(ratios) < MIN_CALIBRATION_SAMPLES:
        return 1.0
    return round(statistics.median(ratios), 3)


def load_records(root=None) -> list:
    """讀歷史紀錄；檔案不存在或壞掉時回空清單（校準是加分項，不是必要條件）。"""
    try:
        document = json.loads(record_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    records = document.get("records") if isinstance(document, dict) else None
    return records if isinstance(records, list) else []


def record(task_id: str, estimated_minutes, actual_minutes, task_classes=None, root=None):
    """把一次「估了多久 / 實際多久」寫進 .harness/timing.json。

    估時只有在事後被對照過才會變準。這支就是那個對照的落地點——
    沒有它，校準係數永遠是 1.0，等於一直用別的專案的單價在估自己的專案。
    """
    records = load_records(root)
    records.append(
        {
            "task_id": task_id,
            "estimated_minutes": estimated_minutes,
            "actual_minutes": actual_minutes,
            "task_classes": list(task_classes or []),
            "recorded_at": time.time(),
        }
    )
    path = record_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    return path
