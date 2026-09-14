#!/usr/bin/env python3
"""run-hidden-tests.py — 用執行權杖驗簽、解密並執行已封存的隱藏測試

設計理由見 scripts/hidden_vault.py。

由 **verifier-reviewer** 在驗收時執行；權杖由 Orchestrator 在派工時提供。
由 **verifier-test-writer** 在封存後立刻以 `--baseline` 執行一次（第二輪 P1-6）。

用法：
    python3 scripts/run-hidden-tests.py --list
    python3 scripts/run-hidden-tests.py --sweep
    python3 scripts/run-hidden-tests.py --task-id DEMO-001 --token <權杖>
    python3 scripts/run-hidden-tests.py --task-id DEMO-001 --token <權杖> --baseline

exit code：
    0 = 隱藏測試全部通過（--baseline 模式：測試如預期是紅的）
    1 = 有隱藏測試失敗（--baseline 模式：測試在沒有實作時就綠了，沒有鑑別力）
    2 = 無法執行：沒有這個 task、權杖錯誤、manifest 簽章不符、鎖定清單被動過、
        封存檔案被竄改、或 manifest 是舊版本需要重新封存

執行流程：
    0. 清掉先前被強制中斷留下的解密目錄（見下方「解密後的明文放在哪裡」）
    1. manifest 版本必須是 2（舊版沒有簽章，一律要求重新封存，不相容）
    1.5 這個 task 若已被 discard-sealed-task.py 作廢（權杖遺失），直接拒絕並說明
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

解密後的明文放在哪裡（為什麼不是 /tmp）：
    明文解到**封存庫底下**的 `.run-<task_id>-<亂數>/`，不是系統暫存目錄。
    差別在於外洩範圍：`guard-hidden-tests.py` 的 VAULT_DIRS 已經涵蓋封存庫
    （不分動詞、指令字串裡出現就擋），所以萬一這支腳本被強制中斷、明文沒清掉，
    殘留檔案至少還落在受保護的路徑裡；放在 /tmp 則完全不受保護。

    清理分三層，因為沒有任何一層擋得住全部情況：
      1. `finally` / `atexit` —— 正常結束與例外
      2. 訊號攔截（SIGINT/SIGTERM/SIGHUP/SIGBREAK）—— 被要求中止時
      3. **啟動時掃殘留**（`sweep_stale`）—— 唯一擋得住 SIGKILL 的一層。
         行程被作業系統直接砍掉時（例如撞到用量上限、整個 session 被回收），
         前兩層都不會執行，只有「下一次有人跑這支腳本」才有機會補救。
    掃描也會一併清掉舊版本留在系統暫存目錄的 `harness-hidden-*`。

已知殘留限制（誠實記載，不要當成已解決）：
    解密後的明文在測試執行期間確實存在於磁碟上，同一個使用者身分的行程
    在那短暫時間內是掃得到的；權杖也會經過 Claude Code 的 transcript。
    要完全消除，需要獨立的使用者/容器，超出本模板的範圍。

    第 3 層是「事後補救」不是「預防」：從行程被砍到下一次執行之間，
    明文確實留在封存庫裡。它換到的是「殘留落在 guard 擋得住的路徑」，
    不是「不會殘留」。
"""
import argparse
import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 相依模組載入失敗一律 exit 2，不能讓它變成 Python 預設的 exit 1——
# 在這支腳本裡 1 的語意是「有隱藏測試失敗」，而 verifier-reviewer 會照這個語意
# 判讀。少一個模組跟「測試失敗」是完全不同的兩件事，混為一談會讓一個根本沒跑起來
# 的驗收被記成一次失敗（還會計入停損次數）。
#
# 這件事在封存版（第三輪 P0-8 (a)）特別容易發生：封存庫裡少一個 .py，
# import 就會在任何檢查之前炸掉。這個案例是 test-vault.py 的
# 「封存版程式碼遺失也要擋下」當場抓出來的。
try:
    import attempts  # noqa: E402
    import hidden_vault as vault  # noqa: E402
    import utf8_output  # noqa: E402
