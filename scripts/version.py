#!/usr/bin/env python3
"""version.py — 讀寫「模板產出的那個專案」的版本號

## 為什麼模板要管這件事

使用者回報「壞掉了」的時候，第一個要問的就是**哪一版**。沒有版本號，
回報無法定位（不知道是哪一版出現的），修好之後也無法交代（不知道從哪一版起沒事）。
而版本號如果只靠人記得更新，它一定會漂——漂掉的版本號比沒有版本號更糟，
因為它看起來是可信的。

所以這裡把它變成機制：

- **版本存在哪由專案自己宣告**（`harness.config.json` 的 `version` 區塊），
  因為那是語言慣例：Node 在 `package.json`、Python 在 `pyproject.toml`、
  其他專案常常就是一個 `VERSION` 檔。寫死任何一種都會讓其他語言用不了。
- **只有這支腳本會寫它**。implementer 不可以自己改版本檔，理由跟已鎖定的公開測試
  一樣：版本是治理資訊，不是實作的一部分。`guard-hidden-tests.py` 會擋下
  implementer 對它的寫入動作。
- **每個 session 開始都會報告目前版本**（`guard-selfcheck.py`），
  沒有版本號時會明講。「這個專案沒在管版本」不該是無聲的。

## 這支腳本刻意不做的事

不做版本號的事後雜湊稽核（像 `verify-locks.py` 對公開測試那樣）。理由是
失效的性質不同：版本號被偷改是**吵鬧失效**——`git diff` 直接看得到，
下次 `--bump` 從當前值算出來的結果也會不對勁。而隱藏測試洩題是**沉默的**，
那才需要事後稽核。多做一層在這裡換不到對應的價值，只會多一個要維護的東西。

## 用法

    python3 scripts/version.py                      # 顯示目前版本
    python3 scripts/version.py --json               # 機器可讀
    python3 scripts/version.py --check              # 有版本且合法才 exit 0
    python3 scripts/version.py --init               # 沒有版本檔時建立（0.1.0）
    python3 scripts/version.py --bump patch         # 0.1.0 -> 0.1.1
    python3 scripts/version.py --set 1.2.3          # 直接指定

## 該升哪一位（Orchestrator 的判準）

| 位 | 什麼情況 |
|---|---|
| MAJOR | 對外介面 breaking：API 合約、CLI 參數、設定檔或資料格式改得不相容 |
| MINOR | 新增功能，既有用法不受影響 |
| PATCH | bug 修正，沒有介面變動——`seal-hidden-tests.py --kind bugfix` 的 task 天然落在這裡 |

exit code：
    0 = 成功
    1 = 沒有版本號、版本字串不合法、或檔案讀寫失敗
    2 = 參數錯誤（例如 --bump 給了不認得的層級）
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

INITIAL_VERSION = "0.1.0"

# semver 的核心三位，外加選用的 pre-release / build metadata。
# 只認這個形狀是刻意的：`v1.2`、`2024.09` 這種東西 --bump 沒辦法一致地處理，
# 與其猜，不如在這裡就明確拒絕。
VERSION_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)

BUMP_LEVELS = ("major", "minor", "patch")


class VersionError(Exception):
    """版本讀寫失敗。呼叫端要明確報錯，不可以自己猜一個版本繼續。"""


# ------------------------------------------------------------------ 版本字串


def parse(text: str) -> dict:
    match = VERSION_PATTERN.match((text or "").strip())
    if not match:
        raise VersionError(
            f"「{(text or '').strip()}」不是合法的 semver 版本（要 x.y.z，"
            "可帶 -pre 或 +build）。"
        )
    return {
        "major": int(match.group("major")),
        "minor": int(match.group("minor")),
        "patch": int(match.group("patch")),
        "pre": match.group("pre"),
        "build": match.group("build"),
    }


def bump(text: str, level: str) -> str:
    """升版。pre-release / build metadata 會被丟掉——那是刻意的：
    `1.2.3-rc1` 升 patch 之後應該是 `1.2.4`，不是 `1.2.4-rc1`。"""
    if level not in BUMP_LEVELS:
        raise VersionError(f"`{level}` 不是有效的層級，只能是 {list(BUMP_LEVELS)}。")
    parts = parse(text)
    if level == "major":
        return f"{parts['major'] + 1}.0.0"
    if level == "minor":
        return f"{parts['major']}.{parts['minor'] + 1}.0"
    return f"{parts['major']}.{parts['minor']}.{parts['patch'] + 1}"


# ------------------------------------------------------------------ 讀寫


def _nested_get(data, dotted_key: str):
    current = data
    for segment in dotted_key.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _nested_set(data, dotted_key: str, value) -> None:
    segments = dotted_key.split(".")
    current = data
    for segment in segments[:-1]:
        nxt = current.get(segment)
        if not isinstance(nxt, dict):
            nxt = {}
            current[segment] = nxt
        current = nxt
    current[segments[-1]] = value


def _toml_version_line(lines, section):
    """回傳 `version = "..."` 那一行的索引，找不到回 None。

    用正則而不是 tomllib：tomllib 是 3.11 才有的，而本模板支援到 3.9；
    而且就算解析得出來，寫回去也會把註解與排版整個重排。這裡只需要換一行。
    """
    in_section = section is None
    header = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*$")
    assign = re.compile(r'^\s*version\s*=\s*["\']')
    for index, line in enumerate(lines):
        found = header.match(line)
        if found:
            in_section = (section is not None and found.group(1) == section)
            continue
        if in_section and assign.match(line):
            return index
    return None


def read_version(root: Path, settings: dict):
    """回傳版本字串；版本檔不存在或裡面沒有版本欄位時回 None。"""
    path = root / settings["file"]
    if not path.is_file():
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VersionError(f"讀不到版本檔「{settings['file']}」：{exc}") from exc

    if settings["format"] == "plain":
        return text.strip() or None

    if settings["format"] == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VersionError(f"版本檔「{settings['file']}」不是合法 JSON：{exc}") from exc
        value = _nested_get(data, settings["key"])
        return value if isinstance(value, str) and value.strip() else None

    lines = text.splitlines()
    index = _toml_version_line(lines, settings["section"])
    if index is None:
        return None
    found = re.search(r'["\']([^"\']*)["\']', lines[index])
    return found.group(1).strip() if found and found.group(1).strip() else None


def write_version(root: Path, settings: dict, version: str) -> Path:
    parse(version)  # 寫進去之前先擋下不合法的值
    path = root / settings["file"]

    if settings["format"] == "plain":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(version + "\n", encoding="utf-8")
        return path

    if settings["format"] == "json":
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise VersionError(
                    f"版本檔「{settings['file']}」不是合法 JSON，拒絕覆寫：{exc}"
                ) from exc
            if not isinstance(data, dict):
                raise VersionError(f"版本檔「{settings['file']}」的最外層必須是物件。")
        else:
            data = {}
        _nested_set(data, settings["key"], version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    # toml：只換那一行，不重排整個檔案（註解與排版要留著）
    if not path.is_file():
        raise VersionError(
            f"版本檔「{settings['file']}」不存在。toml 格式不會幫你生一個新檔案——"
            "那會產出一份缺了其他必要欄位的殘檔。請先建立它再重試。"
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    index = _toml_version_line(lines, settings["section"])
    if index is None:
        where = f"[{settings['section']}] 段落裡" if settings["section"] else "檔案裡"
        raise VersionError(
            f"在「{settings['file']}」的{where}找不到 `version = \"...\"`。"
            "請先手動加一行，之後這支腳本才知道要改哪裡。"
        )
    lines[index] = re.sub(r'(["\'])([^"\']*)(["\'])',
                          lambda m: m.group(1) + version + m.group(3), lines[index], count=1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ CLI


def describe(settings: dict) -> str:
    target = settings["file"]
    if settings["format"] == "json":
        return f"{target}（JSON 的 `{settings['key']}`）"
    if settings["format"] == "toml":
        section = f"[{settings['section']}] 的 " if settings["section"] else ""
        return f"{target}（{section}`version`）"
    return f"{target}（純文字）"


def main() -> int:
    parser = argparse.ArgumentParser(description="讀寫產出專案的版本號（semver）")
    parser.add_argument("--json", action="store_true", help="機器可讀輸出")
    parser.add_argument("--check", action="store_true",
                        help="有版本號且合法才 exit 0；沒有或不合法 exit 1")
    parser.add_argument("--init", action="store_true",
                        help=f"沒有版本號時建立（{INITIAL_VERSION}）；已經有就什麼都不做")
    parser.add_argument("--bump", choices=BUMP_LEVELS, help="升版")
    parser.add_argument("--set", dest="set_to", help="直接指定版本（x.y.z）")

    args = parser.parse_args()

    writes = [bool(args.bump), bool(args.set_to), bool(args.init)]
    if sum(writes) > 1:
        print("--bump / --set / --init 一次只能給一個。", file=sys.stderr)
        return 2

    root = harness_config.repo_root()
    try:
        settings = harness_config.load(root)["version"]
    except harness_config.ConfigError as exc:
        print(f"harness.config.json 有問題：{exc}", file=sys.stderr)
        return 1

    try:
        current = read_version(root, settings)

        if args.init:
            if current is not None:
                new_version = current
                changed = False
            else:
                write_version(root, settings, INITIAL_VERSION)
                new_version = INITIAL_VERSION
                changed = True
        elif args.bump:
            if current is None:
                raise VersionError(
                    f"{describe(settings)} 裡還沒有版本號，無法升版。"
                    "先跑 `python3 scripts/version.py --init`。"
                )
            new_version = bump(current, args.bump)
            write_version(root, settings, new_version)
            changed = True
        elif args.set_to:
            new_version = args.set_to.strip()
            write_version(root, settings, new_version)
            changed = True
        else:
            new_version = current
            changed = False
            if current is not None:
                parse(current)  # --show / --check 也要驗格式
    except VersionError as exc:
        if args.json:
            # 鍵名用 reason 不用 error：init.py --json 的契約裡 `error` 專指
            # 「這一步的輸出不是合法 JSON」，占用它會讓真正的解析失敗無法分辨。
            print(json.dumps({"ok": False, "reason": str(exc),
                              "source": settings["file"]}, ensure_ascii=False))
        else:
            print(f"❌ {exc}", file=sys.stderr)
        return 1

    if new_version is None:
        message = (
            f"這個專案還沒有版本號（預期在 {describe(settings)}）。\n"
            "模板產出的程式要有版本號——沒有的話，使用者回報「壞掉了」時無法定位是哪一版。\n"
            "建立：python3 scripts/version.py --init"
        )
        if args.json:
            print(json.dumps({"ok": False, "version": None, "reason": "沒有版本號",
                              "source": settings["file"]}, ensure_ascii=False))
        elif args.check:
            print(f"❌ {message}", file=sys.stderr)
        else:
            print(message)
        return 1

    if args.json:
        payload = {"ok": True, "version": new_version, "source": settings["file"],
                   "format": settings["format"], "changed": changed}
        if changed and current:
            payload["previous"] = current
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if changed and current and current != new_version:
        print(f"{current} → {new_version}（寫進 {describe(settings)}）")
    elif changed:
        print(f"{new_version}（寫進 {describe(settings)}）")
    elif args.init:
        print(f"{new_version}（已經有版本號，沒有改動）")
    elif args.check:
        print(f"版本號正常：{new_version}（來源 {describe(settings)}）")
    else:
        print(new_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
