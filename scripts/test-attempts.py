#!/usr/bin/env python3
"""test-attempts.py — 驗收嘗試次數與停損判斷的回歸測試（P3-5）

這批測試守的是「停損規則不能靠被監督者自我申報」：

1. **次數由執行測試的一方寫入。** `run-hidden-tests.py` 每跑一次就記一筆，
   所以有整合測試實際封存、實際執行，確認紀錄真的產生。
2. **紀錄檔壞掉時 `should_stop` 是 `None`（未知），不是 `False`。**
   「讀不到紀錄」跟「沒有失敗過」混為一談，等於讓停損規則在檔案壞掉時
   靜靜消失——這個 repo 已經為這類沉默失效付過好幾次代價。
3. **記錄失敗不會讓驗收失敗。** 記錄是輔助資訊，不是驗收結果本身。

用法：python3 scripts/test-attempts.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import attempts  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
SEAL = SCRIPTS_DIR / "seal-hidden-tests.py"
RUN = SCRIPTS_DIR / "run-hidden-tests.py"
SHOW = SCRIPTS_DIR / "show-attempts.py"

CASES = []

PASSING_TEST = """import unittest

from impl import double


class T(unittest.TestCase):
    def test_double(self):
        self.assertEqual(double(21), 42)
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
    repo = tmp / "repo"
    (repo / "tests" / "hidden").mkdir(parents=True)
    (repo / "impl.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    (repo / "tests" / "hidden" / "test_hidden.py").write_text(hidden_source, encoding="utf-8")
    return repo


def run_script(script: Path, repo: Path, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, cwd=str(repo), env=env,
    )


def seal(repo: Path, task_id: str) -> str:
    result = run_script(SEAL, repo, "--task-id", task_id)
    assert result.returncode == 0, f"封存失敗：{result.stdout}\n{result.stderr}"
    for line in result.stdout.splitlines():
        if line.startswith("執行權杖"):
            return line.split("：", 1)[1].strip()
    raise AssertionError(f"輸出裡找不到權杖：{result.stdout}")


def corrupt(repo: Path) -> None:
    path = repo / ".harness" / attempts.RECORD_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 這不是 JSON", encoding="utf-8")


# --------------------------------------------------------------------------
# 純函式
# --------------------------------------------------------------------------

@case("記一次失敗之後讀得回來")
def _(tmp: Path):
    attempts.record("T1", False, note="第一次", root=tmp)
    info = attempts.summary("T1", root=tmp)
    assert info["total"] == 1 and info["failures"] == 1, info
    assert info["consecutive_failures"] == 1, info


@case("連續失敗達門檻時 should_stop 為 True")
def _(tmp: Path):
    for _ in range(3):
        attempts.record("T1", False, root=tmp)
    info = attempts.summary("T1", root=tmp)
    assert info["should_stop"] is True, info


@case("差一次到門檻時 should_stop 仍為 False")
def _(tmp: Path):
    for _ in range(2):
        attempts.record("T1", False, root=tmp)
    assert attempts.summary("T1", root=tmp)["should_stop"] is False


@case("通過一次就把連續失敗歸零")
def _(tmp: Path):
    for _ in range(3):
        attempts.record("T1", False, root=tmp)
    attempts.record("T1", True, root=tmp)
    info = attempts.summary("T1", root=tmp)
    assert info["consecutive_failures"] == 0, info
    assert info["should_stop"] is False, info
    assert info["failures"] == 3, "歷史失敗次數不該被抹掉"


@case("不同 task 的次數互不影響")
def _(tmp: Path):
    for _ in range(3):
        attempts.record("T1", False, root=tmp)
    attempts.record("T2", False, root=tmp)
    assert attempts.summary("T1", root=tmp)["should_stop"] is True
    assert attempts.summary("T2", root=tmp)["should_stop"] is False


@case("沒有紀錄檔時算正常起始狀態（available 為真、次數 0）")
def _(tmp: Path):
    info = attempts.summary("T1", root=tmp)
    assert info["available"] is True and info["total"] == 0, info
    assert info["should_stop"] is False, info


@case("紀錄檔壞掉時 should_stop 是 None（未知），不是 False")
def _(tmp: Path):
    corrupt(tmp)
    info = attempts.summary("T1", root=tmp)
    assert info["should_stop"] is None, "「讀不到紀錄」被當成「沒有失敗過」了"
    assert info["available"] is False, info
    assert info["warnings"], "壞掉卻沒有任何警告"


@case("紀錄檔格式對但 tasks 不是物件時，一樣視為讀不到")
def _(tmp: Path):
    path = tmp / ".harness" / attempts.RECORD_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    assert attempts.summary("T1", root=tmp)["available"] is False


@case("在壞掉的紀錄檔上寫入不會爆炸，而且之後讀得回來")
def _(tmp: Path):
    corrupt(tmp)
    assert attempts.record("T1", False, root=tmp) is not None
    info = attempts.summary("T1", root=tmp)
    assert info["available"] is True and info["total"] == 1, info


@case("threshold 小於 1 時報錯")
def _(tmp: Path):
    try:
        attempts.summary("T1", root=tmp, threshold=0)
    except ValueError:
        return
    raise AssertionError("threshold=0 應該報錯")


@case("停損門檻的預設值跟 implementer 的停損規則一致（3 次）")
def _(tmp: Path):
    # 兩邊如果漂移，文件寫的規則就跟機制算的不是同一件事。
    assert attempts.DEFAULT_THRESHOLD == 3, attempts.DEFAULT_THRESHOLD
    rule = (SCRIPTS_DIR.parent / ".claude" / "agents" / "implementer-generic.md").read_text(
        encoding="utf-8"
    )
    assert "≥3" in rule or "3 次" in rule, "implementer-generic.md 的停損次數看起來變了"


# --------------------------------------------------------------------------
# 與 runner 的整合（次數由執行測試的一方寫入）
# --------------------------------------------------------------------------

@case("隱藏測試失敗時，runner 會記下一次失敗")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    token = seal(repo, "T1")
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 1, result.stdout

    info = attempts.summary("T1", root=repo)
    assert info["total"] == 1 and info["failures"] == 1, info


@case("隱藏測試通過時，runner 記下的是通過")
def _(tmp: Path):
    repo = make_repo(tmp, PASSING_TEST)
    token = seal(repo, "T1")
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, result.stdout

    info = attempts.summary("T1", root=repo)
    assert info["total"] == 1 and info["failures"] == 0, info
    assert info["last_passed"] is True, info


@case("連續失敗到門檻時，runner 會直接印出停損提醒")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    output = ""
    for _ in range(3):
        token = seal(repo, "T1")
        (repo / "tests" / "hidden").mkdir(parents=True, exist_ok=True)
        (repo / "tests" / "hidden" / "test_hidden.py").write_text(FAILING_TEST, encoding="utf-8")
        output = run_script(RUN, repo, "--task-id", "T1", "--token", token).stdout

    assert "連續失敗 3 次" in output, output[-500:]
    assert attempts.summary("T1", root=repo)["should_stop"] is True


@case("記錄機制壞掉不會讓驗收結果改變")
def _(tmp: Path):
    repo = make_repo(tmp, PASSING_TEST)
    corrupt(repo)
    token = seal(repo, "T1")
    result = run_script(RUN, repo, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, f"記錄檔壞掉竟然影響了驗收結果：{result.stdout}"
    assert "狀態：隱藏測試全部通過" in result.stdout, result.stdout[-400:]


# --------------------------------------------------------------------------
# 第二輪 P3-9：紀錄簽章
# --------------------------------------------------------------------------

KEY = b"k" * 32


@case("P3-9：帶金鑰寫入的紀錄，帶同一把金鑰驗證為 ok")
def _(tmp: Path):
    attempts.record("T1", False, root=tmp, mac_key=KEY)
    info = attempts.summary("T1", root=tmp, mac_key=KEY)
    assert info["integrity"] == "ok", info
    assert info["should_stop"] is False


@case("P3-9：沒帶金鑰時 integrity 是 unchecked，跟「沒有紀錄」分開")
def _(tmp: Path):
    attempts.record("T1", False, root=tmp, mac_key=KEY)
    info = attempts.summary("T1", root=tmp)
    assert info["integrity"] == "unchecked" and info["total"] == 1, info


@case("P3-9：把一筆失敗改成通過，簽章對不上，should_stop 變成未知")
def _(tmp: Path):
    for _ in range(3):
        attempts.record("T1", False, root=tmp, mac_key=KEY)
    path = tmp / ".harness" / attempts.RECORD_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tasks"]["T1"][-1]["passed"] = True
    path.write_text(json.dumps(data), encoding="utf-8")

    info = attempts.summary("T1", root=tmp, mac_key=KEY)
    assert info["integrity"] == "tampered", info
    assert info["should_stop"] is None, "紀錄被動過還敢說「不必停損」"


@case("P3-9：整筆刪掉最後幾次失敗，靠 manifest 的計數抓到")
def _(tmp: Path):
    for _ in range(3):
        attempts.record("T1", False, root=tmp, mac_key=KEY)
    path = tmp / ".harness" / attempts.RECORD_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tasks"]["T1"] = data["tasks"]["T1"][:1]  # 砍掉後兩次失敗，剩下那筆簽章仍然有效
    path.write_text(json.dumps(data), encoding="utf-8")

    only_signatures = attempts.summary("T1", root=tmp, mac_key=KEY)
    assert only_signatures["integrity"] == "ok", "逐筆簽章本來就抓不到整筆刪除，這是預期"
    with_count = attempts.summary("T1", root=tmp, mac_key=KEY, expected_total=3)
    assert with_count["integrity"] == "tampered", with_count
    assert with_count["should_stop"] is None


@case("P3-9：重新封存換了金鑰之後，舊紀錄算 partial 而不是 tampered，停損照常判斷")
def _(tmp: Path):
    # 這正是第一版實作踩到的坑：每次重新封存都換權杖，用新金鑰驗舊紀錄全部不符，
    # 於是連續失敗三次的 task 被判成「紀錄被動過、次數不可信」，停損提醒消失。
    old_key = b"a" * 32
    for _ in range(2):
        attempts.record("T1", False, root=tmp, mac_key=old_key)
    attempts.record("T1", False, root=tmp, mac_key=KEY)

    info = attempts.summary("T1", root=tmp, mac_key=KEY, expected_total=3)
    assert info["integrity"] == "partial", info
    assert info["should_stop"] is True, "舊金鑰的紀錄驗不了不該讓停損判斷消失"

    flipped = json.loads((tmp / ".harness" / attempts.RECORD_FILENAME).read_text(encoding="utf-8"))
    flipped["tasks"]["T1"][-1]["passed"] = True  # 改的是現在這把金鑰簽的那筆
    (tmp / ".harness" / attempts.RECORD_FILENAME).write_text(json.dumps(flipped), encoding="utf-8")
    assert attempts.summary("T1", root=tmp, mac_key=KEY)["integrity"] == "tampered"


@case("P3-9：runner 寫的紀錄帶簽章，manifest 記下筆數，show-attempts --token 驗得過")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    token = seal(repo, "T1")
    run_script(RUN, repo, "--task-id", "T1", "--token", token)
    entries = json.loads((repo / ".harness" / attempts.RECORD_FILENAME).read_text(encoding="utf-8"))["tasks"]["T1"]
    assert entries and "signature" in entries[0], entries
    manifest = json.loads((repo / ".harness" / "hidden-manifest.json").read_text(encoding="utf-8"))
    assert manifest["tasks"]["T1"]["attempts_recorded"] == 1, manifest["tasks"]["T1"]

    payload = json.loads(run_script(SHOW, repo, "--task-id", "T1", "--token", token, "--json").stdout)
    assert payload["tasks"][0]["integrity"] == "ok", payload


@case("P3-9：把 runner 寫的紀錄整份砍掉，show-attempts --token 會說被動過")
def _(tmp: Path):
    repo = make_repo(tmp, FAILING_TEST)
    token = seal(repo, "T1")
    run_script(RUN, repo, "--task-id", "T1", "--token", token)
    (repo / ".harness" / attempts.RECORD_FILENAME).unlink()
    payload = json.loads(run_script(SHOW, repo, "--task-id", "T1", "--token", token, "--json").stdout)
    assert payload["tasks"][0]["integrity"] == "tampered", payload
    assert payload["tasks"][0]["should_stop"] is None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

@case("show-attempts --json 的 stdout 只有 JSON")
def _(tmp: Path):
    attempts.record("T1", False, root=tmp)
    result = run_script(SHOW, tmp, "--task-id", "T1", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["tasks"][0]["consecutive_failures"] == 1, payload


@case("show-attempts --all 列出所有有紀錄的 task")
def _(tmp: Path):
    attempts.record("T1", False, root=tmp)
    attempts.record("T2", True, root=tmp)
    payload = json.loads(run_script(SHOW, tmp, "--all", "--json").stdout)
    assert sorted(item["task_id"] for item in payload["tasks"]) == ["T1", "T2"], payload


@case("show-attempts 在紀錄檔壞掉時明講「未知」，不會說沒失敗過")
def _(tmp: Path):
    corrupt(tmp)
    result = run_script(SHOW, tmp, "--task-id", "T1")
    assert result.returncode == 0, result.stderr
    assert "未知" in result.stdout, result.stdout


@case("show-attempts 沒給 --task-id 也沒給 --all 時報參數錯誤")
def _(tmp: Path):
    result = run_script(SHOW, tmp)
    assert result.returncode != 0, result.stdout


@case("show-attempts 對沒有紀錄的 task 回 0 次，不是報錯")
def _(tmp: Path):
    # task_id 刻意用 ASCII：在 LC_ALL=C 這種 locale 下，非 ASCII 的
    # 命令列參數根本編不出去（subprocess 會丟 UnicodeEncodeError）。
    # 這是作業系統層的限制，不是本模板的 bug——見 scripts/attempts.py 的說明。
    payload = json.loads(run_script(SHOW, tmp, "--task-id", "never-run", "--json").stdout)
    assert payload["tasks"][0]["total"] == 0, payload


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
