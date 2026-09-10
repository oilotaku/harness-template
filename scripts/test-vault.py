#!/usr/bin/env python3
"""test-vault.py — seal-hidden-tests.py / run-hidden-tests.py 的離線回歸測試

對應 docs/improvement-suggestions.md 的 P1-1。與 test-guards.py、test-locks.py
同樣的作法：在暫存目錄裡造一個最小的 repo，用 subprocess 執行腳本並斷言結果。

這批測試守的是隔離機制的四個核心承諾：
  1. 封存後工作目錄裡沒有明文
  2. 封存檔案的內容是密文（就算被讀到也拿不到題目）
  3. manifest 不含權杖本身
  4. 沒有正確權杖就跑不動隱藏測試

用法：python3 scripts/test-vault.py
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
SEAL = SCRIPTS_DIR / "seal-hidden-tests.py"
RUN = SCRIPTS_DIR / "run-hidden-tests.py"

CASES = []

PASSING_TEST = """import unittest

from impl import double


class T(unittest.TestCase):
    def test_double(self):
        self.assertEqual(double(21), 42)

    def test_repo_root_env_is_available(self):
        import os
        self.assertTrue(os.environ.get("HARNESS_REPO_ROOT"))
"""

FAILING_TEST = """import unittest

from impl import double


class T(unittest.TestCase):
    def test_double(self):
        self.assertEqual(double(21), 999)
