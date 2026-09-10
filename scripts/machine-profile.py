#!/usr/bin/env python3
"""machine-profile.py — 跨平台機器效能掃描（取代 bash 版本，Win/Linux/Mac 通用）
對應需求第 12 點：必須根據實際機器效能規劃任務

2026-09-10（對應 docs/history/improvement-suggestions.md 的 P3-1）：事實蒐集的部分
（記憶體、GPU、容器判斷）抽到 `scripts/machine_facts.py`，因為 `env-guard.py`
也需要同一批事實來判斷「這是不是同一台機器」。

2026-09-10（P3-2）：新增 `--json`。在此之前這支腳本只印中文散文，
Orchestrator 得靠讀自然語言心算平行度，同一台機器可能每次判出不同數字。
換算邏輯現在在 `scripts/capacity.py`（純函式、可單元測試），
`--json` 直接輸出數字；兩種模式都會把結果寫進 `.harness/last-scan.json`。

用法：
    python3 scripts/machine-profile.py           # 給人看
    python3 scripts/machine-profile.py --json    # 給 Orchestrator 讀（stdout 只有 JSON）
"""
import argparse
import json
import platform
import shutil
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capacity  # noqa: E402
import env_fingerprint  # noqa: E402
import machine_facts  # noqa: E402
import scan_cache  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SECTION = "machine"


def collect() -> dict:
    cpu = machine_facts.cpu_count()
    mem_mb = machine_facts.memory_mb()
    total, _used, free = shutil.disk_usage(".")

    report = {
        "scan": SECTION,
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "cpu_count": cpu,
        "cpu_bucket": env_fingerprint.cpu_bucket(cpu),
        "memory_mb": mem_mb,
        "memory_bucket": env_fingerprint.memory_bucket(mem_mb),
        "disk_free_gb": free // (1024 ** 3),
        "disk_total_gb": total // (1024 ** 3),
        "gpu": machine_facts.gpu_info() or None,
        "container": machine_facts.in_container(),
    }
    report.update(capacity.parallel_limits(cpu, mem_mb))
    return report


def print_human(report: dict) -> None:
    print("===== 機器效能掃描 =====")
    print(f"作業系統：{report['os']} {report['os_release']}")
    print(f"主機名稱：{report['hostname']}")
    print(f"CPU 核心數：{report['cpu_count'] or '未知'}（級距：{report['cpu_bucket']}）")

    if report["memory_mb"]:
        print(f"總記憶體：{report['memory_mb']} MB（級距：{report['memory_bucket']}）")
    else:
        print("總記憶體：未知（可執行 pip install psutil 取得更準確、更跨平台的數字）")

    print(
        f"磁碟可用空間（目前目錄所在磁區）：{report['disk_free_gb']} GB 可用 / "
        f"共 {report['disk_total_gb']} GB"
    )
    print(f"GPU：{report['gpu']}" if report["gpu"] else "GPU：未偵測到（或本腳本尚不支援辨識此裝置的廠牌）")
    print(f"是否在容器環境中：{machine_facts.container_hint()}")

    print()
    print(f"這台機器最多撐得住的平行子智能體數：{report['max_parallel_agents']}"
          f"（重型任務：{report['max_parallel_heavy_tasks']}，依據：{report['limited_by']}）")
    print("注意：那是「機器容量上限」，不是建議值。預設請序列執行（一次一個 task），")
    print("要開平行必須有明確理由——在訂閱制方案下平行度會等倍放大用量視窗的")
    print("消耗速率，見 docs/token-strategy.md §3.4。")
    print("===== 掃描結束 =====")


def main() -> int:
    parser = argparse.ArgumentParser(description="機器效能掃描")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀的 JSON（stdout 只有 JSON）")
    args = parser.parse_args()

    report = collect()
    scan_cache.write(SECTION, report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
