#!/usr/bin/env python3
"""attempts.py — 每個 task 的驗收嘗試次數（P3-5）

## 問題

`implementer-generic.md` 的停損規則寫得很好：「同一條驗收標準連續失敗 ≥3 次
就回報 blocked」。但**沒有任何機制記錄失敗了幾次**——實務上是靠子智能體
自己數，而它每輪都是新的上下文，根本數不到；就算數得到，那也是自我申報。

一個「靠被監督者自己申報」的停損規則，在該生效的時候最不會生效：
實作者卡住、開始亂試的那一輪，正是它最沒有動機說「我失敗三次了」的一輪。

## 解法

由**執行測試的那一方**記錄，不是由被測的那一方。`run-hidden-tests.py`
（隱藏測試的唯一執行入口）每跑一次就寫一筆進 `.harness/attempts.json`，
Orchestrator 讀它就有客觀依據。

## task_id 請用 ASCII

`task_id` 會以命令列參數的形式傳給 `run-hidden-tests.py` / `show-attempts.py`。
在 `LC_ALL=C` 這類非 UTF-8 locale 下，非 ASCII 的參數在 `subprocess` 那一層就
編不出去（`UnicodeEncodeError`），跟本模組怎麼存無關——那是作業系統層的限制。
所以 task_id 建議用 `T-012` 這種形式，中文寫在 task-spec 的標題裡。

## 兩個刻意的設計

- **寫入失敗不會讓驗收失敗。** 記錄是輔助資訊，不是驗收結果本身；
  唯讀檔案系統不該讓一次合法的驗收變成錯誤。
- **紀錄檔壞掉時 `should_stop` 回 `None`（未知），不是 `False`。**
  「讀不到紀錄」跟「沒有失敗過」是兩件事，混為一談等於讓停損規則
  在檔案壞掉時靜靜消失——這個 repo 已經為這類沉默失效付過好幾次代價。
"""
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

RECORD_FILENAME = "attempts.json"

# 跟 implementer-*.md 的停損規則同一個數字。改這裡的話那邊也要改。
DEFAULT_THRESHOLD = 3


def _repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


def record_path(root=None) -> Path:
    return (Path(root) if root else _repo_root()) / ".harness" / RECORD_FILENAME


def load(root=None) -> dict:
    """讀出所有紀錄。

    回傳 `{"tasks": {...}, "available": bool, "warnings": [...]}`。
    `available` 為 False 代表「讀不到」而不是「沒有失敗過」——呼叫方必須分清楚。
    """
    path = record_path(root)
    if not path.exists():
        # 還沒跑過任何一次驗收：這是正常起始狀態，不是異常。
        return {"tasks": {}, "available": True, "warnings": []}

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "tasks": {},
            "available": False,
            "warnings": [f"{RECORD_FILENAME} 讀不到或已損壞（{exc}）：不可把「沒有紀錄」當成「沒有失敗過」。"],
        }

    tasks = document.get("tasks") if isinstance(document, dict) else None
    if not isinstance(tasks, dict):
        return {
            "tasks": {},
            "available": False,
            "warnings": [f"{RECORD_FILENAME} 的格式不對：不可把「沒有紀錄」當成「沒有失敗過」。"],
        }

    return {"tasks": tasks, "available": True, "warnings": []}


def _key_id(mac_key: bytes) -> str:
    return hashlib.sha256(mac_key).hexdigest()[:16]


def _sign(entry: dict, mac_key: bytes) -> str:
    """每筆紀錄的 HMAC（第二輪 P3-9）。金鑰由權杖推導（hidden_vault.mac_key），
    implementer 沒有權杖，所以清掉或改寫自己的失敗紀錄之後簽章對不上。"""
    body = {k: v for k, v in entry.items() if k != "signature"}
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(mac_key, payload, "sha256").hexdigest()