except ImportError as _exc:
    print(
        f"隱藏測試無法執行：載入相依模組失敗（{_exc}）。\n"
        "若你執行的是封存版（<封存庫>/_runner/），代表封存庫裡的檔案不完整——"
        "這本身就是可疑訊號，請判定驗收不通過並在報告裡記載。",
        file=sys.stderr,
    )
    sys.exit(2)

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
        if vault.is_discarded(info):
            previous = info.get("previous") or {}
            print(f"- {task_id}：**已作廢**（權杖遺失），作廢於 {info.get('discarded_at', '未知時間')}")
            print(f"    原本封存於 {previous.get('sealed_at', '未知時間')}，"
                  f"{previous.get('file_count', '?')} 個檔案，已累計作廢 {info.get('discard_count', 1)} 次")
            print("    這個 task 目前**沒有可執行的隱藏測試**，要重新寫一份再封存才能驗收。")
            continue
        baseline = info.get("baseline")
        status = "已證明會紅" if baseline and baseline.get("red") else "尚未做基線執行"
        kind = vault.task_kind(info)
        label = "（bugfix：同類變體）" if kind == "bugfix" else ""
        print(f"- {task_id}：{len(info.get('files', []))} 個檔案，封存於 {info.get('sealed_at', '未知時間')}，{status}{label}")
        print(f"    測試指令：{info.get('test_command')}")
        if info.get("discard_count"):
            print(f"    ⚠️ 這個 task 曾因權杖遺失作廢過 {info['discard_count']} 次，"
                  "目前這份是重寫後重新封存的")
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


def running_from_vault(info: dict) -> bool:
    """這支腳本自己是不是封存版那一份。"""
    try:
        here = Path(__file__).resolve().parent
        vault_dir = Path(info.get("vault_dir") or "")
        return bool(vault_dir) and here == vault.runner_dir(vault_dir).resolve()
    except (OSError, ValueError):
        return False


