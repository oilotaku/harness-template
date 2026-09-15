#!/usr/bin/env python3
"""test-ci-verify.py — CI 驗收（export-sealed-task.py / ci-verify.py / read-ci-report.py）的離線回歸測試

對應第四輪 P0：把驗收搬出 implementer 的執行環境。作法與 test-vault.py 相同——
在暫存目錄裡造一個最小的 repo，用 subprocess 執行腳本並斷言結果。

這批測試守的是六個承諾：
  1. 密文包進 repo 是安全的：裡面沒有明文，改了就簽不回去
  2. 驗收跑得起來，而且失敗與通過分得清楚（零測試假通過算失敗）
  3. **job log 裡沒有隱藏測試的內容**——這是 CI 驗收跟本機驗收最大的差別，
     log 誰都看得到，印出失敗明細等於把隱藏測試變成可反覆查詢的 oracle
  4. 被測的程式碼拿不到權杖，也拿不到 CI 的各種 token
  5. 沒有權杖時是「無法驗收」，不是「跳過」——跳過跟通過在 exit code 上長得一樣
  6. workflow 的信任根不會被悄悄改掉（預設分支的 workflow、同源分支限定、
     PR 程式碼不帶 checkout 憑證）

用法：python3 scripts/test-ci-verify.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hidden_vault as vault  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
SEAL = SCRIPTS_DIR / "seal-hidden-tests.py"
EXPORT = SCRIPTS_DIR / "export-sealed-task.py"
CI_VERIFY = SCRIPTS_DIR / "ci-verify.py"
READ_REPORT = SCRIPTS_DIR / "read-ci-report.py"
WORKFLOW = SCRIPTS_DIR.parent / ".github" / "workflows" / "verify-hidden-tests.yml"

CASES = []

PASSING_TEST = """import unittest

from impl import double


class T(unittest.TestCase):
    def test_double(self):
        self.assertEqual(double(21), 42)
"""

# 失敗訊息裡放一個好認的字串：用來斷言它**沒有**出現在 job log 裡。
SECRET_MARKER = "KOALA_ASSERT_MARKER"

FAILING_TEST = f"""import unittest

from impl import double


class T(unittest.TestCase):
    def test_double(self):
        self.assertEqual(double(21), 99, "{SECRET_MARKER}")
"""

ENV_PROBE_TEST = """import json
import os
import unittest
from pathlib import Path

(Path(os.environ["HARNESS_REPO_ROOT"]) / "env.json").write_text(
    json.dumps(dict(os.environ)), encoding="utf-8"
)


class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""

# 檔名不符合 `test_*.py`，unittest discover 撈不到 → 一個測試都沒跑到。
ZERO_TEST_SOURCE = "assert True\n"


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def make_repo(tmp: Path, hidden_source: str = PASSING_TEST, hidden_name: str = "test_hidden.py") -> Path:
    repo = tmp / "repo"
    (repo / "tests" / "hidden").mkdir(parents=True)
    (repo / "impl.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    (repo / "tests" / "hidden" / hidden_name).write_text(hidden_source, encoding="utf-8")
    return repo


def run_script(script: Path, cwd: Path, *args, extra_env=None, project_dir: Path = None):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir or cwd)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    env.pop(vault.CI_SEALED_DIR, None)
    for name in ("HARNESS_VERIFY_TOKENS", "HARNESS_VERIFY_TOKEN"):
        env.pop(name, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(cwd),
        env=env,
    )


def seal(repo: Path, task_id: str = "T1") -> str:
    result = run_script(SEAL, repo, "--task-id", task_id)
    assert result.returncode == 0, f"封存失敗：{result.stdout}\n{result.stderr}"
    for line in result.stdout.splitlines():
        if line.startswith("執行權杖"):
            return line.split("：", 1)[1].strip()
    raise AssertionError(f"輸出裡找不到權杖：{result.stdout}")


def export(repo: Path, token: str, task_id: str = "T1"):
    return run_script(EXPORT, repo, "--task-id", task_id, "--token", token)


def verify(repo: Path, tokens: str, *args, work_dir: Path = None):
    extra = ["--work-dir", str(work_dir)] if work_dir else []
    return run_script(
        CI_VERIFY, repo, "--repo", str(repo), *extra, *args,
        extra_env={"HARNESS_VERIFY_TOKENS": tokens},
    )


