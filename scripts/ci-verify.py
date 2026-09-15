#!/usr/bin/env python3
"""ci-verify.py — 在 CI 上驗簽、解密並執行隱藏測試（驗收跑在 implementer 碰不到的地方）

第四輪 P0。設計理由見 scripts/hidden_vault.py 的「CI 驗收密文包」與
docs/ci-verification.md；這裡只寫**這支腳本自己**的行為契約。

## 為什麼要有第二個執行入口

`run-hidden-tests.py` 跟 implementer 跑在同一台機器、同一個 OS 使用者底下。
前三輪一路把信任根往外推（明文 → 資料簽章 → 連驗收程式碼一起封存），
但那條軸線有結構性的終點：**任何自我檢查都可以被「換掉檢查自己那一份」繞過**。
第三輪的誠實記載已經把這句話寫出來了。

這支腳本換的是軸線，不是再加一層：它跑在 GitHub Actions 上，
由**預設分支**的 workflow 啟動、用**預設分支**的程式碼與密文包，
權杖是 repository secret。implementer 在自己的分支上能改的東西，
一件都不參與這次驗收。

## 輸出契約（這支腳本最重要的一條規則）

**job log 裡永遠不會出現隱藏測試的內容，包含失敗的測試名稱與 assert 訊息。**
CI 的 log 與 artifact 任何有 repo 讀取權的人都看得到，implementer 也是。
把失敗明細印進 log，等於把隱藏測試變成一個可以反覆查詢的 oracle——
改一行、推一次、看 CI 說哪個 assert 紅了，比直接讀原始碼慢，但一樣有效。

所以 log 只有：task_id、檔案數、測試數量、exit code、通過與否。
完整輸出加密成 `report.enc`（金鑰由權杖推導），由握有權杖的 verifier-reviewer
用 `scripts/read-ci-report.py` 解開。

## 子行程拿不到權杖

被測的程式碼是 implementer 寫的，而它會在這支腳本的子行程裡執行。
所以傳給子行程的環境變數是**重新組出來的**：權杖、GitHub 的各種 token
都不在裡面（見 scrub_env）。這一條有回歸測試守著，不是靠自律。

exit code：
    0 = 全部通過（或沒有任何密文包，沒有東西要驗）
    1 = 有隱藏測試失敗
    2 = 無法執行：簽章不符、權杖缺漏或錯誤、密文被竄改、零測試假通過以外的結構問題
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hidden_vault as vault  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

TOKENS_ENV = "HARNESS_VERIFY_TOKENS"
SINGLE_TOKEN_ENV = "HARNESS_VERIFY_TOKEN"

# 不可以流進子行程的環境變數。被測的程式碼是 implementer 寫的，
# 它拿到權杖就能自己解密隱藏測試，拿到 GitHub token 就能對 repo 動手。
SCRUB_NAMES = (
    TOKENS_ENV,
    SINGLE_TOKEN_ENV,
    "GITHUB_TOKEN",
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
)
# 前綴式的清除。名稱清單是列舉式的，而列舉式的防守遲早會漏——
# 這裡多擋一層，未來新增 HARNESS_VERIFY_* 的變數不必記得回來改這裡。
SCRUB_PREFIXES = ("HARNESS_VERIFY", "INPUT_")


def parse_tokens(raw: str) -> dict:
    """解析 `HARNESS_VERIFY_TOKENS`：每行一個 `<task_id> <權杖>`。

    也接受 `task_id=token`。空行與 `#` 開頭的行忽略。
    格式錯的行**不是**靜靜跳過——會被回報出來，因為「權杖設錯格式」
    與「沒設權杖」在結果上都是驗收跑不動，但修法完全不同。
    """
    tokens, problems = {}, []
    for number, line in enumerate(raw.splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "=" in text and len(text.split("=", 1)[0].split()) == 1:
            task_id, token = text.split("=", 1)
        else:
            parts = text.split()
            if len(parts) != 2:
                problems.append(f"第 {number} 行不是 `<task_id> <權杖>` 的格式")
                continue
            task_id, token = parts
        task_id, token = task_id.strip(), token.strip()
        if not task_id or not token:
            problems.append(f"第 {number} 行的 task_id 或權杖是空的")
            continue
        tokens[task_id] = token
    return tokens, problems


def collect_tokens(env: dict) -> tuple:
    raw = env.get(TOKENS_ENV) or ""
    tokens, problems = parse_tokens(raw)
    single = (env.get(SINGLE_TOKEN_ENV) or "").strip()
    return tokens, single, problems


def scrub_env(base: dict, secrets_seen) -> dict:
    """組出要交給被測程式碼的環境變數。

    三道一起用：名稱清單、名稱前綴、以及**值比對**。第三道是給
    「權杖被順手複製到另一個變數名」那種情況兜底的——名稱防守是列舉式的，
    值防守不是。
    """
    secret_values = {value for value in secrets_seen if value}
    clean = {}
    for name, value in base.items():
        if name in SCRUB_NAMES or name.startswith(SCRUB_PREFIXES):
            continue
        if value in secret_values:
            continue
        clean[name] = value
    return clean


def load_entry(sealed_dir: Path, task_id: str):
    """讀密文包裡簽過章的 manifest 項目。回傳 (entry, 錯誤訊息)。"""
    path = sealed_dir / task_id / vault.CI_ENTRY_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"讀不到或解析不了 {vault.CI_ENTRY_NAME}：{exc}"
    entry = payload.get("entry") if isinstance(payload, dict) else None
    if not isinstance(entry, dict):
        return None, f"{vault.CI_ENTRY_NAME} 裡沒有 entry 物件"
    return entry, None


def run_task(task_id: str, entry: dict, token: str, sealed_dir: Path, work_dir: Path) -> dict:
    """驗簽、解密、執行一個 task 的隱藏測試。回傳結果 dict（不印任何測試內容）。"""
    result = {
        "task_id": task_id,
        "kind": vault.task_kind(entry),
        "files": len(entry.get("files", [])),
        "baseline": bool(entry.get("baseline")),
        "status": None,
        "exit_code": None,
        "tests_seen": None,
        "output": "",
        "problems": [],
    }

    if vault.token_fingerprint(token) != entry.get("token_sha256"):
        result["status"] = "blocked"
        result["problems"].append(
            "權杖錯誤：指紋與封存當下記錄的不符。secret 裡這個 task 的權杖不對，"
            "或密文包與 secret 不是同一次封存的產物。"
        )
        return result

    if not vault.verify_entry(entry, token):
        result["status"] = "blocked"
        result["problems"].append(
            "簽章不符：密文包裡的 manifest 項目被改過。只有握有權杖的一方簽得出有效簽章，"
            "而 implementer 沒有權杖——這本身就是可疑訊號，判定驗收不通過。"
        )
        return result

    # 明文解在系統暫存目錄，而且**一定不在 work_dir 底下**：被測的程式碼以
    # work_dir 為根，解在裡面等於把題目放進它的搜尋範圍。
    workdir = Path(tempfile.mkdtemp(prefix="harness-ci-"))
    try:
        try:
            workdir.chmod(0o700)
        except OSError:
            pass

        for item in entry.get("files", []):
            source = sealed_dir / task_id / (item["path"] + ".enc")
            if not source.is_file():
                result["problems"].append(f"密文包缺少檔案：{item['path']}")
                continue
            plaintext = vault.transform(source.read_bytes(), token, item["path"])
            if vault.sha256_bytes(plaintext) != item["sha256"]:
                result["problems"].append(f"密文被竄改（明文 sha256 不符）：{item['path']}")
                continue
            target = workdir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(plaintext)

        if result["problems"]:
            result["status"] = "blocked"
            return result

        argv = vault.render_test_command(
            entry["test_command"],
            {"dir": str(workdir), "repo": str(work_dir), "python": sys.executable},
        )
        env = scrub_env(
            {
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [str(work_dir)]
                    + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
                ),
                "HARNESS_REPO_ROOT": str(work_dir),
            },
            [token],
        )

        completed = subprocess.run(
            argv,
            cwd=str(work_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        result["output"] = output
        result["exit_code"] = completed.returncode
        result["tests_seen"] = vault.count_tests_seen(output)

        if completed.returncode == 0 and vault.looks_like_zero_tests(output):
            # exit 0 但一個測試都沒跑到——最危險的假通過，一律當成失敗。
            result["status"] = "zero-tests"
        elif completed.returncode == 0:
            result["status"] = "passed"
        else:
            result["status"] = "failed"
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def summarize(result: dict) -> str:
    """給 job log 看的一行摘要。這裡出現的每一個字都是公開的。"""
    counts = f"{result['files']} 個隱藏測試檔案"
    if result["tests_seen"] is not None:
        counts += f"、{result['tests_seen']} 個測試"
    if result["status"] == "passed":
        return f"✅ {result['task_id']}：全部通過（{counts}）"
    if result["status"] == "failed":
        return f"❌ {result['task_id']}：未全部通過（{counts}，exit code = {result['exit_code']}）"
    if result["status"] == "zero-tests":
        return f"❌ {result['task_id']}：測試指令回傳成功，但一個測試都沒跑到（{counts}）"
    return f"⛔ {result['task_id']}：無法執行"


def build_report(results: list, work_dir: Path) -> str:
    """給 verifier-reviewer 看的明細。這份會被加密，所以可以放完整輸出。"""
    lines = [
        "# CI 隱藏測試驗收明細",
        "",
        f"產生時間：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"受測程式碼：{work_dir}",
        "",
        "這份明細是加密的，只有握有權杖的一方解得開。裡面有隱藏測試的失敗訊息，",
        "**不要把它貼進驗收報告、PR 留言或任何 implementer 讀得到的地方**。",
        "",
    ]
    for result in results:
        lines.append("=" * 60)
        lines.append(f"## {result['task_id']}（{result['status']}）")
        lines.append(f"種類：{result['kind']}")
        lines.append(f"檔案數：{result['files']}　exit code：{result['exit_code']}")
        if not result["baseline"]:
            lines.append("⚠️ 這份隱藏測試從未被證明過會紅（沒有基線執行紀錄）。")
        for problem in result["problems"]:
            lines.append(f"⛔ {problem}")
        if result["output"]:
            lines.append("")
            lines.append("--- 測試輸出 ---")
            lines.append(result["output"].rstrip())
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="在 CI 上驗簽、解密並執行隱藏測試")
    parser.add_argument(
        "--repo",
        default=".",
        help="**可信的**那份 checkout（預設分支）。密文包 ci/sealed/ 從這裡讀",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="受測程式碼所在的 checkout（PR 分支）。預設與 --repo 相同",
    )
    parser.add_argument("--task-id", action="append", help="只驗這些 task（可重複）；預設驗全部")
    parser.add_argument("--report-out", default=None, help="加密明細的輸出路徑（預設不寫）")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    work_dir = Path(args.work_dir).resolve() if args.work_dir else repo

    print("===== CI 隱藏測試驗收 =====")
    print(f"密文包來源（可信）：{repo}")
    print(f"受測程式碼：{work_dir}")

    sealed_dir = vault.ci_sealed_dir(repo)
    available = vault.ci_sealed_tasks(repo)
    wanted = args.task_id or available

    if not available:
        print(f"{vault.CI_SEALED_DIR}/ 底下沒有任何密文包，沒有東西要驗。")
        print("（要用 CI 驗收，先 `python3 scripts/export-sealed-task.py` 匯出並 commit 進預設分支）")
        print("===== 結束 =====")
        return 0

    missing = [task_id for task_id in wanted if task_id not in available]
    if missing:
        print(f"指定的 task 沒有密文包：{missing}", file=sys.stderr)
        print("===== 結束 =====")
        return 2

    tokens, single, problems = collect_tokens(os.environ)
    for problem in problems:
        print(f"⛔ {TOKENS_ENV} 格式有問題：{problem}", file=sys.stderr)
    if problems:
        print("===== 結束 =====")
        return 2

    results, blocked = [], False
    for task_id in wanted:
        entry, error = load_entry(sealed_dir, task_id)
        if error:
            print(f"⛔ {task_id}：{error}", file=sys.stderr)
            blocked = True
            continue

        token = tokens.get(task_id) or (single if len(wanted) == 1 else None)
        if not token:
            # 沒有權杖不可以「跳過」——跳過等於驗收沒跑，而驗收沒跑跟驗收通過
            # 在 exit code 上長得一樣。這是這套機制最不能接受的失效方式。
            print(
                f"⛔ {task_id}：secret `{TOKENS_ENV}` 裡沒有這個 task 的權杖，驗收無法進行。",
                file=sys.stderr,
            )
            blocked = True
            continue

        result = run_task(task_id, entry, token, sealed_dir, work_dir)
        results.append(result)
        print(summarize(result))
        for problem in result["problems"]:
            print(f"   {problem}", file=sys.stderr)
        if not result["baseline"]:
            print(f"   ⚠️ {task_id} 沒有基線執行紀錄——「這份測試會紅」沒有人證明過。")

    if args.report_out and results:
        report = build_report(results, work_dir)
        # 用哪一把權杖加密：多個 task 時用第一個有權杖的那個 task 的權杖。
        # 實務上一個 PR 通常只驗一個 task；多個 task 時 verifier 手上會有多把，
        # 報告開頭會寫清楚是哪一個 task 的權杖解得開。
        key_task = results[0]["task_id"]
        token = tokens.get(key_task) or single
        out = Path(args.report_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(vault.encrypt_report(f"（用 {key_task} 的權杖解開）\n\n" + report, token))
        print(f"明細已加密寫到 {out}（用 {key_task} 的權杖解開：python3 scripts/read-ci-report.py）")

    print()
    if blocked or any(r["status"] == "blocked" for r in results):
        print("判定：**無法驗收**。上面的訊息不是環境問題，請照驗收報告的規則記載。")
        print("===== 結束 =====")
        return 2
    if any(r["status"] in ("failed", "zero-tests") for r in results):
        print("判定：**不通過**。明細在加密的 artifact 裡，job log 刻意不印失敗內容——")
        print("      印出來等於把隱藏測試變成可反覆查詢的 oracle。")
        print("===== 結束 =====")
        return 1
    print(f"判定：通過（{len(results)} 個 task）。")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
