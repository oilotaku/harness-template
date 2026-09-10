#!/usr/bin/env python3
"""env-guard.py — 記錄並比對「預期執行環境」的指紋（Win/Linux/Mac 通用）
對應需求第 14 點：若明確告知要在其他機器運行，須向使用者確認相關資訊

設計原則：本模板不預設一定跑在哪台機器，而是「第一次執行時記錄指紋、
之後每次執行都比對」。指紋不符時不會自動判斷對錯，只負責清楚回報，
交由 Orchestrator / 使用者決定；但仍以非 0 的 exit code 表示「有不符」，
讓呼叫方（例如 scripts/init.py）能明確分辨，不用去猜文字輸出。

2026-09-09 code review 後修正：新增 arch 欄位比對時，沒有處理「舊版指紋檔
沒有 arch 欄位」的情況——若不特別處理，每次都會被誤判成「架構不符」，
即使機器根本沒換過。現在缺欄位視為「需要補齊」而不是「不符」，
補齊後直接覆寫回檔案，不當一次真正的環境變更事件。

2026-09-10（對應 docs/improvement-suggestions.md 的 P3-1）：舊版指紋只有
hostname/os/arch，而容器與雲端執行環境每次啟動 hostname 都是隨機的，於是這支
腳本每次都回報「不符」。一個總是誤報的守門機制比沒有更糟——它會訓練使用者
忽略警告。現在改成：偵測到容器環境時 hostname 不參與比對，改以「能力類」欄位
（os/arch/是否容器/CPU 級距/記憶體級距）判斷是不是同一台機器。
比對規則的完整理由見 scripts/env_fingerprint.py。

同一版新增 `--update`：確認過確實換了機器之後，用一行指令把目前環境設為新基準。
在此之前只能手動改 .harness/env-fingerprint.json，但那個檔案被 guard hook 擋著
不給編輯，等於沒有正當管道。

用法：
    python3 scripts/env-guard.py            # 比對（沒有基準時建立）
    python3 scripts/env-guard.py --update   # 把目前環境設為新的基準指紋

exit code：
    0 = 相符（或首次建立基準、或剛完成 --update）
    1 = 不符 —— Orchestrator 應停止派工並向使用者確認
"""
import argparse
import json
import os
import platform
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_fingerprint  # noqa: E402
import machine_facts  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def _repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


REPO_ROOT = _repo_root()
FINGERPRINT_FILE = REPO_ROOT / ".harness" / "env-fingerprint.json"


def current_fingerprint() -> dict:
    return env_fingerprint.build(
        hostname=socket.gethostname(),
        os_name=platform.system(),
        arch=platform.machine(),
        container=machine_facts.in_container(),
        cpu_count=machine_facts.cpu_count(),
        memory_mb=machine_facts.memory_mb(),
        hostname_stable=machine_facts.hostname_is_stable(),
    )


def save(fingerprint: dict) -> None:
    FINGERPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    FINGERPRINT_FILE.write_text(
        json.dumps(fingerprint, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def print_fingerprint(fingerprint: dict, indent: str = "  ") -> None:
    for line in env_fingerprint.describe(fingerprint):
        print(f"{indent}{line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="記錄並比對預期執行環境的指紋")
    parser.add_argument(
        "--update",
        action="store_true",
        help="把目前環境設為新的基準指紋（確認過確實換了機器之後才用）",
    )
    args = parser.parse_args()

    print("===== 環境指紋守門 =====")
    current = current_fingerprint()

    if args.update and FINGERPRINT_FILE.exists():
        previous = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8"))
        save(current)
        print("已把目前環境設為新的基準指紋。")
        print("原本記錄的環境：")
        print_fingerprint(previous)
        print("新的基準：")
        print_fingerprint(current)
        print()
        print("提醒：換機器通常也代表可用資源、既有服務、連接埠都不一樣了，")
        print("請一併重新執行 scripts/machine-profile.py 與 scripts/service-scan.py。")
        print("===== 結束 =====")
        return 0

    if not FINGERPRINT_FILE.exists():
        save(current)
        print("尚未記錄過預期執行環境，已將目前環境設為基準：")
        print_fingerprint(current)
        if not current["hostname_stable"]:
            print()
            print("（偵測到主機名稱不穩定的執行環境（容器／K8s／Codespaces／CI）：")
            print("  主機名稱每次重建都會變，因此之後不列入比對，")
            print("  改以作業系統／架構／CPU 與記憶體級距判斷是不是同一種執行環境）")
        print("狀態：正常（首次執行）")
        print("===== 結束 =====")
        return 0

    recorded = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8"))
    mismatches, missing, skipped = env_fingerprint.compare(recorded, current)

    if missing:
        # 舊版指紋檔沒有這些欄位，視為「需要補齊」而不是「不符」——
        # 沒有紀錄過的東西無從比對起。
        recorded.update({key: current[key] for key in missing})
        save(recorded)
        labels = "、".join(env_fingerprint.FIELD_LABELS.get(key, key) for key in missing)
        print(f"（偵測到舊版指紋檔缺少欄位：{labels}，已自動補上目前值，不視為不符）")

    for _key, reason in skipped:
        print(f"（{reason}）")

    if mismatches:
        for _key, label, before, after in mismatches:
            print(f"⚠️ {label}不符：記錄為「{before}」，目前為「{after}」")
        print()
        print("狀態：不符 —— 這台機器可能跟先前規劃時不同。")
        print("請 Orchestrator 停止自動派工，向使用者確認：")
        print("  1) 這是否為刻意換到的新機器／遠端伺服器？")
        print("  2) 若是，這台機器的資源限制、既有服務、用途為何？")
        print("  3) 確認後執行 `python3 scripts/env-guard.py --update` 更新基準指紋。")
        print("===== 結束 =====")
        return 1

    print("狀態：正常（與記錄的預期環境一致）")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
