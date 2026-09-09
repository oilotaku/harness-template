#!/usr/bin/env python3
"""env-guard.py — 記錄並比對「預期執行環境」的指紋（取代 bash 版本，Win/Linux/Mac 通用）
對應需求第 14 點：若明確告知要在其他機器運行，須向使用者確認相關資訊

設計原則：本模板不預設一定跑在哪台機器，而是「第一次執行時記錄指紋、
之後每次執行都比對」。指紋不符時不會自動判斷對錯，只負責清楚回報，
交由 Orchestrator / 使用者決定。
"""
import json
import platform
import socket
from pathlib import Path

FINGERPRINT_DIR = Path(".harness")
FINGERPRINT_FILE = FINGERPRINT_DIR / "env-fingerprint.json"


def current_fingerprint() -> dict:
    return {
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "arch": platform.machine(),
    }


def main() -> None:
    print("===== 環境指紋守門 =====")
    FINGERPRINT_DIR.mkdir(exist_ok=True)
    current = current_fingerprint()

    if not FINGERPRINT_FILE.exists():
        FINGERPRINT_FILE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("尚未記錄過預期執行環境，已將目前環境設為基準：")
        print(f"  主機名稱：{current['hostname']}")
        print(f"  作業系統：{current['os']}")
        print(f"  架構：{current['arch']}")
        print("狀態：正常（首次執行）")
        print("===== 結束 =====")
        return

    recorded = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8"))
    mismatches = []
    for key, label in (("hostname", "主機名稱"), ("os", "作業系統")):
        if recorded.get(key) != current.get(key):
            mismatches.append(
                f"⚠️ {label}不符：記錄為「{recorded.get(key)}」，目前為「{current.get(key)}」"
            )

    if mismatches:
        for m in mismatches:
            print(m)
        print()
        print("狀態：不符 —— 這台機器可能跟先前規劃時不同。")
        print("請 Orchestrator 停止自動派工，向使用者確認：")
        print("  1) 這是否為刻意換到的新機器／遠端伺服器？")
        print("  2) 若是，這台機器的資源限制、既有服務、用途為何？")
        print(f"  3) 是否要把目前環境更新為新的基準指紋（更新 {FINGERPRINT_FILE}）？")
    else:
        print("狀態：正常（與記錄的預期環境一致）")
    print("===== 結束 =====")


if __name__ == "__main__":
    main()
