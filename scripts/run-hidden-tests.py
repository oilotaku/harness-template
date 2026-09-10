#!/usr/bin/env python3
"""run-hidden-tests.py — 用執行權杖驗簽、解密並執行已封存的隱藏測試

設計理由見 scripts/hidden_vault.py。

由 **verifier-reviewer** 在驗收時執行；權杖由 Orchestrator 在派工時提供。
由 **verifier-test-writer** 在封存後立刻以 `--baseline` 執行一次（第二輪 P1-6）。

用法：
    python3 scripts/run-hidden-tests.py --list
    python3 scripts/run-hidden-tests.py --task-id DEMO-001 --token <權杖>
    python3 scripts/run-hidden-tests.py --task-id DEMO-001 --token <權杖> --baseline

exit code：
    0 = 隱藏測試全部通過（--baseline 模式：測試如預期是紅的）
    1 = 有隱藏測試失敗（--baseline 模式：測試在沒有實作時就綠了，沒有鑑別力）
    2 = 無法執行：沒有這個 task、權杖錯誤、manifest 簽章不符、鎖定清單被動過、
        封存檔案被竄改、或 manifest 是舊版本需要重新封存

執行流程：
    1. manifest 版本必須是 2（舊版沒有簽章，一律要求重新封存，不相容）
    2. 比對權杖指紋
    3. **驗證 manifest 項目的 HMAC 簽章**——通過之前不信任任何其他欄位（第二輪 P0-6）
    4. 比對鎖定清單的 sha256（封存當下 vs 現在）——分得出「被刪」「被改」「本來就沒有」（P0-7）
    5. 解密到一個權限 0700 的暫存目錄，逐檔比對明文 sha256
    6. 以 repo 根目錄為工作目錄執行測試指令
    7. 不論成功失敗，最後一定刪掉暫存目錄

--baseline 模式的差別：
    - 不寫 attempts.json（這一跑不是 implementer 的嘗試）
    - 預期測試指令**非零**結束；全綠代表這份隱藏測試沒有鑑別力，exit 1
    - 結果寫進簽過章的 manifest：baseline = {ran_at, red, tests_seen}
    正式模式若發現 manifest 沒有 baseline，會印警告「這份隱藏測試從未被證明過會紅」，
    verifier-reviewer 要把這件事寫進報告。

隱藏測試的撰寫契約（verifier-test-writer 要注意）：
    測試檔案執行時**不在**暫存區底下，而是在一個隨機命名的暫存目錄，
    所以不可以用 `Path(__file__).parents[n]` 這種相對位置去找實作程式碼。
    本腳本保證：
      - 工作目錄（cwd）是 repo 根目錄
      - PYTHONPATH 含 repo 根目錄
      - 環境變數 HARNESS_REPO_ROOT 指向 repo 根目錄

已知殘留限制（誠實記載，不要當成已解決）：
    解密後的明文在測試執行期間確實存在於暫存目錄，同一個使用者身分的行程
    在那短暫時間內是掃得到的；權杖也會經過 Claude Code 的 transcript。
    要完全消除，需要獨立的使用者/容器，超出本模板的範圍。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import attempts  # noqa: E402
import hidden_vault as vault  # noqa: E402
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

# 從輸出裡估「跑了幾個測試」——只用來記進 baseline，解析不到就 None，不猜。
TEST_COUNT_PATTERNS = (
    re.compile(r"\bRan (\d+) tests?\b"),  # unittest
    re.compile(r"\b(\d+) (?:passed|failed)\b"),  # pytest / vitest
    re.compile(r"\b(\d+) (?:passing|failing)\b"),  # mocha
)


def looks_like_zero_tests(output: str) -> bool:
    lowered = output.lower()
    return any(signature in lowered for signature in ZERO_TEST_SIGNATURES)


def count_tests_seen(output: str):
    total = 0
    matched = False
    for pattern in TEST_COUNT_PATTERNS:
        for found in pattern.findall(output):
            total += int(found)
            matched = True
        if matched:
            return total
    return None


def list_tasks(manifest: dict) -> int:
    tasks = manifest.get("tasks", {})
    print("===== 已封存的隱藏測試 =====")
    if manifest.get("version") != vault.MANIFEST_VERSION:
        print(f"（manifest 版本 {manifest.get('version')} 不是 {vault.MANIFEST_VERSION}：沒有簽章，"
              "全部要重新封存才跑得動）")
    if not tasks:
        print("（目前沒有任何已封存的 task；verifier-test-writer 尚未執行 seal-hidden-tests.py）")
        print("===== 結束 =====")
        return 0
    for task_id, info in sorted(tasks.items()):
        baseline = info.get("baseline")
        status = "已證明會紅" if baseline and baseline.get("red") else "尚未做基線執行"
        print(f"- {task_id}：{len(info.get('files', []))} 個檔案，封存於 {info.get('sealed_at', '未知時間')}，{status}")
        print(f"    測試指令：{info.get('test_command')}")
    print()
    print("執行需要對應的權杖：")
    print("  python3 scripts/run-hidden-tests.py --task-id <task_id> --token <權杖>")
    print("===== 結束 =====")
    return 0


def _record_attempt(manifest: dict, entry: dict, task_id: str, passed: bool, token: str, note=None) -> None:
    """把這一次的驗收結果記進 .harness/attempts.json（第一輪 P3-5，第二輪 P3-9 加簽章）。

    由**執行測試的這一方**記錄，不是由被測的那一方。每筆紀錄用權杖推導的 HMAC 簽過；
    另外把「已記錄幾筆」寫進簽過章的 manifest——逐筆簽章抓得到改寫，抓不到
    **整筆刪掉**（把最後幾次失敗砍掉正是最有動機的那種竄改），只有簽過章的計數抓得到。
    帶權杖的 show-attempts 會兩個都驗。

    記錄失敗不影響驗收結果：這是輔助資訊，唯讀檔案系統不該讓一次合法的驗收
    變成錯誤。所以這裡吞掉例外，只印一行提示。
    """
    key = vault.mac_key(token)
    root = vault.repo_root()
    try:
        path = attempts.record(task_id, passed, note=note, mac_key=key)
        if path is not None:
            entry["attempts_recorded"] = int(entry.get("attempts_recorded") or 0) + 1
            entry["signature"] = vault.sign_entry(entry, token)
            manifest["tasks"][task_id] = entry
            vault.save_manifest(manifest, root)
        info = attempts.summary(task_id, mac_key=key, expected_total=entry.get("attempts_recorded"))
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
        print(f"⚠️ {warning}")


def check_locked_list(info: dict, root: Path):
    """封存當下的鎖定清單 sha256 vs 現在。回傳錯誤訊息或 None。"""
    recorded = info.get("locked_tests_sha256")
    current = vault.sha256_file(vault.locked_list_path(root))
    if recorded is None:
        return None  # 封存當下就沒有清單（舊流程）；由 verify-locks.py 自己提醒
    if current is None:
        return "鎖定清單（.harness/locked-tests.list）在封存之後被**刪除**了。"
    if current != recorded:
        return "鎖定清單（.harness/locked-tests.list）在封存之後被**改寫**了。"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="驗簽、解密並執行已封存的隱藏測試")
    parser.add_argument("--task-id", help="要執行的 task_id")
    parser.add_argument("--token", help="封存時產生的執行權杖")
    parser.add_argument("--list", action="store_true", help="列出已封存的 task（不需要權杖）")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="基線執行：由 verifier-test-writer 在封存後立刻跑，預期測試是紅的；不計入停損次數",
    )
    args = parser.parse_args()

    root = vault.repo_root()
    manifest = vault.load_manifest(root)

    if args.list:
        return list_tasks(manifest)

    if not args.task_id or not args.token:
        print("需要 --task-id 與 --token（或用 --list 查看已封存的 task）。", file=sys.stderr)
        return 2

    if manifest.get("version") != vault.MANIFEST_VERSION:
        print(
            f"manifest 版本是 {manifest.get('version')}，本 runner 只接受版本 {vault.MANIFEST_VERSION}"
            "（有簽章的格式）。舊版本沒有簽章、每檔共用 keystream，一律要求重新封存，"
            "刻意不相容——相容等於留一條「拿掉簽章就回到沒簽章」的路。",
            file=sys.stderr,
        )
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

    # 指紋對了之後、讀任何其他欄位之前，先驗簽。test_command / task_dir / files /
    # locked_tests_sha256 / baseline 都在簽章範圍內，改任何一個都會在這裡被擋。
    if not vault.verify_entry(info, args.token):
        print("===== 隱藏測試無法執行 =====", file=sys.stderr)
        print("❌ manifest 項目的簽章不符：manifest 在封存之後被改過（或簽章被拿掉）。", file=sys.stderr)
        print(
            "這本身就是可疑訊號——只有握有權杖的一方能產生有效簽章，而 implementer 沒有權杖。"
            "請在驗收報告裡明確記載，並判定本次驗收不通過；不要當成環境問題重新封存了事。",
            file=sys.stderr,
        )
        return 2

    lock_problem = check_locked_list(info, root)
    if lock_problem:
        print("===== 隱藏測試無法執行 =====", file=sys.stderr)
        print(f"❌ {lock_problem}", file=sys.stderr)
        print(
            "封存當下清單是存在的（sha256 已寫進簽過章的 manifest），所以這不是「從未鎖定」，"
            "是竄改。verify-locks.py 的 exit 2 在這種情況下要以本判定為準：驗收不通過。",
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
            plaintext = vault.transform(source.read_bytes(), args.token, entry["path"])
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

        argv = vault.render_test_command(
            info["test_command"],
            {"dir": str(workdir), "repo": str(root), "python": sys.executable},
        )
        command = " ".join(argv)
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(root)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
            ),
            "HARNESS_REPO_ROOT": str(root),
        }

        mode = "基線執行（預期是紅的）" if args.baseline else "驗收"
        print(f"===== 執行隱藏測試：{mode} =====")
        print(f"task_id：{args.task_id}")
        print(f"檔案數：{len(info.get('files', []))}（manifest 簽章已驗證）")
        print(f"指令：{command}")
        print("（工作目錄為 repo 根目錄，解密後的暫存目錄在測試結束後會立刻刪除）")
        if not args.baseline and not info.get("baseline"):
            print("⚠️ 這份隱藏測試從未被證明過會紅（沒有基線執行紀錄）——")
            print("   一份全部 assert True 的測試也會「全部通過」。請把這件事寫進驗收報告。")
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

        zero = result.returncode == 0 and looks_like_zero_tests(output)

        if args.baseline:
            return finish_baseline(manifest, info, args, root, result.returncode, zero, output)

        if zero:
            # exit 0 但一個測試都沒跑到——這是最危險的假通過，一定要當成失敗。
            print("狀態：**不通過** —— 測試指令回傳成功，但輸出顯示一個測試都沒跑到。")
            print(f"（本次解密了 {len(info.get('files', []))} 個隱藏測試檔案，卻沒有任何測試被執行）")
            print("常見原因：")
            print("  - 測試檔案放在暫存區的子目錄裡，而 `unittest discover` 不會遞迴進")
            print("    沒有 __init__.py 的子目錄——把隱藏測試平鋪在暫存區第一層即可")
            print("  - harness.config.json 的 hidden_test_command 檔名樣式跟實際檔名對不上")
            print("這不是「通過」，請當成驗收未完成處理，修好之後重新封存再跑一次。")
            _record_attempt(manifest, info, args.task_id,False, args.token, "零測試假通過")
            return 1
        if result.returncode == 0:
            print("狀態：隱藏測試全部通過。")
            print(f"（解密了 {len(info.get('files', []))} 個隱藏測試檔案；"
                  "請順帶確認上面的測試數量看起來合理）")
            _record_attempt(manifest, info, args.task_id,True, args.token)
            return 0
        print(f"狀態：隱藏測試未全部通過（測試指令 exit code = {result.returncode}）。")
        print("請把失敗的測試名稱與訊息寫進驗收報告；不要把隱藏測試的原始碼貼進報告。")
        _record_attempt(manifest, info, args.task_id,False, args.token, f"測試指令 exit code = {result.returncode}")
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        print("（已刪除解密用的暫存目錄）")
        print("===== 結束 =====")


def finish_baseline(manifest, info, args, root, returncode, zero, output) -> int:
    """--baseline 的收尾：不記 attempts，紅了才寫進 manifest。"""
    tests_seen = count_tests_seen(output)
    if zero or (returncode == 0 and tests_seen == 0):
        print("狀態：**基線無效** —— 一個測試都沒跑到，無法證明任何事。")
        print("請確認檔案平鋪在暫存區第一層、檔名樣式與測試指令對得上，修好後重新封存再跑。")
        return 1
    if returncode == 0:
        print("狀態：**沒有鑑別力** —— 這份隱藏測試在沒有實作時就全部通過了。")
        print("黃金法則第 2 條「先寫測試」的前提是測試在實作前是紅的；")
        print("一份實作前就綠的測試，驗收時什麼都證明不了。請修正測試後重新封存。")
        return 1

    info["baseline"] = {
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "red": True,
        "exit_code": returncode,
        "tests_seen": tests_seen,
    }
    info["signature"] = vault.sign_entry(info, args.token)
    manifest["tasks"][args.task_id] = info
    vault.save_manifest(manifest, root)

    seen = f"{tests_seen} 個測試" if tests_seen is not None else "測試數量無法從輸出解析"
    print(f"狀態：基線如預期是紅的（exit code = {returncode}，{seen}）。")
    print("已把基線結果寫進簽過章的 manifest；這一跑**不計入**停損次數。")
    print("接下來把權杖交回 Orchestrator，自己不要留存。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