def entry_path(repo: Path, task_id: str = "T1") -> Path:
    return repo / vault.CI_SEALED_DIR / task_id / vault.CI_ENTRY_NAME


# --------------------------------------------------- 權杖 secret 的解析（純函式）


def _load_ci_verify():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ci_verify", CI_VERIFY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@case("權杖 secret 解析：空白分隔、等號、註解與空行")
def _(tmp: Path):
    module = _load_ci_verify()
    tokens, problems = module.parse_tokens(
        "# 這是註解\n\nT-001 abc123\nT-002=def456\n   T-003   ghi789   \n"
    )
    assert tokens == {"T-001": "abc123", "T-002": "def456", "T-003": "ghi789"}, tokens
    assert not problems, problems


@case("權杖 secret 格式錯誤會被講出來，不是靜靜跳過")
def _(tmp: Path):
    module = _load_ci_verify()
    tokens, problems = module.parse_tokens("T-001 abc def\nT-002\n")
    assert tokens == {}, tokens
    assert len(problems) == 2, problems


@case("傳給被測程式碼的環境變數不含權杖與 CI 的各種 token（純函式層）")
def _(tmp: Path):
    module = _load_ci_verify()
    clean = module.scrub_env(
        {
            "PATH": "/usr/bin",
            "HARNESS_VERIFY_TOKENS": "T1 secret",
            "GITHUB_TOKEN": "ghp_x",
            "ACTIONS_RUNTIME_TOKEN": "rt",
            "HARNESS_VERIFY_ANYTHING_NEW": "y",
            "SOMETHING_ELSE": "secret-token-value",
        },
        ["secret-token-value"],
    )
    assert clean == {"PATH": "/usr/bin"}, clean


# --------------------------------------------------------------- 密文包的承諾


@case("匯出後密文包裡沒有任何明文，內容是密文")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    result = export(repo, token)
    assert result.returncode == 0, result.stdout + result.stderr

    sealed = repo / vault.CI_SEALED_DIR / "T1"
    blob = (sealed / "test_hidden.py.enc").read_bytes()
    assert b"double" not in blob and b"unittest" not in blob, "密文包裡讀得到明文"
    assert token not in (sealed / vault.CI_ENTRY_NAME).read_text(encoding="utf-8"), (
        "entry.json 裡竟然有權杖本身"
    )


@case("匯出需要正確權杖（驗簽之後才匯出）")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)
    result = export(repo, "0" * 32)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (repo / vault.CI_SEALED_DIR / "T1").exists(), "權杖錯了還是寫出了密文包"


@case("帶路徑分隔符號的 task_id 不可以變成密文包的目錄名")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    result = run_script(EXPORT, repo, "--task-id", "../escape", "--token", token)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (repo.parent / "escape").exists(), "密文包寫到 repo 外面去了"


