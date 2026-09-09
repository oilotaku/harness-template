#!/usr/bin/env python3
"""test-guards.py — guard-hidden-tests.py 的離線回歸測試（無需依賴 pytest）

作法：在暫存目錄裡準備最小情境（視需要建立 .harness/locked-tests.list），
用 subprocess 呼叫 guard-hidden-tests.py 並餵入模擬的 Claude Code PreToolUse
stdin payload，斷言 exit code 符合預期。

目的：確保之後修改 guard-hidden-tests.py 時，不會不小心讓防作弊機制失效
卻沒人發現（對應參考模板 hooks/test/run.mjs 的精神——機制本身也要有測試，
不能只靠人工檢查）。

用法：python3 scripts/test-guards.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

GUARD_SCRIPT = Path(__file__).resolve().parent / "guard-hidden-tests.py"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def run_guard(cwd: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=cwd,
    )


@case("Edit 寫入 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Edit", "tool_input": {"file_path": "tests/hidden/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Write 寫入已鎖定的公開測試應被擋")
def _(tmp: Path):
    (tmp / ".harness").mkdir()
    (tmp / ".harness" / "locked-tests.list").write_text("tests/public/test_x.py\n")
    result = run_guard(
        tmp, {"tool_name": "Write", "tool_input": {"file_path": "tests/public/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Write 寫入一般路徑應放行")
def _(tmp: Path):
    result = run_guard(tmp, {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}})
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Windows 反斜線路徑指向 tests/hidden 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Edit", "tool_input": {"file_path": "tests\\hidden\\test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Edit 寫入 .harness 治理檔應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Edit", "tool_input": {"file_path": ".harness/locked-tests.list"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("缺 file_path 應放行（fail open）")
def _(tmp: Path):
    result = run_guard(tmp, {"tool_name": "Edit", "tool_input": {}})
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("malformed JSON 應放行（fail open）")
def _(tmp: Path):
    result = subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input="not json",
        text=True,
        capture_output=True,
        cwd=tmp,
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 rm tests/hidden 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "rm tests/hidden/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令重導向覆寫 .harness/locked-tests.list 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {"command": "echo '' > .harness/locked-tests.list"},
        },
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 sed -i 修改已鎖定測試應被擋")
def _(tmp: Path):
    (tmp / ".harness").mkdir()
    (tmp / ".harness" / "locked-tests.list").write_text("tests/public/test_x.py\n")
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {"command": "sed -i 's/a/b/' tests/public/test_x.py"},
        },
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 一般無關指令應放行")
def _(tmp: Path):
    result = run_guard(tmp, {"tool_name": "Bash", "tool_input": {"command": "ls -la src/"}})
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 純讀取 tests/hidden（無寫入動詞）目前設計上仍放行——已知殘留風險，見腳本註解")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "cat tests/hidden/test_x.py"}}
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


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
