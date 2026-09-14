#!/usr/bin/env python3
"""test-version.py — scripts/version.py 與版本檔保護的離線回歸測試

跟 test-vault.py / test-guards.py 同樣的作法：在暫存目錄裡造一個最小的專案，
用 subprocess 執行腳本並斷言結果。

這批測試守的是四件事：
  1. 三種版本來源（plain / JSON / TOML）都讀得到、寫得回去
  2. 寫回去**不會破壞檔案的其他內容**（JSON 的其他鍵、TOML 的註解與其他段落）
  3. 不合法的版本字串會被擋下，而不是默默寫進去
  4. implementer 改不到版本檔（guard 擋寫入），但讀得到

用法：python3 scripts/test-version.py
"""
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
VERSION = SCRIPTS_DIR / "version.py"
GUARD = SCRIPTS_DIR / "guard-hidden-tests.py"

CASES = []

PACKAGE_JSON = '{\n  "name": "demo",\n  "version": "2.4.9",\n  "scripts": {"build": "x"}\n}\n'
PYPROJECT = (
    "# 檔頭註解\n"
    "[build-system]\n"
    'version = "9.9.9"\n'
    "\n"
    "[project]\n"
    'name = "demo"\n'
    'version = "1.0.0"  # 行尾註解\n'
)


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def make_project(tmp: Path, config=None, files=None) -> Path:
    repo = tmp / "project"
    repo.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (repo / "harness.config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )
    for name, content in (files or {}).items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def run(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERSION), *args],
        cwd=str(repo),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def guard(repo: Path, payload: dict) -> int:
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        cwd=str(repo),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode


# ----------------------------------------------------------------- 基本讀寫


@case("沒有版本號時 --check 回 1，而且訊息說得出要怎麼補")
def _(tmp: Path):
    repo = make_project(tmp)
    result = run(repo, "--check")
    assert result.returncode == 1, result.stdout
    combined = result.stdout + result.stderr
    assert "--init" in combined, f"沒有告訴人怎麼建立版本號：{combined}"


@case("--init 建立 0.1.0；再跑一次不會把版本蓋回去")
def _(tmp: Path):
    repo = make_project(tmp)
    assert run(repo, "--init").returncode == 0
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"

    run(repo, "--bump", "minor")
    again = run(repo, "--init")
    assert again.returncode == 0, again.stderr
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0", (
        "--init 把已經升過的版本蓋回 0.1.0 了"
    )


@case("三個層級各升各的位，而且低位會歸零")
def _(tmp: Path):
    repo = make_project(tmp, files={"VERSION": "1.2.3\n"})
    assert run(repo, "--bump", "patch").returncode == 0
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "1.2.4"
    run(repo, "--bump", "minor")
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "1.3.0"
    run(repo, "--bump", "major")
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "2.0.0"


@case("--bump 會丟掉 pre-release / build（1.2.3-rc1 升 patch 是 1.2.4）")
def _(tmp: Path):
    repo = make_project(tmp, files={"VERSION": "1.2.3-rc1+build5\n"})
    assert run(repo, "--bump", "patch").returncode == 0
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "1.2.4"


@case("沒有版本號時 --bump 拒絕，不會自己從 0.0.0 開始猜")
def _(tmp: Path):
    repo = make_project(tmp)
    result = run(repo, "--bump", "patch")
    assert result.returncode == 1, result.stdout
    assert not (repo / "VERSION").exists(), "拒絕之後竟然還是寫了一個檔案"


# ------------------------------------------------------------- 不合法的輸入


@case("不合法的版本字串在 --check 就被擋下（v1.2 / 1.2 / 文字）")
def _(tmp: Path):
    for bad in ("v1.2", "1.2", "1.2.3.4", "latest", "01.2.3"):
        repo = make_project(tmp / bad.replace(".", "_"), files={"VERSION": bad + "\n"})
        result = run(repo, "--check")
        assert result.returncode == 1, f"「{bad}」竟然被當成合法版本：{result.stdout}"


