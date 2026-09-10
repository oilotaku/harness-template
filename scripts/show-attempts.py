#!/usr/bin/env python3
"""show-attempts.py — 看某個 task 的驗收嘗試次數（P3-5）

用法：
    python3 scripts/show-attempts.py --task-id T-012
    python3 scripts/show-attempts.py --task-id T-012 --json
    python3 scripts/show-attempts.py --all

紀錄由 `run-hidden-tests.py` 自動寫入，不是靠 implementer 自我申報。
`should_stop` 為 null 代表**未知**（紀錄檔壞掉），不可以當成「沒有失敗過」。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import attempts  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def print_human(info: dict) -> None:
    print(f"--- {info['task_id']} ---")
    for warning in info["warnings"]:
        print(f"⚠️ {warning}")
    if info["should_stop"] is None:
        # 先看 available 再看次數：紀錄檔讀不到時 total 也是 0，
        # 但那時候印「尚未跑過」等於把「讀不到」說成「沒失敗過」——
        # 正是這個機制最不該犯的錯。
        print("是否該停損：未知——紀錄檔讀不到，不可以當成「沒有失敗過」。")
        return
    if info["total"] == 0:
        print("尚未跑過任何一次隱藏測試驗收。")
        return
    print(f"總嘗試次數：{info['total']}（失敗 {info['failures']} 次）")
    print(f"目前連續失敗：{info['consecutive_failures']} 次（停損門檻 {info['threshold']}）")
    if info["should_stop"]:
        print("是否該停損：**是**。Orchestrator 應該介入檢視 task-spec 是否有問題，")
        print("而不是再派一輪——連續失敗通常代表規格不清楚，不是實作者不夠努力。")
    else:
        print("是否該停損：否。")


def main() -> int:
    parser = argparse.ArgumentParser(description="查看 task 的驗收嘗試次數與停損判斷")
    parser.add_argument("--task-id", help="要查的 task_id")
    parser.add_argument("--all", action="store_true", help="列出所有有紀錄的 task")
    parser.add_argument("--threshold", type=int, default=attempts.DEFAULT_THRESHOLD,
                        help=f"停損門檻（預設 {attempts.DEFAULT_THRESHOLD}）")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀的 JSON（stdout 只有 JSON）")
    args = parser.parse_args()

    if not args.task_id and not args.all:
        parser.error("需要 --task-id 或 --all")

    document = attempts.load()
    task_ids = sorted(document["tasks"]) if args.all else [args.task_id]

    try:
        infos = [attempts.summary(task_id, threshold=args.threshold) for task_id in task_ids]
    except ValueError as exc:
        print(f"參數錯誤：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"tasks": infos}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("===== 驗收嘗試次數 =====")
    if not infos:
        print("目前沒有任何紀錄（還沒跑過隱藏測試驗收）。")
    for info in infos:
        print_human(info)
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
