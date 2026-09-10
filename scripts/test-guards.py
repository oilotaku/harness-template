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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
GUARD_SCRIPT = SCRIPTS_DIR / "guard-hidden-tests.py"

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
        text=True, encoding="utf-8", errors="replace",
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
    (tmp / ".harness" / "locked-tests.list").write_text(
        "\n".join(paths) + "\n", encoding="utf-8"
    )


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
    (tmp / ".harness" / "locked-tests.list").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


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
    (hidden_dir / "test_existing.py").write_text("# 既有隱藏測試\n", encoding="utf-8")
    result = run_guard(
        tmp,
        {"tool_name": "Write", "tool_input": {"file_path": "tests/hidden/test_existing.py"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("Edit 修改已存在的 tests/hidden 檔案應被擋（Edit 本來就只能對既有檔案操作）")
def _(tmp: Path):
    hidden_dir = tmp / "tests" / "hidden"
    hidden_dir.mkdir(parents=True)
    (hidden_dir / "test_existing.py").write_text("# 既有隱藏測試\n", encoding="utf-8")
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


@case("Write 寫入 .harness/progress/ 進度檢查點應放行")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Write", "tool_input": {"file_path": ".harness/progress/T-001.md"}}
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case("Bash 寫入 .harness/progress/ 進度檢查點應放行")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {"command": "echo '- 狀態：實作中' > .harness/progress/T-001.md"},
        },
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


@case(".harness/progress 的開口不可被 .. 逃逸回治理檔案")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": ".harness/progress/../locked-tests.list"},
        },
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case(".harness/progressive 這種相似名稱不算開口，仍應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Edit", "tool_input": {"file_path": ".harness/progressive.json"}}
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


@case("直接用 unittest 跑暫存區的隱藏測試應被擋（唯一入口是 run-hidden-tests.py）")
def _(tmp: Path):
    # 這個案例的預期在 P0-3 收尾時**刻意翻轉**了。
    # 舊版寫的是「python3 -m unittest discover -s <暫存區> 不該被擋」，那是 P1-1
    # （加密封存）之前的假設：當時暫存區就是隱藏測試的存放處，擋掉等於沒人跑得了。
    # P1-1 之後唯一的合法入口是 run-hidden-tests.py（需要權杖），而直接跑暫存區
    # 會把通過/失敗變成可反覆查詢的 oracle——正是整套機制要防的事。
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 -m unittest discover -s tests/hidden -p 'test_*.py'"
            },
        },
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


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


# --------------------------------------- settings.json 與腳本之間不能漂移（P2-4）


