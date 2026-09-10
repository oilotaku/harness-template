#!/usr/bin/env python3
"""machine-profile.py — 跨平台機器效能掃描（取代 bash 版本，Win/Linux/Mac 通用）
對應需求第 12 點：必須根據實際機器效能規劃任務

2026-09-10（對應 docs/improvement-suggestions.md 的 P3-1）：事實蒐集的部分
（記憶體、GPU、容器判斷）抽到 `scripts/machine_facts.py`，因為 `env-guard.py`
也需要同一批事實來判斷「這是不是同一台機器」。本腳本現在只負責排版印給人看。
"""
import platform
import shutil
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_fingerprint  # noqa: E402
import machine_facts  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def main():
    print("===== 機器效能掃描 =====")
    print(f"作業系統：{platform.system()} {platform.release()}")
    print(f"主機名稱：{socket.gethostname()}")

    cpu = machine_facts.cpu_count()
    print(f"CPU 核心數：{cpu or '未知'}（級距：{env_fingerprint.cpu_bucket(cpu)}）")

    mem_mb = machine_facts.memory_mb()
    if mem_mb:
        print(f"總記憶體：{mem_mb} MB（級距：{env_fingerprint.memory_bucket(mem_mb)}）")
    else:
        print("總記憶體：未知（可執行 pip install psutil 取得更準確、更跨平台的數字）")

    total, _used, free = shutil.disk_usage(".")
    print(
        f"磁碟可用空間（目前目錄所在磁區）：{free // (1024 ** 3)} GB 可用 / "
        f"共 {total // (1024 ** 3)} GB"
    )

    gpu = machine_facts.gpu_info()
    print(f"GPU：{gpu}" if gpu else "GPU：未偵測到（或本腳本尚不支援辨識此裝置的廠牌）")

    print(f"是否在容器環境中：{machine_facts.container_hint()}")

    print()
    print("建議：Orchestrator 應依 CPU 核心數與記憶體大小，決定同時可派出的")
    print("平行子智能體/建置行程數量（經驗法則：平行數 <= CPU 核心數，")
    print("且每個重型任務預留至少 1-2GB 記憶體餘裕）。")
    print("===== 掃描結束 =====")


if __name__ == "__main__":
    main()
