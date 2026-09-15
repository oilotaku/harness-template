#!/usr/bin/env python3
"""test-protection-levels.py — `protection` 設定（minimal / full、CI 驗收宣告）的回歸測試

對應第四輪 P1：提供一個 minimal 模式。

現況是「全有或全無」——四層一起開，而四層對大多數專案過重（要裝 hook、
要維持公開測試鎖定的紀律）。minimal 只留第 1 層（實體隔離），
用三支腳本與十行設定就能得到「隱藏測試 implementer 看不到」這件事。

這批測試守的是兩個承諾，而第二個比第一個重要：

  1. minimal 真的少跑那三層（不鎖公開測試、不做探測、不做事後稽核）
  2. **少了哪幾層每一次都會被講出來**——封存時講、驗收時講、每個 session 講。
     保護少一層而沒有訊號，比沒有保護更危險：後者至少沒有人誤以為自己受保護。

用法：python3 scripts/test-protection-levels.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
SEAL = SCRIPTS_DIR / "seal-hidden-tests.py"
RUN = SCRIPTS_DIR / "run-hidden-tests.py"
SELFCHECK = SCRIPTS_DIR / "guard-selfcheck.py"
EXPORT = SCRIPTS_DIR / "export-sealed-task.py"
DOC = SCRIPTS_DIR.parent / "docs" / "protection-levels.md"

CASES = []

HIDDEN_TEST = """import unittest

from impl import double


class T(unittest.TestCase):
    def test_double(self):
        self.assertEqual(double(21), 42)
"""

PUBLIC_TEST = """import unittest

from impl import double


class T(unittest.TestCase):
    def test_public(self):
        self.assertEqual(double(1), 2)