@case("manifest 被改過時拒絕匯出（不要把壞掉的東西帶上 CI）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    manifest_path = repo / ".harness" / "hidden-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tasks"]["T1"]["test_command"] = "echo OK"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = export(repo, token)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "簽章不符" in result.stderr, result.stderr


# ------------------------------------------------------------- CI 驗收的判定


@case("CI 驗收：隱藏測試全綠時 exit 0")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    assert export(repo, token).returncode == 0
    result = verify(repo, f"T1 {token}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "判定：通過" in result.stdout, result.stdout


@case("CI 驗收：隱藏測試有失敗時 exit 1，而且 job log 不含測試內容")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    token = seal(repo)
    assert export(repo, token).returncode == 0
    result = verify(repo, f"T1 {token}")
    assert result.returncode == 1, result.stdout + result.stderr

    combined = result.stdout + result.stderr
    # 這是 CI 驗收跟本機驗收最大的差別：log 誰都看得到。
    assert SECRET_MARKER not in combined, f"失敗明細洩進 job log 了：{combined}"
    assert "test_double" not in combined, f"測試名稱洩進 job log 了：{combined}"
    assert "未全部通過" in combined, combined


@case("CI 驗收：零測試假通過一律判失敗")
def _(tmp: Path):
    repo = make_repo(tmp, ZERO_TEST_SOURCE, hidden_name="checks_no_match.py")
    token = seal(repo)
    assert export(repo, token).returncode == 0
    result = verify(repo, f"T1 {token}")
    assert result.returncode == 1, f"零測試竟然被判成通過：{result.stdout}"
    assert "判定：通過" not in result.stdout, result.stdout


@case("CI 驗收：secret 裡沒有這個 task 的權杖時是「無法驗收」，不是跳過")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    assert export(repo, token).returncode == 0
    result = verify(repo, "T-OTHER whatever")
    assert result.returncode == 2, f"沒有權杖竟然不是 exit 2：{result.stdout}"
    assert "無法進行" in result.stderr, result.stderr


@case("CI 驗收：權杖錯誤時 exit 2，且不洩漏任何內容")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    token = seal(repo)
    assert export(repo, token).returncode == 0
    result = verify(repo, "T1 " + "0" * 32)
    assert result.returncode == 2, result.stdout + result.stderr
    assert SECRET_MARKER not in (result.stdout + result.stderr)


@case("CI 驗收：密文包的 entry.json 被改過時 exit 2（簽章擋下來）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    assert export(repo, token).returncode == 0

    path = entry_path(repo)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entry"]["test_command"] = "{python} -c \"print('OK')\""
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = verify(repo, f"T1 {token}")
    assert result.returncode == 2, f"改掉測試指令竟然驗得過：{result.stdout}"
    assert "簽章不符" in result.stderr, result.stderr


@case("CI 驗收：密文被竄改時 exit 2（明文 sha256 對不上）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    assert export(repo, token).returncode == 0

    blob = repo / vault.CI_SEALED_DIR / "T1" / "test_hidden.py.enc"
    blob.write_bytes(b"\x00" * blob.stat().st_size)

    result = verify(repo, f"T1 {token}")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "竄改" in result.stderr, result.stderr


@case("CI 驗收：沒有任何密文包時說「沒東西要驗」並 exit 0")
def _(tmp: Path):
    repo = make_repo(tmp)
    result = verify(repo, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "沒有東西要驗" in result.stdout, result.stdout


# ------------------------------------------------- 被測程式碼拿得到什麼、拿不到什麼


@case("被測的程式碼拿不到權杖，也拿不到 CI 的 token（實跑）")
def _(tmp: Path):
    repo = make_repo(tmp, ENV_PROBE_TEST)
    token = seal(repo)
    assert export(repo, token).returncode == 0

    result = verify(
        repo, f"T1 {token}",
        work_dir=repo,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    env = json.loads((repo / "env.json").read_text(encoding="utf-8"))
    assert token not in env.values(), "權杖流進了被測程式碼的環境變數"
    for name in ("HARNESS_VERIFY_TOKENS", "HARNESS_VERIFY_TOKEN", "GITHUB_TOKEN"):
        assert name not in env, f"{name} 流進了被測程式碼的環境變數"
    # 比對要用 resolve() 後的路徑：ci-verify.py 內部對 --work-dir 做了 .resolve()，
    # 而 Windows 的 %TEMP% 常是 8.3 短路徑（RUNNER~1），resolve 之後才是長格式。
    # 不 resolve 兩邊就會在 Windows 上假失敗——本機 Linux 剛好一致所以看不出來。
    assert env.get("HARNESS_REPO_ROOT") == str(repo.resolve()), env.get("HARNESS_REPO_ROOT")


@case("受測程式碼是 --work-dir 那一份，不是密文包所在的那一份")
def _(tmp: Path):
    repo = make_repo(tmp, ENV_PROBE_TEST)
    token = seal(repo)
    assert export(repo, token).returncode == 0

    work = tmp / "work"
    work.mkdir()
    (work / "impl.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")

    result = verify(repo, f"T1 {token}", work_dir=work)
    assert result.returncode == 0, result.stdout + result.stderr

    env = json.loads((work / "env.json").read_text(encoding="utf-8"))
    # 同上：ci-verify.py resolve 過 --work-dir，Windows 的短路徑不 resolve 會對不上。
    assert env["HARNESS_REPO_ROOT"] == str(work.resolve()), env["HARNESS_REPO_ROOT"]
    assert not (repo / "env.json").exists(), "測試跑在可信的那份 checkout 底下"


@case("解密後的明文不落在受測程式碼的目錄底下")
def _(tmp: Path):
    probe = """import os
import unittest
from pathlib import Path

(Path(os.environ["HARNESS_REPO_ROOT"]) / "where.txt").write_text(
    str(Path(__file__).resolve().parent), encoding="utf-8"
)


class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""
    repo = make_repo(tmp, probe)
    token = seal(repo)
    assert export(repo, token).returncode == 0

    work = tmp / "work"
    work.mkdir()
    result = verify(repo, f"T1 {token}", work_dir=work)
    assert result.returncode == 0, result.stdout + result.stderr

    where = Path((work / "where.txt").read_text(encoding="utf-8").strip()).resolve()
    for forbidden in (work.resolve(), repo.resolve()):
        assert forbidden not in where.parents and where != forbidden, (
            f"明文解在 {forbidden} 底下：{where}"
        )
    assert not where.exists(), "解密目錄沒有被清掉"


# ------------------------------------------------------------- 加密明細的來回


@case("驗收明細加密後只有握有權杖的一方解得開")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    token = seal(repo)
    assert export(repo, token).returncode == 0

    report = tmp / vault.CI_REPORT_NAME
    result = verify(repo, f"T1 {token}", "--report-out", str(report))
    assert result.returncode == 1, result.stdout + result.stderr
    assert report.is_file(), "沒有寫出明細"
    assert SECRET_MARKER.encode() not in report.read_bytes(), "明細沒有加密"

    good = run_script(READ_REPORT, repo, "--file", str(report), "--token", token)
    assert good.returncode == 0, good.stdout + good.stderr
    assert SECRET_MARKER in good.stdout, good.stdout

    bad = run_script(READ_REPORT, repo, "--file", str(report), "--token", "0" * 32)
    assert bad.returncode == 2, bad.stdout + bad.stderr
    assert SECRET_MARKER not in (bad.stdout + bad.stderr), "權杖錯了還是洩漏了內容"


@case("明細被改過時讀取會被拒絕（不是給一份假的報告）")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    token = seal(repo)
    assert export(repo, token).returncode == 0

    report = tmp / vault.CI_REPORT_NAME
    verify(repo, f"T1 {token}", "--report-out", str(report))
    data = bytearray(report.read_bytes())
    data[-1] ^= 0xFF
    report.write_bytes(bytes(data))

    result = run_script(READ_REPORT, repo, "--file", str(report), "--token", token)
    assert result.returncode == 2, result.stdout
    assert "簽章不符" in result.stderr, result.stderr


# ------------------------------------------------------- workflow 的信任根不能漂


@case("驗收 workflow 從預設分支跑，且只對同一個 repo 的分支生效")
def _(tmp: Path):
    assert WORKFLOW.is_file(), f"找不到 {WORKFLOW}"
    text = WORKFLOW.read_text(encoding="utf-8")

    # pull_request_target：workflow 檔案取自**預設分支**，implementer 改自己分支
    # 上的那一份不影響驗收。這是整個設計的信任根，換成 pull_request 就全垮了。
    assert "pull_request_target" in text, "workflow 沒有用 pull_request_target"
    assert "\n  pull_request:" not in text, (
        "workflow 同時掛了 pull_request——那一份會用 PR 分支上的 workflow 檔案"
    )
    # 同源限定：pull_request_target 對 fork 的 PR 一樣給得到 secret，
    # 沒有這一行等於任何人開一個 fork PR 就能拿到權杖。
    assert "head.repo.full_name == github.repository" in text, "workflow 沒有限定同源分支"
    # PR 的程式碼不可以帶著 checkout 憑證——它會在這個 job 裡被執行。
    assert "persist-credentials: false" in text, "PR 的 checkout 沒有關掉憑證"
    # 驗收腳本一定要來自可信的那份 checkout。
    assert "base/scripts/ci-verify.py" in text, "workflow 沒有用可信 checkout 裡的驗收腳本"


@case("workflow 不會把加密明細以外的東西上傳成 artifact")
def _(tmp: Path):
    text = WORKFLOW.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("path:") and "upload-artifact" not in stripped:
            value = stripped.split(":", 1)[1].strip()
            if value in ("base", "work"):
                continue  # checkout 的目標目錄，不是 artifact
            assert vault.CI_REPORT_NAME in value, (
                f"artifact 路徑「{value}」不是加密明細——artifact 誰都下載得到"
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
