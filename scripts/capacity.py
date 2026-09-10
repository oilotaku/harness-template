#!/usr/bin/env python3
"""capacity.py — 由機器事實推導出「容量上限」的純函式（P3-2）

為什麼要有這支：在此之前 `machine-profile.py` 只印出一句經驗法則
（「平行數 <= CPU 核心數，每個重型任務預留 1-2GB」），把換算留給 Orchestrator
每次自己心算。同一台機器可能這次判 3、下次判 5，而且沒有任何測試能驗證。
這裡把換算寫成純函式，讓它可被單元測試、也讓 `--json` 直接輸出數字。

**命名刻意跟 improvement-suggestions.md 的草案不同**：草案寫
`recommended_parallel_agents`（建議值），這裡叫 `max_parallel_agents`（上限）。
理由見 docs/token-strategy.md §3.4——機器跑得動不等於應該開平行：
在訂閱制方案下平行度會等倍放大用量視窗的消耗速率。所以這支腳本回報的是
「機器最多撐得住幾個」，預設值另外用 `default_parallel_agents`（恆為 1）表示。
"""

# 留給作業系統、編輯器、既有服務的餘裕，不分配給子智能體。
RESERVE_MB = 2048
# 一個一般子智能體行程的粗估用量；重型任務（建置、測試、容器）另計。
AGENT_MB = 1024
HEAVY_MB = 2048


def parallel_limits(cpu_count, memory_mb) -> dict:
    """回傳這台機器的平行度上限。

    讀不到 CPU 核心數時一律回 1——猜大值的代價（把機器塞爆、任務互相拖慢
    到超時）遠高於猜小值（只是慢一點）。
    """
    if not cpu_count or cpu_count < 1:
        return {
            "max_parallel_agents": 1,
            "max_parallel_heavy_tasks": 1,
            "default_parallel_agents": 1,
            "basis": "cpu_unknown",
            "limited_by": "cpu_unknown",
        }

    by_cpu_agents = cpu_count
    by_cpu_heavy = max(1, cpu_count // 2)

    if not memory_mb or memory_mb <= 0:
        return {
            "max_parallel_agents": max(1, by_cpu_agents),
            "max_parallel_heavy_tasks": by_cpu_heavy,
            "default_parallel_agents": 1,
            "basis": "cpu_only",
            "limited_by": "cpu",
        }

    usable_mb = memory_mb - RESERVE_MB
    by_mem_agents = usable_mb // AGENT_MB
    by_mem_heavy = usable_mb // HEAVY_MB

    agents = max(1, min(by_cpu_agents, by_mem_agents))
    heavy = max(1, min(by_cpu_heavy, by_mem_heavy))

    return {
        "max_parallel_agents": agents,
        "max_parallel_heavy_tasks": heavy,
        "default_parallel_agents": 1,
        "basis": "cpu+memory",
        "limited_by": "memory" if by_mem_agents < by_cpu_agents else "cpu",
    }


# 建議連接埠的搜尋範圍：避開 <1024 的特權埠與常見預設埠（3000/5000/5432/6379/8080）。
PORT_WINDOW = 100
PORT_SEARCH_START = 8100
PORT_SEARCH_END = 9999


def port_range_suggestion(occupied) -> list:
    """挑一段完全沒有被佔用的連接埠區間，回傳 [start, end]；找不到回 None。

    呼叫方必須先確認連接埠清單是完整的（`complete` 為真）。清單不完整時
    這個建議沒有意義——「沒看到」不等於「沒被佔用」，所以 service-scan.py
    在掃描不完整時會直接回 null，而不是給一個看起來很有把握的區間。
    """
    taken = {int(p) for p in occupied or []}
    start = PORT_SEARCH_START
    while start + PORT_WINDOW - 1 <= PORT_SEARCH_END:
        end = start + PORT_WINDOW - 1
        if not any(start <= port <= end for port in taken):
            return [start, end]
        start += PORT_WINDOW
    return None
