#!/usr/bin/env python3
"""utf8_output.py — 讓腳本在 Windows 主控台也印得出繁體中文

## 為什麼需要這個

本模板所有腳本的輸出都是繁體中文，而 Python 在 Windows 上預設用系統的
ANSI 代碼頁（英文語系是 cp1252、繁中語系是 cp950）編碼 stdout/stderr。
只要遇到編不出來的字，`print()` 就直接丟 `UnicodeEncodeError` 讓整支腳本崩潰。

這個 bug 一直存在，只是從來沒被發現——README 與各腳本的模組說明都寫著
「Win/Linux/Mac 通用」，但在 2026-09-10 補上 CI 之前，**沒有任何一次實際在
Windows 上跑過**。CI 一開，兩個 Windows job 立刻紅，錯誤是
`'charmap' codec can't encode characters`。這正是 P3-3（加 CI）的價值。

最危險的是 `guard-hidden-tests.py`：它被擋下時要印出理由再 exit 2，如果 print
先崩潰，exit code 會變成 1——而 Claude Code 只把 exit 2 當成 blocking error，
exit 1 是 non-blocking，**工具照樣執行**。換句話說，在 Windows 上「擋下」會
悄悄變成「放行」。所以那支腳本不 import 這個模組，而是自己內嵌同樣的邏輯，
避免多一個失敗點（細節見該腳本的模組說明）。

## 為什麼不叫使用者設 PYTHONUTF8=1

那是把責任推給使用者，而且忘了設就會回到同樣的崩潰。腳本自己保證自己印得出來，
才是正確的預設。
"""
import sys


def enable() -> None:
    """把 stdout/stderr 切成 UTF-8。在任何 print 之前呼叫。

    `reconfigure()` 是 Python 3.7+ 的 TextIOWrapper 方法；若 stdout 被替換成
    沒有這個方法的物件（少數測試框架會這樣做），就靜靜略過——那種情況下
    輸出通常本來就被接管了，不該讓腳本因此崩潰。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            # errors="replace"：萬一連 UTF-8 都設定不起來的極端環境，
            # 也寧可印出替代字元，不要讓腳本掛掉。
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
