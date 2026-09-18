#!/usr/bin/env python3
"""test-usage-gate.py — scripts/usage-gate.py 的回歸測試

守的行為：
  1. 用量在門檻內 → exit 0，不建立暫停檔
  2. 任一視窗超門檻且有 resets_at → exit 2，寫暫停檔並記下 resume_at
  3. 已在暫停中、未到 resume_at → exit 2（不重讀快照）
  4. 已在暫停中、已過 resume_at → exit 0（自動恢復，清掉暫停檔）
  5. 快照不存在 / 壞掉 → exit 0（fail-open：用量閘門是最佳化，不是安全控制）
  6. 超門檻但缺 resets_at → 不據此暫停（無法自動恢復），exit 0 但出聲
  7. --threshold / --window 覆寫生效
  8. --clear 解除暫停；--status 不改狀態
  9. --json 輸出含 state / resume_at

用法：python3 scripts/test-usage-gate.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utf8_output  # noqa: E402

utf8_output.enable()

SCRIPTS_DIR = Path(__file__).resolve().parent
GATE = SCRIPTS_DIR / "usage-gate.py"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def run(tmp: Path, *args, snapshot=None, threshold=None, env=None):
    """跑 usage-gate.py。pause 檔一律指到 tmp 底下，不碰真的 .harness/。"""
    pause = tmp / "usage-pause.json"
    cmd = [sys.executable, str(GATE), "--pause-file", str(pause)]
    if snapshot is not None:
        cmd += ["--snapshot", str(snapshot)]
    if threshold is not None:
        cmd += ["--threshold", str(threshold)]
    cmd += list(args)
    full_env = {**os.environ}
    if env:
        full_env.update(env)
    proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                          capture_output=True, env=full_env)
    return proc, pause


def write_snapshot(tmp: Path, five=None, seven=None) -> Path:
    snap = {}
    if five is not None:
        snap["five_hour"] = five
    if seven is not None:
        snap["seven_day"] = seven
    path = tmp / "usage-snapshot.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    return path


def in_(delta_secs: int) -> int:
    return int(time.time()) + delta_secs


# --------------------------------------------------------------- 門檻內 / 超門檻


@case("用量在門檻內 → exit 0，不建立暫停檔")
def _(tmp: Path):
    snap = write_snapshot(tmp,
                          five={"used_percentage": 40, "resets_at": in_(3600)},
                          seven={"used_percentage": 55, "resets_at": in_(200000)})
    proc, pause = run(tmp, "--check", snapshot=snap)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not pause.exists(), "門檻內卻寫了暫停檔"


@case("5h 超門檻且有 resets_at → exit 2，寫暫停檔並記 resume_at")
def _(tmp: Path):
    reset = in_(7200)
    snap = write_snapshot(tmp, five={"used_percentage": 94, "resets_at": reset})
    proc, pause = run(tmp, "--check", snapshot=snap)
    assert proc.returncode == 2, f"超門檻卻沒回 2：{proc.stdout}{proc.stderr}"
    assert pause.exists(), "該停卻沒寫暫停檔"
    data = json.loads(pause.read_text(encoding="utf-8"))
    assert data["resume_at"] == reset, data
    assert "5h" in data["reason"], data


@case("多個視窗都超門檻 → resume_at 取最早的重置時間")
def _(tmp: Path):
    early, late = in_(1800), in_(90000)
    snap = write_snapshot(tmp,
                          five={"used_percentage": 99, "resets_at": early},
                          seven={"used_percentage": 95, "resets_at": late})
    proc, pause = run(tmp, "--check", snapshot=snap)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    data = json.loads(pause.read_text(encoding="utf-8"))
    assert data["resume_at"] == early, f"沒取最早的重置時間：{data}"


# ----------------------------------------------------------- 暫停中 / 自動恢復


@case("已在暫停中、未到 resume_at → exit 2（不看快照）")
def _(tmp: Path):
    pause = tmp / "usage-pause.json"
    pause.write_text(json.dumps({"resume_at": in_(3600), "reason": "5h 95% ≥ 90%"}),
                     encoding="utf-8")
    # 故意給一個門檻內的快照：仍應維持暫停，證明暫停優先於重新評估。
    snap = write_snapshot(tmp, five={"used_percentage": 10, "resets_at": in_(3600)})
    proc, _ = run(tmp, "--check", snapshot=snap)
    assert proc.returncode == 2, f"暫停中卻放行了：{proc.stdout}{proc.stderr}"


@case("已在暫停中、已過 resume_at → exit 0 並清掉暫停檔（自動恢復）")
def _(tmp: Path):
    pause = tmp / "usage-pause.json"
    pause.write_text(json.dumps({"resume_at": in_(-10), "reason": "5h 95% ≥ 90%"}),
                     encoding="utf-8")
    proc, _ = run(tmp, "--check")
    assert proc.returncode == 0, f"到期卻沒恢復：{proc.stdout}{proc.stderr}"
    assert not pause.exists(), "恢復後沒清掉暫停檔"
    assert "恢復" in proc.stdout or "resumed" in proc.stdout, proc.stdout


# ----------------------------------------------------------------- fail-open


@case("快照不存在 → exit 0（fail-open）")
def _(tmp: Path):
    proc, pause = run(tmp, "--check", snapshot=tmp / "nope.json")
    assert proc.returncode == 0, f"沒有快照卻擋下：{proc.stdout}{proc.stderr}"
    assert not pause.exists()


@case("快照壞掉 → exit 0（fail-open）")
def _(tmp: Path):
    bad = tmp / "bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    proc, _ = run(tmp, "--check", snapshot=bad)
    assert proc.returncode == 0, f"壞快照卻擋下：{proc.stdout}{proc.stderr}"


@case("超門檻但缺 resets_at → 不暫停（無法自動恢復），exit 0 但出聲")
def _(tmp: Path):
    snap = write_snapshot(tmp, five={"used_percentage": 99})  # 沒有 resets_at
    proc, pause = run(tmp, "--check", snapshot=snap)
    assert proc.returncode == 0, f"缺 resets_at 卻擋死：{proc.stdout}{proc.stderr}"
    assert not pause.exists(), "缺 resets_at 不該建立無法恢復的暫停"
    assert "resets_at" in proc.stdout or "門檻" in proc.stdout, proc.stdout


# ------------------------------------------------------------- 旗標 / 環境變數


@case("--threshold 覆寫：門檻降到 30 後 40% 就該停")
def _(tmp: Path):
    snap = write_snapshot(tmp, five={"used_percentage": 40, "resets_at": in_(3600)})
    proc, _ = run(tmp, "--check", snapshot=snap, threshold=30)
    assert proc.returncode == 2, f"降門檻後沒停：{proc.stdout}{proc.stderr}"


@case("--window 只看 seven_day 時，5h 超門檻不觸發")
def _(tmp: Path):
    snap = write_snapshot(tmp,
                          five={"used_percentage": 99, "resets_at": in_(600)},
                          seven={"used_percentage": 10, "resets_at": in_(90000)})
    proc, pause = run(tmp, "--check", "--window", "seven_day", snapshot=snap)
    assert proc.returncode == 0, f"只看 7d 卻被 5h 觸發：{proc.stdout}{proc.stderr}"
    assert not pause.exists()


@case("HARNESS_USAGE_THRESHOLD 環境變數生效")
def _(tmp: Path):
    snap = write_snapshot(tmp, five={"used_percentage": 40, "resets_at": in_(3600)})
    proc, _ = run(tmp, "--check", snapshot=snap, env={"HARNESS_USAGE_THRESHOLD": "30"})
    assert proc.returncode == 2, f"環境變數門檻沒生效：{proc.stdout}{proc.stderr}"


@case("非法門檻 → exit 3（呼叫錯誤，不能被當成放行）")
def _(tmp: Path):
    snap = write_snapshot(tmp, five={"used_percentage": 40, "resets_at": in_(3600)})
    proc, _ = run(tmp, "--check", snapshot=snap, threshold=150)
    assert proc.returncode == 3, f"非法門檻沒回 3：{proc.returncode} {proc.stderr}"


# ----------------------------------------------------------------- clear/status/json


@case("--clear 解除暫停")
def _(tmp: Path):
    pause = tmp / "usage-pause.json"
    pause.write_text(json.dumps({"resume_at": in_(3600)}), encoding="utf-8")
    proc, _ = run(tmp, "--clear")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not pause.exists(), "--clear 沒刪掉暫停檔"


@case("--status 不建立暫停檔（唯讀）")
def _(tmp: Path):
    snap = write_snapshot(tmp, five={"used_percentage": 99, "resets_at": in_(600)})
    proc, pause = run(tmp, "--status", snapshot=snap)
    # 唯讀：即使超門檻，也不寫暫停檔（但仍如實回報該停 → exit 2）
    assert not pause.exists(), "--status 竟然寫了暫停檔"


@case("--json 輸出含 state 與 resume_at")
def _(tmp: Path):
    reset = in_(3600)
    snap = write_snapshot(tmp, five={"used_percentage": 99, "resets_at": reset})
    proc, _ = run(tmp, "--check", "--json", snapshot=snap)
    payload = json.loads(proc.stdout)
    assert payload["state"] == "pause", payload
    assert payload["resume_at"] == reset, payload


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
