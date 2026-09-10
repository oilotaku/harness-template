#!/usr/bin/env python3
"""seal-hidden-tests.py — 把 tests/hidden/ 的隱藏測試封存到 repo 之外並加密

對應 docs/improvement-suggestions.md 的 P1-1。設計理由（三層隔離、為什麼要加密、
這裡的「加密」宣稱到哪裡為止）全部寫在 scripts/hidden_vault.py 的模組說明，
不在這裡重複。

由 **verifier-test-writer** 在寫完隱藏測試之後執行，是它交付流程的最後一步。

用法：
    python3 scripts/seal-hidden-tests.py --task-id DEMO-001
    python3 scripts/seal-hidden-tests.py --task-id T2 --test-command "npx vitest run {dir}"

執行後：
  1. tests/hidden/ 底下的檔案被加密搬到封存庫（預設 <repo 的父目錄>/.harness-hidden/<repo 名>/），
     工作目錄裡不再有任何隱藏測試的明文
  2. .harness/hidden-manifest.json 記下檔案清單、明文 sha256、權杖指紋、測試指令
  3. 印出**只會出現這一次**的執行權杖

權杖處理規則（重要）：
  - verifier-test-writer 把權杖交回 Orchestrator，自己不要留存
  - Orchestrator 只在派工 verifier-reviewer 時，把權杖放進那個 session 的提示詞
  - 絕對不可以出現在 task-spec、公開測試、驗收報告、commit 訊息裡，
    也不可以傳給任何 implementer——權杖一旦流到 implementer 手上，
    隱藏測試就退化成「可以反覆查詢的 oracle」，等於白做（P0-4）

權杖遺失時沒有救援路徑（manifest 只存指紋），只能重新產生一份隱藏測試再封存一次。
這是刻意的：留後門等於留繞過方式。
"""
import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hidden_vault as vault  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

IGNORE_NAMES = {".gitkeep", "README.md"}


def collect_files(staging_dirs):
    """從所有暫存區蒐集隱藏測試，回傳 [(來源路徑, 封存內的相對路徑)]。

    封存內的相對路徑是「相對於它自己的暫存區」，因為解密後要平鋪回暫存目錄
    給測試指令用。設定多個暫存區時可能撞名，撞名一定要明確報錯——
    默默覆蓋等於有測試消失，而測試消失在這套機制裡是最不能接受的失敗。
    """
    collected = []
    seen = {}
    collisions = []
    for staging in staging_dirs:
        if not staging.is_dir():
            continue
        for source in sorted(staging.rglob("*")):
            if not source.is_file() or source.name in IGNORE_NAMES:
                continue
            relative = source.relative_to(staging).as_posix()
            if relative in seen:
                collisions.append((relative, seen[relative], source))
                continue
            seen[relative] = source
            collected.append((source, relative))
    return collected, collisions


