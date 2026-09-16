#!/usr/bin/env python3
"""test-setup.py — 設定精靈 scripts/setup.py 的回歸測試

setup.py 的目的是降低入門難度，所以它的失效方式也特別要防：
產生一份**看起來對、其實壞掉**的設定，比要人手寫更糟——手寫至少會被
guard-selfcheck / 各腳本的 fail-closed 擋下，而一份「精靈掛保證」的壞設定
更容易被信任。

這批測試守四件事：
  1. 語言偵測對得上標記檔（go.mod / Cargo.toml / package.json / .py）
  2. 產生的設定**一定通過 harness_config.load()**（不把壞設定留給使用者）
  3. **絕不靜默覆蓋**既有設定（要 --force 才動）
  4. 旗標（--level / --lang / --ci / --gui）真的反映到設定裡

用法：python3 scripts/test-setup.py
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
SETUP = SCRIPTS_DIR / "setup.py"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def run_setup(root: Path, *args):
    """一律加 --yes：測試環境沒有 tty，且我們要的是「預設 + 旗標」這條可重現的路徑。"""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    return subprocess.run(
        [sys.executable, str(SETUP), "--yes", *args],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(root),
        env=env,
    )


def config_of(root: Path) -> dict:
    return json.loads((root / harness_config.CONFIG_FILENAME).read_text(encoding="utf-8"))


# ------------------------------------------------------------------- 語言偵測


@case("偵測 Python（有 .py）")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = run_setup(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unittest" in config_of(tmp)["hidden_test_command"], config_of(tmp)


@case("偵測 Node（有 package.json）")
def _(tmp: Path):
    (tmp / "package.json").write_text('{"name":"x","version":"1.0.0"}\n', encoding="utf-8")
    result = run_setup(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    config = config_of(tmp)
    assert "vitest" in config["hidden_test_command"], config
    # Node 的版本源應該指向 package.json
    assert config["version"]["file"] == "package.json", config["version"]


@case("偵測 Go（有 go.mod，暫存區在 internal/testsuite）")
def _(tmp: Path):
    (tmp / "go.mod").write_text("module x\n\ngo 1.21\n", encoding="utf-8")
    result = run_setup(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    config = config_of(tmp)
    assert "go test" in config["hidden_test_command"], config
    assert config["hidden_test_paths"] == ["internal/testsuite/hidden"], config


@case("偵測 Rust（有 Cargo.toml），並警告測試指令是佔位")
def _(tmp: Path):
    (tmp / "Cargo.toml").write_text("[package]\nname = \"x\"\nversion = \"0.1.0\"\n", encoding="utf-8")
    result = run_setup(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "務必" in result.stdout or "確認" in result.stdout, "沒有警告 Rust 指令要自己確認"
    assert config_of(tmp)["version"]["file"] == "Cargo.toml", config_of(tmp)


@case("找不到語言標記檔時，仍產出可用設定並標記未知")
def _(tmp: Path):
    (tmp / "README.md").write_text("# x\n", encoding="utf-8")
    result = run_setup(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    # 就算未知也要產出一份通得過驗證的設定，不是直接放棄。
    harness_config.load(tmp)


@case("--lang 覆寫偵測結果")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")  # 會被偵測成 Python
    result = run_setup(tmp, "--lang", "go")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "go test" in config_of(tmp)["hidden_test_command"], config_of(tmp)


# --------------------------------------------------- 產生的設定一定通得過驗證


@case("每一種語言產生的設定都通過 harness_config.load()")
def _(tmp: Path):
    for index, lang in enumerate(["python", "node", "go", "rust"]):
        sub = tmp / f"proj-{lang}"
        sub.mkdir()
        result = run_setup(sub, "--lang", lang)
        assert result.returncode == 0, f"{lang}: {result.stdout}\n{result.stderr}"
        # 這是這支測試的核心：精靈掛保證的設定，不能是壞的。
        loaded = harness_config.load(sub)
        assert "{dir}" in loaded["hidden_test_command"], f"{lang} 少了 {{dir}}"


# ------------------------------------------------------------- 旗標反映到設定


@case("--level minimal 反映到設定，並印出代價")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = run_setup(tmp, "--level", "minimal")
    assert result.returncode == 0, result.stdout + result.stderr
    assert config_of(tmp)["protection"]["level"] == "minimal", config_of(tmp)
    assert "公開測試被改不會被抓到" in result.stdout, "minimal 沒有講出代價"


@case("--ci 開啟 CI 驗收")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = run_setup(tmp, "--ci")
    assert result.returncode == 0, result.stdout + result.stderr
    assert config_of(tmp)["protection"]["ci_verification"] is True, config_of(tmp)


@case("--no-archive 關掉 releases 歸檔（version.archive = false）")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = run_setup(tmp, "--no-archive")
    assert result.returncode == 0, result.stdout + result.stderr
    assert config_of(tmp)["version"]["archive"] is False, config_of(tmp)["version"]


@case("預設保留 releases 歸檔（省略 archive = 開，見 versioning.md §4.5）")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = run_setup(tmp)  # 不加旗標，--yes 走預設 y
    assert result.returncode == 0, result.stdout + result.stderr
    # 開啟時刻意不寫出 archive 鍵——省略就是預設開，多寫一個會漂的欄位沒有意義。
    assert "archive" not in config_of(tmp)["version"], config_of(tmp)["version"]


@case("--no-gui 關掉設計檢查；--gui 保留（未確認）")
def _(tmp: Path):
    a = tmp / "a"; a.mkdir(); (a / "app.py").write_text("x=1\n", encoding="utf-8")
    run_setup(a, "--no-gui")
    assert config_of(a)["design"] is False, config_of(a)

    b = tmp / "b"; b.mkdir(); (b / "app.py").write_text("x=1\n", encoding="utf-8")
    run_setup(b, "--gui")
    design = config_of(b)["design"]
    assert isinstance(design, dict) and design["confirmed"] is False, design


# ------------------------------------------------------------- 不覆蓋既有設定


@case("已有 harness.config.json 時不覆蓋（沒有 --force）")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    original = '{"protection": {"level": "minimal", "ci_verification": false}}\n'
    (tmp / harness_config.CONFIG_FILENAME).write_text(original, encoding="utf-8")

    result = run_setup(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "不覆蓋" in result.stdout, result.stdout
    # 檔案原封不動
    assert (tmp / harness_config.CONFIG_FILENAME).read_text(encoding="utf-8") == original


@case("--force 才會覆蓋既有設定")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp / harness_config.CONFIG_FILENAME).write_text(
        '{"protection": {"level": "minimal", "ci_verification": false}}\n', encoding="utf-8"
    )
    result = run_setup(tmp, "--force", "--level", "full")
    assert result.returncode == 0, result.stdout + result.stderr
    assert config_of(tmp)["protection"]["level"] == "full", config_of(tmp)


@case("既有設定壞掉時，不覆蓋而是要人先修（或 --force）")
def _(tmp: Path):
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp / harness_config.CONFIG_FILENAME).write_text('{"protection": {"level": "nope"}}\n', encoding="utf-8")
    result = run_setup(tmp)
    assert result.returncode == 1, f"壞設定竟然不是 exit 1：{result.stdout}"
    assert "--force" in result.stderr, result.stderr


# ----------------------------------------------------- 產生的設定能被下游腳本用


@case("精靈產生設定後，seal-hidden-tests.py 能直接用它封存")
def _(tmp: Path):
    # 端到端：setup → 寫隱藏測試 → seal。驗的是「精靈產出的設定路徑，下游真的認得」。
    (tmp / "app.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    result = run_setup(tmp, "--level", "minimal")  # minimal 免裝 hook，端到端最短
    assert result.returncode == 0, result.stdout + result.stderr

    hidden = tmp / config_of(tmp)["hidden_test_paths"][0]
    hidden.mkdir(parents=True, exist_ok=True)
    (hidden / "test_h.py").write_text(
        "import unittest\nfrom app import double\n\n"
        "class T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(double(21), 42)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp)}
    env.pop("HARNESS_HIDDEN_DIR", None)
    sealed = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "seal-hidden-tests.py"), "--task-id", "T-001"],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        cwd=str(tmp), env=env,
    )
    assert sealed.returncode == 0, f"精靈設定下封存失敗：{sealed.stdout}\n{sealed.stderr}"


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
