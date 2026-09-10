#!/usr/bin/env python3
"""env_fingerprint.py — 環境指紋的組成與比對規則（純邏輯，不做 I/O 與輸出）

對應 docs/history/improvement-suggestions.md 的 P3-1。

## 問題

舊版指紋只有 `hostname` / `os` / `arch`。但 Docker、Kubernetes 與 Claude Code 的
遠端執行環境，**每次啟動 hostname 都是隨機的**——於是 `env-guard.py` 每次都回報
「不符」、`init.py` 每次都以 exit 1 收場，而黃金法則第 6 條要求 Orchestrator
一律停下來問使用者。

結果是使用者被問到麻木，養成「看到這個警告就跳過」的習慣。**一個總是誤報的
守門機制，比沒有守門機制更糟**——它不只沒擋到真正的環境變更，還順便訓練使用者
忽略警告。

## 解法：分開「身分」與「能力」

指紋拆成兩類欄位，比對規則不同：

- **身分類**（`hostname`）：容器裡沒有意義，因此**偵測到容器時不參與比對**，
  只當成參考資訊記錄下來。
- **能力類**（`os` / `arch` / `container` / CPU 級距 / 記憶體級距）：
  一律比對。這些才是黃金法則第 5 條真正在意的東西——Orchestrator 要靠它們
  決定平行子智能體數量，變了就該停下來確認。

CPU 與記憶體用**級距**而不是精確值，因為雲端執行環境同一規格的機器也可能
回報 7.8GB / 8.0GB 這種浮動，精確比對又會變成另一種誤報來源。
級距跨越才代表「這台機器的能力真的不一樣了」。
"""

CPU_BUCKETS = [(1, "1"), (2, "2"), (4, "3-4"), (8, "5-8"), (16, "9-16"), (32, "17-32")]
MEMORY_BUCKETS_GB = [
    (2, "<2GB"),
    (4, "2-4GB"),
    (8, "4-8GB"),
    (16, "8-16GB"),
    (32, "16-32GB"),
    (64, "32-64GB"),
]

# 這些欄位一律比對；hostname 另外處理（見 compare()）。
CAPABILITY_FIELDS = (
    ("os", "作業系統"),
    ("arch", "架構"),
    ("container", "是否容器環境"),
    ("cpu_bucket", "CPU 核心數級距"),
    ("memory_bucket", "記憶體級距"),
)

FIELD_LABELS = dict(CAPABILITY_FIELDS, hostname="主機名稱", hostname_stable="主機名稱是否穩定")


def cpu_bucket(count) -> str:
    if not count:
        return "未知"
    for limit, label in CPU_BUCKETS:
        if count <= limit:
            return label
    return "33+"


def memory_bucket(mb) -> str:
    if not mb:
        return "未知"
    gb = mb / 1024
    for limit, label in MEMORY_BUCKETS_GB:
        if gb <= limit:
            return label
    return "64GB+"


def build(hostname, os_name, arch, container, cpu_count, memory_mb, hostname_stable=None) -> dict:
    return {
        "hostname": hostname,
        # hostname_stable 不參與比對，它是「該不該比對 hostname」的依據本身。
        # 預設沿用 container：容器裡的 hostname 一定不穩定。
        "hostname_stable": bool(not container if hostname_stable is None else hostname_stable),
        "os": os_name,
        "arch": arch,
        "container": bool(container),
        "cpu_bucket": cpu_bucket(cpu_count),
        "memory_bucket": memory_bucket(memory_mb),
    }


def compare(recorded: dict, current: dict):
    """比對兩份指紋。

    回傳 (mismatches, missing_fields, skipped)：
      - mismatches：[(欄位, 標籤, 記錄值, 目前值)]，代表真正的環境變更
      - missing_fields：舊版指紋檔缺少的欄位，需要補齊（不算不符）
      - skipped：[(欄位, 原因)]，刻意不比對的欄位
    """
    mismatches = []
    missing = []
    skipped = []

    for key, label in CAPABILITY_FIELDS:
        if key not in recorded:
            missing.append(key)
            continue
        if recorded.get(key) != current.get(key):
            mismatches.append((key, label, recorded.get(key), current.get(key)))

    if "hostname_stable" not in recorded:
        missing.append("hostname_stable")

    # hostname：兩邊只要有一邊的 hostname 不穩定（容器、K8s、Codespaces、CI）
    # 就不比對。「有一邊」而不是「兩邊」是刻意的——從實體機搬到容器（或反過來）時，
    # container 欄位本身就會報不符，不需要 hostname 再報一次同一件事。
    unstable = not recorded.get("hostname_stable", not recorded.get("container")) or not current.get(
        "hostname_stable", True
    )
    if "hostname" not in recorded:
        missing.append("hostname")
    elif unstable:
        if recorded.get("hostname") != current.get("hostname"):
            skipped.append(
                (
                    "hostname",
                    f"這個執行環境（容器／K8s／Codespaces／CI）的 hostname 每次重建都不同"
                    f"（記錄為「{recorded.get('hostname')}」、"
                    f"目前為「{current.get('hostname')}」），不列入比對",
                )
            )
    elif recorded.get("hostname") != current.get("hostname"):
        mismatches.append(
            ("hostname", "主機名稱", recorded.get("hostname"), current.get("hostname"))
        )

    return mismatches, missing, skipped


def describe(fingerprint: dict) -> list:
    """把指紋整理成「標籤：值」的清單，給 CLI 印出來。"""
    order = ["hostname", "hostname_stable", "os", "arch", "container", "cpu_bucket", "memory_bucket"]
    lines = []
    for key in order:
        if key not in fingerprint:
            continue
        value = fingerprint[key]
        if key in ("container", "hostname_stable"):
            value = "是" if value else "否"
        lines.append(f"{FIELD_LABELS.get(key, key)}：{value}")
    return lines