"""


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def make_repo(tmp: Path, hidden_source: str = PASSING_TEST) -> Path:
    """造一個最小的 repo：一份實作 + 一份隱藏測試。"""
    repo = tmp / "repo"
    (repo / "tests" / "hidden").mkdir(parents=True)
    (repo / "impl.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    (repo / "tests" / "hidden" / "test_hidden.py").write_text(hidden_source, encoding="utf-8")
    return repo


def run_script(script: Path, repo: Path, *args, extra_env=None) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(repo),
        env=env,
    )


def seal(repo: Path, task_id: str = "T1", **kwargs) -> str:
    """封存並回傳權杖。"""
    result = run_script(SEAL, repo, "--task-id", task_id, **kwargs)
    assert result.returncode == 0, f"封存失敗：{result.stdout}\n{result.stderr}"
    for line in result.stdout.splitlines():
        if line.startswith("執行權杖"):
            return line.split("：", 1)[1].strip()
    raise AssertionError(f"輸出裡找不到權杖：{result.stdout}")


def manifest_of(repo: Path) -> dict:
    return json.loads((repo / ".harness" / "hidden-manifest.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 封存的承諾


@case("封存後 tests/hidden/ 底下不再有任何隱藏測試明文")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)

    leftovers = [
        p for p in (repo / "tests" / "hidden").rglob("*") if p.is_file() and p.name != "README.md"
    ]
    assert not leftovers, f"工作目錄裡仍留有隱藏測試：{leftovers}"


@case("封存目錄落在 repo 之外（工具的專案目錄邊界就擋得住）")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)

    task_dir = Path(manifest_of(repo)["tasks"]["T1"]["task_dir"])
    assert task_dir.exists(), f"找不到封存目錄：{task_dir}"
    try:
        task_dir.relative_to(repo)
        raise AssertionError(f"封存目錄竟然在 repo 裡面：{task_dir}")
    except ValueError:
        pass  # 這才是預期：不在 repo 底下


@case("封存檔案是密文：直接讀取拿不到題目內容")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)

    task_dir = Path(manifest_of(repo)["tasks"]["T1"]["task_dir"])
    blob = (task_dir / "test_hidden.py.enc").read_bytes()
    assert blob != PASSING_TEST.encode("utf-8"), "封存檔案竟然是明文"
    assert b"double" not in blob, "密文裡出現了明文片段"
    assert b"unittest" not in blob, "密文裡出現了明文片段"


@case("manifest 只存權杖指紋，不存權杖本身")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    raw = (repo / ".harness" / "hidden-manifest.json").read_text(encoding="utf-8")
    assert token not in raw, "manifest 裡竟然出現了權杖明文"
    info = manifest_of(repo)["tasks"]["T1"]
    assert info["token_sha256"] == hashlib.sha256(token.encode()).hexdigest(), "指紋不符"


@case("manifest 不含隱藏測試的內容，只有路徑與雜湊")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)

    raw = (repo / ".harness" / "hidden-manifest.json").read_text(encoding="utf-8")
    assert "double" not in raw, "manifest 洩漏了隱藏測試內容"
    assert manifest_of(repo)["tasks"]["T1"]["files"][0]["path"] == "test_hidden.py"


@case("tests/hidden/ 沒有檔案時，封存會明確報錯而不是產生空的封存")
def _(tmp: Path):
    repo = tmp / "repo"
    (repo / "tests" / "hidden").mkdir(parents=True)
    result = run_script(SEAL, repo, "--task-id", "T1")
    assert result.returncode == 1, f"exit={result.returncode} stdout={result.stdout}"
    assert "沒有隱藏測試檔案" in result.stderr, result.stderr


@case("HARNESS_HIDDEN_DIR 指到 repo 裡面時，封存必須被拒絕")
def _(tmp: Path):
    repo = make_repo(tmp)
    result = run_script(
        SEAL, repo, "--task-id", "T1", extra_env={"HARNESS_HIDDEN_DIR": str(repo / "vault")}
    )
    assert result.returncode == 1, f"exit={result.returncode} stdout={result.stdout}"
    assert "落在 repo" in result.stderr, result.stderr
    assert (repo / "tests" / "hidden" / "test_hidden.py").exists(), "拒絕封存卻把明文刪掉了"


# ------------------------------------------------------------------ 執行的承諾


@case("拿正確權杖可以執行隱藏測試，全部通過時回傳 0")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"exit={result.returncode} stdout={result.stdout}\n{result.stderr}"
    assert "全部通過" in result.stdout, result.stdout


@case("隱藏測試有失敗時回傳 1（驗收判定不通過的依據）")
def _(tmp: Path):
    repo = make_repo(tmp, hidden_source=FAILING_TEST)
    token = seal(repo)

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 1, f"exit={result.returncode} stdout={result.stdout}"
    assert "未全部通過" in result.stdout, result.stdout


@case("權杖錯誤時拒絕解密（回傳 2），且不洩漏任何內容")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)

    result = run_script(RUN, repo, "--task-id", "T1", "--token", "0" * 32)
    assert result.returncode == 2, f"exit={result.returncode} stdout={result.stdout}"
    assert "權杖錯誤" in result.stderr, result.stderr
    assert "double" not in result.stdout, "輸出洩漏了隱藏測試內容"


@case("沒有給權杖就直接執行時回傳 2")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)

    result = run_script(RUN, repo, "--task-id", "T1")
    assert result.returncode == 2, f"exit={result.returncode} stdout={result.stdout}"


@case("封存檔案被竄改時，解密後的雜湊比對會抓到並拒絕執行")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    blob = Path(manifest_of(repo)["tasks"]["T1"]["task_dir"]) / "test_hidden.py.enc"
    blob.write_bytes(b"x" * 64)

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2, f"exit={result.returncode} stdout={result.stdout}"
    assert "被竄改" in result.stderr, result.stderr


@case("封存檔案遺失時回傳 2 並指出是哪一個檔案")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    (Path(manifest_of(repo)["tasks"]["T1"]["task_dir"]) / "test_hidden.py.enc").unlink()

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2, f"exit={result.returncode} stdout={result.stdout}"
    assert "遺失" in result.stderr, result.stderr


@case("不存在的 task_id 回傳 2")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    result = run_script(RUN, repo, "--task-id", "NO-SUCH-TASK", "--token", token)
    assert result.returncode == 2, f"exit={result.returncode} stdout={result.stdout}"


@case("--list 不需要權杖，也不會洩漏隱藏測試內容")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)

    result = run_script(RUN, repo, "--list")
    assert result.returncode == 0, f"exit={result.returncode} stdout={result.stdout}"
    assert "T1" in result.stdout, result.stdout
    assert "double" not in result.stdout, "列表洩漏了隱藏測試內容"


@case("重新封存同一個 task：舊權杖失效、新權杖可用、舊密文不殘留")
def _(tmp: Path):
    repo = make_repo(tmp)
    old_token = seal(repo)

    (repo / "tests" / "hidden").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "hidden" / "test_hidden.py").write_text(PASSING_TEST, encoding="utf-8")
    new_token = seal(repo)

    assert old_token != new_token, "重新封存應該產生新的權杖"

    old_result = run_script(RUN, repo, "--task-id", "T1", "--token", old_token)
    assert old_result.returncode == 2, f"舊權杖竟然還能用：{old_result.stdout}"

    new_result = run_script(RUN, repo, "--task-id", "T1", "--token", new_token)
    assert new_result.returncode == 0, f"新權杖不能用：{new_result.stdout}\n{new_result.stderr}"


@case("執行時 cwd 是 repo 根目錄、PYTHONPATH 含 repo 根（測試才 import 得到實作）")
def _(tmp: Path):
    # PASSING_TEST 直接 `from impl import double`，只有在 PYTHONPATH 正確時才會過；
    # 它同時斷言 HARNESS_REPO_ROOT 有值。這條案例守的是隱藏測試的撰寫契約。
    repo = make_repo(tmp)
    token = seal(repo)

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"exit={result.returncode} stdout={result.stdout}\n{result.stderr}"


@case("執行結束後，解密用的暫存目錄會被刪掉")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    before = set(Path(tempfile.gettempdir()).glob("harness-hidden-*"))
    run_script(RUN, repo, "--task-id", "T1", "--token", token)
    after = set(Path(tempfile.gettempdir()).glob("harness-hidden-*"))
    assert after <= before, f"暫存目錄沒有被清掉：{after - before}"


@case("多個 task 各自獨立：T1 的權杖不能拿來解 T2")
def _(tmp: Path):
    repo = make_repo(tmp)
    token1 = seal(repo, task_id="T1")

    (repo / "tests" / "hidden").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "hidden" / "test_hidden.py").write_text(PASSING_TEST, encoding="utf-8")
    token2 = seal(repo, task_id="T2")

    crossed = run_script(RUN, repo, "--task-id", "T2", "--token", token1)
    assert crossed.returncode == 2, f"T1 的權杖竟然解得開 T2：{crossed.stdout}"

    proper = run_script(RUN, repo, "--task-id", "T2", "--token", token2)
    assert proper.returncode == 0, f"{proper.stdout}\n{proper.stderr}"


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
