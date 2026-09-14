#!/usr/bin/env python3
"""harness_config.py — 專案自己的測試路徑慣例（harness.config.json）

對應 docs/history/improvement-suggestions.md 的 P1-4。

## 問題

`tests/public` 與 `tests/hidden` 原本是**寫死在程式碼裡**的。但
`docs/multi-language-support.md` 宣稱本模板支援 Go / Rust / TypeScript / Java，
而這些語言的測試慣例根本不長這樣（Go 的 `*_test.go` 放在同一個 package、
JS 常見 `__tests__/` 或 `*.spec.ts`）。使用者一旦照自己語言的慣例放測試，
整套保護就落空——**而且不會有任何錯誤訊息告訴他**，看起來一切正常。

這跟「所有腳本在 Windows 上一 print 就崩潰」是同一類問題：
**宣稱支援，但從來沒有真的驗證過**。

## 解法

專案根目錄放一份 `harness.config.json`（會進版控，因為它描述的是這個專案的慣例）：

```json
{
  "public_test_paths": ["tests/public"],
  "hidden_test_paths": ["tests/hidden"],
  "hidden_test_command": "{python} -m unittest discover -s {dir} -p 'test_*.py' -v",
  "version": { "file": "VERSION", "format": "plain" }
}
```

沒有這個檔案時退回上面那組預設值（Python 專案不必特別設定）。
路徑支援 `*` 萬用字元（例如 monorepo 的 `packages/*/tests/hidden`）。

## `version`：產出的程式自己的版本號

模板產出的專案要有版本號，否則使用者回報「壞掉了」時沒有任何東西可以定位——
不知道是哪一版出現的，也不知道修好之後是哪一版。但版本號存在哪裡是**語言慣例**，
跟測試路徑一樣不能寫死：Node 在 `package.json`、Python 在 `pyproject.toml`、
Go / Rust / 純腳本專案常常就是一個 `VERSION` 檔。

所以這裡只宣告「去哪裡讀寫」，格式支援三種：

| `format` | 讀寫對象 | 例子 |
|---|---|---|
| `plain` | 整個檔案就是版本字串 | `VERSION` |
| `json` | JSON 檔的某個鍵（`key` 可用點號指巢狀路徑，預設 `version`） | `package.json` |
| `toml` | `version = "x.y.z"` 這一行（可用 `section` 限定段落） | `pyproject.toml` |

### `version.mirrors`：版本還寫在哪些地方

版本號應該讓人**不必跑任何工具就看得到**——README 上一行、程式的 `--version`
輸出、套件 manifest。但只要版本同時出現在兩個以上的地方，它就會漂，
而 README 上一個過期的版本號比沒有版本號更糟。

所以「還寫在哪裡」也要宣告出來，由 `scripts/version.py` 一起檢查、一起更新：

```json
{
  "version": {
    "file": "VERSION",
    "mirrors": [
      { "file": "README.md", "pattern": "**版本**：{version}" },
      { "file": "src/app.py", "pattern": "__version__ = \"{version}\"" },
      { "file": "package.json", "format": "json", "key": "version" }
    ]
  }
}
```

`pattern` 是一行的字面樣板，裡面要有**剛好一個** `{version}`；其餘欄位跟主要來源
完全一樣（`format` / `key` / `section`），因為鏡像本來就只是「另一個寫著版本的地方」。

版本號一律是 semver `x.y.z`（見 `scripts/version.py`）。
**版本檔由 `scripts/version.py` 讀寫，implementer 不可以自己改**——
理由跟已鎖定的公開測試一樣：它是治理資訊，不是實作的一部分。


## 為什麼設定錯誤要 fail-closed

載入失敗時本模組丟例外，而 `guard-hidden-tests.py` 的頂層處理會把任何例外
變成 exit 2（擋下操作）。這是刻意的：設定檔打錯字如果只是「靜靜退回預設值」，
等於讓使用者可以用一個 typo 關掉防護，而且完全沒有訊號——正是這一項要修的問題本身。
"""
import fnmatch
import json
import os
from pathlib import Path

CONFIG_FILENAME = "harness.config.json"

DEFAULT_PUBLIC_TEST_PATHS = ["tests/public"]
DEFAULT_HIDDEN_TEST_PATHS = ["tests/hidden"]
# {python} 會被換成執行 runner 的直譯器（sys.executable），{dir} 是解密後的暫存目錄。
DEFAULT_HIDDEN_TEST_COMMAND = "{python} -m unittest discover -s {dir} -p 'test_*.py' -v"

PATH_KEYS = ("public_test_paths", "hidden_test_paths")

# 產出的專案的版本來源。預設是一個純文字 VERSION 檔——對任何語言都成立，
# 而且不需要專案先有 package.json / pyproject.toml 這類檔案。
DEFAULT_VERSION_FILE = "VERSION"
DEFAULT_VERSION_FORMAT = "plain"
DEFAULT_VERSION_KEY = "version"
VERSION_FORMATS = ("plain", "json", "toml")