@case("每個 agent 宣告的 thinking 層級，本文都有對應的推理強度指示（P2-3）")
def _(tmp: Path):
    # frontmatter 的 thinking 欄位不會被 Claude Code 讀取——它只是文件標註。
    # 真正讓思考層級生效的是提示詞本文。兩邊漂移的話，
    # 模板就會宣稱一個從來沒有實際效果的設定。
    import re

    agents_dir = SCRIPTS_DIR.parent / ".claude" / "agents"
    problems = []
    for path in sorted(agents_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^thinking:\s*(\w+)\s*$", text, flags=re.M)
        if not match:
            continue
        level = match.group(1)
        if level not in ("low", "medium", "high"):
            problems.append(f"{path.name}：未知的 thinking 層級「{level}」")
            continue
        if "## 推理強度" not in text:
            problems.append(f"{path.name}：宣告了 thinking: {level}，但本文沒有推理強度指示")
            continue
        if f"thinking: {level}" not in text.split("## 推理強度", 1)[1][:200]:
            problems.append(f"{path.name}：推理強度段落沒有對應到 frontmatter 的 {level}")

    assert not problems, "；".join(problems)


# ---------------------------------------------------------------- P0-3 收尾
# 這批是審視報告 P0-3 列出的六類結構性繞過。它們的共同點是「動詞比對」看不到
# 真正的動作：包在 bash -c 裡、藏在 find/xargs/tar 的參數裡、或先存進變數再展開。
# 界定過的範圍：**只要指令字串裡有任何一段解析得出受保護的隱藏測試路徑就擋**，
# 不再問動詞是什麼。沒有寫出路徑的混淆（base64、逐字元組字串）不在範圍內，
# 那由加密封存擋（見 guard 的模組說明）。

@case("P0-3：bash -c 包起來的讀取應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": "bash -c 'cat tests/hidden/test_x.py'"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-3：sh -c 包起來的刪除應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": 'sh -c "rm tests/hidden/test_x.py"'}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-3：find -delete 應被擋（find 既不是寫入動詞也不是讀取動詞）")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": "find tests/hidden -name '*.py' -delete"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-3：管線接 xargs 應被擋（真正的動詞在 xargs 後面看不到）")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "ls tests/hidden | xargs rm"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-3：tar 打包整個隱藏測試目錄應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "tar cf out.tar tests/hidden"}}
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-3：command substitution 應被擋")
def _(tmp: Path):
    for command in ("echo $(cat tests/hidden/test_x.py)", "echo `cat tests/hidden/test_x.py`"):
        result = run_guard(tmp, {"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2, f"{command} → exit={result.returncode}"


@case("P0-3：先存進變數再展開應被擋")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": "D=tests/hidden; cat $D/test_x.py"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-3：絕對路徑寫法也擋得到")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {"tool_name": "Bash", "tool_input": {"command": f"cat {tmp}/tests/hidden/test_x.py"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} stderr={result.stderr}"


@case("P0-3 不可誤傷：範例目錄的同名路徑仍然放行")
def _(tmp: Path):
    # templates/examples/.../tests/hidden 不在受保護路徑內（它是刻意留著的示範，
    # 有自己的 README 說明不受保護）。用子字串比對就會誤傷它——所以這裡是
    # 「解析成 repo 相對路徑再判斷」，不是「字串裡有沒有 tests/hidden」。
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "ls templates/examples/demo-fizzbuzz/tests/hidden"
            },
        },
    )
    assert result.returncode == 0, f"誤傷了範例目錄：exit={result.returncode} {result.stderr}"


@case("P0-3 不可誤傷：cd 進範例目錄之後跑它的隱藏測試仍然放行")
def _(tmp: Path):
    result = run_guard(
        tmp,
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "cd templates/examples/demo-fizzbuzz && "
                           "python3 -m unittest discover -s tests/hidden -p 'test_*.py'"
            },
        },
    )
    assert result.returncode == 0, f"誤傷了範例流程：exit={result.returncode} {result.stderr}"


@case("P0-3 不可誤傷：讀取公開測試仍然放行")
def _(tmp: Path):
    result = run_guard(
        tmp, {"tool_name": "Bash", "tool_input": {"command": "cat tests/public/test_x.py"}}
    )
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"


def selfcheck_in(repo: Path) -> subprocess.CompletedProcess:
    """在一個假 repo 裡跑 guard-selfcheck.py。

    它會先確認 REPO_ROOT/scripts/guard-hidden-tests.py 存在（不存在就直接
    回報「防護完全沒有生效」而不往下走），所以這裡把整個 scripts 目錄複製過去。
    """
    import shutil

    target = repo / "scripts"
    if not target.exists():
        shutil.copytree(SCRIPTS_DIR, target)
    return subprocess.run(
        [sys.executable, str(target / "guard-selfcheck.py")],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        cwd=str(repo), env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
    )


@case("selfcheck 會抓出不在受保護路徑內的隱藏測試目錄（P1-3）")
def _(tmp: Path):
    # monorepo 把 packages/api/tests/hidden 當隱藏測試用時，預設設定碰不到它——
    # 使用者會以為有保護。這個檢查就是要讓它出聲。
    (tmp / "tests" / "hidden").mkdir(parents=True)
    nested = tmp / "packages" / "api" / "tests" / "hidden"
    nested.mkdir(parents=True)
    (nested / "test_secret.py").write_text("assert True\n", encoding="utf-8")

    result = selfcheck_in(tmp)
    assert "packages/api/tests/hidden" in result.stdout, result.stdout


@case("目錄裡的 README 寫明「不受保護」之後，selfcheck 就不再重複警告")
def _(tmp: Path):
    # 一個總是在響的警告等於沒有警告（P3-1 修掉的正是這種失效方式）。
    (tmp / "tests" / "hidden").mkdir(parents=True)
    nested = tmp / "examples" / "tests" / "hidden"
    nested.mkdir(parents=True)
    (nested / "test_demo.py").write_text("assert True\n", encoding="utf-8")
    (nested / "README.md").write_text("這個目錄的隱藏測試不受保護，純示範。\n", encoding="utf-8")

    result = selfcheck_in(tmp)
    assert "examples/tests/hidden" not in result.stdout, result.stdout


