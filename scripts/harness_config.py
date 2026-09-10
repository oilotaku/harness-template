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
  "hidden_test_command": "{python} -m unittest discover -s {dir} -p 'test_*.py' -v"
}
```

沒有這個檔案時退回上面那組預設值（Python 專案不必特別設定）。
路徑支援 `*` 萬用字元（例如 monorepo 的 `packages/*/tests/hidden`）。

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


def load(root: Path = None) -> dict:
    """讀取設定；沒有設定檔時回傳預設值。設定檔有問題時丟 ConfigError。"""
    root = root or repo_root()
    path = config_path(root)

    config = {
        "public_test_paths": list(DEFAULT_PUBLIC_TEST_PATHS),
        "hidden_test_paths": list(DEFAULT_HIDDEN_TEST_PATHS),
        "hidden_test_command": DEFAULT_HIDDEN_TEST_COMMAND,
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

    known = set(PATH_KEYS) | {"hidden_test_command"}
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
