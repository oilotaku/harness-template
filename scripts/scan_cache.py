#!/usr/bin/env python3
"""scan_cache.py — 掃描結果的快取（P3-2）

黃金法則第 5 條要求「任何任務開始前」執行機器與服務掃描。多 task 專案照字面
執行會重複跑 N 次，每次輸出都進上下文（見 docs/token-strategy.md §2.3）。
這支模組把三支掃描腳本的結果集中寫進 `.harness/last-scan.json`，
讓 Orchestrator 可以在「同一台機器」的前提下重用上一輪的結果。

重用的判斷條件刻意只有一個且是機器可驗的：**能力指紋相同**。
指紋不同代表換了機器（或資源級距變了），那正是第 5 條要防的事，一律重掃。
「是不是同一個 session」腳本看不到，由 Orchestrator 自己判斷；
這裡只提供 `age_seconds` 讓它有依據。

快取只是**加速**，不是事實來源：檔案不存在、壞掉、或指紋不符時，
一律當作沒有快取（回 None），不會回傳半套資料。
"""
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_fingerprint  # noqa: E402
import machine_facts  # noqa: E402

CACHE_FILENAME = "last-scan.json"
SCHEMA = 1


def _repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


def cache_path(root=None) -> Path:
    return (Path(root) if root else _repo_root()) / ".harness" / CACHE_FILENAME


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


def capability_key(fingerprint: dict) -> str:
    """只取「能力類」欄位組成的比對鍵。

    不含 hostname——容器/CI 每次重建 hostname 都不一樣，把它算進去等於快取
    永遠失效（這正是 P3-1 修掉的誤報來源）。
    """
    return "|".join(f"{key}={fingerprint.get(key)}" for key, _label in env_fingerprint.CAPABILITY_FIELDS)


def write(section: str, payload: dict, root=None) -> Path:
    """把某一支掃描腳本的結果寫進快取的對應欄位，其餘欄位保留。

    寫入失敗（唯讀檔案系統、權限不足）不應該讓掃描本身失敗——快取是加速，
    不是必要條件，所以這裡回傳 None 而不是拋例外。
    """
    path = cache_path(root)
    existing = _read_raw(path) or {}
    sections = existing.get("sections") if isinstance(existing.get("sections"), dict) else {}

    fingerprint = current_fingerprint()
    document = {
        "schema": SCHEMA,
        "capability_key": capability_key(fingerprint),
        "fingerprint": fingerprint,
        "sections": dict(sections, **{section: payload}),
        "generated_at": time.time(),
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    return path


def _read_raw(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def load(root=None):
    """讀出可重用的快取；不可重用時一律回 None（不回半套資料）。

    回傳的 dict 會多一個 `age_seconds` 欄位，讓呼叫方自己決定夠不夠新。
    """
    document = _read_raw(cache_path(root))
    if not isinstance(document, dict):
        return None
    if document.get("schema") != SCHEMA:
        return None
    if not isinstance(document.get("sections"), dict):
        return None
    if document.get("capability_key") != capability_key(current_fingerprint()):
        # 換了機器或資源級距變了：這正是黃金法則第 5 條要防的事，不可重用。
        return None

    generated_at = document.get("generated_at")
    document["age_seconds"] = (
        max(0.0, time.time() - generated_at) if isinstance(generated_at, (int, float)) else None
    )
    return document
