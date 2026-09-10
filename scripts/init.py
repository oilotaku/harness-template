#!/usr/bin/env python3
"""init.py — 拉下 harness-template 後的一鍵初始化入口

在此腳本之前，README.md「快速開始」要人工依序下三行指令
（machine-profile.py / service-scan.py / env-guard.py）才能完成初次設置，
容易漏做或漏看某一步的警告。這支腳本把三者合併成一個入口，
方便 `git clone`/`git pull` 之後直接執行。

2026-09-10（P3-2）：新增 `--json`，把三支腳本的機器可讀輸出合併成一份文件，
讓 Orchestrator 一次拿到全部掃描結果，不必分三次讀中文散文。

用法：
    git clone <repo-url>
    cd <repo>
    python3 scripts/init.py
    python3 scripts/init.py --json    # 給 Orchestrator 讀（stdout 只有 JSON）
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
STEPS = [
    ("機器效能掃描", "machine-profile.py", "machine"),
    ("既有服務掃描", "service-scan.py", "services"),
    ("環境指紋建立/比對", "env-guard.py", "env_guard"),
]


def run_step(title: str, script_name: str) -> int:
    script = SCRIPTS_DIR / script_name
    print(f"\n>>> {title}（{script_name}）")
    if not script.exists():
        print(f"找不到 {script}，略過此步驟。", file=sys.stderr)
        return 0
    result = subprocess.run([sys.executable, str(script)])
    return result.returncode


def run_step_json(script_name: str) -> tuple:
    """以 --json 執行單一步驟，回傳 (解析後的物件, exit code)。

    解析失敗不會靜靜略過——那正是「JSON 模式卻混進了人看的輸出」這種
    最難察覺的退化，所以把原始輸出原封不動放進 `error` 欄位讓它看得見。
    """
    script = SCRIPTS_DIR / script_name
    if not script.exists():
        return {"error": f"找不到 {script_name}"}, 0
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return json.loads(result.stdout), result.returncode
    except (json.JSONDecodeError, ValueError):
        return {
            "error": f"{script_name} --json 的輸出不是合法 JSON",
            "stdout": result.stdout,
            "stderr": result.stderr,
        }, result.returncode


def main_json() -> int:
    document = {"schema": 1, "status": "ok", "sections": {}}
    env_mismatch = False
    for _title, script_name, key in STEPS:
        payload, returncode = run_step_json(script_name)
        document["sections"][key] = payload
        if script_name == "env-guard.py" and returncode != 0:
            env_mismatch = True

    if env_mismatch:
        document["status"] = "env_mismatch"
    print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if env_mismatch else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="harness-template 一鍵初始化")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀的 JSON（stdout 只有 JSON）")
    args = parser.parse_args()

    if args.json:
        return main_json()

    print("===== harness-template 初始化 =====")
    print(f"Python：{sys.version.split()[0]}（{sys.executable}）")

    env_mismatch = False
    for title, script_name, _key in STEPS:
        returncode = run_step(title, script_name)
        if script_name == "env-guard.py" and returncode != 0:
            env_mismatch = True

    if env_mismatch:
        # 不要在指紋不符的情況下仍然印出「初始化完成」——那會讓人誤以為
        # 一切正常，直接略過上面 env-guard.py 已經印出的警告。
        print("\n===== 初始化未完全通過：環境指紋不符 =====")
        print("上面「環境指紋建立/比對」回報這台機器跟先前記錄的不一致。")
        print("依 CLAUDE.md 黃金法則第 6 條，請先確認：")
        print("  1) 這是否為刻意更換的新機器/遠端伺服器？")
        print("  2) 若是，這台機器的資源限制、既有服務、用途為何？")
        print("  3) 是否要把目前環境更新為新的基準指紋（更新 .harness/env-fingerprint.json）？")
        print("確認後再繼續下一步，不要直接忽略這個警告。")
        return 1

    print("\n===== 初始化完成 =====")
    print("接下來：")
    print("  - 在 Claude Code 打開這個專案目錄")
    print("  - 用 Orchestrator（.claude/agents/orchestrator.md）或 `/task-plan`")
    print("    開始拆解你的需求")
    return 0


if __name__ == "__main__":
    sys.exit(main())
