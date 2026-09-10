#!/usr/bin/env python3
"""run-hidden-tests.py — 用執行權杖解密並執行已封存的隱藏測試

對應 docs/improvement-suggestions.md 的 P1-1。設計理由見 scripts/hidden_vault.py。

由 **verifier-reviewer** 在驗收時執行；權杖由 Orchestrator 在派工時提供。

用法：
    python3 scripts/run-hidden-tests.py --list
    python3 scripts/run-hidden-tests.py --task-id DEMO-001 --token <權杖>

exit code：
    0 = 隱藏測試全部通過
    1 = 有隱藏測試失敗（驗收判定不通過的依據之一）
    2 = 無法執行：沒有這個 task、權杖錯誤、或封存檔案被竄改

執行流程：
    1. 比對權杖指紋（manifest 只存指紋，存不了也不該存權杖本身）
    2. 解密到一個權限 0700 的暫存目錄，並比對每個檔案的明文 sha256
    3. 以 repo 根目錄為工作目錄執行測試指令
    4. 不論成功失敗，最後一定刪掉暫存目錄

隱藏測試的撰寫契約（verifier-test-writer 要注意）：
    測試檔案執行時**不在** `tests/hidden/` 底下，而是在一個隨機命名的暫存目錄，
    所以不可以用 `Path(__file__).parents[n]` 這種相對位置去找實作程式碼。
    本腳本保證：
      - 工作目錄（cwd）是 repo 根目錄
      - PYTHONPATH 含 repo 根目錄
      - 環境變數 HARNESS_REPO_ROOT 指向 repo 根目錄
    要找實作就用這三者其中之一。

已知殘留限制（誠實記載，不要當成已解決）：
    解密後的明文在測試執行期間確實存在於暫存目錄，同一個使用者身分的行程
    在那短暫時間內是掃得到的。要完全消除，需要把測試放進獨立的使用者/容器
    執行，超出本模板的範圍。
"""
import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hidden_vault as vault  # noqa: E402


def list_tasks(manifest: dict) -> int:
    tasks = manifest.get("tasks", {})
    print("===== 已封存的隱藏測試 =====")
    if not tasks:
        print("（目前沒有任何已封存的 task；verifier-test-writer 尚未執行 seal-hidden-tests.py）")
        print("===== 結束 =====")
        return 0
    for task_id, info in sorted(tasks.items()):
        print(f"- {task_id}：{len(info.get('files', []))} 個檔案，封存於 {info.get('sealed_at', '未知時間')}")
        print(f"    測試指令：{info.get('test_command')}")
    print()
    print("執行需要對應的權杖：")
    print("  python3 scripts/run-hidden-tests.py --task-id <task_id> --token <權杖>")
    print("===== 結束 =====")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="解密並執行已封存的隱藏測試")
    parser.add_argument("--task-id", help="要執行的 task_id")
    parser.add_argument("--token", help="封存時產生的執行權杖")
    parser.add_argument("--list", action="store_true", help="列出已封存的 task（不需要權杖）")
    args = parser.parse_args()

    root = vault.repo_root()
    manifest = vault.load_manifest(root)

    if args.list:
        return list_tasks(manifest)

    if not args.task_id or not args.token:
        print("需要 --task-id 與 --token（或用 --list 查看已封存的 task）。", file=sys.stderr)
        return 2

    info = manifest.get("tasks", {}).get(args.task_id)
    if not info:
        print(f"manifest 裡沒有 task「{args.task_id}」。", file=sys.stderr)
        print("可用 --list 查看有哪些已封存的 task。", file=sys.stderr)
        return 2

    if vault.token_fingerprint(args.token) != info.get("token_sha256"):
        print("權杖錯誤：指紋與封存當下記錄的不符，拒絕解密。", file=sys.stderr)
        print(
            "如果你是 implementer——你本來就不該有這個權杖，隱藏測試對你不可見是刻意設計。",
            file=sys.stderr,
        )
        return 2

    task_dir = Path(info["task_dir"])
    if not task_dir.exists():
        print(f"找不到封存目錄「{task_dir}」，封存內容可能已被刪除或搬移。", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="harness-hidden-"))
    try:
        try:
            workdir.chmod(0o700)
        except OSError:
            pass

        problems = []
        for entry in info.get("files", []):
            source = task_dir / (entry["path"] + ".enc")
            if not source.is_file():
                problems.append(f"封存檔案遺失：{entry['path']}")
                continue
            plaintext = vault.transform(source.read_bytes(), args.token)
            if vault.sha256_bytes(plaintext) != entry["sha256"]:
                problems.append(f"封存檔案被竄改（明文 sha256 不符）：{entry['path']}")
                continue
            target = workdir / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(plaintext)

        if problems:
            print("===== 隱藏測試無法執行 =====", file=sys.stderr)
            for problem in problems:
                print(f"❌ {problem}", file=sys.stderr)
            print(
                "封存內容與 manifest 不一致，這本身就是可疑訊號，"
                "請在驗收報告裡明確記載，不要當成單純的環境問題。",
                file=sys.stderr,
            )
            return 2

        # 路徑一律用正斜線再帶進指令：Windows 的反斜線會被 shlex.split 當成
        # 跳脫字元吃掉，而 Python 與絕大多數工具在 Windows 上都吃正斜線。
        command = info["test_command"].format(
            dir=str(workdir).replace("\\", "/"), repo=str(root).replace("\\", "/")
        )
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(root)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
            ),
            "HARNESS_REPO_ROOT": str(root),
        }

        print("===== 執行隱藏測試 =====")
        print(f"task_id：{args.task_id}")
        print(f"檔案數：{len(info.get('files', []))}")
        print(f"指令：{command}")
        print("（工作目錄為 repo 根目錄，解密後的暫存目錄在測試結束後會立刻刪除）")
        print("-" * 60)

        result = subprocess.run(shlex.split(command), cwd=str(root), env=env)

        print("-" * 60)
        if result.returncode == 0:
            print("狀態：隱藏測試全部通過。")
            return 0
        print(f"狀態：隱藏測試未全部通過（測試指令 exit code = {result.returncode}）。")
        print("請把失敗的測試名稱與訊息寫進驗收報告；不要把隱藏測試的原始碼貼進報告。")
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        print("（已刪除解密用的暫存目錄）")
        print("===== 結束 =====")


if __name__ == "__main__":
    sys.exit(main())
