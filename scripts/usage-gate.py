#!/usr/bin/env python3
"""usage-gate.py — 依用量重置時間的「停止／恢復」閘門（給 Orchestrator 在派工邊界查）

## 這支解決什麼

`docs/token-strategy.md` §3.1 說明「撞上限的代價是複利的」：mid-task 被砍 →
session 斷 → 重載固定開銷 → 更快撞下一次。這支把「撞牆」換成「在乾淨的任務邊界
主動停」：Orchestrator 在派下一個 task 前查一次，任一用量視窗接近上限就停在這裡
（此時上一個 task 已 commit，見 §3.3），並記下重置時間；到重置時間後自動放行。

## 為什麼是「任務邊界」而不是 mid-task 強停

模板本來就要求序列執行、每個 task 做完就 commit（§3.3/§3.4），所以任務之間是唯一
「停下來零損失」的點。硬在工具呼叫層攔截（PreToolUse）會 mid-task 打斷、丟掉未
commit 的進度，正是這支要避免的複利成本。

## 資料從哪來

讀 `~/.claude/usage-snapshot.json`（由狀態列腳本 `scripts/statusline-usage.py`
寫入，內容是 Claude Code 餵給狀態列的 rate_limits：`five_hour` / `seven_day` 的
`used_percentage` 與 `resets_at`）。沒有這個檔（非 Pro/Max、或還沒發第一次 API
回應）就放行——見下面「為什麼這裡 fail-open」。

## 為什麼這裡 fail-open（跟隱藏測試的 fail-closed 相反）

用量閘門是**最佳化**，不是安全控制。沒有用量資料就擋掉所有工作，比偶爾多跑一個
task 更糟。所以拿不到資料時放行並出聲，而不是 fail-closed。安全相關的 fail-closed
只用在「看不到就等於防護失效」的地方（隱藏測試、設定檔），那裡的沉默失效才是敵人。

## 恢復（resume）

- **被動**：下次 Orchestrator 查閘門時 `now >= resume_at` 就自動放行、清掉暫停檔。
  對所有環境都成立，不依賴任何排程器。
- **主動**（選配，因環境而異）：用 `--resume-at` 印出的 epoch 去掛排程
  （本機 `at`/`cron`、或代管環境的排程），時間到自動喚醒續跑。見 §3.7。

## 用法

    python3 scripts/usage-gate.py --check     # 派工前查：exit 0 放行、exit 2 該停
    python3 scripts/usage-gate.py --status    # 只報告，不改狀態（不建立/清除暫停）
    python3 scripts/usage-gate.py --clear     # 手動解除暫停
    python3 scripts/usage-gate.py --check --json

選項：
    --threshold N   用量百分比門檻（預設 90；或環境變數 HARNESS_USAGE_THRESHOLD）
    --snapshot P    快照檔（預設 ~/.claude/usage-snapshot.json；或 HARNESS_USAGE_SNAPSHOT）
    --pause-file P  暫停狀態檔（預設 <repo>/.harness/usage-pause.json）
    --window W      five_hour / seven_day / both（預設 both）

exit code：0＝可繼續（含「已到重置時間、自動恢復」）；2＝該停（尚在暫停中）。
其他錯誤（門檻參數非法等）回 3——那是呼叫錯誤，不該被當成「放行」。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設 ANSI 代碼頁，不先切 UTF-8 會印不出中文

DEFAULT_THRESHOLD = 90.0
WINDOWS = ("five_hour", "seven_day")
LABEL = {"five_hour": "5h", "seven_day": "7d"}

# 讀檔的三種結果要分清楚：不存在（None）、壞掉（BAD）、正常（dict）。
# 「不存在」與「壞掉」都要 fail-open，但訊息不同，才看得出是沒裝還是設錯。
_MISSING = None
_BAD = object()


def _load_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _MISSING
    except Exception:
        return _BAD


def _now() -> int:
    return int(time.time())


def _fmt_delta(secs: int) -> str:
    if secs <= 0:
        return "現在"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _fmt_when(epoch: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def _write_pause(path: Path, payload: dict) -> None:
    # 原子寫入，避免同時被讀到寫一半。
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _clear_pause(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def evaluate(snapshot: Path, pause_file: Path, threshold: float,
             windows, write: bool) -> dict:
    """回傳 {state, ...}。state ∈ {resumed, paused, pause, ok}。

    write=True 時才會建立/清除暫停檔（--check 用）；--status 傳 False。
    """
    # 1) 已經在暫停中？到期就恢復，沒到期就維持停。
    pause = _load_json(pause_file)
    if isinstance(pause, dict) and "resume_at" in pause:
        try:
            resume_at = int(pause["resume_at"])
        except (TypeError, ValueError):
            resume_at = 0
        if _now() >= resume_at:
            if write:
                _clear_pause(pause_file)
            return {"state": "resumed", "resume_at": resume_at,
                    "reason": pause.get("reason", "")}
        return {"state": "paused", "resume_at": resume_at,
                "remaining": resume_at - _now(), "reason": pause.get("reason", "")}

    # 2) 沒有暫停：看快照。拿不到就 fail-open（放行）。
    snap = _load_json(snapshot)
    if snap is _MISSING:
        return {"state": "ok", "note": "no-snapshot",
                "detail": f"找不到用量快照 {snapshot}（非 Pro/Max 或尚無 API 回應）——放行"}
    if snap is _BAD or not isinstance(snap, dict):
        return {"state": "ok", "note": "bad-snapshot",
                "detail": f"用量快照 {snapshot} 讀不出來——放行（fail-open）"}

    tripped = []          # (window, pct, resets_at)
    no_reset = []         # (window, pct) 超過門檻但沒有 resets_at，無法自動恢復
    for w in windows:
        win = snap.get(w)
        if not isinstance(win, dict):
            continue
        pct = win.get("used_percentage")
        if pct is None:
            continue
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue
        if pct < threshold:
            continue
        resets_at = win.get("resets_at")
        if resets_at is None:
            no_reset.append((w, pct))
            continue
        try:
            tripped.append((w, pct, int(resets_at)))
        except (TypeError, ValueError):
            no_reset.append((w, pct))

    if tripped:
        resume_at = min(ra for _, _, ra in tripped)
        reason = "；".join(f"{LABEL[w]} {pct:.0f}% ≥ {threshold:.0f}%"
                           for w, pct, _ in tripped)
        if write:
            _write_pause(pause_file, {
                "paused_at": _now(),
                "resume_at": resume_at,
                "threshold": threshold,
                "reason": reason,
                "windows": [{"window": w, "used_percentage": pct, "resets_at": ra}
                            for w, pct, ra in tripped],
            })
        return {"state": "pause", "resume_at": resume_at, "reason": reason,
                "no_reset": no_reset}

    return {"state": "ok", "note": "under-threshold", "no_reset": no_reset}


def _render(result: dict) -> str:
    state = result["state"]
    if state in ("pause", "paused"):
        ra = result["resume_at"]
        remaining = result.get("remaining", ra - _now())
        head = "⛔ 已達用量門檻，停在任務邊界" if state == "pause" else "⛔ 仍在暫停中"
        return (f"{head}：{result.get('reason', '')}\n"
                f"   重置／恢復時間：{_fmt_when(ra)}（還有 {_fmt_delta(remaining)}）\n"
                f"   到點後再查一次即自動放行；或用 --resume-at 掛排程主動喚醒。")
    if state == "resumed":
        return (f"✅ 已到重置時間，自動恢復（先前原因：{result.get('reason', '') or '—'}）。"
                "可以繼續派工。")
    # ok
    note = result.get("note", "")
    extra = ""
    if result.get("no_reset"):
        pairs = "、".join(f"{LABEL[w]} {pct:.0f}%" for w, pct in result["no_reset"])
        extra = f"（注意：{pairs} 已超門檻但快照沒有 resets_at，無法自動恢復，不據此暫停）"
    if note == "no-snapshot":
        return f"✅ 放行：{result.get('detail', '')}{extra}"
    if note == "bad-snapshot":
        return f"✅ 放行：{result.get('detail', '')}{extra}"
    return f"✅ 用量在門檻內，可以繼續派工。{extra}"


def _exit_code(result: dict) -> int:
    return 2 if result["state"] in ("pause", "paused") else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="依用量重置時間的停止／恢復閘門（Orchestrator 在派工邊界查）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="派工前查：exit 0 放行、exit 2 該停（會建立/清除暫停檔）")
    mode.add_argument("--status", action="store_true", help="只報告，不改狀態")
    mode.add_argument("--clear", action="store_true", help="手動解除暫停")
    parser.add_argument("--threshold", type=float, default=None,
                        help="用量百分比門檻（預設 90，或 HARNESS_USAGE_THRESHOLD）")
    parser.add_argument("--snapshot", default=None,
                        help="快照檔（預設 ~/.claude/usage-snapshot.json，或 HARNESS_USAGE_SNAPSHOT）")
    parser.add_argument("--pause-file", default=None,
                        help="暫停狀態檔（預設 <repo>/.harness/usage-pause.json）")
    parser.add_argument("--window", choices=["five_hour", "seven_day", "both"],
                        default="both", help="看哪些視窗（預設 both）")
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    args = parser.parse_args()

    # 門檻：CLI > 環境變數 > 預設。非法值 fail（回 3），不靜默退回預設。
    threshold = args.threshold
    if threshold is None:
        env = os.environ.get("HARNESS_USAGE_THRESHOLD")
        if env:
            try:
                threshold = float(env)
            except ValueError:
                print(f"HARNESS_USAGE_THRESHOLD 不是數字：{env!r}", file=sys.stderr)
                return 3
    if threshold is None:
        threshold = DEFAULT_THRESHOLD
    if not (0 < threshold <= 100):
        print(f"--threshold 必須在 (0, 100]，收到 {threshold}", file=sys.stderr)
        return 3

    snapshot = Path(args.snapshot).expanduser() if args.snapshot else (
        Path(os.environ["HARNESS_USAGE_SNAPSHOT"]).expanduser()
        if os.environ.get("HARNESS_USAGE_SNAPSHOT")
        else Path.home() / ".claude" / "usage-snapshot.json")
    pause_file = Path(args.pause_file) if args.pause_file else (
        harness_config.repo_root() / ".harness" / "usage-pause.json")

    windows = WINDOWS if args.window == "both" else (args.window,)

    if args.clear:
        removed = _clear_pause(pause_file)
        msg = "已解除暫停。" if removed else "本來就沒有暫停。"
        if args.json:
            print(json.dumps({"state": "cleared", "removed": removed}, ensure_ascii=False))
        else:
            print(msg)
        return 0

    write = args.check  # --status 不改狀態；未指定模式時預設等同 --status（唯讀）
    result = evaluate(snapshot, pause_file, threshold, windows, write=write)
    result["resume_at_epoch"] = result.get("resume_at")  # 給排程用的原始 epoch

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(_render(result))
        if result.get("resume_at"):
            print(f"--resume-at {result['resume_at']}")
    return _exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
