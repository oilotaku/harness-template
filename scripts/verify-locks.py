#!/usr/bin/env python3
"""verify-locks.py — 事後稽核：已鎖定的公開測試有沒有被竄改

對應 docs/history/improvement-suggestions.md 的 P1-2。

為什麼需要這支：`guard-hidden-tests.py` 是「事前攔截」，而事前攔截有兩個
先天弱點——(1) 它靠列舉路徑寫法來判斷，總會有沒想到的繞法；(2) 它是 hook，
只要沒被執行到（找不到 python3、hook 設定被改掉、工作目錄不對），
防護就等於不存在，而且沒有任何訊號。

本腳本不攔截任何動作，只做一件事：把 `.harness/locked-tests.list` 裡記錄的
sha256 跟磁碟上的實際內容重新比對。因此不管 hook 有沒有被繞過、有沒有真的
執行，只要公開測試在鎖定之後被動過，這裡就會發現。

由 `verifier-reviewer` 在驗收的第一步執行（見 .claude/agents/verifier-reviewer.md）。

exit code：
  0 = 全部相符，公開測試與鎖定當下一致
  1 = 偵測到竄改或檔案遺失 —— 驗收應直接判定「不通過」
  2 = 無法驗證（沒有鎖定清單，或清單是沒有雜湊的舊格式）
      —— 不代表安全，只代表這次驗收沒有公開測試鎖定保護，要在報告裡註記
"""
import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


REPO_ROOT = _repo_root()
LOCKED_LIST = REPO_ROOT / ".harness" / "locked-tests.list"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_entries(text: str):
    """回傳 (entries, legacy_paths)。

    entries：[(sha256, 路徑)]，新格式，可以驗證
    legacy_paths：[路徑]，舊格式（只有路徑、沒有雜湊），無法驗證
    """
    entries = []
    legacy = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and SHA256_PATTERN.match(parts[0]):
            entries.append((parts[0].lower(), parts[1].strip().replace("\\", "/")))
        else:
            legacy.append(line.replace("\\", "/"))
    return entries, legacy


def main() -> int:
    print("===== 公開測試鎖定稽核 =====")

    if not LOCKED_LIST.exists():
        print("⚠️ 找不到 .harness/locked-tests.list。")
        print()
        print("這代表：本次驗收沒有公開測試鎖定保護——沒有任何依據可以證明")
        print("implementer 跑的公開測試，跟 verifier-test-writer 當初寫的是同一份。")
        print("可能原因：verifier-test-writer 沒有執行 scripts/lock-tests.py，")
        print("或這是一份新 clone / CI 環境（.harness/ 已被 .gitignore 排除）。")
        print("請在驗收報告裡明確註記這件事，不要當成「沒問題」。")
        print("===== 結束 =====")
        return 2

    entries, legacy = parse_entries(LOCKED_LIST.read_text(encoding="utf-8"))

    if not entries and not legacy:
        print("鎖定清單是空的（tests/public/ 沒有檔案被鎖定）。")
        print("若這個 task 本來就沒有公開測試，屬正常；否則請確認 lock-tests.py 是否執行過。")
        print("===== 結束 =====")
        return 2

    tampered = []
    missing = []
    ok = []

    for expected, rel_path in entries:
        target = REPO_ROOT / rel_path
        if not target.is_file():
            missing.append(rel_path)
            continue
        actual = sha256_of(target)
        if actual != expected:
            tampered.append((rel_path, expected, actual))
        else:
            ok.append(rel_path)

    for rel_path in ok:
        print(f"✅ 相符：{rel_path}")
    for rel_path in missing:
        print(f"❌ 檔案遺失：{rel_path}（鎖定當下存在，現在找不到）")
    for rel_path, expected, actual in tampered:
        print(f"❌ 內容被竄改：{rel_path}")
        print(f"    鎖定當下 sha256：{expected}")
        print(f"    目前     sha256：{actual}")
    for rel_path in legacy:
        print(f"⚠️ 無雜湊、無法驗證（舊格式清單）：{rel_path}")

    print()
    if tampered or missing:
        print(f"狀態：不通過 —— 竄改 {len(tampered)} 個、遺失 {len(missing)} 個。")
        print("公開測試在鎖定之後被動過，本次驗收必須直接判定「不通過」，")
        print("並在驗收報告的「可疑模式檢查」裡記下「修改/刪除測試」為「是」。")
        print("===== 結束 =====")
        return 1

    if legacy:
        print(f"狀態：部分無法驗證 —— {len(legacy)} 個項目是舊格式（沒有雜湊）。")
        print("請重新執行 scripts/lock-tests.py 產生含雜湊的清單；")
        print("在那之前，這些檔案沒有事後稽核保護，要在驗收報告裡註記。")
        print("===== 結束 =====")
        return 2

    print(f"狀態：正常 —— {len(ok)} 個公開測試與鎖定當下完全一致。")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