def check_runner(info: dict, args):
    """驗收執行的程式碼是不是封存當下那一份（第三輪 P0-8 (a)）。

    回傳 None 代表可以繼續，否則回傳要用的 exit code。

    兩種情況的意義完全不同，所以分開處理：

      - **正在跑封存版**：比對 `_runner/` 底下每個檔案的 sha256 與簽過章的紀錄。
        對不上就是「封存之後有人動了封存庫裡的程式碼」——跟簽章不符同一級。
      - **正在跑 repo 裡那一份**：repo 裡的副本 implementer 改得到，所以它的
        執行結果**本來就不該被當成驗收依據**。這裡不直接拒絕（`--list`、
        `--sweep`、以及沒有封存版的舊 task 仍然要跑得動），但會把話講清楚，
        並在偵測到「repo 副本與封存版不一致」時直接擋下。
    """
    recorded = info.get("runner_files")
    vault_dir = Path(info.get("vault_dir") or "")

    if not recorded:
        # 舊版封存沒有這個欄位。不強制失敗（那會讓升級 harness 之後所有進行中的
        # task 全部卡住），但要明講這次驗收少了哪一層保護。
        print("⚠️ 這個 task 是在「驗收程式碼一起封存」之前封存的，沒有 runner_files 紀錄。")
        print("   本次執行的程式碼是否被改過**無法驗證**（第三輪 P0-8）。")
        print("   要拿回這一層保護，請重新封存一次。")
        return None

    if running_from_vault(info):
        problems = vault.verify_runner(vault_dir, recorded)
        if problems:
            print("===== 隱藏測試無法執行 =====", file=sys.stderr)
            print("❌ 封存版的驗收程式碼與封存當下記錄的 sha256 不符：", file=sys.stderr)
            for item in problems:
                print(f"   - {item}", file=sys.stderr)
            print(
                "   簽章金鑰由權杖推導，implementer 沒有權杖，所以這不可能是意外——"
                "請判定本次驗收不通過並在報告裡明確記載。",
                file=sys.stderr,
            )
            return 2
        print(f"驗收程式碼：封存版，{len(recorded)} 個檔案的 sha256 與簽章紀錄相符。")
        return None

    # 跑的是 repo 裡那一份。
    here = Path(__file__).resolve().parent
    drifted = [
        item["name"]
        for item in recorded
        if isinstance(item, dict)
        and item.get("name")
        and vault.sha256_file(here / item["name"]) != item.get("sha256")
    ]
    sealed = vault.runner_dir(vault_dir) / "run-hidden-tests.py" if vault_dir else None

    if drifted:
        print("===== 隱藏測試無法執行 =====", file=sys.stderr)
        print(
            "❌ 你正在執行 repo 裡的驗收程式碼，而它與封存當下那一份**不一致**：",
            file=sys.stderr,
        )
        for name in drifted:
            print(f"   - scripts/{name}", file=sys.stderr)
        print(
            "   repo 裡這幾支 implementer 改得到。第三輪實測：覆寫 run-hidden-tests.py "
            "之後，用完全正確的權杖驗收會得到 exit 0 與「隱藏測試全部通過」。",
            file=sys.stderr,
        )
        if sealed:
            print(f"   請改用封存版：python3 \"{sealed}\" --task-id {args.task_id} --token <權杖>",
                  file=sys.stderr)
        print(
            "   若這是因為 harness 本身被正當地更新過（不是被竄改），"
            "請重新封存這個 task，讓紀錄與程式碼重新對齊。",
            file=sys.stderr,
        )
        return 2

    print("⚠️ 你正在執行 repo 裡的驗收程式碼（目前與封存版一致）。")
    if sealed:
        print(f"   驗收請改用封存版，它在 repo 之外、implementer 改不到：\n   {sealed}")
    return None


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


# ------------------------------------------------------- 解密暫存目錄與殘留清理

# 解密後的明文放在封存庫底下而不是系統暫存目錄——理由見模組說明「解密後的明文
# 放在哪裡」。目錄名以 "." 開頭，測試探索（`test_*.py` 之類的樣式）不會撈到它。
RUN_DIR_PREFIX = ".run-"
# 舊版本用的位置。留著只是為了把既有的殘留一併清掉，不再拿來開新目錄
# （除非封存庫寫不進去，見 open_workdir 的 fallback）。
LEGACY_TMP_PREFIX = "harness-hidden-"
# 記下是哪個行程開的，殘留判定才不會誤刪「別人正在用」的目錄（平行驗收兩個 task）。
OWNER_MARKER = ".owner-pid"
# PID 會重用，所以年齡是第二道判準；Windows 上沒有便宜可靠的 PID 存活判斷，
# 那裡它是唯一的判準。取 6 小時：比任何合理的測試執行時間長得多。
STALE_AGE_SECONDS = 6 * 3600

_ACTIVE_WORKDIRS = []
_ACTIVE_CHILD = None


def _purge_active() -> None:
    """清掉這個行程開出來的解密目錄，並先確保子行程已經結束。

    順序不能反過來：子行程還活著就 rmtree，Windows 上會因為檔案被占用而刪不掉。
    """
    global _ACTIVE_CHILD
    child = _ACTIVE_CHILD
    if child is not None and child.poll() is None:
        try:
            child.terminate()
            child.wait(timeout=5)
        except Exception:  # noqa: BLE001 — 清理路徑上任何失敗都不該蓋掉原本的結束原因
            try:
                child.kill()
            except Exception:  # noqa: BLE001
                pass
    _ACTIVE_CHILD = None
    while _ACTIVE_WORKDIRS:
        shutil.rmtree(_ACTIVE_WORKDIRS.pop(), ignore_errors=True)


