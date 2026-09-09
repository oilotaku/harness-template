#!/usr/bin/env python3
"""lock-tests.py — 掃描 tests/public/ 產生 .harness/locked-tests.list

在此腳本之前，「鎖定公開測試」只存在於 docs/implementer-verifier-workflow.md
的一句文字敘述，沒有任何程式碼真的去產生 .harness/locked-tests.list——
guard-hidden-tests.py 雖然會讀這份清單，但沒人寫入它，等於防作弊機制的
關鍵一步從未真正落地。

由 verifier-test-writer 在完成當次任務的公開測試後執行本腳本。每次執行是
「覆寫」而非累加：只鎖定當下 tests/public/ 底下真實存在的檔案，避免舊任務
殘留的路徑，誤鎖到之後合理被刪除/搬移的檔案。

寫入格式：一行一個相對路徑，一律用正斜線（與 guard-hidden-tests.py 的
比對格式一致），無副檔名限制、忽略 .gitkeep。
"""
from pathlib import Path

PUBLIC_DIR = Path("tests/public")
LOCKED_LIST = Path(".harness/locked-tests.list")
IGNORE_NAMES = {".gitkeep"}


def main() -> None:
    LOCKED_LIST.parent.mkdir(exist_ok=True)

    if not PUBLIC_DIR.exists():
        print(f"找不到 {PUBLIC_DIR}/，沒有東西可鎖定，已將鎖定清單清空。")
        LOCKED_LIST.write_text("", encoding="utf-8")
        return

    paths = sorted(
        p.relative_to(".").as_posix()
        for p in PUBLIC_DIR.rglob("*")
        if p.is_file() and p.name not in IGNORE_NAMES
    )

    content = "\n".join(paths) + ("\n" if paths else "")
    LOCKED_LIST.write_text(content, encoding="utf-8")

    print(f"已鎖定 {len(paths)} 個公開測試檔案至 {LOCKED_LIST}：")
    for p in paths:
        print(f"  - {p}")
    if not paths:
        print("（tests/public/ 目前是空的，鎖定清單也已清空）")


if __name__ == "__main__":
    main()