@case("repo 自己的範例隱藏測試目錄已經標註過「不受保護」")
def _(tmp: Path):
    example = (
        SCRIPTS_DIR.parent / "templates" / "examples" / "demo-fizzbuzz" / "tests" / "hidden"
    )
    if not example.exists():
        return
    readme = example / "README.md"
    assert readme.exists(), "範例的隱藏測試目錄少了說明它不受保護的 README"
    assert "不受保護" in readme.read_text(encoding="utf-8"), readme


@case("settings.json 的 allow 清單涵蓋每一組回歸測試（不然預設沒人會跑它）")
def _(tmp: Path):
    # P2-5 的一個具體案例：test-guards.py 曾經是這個 repo 唯一的自動化測試，
    # 卻沒被寫進 allow 清單也沒寫進 README——等於預設沒人會跑它。
    # 新增一組測試卻忘了同步的話，這裡會紅。
    settings = json.loads(
        (SCRIPTS_DIR.parent / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    allow = "\n".join(settings["permissions"]["allow"])
    missing = [
        path.name
        for path in sorted(SCRIPTS_DIR.glob("test-*.py"))
        if path.name not in allow
    ]
    assert not missing, f"settings.json 的 allow 清單少了：{missing}"


@case("README 的回歸測試指令涵蓋每一組測試")
def _(tmp: Path):
    readme = (SCRIPTS_DIR.parent / "README.md").read_text(encoding="utf-8")
    missing = [
        path.name
        for path in sorted(SCRIPTS_DIR.glob("test-*.py"))
        if path.name not in readme
    ]
    assert not missing, f"README.md 沒提到：{missing}（等於預設沒人會跑它）"


@case("CI 會跑每一組回歸測試")
def _(tmp: Path):
    workflow = (SCRIPTS_DIR.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    missing = [
        path.name
        for path in sorted(SCRIPTS_DIR.glob("test-*.py"))
        if path.name not in workflow
    ]
    assert not missing, f"ci.yml 沒跑：{missing}（機制退化是無聲的，沒進 CI 等於沒有保護）"


@case("settings.json 的 PreToolUse matcher 涵蓋 guard 實際處理的每一種工具")
def _(tmp: Path):
    # 舊的 matcher 是 "Edit|Write|Bash|Read|Grep|Glob"，能擋到 MultiEdit/NotebookEdit
    # 純粹是因為名稱剛好含有 "Edit"。這個案例確保兩邊不會再漂移——
    # 有人在 guard 新增一種工具、卻忘了改 matcher 時，這裡會紅。
    import re

    settings = json.loads(
        (SCRIPTS_DIR.parent / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    matchers = [
        entry.get("matcher", "")
        for entry in settings["hooks"]["PreToolUse"]
        if any(
            "guard-hidden-tests.py" in hook.get("command", "")
            for hook in entry.get("hooks", [])
        )
    ]
    assert matchers, "settings.json 裡找不到掛 guard-hidden-tests.py 的 PreToolUse hook"

    covered = "|".join(matchers)
    expected = ["Bash", "Edit", "MultiEdit", "Write", "NotebookEdit", "Read", "Grep", "Glob"]
    missing = [tool for tool in expected if not re.search(covered, tool)]
    assert not missing, f"matcher 沒涵蓋到這些工具：{missing}（目前 matcher：{covered}）"


@case("settings.json 的 hook 指令用 $CLAUDE_PROJECT_DIR 絕對路徑（P0-5）")
def _(tmp: Path):
    settings = json.loads(
        (SCRIPTS_DIR.parent / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    commands = [
        hook.get("command", "")
        for event in settings["hooks"].values()
        for entry in event
        for hook in entry.get("hooks", [])
    ]
    assert commands, "settings.json 裡沒有任何 hook 指令"
    for command in commands:
        assert "$CLAUDE_PROJECT_DIR" in command, (
            f"hook 指令用了相對路徑：{command}——工作目錄不是 repo 根時會靜默失效"
        )


# ------------------------------------------ 舊代碼頁主控台（Windows）（P3-3）
#
# 這批案例是 CI 抓出來的：本模板所有腳本都印繁體中文，而 Python 在 Windows 上
# 預設用系統 ANSI 代碼頁編 stdout，結果每一支腳本一 print 就丟 UnicodeEncodeError。
# 用 PYTHONIOENCODING=cp1252 就能在任何平台重現，所以測試不需要真的跑在 Windows。


def run_with_legacy_console(script: Path, cwd: Path, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "CLAUDE_PROJECT_DIR": str(cwd)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(cwd),
        env=env,
    )


@case("舊代碼頁主控台下，guard 擋下時仍必須 exit 2（不能因為印不出中文變成放行）")
def _(tmp: Path):
    # 這是最危險的情境：print 崩潰會讓 exit code 從 2 變成 1，
    # 而 Claude Code 只把 2 當成 blocking error——「擋下」會悄悄變成「放行」。
    result = subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": "tests/hidden/x.py"}}),
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(tmp),
        env={**os.environ, "PYTHONIOENCODING": "cp1252", "CLAUDE_PROJECT_DIR": str(tmp)},
    )
    assert result.returncode == 2, (
        f"exit={result.returncode}（在舊代碼頁主控台下失去攔截能力）stderr={result.stderr}"
    )
    assert "UnicodeEncodeError" not in result.stderr, result.stderr


@case("舊代碼頁主控台下，所有入口腳本都不會因為印中文而崩潰")
def _(tmp: Path):
    checks = [
        ("machine-profile.py", ()),
        ("service-scan.py", ()),
        ("env-guard.py", ()),
        ("lock-tests.py", ()),
        ("verify-locks.py", ()),
        ("guard-selfcheck.py", ()),
        ("seal-hidden-tests.py", ("--help",)),
        ("run-hidden-tests.py", ("--help",)),
    ]
    broken = []
    for name, args in checks:
        result = run_with_legacy_console(SCRIPTS_DIR / name, tmp, *args)
        if "UnicodeEncodeError" in result.stderr or "UnicodeEncodeError" in result.stdout:
            broken.append(name)
    assert not broken, f"這些腳本在舊代碼頁主控台下會崩潰：{broken}"


@case("所有腳本都明確指定編碼，不依賴系統預設（靜態檢查）")
def _(tmp: Path):
    # 這類 bug 在這個 repo 已經咬了兩次（主控台輸出、檔案讀寫），而且都只在
    # 非 UTF-8 環境才會發作，靠人工 review 很難每次都抓到。改用 AST 靜態檢查：
    #   - read_text / write_text 必須帶 encoding
    #   - open() 的文字模式必須帶 encoding
    #   - subprocess 的 text=True 必須帶 encoding（否則用系統預設解子行程輸出）
    import ast

    problems = []
    for script in sorted(SCRIPTS_DIR.glob("*.py")):
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kwargs = {kw.arg for kw in node.keywords}
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )

            if name in ("read_text", "write_text") and "encoding" not in kwargs:
                problems.append(f"{script.name}:{node.lineno} {name}() 沒有指定 encoding")

            if name == "open" and "encoding" not in kwargs:
                # 二進位模式不需要 encoding。注意 mode 的位置不一樣：
                # 內建 open(file, mode) 在 args[1]，Path.open(mode) 在 args[0]。
                mode_index = 0 if isinstance(node.func, ast.Attribute) else 1
                mode = ""
                if len(node.args) > mode_index and isinstance(node.args[mode_index], ast.Constant):
                    mode = str(node.args[mode_index].value)
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = str(kw.value.value)
                if "b" not in mode:
                    problems.append(f"{script.name}:{node.lineno} open() 沒有指定 encoding")

            if name in ("run", "check_output", "Popen") and "encoding" not in kwargs:
                for kw in node.keywords:
                    if kw.arg in ("text", "universal_newlines") and getattr(
                        kw.value, "value", False
                    ) is True:
                        problems.append(
                            f"{script.name}:{node.lineno} subprocess 用了 text=True 卻沒指定 encoding"
                        )

    assert not problems, "以下位置依賴系統預設編碼，在非 UTF-8 環境會炸：\n  " + "\n  ".join(
        problems
    )


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