def record(task_id: str, passed: bool, note=None, root=None, mac_key=None):
    """記一次驗收嘗試。寫入失敗回 None——記錄是輔助資訊，不該讓驗收本身失敗。

    給了 `mac_key` 就在這筆紀錄上附簽章；沒給就不簽（例如本機除錯）。
    """
    document = load(root)
    if not document["available"]:
        # 舊檔壞掉時不要在上面疊加，那會讓壞掉的內容永遠留著。
        # 直接以這一筆為起點重建，並在 note 留下痕跡。
        tasks = {}
    else:
        tasks = dict(document["tasks"])

    entries = list(tasks.get(task_id) or [])
    entry = {"passed": bool(passed), "at": time.time(), "note": note}
    if mac_key is not None:
        # key_id 讓驗證端分得出「用別把金鑰簽的」與「沒簽／簽錯」：同一個 task
        # 重新封存會換權杖（換金鑰），舊紀錄不是被動過，只是這把金鑰驗不了。
        entry["key_id"] = _key_id(mac_key)
        entry["signature"] = _sign(entry, mac_key)
    entries.append(entry)
    tasks[task_id] = entries

    path = record_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    return path


def summary(task_id: str, root=None, threshold: int = DEFAULT_THRESHOLD, mac_key=None, expected_total=None) -> dict:
    """某個 task 的嘗試統計，含「該不該停損」的判斷。

    `should_stop` 有三種值：
      True  —— 連續失敗次數已達門檻，Orchestrator 應該介入而不是再派一輪
      False —— 還沒到門檻
      None  —— **未知**（紀錄檔壞掉，或給了金鑰但簽章對不上）。不可以當成 False。

    `integrity`：
      "unchecked" —— 沒給金鑰，無法驗證（跟「沒有紀錄」是兩件事）
      "ok"        —— 每一筆簽章都對
      "partial"   —— 這把金鑰能驗的都對，但有些是先前封存（另一把權杖）簽的，驗不了
      "tampered"  —— 至少一筆缺簽章或簽章不符、或筆數比 manifest 記的少：紀錄被動過

    重新封存同一個 task 會換權杖、換金鑰，所以舊紀錄用新金鑰驗不了——那不是竄改。
    停損看的是**最後幾筆**連續失敗，而最後幾筆一定是現在這把金鑰簽的（只要新權杖
    跑過一次），所以「partial」不影響 should_stop 的可信度；舊紀錄被改只會動到
    歷史總數，動不到尾端。
    """
    if threshold < 1:
        raise ValueError(f"threshold 至少是 1，收到 {threshold}")

    document = load(root)
    entries = document["tasks"].get(task_id) or []
    warnings = list(document["warnings"])

    integrity = "unchecked"
    if mac_key is not None:
        current_key = _key_id(mac_key)
        bad, foreign = [], []
        for index, entry in enumerate(entries):
            signature = entry.get("signature")
            if not isinstance(signature, str):
                bad.append(index)
            elif entry.get("key_id") not in (None, current_key):
                foreign.append(index)
            elif not hmac.compare_digest(_sign(entry, mac_key), signature):
                bad.append(index)
        if bad:
            integrity = "tampered"
            warnings.append(
                f"{len(bad)} 筆紀錄缺簽章或簽章不符（第 {', '.join(str(i + 1) for i in bad)} 筆）："
                "attempts.json 在 runner 寫入之後被動過，次數不可信。"
            )
        elif foreign:
            integrity = "partial"
            warnings.append(
                f"{len(foreign)} 筆紀錄是先前封存的權杖簽的，這把權杖驗不了"
                f"（第 {', '.join(str(i + 1) for i in foreign)} 筆）；最近的紀錄已驗證。"
            )
        else:
            integrity = "ok"

    # 逐筆簽章抓不到「整筆刪掉」。runner 會把已記錄的筆數寫進簽過章的 manifest，
    # 呼叫方帶過來比對：少了就是有人砍掉紀錄（最有動機砍的正是最後幾次失敗）。
    if expected_total is not None and integrity != "tampered" and len(entries) != int(expected_total):
        integrity = "tampered"
        warnings.append(
            f"manifest 記錄 runner 寫過 {expected_total} 筆，attempts.json 裡只有 {len(entries)} 筆："
            "有紀錄被刪掉了，次數不可信。"
        )

    consecutive = 0
    for entry in reversed(entries):
        if entry.get("passed"):
            break
        consecutive += 1

    trustworthy = document["available"] and integrity != "tampered"

    return {
        "task_id": task_id,
        "total": len(entries),
        "failures": sum(1 for entry in entries if not entry.get("passed")),
        "consecutive_failures": consecutive,
        "last_passed": bool(entries[-1].get("passed")) if entries else None,
        "threshold": threshold,
        "should_stop": (consecutive >= threshold) if trustworthy else None,
        "available": document["available"],
        "integrity": integrity,
        "warnings": warnings,
    }
