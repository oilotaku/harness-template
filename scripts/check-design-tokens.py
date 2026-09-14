#!/usr/bin/env python3
"""check-design-tokens.py — 驗證設計 token 的結構與對比度

設計基準本身不可能自動驗「好不好看」，但有一件事是算得出來的：**對比度**。
`implementer-frontend.md` 原本把「可及性視為隱性驗收標準」當成一句自律規則，
這支腳本把它變成會紅的檢查——而且它抓得到人眼抓不到的東西：
這份模板附的預設色票第一版，深色邊框算出來是 **2.99**，門檻是 **3.00**。

用法：
    python3 scripts/check-design-tokens.py
    python3 scripts/check-design-tokens.py --json
    python3 scripts/check-design-tokens.py --show      # 列出所有配對的實際比值

exit code：
    0 = 結構完整且所有宣告的配對都達 WCAG AA（或這個專案設了 design: false）
    1 = 結構有問題、對比度不足、或 token 檔讀不到
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import design_tokens as tokens  # noqa: E402
import harness_config  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

LEVEL_LABEL = {"text": "文字", "large-text": "大字", "non-text": "非文字元件"}


def main() -> int:
    parser = argparse.ArgumentParser(description="驗證設計 token 的結構與對比度（WCAG AA）")
    parser.add_argument("--json", action="store_true", help="機器可讀輸出")
    parser.add_argument("--show", action="store_true", help="列出每一組配對的實際比值，不只列不合格的")
    args = parser.parse_args()

    root = tokens.repo_root()
    try:
        config = harness_config.load(root)
    except harness_config.ConfigError as exc:
        message = f"harness.config.json 有問題：{exc}"
        print(json.dumps({"ok": False, "reason": message}, ensure_ascii=False)
              if args.json else f"❌ {message}", file=None if args.json else sys.stderr)
        return 1

    settings = tokens.settings_of(config)
    if not settings["enabled"]:
        if args.json:
            print(json.dumps({"ok": True, "skipped": "design: false（這個專案沒有圖形介面）"},
                             ensure_ascii=False))
        else:
            print("略過：harness.config.json 設了 \"design\": false（這個專案沒有圖形介面）。")
        return 0

    try:
        data = tokens.load(root, settings)
    except tokens.TokenError as exc:
        if args.json:
            print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False))
        else:
            print(f"❌ {exc}", file=sys.stderr)
        return 1

    problems = tokens.check_structure(data)
    results = [] if problems else tokens.check_contrast(data)
    failures = [r for r in results if not r["passed"]]

    if args.json:
        payload = {
            "ok": not problems and not failures,
            "source": settings["tokens"],
            "pairs_checked": len(results),
            # 未確認不影響 ok：它是流程提醒，不是設計錯誤。
            "confirmed": settings["confirmed"],
        }
        if problems:
            payload["structure_problems"] = problems
        if failures:
            payload["contrast_failures"] = failures
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print(f"===== 設計 token 檢查（{settings['tokens']}）=====")

    if problems:
        print(f"❌ 結構有 {len(problems)} 個問題：", file=sys.stderr)
        for problem in problems:
            print(f"   - {problem}", file=sys.stderr)
        print("結構不完整時不檢查對比度——先把角色補齊，算出來的數字才有意義。", file=sys.stderr)
        print("===== 結束 =====")
        return 1

    if args.show:
        for r in results:
            mark = "  " if r["passed"] else "❌"
            print(f"{mark} {r['theme']:5} {r['foreground']:17} on {r['background']:14} "
                  f"{r['ratio']:5.2f}（{LEVEL_LABEL[r['level']]}需 {r['required']}）")

    if failures:
        print(f"❌ {len(failures)}/{len(results)} 組配對未達 WCAG AA：", file=sys.stderr)
        for r in failures:
            print(f"   - {r['theme']} 主題：`{r['foreground']}` 畫在 `{r['background']}` 上 "
                  f"只有 {r['ratio']}，{LEVEL_LABEL[r['level']]}需要 {r['required']}",
                  file=sys.stderr)
        print("   對比度不足是**看得見卻很難用眼睛判斷**的缺陷：差 0.01 跟差很多，"
              "看起來一模一樣。", file=sys.stderr)
        print("   調整 design.tokens.json 裡對應角色的值，不要改門檻。", file=sys.stderr)
        print("===== 結束 =====")
        return 1

    if not settings["confirmed"]:
        print()
        print("⚠️ 這份設計還沒有人跟使用者確認過。")
        print("   模板附了一份預設色票，所以 clone 下來的專案會**默默繼承**一套美學——")
        print("   而美學是使用者的決定，不是模板的。派工任何前端/GUI task 之前，")
        print("   Orchestrator 要先問：要套用模板預設，還是你有自己的設計規範？")
        print("   問過之後在 harness.config.json 設 `\"design\": { \"confirmed\": true }`。")
        print("   （這不是錯誤，也刻意不讓它變成 CI 紅燈——紅燈只會逼人隨手填 true")
        print("   　而不是真的去問，那比沒有檢查更糟。）")
        print()

    # 餘裕最小的那一組值得講出來：卡在門檻邊緣的配對，下次有人微調顏色就會跌破。
    tightest = min(results, key=lambda r: r["ratio"] / r["required"])
    print(f"結構完整，{len(results)} 組配對全部達 WCAG AA。")
    print(f"（餘裕最小：{tightest['theme']} 主題的 `{tightest['foreground']}` 畫在 "
          f"`{tightest['background']}` 上 = {tightest['ratio']}，需 {tightest['required']}）")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
