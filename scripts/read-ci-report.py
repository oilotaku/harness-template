#!/usr/bin/env python3
"""read-ci-report.py — 解開 CI 驗收產生的加密明細

第四輪 P0。由 **verifier-reviewer** 執行，權杖由 Orchestrator 在派工時提供。

CI 的 job log 只會有 exit code 與數量摘要，失敗的測試名稱與 assert 訊息一律
不進 log——log 與 artifact 任何有 repo 讀取權的人都看得到，包含 implementer，
而失敗明細本身就是題目的一部分。明細因此被加密成 artifact（`report.enc`），
金鑰由權杖推導。

用法：
    python3 scripts/read-ci-report.py --file report.enc --token <權杖>

exit code：
    0 = 解開了
    2 = 權杖不對、檔案不是這支機制產生的、或內容在產生之後被改過

**解開之後的內容不要貼進驗收報告、PR 留言或任何 implementer 讀得到的地方。**
驗收報告要寫的是「哪些驗收標準沒達成」，不是隱藏測試的原始碼與 assert 訊息。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hidden_vault as vault  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def main() -> int:
    parser = argparse.ArgumentParser(description="解開 CI 驗收產生的加密明細")
    parser.add_argument("--file", required=True, help=f"加密明細的路徑（通常是 {vault.CI_REPORT_NAME}）")
    parser.add_argument("--token", required=True, help="對應 task 的執行權杖")
    args = parser.parse_args()

    path = Path(args.file)
    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"讀不到 {path}：{exc}", file=sys.stderr)
        return 2

    try:
        text = vault.decrypt_report(data, args.token)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        print(
            "若你是 implementer——這份明細對你不可見是刻意設計，"
            "請只依 task-spec 與公開測試實作（黃金法則第 1 條）。",
            file=sys.stderr,
        )
        return 2

    print(text)
    print("-" * 60)
    print("提醒：上面的內容包含隱藏測試的失敗訊息。驗收報告只寫「哪些驗收標準沒達成」，")
    print("      不要把測試原始碼或 assert 訊息貼進任何 implementer 讀得到的地方。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
