#!/usr/bin/env python3
"""test-config.py — harness.config.json（專案測試路徑慣例）的回歸測試

對應 docs/improvement-suggestions.md 的 P1-4。

這批測試守的是兩件事：

1. **設定真的有效**：自訂路徑會被保護、預設路徑在自訂後不再被特別對待。
2. **設定錯誤不會靜默**：打錯欄位名、路徑寫成字串、指到 repo 之外——
   全部要明確報錯。因為 P1-4 要修的問題本身就是「保護悄悄失效而沒有訊號」，
   如果設定檔打錯字只是靜靜退回預設值，等於換一種方式重蹈覆轍。

另外也涵蓋一個順帶抓到的真 bug：測試指令「一個測試都沒跑到」卻回傳 0 時，
runner 必須判定不通過（見 run-hidden-tests.py 的 ZERO_TEST_SIGNATURES）。

用法：python3 scripts/test-config.py
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
GUARD = SCRIPTS_DIR / "guard-hidden-tests.py"
LOCK = SCRIPTS_DIR / "lock-tests.py"
SEAL = SCRIPTS_DIR / "seal-hidden-tests.py"
RUN = SCRIPTS_DIR / "run-hidden-tests.py"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def write_config(tmp: Path, config) -> None:
    text = config if isinstance(config, str) else json.dumps(config, ensure_ascii=False)
    (tmp / harness_config.CONFIG_FILENAME).write_text(text, encoding="utf-8")


def touch(tmp: Path, rel: str, content: str = "assert True\n") -> Path:
    target = tmp / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def env_for(tmp: Path) -> dict:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    return env


def run_script(script: Path, tmp: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(tmp),
        env=env_for(tmp),
    )


def run_guard(tmp: Path, payload) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(tmp),
        env=env_for(tmp),
    )


def expect_config_error(tmp: Path, config, hint: str):
    write_config(tmp, config)
    try:
        harness_config.load(tmp)
    except harness_config.ConfigError as exc:
        assert hint in str(exc), f"錯誤訊息沒提到「{hint}」：{exc}"
        return
    raise AssertionError(f"設定 {config!r} 應該要被拒絕，卻通過了")


# ------------------------------------------------------------------ 載入與驗證


@case("沒有設定檔時使用預設值（Python 專案不必特別設定）")
def _(tmp: Path):
    config = harness_config.load(tmp)
    assert config["hidden_test_paths"] == ["tests/hidden"], config
    assert config["public_test_paths"] == ["tests/public"], config
    assert "{dir}" in config["hidden_test_command"], config


@case("合法設定會覆寫預設值，並記下來源")
def _(tmp: Path):
    write_config(tmp, {"hidden_test_paths": ["spec/secret"], "public_test_paths": ["spec/open"]})
    config = harness_config.load(tmp)
    assert config["hidden_test_paths"] == ["spec/secret"], config
    assert config["public_test_paths"] == ["spec/open"], config
    assert harness_config.CONFIG_FILENAME in config["_source"], config


@case("路徑前後的斜線與反斜線會被正規化")
def _(tmp: Path):
    write_config(tmp, {"hidden_test_paths": ["/spec/secret/", "a\\b"]})
    config = harness_config.load(tmp)
    assert config["hidden_test_paths"] == ["spec/secret", "a/b"], config


@case("路徑欄位寫成字串（而不是陣列）要明確報錯")
def _(tmp: Path):
    expect_config_error(tmp, {"hidden_test_paths": "tests/hidden"}, "必須是非空字串陣列")


@case("空陣列要明確報錯")
def _(tmp: Path):
    expect_config_error(tmp, {"public_test_paths": []}, "必須是非空字串陣列")


@case("陣列裡有空字串要明確報錯")
def _(tmp: Path):
    expect_config_error(tmp, {"hidden_test_paths": ["tests/hidden", ""]}, "空的或非字串")


@case("把整個 repo 設成受保護路徑要被拒絕（會讓任何檔案都動不了）")
def _(tmp: Path):
    expect_config_error(tmp, {"hidden_test_paths": ["."]}, "整個 repo 根目錄")
    expect_config_error(tmp, {"hidden_test_paths": ["/"]}, "整個 repo 根目錄")


@case("指到 repo 之外的路徑要被拒絕")
def _(tmp: Path):
    expect_config_error(tmp, {"hidden_test_paths": ["../elsewhere"]}, "不在 repo 內")


@case("欄位名打錯不能靜默忽略（那等於用 typo 關掉防護）")
def _(tmp: Path):
    expect_config_error(tmp, {"hidden_test_path": ["tests/hidden"]}, "無法辨識的欄位")


@case("以 $ 開頭的欄位當註解，允許存在")
def _(tmp: Path):
    write_config(tmp, {"$comment": "這是註解", "hidden_test_paths": ["x"]})
    assert harness_config.load(tmp)["hidden_test_paths"] == ["x"]


@case("hidden_test_command 少了 {dir} 要明確報錯")
def _(tmp: Path):
    expect_config_error(tmp, {"hidden_test_command": "pytest -q"}, "{dir}")


@case("設定檔不是合法 JSON 要明確報錯")
def _(tmp: Path):
    expect_config_error(tmp, "{ 這不是 JSON", "不是合法 JSON")


@case("最外層不是物件要明確報錯")
def _(tmp: Path):
    expect_config_error(tmp, "[1, 2, 3]", "最外層必須是物件")


# ------------------------------------------------------------------ 路徑比對


@case("matches()：前綴、萬用字元、不相符")
def _(tmp: Path):
    assert harness_config.matches("tests/hidden/test_a.py", ["tests/hidden"])
    assert harness_config.matches("tests/hidden", ["tests/hidden"])
    assert not harness_config.matches("tests/public/test_a.py", ["tests/hidden"])
    assert not harness_config.matches("tests/hidden_notes.md", ["tests/hidden"]), "不能只比字串前綴"
    assert harness_config.matches("packages/api/tests/hidden/t.py", ["packages/*/tests/hidden"])
    assert not harness_config.matches("src/app.py", ["packages/*/tests/hidden"])


# --------------------------------------------------------- guard 吃設定（端對端）


@case("guard：自訂的隱藏測試路徑會被保護")
def _(tmp: Path):
    write_config(tmp, {"hidden_test_paths": ["spec/secret"]})
    result = run_guard(tmp, {"tool_name": "Read", "tool_input": {"file_path": "spec/secret/t.py"}})
    assert result.returncode == 2, f"exit={result.returncode} {result.stderr}"


@case("guard：設定自訂路徑後，預設的 tests/hidden 不再被特別對待")
def _(tmp: Path):
    write_config(tmp, {"hidden_test_paths": ["spec/secret"]})
    result = run_guard(tmp, {"tool_name": "Read", "tool_input": {"file_path": "tests/hidden/t.py"}})
    assert result.returncode == 0, f"exit={result.returncode} {result.stderr}"


@case("guard：monorepo 萬用字元路徑會被保護")
def _(tmp: Path):
    write_config(tmp, {"hidden_test_paths": ["packages/*/tests/hidden"]})
    result = run_guard(
        tmp,
        {"tool_name": "Read", "tool_input": {"file_path": "packages/api/tests/hidden/t.py"}},
    )
    assert result.returncode == 2, f"exit={result.returncode} {result.stderr}"


@case("guard：設定檔壞掉時 fail-closed（擋下，而不是退回預設值）")
def _(tmp: Path):
    write_config(tmp, {"hidden_test_path": ["typo"]})
    result = run_guard(tmp, {"tool_name": "Read", "tool_input": {"file_path": "README.md"}})
    assert result.returncode == 2, f"設定壞掉卻放行了：exit={result.returncode} {result.stderr}"
    assert "無法辨識的欄位" in result.stderr, result.stderr


# ---------------------------------------------------- lock / seal 吃設定（端對端）


@case("lock-tests：多個公開測試目錄都會被鎖定")
def _(tmp: Path):
    write_config(tmp, {"public_test_paths": ["spec/open", "extra/open"]})
    touch(tmp, "spec/open/test_a.py")
    touch(tmp, "extra/open/test_b.py")

    result = run_script(LOCK, tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    locked = (tmp / ".harness" / "locked-tests.list").read_text(encoding="utf-8")
    assert "spec/open/test_a.py" in locked, locked
    assert "extra/open/test_b.py" in locked, locked


@case("seal：自訂的隱藏測試路徑會被封存，且封存後沒有明文殘留")
def _(tmp: Path):
    write_config(tmp, {"hidden_test_paths": ["spec/secret"]})
    touch(tmp, "impl.py", "def double(n):\n    return n * 2\n")
    touch(
        tmp,
        "spec/secret/test_h.py",
        "import unittest\nfrom impl import double\n\n"
        "class T(unittest.TestCase):\n    def test_d(self):\n        self.assertEqual(double(2), 4)\n",
    )

    result = run_script(SEAL, tmp, "--task-id", "T1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp / "spec/secret/test_h.py").exists(), "封存後不該留下明文"


@case("seal：不同暫存區出現同名檔案時，明確拒絕而不是默默覆蓋")
def _(tmp: Path):
    write_config(tmp, {"hidden_test_paths": ["a/hidden", "b/hidden"]})
    touch(tmp, "a/hidden/test_same.py")
    touch(tmp, "b/hidden/test_same.py")

    result = run_script(SEAL, tmp, "--task-id", "T1")
    assert result.returncode == 1, f"撞名竟然被接受：{result.stdout}"
    assert "同名" in result.stderr, result.stderr
    assert (tmp / "a/hidden/test_same.py").exists(), "拒絕封存卻把明文刪掉了"


# --------------------------------------------- 「一個測試都沒跑到」不能算通過


@case("run：測試指令回傳 0 但一個測試都沒跑到時，判定不通過")
def _(tmp: Path):
    # 檔名不符合 discover 的 test_*.py 樣式 → unittest 會回報 Ran 0 tests 且 exit 0。
    # 這正是最危險的假通過：verifier 會把它讀成「隱藏測試全過」。
    touch(tmp, "impl.py", "def double(n):\n    return n * 2\n")
    touch(tmp, "tests/hidden/checks_not_matching_pattern.py", "assert True\n")

    sealed = run_script(SEAL, tmp, "--task-id", "T1")
    assert sealed.returncode == 0, sealed.stdout + sealed.stderr
    token = next(
        line.split("：", 1)[1].strip()
        for line in sealed.stdout.splitlines()
        if line.startswith("執行權杖")
    )

    result = run_script(RUN, tmp, "--task-id", "T1", "--token", token)
    # 行為契約：不管哪個 Python 版本，都不可以被判成通過。
    # （Python 3.13 起 unittest 自己就會對零測試回傳非 0；3.12 以前回傳 0，
    #   那正是需要 ZERO_TEST_SIGNATURES 兜底的情況。所以這裡不綁特定訊息。）
    assert result.returncode == 1, f"零測試竟然被判成通過：exit={result.returncode}\n{result.stdout}"
    # 比對完整的狀態行——只比「全部通過」會被「未全部通過」誤中。
    assert "狀態：隱藏測試全部通過" not in result.stdout, f"零測試被描述成通過：{result.stdout}"


@case("零測試偵測器認得各家 runner 的『沒跑到測試』輸出")
def _(tmp: Path):
    # 直接測偵測函式，不受直譯器版本的 exit code 語意影響。
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_hidden_tests", RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    zero_outputs = [
        "Ran 0 tests in 0.000s\n\nOK",  # unittest（3.12 以前會 exit 0）
        "no tests ran in 0.01s",  # pytest
        "collected 0 items",  # pytest
        "no test files",  # go test
        "No test files found, exiting with code 1",  # vitest
        "0 passing (2ms)",  # mocha
    ]
    for output in zero_outputs:
        assert module.looks_like_zero_tests(output), f"沒認出零測試輸出：{output!r}"

    real_outputs = [
        "Ran 12 tests in 0.03s\n\nOK",
        "collected 12 items",
        "12 passing (2ms)",
        "ok  \texample/pkg\t0.002s",
    ]
    for output in real_outputs:
        assert not module.looks_like_zero_tests(output), f"誤判成零測試：{output!r}"


@case("run：正常跑到測試時，仍然回報通過並附上檔案數")
def _(tmp: Path):
    touch(tmp, "impl.py", "def double(n):\n    return n * 2\n")
    touch(
        tmp,
        "tests/hidden/test_h.py",
        "import unittest\nfrom impl import double\n\n"
        "class T(unittest.TestCase):\n    def test_d(self):\n        self.assertEqual(double(2), 4)\n",
    )

    sealed = run_script(SEAL, tmp, "--task-id", "T1")
    token = next(
        line.split("：", 1)[1].strip()
        for line in sealed.stdout.splitlines()
        if line.startswith("執行權杖")
    )

    result = run_script(RUN, tmp, "--task-id", "T1", "--token", token)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "全部通過" in result.stdout, result.stdout


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
