#!/usr/bin/env python3
"""machine-profile.py — 跨平台機器效能掃描（取代 bash 版本，Win/Linux/Mac 通用）
對應需求第 12 點：必須根據實際機器效能規劃任務
"""
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path


def get_memory_mb():
    system = platform.system()
    try:
        import psutil  # 選用：裝了會更準確、更跨平台
        return round(psutil.virtual_memory().total / (1024 * 1024))
    except ImportError:
        pass

    if system == "Linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb // 1024
        except OSError:
            return None
    elif system == "Darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return int(out.strip()) // (1024 * 1024)
        except Exception:
            return None
    elif system == "Windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return stat.ullTotalPhys // (1024 * 1024)
        except Exception:
            return None
    return None


def get_gpu_info():
    """依序嘗試 nvidia-smi → lspci → /dev/dri → macOS system_profiler。
    只認 nvidia-smi 會漏掉 AMD/Intel/Apple/樹莓派 V3D 等 GPU，讓 Orchestrator
    誤以為機器完全沒有 GPU，因此加上非 NVIDIA 的 fallback 判斷。"""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            out = subprocess.check_output(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                text=True,
            )
            if out.strip():
                return out.strip()
        except Exception:
            pass

    system = platform.system()

    if system == "Linux":
        lspci = shutil.which("lspci")
        if lspci:
            try:
                out = subprocess.check_output([lspci], text=True)
                gpu_lines = [
                    line.split(": ", 1)[1] if ": " in line else line
                    for line in out.splitlines()
                    if re.search(r"VGA compatible controller|3D controller|Display controller", line)
                ]
                if gpu_lines:
                    return "; ".join(gpu_lines) + "（廠牌由 lspci 判斷，非 NVIDIA 專屬偵測）"
            except Exception:
                pass

        dri_dir = Path("/dev/dri")
        try:
            if dri_dir.is_dir() and any(dri_dir.iterdir()):
                return "偵測到 /dev/dri render node，存在 GPU/顯示裝置但廠牌未知（lspci 不可用）"
        except OSError:
            pass

    elif system == "Darwin":
        profiler = shutil.which("system_profiler")
        if profiler:
            try:
                out = subprocess.check_output(
                    [profiler, "SPDisplaysDataType"], text=True, timeout=10
                )
                names = [
                    line.split(":", 1)[1].strip()
                    for line in out.splitlines()
                    if "Chipset Model:" in line
                ]
                if names:
                    return "; ".join(names)
            except Exception:
                pass

    return None


def get_container_hint():
    if platform.system() != "Linux":
        return "否（非 Linux，容器判斷不適用）"
    if os.path.exists("/.dockerenv"):
        return "是（Docker 容器）"
    try:
        with open("/proc/1/cgroup") as f:
            content = f.read()
            if "docker" in content or "kubepods" in content:
                return "是（容器環境）"
    except OSError:
        pass
    return "否"


def main():
    print("===== 機器效能掃描 =====")
    print(f"作業系統：{platform.system()} {platform.release()}")
    print(f"主機名稱：{socket.gethostname()}")
    print(f"CPU 核心數：{os.cpu_count() or '未知'}")

    mem_mb = get_memory_mb()
    if mem_mb:
        print(f"總記憶體：{mem_mb} MB")
    else:
        print("總記憶體：未知（可執行 pip install psutil 取得更準確、更跨平台的數字）")

    total, _used, free = shutil.disk_usage(".")
    print(
        f"磁碟可用空間（目前目錄所在磁區）：{free // (1024 ** 3)} GB 可用 / "
        f"共 {total // (1024 ** 3)} GB"
    )

    gpu = get_gpu_info()
    print(f"GPU：{gpu}" if gpu else "GPU：未偵測到（或本腳本尚不支援辨識此裝置的廠牌）")

    print(f"是否在容器環境中：{get_container_hint()}")

    print()
    print("建議：Orchestrator 應依 CPU 核心數與記憶體大小，決定同時可派出的")
    print("平行子智能體/建置行程數量（經驗法則：平行數 <= CPU 核心數，")
    print("且每個重型任務預留至少 1-2GB 記憶體餘裕）。")
    print("===== 掃描結束 =====")


if __name__ == "__main__":
    main()
