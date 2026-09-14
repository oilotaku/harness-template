#!/usr/bin/env python3
"""release_archive.py — 每個版本各開一個資料夾，存放那一版的完整原始碼

由 `scripts/version.py` 在升版時自動呼叫，也可以用 `version.py --archive` 手動觸發。

## 這件事的取捨（誠實記載）

git 本來就存了完整歷史，所以「每版再複製一份原始碼」在版控的角度是重複的，
而且會讓 repo 隨版本數線性長大。這是使用者明確要求的做法，理由通常是
**不必會用 git 也能直接拿到某一版**——那個需求是真的，代價也是真的。

代價寫在這裡，不要假裝沒有：

- repo 大小隨版本數成長，`git clone` 會越來越慢
- 同一份程式碼存在多個位置，改 bug 時要清楚「該改哪一份」
  （答案永遠是：改工作目錄那一份，歸檔是唯讀的快照，不回頭修改）

## 絕對不能被快照進去的東西

**隱藏測試暫存區**。這是所有排除項目裡唯一會造成安全問題的：把暫存區複製進
`releases/` 等於把題目放到一個 implementer 讀得到的地方，整套防作弊機制當場失效。
封存之後暫存區本來就是空的，但升版不保證發生在封存之後，所以這裡一律排除，
不依賴時序。

其餘排除項目只是為了不要把垃圾與無限遞迴放進去：歸檔目錄自己（會遞迴）、
`.git/`、`.harness/`、各語言的建置產物與相依套件目錄。

## 不覆蓋已經存在的版本

`releases/1.2.0/` 已經存在時直接拒絕，不會默默覆蓋。一個已發布版本的快照
被就地改掉，比沒有快照更糟——它看起來是那一版，其實不是。
真的要重做要自己先刪掉那個資料夾。
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402

DEFAULT_ARCHIVE_DIR = "releases"
META_FILENAME = ".release-meta.json"

# 這些目錄不進快照。第一個是安全問題，其餘只是不要放垃圾進去。
ALWAYS_EXCLUDE = (
    ".git",
    ".harness",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    ".next",
    "vendor",
)


class ArchiveError(Exception):
    """歸檔失敗。呼叫端要明確報錯——默默跳過歸檔等於使用者以為有、其實沒有。"""


def settings_of(config: dict) -> dict:
    """從 harness.config.json 的 version 區塊取出歸檔設定。"""
    raw = (config.get("version") or {}).get("archive")
    if raw is False:
        return {"enabled": False, "dir": DEFAULT_ARCHIVE_DIR, "exclude": []}
    if raw is None:
        raw = {}
    return {
        "enabled": True,
        "dir": raw.get("dir", DEFAULT_ARCHIVE_DIR),
        "exclude": list(raw.get("exclude") or []),
    }


def archive_dir(root: Path, settings: dict) -> Path:
    return root / settings["dir"]


def excluded_names(root: Path, settings: dict) -> set:
    """不進快照的**目錄名稱**（第一層與巢狀都適用）。"""
    names = set(ALWAYS_EXCLUDE)
    names.add(settings["dir"].strip("/").split("/")[0])
    names.update(str(name).strip("/") for name in settings["exclude"] if str(name).strip("/"))
    return names


def hidden_prefixes(root: Path) -> list:
    """隱藏測試暫存區的 repo 相對路徑前綴。萬用字元取固定的那一段。

    設定載入失敗時丟出去——這裡不可以 fail-open。讀不到設定就不知道要排除什麼，
    而「不知道要排除什麼」的正確反應是停下來，不是照樣複製。
    """
    config = harness_config.load(root)
    prefixes = []
    for pattern in config["hidden_test_paths"]:
        fixed = pattern.split("*")[0].split("?")[0].strip("/")
        if fixed:
            prefixes.append(fixed)
    return prefixes


def _git_commit(root: Path):
    if shutil.which("git") is None or not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def _should_skip_dir(rel: str, names: set, hidden: list) -> bool:
    parts = [p for p in rel.split("/") if p]
    if any(part in names for part in parts):
        return True
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in hidden)


def plan(root: Path, settings: dict) -> list:
    """要複製哪些檔案（repo 相對路徑）。先算清單再複製，才有辦法先檢查再動手。"""
    names = excluded_names(root, settings)
    hidden = hidden_prefixes(root)

    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir

        # 就地修剪 dirnames，os.walk 才不會走進去（也才不會為了排除而白走一遍）
        kept = []
        for name in dirnames:
            child = f"{rel_dir}/{name}" if rel_dir else name
            if _should_skip_dir(child, names, hidden):
                continue
            # 符號連結不跟進：它可能指到 repo 之外，快照會因此包含不該包含的東西
            if (Path(dirpath) / name).is_symlink():
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            source = Path(dirpath) / name
            if source.is_symlink():
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            files.append(rel)
    return sorted(files)


def create(root: Path, settings: dict, version: str) -> dict:
    """把目前工作目錄的原始碼複製成 releases/<version>/。回傳歸檔資訊。"""
    if not settings["enabled"]:
        return {"skipped": "設定裡 version.archive 是 false"}

    target = archive_dir(root, settings) / version
    if target.exists():
        raise ArchiveError(
            f"「{target.relative_to(root)}」已經存在，拒絕覆蓋。"
            "一個已發布版本的快照被就地改掉，比沒有快照更糟——它看起來是那一版，"
            "其實不是。真的要重做請先自行刪除該資料夾。"
        )

    files = plan(root, settings)
    if not files:
        raise ArchiveError("沒有任何檔案可以歸檔（排除清單是不是把整個 repo 都排掉了？）。")

    target.mkdir(parents=True)
    for rel in files:
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, destination)

    meta = {
        "version": version,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _git_commit(root),
        "file_count": len(files),
    }
    (target / META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    meta["path"] = target.relative_to(root).as_posix()
    return meta


def listing(root: Path, settings: dict) -> list:
    """已經歸檔的版本（照資料夾名排序）。"""
    base = archive_dir(root, settings)
    if not base.is_dir():
        return []
    found = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        meta_path = child / META_FILENAME
        meta = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        found.append({"version": child.name, **meta})
    return found
