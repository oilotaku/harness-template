#!/usr/bin/env python3
"""export-sealed-task.py — 把已封存的 task 匯出成 CI 驗收用的密文包

第四輪 P0：把驗收搬出 implementer 的執行環境。設計理由見 scripts/hidden_vault.py
的「CI 驗收密文包」一節與 docs/ci-verification.md。

本腳本做的事很小，小到值得寫清楚**它不做什麼**：

  做：把封存庫裡的密文複製一份到 repo 內的 `ci/sealed/<task_id>/`，
      並附上那個 task **簽過章的** manifest 項目（`entry.json`）。
  不做：不解密、不重新加密、不重新簽章。密文與簽章原封不動——
      這代表匯出這個動作本身不需要信任，也不可能產生一份「內容對但簽章是我自己簽的」
      密文包。沒有權杖就簽不出有效簽章，而權杖只在 Orchestrator 手上。

`--token` 是必要的，理由不是加解密（複製不需要），而是**匯出前要先驗一次**：
確認要帶上 CI 的這一份，此刻的簽章仍然有效。帶著一份已經被動過的 manifest 上 CI，
只會在 CI 上失敗一次，那時再回頭查是誰動的，成本高得多。

用法：
    python3 scripts/export-sealed-task.py --task-id T-001 --token <權杖>

匯出之後還有兩件事要人做（腳本不會、也不應該自己做）：
  1. 把 `ci/sealed/<task_id>/` commit 進**預設分支**——不是 implementer 的分支。
     驗收 workflow 從預設分支讀密文包，這是整個設計的信任根。
  2. 把權杖存成 GitHub 的 repository secret `HARNESS_VERIFY_TOKENS`
     （格式見下方輸出）。存進去之後就再也讀不出來，這正是重點。
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hidden_vault as vault  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SECRET_NAME = "HARNESS_VERIFY_TOKENS"


def export(root: Path, task_id: str, token: str, quiet: bool = False,
           expect_baseline: bool = True) -> int:
    """把一個已封存的 task 匯出成密文包。回傳 exit code。

    `expect_baseline=False` 是給「封存當下順手匯出」用的：那個時間點基線執行
    還沒發生，警告「還沒做基線」只會每次都出現，而每次都出現的警告等於沒有警告。
    """
    # 密文包是**寫進 repo 裡**的，所以 task_id 會變成路徑的一段。
    # 帶分隔符號的 task_id 會讓它寫到 ci/sealed/ 以外的地方，那是一個不該存在的開口。
    if task_id in ("", ".", "..") or any(sep in task_id for sep in ("/", "\\")):
        print(
            f"task_id「{task_id}」不能當成目錄名（不可含路徑分隔符號）。"
            "請用 T-001 這種形式，中文寫在 task-spec 的標題裡。",
            file=sys.stderr,
        )
        return 2

    manifest = vault.load_manifest(root)

    if manifest.get("version") != vault.MANIFEST_VERSION:
        print(
            f"manifest 版本是 {manifest.get('version')}，需要版本 {vault.MANIFEST_VERSION}"
            "（有簽章的格式）。請重新封存。",
            file=sys.stderr,
        )
        return 2

    entry = (manifest.get("tasks") or {}).get(task_id)
    if not entry:
        print(f"manifest 裡沒有 task「{task_id}」。", file=sys.stderr)
        return 2

    if vault.is_discarded(entry):
        print(
            f"task「{task_id}」的封存已經被作廢（權杖遺失），沒有可以匯出的密文。",
            file=sys.stderr,
        )
        return 2

    if vault.token_fingerprint(token) != entry.get("token_sha256"):
        print("權杖錯誤：指紋與封存當下記錄的不符，拒絕匯出。", file=sys.stderr)
        return 2

    if not vault.verify_entry(entry, token):
        print(
            "manifest 項目的簽章不符：manifest 在封存之後被改過（或簽章被拿掉）。\n"
            "這本身就是可疑訊號，不要匯出——請先查清楚，不要當成環境問題重新封存了事。",
            file=sys.stderr,
        )
        return 2

    task_dir = Path(entry["task_dir"])
    if not task_dir.is_dir():
        print(f"找不到封存目錄「{task_dir}」，封存內容可能已被刪除或搬移。", file=sys.stderr)
        return 2

    target = vault.ci_task_dir(root, task_id)
    if target.exists():
        # 重新匯出是合法的（重新封存之後就該重匯），但舊密文必須整批清掉——
        # 留下舊權杖才解得開的殘檔，會在 CI 上變成一半成功一半失敗。
        shutil.rmtree(target)
    target.mkdir(parents=True)

    copied = []
    for item in entry.get("files", []):
        source = task_dir / (item["path"] + ".enc")
        if not source.is_file():
            print(f"封存檔案遺失：{item['path']}", file=sys.stderr)
            shutil.rmtree(target, ignore_errors=True)
            return 2
        destination = target / (item["path"] + ".enc")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(item["path"])

    payload = {
        "task_id": task_id,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # 原封不動的簽過章的項目。CI 端驗的就是這個簽章，所以這裡不能「整理」它——
        # 少一個欄位、改一個空白，簽章就對不上了。
        "entry": entry,
    }
    (target / vault.CI_ENTRY_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if quiet:
        return 0

    relative = target.relative_to(root).as_posix()
    print(f"已匯出 {len(copied)} 個密文檔案到 {relative}/")
    for path in copied:
        print(f"  - {path}.enc")
    print(f"  - {vault.CI_ENTRY_NAME}（簽過章的 manifest 項目，CI 端驗的就是它）")
    if expect_baseline and not entry.get("baseline"):
        print("⚠️ 這個 task 還沒有做過基線執行——CI 上會照樣執行，但「這份隱藏測試會紅」")
        print("   這件事沒有任何人證明過。請先跑一次 --baseline 再匯出。")
    print()
    print("接下來要人做的兩件事（腳本不會自己做，也不應該）：")
    print(f"  1. 把 {relative}/ commit 進**預設分支**（不是 implementer 的分支）。")
    print("     驗收 workflow 從預設分支讀密文包，那是這個設計的信任根。")
    print(f"  2. 把權杖存成 repository secret `{SECRET_NAME}`，每行一個 task：")
    print()
    print("     " + "-" * 50)
    print(f"     {task_id} <權杖>")
    print("     " + "-" * 50)
    print()
    print("     已經有這個 secret 的話是**整份覆寫**，記得把其他 task 那幾行一起帶上。")
    print("     設定位置：Settings → Secrets and variables → Actions → New repository secret")
    print()
    print("密文包進 repo 是安全的：implementer 讀得到，但沒有權杖解不開；")
    print("改得動，但改了簽章就對不上。權杖不在 repo 裡，它在 secret 裡。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把已封存的 task 匯出成 CI 驗收用的密文包")
    parser.add_argument("--task-id", required=True, help="要匯出的 task_id")
    parser.add_argument("--token", required=True, help="封存時產生的執行權杖（用來驗簽，不用來解密）")
    args = parser.parse_args()

    root = vault.repo_root()
    print("===== 匯出 CI 驗收密文包 =====")
    code = export(root, args.task_id, args.token)
    print("===== 結束 =====")
    return code


if __name__ == "__main__":
    sys.exit(main())