@case("--set 不合法的值時，原本的版本不會被破壞")
def _(tmp: Path):
    repo = make_project(tmp, files={"VERSION": "1.2.3\n"})
    result = run(repo, "--set", "nope")
    assert result.returncode == 1, result.stdout
    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "1.2.3", (
        "寫入被拒絕，但檔案已經被動過了"
    )


@case("--bump / --set / --init 一次只能給一個")
def _(tmp: Path):
    repo = make_project(tmp, files={"VERSION": "1.2.3\n"})
    result = run(repo, "--bump", "patch", "--set", "2.0.0")
    assert result.returncode == 2, result.stdout


@case("harness.config.json 的 version 區塊打錯欄位名時 fail-closed，不是靜靜用預設值")
def _(tmp: Path):
    repo = make_project(tmp, config={"version": {"fil": "VERSION"}})
    result = run(repo, "--check")
    assert result.returncode == 1, result.stdout
    assert "無法辨識" in (result.stdout + result.stderr), result.stderr


# ------------------------------------------------------- JSON / TOML 不被破壞


@case("JSON 來源：讀得到、升得動，而且其他鍵原封不動")
def _(tmp: Path):
    repo = make_project(
        tmp,
        config={"version": {"file": "package.json", "format": "json"}},
        files={"package.json": PACKAGE_JSON},
    )
    assert run(repo).stdout.strip() == "2.4.9"
    assert run(repo, "--bump", "minor").returncode == 0

    data = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    assert data["version"] == "2.5.0", data
    assert data["name"] == "demo", "升版把其他欄位弄丟了"
    assert data["scripts"] == {"build": "x"}, "升版把巢狀內容弄丟了"


@case("JSON 來源：key 可以指巢狀路徑")
def _(tmp: Path):
    repo = make_project(
        tmp,
        config={"version": {"file": "meta.json", "format": "json", "key": "app.version"}},
        files={"meta.json": '{"app": {"version": "3.1.4", "name": "x"}}\n'},
    )
    assert run(repo).stdout.strip() == "3.1.4"
    run(repo, "--bump", "patch")
    data = json.loads((repo / "meta.json").read_text(encoding="utf-8"))
    assert data["app"]["version"] == "3.1.5", data
    assert data["app"]["name"] == "x", data


@case("TOML 來源：只換那一行，註解與其他段落都留著")
def _(tmp: Path):
    repo = make_project(
        tmp,
        config={"version": {"file": "pyproject.toml", "format": "toml", "section": "project"}},
        files={"pyproject.toml": PYPROJECT},
    )
    assert run(repo).stdout.strip() == "1.0.0", "讀到的不是 [project] 段落的版本"
    assert run(repo, "--bump", "patch").returncode == 0

    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert "# 檔頭註解" in text, "註解被吃掉了"
    assert "# 行尾註解" in text, "行尾註解被吃掉了"
    assert 'version = "9.9.9"' in text, "動到了 [build-system] 段落的版本"
    assert 'version = "1.0.1"' in text, text


@case("TOML 來源：檔案裡沒有 version 那一行時明確報錯，不會亂加")
def _(tmp: Path):
    repo = make_project(
        tmp,
        config={"version": {"file": "pyproject.toml", "format": "toml", "section": "project"}},
        files={"pyproject.toml": '[project]\nname = "demo"\n'},
    )
    result = run(repo, "--set", "1.0.0")
    assert result.returncode == 1, result.stdout
    assert "找不到" in (result.stdout + result.stderr), result.stderr


# ------------------------------------------------------------------- 機器可讀


@case("--json 只吐 JSON，而且帶得出前一版")
def _(tmp: Path):
    repo = make_project(tmp, files={"VERSION": "1.2.3\n"})
    result = run(repo, "--json")
    payload = json.loads(result.stdout)
    assert payload == {"ok": True, "version": "1.2.3", "source": "VERSION",
                       "format": "plain", "changed": False, "mirrors": 0}, payload

    bumped = json.loads(run(repo, "--bump", "patch", "--json").stdout)
    assert bumped["version"] == "1.2.4" and bumped["previous"] == "1.2.3", bumped


