#!/usr/bin/env python3
"""test-locks.py — lock-tests.py 與 verify-locks.py 的離線回歸測試

作法與 test-guards.py 相同：在暫存目錄裡準備最小情境，用 subprocess 執行腳本，
斷言 exit code 與輸出內容符合預期。之所以用 subprocess 而不是直接 import，
是因為兩支腳本都以「repo 根目錄」為基準（CLAUDE_PROJECT_DIR / cwd），
用子行程才能乾淨地換掉那個基準。

對應 docs/improvement-suggestions.md 的 P1-2：鎖定清單改成帶 sha256 的格式，
並新增 verify-locks.py 做事後稽核。這批測試存在的理由跟 test-guards.py 一樣——
稽核機制本身壞掉時，必須有人會發現。

用法：python3 scripts/test-locks.py
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
LOCK_SCRIPT = SCRIPTS_DIR / "lock-tests.py"
VERIFY_SCRIPT = SCRIPTS_DIR / "verify-locks.py"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def run(script: Path, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script)],
        text=True,
        capture_output=True,
        cwd=cwd,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)},
    )


def touch(tmp: Path, path: str, content: str = "assert True\n") -> Path:
    target = tmp / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def locked_list(tmp: Path) -> str:
    return (tmp / ".harness" / "locked-tests.list").read_text(encoding="utf-8")


# ------------------------------------------------------------------ lock-tests


@case("lock-tests 產生的每一行都是 `<sha256>  <路徑>`，且雜湊與檔案內容相符")
def _(tmp: Path):
    target = touch(tmp, "tests/public/test_a.py")
    result = run(LOCK_SCRIPT, tmp)
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"

    lines = [line for line in locked_list(tmp).splitlines() if line.strip()]
    assert len(lines) == 1, f"預期 1 行，實際：{lines}"
    digest, path = lines[0].split(None, 1)
    assert path == "tests/public/test_a.py", f"路徑不符：{path}"
    assert digest == hashlib.sha256(target.read_bytes()).hexdigest(), "雜湊與檔案內容不符"


@case("lock-tests 會鎖定巢狀子目錄的檔案，並忽略 .gitkeep")
def _(tmp: Path):
    touch(tmp, "tests/public/test_a.py")
    touch(tmp, "tests/public/nested/test_b.py")
    touch(tmp, "tests/public/.gitkeep", "")
    run(LOCK_SCRIPT, tmp)

    paths = {line.split(None, 1)[1] for line in locked_list(tmp).splitlines() if line.strip()}
    assert paths == {"tests/public/test_a.py", "tests/public/nested/test_b.py"}, paths


@case("lock-tests 是覆寫而非累加：上一輪任務殘留的路徑不會被留下")
def _(tmp: Path):
    touch(tmp, "tests/public/test_old.py")
    run(LOCK_SCRIPT, tmp)
    (tmp / "tests/public/test_old.py").unlink()
    touch(tmp, "tests/public/test_new.py")
    run(LOCK_SCRIPT, tmp)

    content = locked_list(tmp)
    assert "test_new.py" in content, content
    assert "test_old.py" not in content, f"舊路徑殘留：{content}"


@case("tests/public/ 不存在時，lock-tests 把清單清空而不是報錯")
def _(tmp: Path):
    result = run(LOCK_SCRIPT, tmp)
    assert result.returncode == 0, f"exit={result.returncode} stderr={result.stderr}"
    assert locked_list(tmp) == "", f"清單應為空，實際：{locked_list(tmp)!r}"


# ----------------------------------------------------------------- verify-locks


@case("公開測試沒被動過時，verify-locks 回傳 0")
def _(tmp: Path):
    touch(tmp, "tests/public/test_a.py")
    run(LOCK_SCRIPT, tmp)

    result = run(VERIFY_SCRIPT, tmp)
    assert result.returncode == 0, f"exit={result.returncode} stdout={result.stdout}"
    assert "正常" in result.stdout, result.stdout


@case("公開測試被竄改時，verify-locks 回傳 1 並指出是哪個檔案")
def _(tmp: Path):
    touch(tmp, "tests/public/test_a.py")
    run(LOCK_SCRIPT, tmp)
    # 模擬 implementer 用某種繞過方式改掉斷言，讓自己的實作剛好通過
    touch(tmp, "tests/public/test_a.py", "assert True  # 被改過\n")

    result = run(VERIFY_SCRIPT, tmp)
    assert result.returncode == 1, f"exit={result.returncode} stdout={result.stdout}"
    assert "被竄改" in result.stdout, result.stdout
    assert "tests/public/test_a.py" in result.stdout, result.stdout


@case("公開測試被刪除時，verify-locks 回傳 1")
def _(tmp: Path):
    touch(tmp, "tests/public/test_a.py")
    run(LOCK_SCRIPT, tmp)
    (tmp / "tests/public/test_a.py").unlink()

    result = run(VERIFY_SCRIPT, tmp)
    assert result.returncode == 1, f"exit={result.returncode} stdout={result.stdout}"
    assert "遺失" in result.stdout, result.stdout


@case("只有部分檔案被竄改時，未被動過的檔案仍標示為相符")
def _(tmp: Path):
    touch(tmp, "tests/public/test_a.py")
    touch(tmp, "tests/public/test_b.py")
    run(LOCK_SCRIPT, tmp)
    touch(tmp, "tests/public/test_b.py", "# 被改過\n")

    result = run(VERIFY_SCRIPT, tmp)
    assert result.returncode == 1, f"exit={result.returncode} stdout={result.stdout}"
    assert "✅ 相符：tests/public/test_a.py" in result.stdout, result.stdout
    assert "❌ 內容被竄改：tests/public/test_b.py" in result.stdout, result.stdout


@case("沒有鎖定清單時，verify-locks 回傳 2 並明確警告「沒有鎖定保護」")
def _(tmp: Path):
    result = run(VERIFY_SCRIPT, tmp)
    assert result.returncode == 2, f"exit={result.returncode} stdout={result.stdout}"
    assert "沒有公開測試鎖定保護" in result.stdout, result.stdout


@case("舊格式（純路徑、無雜湊）清單回傳 2：可以擋寫入，但無法事後驗證")
def _(tmp: Path):
    touch(tmp, "tests/public/test_a.py")
    (tmp / ".harness").mkdir()
    (tmp / ".harness" / "locked-tests.list").write_text(
        "tests/public/test_a.py\n", encoding="utf-8"
    )

    result = run(VERIFY_SCRIPT, tmp)
    assert result.returncode == 2, f"exit={result.returncode} stdout={result.stdout}"
    assert "無法驗證" in result.stdout, result.stdout


@case("空的鎖定清單回傳 2（沒有東西可驗證，不能當成通過）")
def _(tmp: Path):
    (tmp / ".harness").mkdir()
    (tmp / ".harness" / "locked-tests.list").write_text("", encoding="utf-8")

    result = run(VERIFY_SCRIPT, tmp)
    assert result.returncode == 2, f"exit={result.returncode} stdout={result.stdout}"


@case("lock → verify 全流程：鎖定後立刻驗證應相符（含巢狀目錄）")
def _(tmp: Path):
    touch(tmp, "tests/public/test_a.py")
    touch(tmp, "tests/public/nested/test_b.py")
    run(LOCK_SCRIPT, tmp)

    result = run(VERIFY_SCRIPT, tmp)
    assert result.returncode == 0, f"exit={result.returncode} stdout={result.stdout}"
    assert "2 個公開測試" in result.stdout, result.stdout


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
