#!/usr/bin/env python3
"""lock-tests.py — 掃描 tests/public/ 產生 .harness/locked-tests.list

在此腳本之前，「鎖定公開測試」只存在於 docs/implementer-verifier-workflow.md
的一句文字敘述，沒有任何程式碼真的去產生 .harness/locked-tests.list——
guard-hidden-tests.py 雖然會讀這份清單，但沒人寫入它，等於防作弊機制的
關鍵一步從未真正落地。

由 verifier-test-writer 在完成當次任務的公開測試後執行本腳本。每次執行是
「覆寫」而非累加：只鎖定當下 tests/public/ 底下真實存在的檔案，避免舊任務
殘留的路徑，誤鎖到之後合理被刪除/搬移的檔案。

2026-09-10（對應 docs/history/improvement-suggestions.md 的 P1-2）：清單從「一行一個
路徑」改成「一行 `<sha256>  <路徑>`」。原因是舊格式只記路徑，只要有任何一條
繞過 hook 的路徑成功（實測存在多條），公開測試被改了之後沒有任何人會發現，
verifier-reviewer 也無從知道它跑的公開測試還是不是檢驗者當初寫的那份。
有了雜湊，`scripts/verify-locks.py` 就能在驗收時做事後稽核——這條防線不需要
攔截任何動作，因此不受 hook 是否被繞過、是否根本沒被執行到影響。

寫入格式：一行一個 `<sha256>  <相對路徑>`（兩個空格分隔，比照 sha256sum），
路徑一律用正斜線（與 guard-hidden-tests.py 的比對格式一致），
無副檔名限制、忽略 .gitkeep。
"""
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

IGNORE_NAMES = {".gitkeep"}


def _repo_root() -> Path:
    """repo 根目錄。與 guard-hidden-tests.py 用同一套判斷，確保寫進清單的
    路徑跟 hook 比對時算出來的路徑是同一個基準。"""
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


REPO_ROOT = _repo_root()
LOCKED_LIST = REPO_ROOT / ".harness" / "locked-tests.list"


def public_dirs():
    """公開測試目錄。來自 harness.config.json 的 `public_test_paths`
    （預設 ["tests/public"]），可以有多個，也支援 monorepo 的萬用字元。"""
    config = harness_config.load(REPO_ROOT)
    dirs = []
    for pattern in config["public_test_paths"]:
        if "*" in pattern or "?" in pattern:
            dirs.extend(sorted(p for p in REPO_ROOT.glob(pattern) if p.is_dir()))
        else:
            candidate = REPO_ROOT / pattern
            if candidate.is_dir():
                dirs.append(candidate)
    return dirs, config["public_test_paths"]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    LOCKED_LIST.parent.mkdir(parents=True, exist_ok=True)

    dirs, patterns = public_dirs()

    if not dirs:
        print(f"找不到任何公開測試目錄（設定的路徑：{'、'.join(patterns)}），沒有東西可鎖定，")
        print("已將鎖定清單清空。若你的專案用別的測試目錄慣例，請在 harness.config.json")
        print("的 `public_test_paths` 指定（見 docs/multi-language-support.md）。")
        LOCKED_LIST.write_text("", encoding="utf-8")
        return

    entries = sorted(
        (p.relative_to(REPO_ROOT).as_posix(), sha256_of(p))
        for directory in dirs
        for p in directory.rglob("*")
        if p.is_file() and p.name not in IGNORE_NAMES
    )

    lines = [f"{digest}  {path}" for path, digest in entries]
    content = "\n".join(lines) + ("\n" if lines else "")
    LOCKED_LIST.write_text(content, encoding="utf-8")

    print(f"已鎖定 {len(entries)} 個公開測試檔案至 {LOCKED_LIST.relative_to(REPO_ROOT)}：")
    for path, digest in entries:
        print(f"  - {path}  （sha256: {digest[:12]}…）")
    if not entries:
        print(f"（{'、'.join(str(d.relative_to(REPO_ROOT)) for d in dirs)} 目前是空的，鎖定清單也已清空）")
    else:
        print()
        print("驗收前請執行 python3 scripts/verify-locks.py 比對雜湊，")
        print("確認這些公開測試在實作期間沒有被竄改。")


if __name__ == "__main__":
    main()
