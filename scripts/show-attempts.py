#!/usr/bin/env python3
"""show-attempts.py — 看某個 task 的驗收嘗試次數（第一輪 P3-5，第二輪 P3-9 加簽章驗證）

用法：
    python3 scripts/show-attempts.py --task-id T-012
    python3 scripts/show-attempts.py --task-id T-012 --token <權杖>   # 驗證紀錄沒被動過
    python3 scripts/show-attempts.py --task-id T-012 --json
    python3 scripts/show-attempts.py --all

紀錄由 `run-hidden-tests.py` 自動寫入，每筆都用權杖推導的 HMAC 簽過。
帶 `--token` 才能驗證；沒帶時 integrity 是 "unchecked"——**「無法驗證」跟「沒有紀錄」
是兩件事**，兩者都不可以當成「沒有失敗過」。
`should_stop` 為 null 代表**未知**（紀錄檔壞掉、或簽章對不上），不可以當成 false。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import attempts  # noqa: E402
import hidden_vault as vault  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

INTEGRITY_LABELS = {
    "unchecked": "未驗證（沒有提供權杖；這不代表紀錄可信）",
    "ok": "已驗證，每一筆簽章都對",
    "partial": "最近的紀錄已驗證；更早的是先前封存的權杖簽的，這把權杖驗不了",
    "tampered": "**簽章不符**，紀錄被動過，次數不可信",
}


def print_human(info: dict) -> None:
    print(f"--- {info['task_id']} ---")
    for warning in info["warnings"]:
        print(f"⚠️ {warning}")
    print(f"紀錄完整性：{INTEGRITY_LABELS.get(info['integrity'], info['integrity'])}")
    if info["should_stop"] is None:
        # 先看可信度再看次數：紀錄檔讀不到時 total 也是 0，
        # 但那時候印「尚未跑過」等於把「讀不到」說成「沒失敗過」——
        # 正是這個機制最不該犯的錯。
        if not info["available"]:
            print("是否該停損：未知——紀錄檔讀不到，不可以當成「沒有失敗過」。")
        else:
            print("是否該停損：未知——紀錄被動過，次數不可信；請人工確認實際狀況。")
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
    parser.add_argument("--token", help="該 task 的執行權杖；提供時會驗證每筆紀錄的簽章")
    parser.add_argument("--threshold", type=int, default=attempts.DEFAULT_THRESHOLD,
                        help=f"停損門檻（預設 {attempts.DEFAULT_THRESHOLD}）")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀的 JSON（stdout 只有 JSON）")
    args = parser.parse_args()

    if not args.task_id and not args.all:
        parser.error("需要 --task-id 或 --all")
    if args.token and args.all:
        parser.error("--token 對應單一 task 的權杖，不能與 --all 併用")

    document = attempts.load()
    task_ids = sorted(document["tasks"]) if args.all else [args.task_id]
    mac_key = vault.mac_key(args.token) if args.token else None

    # 有權杖時，順便從簽過章的 manifest 拿「runner 寫過幾筆」——逐筆簽章抓不到整筆刪除，
    # 只有這個計數抓得到。manifest 簽章不符就不採信它的計數（那本身已是竄改訊號）。
    expected_total = None
    if args.token:
        entry = vault.load_manifest().get("tasks", {}).get(args.task_id)
        if entry and vault.verify_entry(entry, args.token):
            expected_total = entry.get("attempts_recorded") or 0
        elif entry:
            print("⚠️ manifest 項目的簽章不符，無法採信它記錄的嘗試筆數。", file=sys.stderr)

    try:
        infos = [
            attempts.summary(
                task_id, threshold=args.threshold, mac_key=mac_key, expected_total=expected_total
            )
            for task_id in task_ids
        ]
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
