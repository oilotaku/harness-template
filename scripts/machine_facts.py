#!/usr/bin/env python3
"""machine_facts.py — 蒐集「這台機器是什麼樣子」的事實（不負責印出來）

原本這些函式都寫在 `machine-profile.py` 裡，2026-09-10（對應
docs/history/improvement-suggestions.md 的 P3-1）抽出來成為共用模組，因為
`env-guard.py` 也需要同一批事實來判斷「這是不是同一台機器」——
指紋不能只看 hostname，容器每次重建 hostname 都是隨機的（見該腳本說明）。

分工：本模組只回傳資料，`machine-profile.py` 負責排版印給人看，
`env_fingerprint.py` 負責把它們變成可比對的指紋。
"""
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path


def cpu_count():
    return os.cpu_count()


def memory_mb():
    system = platform.system()
    try:
        import psutil  # 選用：裝了會更準確、更跨平台

        return round(psutil.virtual_memory().total / (1024 * 1024))
    except ImportError:
        pass

    if system == "Linux":
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb // 1024
        except OSError:
            return None
    elif system == "Darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, encoding="utf-8", errors="replace"
            )
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


def gpu_info():
    """依序嘗試 nvidia-smi → lspci → /dev/dri → macOS system_profiler。
    只認 nvidia-smi 會漏掉 AMD/Intel/Apple/樹莓派 V3D 等 GPU，讓 Orchestrator
    誤以為機器完全沒有 GPU，因此加上非 NVIDIA 的 fallback 判斷。"""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            out = subprocess.check_output(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                text=True,
                encoding="utf-8",
                errors="replace",
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
                out = subprocess.check_output(
                    [lspci], text=True, encoding="utf-8", errors="replace"
                )
                gpu_lines = [
                    line.split(": ", 1)[1] if ": " in line else line
                    for line in out.splitlines()
                    if re.search(
                        r"VGA compatible controller|3D controller|Display controller", line
                    )
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
                    [profiler, "SPDisplaysDataType"],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
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


# 這些環境變數存在時，代表跑在「每次都重新建立」的執行環境裡，
# hostname 隨機且沒有識別意義（見 hostname_is_stable）。
EPHEMERAL_ENV_VARS = (
    "KUBERNETES_SERVICE_HOST",  # Kubernetes
    "CODESPACES",  # GitHub Codespaces
    "GITHUB_ACTIONS",  # GitHub Actions runner
    "GITLAB_CI",
    "CI",  # 通用 CI 慣例（放最後，因為最容易誤中）
)


def in_container() -> bool:
    """是否在容器裡。"""
    if platform.system() != "Linux":
        # macOS/Windows 上跑容器時，Python 本身通常也在 Linux 容器內，
        # 所以走不到這裡；這個分支代表「原生桌面環境」。
        return False
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True  # Docker / Podman
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    return any(marker in content for marker in ("docker", "kubepods", "containerd", "lxc"))


def hostname_is_stable() -> bool:
    """這個環境的 hostname 能不能當成「同一台機器」的判準。

    對 env-guard 特別重要：容器、K8s、Codespaces、CI runner 每次重建 hostname
    都不同，拿它當判準會每次都誤報，把守門機制變成雜訊（見 env_fingerprint.py）。
    容器以外還看環境變數，是因為 K8s／Codespaces 不一定命中 /.dockerenv。
    """
    if in_container():
        return False
    return not any(os.environ.get(name) for name in EPHEMERAL_ENV_VARS)


def container_hint() -> str:
    """給人看的容器狀態描述（machine-profile.py 用）。"""
    if platform.system() != "Linux":
        return "否（非 Linux，容器判斷不適用）"
    if os.path.exists("/.dockerenv"):
        return "是（Docker 容器）"
    if os.path.exists("/run/.containerenv"):
        return "是（Podman 容器）"
    if in_container():
        return "是（容器環境）"
    return "否"
