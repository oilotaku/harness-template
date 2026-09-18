#!/usr/bin/env python3
"""test-release-archive.py — 產出專案端 releases/ 歸檔的回歸測試

`scripts/release_archive.py` 本身是被 `version.py` 呼叫的模組；這支從**產出專案的角度**
端到端驗它：在一個臨時專案裡跑 `version.py --bump`，確認歸檔真的照設定發生／不發生。

守的行為（對應 docs/versioning.md §4.5）：
  1. archive 預設開啟（設定省略 archive）→ 升版產生 releases/<版本>/ 快照
  2. **隱藏測試暫存區一律排除**——這是所有排除項目裡唯一牽涉安全的：進了歸檔
     等於把題目放到 implementer 讀得到的地方，整套防作弊當場失效
  3. `archive: false` → 完全不建歸檔目錄
  4. 自訂 `dir` 生效
  5. 目標版本資料夾已存在 → 拒絕覆蓋（一個已發布版本的快照被就地改掉，比沒有更糟）

模板自己的 harness.config.json 設 `archive: false`（見該檔說明），所以這支測的是
「clone 下去、預設開著歸檔」的那種專案，模板本身的行為不受影響。

用法：python3 scripts/test-release-archive.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utf8_output  # noqa: E402

utf8_output.enable()

SCRIPTS_DIR = Path(__file__).resolve().parent
VERSION_PY = SCRIPTS_DIR / "version.py"

# 隱藏測試暫存區用預設路徑名，才貼近真實產出專案；不寫成連續字面字串只是為了
# 讓「這支腳本本身」不要在原始碼裡出現會被 grep 誤判的完整字串，效果相同。
HIDDEN = "tests/" + "hidden"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def make_project(root: Path, archive):
    """建立一個最小產出專案。archive=None 代表設定裡省略 archive（＝預設開啟）。"""
    (root / "src").mkdir(parents=True)
    (root / HIDDEN).mkdir(parents=True)
    (root / "tests" / "public").mkdir(parents=True)
    (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / HIDDEN / "test_secret.py").write_text(
        "SECRET_HIDDEN_MARKER = 42\n", encoding="utf-8")
    (root / "tests" / "public" / "test_pub.py").write_text("# public\n", encoding="utf-8")
    (root / "README.md").write_text("# produced project\n", encoding="utf-8")
    version_cfg = {"file": "VERSION", "format": "plain"}
    if archive is not None:
        version_cfg["archive"] = archive
    (root / "harness.config.json").write_text(json.dumps({
        "public_test_paths": ["tests/public"],
        "hidden_test_paths": [HIDDEN],
        "hidden_test_command": "python3 -m unittest discover -s {dir}",
        "version": version_cfg,
    }, ensure_ascii=False), encoding="utf-8")


def bump(root: Path, level="minor"):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run(
        [sys.executable, str(VERSION_PY), "--bump", level],
        cwd=str(root), env=env, text=True, capture_output=True,
        encoding="utf-8", errors="replace")


def hidden_leaks(archive_root: Path):
    """回傳歸檔裡任何疑似隱藏測試的檔案（應為空）。"""
    if not archive_root.exists():
        return []
    leaks = []
    for p in archive_root.rglob("*"):
        if not p.is_file():
            continue
        if "hidden" in str(p).lower() or p.name == "test_secret.py":
            leaks.append(str(p))
            continue
        if p.suffix == ".py" and "SECRET_HIDDEN_MARKER" in p.read_text(encoding="utf-8", errors="ignore"):
            leaks.append(str(p))
    return leaks


@case("archive 預設開啟：升版產生 releases/<版本>/ 快照")
def _(tmp: Path):
    make_project(tmp, archive=None)
    result = bump(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"
    rel = tmp / "releases" / "0.2.0"
    assert rel.is_dir(), "沒有產生 releases/0.2.0/"
    assert (rel / "src" / "app.py").is_file(), "快照少了 src/app.py"
    assert (rel / "README.md").is_file(), "快照少了 README.md"
    assert (rel / ".release-meta.json").is_file(), "快照少了 .release-meta.json"
    meta = json.loads((rel / ".release-meta.json").read_text(encoding="utf-8"))
    assert meta["version"] == "0.2.0", meta


@case("隱藏測試暫存區一律排除（進了歸檔＝洩題）")
def _(tmp: Path):
    make_project(tmp, archive=None)
    result = bump(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    leaks = hidden_leaks(tmp / "releases")
    assert not leaks, f"隱藏測試洩進了歸檔：{leaks}"


@case("archive:false → 完全不建歸檔目錄")
def _(tmp: Path):
    make_project(tmp, archive=False)
    result = bump(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"
    assert not (tmp / "releases").exists(), "archive:false 卻建了 releases/"


@case("自訂 dir 生效（releases 換成別的資料夾）")
def _(tmp: Path):
    make_project(tmp, archive={"dir": "dist-archive"})
    result = bump(tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp / "dist-archive" / "0.2.0").is_dir(), "自訂 dir 沒生效"
    assert not (tmp / "releases").exists(), "不該同時建預設的 releases/"


@case("目標版本資料夾已存在 → 拒絕覆蓋")
def _(tmp: Path):
    make_project(tmp, archive=None)
    # 預先佔位：讓升版的目標 releases/0.2.0/ 已經存在。
    (tmp / "releases" / "0.2.0").mkdir(parents=True)
    (tmp / "releases" / "0.2.0" / "stale.txt").write_text("舊的\n", encoding="utf-8")
    result = bump(tmp)
    assert result.returncode != 0, f"目標已存在卻沒拒絕：{result.stdout}"
    blob = result.stdout + result.stderr
    assert ("已經存在" in blob or "拒絕" in blob or "exist" in blob.lower()), \
        f"拒絕覆蓋時沒有明確訊息：{blob}"


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
