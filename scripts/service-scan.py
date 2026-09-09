#!/usr/bin/env python3
"""service-scan.py — 跨平台既有服務／連接埠掃描（取代 bash 版本，Win/Linux/Mac 通用）
對應需求第 13 點：必須根據實際機器已有服務檢查並避開服務

優先使用 psutil（`pip install psutil`）以取得三平台一致、準確的結果；
沒有安裝時退回各作業系統原生指令，盡力而為。
"""
import platform
import shutil
import subprocess

COMMON_SERVICES = [
    "postgres", "mysqld", "mariadbd", "redis-server", "redis",
    "mongod", "nginx", "docker", "dockerd", "elasticsearch",
    "kafka", "rabbitmq", "memcached",
]


def scan_with_psutil():
    import psutil

    print("--- 已在監聽的連接埠（含所屬行程，psutil）---")
    seen = set()
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        print("（權限不足，無法讀取完整連線清單；建議以系統管理員/sudo 重新執行）")
        connections = []

    for conn in connections:
        if conn.status == psutil.CONN_LISTEN and conn.laddr:
            key = (conn.laddr.port, conn.pid)
            if key in seen:
                continue
            seen.add(key)
            proc_name = "未知"
            if conn.pid:
                try:
                    proc_name = psutil.Process(conn.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            print(f"port {conn.laddr.port:<6} pid {conn.pid or '-':<8} {proc_name}")

    print()
    print("--- 常見開發服務行程（psutil）---")
    names = {
        p.info["name"].lower()
        for p in psutil.process_iter(["name"])
        if p.info.get("name")
    }
    for svc in COMMON_SERVICES:
        if any(svc in n for n in names):
            print(f"偵測到執行中：{svc}")


def scan_fallback():
    system = platform.system()
    print("（未安裝 psutil，退回作業系統原生指令；建議 pip install psutil 取得更準確、跨平台一致的結果）")
    print()
    print("--- 已在監聽的連接埠 ---")

    if system == "Windows":
        cmd = ["netstat", "-ano"]
    elif shutil.which("ss"):
        cmd = ["ss", "-tulpn"]
    elif shutil.which("lsof"):
        cmd = ["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"]
    elif shutil.which("netstat"):
        cmd = ["netstat", "-tulpn"] if system == "Linux" else ["netstat", "-an"]
    else:
        print("（此環境找不到 ss / lsof / netstat，無法掃描連接埠，請人工確認）")
        return

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        print(result.stdout.strip() or "（無輸出，可能需要權限）")
    except Exception as e:  # noqa: BLE001 — 這裡刻意攔截所有例外，掃描失敗不應中斷整個流程
        print(f"（無法執行 {' '.join(cmd)}：{e}，請人工確認連接埠使用狀況）")


def main():
    print("===== 既有服務／連接埠掃描 =====")
    try:
        scan_with_psutil()
    except ImportError:
        scan_fallback()

    print()
    print("建議：Orchestrator 規劃新服務（開發伺服器、資料庫、快取等）的連接埠與")
    print("服務名稱時，一律避開上方清單，且不可自行停止/重啟已偵測到的既有服務，")
    print("除非使用者明確同意。")
    print("===== 掃描結束 =====")


if __name__ == "__main__":
    main()
