#!/usr/bin/env python3
"""guard-hidden-tests.py — PreToolUse hook（取代 bash 版本，Win/Linux/Mac 通用）
阻擋任何對 tests/hidden/ 或已鎖定公開測試的寫入操作，強制實作者/檢驗者分離（需求第 8 點）。

涵蓋兩種攻擊面：
1. Edit/Write（或其他帶 file_path 的工具）直接指定目標路徑寫入受保護路徑。
2. Bash 工具間接下指令（rm/mv/cp/sed -i/tee/重導向等）寫入受保護路徑——
   這是本腳本原本只擋 Edit/Write 時最大的漏洞：implementer 仍握有 Bash 工具，
   可以完全繞過路徑層級的保護（例如 `rm tests/hidden/x.py`）。

同時保護 `.harness/` 目錄本身：這裡的 locked-tests.list、env-fingerprint.json
是治理檔案，只該由 lock-tests.py / env-guard.py 這些腳本寫入，不該被一般
Edit/Write/Bash 動作竄改（例如把 locked-tests.list 的條目刪掉，藉此解鎖
已鎖定的公開測試）。

注意：Bash 指令偵測是關鍵字/子字串比對，不是完整的 shell parser，無法防住
所有刻意規避的寫法（例如用 base64 編碼路徑再解碼），目的是提高作弊成本、
擋掉常見繞過手法，而不是做到密不透風。另外，本腳本目前只擋「寫入」，
不擋「讀取」（例如 `cat tests/hidden/x.py`）——用 Bash 直接讀取隱藏測試內容
目前仍是已知殘留風險，不在本次修補範圍內。

Claude Code 會把這次工具呼叫的資訊以 JSON 透過 stdin 傳入本腳本。
Edit/Write 等工具的目標路徑在 tool_input.file_path；
Bash 工具的指令字串在 tool_input.command。
"""
import json
import re
import sys
from pathlib import Path

LOCKED_LIST = Path(".harness/locked-tests.list")

PROTECTED_PREFIXES = ("tests/hidden/", ".harness/")

# 常見「寫入類」bash 操作的關鍵字/樣式（粗略比對，見上方模組說明的限制）
WRITE_OP_PATTERN = re.compile(
    r"""
    \brm\b |              # rm / rm -rf ...
    \bmv\b |              # mv ... dest
    \bcp\b |              # cp ... dest
    \bsed\b[^\n]*-i\b |   # sed -i / --in-place
    \btee\b |             # tee dest
    \btruncate\b |        # truncate
    >{1,2} |              # > 或 >> 重導向
    \bdd\b[^\n]*\bof= |   # dd of=...
    \bgit\s+rm\b          # git rm
    """,
    re.VERBOSE,
)


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _load_locked() -> set:
    if not LOCKED_LIST.exists():
        return set()
    return {
        line.strip().replace("\\", "/")
        for line in LOCKED_LIST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _check_file_path(raw_path: str, locked: set):
    target = _normalize(raw_path)

    if target.startswith("tests/hidden/"):
        return "拒絕：實作者子智能體不可存取或修改 tests/hidden/ 目錄（隱藏驗收測試）。"

    if target.startswith(".harness/"):
        return (
            f"拒絕：「{target}」屬於 harness 治理檔案（鎖定清單/環境指紋），"
            "不可由一般編輯動作寫入，只能由對應腳本（lock-tests.py / env-guard.py）產生。"
        )

    if target in locked:
        return f"拒絕：「{target}」已被檢驗者鎖定為公開測試，實作者不可修改。"

    return None


def _check_bash_command(command: str, locked: set):
    normalized = _normalize(command)

    mentions_protected = any(prefix in normalized for prefix in PROTECTED_PREFIXES)
    mentions_locked = any(path in normalized for path in locked)
    if not (mentions_protected or mentions_locked):
        return None

    if not WRITE_OP_PATTERN.search(normalized):
        return None

    return (
        "拒絕：偵測到 Bash 指令疑似對受保護路徑（tests/hidden/、.harness/ 或已鎖定的"
        "公開測試）執行寫入類操作，一律擋下。若為誤判，請改用不涉及這些路徑的方式完成，"
        "或請 Orchestrator / verifier 協助處理。"
    )


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        # 讀不到合法 payload，視為不適用本檢查，放行（fail open：本腳本只負責
        # 「已知格式」的攔截，不應該因為解析失敗就把所有工具呼叫都擋住）。
        sys.exit(0)

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    locked = _load_locked()

    reason = None
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and command:
            reason = _check_bash_command(command, locked)
    else:
        raw_path = tool_input.get("file_path")
        if isinstance(raw_path, str) and raw_path:
            reason = _check_file_path(raw_path, locked)

    if reason:
        print(reason, file=sys.stderr)
        # exit code 2 才會被 Claude Code 視為 blocking error 並真正擋下工具呼叫；
        # exit code 1 只是 non-blocking error，動作仍會繼續執行。
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
