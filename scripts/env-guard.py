#!/usr/bin/env python3
"""env-guard.py — 記錄並比對「預期執行環境」的指紋（取代 bash 版本，Win/Linux/Mac 通用）
對應需求第 14 點：若明確告知要在其他機器運行，須向使用者確認相關資訊

設計原則：本模板不預設一定跑在哪台機器，而是「第一次執行時記錄指紋、
之後每次執行都比對」。指紋不符時不會自動判斷對錯，只負責清楚回報，
交由 Orchestrator / 使用者決定；但仍以非 0 的 exit code 表示「有不符」，
讓呼叫方（例如 scripts/init.py）能明確分辨，不用去猜文字輸出。

2026-09-09 code review 後修正：新增 arch 欄位比對時，沒有處理「舊版指紋檔
沒有 arch 欄位」的情況——若不特別處理，每次都會被誤判成「架構不符」，
即使機器根本沒換過。現在缺欄位視為「需要補齊」而不是「不符」，
補齊後直接覆寫回檔案，不當一次真正的環境變更事件。
"""
import json
import platform
import socket
import sys
from pathlib import Path

FINGERPRINT_DIR = Path(".harness")
FINGERPRINT_FILE = FINGERPRINT_DIR / "env-fingerprint.json"


def current_fingerprint() -> dict:
    return {
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "arch": platform.machine(),
    }


def main() -> int:
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
        return 0

    recorded = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8"))
    mismatches = []
    upgraded_fields = []
    for key, label in (("hostname", "主機名稱"), ("os", "作業系統"), ("arch", "架構")):
        if key not in recorded:
            # 舊版指紋檔沒有這個欄位，視為「需要補齊」，不算不符
            # （沒有紀錄過的東西無從比對起）。
            upgraded_fields.append(key)
            continue
        if recorded.get(key) != current.get(key):
            mismatches.append(
                f"⚠️ {label}不符：記錄為「{recorded.get(key)}」，目前為「{current.get(key)}」"
            )

    if upgraded_fields:
        recorded.update({key: current[key] for key in upgraded_fields})
        FINGERPRINT_FILE.write_text(
            json.dumps(recorded, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        labels = "、".join(upgraded_fields)
        print(f"（偵測到舊版指紋檔缺少欄位：{labels}，已自動補上目前值，不視為不符）")

    if mismatches:
        for m in mismatches:
            print(m)
        print()
        print("狀態：不符 —— 這台機器可能跟先前規劃時不同。")
        print("請 Orchestrator 停止自動派工，向使用者確認：")
        print("  1) 這是否為刻意換到的新機器／遠端伺服器？")
        print("  2) 若是，這台機器的資源限制、既有服務、用途為何？")
        print(f"  3) 是否要把目前環境更新為新的基準指紋（更新 {FINGERPRINT_FILE}）？")
        print("===== 結束 =====")
        return 1

    print("狀態：正常（與記錄的預期環境一致）")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
