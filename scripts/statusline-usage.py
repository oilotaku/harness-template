#!/usr/bin/env python3
"""statusline-usage.py — Claude Code 狀態列：5 小時／7 天用量百分比與重置時間。

這是 usage-gate.py 的**資料來源**：Claude Code 把 session 資訊（含 rate_limits）以
JSON 從 stdin 餵給狀態列命令，這支腳本印出的一行就是狀態列內容，並把用量寫進
`~/.claude/usage-snapshot.json` 當快取，讓 `scripts/usage-gate.py` 讀來做停止／恢復
判斷（見 docs/token-strategy.md §3.7）。

## 怎麼啟用

在你實際跑 claude 的機器的 `~/.claude/settings.json` 加（已有檔就只併入 statusLine）：

    {
      "statusLine": {
        "type": "command",
        "command": "python3 ~/.claude/statusline-usage.py",
        "refreshInterval": 10
      }
    }

並把這支複製到 `~/.claude/statusline-usage.py`。之後重開 Claude Code 狀態列才生效。

## 前提（誠實記載）

`rate_limits` 只在 **Pro/Max（或 gateway）帳號、且該 session 發過第一次 API 回應
之後**才會出現在 stdin，且每個視窗（five_hour / seven_day）可能各自缺席。所以：

  - 有新資料時：寫進快照當快取
  - 沒有新資料時：回退讀快取（標記 cached），避免狀態列與 usage-gate 忽有忽無

不依賴 jq，只用 Python 標準庫。
"""
import sys
import json
import time
from pathlib import Path

SNAP = Path.home() / ".claude" / "usage-snapshot.json"


def load_stdin() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def read_snapshot() -> dict:
    try:
        return json.loads(SNAP.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_snapshot(data: dict) -> None:
    # 原子寫入：先寫暫存再 rename，避免狀態列每幾秒讀到寫一半的檔。
    try:
        SNAP.parent.mkdir(parents=True, exist_ok=True)
        tmp = SNAP.with_name(SNAP.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SNAP)
    except Exception:
        pass


def window(rate_limits: dict, key: str):
    w = (rate_limits or {}).get(key) or {}
    pct = w.get("used_percentage")
    if pct is None:
        return None
    return {"used_percentage": pct, "resets_at": w.get("resets_at")}


def fmt_reset(epoch) -> str:
    """把重置的 epoch 秒轉成『本地時刻 + 還有多久』，例如 14:30(2h05m)。"""
    try:
        epoch = int(epoch)
    except (TypeError, ValueError):
        return "?"
    clock = time.strftime("%H:%M", time.localtime(epoch))
    delta = epoch - int(time.time())
    if delta <= 0:
        return clock  # 已過重置點，Claude Code 通常也會把該視窗移除
    days, rem = divmod(delta, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{clock}(+{days}d{hours}h)"
    if hours:
        return f"{clock}({hours}h{minutes:02d}m)"
    return f"{clock}({minutes}m)"


def main() -> int:
    data = load_stdin()
    rate_limits = data.get("rate_limits") or {}
    five = window(rate_limits, "five_hour")
    seven = window(rate_limits, "seven_day")

    snap = read_snapshot()
    fresh = bool(five or seven)
    if fresh:
        # 只更新這次有拿到的視窗，缺席的沿用快取裡的舊值。
        if five:
            snap["five_hour"] = five
        if seven:
            snap["seven_day"] = seven
        snap["updated_at"] = int(time.time())
        write_snapshot(snap)

    f = snap.get("five_hour")
    s = snap.get("seven_day")

    parts = []
    if f:
        parts.append(f"5h {float(f['used_percentage']):.0f}% →{fmt_reset(f.get('resets_at'))}")
    if s:
        parts.append(f"7d {float(s['used_percentage']):.0f}% →{fmt_reset(s.get('resets_at'))}")

    if not parts:
        # 從未拿到過用量：非 Pro/Max、或該 session 還沒發第一次 API 回應。
        print("⏳ 用量待載入")
        return 0

    line = " · ".join(parts)
    if not fresh:
        line += " (cached)"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
