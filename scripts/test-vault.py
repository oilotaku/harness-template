#!/usr/bin/env python3
"""test-vault.py — seal-hidden-tests.py / run-hidden-tests.py 的離線回歸測試

對應 docs/history/improvement-suggestions.md 的 P1-1。與 test-guards.py、test-locks.py
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


def seal(repo: Path, task_id: str = "T1", test_command: str = None, **kwargs) -> str:
    """封存並回傳權杖。"""
    extra = ["--test-command", test_command] if test_command else []
    result = run_script(SEAL, repo, "--task-id", task_id, *extra, **kwargs)
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


# ------------------------------------------------ 第二輪：manifest 簽章（P0-6）


def rewrite_manifest(repo: Path, mutate) -> None:
    """用「不經過任何受保護路徑字串」的方式改 manifest——模擬 implementer 寫一支腳本去改。"""
    path = repo / ".harness" / "hidden-manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@case("P0-6：manifest 的 test_command 被換成假腳本時，runner 驗簽失敗回 2，不會假通過")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    (repo / "fake.py").write_text("print('Ran 2 tests in 0.001s\\n\\nOK')\n", encoding="utf-8")

    def mutate(data):
        data["tasks"]["T1"]["test_command"] = "{python} " + str(repo / "fake.py") + " {dir}"

    rewrite_manifest(repo, mutate)
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2, f"假腳本竟然被接受：exit={result.returncode}\n{result.stdout}"
    assert "簽章不符" in result.stderr, result.stderr
    assert not (repo / ".harness" / "attempts.json").exists(), "驗簽失敗還記了一筆嘗試"


@case("P0-6：manifest 的 task_dir 被改時驗簽失敗回 2")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    def mutate(data):
        data["tasks"]["T1"]["task_dir"] = str(tmp / "elsewhere")

    rewrite_manifest(repo, mutate)
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2 and "簽章不符" in result.stderr, result.stderr


@case("P0-6：把 signature 欄位拿掉不會回到「沒簽章」的舊行為，一樣回 2")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    rewrite_manifest(repo, lambda data: data["tasks"]["T1"].pop("signature"))
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2 and "簽章不符" in result.stderr, result.stderr


@case("P0-6：JSON 欄位重排不影響簽章（簽章對順序不敏感）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    def mutate(data):
        entry = data["tasks"]["T1"]
        data["tasks"]["T1"] = dict(reversed(list(entry.items())))

    rewrite_manifest(repo, mutate)
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"重排 JSON 就驗不過：{result.stderr}"


@case("P0-6：版本 1 的 manifest 一律要求重新封存（不相容是刻意的）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    rewrite_manifest(repo, lambda data: data.__setitem__("version", 1))
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2 and "版本" in result.stderr, result.stderr


# ------------------------------------------------ 第二輪：鎖定清單納入簽章（P0-7）


def make_repo_with_public(tmp: Path, hidden_source: str = PASSING_TEST) -> Path:
    repo = make_repo(tmp, hidden_source)
    (repo / "tests" / "public").mkdir(parents=True)
    (repo / "tests" / "public" / "test_public.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    return repo


@case("P0-7：封存會先鎖定公開測試，並把清單 sha256 寫進簽過章的 manifest")
def _(tmp: Path):
    repo = make_repo_with_public(tmp)
    seal(repo)
    locked = repo / ".harness" / "locked-tests.list"
    assert locked.exists(), "封存後應該已經有鎖定清單"
    assert "tests/public/test_public.py" in locked.read_text(encoding="utf-8")
    info = manifest_of(repo)["tasks"]["T1"]
    assert info["locked_tests_sha256"] == hashlib.sha256(locked.read_bytes()).hexdigest()


@case("P0-7：鎖定清單在封存之後被整份刪掉，runner 回 2 並明講是「被刪除」")
def _(tmp: Path):
    repo = make_repo_with_public(tmp)
    token = seal(repo)
    (repo / ".harness" / "locked-tests.list").unlink()
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2, result.stdout
    assert "刪除" in result.stderr, result.stderr


@case("P0-7：鎖定清單在封存之後被改寫，runner 回 2 並明講是「被改寫」")
def _(tmp: Path):
    repo = make_repo_with_public(tmp)
    token = seal(repo)
    locked = repo / ".harness" / "locked-tests.list"
    locked.write_text(locked.read_text(encoding="utf-8").replace("a", "b", 1) + "\n# x\n", encoding="utf-8")
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2 and "改寫" in result.stderr, result.stderr


# ------------------------------------------------ 第二輪：每檔獨立金鑰（P1-5）


@case("P1-5：兩個檔案的密文 XOR 不等於明文 XOR（不再是 two-time pad）")
def _(tmp: Path):
    p1 = b"import unittest\nfrom impl import double\n\nclass Hidden(unittest.TestCase):\n    def test_a(self): pass\n"
    p2 = b"import unittest\nfrom impl import double\n\nclass Hidden2(unittest.TestCase):\n    def test_b(self): pass\n"
    repo = make_repo(tmp)
    (repo / "tests" / "hidden" / "test_h1.py").write_bytes(p1)
    (repo / "tests" / "hidden" / "test_h2.py").write_bytes(p2)
    seal(repo)
    task_dir = Path(manifest_of(repo)["tasks"]["T1"]["task_dir"])
    c1 = (task_dir / "test_h1.py.enc").read_bytes()
    c2 = (task_dir / "test_h2.py.enc").read_bytes()
    n = min(len(c1), len(c2))
    cipher_xor = bytes(a ^ b for a, b in zip(c1[:n], c2[:n]))
    plain_xor = bytes(a ^ b for a, b in zip(p1[:n], p2[:n]))
    assert cipher_xor != plain_xor, "C1^C2 == P1^P2：keystream 仍然共用，crib 就能還原隱藏測試"


@case("P1-5：同樣的內容放在不同路徑，密文不同")
def _(tmp: Path):
    repo = make_repo(tmp)
    (repo / "tests" / "hidden" / "test_same_a.py").write_text(PASSING_TEST, encoding="utf-8")
    (repo / "tests" / "hidden" / "test_same_b.py").write_text(PASSING_TEST, encoding="utf-8")
    seal(repo)
    task_dir = Path(manifest_of(repo)["tasks"]["T1"]["task_dir"])
    assert (task_dir / "test_same_a.py.enc").read_bytes() != (task_dir / "test_same_b.py.enc").read_bytes()


# ------------------------------------------------ 第二輪：基線執行（P1-6）


@case("P1-6：--baseline 在測試是紅的時候回 0、寫進 manifest、不記 attempts")
def _(tmp: Path):
    repo = make_repo(tmp, hidden_source=FAILING_TEST)
    token = seal(repo)
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token, "--baseline")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "基線如預期是紅的" in result.stdout, result.stdout
    baseline = manifest_of(repo)["tasks"]["T1"]["baseline"]
    assert baseline and baseline["red"] is True, baseline
    assert baseline["tests_seen"] == 1, baseline
    assert not (repo / ".harness" / "attempts.json").exists(), "基線執行不該計入停損次數"


@case("P1-6：--baseline 在測試沒有實作就綠的時候回 1（沒有鑑別力）")
def _(tmp: Path):
    repo = make_repo(tmp, hidden_source=PASSING_TEST)
    token = seal(repo)
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token, "--baseline")
    assert result.returncode == 1, result.stdout
    assert "沒有鑑別力" in result.stdout, result.stdout
    assert manifest_of(repo)["tasks"]["T1"]["baseline"] is None


@case("P1-6：基線寫進 manifest 之後簽章仍然有效，正式執行不再警告")
def _(tmp: Path):
    repo = make_repo(tmp, hidden_source=FAILING_TEST)
    token = seal(repo)
    assert run_script(RUN, repo, "--task-id", "T1", "--token", token, "--baseline").returncode == 0
    (repo / "impl.py").write_text("def double(n):\n    return 999\n", encoding="utf-8")
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "從未被證明過會紅" not in result.stdout, result.stdout


@case("P1-6：沒做過基線的 task，正式執行會印出「從未被證明過會紅」的警告")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0
    assert "從未被證明過會紅" in result.stdout, result.stdout


# ------------------------------------------------ 第二輪：字面大括號（P2-9）


@case("P2-9：測試指令含字面大括號時不會 KeyError，runner 照常執行")
def _(tmp: Path):
    repo = make_repo(tmp)
    command = "{python} -c \"print('marker-{Foo}-ok')\""
    token = seal(repo, test_command=command)
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert "KeyError" not in result.stderr, result.stderr
    assert "marker-{Foo}-ok" in result.stdout, result.stdout


# ------------------------------------------------ 第二輪：git 歷史（P1-7）


def git(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


@case("P1-7：隱藏測試已經在 git 歷史裡時，封存被拒絕")
def _(tmp: Path):
    import shutil as _shutil
    if _shutil.which("git") is None:
        return  # 沒有 git 就沒有歷史可以外洩，這個案例無從驗證
    repo = make_repo(tmp)
    assert git(repo, "init", "-q").returncode == 0
    assert git(repo, "add", "-A").returncode == 0
    assert git(repo, "commit", "-qm", "leak").returncode == 0
    result = run_script(SEAL, repo, "--task-id", "T1")
    assert result.returncode == 1, f"進了歷史的測試竟然封存成功：{result.stdout}"
    assert "git 歷史" in result.stderr, result.stderr
    assert (repo / "tests" / "hidden" / "test_hidden.py").exists(), "拒絕封存卻把明文刪掉了"


@case("P1-7：暫存區被 .gitignore 排除、從未 commit 時，封存照常")
def _(tmp: Path):
    import shutil as _shutil
    if _shutil.which("git") is None:
        return
    repo = make_repo(tmp)
    (repo / ".gitignore").write_text("tests/hidden/*\n", encoding="utf-8")
    assert git(repo, "init", "-q").returncode == 0
    assert git(repo, "add", "-A").returncode == 0
    assert git(repo, "commit", "-qm", "init").returncode == 0
    seal(repo)


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
