#!/usr/bin/env python3
"""guard-selfcheck.py — SessionStart hook：確認防作弊機制這次真的有生效

對應 docs/improvement-suggestions.md 的 P0-5。

`guard-hidden-tests.py` 已經改成 fail-closed（自己出錯時擋下操作），但那只
救得了「腳本有被執行到」的情況。如果 hook 根本沒被執行——找不到 python3、
hook 設定被改掉或刪掉、路徑寫錯、腳本檔案不存在——Claude Code 只會得到一個
非 2 的 exit code，把它當成 non-blocking error，工具照常執行，
而**使用者不會看到任何訊號**，會以為隱藏測試一直有被保護。

本腳本在每個 session 開始時，用幾個「已知應該被擋」與「已知應該放行」的
payload 實際跑一次 guard，把結果印出來。SessionStart hook 的 stdout 會進入
session 的上下文，所以防護一旦失效，Orchestrator 與使用者第一時間就看得到。

本腳本一律以 exit code 0 結束——它的任務是「回報」，不是「阻止 session 開始」。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def _repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


REPO_ROOT = _repo_root()
GUARD = REPO_ROOT / "scripts" / "guard-hidden-tests.py"
VERIFY_LOCKS = REPO_ROOT / "scripts" / "verify-locks.py"

HIDDEN_PROBE_REL = "tests/hidden/__selfcheck_probe__.py"

# (說明, payload 或 None 代表送出不合法的 stdin, 期望的 exit code)
PROBES = [
    (
        "Read 相對路徑讀隱藏測試",
        {"tool_name": "Read", "tool_input": {"file_path": HIDDEN_PROBE_REL}},
        2,
    ),
    (
        "Read 絕對路徑讀隱藏測試",
        {"tool_name": "Read", "tool_input": {"file_path": str(REPO_ROOT / HIDDEN_PROBE_REL)}},
        2,
    ),
    (
        "Bash cat 隱藏測試",
        {"tool_name": "Bash", "tool_input": {"command": f"cat {HIDDEN_PROBE_REL}"}},
        2,
    ),
    (
        "Edit 修改隱藏測試",
        {"tool_name": "Edit", "tool_input": {"file_path": HIDDEN_PROBE_REL}},
        2,
    ),
    (
        "無法解析的輸入應 fail-closed",
        None,
        2,
    ),
    (
        "讀取一般檔案不應被誤擋",
        {"tool_name": "Read", "tool_input": {"file_path": "README.md"}},
        0,
    ),
]


def run_probe(payload) -> int:
    stdin = "這不是合法 JSON" if payload is None else json.dumps(payload)
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
    )
    return result.returncode


def check_locks() -> None:
    if not VERIFY_LOCKS.exists():
        return
    result = subprocess.run(
        [sys.executable, str(VERIFY_LOCKS)],
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
    )
    if result.returncode == 0:
        print("公開測試鎖定稽核：正常（雜湊與鎖定當下一致）。")
    elif result.returncode == 1:
        print("⚠️ 公開測試鎖定稽核：**偵測到竄改或檔案遺失**，完整輸出如下：")
        print(result.stdout.strip())
    else:
        print("公開測試鎖定稽核：目前沒有可驗證的鎖定清單（尚未執行 lock-tests.py，屬正常起始狀態）。")


def main() -> int:
    print("===== 防作弊機制自我檢查（SessionStart）=====")

    if not GUARD.exists():
        print(f"⚠️ 找不到 {GUARD}——隱藏測試防護目前**完全沒有生效**。")
        print("在修好之前，不要把任何 task 交給 implementer，隱藏測試等同公開。")
        print("===== 結束 =====")
        return 0

    failures = []
    for name, payload, expected in PROBES:
        actual = run_probe(payload)
        if actual != expected:
            failures.append(f"{name}（預期 exit {expected}，實際 {actual}）")

    if failures:
        print(f"⚠️ 防護未如預期運作，{len(failures)}/{len(PROBES)} 項檢查失敗：")
        for item in failures:
            print(f"  - {item}")
        print("這代表 tests/hidden/ 目前可能讀得到或改得到。")
        print("請先執行 python3 scripts/test-guards.py 找出原因，修好之前不要派工給 implementer。")
    else:
        print(f"防護正常：{len(PROBES)}/{len(PROBES)} 項檢查符合預期（隱藏測試讀寫皆被擋、一般檔案不受影響）。")

    check_locks()
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