"""


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def make_repo(tmp: Path, protection=None) -> Path:
    repo = tmp / "repo"
    (repo / "tests" / "hidden").mkdir(parents=True)
    (repo / "tests" / "public").mkdir(parents=True)
    (repo / "impl.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    (repo / "tests" / "hidden" / "test_hidden.py").write_text(HIDDEN_TEST, encoding="utf-8")
    (repo / "tests" / "public" / "test_public.py").write_text(PUBLIC_TEST, encoding="utf-8")
    if protection is not None:
        write_config(repo, {"protection": protection})
    return repo


def write_config(repo: Path, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    (repo / harness_config.CONFIG_FILENAME).write_text(text, encoding="utf-8")


def run_script(script: Path, repo: Path, *args):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(repo),
        env=env,
    )


def seal(repo: Path, task_id: str = "T1"):
    return run_script(SEAL, repo, "--task-id", task_id)


def token_of(result) -> str:
    for line in result.stdout.splitlines():
        if line.startswith("執行權杖"):
            return line.split("：", 1)[1].strip()
    raise AssertionError(f"輸出裡找不到權杖：{result.stdout}")


def locked_list(repo: Path) -> Path:
    return repo / ".harness" / "locked-tests.list"


# ----------------------------------------------------------------- 設定的驗證


@case("預設是 full，而且 CI 驗收預設不開")
def _(tmp: Path):
    repo = make_repo(tmp)
    config = harness_config.load(repo)
    assert config["protection"] == {"level": "full", "ci_verification": False}, config["protection"]


@case("level 打錯字一律報錯，不靜靜退回預設值")
def _(tmp: Path):
    repo = make_repo(tmp, {"level": "minimum"})
    try:
        harness_config.load(repo)
    except harness_config.ConfigError as exc:
        assert "protection.level" in str(exc), exc
        return
    raise AssertionError("打錯的 level 竟然被接受了——用一個 typo 就能改變保護等級")


@case("ci_verification 不是布林值時報錯")
def _(tmp: Path):
    repo = make_repo(tmp, {"ci_verification": "true"})
    try:
        harness_config.load(repo)
    except harness_config.ConfigError as exc:
        assert "ci_verification" in str(exc), exc
        return
    raise AssertionError("字串 \"true\" 竟然被當成 true")


@case("protection 裡出現無法辨識的欄位時報錯")
def _(tmp: Path):
    repo = make_repo(tmp, {"levels": "minimal"})
    try:
        harness_config.load(repo)
    except harness_config.ConfigError as exc:
        assert "無法辨識" in str(exc), exc
        return
    raise AssertionError("欄位名打錯竟然被忽略——那等於設定沒生效而且沒有訊號")


# ------------------------------------------------------------- minimal 的行為


@case("minimal：封存不鎖定公開測試，而且明講這件事")
def _(tmp: Path):
    repo = make_repo(tmp, {"level": "minimal"})
    result = seal(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not locked_list(repo).exists(), "minimal 竟然還是鎖定了公開測試"
    assert "minimal" in result.stdout, f"沒有講出保護等級：{result.stdout}"
    assert "公開測試被改不會有人抓到" in result.stdout, result.stdout

    manifest = json.loads((repo / ".harness" / "hidden-manifest.json").read_text(encoding="utf-8"))
    assert manifest["tasks"]["T1"]["locked_tests_sha256"] is None, manifest["tasks"]["T1"]


@case("full（預設）：封存會鎖定公開測試")
def _(tmp: Path):
    repo = make_repo(tmp)
    result = seal(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert locked_list(repo).exists(), "full 竟然沒有鎖定公開測試"


@case("minimal：第 1 層（實體隔離）完整保留——封存後沒有明文、權杖錯就跑不動")
def _(tmp: Path):
    repo = make_repo(tmp, {"level": "minimal"})
    result = seal(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    leftovers = [p.name for p in (repo / "tests" / "hidden").rglob("*") if p.is_file()]
    assert leftovers == [], f"暫存區還有明文：{leftovers}"

    bad = run_script(RUN, repo, "--task-id", "T1", "--token", "0" * 32)
    assert bad.returncode == 2, bad.stdout + bad.stderr


@case("minimal：驗收時會講出「這次沒有鎖定與稽核」")
def _(tmp: Path):
    repo = make_repo(tmp, {"level": "minimal"})
    token = token_of(seal(repo))
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "minimal" in result.stdout, f"驗收時沒有講出保護等級：{result.stdout}"
    assert "驗收報告要寫明" in result.stdout, result.stdout


@case("full：驗收時不會冒出 minimal 的警告（不要製造會被習慣性忽略的雜訊）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = token_of(seal(repo))
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "minimal" not in result.stdout, result.stdout


@case("設定壞掉時驗收直接擋下（exit 2），不猜也不退回預設值")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = token_of(seal(repo))
    write_config(repo, '{"protection": {"level": "nope"}}')
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2, f"設定壞掉竟然不是 exit 2：{result.stdout}\n{result.stderr}"
    assert "保護等級" in result.stderr, result.stderr


@case("設定壞掉時封存也直接擋下")
def _(tmp: Path):
    repo = make_repo(tmp)
    write_config(repo, '{"protection": {"level": "nope"}}')
    result = seal(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "保護等級" in result.stderr, result.stderr


# ------------------------------------------------- SessionStart 每次都要講等級


@case("自我檢查：minimal 會列出沒有開的那幾層，而且不會印一整片假失敗")
def _(tmp: Path):
    repo = make_repo(tmp, {"level": "minimal"})
    result = run_script(SELFCHECK, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "保護等級：**minimal**" in result.stdout, result.stdout
    assert "沒有開的三層" in result.stdout, result.stdout
    # minimal 沒裝 hook，探測一定全失敗；照跑只會每個 session 印一片紅字，
    # 而「習慣忽略警告」比少一層保護更難修。
    assert "項檢查失敗" not in result.stdout, result.stdout


@case("自我檢查：full 也會把等級講出來")
def _(tmp: Path):
    repo = make_repo(tmp)
    result = run_script(SELFCHECK, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "保護等級：full" in result.stdout, result.stdout


# --------------------------------------------------------------- CI 驗收的宣告


@case("宣告 CI 驗收之後，封存會順手匯出密文包")
def _(tmp: Path):
    repo = make_repo(tmp, {"ci_verification": True})
    result = seal(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    sealed = repo / harness_config.CI_SEALED_DIR / "T1"
    assert (sealed / "entry.json").is_file(), f"沒有匯出密文包：{result.stdout}"
    assert (sealed / "test_hidden.py.enc").is_file(), sorted(p.name for p in sealed.iterdir())
    assert "預設分支" in result.stdout, "沒有講出密文包要 commit 到哪裡"


@case("沒宣告 CI 驗收時不會擅自產生密文包")
def _(tmp: Path):
    repo = make_repo(tmp)
    assert seal(repo).returncode == 0
    assert not (repo / harness_config.CI_SEALED_DIR).exists()


@case("宣告了 CI 驗收卻沒有密文包時，自我檢查會警告")
def _(tmp: Path):
    repo = make_repo(tmp, {"ci_verification": True})
    result = run_script(SELFCHECK, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "沒有任何密文包" in result.stdout, result.stdout
    # 這個漂移的可怕之處在於它安靜：CI 上會因為「沒東西可驗」而回 0。
    assert "跟「全部通過」長得一樣" in result.stdout, result.stdout


@case("基線執行會提醒重新匯出密文包（基線改寫了簽過章的項目）")
def _(tmp: Path):
    repo = make_repo(tmp, {"ci_verification": True})
    # 隱藏測試在沒有實作時要是紅的，基線才有意義。
    (repo / "impl.py").write_text("def double(n):\n    return n\n", encoding="utf-8")
    token = token_of(seal(repo))
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token, "--baseline")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "重新匯出密文包" in result.stdout, f"沒有提醒重匯：{result.stdout}"


@case("密文包比 manifest 舊時，自我檢查抓得出來")
def _(tmp: Path):
    repo = make_repo(tmp, {"ci_verification": True})
    (repo / "impl.py").write_text("def double(n):\n    return n\n", encoding="utf-8")
    token = token_of(seal(repo))
    # 基線執行會重新簽章，密文包裡那一份就過期了。
    assert run_script(RUN, repo, "--task-id", "T1", "--token", token, "--baseline").returncode == 0

    result = run_script(SELFCHECK, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "比 manifest 舊" in result.stdout, f"沒有抓到過期的密文包：{result.stdout}"

    # 重匯之後就不該再吵——會一直出現的警告等於沒有警告。
    export = run_script(EXPORT, repo, "--task-id", "T1", "--token", token)
    assert export.returncode == 0, export.stdout + export.stderr
    again = run_script(SELFCHECK, repo)
    assert "比 manifest 舊" not in again.stdout, again.stdout


@case("宣告了 CI 驗收而且密文包在，自我檢查就只是報告")
def _(tmp: Path):
    repo = make_repo(tmp, {"ci_verification": True})
    assert seal(repo).returncode == 0
    result = run_script(SELFCHECK, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CI 驗收：已宣告" in result.stdout, result.stdout
    assert "沒有任何密文包" not in result.stdout, result.stdout


# ------------------------------------------------------------- 文件不能漂掉


@case("每一個保護等級都在 docs/protection-levels.md 裡有說明")
def _(tmp: Path):
    assert DOC.is_file(), f"找不到 {DOC}"
    text = DOC.read_text(encoding="utf-8")
    missing = [level for level in harness_config.PROTECTION_LEVELS if level not in text]
    assert not missing, f"文件沒有說明這些等級：{missing}"


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
