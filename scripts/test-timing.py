#!/usr/bin/env python3
"""test-timing.py — 執行時間預測與校準的回歸測試

這批測試守的是「估時不要變成假的承諾」：

1. **一律回區間，不回單一數字。** 單一數字會被當成承諾。
2. **無界任務不給數字。** 硬給一個看起來很有把握的估時，比誠實說
   「這題還沒被界定」更危險——這跟 service-scan 掃描不完整時不給
   建議連接埠區間是同一條原則。
3. **樣本不足時不套用校準。** 用一兩筆樣本調係數只是把雜訊放大成偏見，
   而且會讓人以為估時「有根據」。

用法：python3 scripts/test-timing.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import timing  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def run_cli(cwd: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "estimate-time.py"), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(cwd),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)},
    )


def load_cli_module():
    path = SCRIPTS_DIR / "estimate-time.py"
    spec = importlib.util.spec_from_file_location("estimate_time", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# estimate()
# --------------------------------------------------------------------------

@case("估時一律回區間，且 low 不會大於 high")
def _(tmp: Path):
    result = timing.estimate(["doc", "module"])
    assert result["low_minutes"] <= result["high_minutes"], result


@case("任務越多，估出來的時間越長")
def _(tmp: Path):
    small = timing.estimate(["doc"])
    large = timing.estimate(["doc", "doc", "module"])
    assert large["low_minutes"] > small["low_minutes"], (small, large)


@case("PR 週期越多，固定開銷越高")
def _(tmp: Path):
    one = timing.estimate(["doc"], rounds=1)
    three = timing.estimate(["doc"], rounds=3)
    assert three["low_minutes"] > one["low_minutes"], (one, three)


@case("CI 風險越高，估時越長；風險 0 時沒有重試成本")
def _(tmp: Path):
    zero = timing.estimate(["doc"], ci_risk=0.0)
    high = timing.estimate(["doc"], ci_risk=1.0)
    assert high["high_minutes"] > zero["high_minutes"], (zero, high)


@case("無界任務丟 UnboundedTask，而不是回一個看起來很有把握的數字")
def _(tmp: Path):
    try:
        timing.estimate(["unbounded"])
    except timing.UnboundedTask:
        return
    raise AssertionError("無界任務竟然估出了數字")


@case("未知分類直接報錯，不會靜靜當成 0 分鐘")
def _(tmp: Path):
    try:
        timing.estimate(["這個分類不存在"])
    except ValueError:
        return
    raise AssertionError("未知分類應該報錯")


@case("ci_risk 超出 0-1 範圍時報錯")
def _(tmp: Path):
    for bad in (-0.1, 1.5):
        try:
            timing.estimate(["doc"], ci_risk=bad)
        except ValueError:
            continue
        raise AssertionError(f"ci_risk={bad} 應該報錯")


@case("rounds 小於 1 時報錯")
def _(tmp: Path):
    try:
        timing.estimate(["doc"], rounds=0)
    except ValueError:
        return
    raise AssertionError("rounds=0 應該報錯")


@case("校準係數會等比例套用到估時上")
def _(tmp: Path):
    base = timing.estimate(["doc"], calibration=1.0)
    doubled = timing.estimate(["doc"], calibration=2.0)
    assert abs(doubled["low_minutes"] - base["low_minutes"] * 2) < 0.2, (base, doubled)


@case("估時結果必須寫明「不含撞到用量上限的等待」")
def _(tmp: Path):
    # 這句提醒被悄悄拿掉的話，估時就會被當成「什麼時候拿得到結果」，
    # 而那正是訂閱制方案下最容易失準的地方。
    result = timing.estimate(["doc"])
    assert any("用量上限" in item for item in result["excludes"]), result["excludes"]


# --------------------------------------------------------------------------
# 校準
# --------------------------------------------------------------------------

@case("樣本不足 3 筆時不套用校準（回 1.0）")
def _(tmp: Path):
    records = [{"estimated_minutes": 10, "actual_minutes": 30}] * 2
    assert timing.calibration_factor(records) == 1.0


@case("樣本足夠時用中位數，不用平均（單一離群值不該主導）")
def _(tmp: Path):
    records = [
        {"estimated_minutes": 10, "actual_minutes": 10},   # ratio 1
        {"estimated_minutes": 10, "actual_minutes": 20},   # ratio 2
        {"estimated_minutes": 10, "actual_minutes": 900},  # ratio 90（離群）
    ]
    assert timing.calibration_factor(records) == 2.0, timing.calibration_factor(records)


@case("壞掉的紀錄會被略過，不會讓校準整組炸掉")
def _(tmp: Path):
    records = [
        {"estimated_minutes": 10, "actual_minutes": 20},
        {"estimated_minutes": 0, "actual_minutes": 20},      # 除以零
        {"estimated_minutes": "abc", "actual_minutes": 20},  # 型別錯
        {"actual_minutes": 20},                              # 缺欄位
        {"estimated_minutes": 10, "actual_minutes": 20},
        {"estimated_minutes": 10, "actual_minutes": 20},
    ]
    assert timing.calibration_factor(records) == 2.0


@case("沒有紀錄檔時回空清單，不是崩掉")
def _(tmp: Path):
    assert timing.load_records(root=tmp) == []


@case("紀錄檔壞掉時回空清單（校準是加分項，不是必要條件）")
def _(tmp: Path):
    (tmp / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp / ".harness" / "timing.json").write_text("{ 這不是 JSON", encoding="utf-8")
    assert timing.load_records(root=tmp) == []


@case("record 寫得進去也讀得回來")
def _(tmp: Path):
    timing.record("T-001", 20, 34, ["module"], root=tmp)
    records = timing.load_records(root=tmp)
    assert len(records) == 1, records
    assert records[0]["task_id"] == "T-001", records[0]


@case("多筆 record 會累積，不會互相覆蓋")
def _(tmp: Path):
    for index in range(3):
        timing.record(f"T-{index}", 10, 20, ["doc"], root=tmp)
    records = timing.load_records(root=tmp)
    assert len(records) == 3, records
    assert timing.calibration_factor(records) == 2.0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

@case("CLI --json 的 stdout 只有 JSON")
def _(tmp: Path):
    result = run_cli(tmp, "--tasks", "doc:2,module:1", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["task_count"] == 3, payload


@case("CLI 沒有 --json 時印中文報表")
def _(tmp: Path):
    result = run_cli(tmp, "--tasks", "doc:2")
    assert result.returncode == 0, result.stderr
    assert "===== 執行時間預測 =====" in result.stdout, result.stdout[:200]


@case("CLI 對無界任務回非 0，且輸出裡沒有任何估時數字")
def _(tmp: Path):
    result = run_cli(tmp, "--tasks", "unbounded:1")
    assert result.returncode == 1, result.returncode
    assert "分鐘" not in result.stdout, result.stdout


@case("CLI 對未知分類回參數錯誤（exit 2），不是估成 0 分鐘")
def _(tmp: Path):
    result = run_cli(tmp, "--tasks", "nope:1")
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)


@case("CLI --record 缺 --actual 時不會靜靜寫入半套紀錄")
def _(tmp: Path):
    result = run_cli(tmp, "--record", "T-9", "--estimated", "10")
    assert result.returncode != 0, result.returncode
    assert timing.load_records(root=tmp) == []


@case("CLI --record 寫進 .harness/timing.json，並回報樣本數")
def _(tmp: Path):
    result = run_cli(tmp, "--record", "T-9", "--estimated", "10", "--actual", "20", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["samples"] == 1, payload
    assert (tmp / ".harness" / "timing.json").exists()


@case("CLI 會自動套用歷史校準，--no-calibration 則不套用")
def _(tmp: Path):
    for index in range(3):
        timing.record(f"T-{index}", 10, 20, ["doc"], root=tmp)

    calibrated = json.loads(run_cli(tmp, "--tasks", "doc:1", "--json").stdout)
    raw = json.loads(run_cli(tmp, "--tasks", "doc:1", "--json", "--no-calibration").stdout)
    assert calibrated["calibration"] == 2.0, calibrated
    assert raw["calibration"] == 1.0, raw
    assert calibrated["low_minutes"] > raw["low_minutes"], (calibrated, raw)


@case("CLI 的 doc:3 與 doc,doc,doc 展開結果相同")
def _(tmp: Path):
    module = load_cli_module()
    assert module.parse_tasks("doc:3") == module.parse_tasks("doc,doc,doc") == ["doc"] * 3


@case("CLI 的任務清單為空或數量非法時報錯")
def _(tmp: Path):
    module = load_cli_module()
    for bad in ("", "  ", "doc:0", "doc:x"):
        try:
            module.parse_tasks(bad)
        except ValueError:
            continue
        raise AssertionError(f"「{bad}」應該報錯")


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as base:
        for index, (name, fn) in enumerate(CASES):
            case_dir = Path(base) / f"case-{index}"
            case_dir.mkdir()
            try:
                fn(case_dir)
                print(f"PASS  {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"FAIL  {name}\n      {e}")

    print()
    if failures:
        print(f"{len(failures)}/{len(CASES)} 案例失敗：")
        for name in failures:
            print(f"  - {name}")
        return 1

    print(f"全部 {len(CASES)} 案例通過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
