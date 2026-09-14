#!/usr/bin/env python3
"""test-vault.py — seal-hidden-tests.py / run-hidden-tests.py 的離線回歸測試

對應 docs/history/improvement-suggestions.md 的 P1-1。與 test-guards.py、test-locks.py
同樣的作法：在暫存目錄裡造一個最小的 repo，用 subprocess 執行腳本並斷言結果。

這批測試守的是隔離機制的六個核心承諾：
  1. 封存後工作目錄裡沒有明文
  2. 封存檔案的內容是密文（就算被讀到也拿不到題目）
  3. manifest 不含權杖本身
  4. 沒有正確權杖就跑不動隱藏測試
  5. 解密後的明文落在受 guard 保護的封存庫裡，而且不論怎麼結束都會被清掉
     （含被強制中斷之後，由下一次執行補清）
  6. 權杖遺失只能作廢重寫，而且作廢留得下痕跡、洗不白歷史

用法：python3 scripts/test-vault.py
"""
import hashlib
import json
import os
import shutil
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
DISCARD = SCRIPTS_DIR / "discard-sealed-task.py"

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

# 把「自己被解到哪個目錄」寫回 repo，用來斷言明文不是解在 /tmp 而是封存庫底下。
PROBING_TEST = """import os
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
(Path(os.environ["HARNESS_REPO_ROOT"]) / "rundir.txt").write_text(str(_here), encoding="utf-8")


class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
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


def seal(repo: Path, task_id: str = "T1", test_command: str = None, kind: str = None, **kwargs) -> str:
    """封存並回傳權杖。"""
    extra = ["--test-command", test_command] if test_command else []
    if kind:
        extra += ["--kind", kind]
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


@case("執行結束後，解密用的暫存目錄會被刪掉（封存庫與系統暫存目錄都要乾淨）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    before = set(Path(tempfile.gettempdir()).glob("harness-hidden-*"))
    run_script(RUN, repo, "--task-id", "T1", "--token", token)
    after = set(Path(tempfile.gettempdir()).glob("harness-hidden-*"))
    assert after <= before, f"系統暫存目錄沒有被清掉：{after - before}"

    vault_dir = Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"])
    leftovers = list(vault_dir.glob(".run-*"))
    assert not leftovers, f"封存庫裡留下解密目錄：{leftovers}"


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


# --------------------------------- 解密明文的位置與清理（撞到用量上限的那個情境）


@case("解密後的明文解在封存庫底下，不是系統暫存目錄（殘留才落在 guard 保護範圍內）")
def _(tmp: Path):
    repo = make_repo(tmp, hidden_source=PROBING_TEST)
    token = seal(repo)

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    rundir = Path((repo / "rundir.txt").read_text(encoding="utf-8").strip())
    vault_dir = Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"])
    assert rundir.parent == vault_dir, f"解密目錄不在封存庫底下：{rundir}（封存庫 {vault_dir}）"
    assert rundir.name.startswith(".run-"), f"解密目錄命名不符：{rundir.name}"


@case("上一次被強制中斷留下的解密目錄，下一次執行會清掉並警告（擋 SIGKILL 的唯一一層）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    vault_dir = Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"])

    # 模擬「行程被直接砍掉」：目錄留著、裡面有明文、沒有 owner 標記。
    stale = vault_dir / ".run-T1-deadbeef"
    stale.mkdir()
    (stale / "test_hidden.py").write_text("# 這是上一次沒清掉的明文\n", encoding="utf-8")

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert not stale.exists(), "殘留的解密目錄沒有被清掉"
    assert "先前留下的解密目錄" in result.stdout, (
        f"清掉殘留卻沒有警告，verifier 不會知道上一次被砍過：\n{result.stdout}"
    )


@case("殘留清理不會誤刪「另一個行程正在用」的解密目錄")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    vault_dir = Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"])

    # 標記成「這個測試行程正在用」——它確實活著，所以不該被當成殘留。
    live = vault_dir / ".run-T2-inuse"
    live.mkdir()
    (live / ".owner-pid").write_text(str(os.getpid()), encoding="utf-8")

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert live.exists(), "把別的行程正在用的解密目錄刪掉了（平行驗收會無故失敗）"


@case("權杖錯誤等提前結束的路徑，一樣要報告清掉的殘留（那是同一次中斷留下的）")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)
    vault_dir = Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"])
    stale = vault_dir / ".run-T1-killed"
    stale.mkdir()
    (stale / "test_hidden.py").write_text("# 明文\n", encoding="utf-8")

    result = run_script(RUN, repo, "--task-id", "T1", "--token", "0" * 32)
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    assert not stale.exists(), "提前結束的路徑沒有清掉殘留"
    assert "先前留下的解密目錄" in result.stdout, (
        f"清掉了殘留卻沒說，這次中斷的痕跡就這樣消失了：\n{result.stdout}"
    )


@case("--sweep 只清殘留、不需要權杖、不執行任何測試")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)
    vault_dir = Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"])
    stale = vault_dir / ".run-T1-stale"
    stale.mkdir()
    (stale / "test_hidden.py").write_text("# 明文\n", encoding="utf-8")

    result = run_script(RUN, repo, "--sweep")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert not stale.exists(), "--sweep 沒有清掉殘留"
    assert "Ran " not in result.stdout, f"--sweep 竟然跑了測試：\n{result.stdout}"


@case("收到 SIGTERM 時，解密後的明文會被清掉（可攔截的中止路徑）")
def _(tmp: Path):
    if os.name == "nt":
        return  # Windows 的 terminate() 是 TerminateProcess，不給行程執行處理常式
    import signal as _signal
    import time as _time

    repo = make_repo(tmp)
    # 讓測試指令卡住，才有時間在它還活著的時候送訊號。
    token = seal(repo, test_command='{python} -c "import time; time.sleep(60)"')
    vault_dir = Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"])

    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    child = subprocess.Popen(
        [sys.executable, str(RUN), "--task-id", "T1", "--token", token],
        cwd=str(repo), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        deadline = _time.time() + 30
        while _time.time() < deadline and not list(vault_dir.glob(".run-*")):
            _time.sleep(0.05)
        assert list(vault_dir.glob(".run-*")), "解密目錄一直沒有出現，測不到中止清理"

        child.send_signal(_signal.SIGTERM)
        child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()

    leftovers = list(vault_dir.glob(".run-*"))
    assert not leftovers, f"SIGTERM 之後明文仍留在封存庫：{leftovers}"


# ------------------------------------------- 權杖遺失：作廢重來，而不是留救援後門


@case("權杖遺失後作廢：密文移進作廢區（不是刪掉）、manifest 留下墓碑")
def _(tmp: Path):
    # 第三輪 P0-9 之前這裡是直接 rmtree，於是「作廢 + 刪墓碑」之後這個 task
    # 曾經存在過的證據一點都不剩。密文沒有權杖本來就解不開，留著不增加洩題風險。
    repo = make_repo(tmp)
    seal(repo)
    entry_before = manifest_of(repo)["tasks"]["T1"]
    task_dir = Path(entry_before["task_dir"])
    vault_dir = Path(entry_before["vault_dir"])
    assert task_dir.is_dir()

    result = run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert not task_dir.exists(), "原本的封存目錄應該已經被搬走"

    import hidden_vault as _vault

    retired = sorted((vault_dir / _vault.DISCARDED_DIR_NAME).glob("T1-*"))
    assert retired, "密文沒有被保留到作廢區"
    kept = [p.name for p in retired[0].rglob("*") if p.is_file()]
    assert kept == ["test_hidden.py.enc"], f"作廢區的內容不對：{kept}"

    entry = manifest_of(repo)["tasks"]["T1"]
    assert entry["status"] == "discarded", entry
    assert entry["reason"] == "token-lost", entry
    assert entry["discard_count"] == 1, entry
    assert entry["previous"]["file_count"] == 1, entry


def make_repo_with_scripts(tmp: Path, hidden_source: str = PASSING_TEST) -> Path:
    """連 `scripts/` 一起複製的 repo。

    一般案例用 `make_repo`，封存時 seal 會從**真正的** scripts 目錄複製驗收程式碼，
    那對大多數斷言都沒差。但要測「implementer 改掉 repo 裡的副本會怎樣」就不行了——
    那得改到一份可丟棄的副本，不能動到這個 repo 自己的腳本。
    """
    repo = make_repo(tmp, hidden_source)
    shutil.copytree(SCRIPTS_DIR, repo / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    return repo


@case("封存會把驗收用的程式碼一起封存，並把每個檔案的 sha256 簽進 manifest")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    import hidden_vault as _vault

    entry = manifest_of(repo)["tasks"]["T1"]
    names = {item["name"] for item in entry["runner_files"]}
    assert names == set(_vault.RUNNER_FILES), names

    runner = _vault.runner_dir(Path(entry["vault_dir"]))
    assert (runner / "run-hidden-tests.py").is_file(), "封存版 runner 不存在"
    # sha256 必須在簽章範圍內，否則改了封存版程式碼也能連帶改紀錄
    tampered = dict(entry)
    tampered["runner_files"] = [{"name": "run-hidden-tests.py", "sha256": "0" * 64}]
    assert not _vault.verify_entry(tampered, token), "runner_files 被改掉卻驗得過"


@case("用封存版執行：自我比對通過，行為與 repo 版一致")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    token = seal(repo)
    import hidden_vault as _vault

    sealed = _vault.runner_dir(Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"]))
    result = run_script(sealed / "run-hidden-tests.py", repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 1, f"{result.stdout}\n{result.stderr}"
    assert "封存版" in result.stdout, result.stdout


@case("改掉 repo 裡的 runner，封存版的驗收結果不受影響（P0-8 (a) 的重點）")
def _(tmp: Path):
    # 第三輪 P0-8 實測：覆寫 scripts/run-hidden-tests.py 之後，用完全正確的權杖
    # 驗收會得到 exit 0 與「隱藏測試全部通過」，而三項事後稽核都回報正常。
    repo = make_repo_with_scripts(tmp, FAILING_TEST)
    result = run_script(repo / "scripts" / "seal-hidden-tests.py", repo, "--task-id", "T1")
    assert result.returncode == 0, result.stdout
    token = next(
        line.split("：", 1)[1].strip()
        for line in result.stdout.splitlines()
        if line.startswith("執行權杖")
    )
    import hidden_vault as _vault

    sealed = _vault.runner_dir(Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"]))
    (repo / "scripts" / "run-hidden-tests.py").write_text(
        "import sys\nprint('狀態：隱藏測試全部通過。')\nsys.exit(0)\n", encoding="utf-8"
    )
    result = run_script(sealed / "run-hidden-tests.py", repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 1, f"封存版被 repo 的假 runner 影響了：{result.stdout}"
    # 注意不能只查「全部通過」——「未全部通過」含有同一段字。
    assert "狀態：隱藏測試全部通過" not in result.stdout, result.stdout
    assert "未全部通過" in result.stdout, result.stdout


@case("改掉封存版的相依模組：沒有權杖就簽不出新 sha256，直接擋下")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    import hidden_vault as _vault

    runner = _vault.runner_dir(Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"]))
    (runner / "attempts.py").write_text("def record(*a, **k): pass\n", encoding="utf-8")
    result = run_script(runner / "run-hidden-tests.py", repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    assert "被竄改" in result.stderr, result.stderr


@case("封存版程式碼遺失也要擋下（不是只有內容被改才算）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    import hidden_vault as _vault

    runner = _vault.runner_dir(Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"]))
    sealed_runner = runner / "run-hidden-tests.py"
    keep = sealed_runner.read_bytes()
    (runner / "attempts.py").unlink()
    result = run_script(sealed_runner, repo, "--task-id", "T1", "--token", token)
    # exit 2（無法執行），**不是** 1——1 在這支腳本裡的語意是「有隱藏測試失敗」，
    # 而 verifier-reviewer 會照那個語意判讀，還會計入停損次數。
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    assert "不完整" in result.stderr or "遺失" in result.stderr, result.stderr
    sealed_runner.write_bytes(keep)


@case("repo 副本與封存版不一致時擋下，並指出封存版的路徑")
def _(tmp: Path):
    repo = make_repo_with_scripts(tmp)
    result = run_script(repo / "scripts" / "seal-hidden-tests.py", repo, "--task-id", "T1")
    assert result.returncode == 0, result.stdout
    token = next(
        line.split("：", 1)[1].strip()
        for line in result.stdout.splitlines()
        if line.startswith("執行權杖")
    )
    (repo / "scripts" / "attempts.py").write_text("# drifted\n", encoding="utf-8")
    result = run_script(repo / "scripts" / "run-hidden-tests.py", repo,
                        "--task-id", "T1", "--token", token)
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    assert "不一致" in result.stderr, result.stderr
    assert "_runner" in result.stderr, f"沒有指出封存版路徑：{result.stderr}"


@case("repo 副本沒被動過時仍可執行，但要提醒驗收該用封存版")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "repo 裡的驗收程式碼" in result.stdout, result.stdout


@case("作廢會清掉殘留的明文，只把密文留進作廢區")
def _(tmp: Path):
    # 權杖遺失前的最後一次執行，正是最可能被中斷、最可能把明文留在封存庫裡的那一次。
    repo = make_repo(tmp)
    seal(repo)
    entry = manifest_of(repo)["tasks"]["T1"]
    (Path(entry["task_dir"]) / "leftover_plain.py").write_text(
        "self.assertEqual(answer, 42)\n", encoding="utf-8"
    )

    result = run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    import hidden_vault as _vault

    retired = sorted((Path(entry["vault_dir"]) / _vault.DISCARDED_DIR_NAME).glob("T1-*"))[0]
    names = sorted(p.name for p in retired.rglob("*") if p.is_file())
    assert "leftover_plain.py" not in names, f"殘留的明文跟著搬進作廢區了：{names}"
    assert "test_hidden.py.enc" in names, names


@case("封存與作廢都會寫進 repo 之外的流水帳（記指紋，不記權杖）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    import hidden_vault as _vault

    vault_dir = Path(manifest_of(repo)["tasks"]["T1"]["vault_dir"])
    log = (vault_dir / _vault.SEALED_LOG_NAME).read_text(encoding="utf-8")
    assert token not in log, "流水帳裡出現了權杖本身"
    assert _vault.token_fingerprint(token) in log, log

    run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")
    events = [e["event"] for e in _vault.sealed_log_entries(vault_dir, "T1")]
    assert events == ["sealed", "discarded"], events


@case("刪掉墓碑退不回「從來沒封存過」——流水帳讓 runner 判定為竄改")
def _(tmp: Path):
    # 這正是第二輪 P0-7 對鎖定清單修掉的失效方式，從作廢這條路回來的版本。
    repo = make_repo(tmp)
    token = seal(repo)
    run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")

    manifest = manifest_of(repo)
    del manifest["tasks"]["T1"]
    (repo / ".harness" / "hidden-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 2, result.stdout
    assert "封存過" in result.stderr, result.stderr
    assert "不通過" in result.stderr, result.stderr


@case("真的沒封存過的 task_id 不會被誤判成竄改（訊息必須分得出這兩件事）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    result = run_script(RUN, repo, "--task-id", "NEVER-SEALED", "--token", token)
    assert result.returncode == 2, result.stdout
    assert "封存紀錄在封存之後被刪除" not in result.stderr, (
        f"把「沒封存過」誤判成竄改：{result.stderr}"
    )
    assert "manifest 裡沒有 task" in result.stderr, result.stderr


@case("作廢需要 --token-lost 與 --confirm 兩個旗標，缺一就什麼都不動")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)
    task_dir = Path(manifest_of(repo)["tasks"]["T1"]["task_dir"])

    for args in (("--token-lost",), ("--confirm",), ()):
        result = run_script(DISCARD, repo, "--task-id", "T1", *args)
        assert result.returncode == 2, f"旗標 {args} 竟然不是參數錯誤：{result.stdout}"
        assert task_dir.is_dir(), f"旗標 {args} 沒給齊卻已經把密文刪了"
        assert manifest_of(repo)["tasks"]["T1"].get("status") != "discarded", args


@case("權杖其實是對的時候，拒絕作廢（救「只是打錯字」這種情況）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    task_dir = Path(manifest_of(repo)["tasks"]["T1"]["task_dir"])

    result = run_script(DISCARD, repo, "--task-id", "T1", "--token", token,
                        "--token-lost", "--confirm")
    assert result.returncode == 1, f"對的權杖竟然照樣作廢：{result.stdout}"
    assert task_dir.is_dir(), "對的權杖竟然把密文刪了"
    assert manifest_of(repo)["tasks"]["T1"].get("status") != "discarded"


@case("作廢後 runner 拒絕執行，而且不必先給權杖就看得到重寫指引")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)
    run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")

    # 權杖遺失的人拿不出權杖，所以這個訊息不能要求先給 --token。
    result = run_script(RUN, repo, "--task-id", "T1")
    assert result.returncode == 2, f"{result.stdout}\n{result.stderr}"
    assert "已經被作廢" in result.stderr, result.stderr
    assert "seal-hidden-tests.py" in result.stderr, "沒有指出重寫後怎麼回到流程"


@case("作廢後重新封存：嘗試次數與作廢次數都帶進簽過章的新項目，歷史洗不白")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)

    # 先讓 runner 記一筆失敗，製造「有嘗試紀錄」的狀態。
    (repo / "impl.py").write_text("def double(n):\n    return 0\n", encoding="utf-8")
    failed = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert failed.returncode == 1, f"{failed.stdout}\n{failed.stderr}"
    recorded = manifest_of(repo)["tasks"]["T1"]["attempts_recorded"]
    assert recorded == 1, recorded

    run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")

    # 重寫一份隱藏測試再封存（實務上是重新派工 verifier-test-writer）。
    (repo / "impl.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    (repo / "tests" / "hidden").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "hidden" / "test_hidden.py").write_text(PASSING_TEST, encoding="utf-8")
    new_token = seal(repo)

    entry = manifest_of(repo)["tasks"]["T1"]
    assert entry.get("status") != "discarded", entry
    assert entry["discard_count"] == 1, f"作廢次數沒有帶過來：{entry}"
    assert entry["attempts_recorded"] == recorded, f"嘗試筆數沒有帶過來：{entry}"

    # 簽章要涵蓋這兩個欄位：改掉任何一個，runner 都要驗不過。
    import hidden_vault as _vault
    assert _vault.verify_entry(entry, new_token), "重新封存後的項目簽章驗不過"
    tampered = dict(entry)
    tampered["discard_count"] = 0
    assert not _vault.verify_entry(tampered, new_token), "作廢次數被改掉卻驗得過"

    ok = run_script(RUN, repo, "--task-id", "T1", "--token", new_token)
    assert ok.returncode == 0, f"重新封存後跑不起來：{ok.stdout}\n{ok.stderr}"


@case("已經是墓碑的 task 不會被重複作廢（討回一個明確的拒絕，而不是靜默成功）")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)
    run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")

    again = run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")
    assert again.returncode == 1, f"{again.stdout}\n{again.stderr}"
    assert manifest_of(repo)["tasks"]["T1"]["discard_count"] == 1, "重複作廢把次數又加了一次"


@case("--list 會把墓碑與「曾經作廢過」的歷史列出來")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo)
    run_script(DISCARD, repo, "--task-id", "T1", "--token-lost", "--confirm")

    listed = run_script(RUN, repo, "--list")
    assert listed.returncode == 0, listed.stderr
    assert "已作廢" in listed.stdout, listed.stdout

    (repo / "tests" / "hidden").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "hidden" / "test_hidden.py").write_text(PASSING_TEST, encoding="utf-8")
    seal(repo)
    listed_again = run_script(RUN, repo, "--list")
    assert "曾因權杖遺失作廢過 1 次" in listed_again.stdout, listed_again.stdout



# --------------------------- bugfix 任務：公開最小重現 vs 隱藏同類變體（第 4 點）


@case("--kind bugfix 寫進 manifest，而且在簽章範圍內（改掉就驗不過）")
def _(tmp: Path):
    import hidden_vault as _vault

    repo = make_repo(tmp)
    token = seal(repo, kind="bugfix")

    entry = manifest_of(repo)["tasks"]["T1"]
    assert entry["kind"] == "bugfix", entry
    assert _vault.verify_entry(entry, token), "簽章驗不過"

    tampered = dict(entry)
    tampered["kind"] = "normal"
    assert not _vault.verify_entry(tampered, token), (
        "把 bugfix 改成 normal 竟然驗得過——implementer 就能關掉這層判讀"
    )


@case("沒給 --kind 時是 normal；舊版封存沒有這個欄位也當 normal")
def _(tmp: Path):
    import hidden_vault as _vault

    repo = make_repo(tmp)
    seal(repo)
    assert manifest_of(repo)["tasks"]["T1"]["kind"] == "normal"

    assert _vault.task_kind({}) == "normal", "舊版項目沒有 kind 欄位時要當 normal"
    assert _vault.task_kind({"kind": "亂寫"}) == "normal", "不認得的值要退回 normal"


@case("bugfix 任務失敗時，runner 直接講出「修症狀不是修根因」的判讀")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo, kind="bugfix")
    # 讓隱藏的同類變體紅掉（模擬 implementer 只修了被回報的那一個 case）。
    (repo / "impl.py").write_text("def double(n):\n    return 0\n", encoding="utf-8")

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 1, f"{result.stdout}\n{result.stderr}"
    assert "修症狀不是修根因" in result.stdout, (
        f"只說了「有測試失敗」，verifier 不會想到要往這個方向看：\n{result.stdout}"
    )
    assert "公開綠 + 隱藏紅" in result.stdout, result.stdout


@case("一般任務失敗時不會出現 bugfix 的判讀（不該對所有任務都喊「修症狀」）")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo)
    (repo / "impl.py").write_text("def double(n):\n    return 0\n", encoding="utf-8")

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 1, f"{result.stdout}\n{result.stderr}"
    assert "修症狀不是修根因" not in result.stdout, result.stdout


@case("bugfix 任務通過時，明講「同類變體也綠了＝修的是根因」")
def _(tmp: Path):
    repo = make_repo(tmp)
    token = seal(repo, kind="bugfix")

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "修的是根因" in result.stdout, result.stdout


@case("bugfix 的基線若在修正前就全綠，訊息要說「不是這個 bug 的同類」")
def _(tmp: Path):
    # PASSING_TEST 對著一份正確的實作，等同「修正前就綠」的變體。
    repo = make_repo(tmp)
    token = seal(repo, kind="bugfix")

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token, "--baseline")
    assert result.returncode == 1, f"{result.stdout}\n{result.stderr}"
    assert "不是這個 bug 的同類" in result.stdout, (
        f"講成一般的「沒有鑑別力」，看的人不知道該重挑變體：\n{result.stdout}"
    )


@case("bugfix 的基線紅了之後，要誠實說明它只證明「至少一個變體是紅的」")
def _(tmp: Path):
    repo = make_repo(tmp, hidden_source=FAILING_TEST)
    token = seal(repo, kind="bugfix")

    result = run_script(RUN, repo, "--task-id", "T1", "--token", token, "--baseline")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "至少有一個" in result.stdout, (
        f"沒有講清楚基線驗不到「每個變體都紅」：\n{result.stdout}"
    )


@case("--list 標出 bugfix 任務")
def _(tmp: Path):
    repo = make_repo(tmp)
    seal(repo, kind="bugfix")

    listed = run_script(RUN, repo, "--list")
    assert listed.returncode == 0, listed.stderr
    assert "bugfix" in listed.stdout, listed.stdout



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