def install_cleanup_handlers() -> None:
    """正常結束、例外、以及可攔截的中止訊號，都要把明文清掉。

    SIGKILL 攔不到，被作業系統直接回收（撞到用量上限、容器被收走）也攔不到——
    那正是 sweep_stale() 存在的理由。
    """
    atexit.register(_purge_active)

    def handler(signum, _frame):
        _purge_active()
        # 不自己決定退出碼語意：128+訊號 是 shell 的慣例，呼叫端看得懂。
        raise SystemExit(128 + signum)

    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue  # 這個平台沒有這個訊號
        try:
            signal.signal(signum, handler)
        except (ValueError, OSError):
            pass  # 不在主執行緒，或平台不允許攔這個訊號


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return True  # 沒有便宜又可靠的判斷，交給年齡門檻決定
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不屬於我們
    except OSError:
        return True  # 判斷不出來就當它還活著（寧可留著也不要誤刪）
    return True


def _is_stale(run_dir: Path) -> bool:
    """這個解密目錄是不是先前被強制中斷留下來的。

    判定刻意保守：誤刪一個「正在用」的目錄會讓一次合法驗收無故失敗，
    而漏刪只是殘留到下一次再清。所以只有「標記不見/壞掉」「標記的行程已經不在」
    「超過年齡門檻」才算殘留。
    """
    try:
        age = time.time() - run_dir.stat().st_mtime
    except OSError:
        return False
    if age > STALE_AGE_SECONDS:
        return True
    try:
        pid = int((run_dir / OWNER_MARKER).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True  # 舊版目錄或標記寫壞了：沒有「正在用」的證據
    if pid == os.getpid():
        return False
    return not _pid_alive(pid)


def known_vault_dirs(manifest: dict, root: Path) -> list:
    """所有可能放著解密殘留的封存庫位置：預設/環境變數指定的，加上 manifest 記過的。"""
    dirs = []

    def add(raw):
        if not raw:
            return
        try:
            resolved = Path(raw).resolve()
        except OSError:
            return
        if resolved not in dirs:
            dirs.append(resolved)

    add(vault.resolve_vault_dir(root))
    for info in (manifest.get("tasks") or {}).values():
        if isinstance(info, dict):
            add(info.get("vault_dir"))
    return dirs


def sweep_stale(vault_dirs) -> list:
    """清掉先前被強制中斷留下的解密目錄，回傳被清掉的路徑。

    這是整套清理裡唯一擋得住 SIGKILL 的一層：訊號攔截與 atexit 在行程被直接砍掉時
    都不會執行，只有「下一次有人跑這支腳本」才有機會補救。所以 --list / --sweep /
    正式執行都會先跑它一次。
    """
    candidates = []
    for vault_dir in vault_dirs:
        if vault_dir.is_dir():
            candidates.extend(sorted(vault_dir.glob(RUN_DIR_PREFIX + "*")))
    # 舊版本把明文解到系統暫存目錄，那裡完全不受 guard 保護，一併清掉。
    try:
        candidates.extend(sorted(Path(tempfile.gettempdir()).glob(LEGACY_TMP_PREFIX + "*")))
    except OSError:
        pass

    swept = []
    for candidate in candidates:
        if not candidate.is_dir() or not _is_stale(candidate):
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        if not candidate.exists():
            swept.append(candidate)
    return swept


def report_swept(swept) -> None:
    if not swept:
        return
    print(f"⚠️ 清掉了 {len(swept)} 個先前留下的解密目錄——代表上一次執行是被強制中斷的")
    print("   （撞到用量上限、session 被回收、或行程被 kill）。在那之後到現在，")
    print("   隱藏測試的明文一直留在磁碟上。請把這件事寫進驗收報告。")
    for path in swept:
        print(f"   - {path}")


def open_workdir(vault_dir, task_id: str):
    """開一個 0700 的解密目錄，優先開在封存庫底下。回傳 (路徑, fallback 原因或 None)。"""
    target = None
    fallback_reason = None
    if vault_dir is not None:
        try:
            vault_dir.mkdir(parents=True, exist_ok=True)
            target = Path(tempfile.mkdtemp(prefix=f"{RUN_DIR_PREFIX}{task_id}-", dir=str(vault_dir)))
        except OSError as exc:
            fallback_reason = f"{type(exc).__name__}: {exc}"
    if target is None:
        # 封存庫寫不進去（唯讀掛載、權限不足）時仍要能驗收，但要說清楚代價。
        target = Path(tempfile.mkdtemp(prefix=LEGACY_TMP_PREFIX))
    _ACTIVE_WORKDIRS.append(target)
    try:
        target.chmod(0o700)
    except OSError:
        pass  # Windows 上沒有 POSIX 權限語意
    try:
        (target / OWNER_MARKER).write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass  # 沒有標記只會讓下一次掃描把它當殘留，不影響這一次
    return target, fallback_reason



def main() -> int:
    parser = argparse.ArgumentParser(description="驗簽、解密並執行已封存的隱藏測試")
    parser.add_argument("--task-id", help="要執行的 task_id")
    parser.add_argument("--token", help="封存時產生的執行權杖")
    parser.add_argument("--list", action="store_true", help="列出已封存的 task（不需要權杖）")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="只清掉先前被強制中斷留下的解密目錄，不執行任何測試（不需要權杖）",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="基線執行：由 verifier-test-writer 在封存後立刻跑，預期測試是紅的；不計入停損次數",
    )
    args = parser.parse_args()

    install_cleanup_handlers()

    root = vault.repo_root()
    manifest = vault.load_manifest(root)

    # 掃殘留要在任何模式下都先做一次：上一次執行若是被 SIGKILL 砍掉的，
    # 明文從那時起就一直留在磁碟上，愈早清掉愈好。
    swept = sweep_stale(known_vault_dirs(manifest, root))

    if args.sweep:
        print("===== 清理解密殘留 =====")
        if swept:
            report_swept(swept)
        else:
            print("沒有發現殘留的解密目錄。")
        print("===== 結束 =====")
        return 0

    report_swept(swept)

    if args.list:
        return list_tasks(manifest)

    if not args.task_id:
        print("需要 --task-id（或用 --list 查看已封存的 task、--sweep 清殘留）。", file=sys.stderr)
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
        # 第三輪 P0-9：這句話以前是中性的，跟「Orchestrator 派工時 task_id 打錯」
        # 完全一樣——於是「作廢 + 刪掉墓碑」這條路可以退回「從來沒封存過」。
        # 封存流水帳在 repo 之外，是第二個可以問「到底封存過沒有」的地方。
        history = []
        try:
            history = vault.sealed_log_entries(
                vault.resolve_vault_dir(root), args.task_id
            )
        except (OSError, ValueError):
            history = []
        if history and args.token:
            fingerprint = vault.token_fingerprint(args.token)
            if any(item["token_sha256"] == fingerprint for item in history):
                print("===== 隱藏測試無法執行 =====", file=sys.stderr)
                print(
                    f"❌ task「{args.task_id}」**封存過**（流水帳有紀錄，而且權杖指紋對得上），"
                    "但 manifest 裡那一筆不見了。",
                    file=sys.stderr,
                )
                for item in history:
                    print(f"   流水帳：{item['at']}  {item['event']}", file=sys.stderr)
                print(
                    "   這不是派工打錯 task_id——封存紀錄在封存之後被刪除了，"
                    "這本身就是可疑訊號，跟「簽章不符」同一級。",
                    file=sys.stderr,
                )
                print("   驗收判定：不通過。請回報 Orchestrator 對照派工紀錄。", file=sys.stderr)
                return 2

        print(f"manifest 裡沒有 task「{args.task_id}」。", file=sys.stderr)
        if history:
            print(
                "⚠️ 但封存流水帳裡有這個 task_id 的紀錄——若你確定曾經封存過，"
                "請帶 --token 再執行一次以確認，並回報 Orchestrator。",
                file=sys.stderr,
            )
        print("可用 --list 查看有哪些已封存的 task。", file=sys.stderr)
        print(
            "若你確信這個 task 封存過，**不要**自己判斷成派工錯誤："
            "請回報 Orchestrator 對照派工紀錄（第三輪 P0-9）。",
            file=sys.stderr,
        )
        return 2

    # 墓碑沒有 token_sha256 也沒有 signature，順序反過來只會得到「權杖錯誤」
    # 這種會誤導人的訊息——那會讓人以為權杖打錯了，再去找一個根本不存在的權杖。
    if vault.is_discarded(info):
        previous = info.get("previous") or {}
        print("===== 隱藏測試無法執行 =====", file=sys.stderr)
        print(f"❌ task「{args.task_id}」的封存已經被作廢（權杖遺失），"
              f"作廢於 {info.get('discarded_at', '未知時間')}。", file=sys.stderr)
        print(f"   原本封存於 {previous.get('sealed_at', '未知時間')}，"
              f"共 {previous.get('file_count', '?')} 個檔案；密文已刪除。", file=sys.stderr)
        print("   權杖遺失沒有救援路徑（manifest 只存指紋），這是刻意的——", file=sys.stderr)
        print("   留後門等於留繞過方式。要恢復驗收能力，只能重寫一份隱藏測試再封存：", file=sys.stderr)
        print("     1. Orchestrator 重新派工 verifier-test-writer（帶同一份 task-spec）", file=sys.stderr)
        print(f"     2. 寫完後 python3 scripts/seal-hidden-tests.py --task-id {args.task_id}", file=sys.stderr)
        print("     3. 新權杖立刻做一次 --baseline，再交回 Orchestrator", file=sys.stderr)
        print("   驗收報告要記載這個 task 曾經作廢過"
              f"（目前累計 {info.get('discard_count', 1)} 次）。", file=sys.stderr)
        return 2

    if not args.token:
        print("需要 --token；權杖由 Orchestrator 在派工時提供。", file=sys.stderr)
        print("若權杖確實已經遺失（session 被中斷等），沒有救援路徑，", file=sys.stderr)
        print("請用 `python3 scripts/discard-sealed-task.py --task-id "
              f"{args.task_id} --token-lost --confirm` 作廢後重寫一份。", file=sys.stderr)
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

    # 第三輪 P0-8 (a)：驗簽之後、執行任何東西之前，確認「正在執行的這份程式碼」
    # 就是封存當下那一份。兩件事分開判斷，因為它們的意義完全不同。
    runner_verdict = check_runner(info, args)
    if runner_verdict is not None:
        return runner_verdict

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

    # 明文解在封存庫底下而不是 /tmp：guard 的 VAULT_DIRS 涵蓋封存庫，
    # 萬一這支腳本被強制中斷、明文沒清掉，殘留至少落在受保護的路徑裡。
    vault_dir = Path(info.get("vault_dir")) if info.get("vault_dir") else task_dir.parent
    workdir, fallback_reason = open_workdir(vault_dir, args.task_id)
    try:
        if fallback_reason:
            print(f"⚠️ 封存庫（{vault_dir}）無法建立解密目錄：{fallback_reason}")
            print(f"   改用系統暫存目錄 {workdir}——那裡不在 guard 的保護範圍內，")
            print("   這次執行若被強制中斷，殘留的明文不受保護。請把這件事寫進驗收報告。")

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

        kind = vault.task_kind(info)
        mode = "基線執行（預期是紅的）" if args.baseline else "驗收"
        print(f"===== 執行隱藏測試：{mode} =====")
        print(f"task_id：{args.task_id}")
        if kind == "bugfix":
            print("種類：**bugfix**——這批隱藏測試是同一個根因的其他變體，")
            print("      被回報的那一個最小重現在公開測試裡（implementer 看得到）。")
        print(f"檔案數：{len(info.get('files', []))}（manifest 簽章已驗證）")
        print(f"指令：{command}")
        print("（工作目錄為 repo 根目錄，解密後的暫存目錄在測試結束後會立刻刪除）")
        if not args.baseline and not info.get("baseline"):
            print("⚠️ 這份隱藏測試從未被證明過會紅（沒有基線執行紀錄）——")
            print("   一份全部 assert True 的測試也會「全部通過」。請把這件事寫進驗收報告。")
        print("-" * 60)

        # 這裡要捕捉輸出而不是直接串到主控台，因為下面要檢查「有沒有真的跑到測試」；
        # 捕捉後原樣印出來，verifier 看到的內容不變。
        # 用 Popen 而不是 subprocess.run：收到中止訊號時要先把子行程收掉，
        # 否則它會繼續讀那個我們正要刪掉的解密目錄（Windows 上還會刪不掉）。
        global _ACTIVE_CHILD
        _ACTIVE_CHILD = subprocess.Popen(
            argv,
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = _ACTIVE_CHILD.communicate()
            returncode = _ACTIVE_CHILD.returncode
        finally:
            _ACTIVE_CHILD = None
        result = subprocess.CompletedProcess(argv, returncode, stdout, stderr)
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
            if kind == "bugfix":
                print("（bugfix：同類變體也全綠，代表修的是根因而不是被回報的那一個 case——"
                      "這正是 docs/root-cause-and-fix.md 步驟 6「找同類」要驗的事）")
            _record_attempt(manifest, info, args.task_id,True, args.token)
            return 0
        print(f"狀態：隱藏測試未全部通過（測試指令 exit code = {result.returncode}）。")
        if kind == "bugfix":
            # 這句話是 --kind bugfix 存在的理由：不講出來的話，verifier 看到的只是
            # 「有測試失敗」，很容易退回去要 implementer「再修一下」，而真正該問的是
            # 「他是不是只修了被回報的那一個 case」。
            print()
            print("⚠️ 這是 bugfix 任務，而且公開的最小重現很可能**已經綠了**——")
            print("   紅的是同一個根因的其他變體。這通常代表 implementer 針對被回報的")
            print("   那一個 case 寫了特例（多一個 if、多一個 early return），根因還在。")
            print("   請先確認公開測試的狀態再判定：")
            print("     - 公開綠 + 隱藏紅 → **修症狀不是修根因**，退回並明確要求處理根因，")
            print("       不要只說「還有測試沒過」")
            print("     - 公開紅 + 隱藏紅 → 連被回報的 case 都還沒修好，一般退回即可")
            print()
        print("請把失敗的測試名稱與訊息寫進驗收報告；不要把隱藏測試的原始碼貼進報告。")
        _record_attempt(manifest, info, args.task_id,False, args.token, f"測試指令 exit code = {result.returncode}")
        return 1
    finally:
        _purge_active()
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
        if vault.task_kind(info) == "bugfix":
            print("狀態：**沒有鑑別力** —— 這些變體在**修正之前**就全部通過了。")
            print("那代表它們不是這個 bug 的同類：真正同根因的變體，在根因被修掉之前")
            print("一定是紅的。請重新挑變體（判準見 docs/root-cause-and-fix.md §1.5），")
            print("修正後重新封存。")
            return 1
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
    if vault.task_kind(info) == "bugfix":
        # 誠實記載這個機制驗不到的部分，不要讓人以為基線綠了就代表每個變體都合格。
        print("⚠️ 基線看的是整批的 exit code，證明的是「**至少有一個**變體在修正前是紅的」，")
        print("   不是「每個變體都紅」。混進一個修正前就綠的變體，這裡看不出來——")
        print("   請自己對著上面的輸出確認每個變體都失敗了，再交回權杖。")
    print("已把基線結果寫進簽過章的 manifest；這一跑**不計入**停損次數。")
    print("接下來把權杖交回 Orchestrator，自己不要留存。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
