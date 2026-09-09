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

SCRIPTS_DIR = Path(__file__).resolve().parent
STEPS = [
    ("機器效能掃描", "machine-profile.py"),
    ("既有服務掃描", "service-scan.py"),
    ("環境指紋建立/比對", "env-guard.py"),
]


def run_step(title: str, script_name: str) -> None:
    script = SCRIPTS_DIR / script_name
    print(f"\n>>> {title}（{script_name}）")
    if not script.exists():
        print(f"找不到 {script}，略過此步驟。", file=sys.stderr)
        return
    subprocess.run([sys.executable, str(script)])


def main() -> int:
    print("===== harness-template 初始化 =====")
    print(f"Python：{sys.version.split()[0]}（{sys.executable}）")

    for title, script_name in STEPS:
        run_step(title, script_name)

    print("\n===== 初始化完成 =====")
    print("接下來：")
    print("  - 在 Claude Code 打開這個專案目錄")
    print("  - 用 Orchestrator（.claude/agents/orchestrator.md）或 `/task-plan`")
    print("    開始拆解你的需求")
    print("  - 若上面「環境指紋建立/比對」回報「指紋不符」，先確認是否為刻意")
    print("    更換的機器，再決定是否要更新 .harness/env-fingerprint.json 的基準值")
    print("    （不會自動覆寫，需要人工/Orchestrator 確認）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
