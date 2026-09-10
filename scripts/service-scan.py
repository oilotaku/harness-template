#!/usr/bin/env python3
"""service-scan.py — 跨平台既有服務／連接埠掃描（取代 bash 版本，Win/Linux/Mac 通用）
對應需求第 13 點：必須根據實際機器已有服務檢查並避開服務

優先使用 psutil（`pip install psutil`）以取得三平台一致、準確的結果；
沒有安裝時退回各作業系統原生指令，盡力而為。

2026-09-10（P3-2）：新增 `--json`，並直接算出「建議使用的連接埠區間」，
Orchestrator 不必再自己讀中文清單挑埠號。

一個刻意的設計：**掃描不完整時不給建議區間**（`suggested_port_range` 為 null）。
沒有 psutil、或權限不足讀不到完整連線清單時，「沒看到」不等於「沒被佔用」，
給一個看起來很有把握的區間比不給更危險。`complete` 欄位就是這件事的旗標。

用法：
    python3 scripts/service-scan.py           # 給人看
    python3 scripts/service-scan.py --json    # 給 Orchestrator 讀（stdout 只有 JSON）
"""
import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capacity  # noqa: E402
import scan_cache  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SECTION = "services"

COMMON_SERVICES = [
    "postgres", "mysqld", "mariadbd", "redis-server", "redis",
    "mongod", "nginx", "docker", "dockerd", "elasticsearch",
    "kafka", "rabbitmq", "memcached",
]

# 連接埠出現在位址的最後一段：ss/lsof 用 `127.0.0.1:5432`、
# macOS 的 netstat -an 用 `127.0.0.1.5432`、IPv6 用 `[::]:5432`。
# 結尾一定要求空白或行尾，否則會把 IPv4 位址的每一段（.0 / .1）都當成埠號——
# 第一版就是這樣把 `127.0.0.1:33697` 解析出一個不存在的 port 1。
LISTEN_PORT_RE = re.compile(r"[:.](\d{1,5})(?=\s|$)")


def parse_listening_ports(output: str) -> list:
    """從原生指令的輸出裡盡力而為地挑出監聽中的連接埠。

    只看含有 LISTEN/LISTENING 的行；解析結果一律視為不完整（見模組說明）。
    """
    ports = set()
    for line in (output or "").splitlines():
        if "LISTEN" not in line.upper():
            continue
        for match in LISTEN_PORT_RE.finditer(line):
            port = int(match.group(1))
            if 1 <= port <= 65535:
                ports.add(port)
    return sorted(ports)


def collect_with_psutil() -> dict:
    import psutil

    report = {
        "scan": SECTION,
        "source": "psutil",
        "complete": True,
        "listening_ports": [],
        "detected_services": [],
        "notes": [],
    }

    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        report["complete"] = False
        report["notes"].append(
            "權限不足，無法讀取完整連線清單；建議以系統管理員/sudo 重新執行。"
        )
        connections = []

    seen = set()
    for conn in connections:
        if conn.status != psutil.CONN_LISTEN or not conn.laddr:
            continue
        key = (conn.laddr.port, conn.pid)
        if key in seen:
            continue
        seen.add(key)
        proc_name = None
        if conn.pid:
            try:
                proc_name = psutil.Process(conn.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        report["listening_ports"].append(
            {"port": conn.laddr.port, "pid": conn.pid, "process": proc_name}
        )

    report["listening_ports"].sort(key=lambda item: (item["port"], item["pid"] or 0))

    names = {
        p.info["name"].lower()
        for p in psutil.process_iter(["name"])
        if p.info.get("name")
    }
    report["detected_services"] = [svc for svc in COMMON_SERVICES if any(svc in n for n in names)]
    return report


def _fallback_command():
    system = platform.system()
    if system == "Windows":
        return ["netstat", "-ano"]
    if shutil.which("ss"):
        return ["ss", "-tulpn"]
    if shutil.which("lsof"):
        return ["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"]
    if shutil.which("netstat"):
        return ["netstat", "-tulpn"] if system == "Linux" else ["netstat", "-an"]
    return None


def collect_fallback() -> dict:
    report = {
        "scan": SECTION,
        "source": "fallback",
        # 原生指令的輸出格式每個平台都不一樣，這裡只做盡力而為的解析，
        # 因此一律標記為不完整——這個旗標會讓 suggested_port_range 變成 null。
        "complete": False,
        "listening_ports": [],
        "detected_services": [],
        "notes": ["未安裝 psutil，退回作業系統原生指令；建議 pip install psutil 取得更準確、跨平台一致的結果。"],
        "raw_output": None,
    }

    cmd = _fallback_command()
    if cmd is None:
        report["source"] = "none"
        report["notes"].append("此環境找不到 ss / lsof / netstat，無法掃描連接埠，請人工確認。")
        return report

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        output = result.stdout.strip()
    except Exception as e:  # noqa: BLE001 — 掃描失敗不應中斷整個流程
        report["source"] = "none"
        report["notes"].append(f"無法執行 {' '.join(cmd)}：{e}，請人工確認連接埠使用狀況。")
        return report

    report["raw_output"] = output or None
    report["listening_ports"] = [
        {"port": port, "pid": None, "process": None} for port in parse_listening_ports(output)
    ]
    return report


def collect() -> dict:
    try:
        report = collect_with_psutil()
    except ImportError:
        report = collect_fallback()

    report["occupied_ports"] = sorted({item["port"] for item in report["listening_ports"]})
    report["suggested_port_range"] = (
        capacity.port_range_suggestion(report["occupied_ports"]) if report["complete"] else None
    )
    return report


def print_human(report: dict) -> None:
    print("===== 既有服務／連接埠掃描 =====")
    for note in report["notes"]:
        print(f"（{note}）")

    print(f"--- 已在監聽的連接埠（來源：{report['source']}）---")
    if report["listening_ports"]:
        for item in report["listening_ports"]:
            print(f"port {item['port']:<6} pid {item['pid'] or '-':<8} {item['process'] or '未知'}")
    else:
        print("（沒有讀到任何監聽中的連接埠）")

    if report["detected_services"]:
        print()
        print("--- 常見開發服務行程 ---")
        for svc in report["detected_services"]:
            print(f"偵測到執行中：{svc}")

    print()
    if report["suggested_port_range"]:
        start, end = report["suggested_port_range"]
        print(f"建議可用的連接埠區間：{start}-{end}（這段目前沒有任何監聽中的埠）")
    else:
        print("建議可用的連接埠區間：無法給出——這次掃描不完整，")
        print("「沒看到」不等於「沒被佔用」，請人工確認或安裝 psutil 後重掃。")

    print("Orchestrator 規劃新服務時一律避開上方清單，且不可自行停止/重啟已偵測到的")
    print("既有服務，除非使用者明確同意。")
    print("===== 掃描結束 =====")


def main() -> int:
    parser = argparse.ArgumentParser(description="既有服務／連接埠掃描")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀的 JSON（stdout 只有 JSON）")
    args = parser.parse_args()

    report = collect()
    cached = dict(report)
    cached.pop("raw_output", None)  # 原始輸出可能很長，不進快取
    scan_cache.write(SECTION, cached)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
