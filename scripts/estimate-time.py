#!/usr/bin/env python3
"""estimate-time.py — 執行時間預測的指令入口（給 Orchestrator 用）

用法：
    # 估一批任務：3 個純文件 + 1 個新模組，分 2 個 PR 週期送出
    python3 scripts/estimate-time.py --tasks doc:3,module:1 --rounds 2
    python3 scripts/estimate-time.py --tasks doc:3,module:1 --rounds 2 --json

    # 任務做完之後回填實際值（估時只有被對照過才會變準）
    python3 scripts/estimate-time.py --record T-012 --estimated 20 --actual 34

分類與單價見 scripts/timing.py 與 docs/time-estimation.md。
校準係數會自動從 .harness/timing.json 讀出來套用；樣本不足 3 筆時不套用。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import timing  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def parse_tasks(raw: str) -> list:
    """把 `doc:3,module:1` 或 `doc,doc,module` 展開成分類清單。"""
    classes = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, count = chunk.partition(":")
        name = name.strip()
        if count:
            try:
                repeat = int(count)
            except ValueError:
                raise ValueError(f"「{chunk}」的數量不是整數")
            if repeat < 1:
                raise ValueError(f"「{chunk}」的數量必須至少是 1")
        else:
            repeat = 1
        classes.extend([name] * repeat)
    if not classes:
        raise ValueError("--tasks 至少要有一個分類")
    return classes


def main() -> int:
    parser = argparse.ArgumentParser(description="執行時間預測與校準")
    parser.add_argument("--tasks", help="任務分類清單，例如 doc:3,module:1")
    parser.add_argument("--rounds", type=int, default=1, help="打算分成幾個 PR 週期（預設 1）")
    parser.add_argument("--ci-risk", type=float, default=0.3, help="預期多少比例的週期會 CI 紅一次（預設 0.3）")
    parser.add_argument("--no-calibration", action="store_true", help="不套用這個專案的歷史校準係數")
    parser.add_argument("--record", metavar="TASK_ID", help="回填某個 task 的實際耗時")
    parser.add_argument("--estimated", type=float, help="搭配 --record：當初估了幾分鐘")
    parser.add_argument("--actual", type=float, help="搭配 --record：實際花了幾分鐘")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀的 JSON（stdout 只有 JSON）")
    args = parser.parse_args()

    if args.record:
        if args.estimated is None or args.actual is None:
            parser.error("--record 需要同時提供 --estimated 與 --actual")
        timing.record(args.record, args.estimated, args.actual)
        records = timing.load_records()
        factor = timing.calibration_factor(records)
        payload = {
            "recorded": args.record,
            "samples": len(records),
            "calibration": factor,
            "calibrated": factor != 1.0 or len(records) >= timing.MIN_CALIBRATION_SAMPLES,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"已記錄 {args.record}：估 {args.estimated} 分 / 實際 {args.actual} 分")
            print(f"目前樣本數：{payload['samples']}，校準係數：{factor}")
            if payload["samples"] < timing.MIN_CALIBRATION_SAMPLES:
                print(f"（樣本不足 {timing.MIN_CALIBRATION_SAMPLES} 筆，暫不套用校準——"
                      "用一兩筆樣本調係數只是把雜訊放大成偏見）")
        return 0

    if not args.tasks:
        parser.error("需要 --tasks（或用 --record 回填實際值）")

    try:
        classes = parse_tasks(args.tasks)
    except ValueError as exc:
        print(f"參數錯誤：{exc}", file=sys.stderr)
        return 2

    factor = 1.0 if args.no_calibration else timing.calibration_factor(timing.load_records())

    try:
        result = timing.estimate(classes, rounds=args.rounds, ci_risk=args.ci_risk, calibration=factor)
    except timing.UnboundedTask as exc:
        # 無界任務不給數字。硬給一個看起來很有把握的估時，
        # 比誠實說「這題還沒被界定」更危險。
        if args.json:
            print(json.dumps({"error": "unbounded", "message": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"無法估時：{exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"參數錯誤：{exc}", file=sys.stderr)
        return 2

    result["task_classes"] = classes
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("===== 執行時間預測 =====")
    print(f"任務：{len(classes)} 個（{args.tasks}），分 {args.rounds} 個 PR 週期")
    print(f"校準係數：{factor}" + ("（樣本不足，尚未校準）" if factor == 1.0 else ""))
    print(f"預估：{result['low_minutes']}–{result['high_minutes']} 分鐘")
    print()
    print("以下不含在內：")
    for item in result["excludes"]:
        print(f"  - {item}")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