@case("--json 在沒有版本號時仍然是合法 JSON（ok=false），不是中文散文")
def _(tmp: Path):
    repo = make_project(tmp)
    payload = json.loads(run(repo, "--json").stdout)
    assert payload["ok"] is False and payload["version"] is None, payload


# --------------------------------------------------------------- guard 保護


@case("guard 擋下 implementer 對版本檔的寫入（Write / Edit / 重導向 / rm）")
def _(tmp: Path):
    repo = make_project(tmp, files={"VERSION": "1.0.0\n"})
    blocked = [
        {"tool_name": "Write", "tool_input": {"file_path": "VERSION"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "VERSION"}},
        {"tool_name": "Bash", "tool_input": {"command": "echo 9.9.9 > VERSION"}},
        {"tool_name": "Bash", "tool_input": {"command": "rm VERSION"}},
        {"tool_name": "Bash", "tool_input": {"command": "sed -i s/1/2/ VERSION"}},
    ]
    for payload in blocked:
        assert guard(repo, payload) == 2, f"沒有擋下：{payload}"


@case("guard 不擋讀取版本檔（implementer 有正當理由讀，例如實作 --version）")
def _(tmp: Path):
    repo = make_project(tmp, files={"VERSION": "1.0.0\n"})
    allowed = [
        {"tool_name": "Read", "tool_input": {"file_path": "VERSION"}},
        {"tool_name": "Bash", "tool_input": {"command": "cat VERSION"}},
        {"tool_name": "Bash", "tool_input": {"command": "python3 scripts/version.py --bump patch"}},
        {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
    ]
    for payload in allowed:
        assert guard(repo, payload) == 0, f"誤擋：{payload}"


@case("版本檔換成 package.json 時，guard 跟著保護新的路徑")
def _(tmp: Path):
    repo = make_project(
        tmp,
        config={"version": {"file": "package.json", "format": "json"}},
        files={"package.json": PACKAGE_JSON, "VERSION": "1.0.0\n"},
    )
    assert guard(repo, {"tool_name": "Write", "tool_input": {"file_path": "package.json"}}) == 2, (
        "設定指到 package.json，guard 卻沒保護它"
    )
    assert guard(repo, {"tool_name": "Write", "tool_input": {"file_path": "VERSION"}}) == 0, (
        "設定沒有指到 VERSION 了，卻還在擋它"
    )


# ------------------------------------------- 鏡像：不必跑工具就看得到，且不會漂

MIRROR_CONFIG = {
    "version": {
        "file": "VERSION",
        "mirrors": [
            {"file": "README.md", "pattern": "**版本**：{version}"},
            {"file": "src/app.py", "pattern": '__version__ = "{version}"'},
            {"file": "package.json", "format": "json", "key": "version"},
        ],
    }
}

MIRROR_FILES = {
    "VERSION": "1.0.0\n",
    "README.md": "# Demo\n\n**版本**：1.0.0\n\n說明文字要留著。\n",
    "src/app.py": '__version__ = "1.0.0"\n\ndef main():\n    print(__version__)\n',
    "package.json": '{\n  "name": "demo",\n  "version": "1.0.0"\n}\n',
}


def mirror_project(tmp: Path) -> Path:
    return make_project(tmp, config=MIRROR_CONFIG, files=dict(MIRROR_FILES))


@case("全部一致時 --check 通過，而且說得出檢查了幾個鏡像")
def _(tmp: Path):
    repo = mirror_project(tmp)
    result = run(repo, "--check")
    assert result.returncode == 0, result.stderr
    assert "3 個鏡像一致" in result.stdout, result.stdout


@case("--bump 一次同步所有鏡像（逐行樣板與 JSON 都要跟上）")
def _(tmp: Path):
    repo = mirror_project(tmp)
    assert run(repo, "--bump", "minor").returncode == 0

    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "1.1.0"
    assert "**版本**：1.1.0" in (repo / "README.md").read_text(encoding="utf-8")
    assert '__version__ = "1.1.0"' in (repo / "src" / "app.py").read_text(encoding="utf-8")
    assert json.loads((repo / "package.json").read_text(encoding="utf-8"))["version"] == "1.1.0"


@case("同步鏡像不會破壞檔案的其他內容")
def _(tmp: Path):
    repo = mirror_project(tmp)
    run(repo, "--bump", "major")

    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "說明文字要留著。" in readme, readme
    assert readme.startswith("# Demo"), readme

    app = (repo / "src" / "app.py").read_text(encoding="utf-8")
    assert "def main():" in app and "print(__version__)" in app, app
    assert json.loads((repo / "package.json").read_text(encoding="utf-8"))["name"] == "demo"


@case("鏡像漂掉時 --check 回 1，而且指得出是哪一個、現在寫的是什麼")
def _(tmp: Path):
    repo = mirror_project(tmp)
    (repo / "README.md").write_text("# Demo\n\n**版本**：0.0.1\n", encoding="utf-8")

    result = run(repo, "--check")
    assert result.returncode == 1, result.stdout
    combined = result.stdout + result.stderr
    assert "README.md" in combined and "0.0.1" in combined, combined
    assert "src/app.py" not in combined, f"沒漂的鏡像不該被列出來：{combined}"


@case("漂掉時 --json 是 ok=false 並帶出 drifted 清單")
def _(tmp: Path):
    repo = mirror_project(tmp)
    (repo / "package.json").write_text('{"name": "demo", "version": "9.9.9"}\n', encoding="utf-8")

    result = run(repo, "--json")
    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False, payload
    assert payload["version"] == "1.0.0", payload
    assert payload["drifted"] == [{"file": "package.json", "found": "9.9.9"}], payload


@case("鏡像找不到版本時，**任何地方都不會被寫入**（先全部確認再動手）")
def _(tmp: Path):
    repo = mirror_project(tmp)
    # 把 README 的那一行整個拿掉：樣板不存在了，鏡像無處可寫。
    (repo / "README.md").write_text("# Demo\n\n沒有版本那一行了。\n", encoding="utf-8")

    result = run(repo, "--bump", "patch")
    assert result.returncode == 1, result.stdout

    assert (repo / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0", (
        "主要來源被寫進去了——寫到一半失敗會留下各處不一致的狀態"
    )
    assert '__version__ = "1.0.0"' in (repo / "src" / "app.py").read_text(encoding="utf-8"), (
        "其他鏡像被寫進去了"
    )
    assert json.loads((repo / "package.json").read_text(encoding="utf-8"))["version"] == "1.0.0"


@case("鏡像不會自己插一行進去，而是明講找不到樣板")
def _(tmp: Path):
    repo = mirror_project(tmp)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    result = run(repo, "--set", "2.0.0")
    combined = result.stdout + result.stderr
    assert "找不到版本號" in combined or "找不到樣板" in combined, combined
    assert "**版本**" not in (repo / "README.md").read_text(encoding="utf-8"), "竟然自己加了一行"


@case("pattern 裡的正則特殊字元被當成字面文字（`[`、`.`、`*` 不會變成萬用）")
def _(tmp: Path):
    repo = make_project(
        tmp,
        config={"version": {"file": "VERSION", "mirrors": [
            {"file": "app.ini", "pattern": "[app] ver=v{version} (stable)"},
        ]}},
        files={"VERSION": "1.0.0\n", "app.ini": "[app] ver=v1.0.0 (stable)\n"},
    )
    assert run(repo, "--check").returncode == 0, run(repo, "--check").stderr
    assert run(repo, "--bump", "patch").returncode == 0
    assert (repo / "app.ini").read_text(encoding="utf-8").strip() == "[app] ver=v1.0.1 (stable)"


@case("設定驗證：pattern 要有剛好一個 {version}，而且不能同時給 format")
def _(tmp: Path):
    bad_patterns = [
        {"file": "README.md", "pattern": "沒有佔位符"},
        {"file": "README.md", "pattern": "{version} 又一個 {version}"},
        {"file": "README.md", "pattern": "版本 {version}", "format": "json"},
    ]
    for index, mirror in enumerate(bad_patterns):
        repo = make_project(
            tmp / f"bad{index}",
            config={"version": {"file": "VERSION", "mirrors": [mirror]}},
            files={"VERSION": "1.0.0\n"},
        )
        result = run(repo, "--check")
        assert result.returncode == 1, f"{mirror} 竟然被接受：{result.stdout}"


@case("設定驗證：mirrors 不是陣列、或項目缺 file 時 fail-closed")
def _(tmp: Path):
    for index, bad in enumerate(["不是陣列", [{"pattern": "{version}"}], [{"file": "", }]]):
        repo = make_project(
            tmp / f"shape{index}",
            config={"version": {"file": "VERSION", "mirrors": bad}},
            files={"VERSION": "1.0.0\n"},
        )
        result = run(repo, "--check")
        assert result.returncode == 1, f"{bad!r} 竟然被接受：{result.stdout}"



# ------------------------------------ 版本歸檔：每個版本各開一個資料夾存放原始碼


def archive_project(tmp: Path, config=None) -> Path:
    """一個有原始碼、也有「絕對不能被快照進去」那些東西的最小專案。"""
    repo = make_project(tmp, config=config, files={
        "VERSION": "1.0.0\n",
        "README.md": "# Demo\n",
        "src/app.py": "print('hi')\n",
        # 以下都不該出現在快照裡
        "tests/hidden/test_secret.py": "# 題目，洩出去整套機制就失效\n",
        "node_modules/junk.js": "x\n",
        ".harness/state.json": "{}\n",
    })
    return repo


def snapshot_files(repo: Path, version: str, base: str = "releases") -> list:
    root = repo / base / version
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@case("升版會把原始碼另存成 releases/<版本>/")
def _(tmp: Path):
    repo = archive_project(tmp)
    result = run(repo, "--bump", "minor")
    assert result.returncode == 0, result.stderr
    assert (repo / "releases" / "1.1.0").is_dir(), result.stdout

    files = snapshot_files(repo, "1.1.0")
    assert "src/app.py" in files and "README.md" in files, files


@case("隱藏測試暫存區**絕對不會**被快照進去（進去等於從歸檔資料夾洩題）")
def _(tmp: Path):
    repo = archive_project(tmp)
    run(repo, "--bump", "patch")

    files = snapshot_files(repo, "1.0.1")
    leaked = [f for f in files if "hidden" in f]
    assert not leaked, f"隱藏測試被快照進去了：{leaked}"


@case("建置產物與治理目錄不進快照（node_modules / .harness / .git）")
def _(tmp: Path):
    repo = archive_project(tmp)
    run(repo, "--bump", "patch")

    files = snapshot_files(repo, "1.0.1")
    for unwanted in ("node_modules", ".harness", ".git"):
        assert not any(f.startswith(unwanted) for f in files), f"{unwanted} 進去了：{files}"


@case("歸檔目錄不會遞迴把自己複製進去")
def _(tmp: Path):
    repo = archive_project(tmp)
    run(repo, "--bump", "patch")
    run(repo, "--bump", "patch")

    files = snapshot_files(repo, "1.0.2")
    assert not any(f.startswith("releases") for f in files), f"遞迴了：{files}"


@case("快照裡的版本號是**新版**，不是升版前那個")
def _(tmp: Path):
    repo = archive_project(tmp)
    run(repo, "--bump", "major")
    archived = (repo / "releases" / "2.0.0" / "VERSION").read_text(encoding="utf-8").strip()
    assert archived == "2.0.0", f"快照裡寫的是 {archived}"


@case("同一版重複歸檔被拒絕，而且說得出為什麼")
def _(tmp: Path):
    repo = archive_project(tmp)
    run(repo, "--bump", "minor")
    marker = repo / "releases" / "1.1.0" / "src" / "app.py"
    original = marker.read_text(encoding="utf-8")

    result = run(repo, "--set", "1.1.0")
    assert result.returncode == 1, result.stdout
    assert marker.read_text(encoding="utf-8") == original, "已發布版本的快照被覆蓋了"


@case("--no-archive 只升版不歸檔；--init 本來就不歸檔")
def _(tmp: Path):
    repo = archive_project(tmp)
    assert run(repo, "--bump", "patch", "--no-archive").returncode == 0
    assert not (repo / "releases").exists(), "--no-archive 還是建了資料夾"

    fresh = make_project(tmp / "fresh")
    assert run(fresh, "--init").returncode == 0
    assert not (fresh / "releases").exists(), "--init 不該歸檔（那時還沒有程式碼）"


@case("archive: false 關掉歸檔；archive.dir 可以改地方")
def _(tmp: Path):
    off = archive_project(tmp / "off", config={"version": {"file": "VERSION", "archive": False}})
    assert run(off, "--bump", "patch").returncode == 0
    assert not (off / "releases").exists(), "設成 false 卻還是歸檔了"

    moved = archive_project(
        tmp / "moved",
        config={"version": {"file": "VERSION", "archive": {"dir": "snapshots"}}},
    )
    assert run(moved, "--bump", "patch").returncode == 0
    assert (moved / "snapshots" / "1.0.1").is_dir(), "自訂目錄沒有生效"
    assert not (moved / "releases").exists()


@case("archive.exclude 可以再排除專案自己的目錄")
def _(tmp: Path):
    repo = archive_project(
        tmp,
        config={"version": {"file": "VERSION",
                            "archive": {"dir": "releases", "exclude": ["src"]}}},
    )
    run(repo, "--bump", "patch")
    files = snapshot_files(repo, "1.0.1")
    assert not any(f.startswith("src") for f in files), files
    assert "README.md" in files, files


@case("每個歸檔都有 .release-meta.json，記著版本與檔案數")
def _(tmp: Path):
    repo = archive_project(tmp)
    run(repo, "--bump", "patch")
    meta = json.loads(
        (repo / "releases" / "1.0.1" / ".release-meta.json").read_text(encoding="utf-8")
    )
    assert meta["version"] == "1.0.1", meta
    assert meta["file_count"] >= 3, meta
    assert "created_at" in meta, meta


@case("--json 帶出歸檔路徑")
def _(tmp: Path):
    repo = archive_project(tmp)
    payload = json.loads(run(repo, "--bump", "patch", "--json").stdout)
    assert payload["archived"] == "releases/1.0.1", payload


@case("歸檔設定壞掉時 fail-closed（archive 不是物件也不是 false）")
def _(tmp: Path):
    repo = archive_project(tmp, config={"version": {"file": "VERSION", "archive": "yes"}})
    result = run(repo, "--check")
    assert result.returncode == 1, result.stdout


@case("guard 擋下對歸檔的寫入，但不擋讀取（比對舊版是正當用途）")
def _(tmp: Path):
    repo = archive_project(tmp)
    run(repo, "--bump", "patch")

    blocked = [
        {"tool_name": "Write", "tool_input": {"file_path": "releases/1.0.1/src/app.py"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "releases/1.0.1/VERSION"}},
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf releases/1.0.1"}},
    ]
    for payload in blocked:
        assert guard(repo, payload) == 2, f"沒有擋下：{payload}"

    allowed = [
        {"tool_name": "Read", "tool_input": {"file_path": "releases/1.0.1/src/app.py"}},
        {"tool_name": "Bash", "tool_input": {"command": "diff releases/1.0.1/src/app.py src/app.py"}},
        {"tool_name": "Write", "tool_input": {"file_path": "releases-notes.md"}},
    ]
    for payload in allowed:
        assert guard(repo, payload) == 0, f"誤擋：{payload}"



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