class ConfigError(Exception):
    """設定檔有問題。呼叫方應該擋下動作或明確報錯，不可以靜靜退回預設值。"""


def repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


def config_path(root: Path = None) -> Path:
    return (root or repo_root()) / CONFIG_FILENAME


def _validate_paths(value, key: str) -> list:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"`{key}` 必須是非空字串陣列（目前是 {type(value).__name__}）。")

    cleaned = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"`{key}` 裡出現空的或非字串的項目：{item!r}")
        normalized = item.strip().replace("\\", "/").strip("/")
        if not normalized or normalized == ".":
            raise ConfigError(
                f"`{key}` 裡的「{item}」等於整個 repo 根目錄。"
                "把整個 repo 設成受保護路徑會讓任何檔案都動不了，請指定實際的測試目錄。"
            )
        if normalized.startswith("..") or Path(normalized).is_absolute():
            raise ConfigError(
                f"`{key}` 裡的「{item}」不在 repo 內。路徑必須是相對於 repo 根目錄的相對路徑。"
            )
        cleaned.append(normalized)
    return cleaned


def _validate_version(value) -> dict:
    """驗證 `version` 區塊。

    跟其他欄位一樣 fail-closed：寧可報錯也不要靜靜退回預設值。打錯欄位名如果只是
    被忽略，結果會是「這個專案其實沒在管版本」而且完全沒有訊號——正是本模組開頭
    那段要修的問題本身。
    """
    if not isinstance(value, dict):
        raise ConfigError(f"`version` 必須是物件（目前是 {type(value).__name__}）。")

    known = {"file", "format", "key", "section", "mirrors"}
    unknown = [k for k in value if k not in known and not k.startswith("$")]
    if unknown:
        raise ConfigError(
            f"`version` 有無法辨識的欄位：{unknown}。可用欄位：{sorted(known)}。"
        )

    file_value = value.get("file", DEFAULT_VERSION_FILE)
    if not isinstance(file_value, str) or not file_value.strip():
        raise ConfigError("`version.file` 必須是非空字串。")
    normalized = file_value.strip().replace("\\", "/").strip("/")
    if not normalized or normalized == "." or normalized.startswith(".."):
        raise ConfigError(f"`version.file` 的「{file_value}」不是 repo 內的相對路徑。")
    if Path(normalized).is_absolute():
        raise ConfigError(f"`version.file` 的「{file_value}」必須是相對於 repo 根目錄的路徑。")

    format_value = value.get("format", DEFAULT_VERSION_FORMAT)
    if format_value not in VERSION_FORMATS:
        raise ConfigError(
            f"`version.format` 必須是 {list(VERSION_FORMATS)} 其中之一，收到 {format_value!r}。"
        )

    key_value = value.get("key", DEFAULT_VERSION_KEY)
    if not isinstance(key_value, str) or not key_value.strip():
        raise ConfigError("`version.key` 必須是非空字串（JSON 可用點號指定巢狀路徑）。")

    section = value.get("section")
    if section is not None and (not isinstance(section, str) or not section.strip()):
        raise ConfigError("`version.section` 若有指定，必須是非空字串。")

    return {
        "file": normalized,
        "format": format_value,
        "key": key_value.strip(),
        "section": section.strip() if isinstance(section, str) else None,
        "pattern": None,
        "mirrors": _validate_mirrors(value.get("mirrors")),
    }


