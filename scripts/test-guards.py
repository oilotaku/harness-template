#!/usr/bin/env python3
"""test-guards.py — guard-hidden-tests.py 的離線回歸測試（無需依賴 pytest）

作法：在暫存目錄裡準備最小情境（視需要建立 .harness/locked-tests.list），
用 subprocess 呼叫 guard-hidden-tests.py 並餵入模擬的 Claude Code PreToolUse
stdin payload，斷言 exit code 符合預期。

目的：確保之後修改 guard-hidden-tests.py 時，不會不小心讓防作弊機制失效
卻沒人發現（對應參考模板 hooks/test/run.mjs 的精神——機制本身也要有測試，
不能只靠人工檢查）。

2026-09-09 code review 後新增了一批案例，涵蓋當時被抓到的具體繞過手法
（沒有結尾斜線的 rm、cd+相對路徑、sed --in-place、直譯器一行指令、
讀取類工具/指令），並修正了一個誤判案例（`2>&1` 不該被當成寫入）。

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


def run_guard(cwd: Path, payload) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=cwd,
    )


def lock(tmp: Path, *paths: str) -> None:
    (tmp / ".harness").mkdir(exist_ok=True)
    (tmp / ".harness" / "locked-tests.list").write_text("\n".join(paths) + "\n")


# ---------------------------------------------------------------- Edit/Write


@case("Edit 寫入 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Edit", "tool_input": {"file_path": "tests/hidden/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Write 建立全新的 tests/hidden 檔案應放行（verifier-test-writer 首次建立）")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Write", "tool_input": {"file_path": "tests/hidden/test_new.py"}}
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Write 覆蓋已存在的 tests/hidden 檔案應被擋")
def _(tmp: Path):
    hidden_dir = tmp / "tests" / "hidden"
    hidden_dir.mkdir(parents=True)
    (hidden_dir / "test_existing.py").write_text("# 既有隱藏測試\n")
    result = run_guard(
        tmp,
        {"tool_name": "Write", "tool_input": {"file_path": "tests/hidden/test_existing.py"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Edit 修改已存在的 tests/hidden 檔案應被擋（Edit 本來就只能對既有檔案操作）")
def _(tmp: Path):
    hidden_dir = tmp / "tests" / "hidden"
    hidden_dir.mkdir(parents=True)
    (hidden_dir / "test_existing.py").write_text("# 既有隱藏測試\n")
    result = run_guard(
        tmp, {"tool_name": "Edit", "tool_input": {"file_path": "tests/hidden/test_existing.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Write 寫入已鎖定的公開測試應被擋")
def _(tmp: Path):
    lock(tmp, "tests/public/test_x.py")
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


@case("合法但非物件的 JSON（null）應放行且不噴未處理例外")
def _(tmp: Path):
    result = subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input="null",
        text=True,
        capture_output=True,
        cwd=tmp,
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"
    assert "Traceback" not in result.stderr, f"不該有未處理例外：{result.stderr}"


@case("合法但非物件的 JSON（陣列）應放行")
def _(tmp: Path):
    result = subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input="[1, 2, 3]",
        text=True,
        capture_output=True,
        cwd=tmp,
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


# --------------------------------------------------------------------- Bash


@case("Bash 指令 rm tests/hidden/x.py 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "rm tests/hidden/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 rm -rf tests/hidden（沒有結尾斜線）應被擋")
def _(tmp: Path):
    result = run_guard(tmp, {"tool_name": "Bash", "tool_input": {"command": "rm -rf tests/hidden"}})
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
    lock(tmp, "tests/public/test_x.py")
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {"command": "sed -i 's/a/b/' tests/public/test_x.py"},
        },
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 sed --in-place（長選項）修改已鎖定測試應被擋")
def _(tmp: Path):
    lock(tmp, "tests/public/test_x.py")
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {"command": "sed --in-place s/a/b/ tests/public/test_x.py"},
        },
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 cd tests/public && rm x.py（相對路徑繞過）應被擋")
def _(tmp: Path):
    lock(tmp, "tests/public/test_x.py")
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": "cd tests/public && rm test_x.py"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 python3 -c 內嵌 .harness 路徑應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 -c \"open('.harness/locked-tests.list','w').close()\""
            },
        },
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 cat tests/hidden/x.py（純讀取）應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "cat tests/hidden/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 cat 已鎖定的公開測試應放行（implementer 本來就該讀得到公開測試）")
def _(tmp: Path):
    lock(tmp, "tests/public/test_x.py")
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "cat tests/public/test_x.py"}}
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 grep 搜尋 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {"command": "grep -r 'assert' tests/hidden/"},
        },
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 一般無關指令應放行")
def _(tmp: Path):
    result = run_guard(tmp, {"tool_name": "Bash", "tool_input": {"command": "ls -la src/"}})
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 指令 diff a b 2>&1（純 fd 重導向，非寫入）不該被誤判為寫入")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {"command": "diff /tmp/a.py /tmp/b.py 2>&1"},
        },
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 執行隱藏測試（python3 -m unittest，非 -c/-e）不該被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 -m unittest discover -s tests/hidden -p 'test_*.py'"
            },
        },
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


# ------------------------------------------------------------ Read/Grep/Glob


@case("Read 工具讀取 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Read", "tool_input": {"file_path": "tests/hidden/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Read 工具讀取一般路徑應放行")
def _(tmp: Path):
    result = run_guard(tmp, {"tool_name": "Read", "tool_input": {"file_path": "src/app.py"}})
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Read 工具讀取 .harness/ 應放行（只需寫入保護，讀取無害）")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Read", "tool_input": {"file_path": ".harness/env-fingerprint.json"}}
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Grep 工具搜尋 tests/hidden/ 目錄應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Grep", "tool_input": {"pattern": "assert", "path": "tests/hidden"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Glob 工具搜尋 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Glob", "tool_input": {"pattern": "*.py", "path": "tests/hidden"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


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
