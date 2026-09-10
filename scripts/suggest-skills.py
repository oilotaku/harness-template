#!/usr/bin/env python3
"""suggest-skills.py — 依專案目標決定要安裝哪些 skill（指令入口）

用法：
    # 這個 task 的交付物是 PDF、用 Python 實作，implementer 該裝什麼？
    python3 scripts/suggest-skills.py --role implementer --deliverable pdf --language python

    # 三個角色一次看完（Orchestrator 規劃時用）
    python3 scripts/suggest-skills.py --role all --deliverable pptx --keyword 簡報 --json

判斷依據是專案根目錄的 `skills.catalog.json`（範例見
`templates/skills.catalog.example.json`，方法見 `docs/skill-selection.md`）。

**這支腳本不會替你安裝任何東西**，它只做決定並留下理由。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import skill_policy  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def print_human(result: dict) -> None:
    print(f"--- 角色：{result['role']} ---")
    for warning in result["warnings"]:
        print(f"（{warning}）")

    if result["recommend"]:
        print("建議安裝：")
        for item in result["recommend"]:
            print(f"  + {item['name']}（命中：{'、'.join(item['matched_on'])}）")
            print(f"      理由：{item['why']}")
    else:
        print("建議安裝：無——這個目標沒有對應的 skill。")
        print("  （這是正常結果。skill 的成本是每個 session 都要付的，")
        print("    「沒有適用的」比「裝著以防萬一」便宜。）")

    if result["excluded"]:
        print("明確排除：")
        for item in result["excluded"]:
            print(f"  - {item['name']}：{item['reason']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="依專案目標決定要安裝哪些 skill")
    parser.add_argument("--role", default="all", help="orchestrator / implementer / verifier / all（預設 all）")
    parser.add_argument("--deliverable", action="append", default=[], help="交付物形式，可重複（例如 pdf、pptx、api）")
    parser.add_argument("--language", action="append", default=[], help="語言，可重複")
    parser.add_argument("--framework", action="append", default=[], help="框架，可重複")
    parser.add_argument("--keyword", action="append", default=[], help="領域關鍵字，可重複")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀的 JSON（stdout 只有 JSON）")
    args = parser.parse_args()

    goal = {
        "deliverables": args.deliverable,
        "languages": args.language,
        "frameworks": args.framework,
        "keywords": args.keyword,
    }

    roles = list(skill_policy.ROLES) if args.role == "all" else [args.role]

    try:
        # 先讀一次 catalog，三個角色共用——讀三次只是讓「檔案中途被改」變成可能。
        catalog = skill_policy.load()
        results = [skill_policy.recommend(goal, role, catalog=catalog) for role in roles]
    except skill_policy.CatalogError as exc:
        # 設定壞掉一律報錯，不退回「沒有建議」——那會看起來像「這個目標不需要 skill」。
        print(f"skills.catalog.json 有問題：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"參數錯誤：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("===== skill 安裝建議 =====")
    for result in results:
        print_human(result)
    print()
    print(results[0]["install_hint"])
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