def _validate_mirrors(value) -> list:
    """驗證 `version.mirrors`：版本號還寫在哪些「不必跑工具就看得到」的地方。

    每一筆跟主要來源同一個形狀，額外多一個 `pattern`（一行的字面樣板）。
    這裡驗得嚴是刻意的：鏡像設定寫錯如果只是被忽略，結果會是「以為有在同步、
    其實沒有」——那正是漂移本身，而漂掉的版本號比沒有版本號更糟。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"`version.mirrors` 必須是陣列（目前是 {type(value).__name__}）。")

    known = {"file", "format", "key", "section", "pattern"}
    mirrors = []
    for index, item in enumerate(value):
        where = f"`version.mirrors[{index}]`"
        if not isinstance(item, dict):
            raise ConfigError(f"{where} 必須是物件（目前是 {type(item).__name__}）。")
        unknown = [k for k in item if k not in known and not k.startswith("$")]
        if unknown:
            raise ConfigError(f"{where} 有無法辨識的欄位：{unknown}。可用欄位：{sorted(known)}。")

        file_value = item.get("file")
        if not isinstance(file_value, str) or not file_value.strip():
            raise ConfigError(f"{where} 的 `file` 必須是非空字串。")
        normalized = file_value.strip().replace("\\", "/").strip("/")
        if not normalized or normalized == "." or normalized.startswith(".."):
            raise ConfigError(f"{where} 的「{file_value}」不是 repo 內的相對路徑。")
        if Path(normalized).is_absolute():
            raise ConfigError(f"{where} 的「{file_value}」必須是相對於 repo 根目錄的路徑。")

        pattern = item.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ConfigError(f"{where} 的 `pattern` 必須是非空字串。")
            if pattern.count("{version}") != 1:
                raise ConfigError(
                    f"{where} 的 `pattern` 要有剛好一個 {{version}}，"
                    f"目前有 {pattern.count('{version}')} 個。"
                )
            if "format" in item and item["format"] != "line":
                raise ConfigError(
                    f"{where} 同時給了 `pattern` 與 `format`。有 `pattern` 就是逐行樣板，"
                    "不需要再指定格式。"
                )
            format_value = "line"
        else:
            format_value = item.get("format", DEFAULT_VERSION_FORMAT)
            if format_value not in VERSION_FORMATS:
                raise ConfigError(
                    f"{where} 的 `format` 必須是 {list(VERSION_FORMATS)} 其中之一"
                    "，或改用 `pattern`。"
                )

        key_value = item.get("key", DEFAULT_VERSION_KEY)
        if not isinstance(key_value, str) or not key_value.strip():
            raise ConfigError(f"{where} 的 `key` 必須是非空字串。")

        section = item.get("section")
        if section is not None and (not isinstance(section, str) or not section.strip()):
            raise ConfigError(f"{where} 的 `section` 若有指定，必須是非空字串。")

        mirrors.append({
            "file": normalized,
            "format": format_value,
            "key": key_value.strip(),
            "section": section.strip() if isinstance(section, str) else None,
            "pattern": pattern,
        })
    return mirrors


def load(root: Path = None) -> dict:
    """讀取設定；沒有設定檔時回傳預設值。設定檔有問題時丟 ConfigError。"""
    root = root or repo_root()
    path = config_path(root)

    config = {
        "public_test_paths": list(DEFAULT_PUBLIC_TEST_PATHS),
        "hidden_test_paths": list(DEFAULT_HIDDEN_TEST_PATHS),
        "hidden_test_command": DEFAULT_HIDDEN_TEST_COMMAND,
        "version": {
            "file": DEFAULT_VERSION_FILE,
            "format": DEFAULT_VERSION_FORMAT,
            "key": DEFAULT_VERSION_KEY,
            "section": None,
            "pattern": None,
            "mirrors": [],
        },
        "_source": "預設值（沒有 harness.config.json）",
    }

    if not path.exists():
        return config

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{CONFIG_FILENAME} 讀不到或不是合法 JSON：{exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_FILENAME} 的最外層必須是物件（{{...}}）。")

    known = set(PATH_KEYS) | {"hidden_test_command", "version"}
    unknown = [key for key in raw if key not in known and not key.startswith("$")]
    if unknown:
        raise ConfigError(
            f"{CONFIG_FILENAME} 有無法辨識的欄位：{unknown}。"
            f"可用欄位：{sorted(known)}（以 $ 開頭的欄位會被當成註解忽略）。"
            "刻意擋下而不是忽略，是因為打錯欄位名等於防護悄悄失效。"
        )

    for key in PATH_KEYS:
        if key in raw:
            config[key] = _validate_paths(raw[key], key)

    if "hidden_test_command" in raw:
        command = raw["hidden_test_command"]
        if not isinstance(command, str) or not command.strip():
            raise ConfigError("`hidden_test_command` 必須是非空字串。")
        if "{dir}" not in command:
            raise ConfigError(
                "`hidden_test_command` 必須包含 {dir}（解密後的暫存目錄），"
                "否則測試指令根本不知道要跑哪裡的檔案。"
            )
        config["hidden_test_command"] = command.strip()

    if "version" in raw:
        config["version"] = _validate_version(raw["version"])

    config["_source"] = str(path)
    return config


def matches(rel_path: str, patterns) -> bool:
    """repo 相對路徑是否落在任一個受保護路徑（或其底下）。

    `*` 萬用字元用 fnmatch 比對，而 fnmatch 的 `*` 會跨過 `/`——也就是
    `packages/*/tests/hidden` 也會命中 `packages/a/b/tests/hidden`。
    這個過度比對是**刻意接受**的：對防護機制來說，多擋一點是安全的，
    少擋一點才危險。
    """
    if not rel_path:
        return False
    normalized = rel_path.replace("\\", "/").strip("/")
    for pattern in patterns:
        if "*" in pattern or "?" in pattern:
            if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(
                normalized, pattern.rstrip("/") + "/*"
            ):
                return True
        elif normalized == pattern or normalized.startswith(pattern + "/"):
            return True
    return False


def existing_dirs(patterns, root: Path = None) -> list:
    """把（可能含萬用字元的）路徑樣式展開成實際存在的目錄。"""
    root = root or repo_root()
    found = []
    for pattern in patterns:
        if "*" in pattern or "?" in pattern:
            for candidate in sorted(root.glob(pattern)):
                if candidate.is_dir() and candidate not in found:
                    found.append(candidate)
        else:
            candidate = root / pattern
            if candidate.is_dir() and candidate not in found:
                found.append(candidate)
    return found
