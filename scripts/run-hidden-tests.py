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

import attempts  # noqa: E402
import hidden_vault as vault  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


# 測試指令「一個測試都沒跑到」卻回傳 0 的常見輸出特徵。
# 這件事在這套機制裡特別危險：verifier-reviewer 會把 exit 0 讀成「隱藏測試全過」，
# 於是一個什麼都沒驗到的實作就這樣通過驗收。實測觸發過的例子：
# `unittest discover` 不會遞迴進沒有 __init__.py 的子目錄，檔案放巢狀就變成 Ran 0 tests。
ZERO_TEST_SIGNATURES = (
    "ran 0 tests",  # unittest
    "no tests ran",  # pytest
    "collected 0 items",  # pytest
    "no tests found",  # 多數 runner
    "no test files",  # go test
    "no test files found",  # vitest
    "0 passing",  # mocha
)


def looks_like_zero_tests(output: str) -> bool:
    lowered = output.lower()
    return any(signature in lowered for signature in ZERO_TEST_SIGNATURES)


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


def _record_attempt(task_id: str, passed: bool, note=None) -> None:
    """把這一次的驗收結果記進 .harness/attempts.json（P3-5）。

    由**執行測試的這一方**記錄，不是由被測的那一方——停損規則靠 implementer
    自我申報的話，在它最該生效的那一輪（實作者卡住、開始亂試）最不會生效。

    記錄失敗不影響驗收結果：這是輔助資訊，唯讀檔案系統不該讓一次合法的驗收
    變成錯誤。所以這裡吞掉例外，只印一行提示。
    """
    try:
        path = attempts.record(task_id, passed, note=note)
        info = attempts.summary(task_id)
    except Exception as exc:  # noqa: BLE001 — 記錄失敗不該中斷驗收
        print(f"（嘗試次數未能記錄：{type(exc).__name__}: {exc}）")
        return

    if path is None:
        print("（嘗試次數未能寫入 .harness/attempts.json，本次結果不會計入停損判斷）")
        return

    if info["should_stop"]:
        print(f"⚠️ 這個 task 已連續失敗 {info['consecutive_failures']} 次"
              f"（門檻 {info['threshold']}）——依停損規則，Orchestrator 應該介入"
              "檢視 task-spec 是否有問題，而不是再派一輪。")
    for warning in info["warnings"]:
        print(f"（{warning}）")


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

        # 先把指令範本斷詞，再逐一代換 placeholder——順序反過來的話，
        # 路徑裡只要有空白（Windows 的 C:\\Program Files\\...）就會被 shlex 拆成兩個參數，
        # 反斜線也會被當成跳脫字元吃掉。範本本身不含空白路徑，所以先斷詞是安全的。
        substitutions = {
            "dir": str(workdir),
            "repo": str(root),
            "python": sys.executable,
        }
        argv = [token.format(**substitutions) for token in shlex.split(info["test_command"])]
        command = " ".join(argv)
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

        # 這裡要捕捉輸出而不是直接串到主控台，因為下面要檢查「有沒有真的跑到測試」；
        # 捕捉後原樣印出來，verifier 看到的內容不變。
        result = subprocess.run(
            argv,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (result.stdout or "") + (result.stderr or "")
        print(output.rstrip())

        print("-" * 60)
        if result.returncode == 0 and looks_like_zero_tests(output):
            # exit 0 但一個測試都沒跑到——這是最危險的假通過，一定要當成失敗。
            print("狀態：**不通過** —— 測試指令回傳成功，但輸出顯示一個測試都沒跑到。")
            print(f"（本次解密了 {len(info.get('files', []))} 個隱藏測試檔案，卻沒有任何測試被執行）")
            print("常見原因：")
            print("  - 測試檔案放在暫存區的子目錄裡，而 `unittest discover` 不會遞迴進")
            print("    沒有 __init__.py 的子目錄——把隱藏測試平鋪在暫存區第一層即可")
            print("  - harness.config.json 的 hidden_test_command 檔名樣式跟實際檔名對不上")
            print("這不是「通過」，請當成驗收未完成處理，修好之後重新封存再跑一次。")
            _record_attempt(args.task_id, False, "零測試假通過")
            return 1
        if result.returncode == 0:
            print("狀態：隱藏測試全部通過。")
            print(f"（解密了 {len(info.get('files', []))} 個隱藏測試檔案；"
                  "請順帶確認上面的測試數量看起來合理）")
            _record_attempt(args.task_id, True)
            return 0
        print(f"狀態：隱藏測試未全部通過（測試指令 exit code = {result.returncode}）。")
        print("請把失敗的測試名稱與訊息寫進驗收報告；不要把隱藏測試的原始碼貼進報告。")
        _record_attempt(args.task_id, False, f"測試指令 exit code = {result.returncode}")
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        print("（已刪除解密用的暫存目錄）")
        print("===== 結束 =====")


if __name__ == "__main__":
    sys.exit(main())
