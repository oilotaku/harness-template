#!/usr/bin/env python3
"""guard-hidden-tests.py — PreToolUse hook（取代 bash 版本，Win/Linux/Mac 通用）
阻擋任何對 tests/hidden/ 或已鎖定公開測試的寫入操作，強制實作者/檢驗者分離（需求第 8 點）。

Claude Code 會把這次工具呼叫的資訊以 JSON 透過 stdin 傳入本腳本，
Edit/Write 等工具的目標路徑在 tool_input.file_path。
"""
import json
import sys
from pathlib import Path

LOCKED_LIST = Path(".harness/locked-tests.list")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        raw_path = payload.get("tool_input", {}).get("file_path")
    except (json.JSONDecodeError, AttributeError):
        raw_path = None

    if not raw_path:
        # 沒有指定路徑，視為不適用本檢查，放行
        sys.exit(0)

    # 統一用正斜線比對，避免 Windows 反斜線路徑造成誤判
    target = raw_path.replace("\\", "/")

    if target.startswith("tests/hidden/"):
        print(
            "拒絕：實作者子智能體不可存取或修改 tests/hidden/ 目錄（隱藏驗收測試）。",
            file=sys.stderr,
        )
        # exit code 2 才會被 Claude Code 視為 blocking error 並真正擋下工具呼叫；
        # exit code 1 只是 non-blocking error，動作仍會繼續執行。
        sys.exit(2)

    if LOCKED_LIST.exists():
        locked = {
            line.strip().replace("\\", "/")
            for line in LOCKED_LIST.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        if target in locked:
            print(
                f"拒絕：「{target}」已被檢驗者鎖定為公開測試，實作者不可修改。",
                file=sys.stderr,
            )
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