def main() -> int:
    parser = argparse.ArgumentParser(description="封存隱藏測試到 repo 之外並加密")
    parser.add_argument("--task-id", required=True, help="這批隱藏測試對應的 task_id")
    parser.add_argument(
        "--test-command",
        default=None,
        help=(
            "執行這批隱藏測試的指令，{dir} 會被換成解密後的暫存目錄、"
            "{repo} 換成 repo 根目錄、{python} 換成執行 runner 的直譯器。"
            "沒指定時用 harness.config.json 的 hidden_test_command"
            f"（目前預設：{vault.DEFAULT_TEST_COMMAND}）"
        ),
    )
    args = parser.parse_args()

    root = vault.repo_root()
    staging_dirs = vault.staging_dirs(root)
    staging_labels = "、".join(
        str(d.relative_to(root)) if d.is_relative_to(root) else str(d) for d in staging_dirs
    ) or "（設定裡沒有任何隱藏測試路徑）"

    print("===== 封存隱藏測試 =====")

    files, collisions = collect_files(staging_dirs)

    if collisions:
        print("拒絕封存：不同暫存區出現同名的隱藏測試，封存後會互相覆蓋。", file=sys.stderr)
        for relative, first, second in collisions:
            print(f"  「{relative}」同時來自 {first} 與 {second}", file=sys.stderr)
        print("請改名，或把 harness.config.json 的 hidden_test_paths 收斂成一個目錄。", file=sys.stderr)
        return 1

    if not files:
        print(
            f"{staging_labels} 底下沒有隱藏測試檔案（.gitkeep / README.md 不算）。\n"
            "請先寫好隱藏測試再執行本腳本。\n"
            "若你的專案用別的測試目錄慣例，請在 harness.config.json 的 `hidden_test_paths`\n"
            "指定（見 docs/multi-language-support.md）。",
            file=sys.stderr,
        )
        return 1

    try:
        vault_dir = vault.resolve_vault_dir(root)
        vault.assert_outside_repo(vault_dir, root)
    except ValueError as exc:
        print(f"拒絕封存：{exc}", file=sys.stderr)
        return 1

    task_dir = vault_dir / args.task_id
    if task_dir.exists():
        # 重新封存同一個 task 是合法的（例如測試本身寫錯要修），但舊的密文必須整批
        # 清掉，否則會留下用舊權杖才解得開的殘檔，之後 run 起來一半成功一半失敗。
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)

    try:
        task_dir.chmod(0o700)
    except OSError:
        pass  # Windows 上沒有 POSIX 權限語意，不是錯誤

    token = vault.new_token()
    entries = []

    # 順序很重要：先把密文全部寫完，再寫 manifest，最後才刪明文。
    # 反過來（邊寫邊刪）一旦中途失敗，會留下「明文已刪、但沒有 manifest
    # 所以永遠解不開」的狀態，隱藏測試等於直接消失。
    for source, relative in files:
        plaintext = source.read_bytes()
        target = task_dir / (relative + ".enc")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(vault.transform(plaintext, token))
        entries.append(
            {
                "path": relative,
                "sha256": vault.sha256_bytes(plaintext),
                "bytes": len(plaintext),
            }
        )

    manifest = vault.load_manifest(root)
    manifest["version"] = vault.MANIFEST_VERSION
    manifest["tasks"][args.task_id] = {
        "sealed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault_dir": str(vault_dir),
        "task_dir": str(task_dir),
        "test_command": args.test_command or vault.test_command(root),
        "token_sha256": vault.token_fingerprint(token),
        "files": entries,
    }
    vault.save_manifest(manifest, root)

    # 密文與 manifest 都已落地，現在才刪掉工作目錄裡的明文。
    for source, _relative in files:
        source.unlink()

    # 把暫存區裡剩下的空目錄清掉，但保留暫存區本身與說明檔，
    # 讓下一個 task 還有地方可以放。
    for staging in staging_dirs:
        if not staging.is_dir():
            continue
        for leftover in sorted(staging.rglob("*"), reverse=True):
            if leftover.is_dir() and not any(leftover.iterdir()):
                leftover.rmdir()

    print(f"task_id：{args.task_id}")
    print(f"來源暫存區：{staging_labels}")
    print(f"已封存 {len(entries)} 個隱藏測試檔案：")
    for entry in entries:
        print(f"  - {entry['path']}（{entry['bytes']} bytes）")
    print(f"封存位置（repo 之外、內容已加密）：{task_dir}")
    print(f"manifest：{vault.manifest_path(root).relative_to(root)}")
    print()
    print("工作目錄裡已經沒有隱藏測試的明文——implementer 用任何工具、任何路徑寫法")
    print("都讀不到，即使自己寫腳本去讀封存檔案，拿到的也是密文。")
    print()
    print("=" * 60)
    print(f"執行權杖（只會出現這一次）：{token}")
    print("=" * 60)
    print()
    print("接下來：")
    print("  1. 把這串權杖交回 Orchestrator，你自己不要留存。")
    print("  2. Orchestrator 只在派工 verifier-reviewer 時把它放進那個 session。")
    print("  3. 絕對不要寫進 task-spec、公開測試、驗收報告或 commit 訊息，")
    print("     更不要交給任何 implementer——那等於把隱藏測試變成可查詢的 oracle。")
    print()
    print("verifier-reviewer 驗收時執行：")
    print(f"  python3 scripts/run-hidden-tests.py --task-id {args.task_id} --token <權杖>")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
