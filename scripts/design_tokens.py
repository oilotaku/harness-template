#!/usr/bin/env python3
"""design_tokens.py — 讀取與驗證專案的設計 token（前端／GUI 共用）

由 `scripts/check-design-tokens.py` 使用；`implementer-frontend` 依這份 token
決定顏色、間距、字級，而不是每個 task 自己發明一套。

## 為什麼 token 要「語意化」而不是「色階化」

命名成 `gray-900` / `blue-500` 的 token 綁死了一件事：**它長什麼樣子**。
換成深色主題、換成另一個技術棧，那個名字就開始說謊——深色主題裡的 `gray-900`
是背景不是文字，而 Flutter 的 `ColorScheme` 裡根本沒有色階這種東西。

所以這裡用的是**角色**命名：`surface`（畫面底色）、`on-surface`（畫在它上面的文字）、
`primary`、`on-primary`、`danger`……。這個命名法跨得了技術棧，因為每個 GUI 框架
都有「背景/前景配對」這個概念：

| 技術棧 | 對應 |
|---|---|
| Web／CSS | custom properties（`--color-surface`） |
| Flutter | `ColorScheme`（`surface` / `onSurface` / `primary` / `onPrimary`——名字幾乎一樣） |
| SwiftUI | Asset catalog 的具名顏色 + `Color("surface")` |
| Qt | palette 的 `Window` / `WindowText` / `Highlight` |
| WPF | `ResourceDictionary` 的具名 brush |

也因此**明暗兩套用同一組名字**：切主題換的是值，不是名字。程式碼裡不該出現
「如果是深色就改用另一個顏色」這種分支。

## 這個模組不做的事

不決定任何值好不好看。它只做兩件可以客觀判定的事：

1. **結構完整**：該有的角色都在、明暗兩套一致、值是合法的顏色
2. **對比度達標**：宣告成必須可讀的配對，實際算出來要過 WCAG AA

第 2 點是這整份東西唯一能機械化的部分，也正是最容易在人工檢查裡被漏掉的部分——
「可及性視為隱性驗收標準」原本只是一句自律規則，現在它算得出來。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402

DEFAULT_TOKENS_FILE = "design.tokens.json"

# 每個主題都必須具備的角色。少一個就會逼實作者自己發明一個顏色，
# 而那正是「每個 task 各有一套」的起點。
REQUIRED_ROLES = (
    "surface",          # 畫面底色
    "surface-muted",    # 次要區塊底色（卡片、輸入框）
    "on-surface",       # 畫在 surface 上的主要文字
    "on-surface-muted", # 次要文字（說明、placeholder）
    "border",           # 分隔線與外框
    "primary",          # 主要動作
    "on-primary",
    "danger",           # 破壞性動作與錯誤
    "on-danger",
    "success",
    "on-success",
    "warning",
    "on-warning",
    "focus",            # 鍵盤焦點指示
)

THEMES = ("light", "dark")

HEX_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# WCAG 2.1 AA。大字與非文字元件（外框、圖示、焦點框）門檻較低是標準本身的規定，
# 不是我們放水。
AA_TEXT = 4.5
AA_LARGE_TEXT = 3.0
AA_NON_TEXT = 3.0


class TokenError(Exception):
    """token 檔有問題。呼叫端要明確報錯——設計基準悄悄失效比沒有基準更糟。"""


# --------------------------------------------------------------- 顏色與對比度


def parse_hex(value: str):
    """`#rgb` / `#rrggbb` → (r, g, b)，各 0–255。"""
    if not isinstance(value, str) or not HEX_PATTERN.match(value.strip()):
        raise TokenError(f"「{value}」不是合法的十六進位顏色（要 #rgb 或 #rrggbb）。")
    digits = value.strip().lstrip("#")
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def relative_luminance(rgb) -> float:
    """WCAG 2.1 的相對亮度。"""
    channels = []
    for raw in rgb:
        c = raw / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float:
    """兩個顏色的對比度（1.0–21.0）。"""
    a = relative_luminance(parse_hex(foreground))
    b = relative_luminance(parse_hex(background))
    lighter, darker = (a, b) if a >= b else (b, a)
    return (lighter + 0.05) / (darker + 0.05)


# ------------------------------------------------------------------- 讀取設定


def settings_of(config: dict) -> dict:
    """從 harness.config.json 取出設計 token 的設定。

    `design: false` 代表這個專案沒有圖形介面，不檢查——CLI 與函式庫專案本來就
    不該被逼著維護一份用不到的色票。
    """
    raw = config.get("design")
    if raw is False:
        return {"enabled": False, "tokens": DEFAULT_TOKENS_FILE}
    if raw is None:
        raw = {}
    return {"enabled": True, "tokens": raw.get("tokens", DEFAULT_TOKENS_FILE)}


def load(root: Path, settings: dict) -> dict:
    path = root / settings["tokens"]
    if not path.is_file():
        raise TokenError(
            f"找不到設計 token「{settings['tokens']}」。"
            "沒有圖形介面的專案可以在 harness.config.json 設 `\"design\": false` 關掉檢查；"
            "有介面的話，少了這份基準，每個 task 都會自己發明一套顏色與間距。"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TokenError(f"「{settings['tokens']}」讀不到或不是合法 JSON：{exc}") from exc


# ------------------------------------------------------------------- 結構驗證


def _check_scale(data: dict, section: str, key: str, problems: list) -> None:
    block = data.get(section)
    if not isinstance(block, dict):
        problems.append(f"缺少 `{section}` 區塊（或它不是物件）。")
        return
    values = block.get(key)
    if not isinstance(values, list) or not values:
        problems.append(f"`{section}.{key}` 必須是非空陣列。")
        return
    if any(not isinstance(v, (int, float)) or v < 0 for v in values):
        problems.append(f"`{section}.{key}` 只能是非負數字。")
        return
    if list(values) != sorted(values):
        problems.append(f"`{section}.{key}` 必須由小到大排序（讓「大一階」有明確意義）。")
    if len(set(values)) != len(values):
        problems.append(f"`{section}.{key}` 有重複的值。")


def check_structure(data: dict) -> list:
    """回傳問題清單（空的代表結構沒問題）。"""
    problems = []

    colors = data.get("color")
    if not isinstance(colors, dict):
        problems.append("缺少 `color` 區塊（或它不是物件）。")
    else:
        for theme in THEMES:
            palette = colors.get(theme)
            if not isinstance(palette, dict):
                problems.append(f"缺少 `color.{theme}` 主題（明暗兩套都要有，見模組說明）。")
                continue
            for role in REQUIRED_ROLES:
                if role not in palette:
                    problems.append(f"`color.{theme}` 缺少角色 `{role}`。")
                    continue
                try:
                    parse_hex(palette[role])
                except TokenError as exc:
                    problems.append(f"`color.{theme}.{role}`：{exc}")
            extra = sorted(set(palette) - set(REQUIRED_ROLES))
            for role in extra:
                if not role.startswith("$"):
                    problems.append(
                        f"`color.{theme}` 有未定義的角色 `{role}`。"
                        "新增角色要兩個主題一起加，並想清楚它跟既有角色的差別——"
                        "角色一多就會退化成色階。"
                    )
        light, dark = colors.get("light"), colors.get("dark")
        if isinstance(light, dict) and isinstance(dark, dict):
            only_light = sorted(set(light) - set(dark))
            only_dark = sorted(set(dark) - set(light))
            if only_light or only_dark:
                problems.append(
                    f"明暗兩套的角色不一致（只在 light：{only_light}；只在 dark：{only_dark}）。"
                    "同一組名字才切得了主題。"
                )

    _check_scale(data, "spacing", "scale", problems)
    _check_scale(data, "typography", "scale", problems)

    pairs = data.get("contrast_pairs")
    if not isinstance(pairs, list) or not pairs:
        problems.append("缺少 `contrast_pairs`：沒有宣告哪些配對必須可讀，對比度就無從檢查。")
    else:
        for index, pair in enumerate(pairs):
            if not isinstance(pair, dict):
                problems.append(f"`contrast_pairs[{index}]` 必須是物件。")
                continue
            for field in ("foreground", "background"):
                if not isinstance(pair.get(field), str):
                    problems.append(f"`contrast_pairs[{index}]` 缺少 `{field}`。")
            level = pair.get("level", "text")
            if level not in ("text", "large-text", "non-text"):
                problems.append(
                    f"`contrast_pairs[{index}].level` 只能是 text / large-text / non-text，"
                    f"收到 {level!r}。"
                )
    return problems


# ------------------------------------------------------------------- 對比度


def threshold_for(level: str) -> float:
    return {"text": AA_TEXT, "large-text": AA_LARGE_TEXT, "non-text": AA_NON_TEXT}[level]


def check_contrast(data: dict) -> list:
    """算出每一組宣告的配對在明暗兩套下的對比度。回傳 [(主題, 配對, 比值, 門檻, 是否通過)]。"""
    results = []
    colors = data["color"]
    for theme in THEMES:
        palette = colors[theme]
        for pair in data["contrast_pairs"]:
            level = pair.get("level", "text")
            fg_role, bg_role = pair["foreground"], pair["background"]
            if fg_role not in palette or bg_role not in palette:
                continue  # 結構檢查已經報過了
            ratio = contrast_ratio(palette[fg_role], palette[bg_role])
            needed = threshold_for(level)
            results.append({
                "theme": theme,
                "foreground": fg_role,
                "background": bg_role,
                "level": level,
                "ratio": round(ratio, 2),
                "required": needed,
                "passed": ratio >= needed,
            })
    return results


def repo_root() -> Path:
    return harness_config.repo_root()
