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
import hashlib
import json
import os
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


def run_guard(cwd: Path, payload, raw_stdin: str = None, extra_env=None) -> subprocess.CompletedProcess:
    # 一律明確指定 CLAUDE_PROJECT_DIR，否則會繼承外層 Claude Code session 的值，
    # 讓 guard 拿真正的專案目錄當基準，測試就會全部對不上暫存情境。
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=raw_stdin if raw_stdin is not None else json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=cwd,
        env=env,
    )


def default_vault(tmp: Path) -> Path:
    """guard 推導封存庫預設位置的方式（見 guard-hidden-tests.py 的 _vault_dirs）。"""
    return tmp.parent / ".harness-hidden" / tmp.name


def write_manifest(tmp: Path, task_dir: Path) -> None:
    (tmp / ".harness").mkdir(exist_ok=True)
    (tmp / ".harness" / "hidden-manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "T1": {
                        "task_dir": str(task_dir),
                        "vault_dir": str(task_dir.parent),
                        "token_sha256": "0" * 64,
                        "files": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def lock(tmp: Path, *paths: str) -> None:
    """寫入舊格式（純路徑）鎖定清單，確保向後相容仍然有效。"""
    (tmp / ".harness").mkdir(exist_ok=True)
    (tmp / ".harness" / "locked-tests.list").write_text("\n".join(paths) + "\n")


def lock_with_hash(tmp: Path, *paths: str) -> None:
    """寫入新格式（`<sha256>  <路徑>`）鎖定清單，比照 lock-tests.py 的輸出。"""
    (tmp / ".harness").mkdir(exist_ok=True)
    lines = []
    for path in paths:
        target = tmp / path
        digest = (
            hashlib.sha256(target.read_bytes()).hexdigest()
            if target.is_file()
            else "0" * 64
        )
        lines.append(f"{digest}  {path}")
    (tmp / ".harness" / "locked-tests.list").write_text("\n".join(lines) + "\n")


def touch(tmp: Path, path: str, content: str = "assert True\n") -> Path:
    target = tmp / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


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


@case("缺 file_path 應放行（payload 合法，只是沒有可判斷的目標）")
def _(tmp: Path):
    result = run_guard(tmp, {"tool_name": "Edit", "tool_input": {}})
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("合法但非物件的 JSON（null）應 fail-closed 且不噴未處理例外")
def _(tmp: Path):
    # 2026-09-10（P0-5）行為變更：這三種「看不懂的輸入」以前是放行（fail open），
    # 現在一律擋下。理由見 guard-hidden-tests.py 模組說明——腳本對這次呼叫
    # 已經失去判斷能力，放行等於在防護失效時默默全開，而且沒有任何訊號。
    result = run_guard(tmp, None, raw_stdin="null")
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"
    assert "Traceback" not in result.stderr, f"不該有未處理例外：{result.stderr}"


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


# ------------------------------------------------- P0-1：路徑寫法正規化（2026-09-10）
#
# 以下這批案例對應 docs/improvement-suggestions.md 的 P0-1：第二版的
# Edit/Write/Read/Grep/Glob 分支只做字串前綴比對，因此絕對路徑、`./`、`//`、`..`
# 這些寫法全部擋不到。其中「絕對路徑」尤其嚴重——Read 工具的 file_path 規定
# 就是要絕對路徑，等於讀取保護在正常用法下完全失效。


@case("P0-1 Read 用絕對路徑讀 tests/hidden/ 應被擋（Read 工具規定就是絕對路徑）")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Read", "tool_input": {"file_path": str(tmp / "tests/hidden/test_x.py")}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 Read 用 ./ 前綴讀 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Read", "tool_input": {"file_path": "./tests/hidden/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 Read 用雙斜線 tests//hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Read", "tool_input": {"file_path": "tests//hidden/test_x.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 Read 用 ../ 迂迴回 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Read", "tool_input": {"file_path": "tests/../tests/hidden/test_x.py"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 Write 用絕對路徑覆寫已鎖定的公開測試應被擋")
def _(tmp: Path):
    touch(tmp, "tests/public/test_p.py")
    lock_with_hash(tmp, "tests/public/test_p.py")
    result = run_guard(
        tmp,
        {"tool_name": "Write", "tool_input": {"file_path": str(tmp / "tests/public/test_p.py")}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 Edit 用絕對路徑修改 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Edit", "tool_input": {"file_path": str(tmp / "tests/hidden/test_x.py")}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 Bash cat 用絕對路徑讀 tests/hidden/ 應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": f"cat {tmp / 'tests/hidden/test_x.py'}"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 repo 之外的絕對路徑不歸本 hook 管，應放行")
def _(tmp: Path):
    # 另一個專案自己的 tests/hidden/ 不該被這個 repo 的 hook 擋下來。
    outside = tmp.parent / "another-project" / "tests" / "hidden" / "test_x.py"
    result = run_guard(tmp, {"tool_name": "Read", "tool_input": {"file_path": str(outside)}})
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 cd 到 repo 之外後的相對路徑應放行（不是本 repo 的隱藏測試）")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": "cd .. && cat tests/hidden/test_x.py"}},
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 新格式（含 sha256）鎖定清單一樣能擋下對公開測試的寫入")
def _(tmp: Path):
    touch(tmp, "tests/public/test_p.py")
    lock_with_hash(tmp, "tests/public/test_p.py")
    result = run_guard(
        tmp, {"tool_name": "Edit", "tool_input": {"file_path": "tests/public/test_p.py"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-1 新格式鎖定清單仍允許讀取公開測試（鎖定只限制寫入）")
def _(tmp: Path):
    touch(tmp, "tests/public/test_p.py")
    lock_with_hash(tmp, "tests/public/test_p.py")
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "cat tests/public/test_p.py"}}
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


# ------------------------------------------------- P0-5：fail-closed（2026-09-10）


@case("P0-5 stdin 不是合法 JSON 時應 fail-closed（擋下而非放行）")
def _(tmp: Path):
    result = run_guard(tmp, None, raw_stdin="這不是 JSON")
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-5 stdin 是合法 JSON 但最外層不是物件時應 fail-closed")
def _(tmp: Path):
    result = run_guard(tmp, None, raw_stdin="[1, 2, 3]")
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-5 空 stdin 應 fail-closed")
def _(tmp: Path):
    result = run_guard(tmp, None, raw_stdin="")
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


# ------------------------------------------- P1-1：封存庫路徑保護（2026-09-10）
#
# 封存後的隱藏測試在 repo 之外，而且內容是加密的（見 scripts/hidden_vault.py），
# 所以這一層只是縱深防禦——目的是讓誤觸的人得到明確訊息，而不是一堆亂碼。


@case("P1-1 Read 預設封存庫路徑應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Read", "tool_input": {"file_path": str(default_vault(tmp) / "T1/test.py.enc")}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P1-1 Bash cat 預設封存庫路徑應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": f"cat {default_vault(tmp) / 'T1/test.py.enc'}"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P1-1 封存庫底下的存取不分動詞一律擋（ls 也擋）")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": f"ls {default_vault(tmp)}"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P1-1 HARNESS_HIDDEN_DIR 覆寫的位置也受保護")
def _(tmp: Path):
    custom = tmp.parent / f"custom-vault-{tmp.name}"
    result = run_guard(
        tmp,
        {"tool_name": "Read", "tool_input": {"file_path": str(custom / "T1/test.py.enc")}},
        extra_env={"HARNESS_HIDDEN_DIR": str(custom)},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P1-1 manifest 記錄過的封存位置也受保護（封存時有覆寫、現在沒設環境變數）")
def _(tmp: Path):
    recorded = tmp.parent / f"recorded-vault-{tmp.name}" / "T1"
    write_manifest(tmp, recorded)
    result = run_guard(
        tmp, {"tool_name": "Read", "tool_input": {"file_path": str(recorded / "test.py.enc")}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P1-1 執行 run-hidden-tests.py 本身不該被擋（唯一的合法入口）")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 scripts/run-hidden-tests.py --task-id T1 --token abc123"
            },
        },
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("P1-1 執行 seal-hidden-tests.py 本身不該被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": "python3 scripts/seal-hidden-tests.py --task-id T1"}},
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("P1-1 repo 之外、與封存庫無關的路徑仍然放行")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Read", "tool_input": {"file_path": str(tmp.parent / "unrelated/x.py")}}
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
