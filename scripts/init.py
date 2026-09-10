#!/usr/bin/env python3
"""init.py — 拉下 harness-template 後的一鍵初始化入口

在此腳本之前，README.md「快速開始」要人工依序下三行指令
（machine-profile.py / service-scan.py / env-guard.py）才能完成初次設置，
容易漏做或漏看某一步的警告。這支腳本把三者合併成一個入口，
方便 `git clone`/`git pull` 之後直接執行。

用法：
    git clone <repo-url>
    cd <repo>
    python3 scripts/init.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
STEPS = [
    ("機器效能掃描", "machine-profile.py"),
    ("既有服務掃描", "service-scan.py"),
    ("環境指紋建立/比對", "env-guard.py"),
]


def run_step(title: str, script_name: str) -> int:
    script = SCRIPTS_DIR / script_name
    print(f"\n>>> {title}（{script_name}）")
    if not script.exists():
        print(f"找不到 {script}，略過此步驟。", file=sys.stderr)
        return 0
    result = subprocess.run([sys.executable, str(script)])
    return result.returncode


def main() -> int:
    print("===== harness-template 初始化 =====")
    print(f"Python：{sys.version.split()[0]}（{sys.executable}）")

    env_mismatch = False
    for title, script_name in STEPS:
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
