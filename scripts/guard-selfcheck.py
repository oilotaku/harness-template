#!/usr/bin/env python3
"""guard-selfcheck.py — SessionStart hook：確認防作弊機制這次真的有生效

對應 docs/history/improvement-suggestions.md 的 P0-5。

`guard-hidden-tests.py` 已經改成 fail-closed（自己出錯時擋下操作），但那只
救得了「腳本有被執行到」的情況。如果 hook 根本沒被執行——找不到 python3、
hook 設定被改掉或刪掉、路徑寫錯、腳本檔案不存在——Claude Code 只會得到一個
非 2 的 exit code，把它當成 non-blocking error，工具照常執行，
而**使用者不會看到任何訊號**，會以為隱藏測試一直有被保護。

本腳本在每個 session 開始時，用幾個「已知應該被擋」與「已知應該放行」的
payload 實際跑一次 guard，把結果印出來。SessionStart hook 的 stdout 會進入
session 的上下文，所以防護一旦失效，Orchestrator 與使用者第一時間就看得到。

本腳本預設一律以 exit code 0 結束——它的任務是「回報」，不是「阻止 session 開始」。
加 `--strict` 時（第二輪 P3-6，給 verifier-reviewer 在驗收第 0 步跑）：找不到 guard、
任何探測不符預期、或設定檔壞掉，就回 exit 1——子智能體是新 session，不會再跑一次
SessionStart hook，hook 若在派工中途失效，只有這一步能讓 verifier 知道。

第二輪 P1-7：另外對每個受保護的隱藏測試路徑跑 `git check-ignore`，沒被忽略就警告——
明文一旦進了 git 歷史，封存就沒有意義。
"""
import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def _repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


REPO_ROOT = _repo_root()
GUARD = REPO_ROOT / "scripts" / "guard-hidden-tests.py"
VERIFY_LOCKS = REPO_ROOT / "scripts" / "verify-locks.py"
RUN_HIDDEN = REPO_ROOT / "scripts" / "run-hidden-tests.py"
VERSION_SCRIPT = REPO_ROOT / "scripts" / "version.py"
DESIGN_SCRIPT = REPO_ROOT / "scripts" / "check-design-tokens.py"
HIDDEN_MANIFEST = REPO_ROOT / ".harness" / "hidden-manifest.json"

# 探測用的路徑要跟設定檔一致，否則在自訂測試目錄的專案上會探到一個
# 「本來就不受保護」的位置，然後回報一個假的失敗。
def _hidden_probe_rel() -> str:
    try:
        patterns = harness_config.load(REPO_ROOT)["hidden_test_paths"]
    except harness_config.ConfigError:
        patterns = harness_config.DEFAULT_HIDDEN_TEST_PATHS
    base = patterns[0].split("*")[0].split("?")[0].strip("/") or "tests/hidden"
    return f"{base}/__selfcheck_probe__.py"


HIDDEN_PROBE_REL = _hidden_probe_rel()

# (說明, payload 或 None 代表送出不合法的 stdin, 期望的 exit code)
PROBES = [
    (
        "Read 相對路徑讀隱藏測試",
        {"tool_name": "Read", "tool_input": {"file_path": HIDDEN_PROBE_REL}},
        2,
    ),
    (
        "Read 絕對路徑讀隱藏測試",
        {"tool_name": "Read", "tool_input": {"file_path": str(REPO_ROOT / HIDDEN_PROBE_REL)}},
        2,
    ),
    (
        "Bash cat 隱藏測試",
        {"tool_name": "Bash", "tool_input": {"command": f"cat {HIDDEN_PROBE_REL}"}},
        2,
    ),
    (
        "Edit 修改隱藏測試",
        {"tool_name": "Edit", "tool_input": {"file_path": HIDDEN_PROBE_REL}},
        2,
    ),
    (
        "無法解析的輸入應 fail-closed",
        None,
        2,
    ),
    (
        "讀取一般檔案不應被誤擋",
        {"tool_name": "Read", "tool_input": {"file_path": "README.md"}},
        0,
    ),
]


def run_probe(payload) -> int:
    stdin = "這不是合法 JSON" if payload is None else json.dumps(payload)
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=stdin,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
    )
    return result.returncode


# 常見的測試檔案命名慣例。用來偵測「專案有測試，但都不在受保護路徑裡」——
# 也就是 P1-4 要修的那個沉默失效：保護沒生效，而且沒有任何訊號。
TEST_FILE_PATTERNS = (
    "test_*.py", "*_test.py", "*_test.go", "*_test.rs",
    "*.test.ts", "*.test.js", "*.test.tsx", "*.test.jsx",
    "*.spec.ts", "*.spec.js", "*Test.java", "*Tests.cs",
)
SCAN_SKIP_DIRS = {
    ".git", ".harness", "node_modules", "venv", ".venv", "__pycache__",
    "target", "dist", "build", ".next", "vendor",
    "templates",  # 本模板自己的範例目錄，不是使用者的測試
    # 版本歸檔裡有 tests/public 的副本。不跳過的話，這個掃描會把那些副本
    # 當成「有測試但不在受保護路徑」而誤報——而誤報久了就會讓人忽略真的警告。
    "releases",
}


def check_config() -> bool:
    """回報受保護路徑，並在「專案有測試但都沒被保護」時明確警告。回傳設定是否健康。"""
    try:
        config = harness_config.load(REPO_ROOT)
    except harness_config.ConfigError as exc:
        print(f"⚠️ {harness_config.CONFIG_FILENAME} 有問題：{exc}")
        print("在修好之前，guard hook 會 fail-closed 擋下所有工具呼叫。")
        return False

    hidden = config["hidden_test_paths"]
    public = config["public_test_paths"]
    print(f"受保護路徑（來源：{config['_source']}）：")
    print(f"  隱藏測試暫存區：{'、'.join(hidden)}")
    print(f"  公開測試（鎖定對象）：{'、'.join(public)}")

    configured_exists = bool(
        harness_config.existing_dirs(hidden, REPO_ROOT)
        or harness_config.existing_dirs(public, REPO_ROOT)
    )
    if configured_exists:
        return True

    # 設定的測試目錄一個都不存在時，看看專案裡是不是其實有測試放在別的地方。
    stray = []
    for path in REPO_ROOT.rglob("*"):
        if len(stray) >= 5:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(part in SCAN_SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts[:-1]):
            continue
        if harness_config.matches(rel, hidden) or harness_config.matches(rel, public):
            continue
        if any(fnmatch.fnmatch(path.name, pattern) for pattern in TEST_FILE_PATTERNS):
            stray.append(rel)

    if stray:
        print()
        print("⚠️ 設定的測試目錄一個都不存在，但專案裡看起來有測試檔案：")
        for rel in stray:
            print(f"    {rel}")
        print("這代表**這些測試完全不受保護**——鎖定與封存機制碰不到它們。")
        print(f"請在 {harness_config.CONFIG_FILENAME} 指定你的專案慣例，例如：")
        print('  {"public_test_paths": ["tests"], "hidden_test_paths": [".harness-hidden-staging"]}')
        print("詳見 docs/multi-language-support.md。")
    return True


def _protection() -> dict:
    """這個專案宣告了幾層防護。設定壞掉時由 check_config() 負責報錯，
    這裡退回 full——把壞掉的設定當成 minimal 會讓警告消失，正好是反過來的。"""
    try:
        return harness_config.load(REPO_ROOT)["protection"]
    except harness_config.ConfigError:
        return {"level": harness_config.DEFAULT_PROTECTION_LEVEL, "ci_verification": False}


def _stale_ci_bundles(base: Path, sealed) -> list:
    """密文包裡的簽章與 manifest 目前那一筆不同的 task。

    比的是簽章而不是內容：簽章涵蓋整個項目，只要有任何一個欄位被重新簽過
    （基線執行、嘗試次數累加）就會不同。讀不到就當它沒問題——這是提醒，
    不是判定，在這裡 fail-closed 只會製造雜訊。
    """
    try:
        manifest = json.loads(HIDDEN_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    tasks = manifest.get("tasks") or {}

    stale = []
    for task_id in sealed:
        current = tasks.get(task_id)
        if not isinstance(current, dict) or not current.get("signature"):
            continue
        try:
            payload = json.loads((base / task_id / "entry.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        exported = (payload or {}).get("entry") or {}
        if exported.get("signature") != current["signature"]:
            stale.append(task_id)
    return stale


def check_protection(protection: dict) -> None:
    """把「這個專案開了幾層」每個 session 講一次（第四輪 P1）。

    minimal 模式最大的風險不是少了三層，是**沒有人記得少了三層**。
    所以它不是靜靜少跑幾個檢查：每個 session、每次封存、每次驗收都會講。
    """
    if protection["level"] == "minimal":
        print("保護等級：**minimal**（只有第 1 層：實體隔離）")
        print("  沒有開的三層：事前攔截 hook、公開測試鎖定與事後稽核、本檢查的探測。")
        print("  代價：公開測試在封存後被改不會有人抓到。見 docs/protection-levels.md。")
    else:
        print("保護等級：full（四層）")

    if not protection["ci_verification"]:
        return

    sealed = []
    base = REPO_ROOT / harness_config.CI_SEALED_DIR
    if base.is_dir():
        sealed = sorted(
            path.name for path in base.iterdir()
            if path.is_dir() and (path / "entry.json").is_file()
        )
    if sealed:
        print(f"CI 驗收：已宣告，{harness_config.CI_SEALED_DIR}/ 有 {len(sealed)} 個密文包"
              f"（{'、'.join(sealed)}）")
        for task_id in _stale_ci_bundles(base, sealed):
            # 最常見的成因：封存後做了基線執行（那會改寫簽過章的項目），
            # 但沒有重新匯出。後果不是驗收出錯，是 CI 上永遠說「沒有基線紀錄」——
            # 一個永遠出現的警告等於沒有警告，所以在這裡先抓出來。
            print(f"⚠️ 密文包「{task_id}」比 manifest 舊（多半是基線執行之後沒有重新匯出）。")
            print("   重匯：python3 scripts/export-sealed-task.py --task-id "
                  f"{task_id} --token <權杖>")
        return
    # 宣告了要用 CI 驗收、卻沒有密文包，是會讓驗收「安靜地沒有跑」的那種漂移：
    # workflow 會發現沒東西可驗而回 0，看起來跟「全部通過」一模一樣。
    print(f"⚠️ 宣告了 CI 驗收（protection.ci_verification = true），"
          f"但 {harness_config.CI_SEALED_DIR}/ 裡沒有任何密文包。")
    print("   CI 上的驗收會因為「沒東西可驗」而回傳成功——那跟「全部通過」長得一樣。")
    print("   封存後請執行 `python3 scripts/export-sealed-task.py` 並 commit 進預設分支。")


def check_gitignore() -> None:
    """每個隱藏測試暫存區都必須被 .gitignore 排除（第二輪 P1-7）。

    明文一旦進了 git 歷史，`git log -p` 就永遠讀得到——指令字串裡完全不需要出現
    暫存區路徑，封存腳本刪掉工作目錄裡的檔案對歷史也沒有作用。
    不是 git repo、或沒裝 git 時不檢查（沒有歷史可以外洩）。
    """
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        return
    try:
        patterns = harness_config.load(REPO_ROOT)["hidden_test_paths"]
    except harness_config.ConfigError:
        return

    unignored = []
    for pattern in patterns:
        base = pattern.split("*")[0].split("?")[0].strip("/")
        if not base:
            continue
        probe = f"{base}/__gitignore_probe__.py"
        result = subprocess.run(
            ["git", "check-ignore", "-q", probe],
            cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            unignored.append(pattern)

    if unignored:
        print()
        print("⚠️ 這些隱藏測試暫存區**沒有被 .gitignore 排除**：")
        for pattern in unignored:
            print(f"    {pattern}")
        print("寫在這裡的明文只要被 commit 一次，`git log -p` 就永遠讀得到，封存也救不回來。")
        print("請在 .gitignore 加上對應規則（本模板預設有 tests/hidden/* 那組，")
        print("自訂路徑要自己加），seal-hidden-tests.py 也會拒絕封存已進歷史的檔案。")


# 一個目錄要被視為「已知不受保護」，只要它自己寫明這件事就好——
# 修正動作與承認動作是同一件事：把「這裡不受保護」寫下來給下一個讀到的人看。
ACK_MARKER = "不受保護"
ACK_FILES = ("README.md", "readme.md")


def _acknowledged(directory: Path) -> bool:
    for name in ACK_FILES:
        candidate = directory / name
        if not candidate.exists():
            continue
        try:
            if ACK_MARKER in candidate.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def check_unprotected_hidden_dirs() -> None:
    """找出「看起來是隱藏測試目錄、但不在受保護路徑內」的位置（P1-3）。

    這是原本 stray 偵測漏掉的一半：舊的偵測只在「設定的測試目錄一個都不存在」時
    才跑，所以像 monorepo 那樣**設定的目錄存在、但另外還有第二個 tests/hidden**
    的情況永遠不會被發現——使用者照著範例的目錄結構套進子專案，會以為有保護。

    刻意的設計：目錄裡的 README 只要寫明「不受保護」就不再警告。
    否則這個檢查會在每個 session 都對著同一個刻意留著的示範目錄大喊，
    而一個總是在響的警告等於沒有警告（那正是 P3-1 修掉的失效方式）。
    """
    try:
        patterns = harness_config.load(REPO_ROOT)["hidden_test_paths"]
    except harness_config.ConfigError:
        return  # 設定壞掉的訊息由 check_config() 負責，這裡不重複吵

    tails = {pattern.rstrip("/").split("/")[-1] for pattern in patterns if pattern.strip("/")}
    if not tails:
        return

    # 這裡不沿用 SCAN_SKIP_DIRS：那份清單為了避免雜訊而跳過 templates/，
    # 但「範例目錄示範了一個看起來受保護、實際不受保護的結構」正是 P1-3 的原始問題，
    # 不該被跳過。改用 README 承認機制處理雜訊，而不是整個目錄視而不見。
    skip_dirs = SCAN_SKIP_DIRS - {"templates"}

    findings = []
    for path in REPO_ROOT.rglob("*"):
        if len(findings) >= 5:
            break
        if not path.is_dir() or path.name not in tails:
            continue
        rel = path.relative_to(REPO_ROOT)
        if any(part in skip_dirs for part in rel.parts):
            continue
        if harness_config.matches(rel.as_posix() + "/probe.py", patterns):
            continue  # 在受保護路徑內，正常
        if not any(
            any(fnmatch.fnmatch(child.name, pattern) for pattern in TEST_FILE_PATTERNS)
            for child in path.rglob("*")
            if child.is_file()
        ):
            continue  # 空目錄不算，沒有東西可以外洩
        if _acknowledged(path):
            continue
        findings.append(rel.as_posix())

    if findings:
        print()
        print("⚠️ 這些目錄看起來是隱藏測試，但**不在受保護路徑內**：")
        for rel in findings:
            print(f"    {rel}/")
        print("放在這裡的隱藏測試任何人都讀得到——鎖定與封存機制碰不到它們。")
        print(f"處理方式二選一：把它納入 {harness_config.CONFIG_FILENAME} 的 hidden_test_paths，")
        print("或在該目錄放一份 README 寫明它「不受保護」（示範用途就屬於後者）。")


def check_locks() -> None:
    if not VERIFY_LOCKS.exists():
        return
    result = subprocess.run(
        [sys.executable, str(VERIFY_LOCKS)],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
    )
    if result.returncode == 0:
        print("公開測試鎖定稽核：正常（雜湊與鎖定當下一致）。")
    elif result.returncode == 1:
        print("⚠️ 公開測試鎖定稽核：**偵測到竄改或檔案遺失**，完整輸出如下：")
        print(result.stdout.strip())
    else:
        print("公開測試鎖定稽核：目前沒有可驗證的鎖定清單（尚未執行 lock-tests.py，屬正常起始狀態）。")


def check_audit_scripts() -> bool:
    """事後稽核腳本也要**實跑**一次，不是只看它在不在。

    第三輪 P1-9：SessionStart 的自我檢查原本只涵蓋 guard 一支。實測把
    `verify-locks.py`／`run-hidden-tests.py`／`hidden_vault.py` 各自換成
    `sys.exit(0)`，這支腳本全部回報「一切正常」——**連它自己被換掉也一樣**。

    這裡補上 `verify-locks.py` 的實跑驗證：在暫存目錄裡造一份「內容與雜湊
    對得上」與一份「被竄改」的鎖定清單，斷言它分別回 0 與 1。作法跟上面對
    guard 做的完全一樣——不看程式碼，看它在已知情境下的行為。

    回傳 False 代表稽核腳本的行為不符預期（--strict 會據此回非零）。
    """
    if not VERIFY_LOCKS.exists():
        print("⚠️ 事後稽核自我檢查：找不到 verify-locks.py——公開測試竄改目前**沒有人在稽核**。")
        return False

    def run_in(sandbox: Path) -> int:
        return subprocess.run(
            [sys.executable, str(VERIFY_LOCKS)],
            text=True, encoding="utf-8", errors="replace",
            capture_output=True,
            cwd=str(sandbox),
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(sandbox)},
        ).returncode

    problems = []
    try:
        with tempfile.TemporaryDirectory() as base:
            sandbox = Path(base)
            test_file = sandbox / "tests" / "public" / "test_probe.py"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text("assert True\n", encoding="utf-8")
            digest = hashlib.sha256(test_file.read_bytes()).hexdigest()
            locked = sandbox / ".harness" / "locked-tests.list"
            locked.parent.mkdir(parents=True, exist_ok=True)
            locked.write_text(f"{digest}  tests/public/test_probe.py\n", encoding="utf-8")

            intact = run_in(sandbox)
            if intact != 0:
                problems.append(f"未被竄改的清單應回 0，實際 {intact}")

            test_file.write_text("assert False\n", encoding="utf-8")
            tampered = run_in(sandbox)
            if tampered != 1:
                problems.append(f"被竄改的公開測試應回 1，實際 {tampered}")
    except OSError as exc:
        print(f"事後稽核自我檢查：無法建立暫存情境（{exc}），本次略過。")
        return True

    if problems:
        print(f"⚠️ 事後稽核自我檢查：**verify-locks.py 的行為不符預期**（{len(problems)} 項）：")
        for item in problems:
            print(f"  - {item}")
        print("公開測試被改掉將不會有人發現。修好之前不要進行驗收。")
        return False

    print("事後稽核自我檢查：verify-locks.py 實跑正常（相符回 0、竄改回 1）。")
    print("（run-hidden-tests.py 與 hidden_vault.py 不在這裡驗：驗收跑的是封存庫裡"
          "那一份，它會自己比對簽過章的 sha256——見第三輪 P0-8 (a)。"
          "這支腳本自己仍然驗不了，誰來驗驗證者是個真的循環。）")
    return True


def check_version() -> None:
    """報告產出專案目前的版本號。

    「這個專案沒在管版本」如果是無聲的，它就會一直沒聲音——直到某天使用者回報
    「壞掉了」，而沒有人說得出是哪一版壞的。所以這裡每個 session 都講一次：
    有版本就報版本，沒有就明講缺什麼、怎麼補。
    """
    if not VERSION_SCRIPT.exists():
        return
    result = subprocess.run(
        [sys.executable, str(VERSION_SCRIPT), "--json"],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
    )
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        print("⚠️ 版本號檢查失敗（version.py --json 沒有吐出合法 JSON）。")
        return

    if payload.get("ok"):
        mirrors = payload.get("mirrors") or 0
        suffix = f"，{mirrors} 個顯示位置一致" if mirrors else ""
        print(f"產出專案版本：{payload['version']}（來源 {payload.get('source')}{suffix}）")
        return

    drifted = payload.get("drifted") or []
    if drifted:
        # 「有版本號但各處不一致」跟「沒有版本號」是兩件事，不能講成同一句：
        # 前者要修的是同步，後者要修的是建立。
        print(f"⚠️ 版本號漂掉了：主要來源是 {payload.get('version')}，但這些地方不一致——")
        for item in drifted:
            found = item.get("found") or "找不到版本號"
            print(f"   - {item.get('file')}：{found}")
        print("   不必跑工具就看得到的那些地方寫的是錯的版本，那比沒有版本號更糟。")
        print(f"   同步：python3 scripts/version.py --set {payload.get('version')}")
        return

    print(f"⚠️ 這個專案還沒有版本號（{payload.get('reason', '原因不明')}）——")
    print("   使用者回報問題時將無法定位是哪一版。建立：python3 scripts/version.py --init")


def check_design() -> None:
    """設計基準有沒有被確認過。

    模板附了一份預設色票，所以 clone 下來的專案會**默默繼承**一套美學——
    而美學是使用者的決定，不是模板的。這裡每個 session 提醒一次，直到有人真的問過。
    刻意只提醒不失敗：把它做成錯誤只會逼人隨手填 true 而不是真的去問。
    """
    if not DESIGN_SCRIPT.exists():
        return
    result = subprocess.run(
        [sys.executable, str(DESIGN_SCRIPT), "--json"],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
    )
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return  # 這支腳本本身的問題由它自己的測試守，不在這裡加雜訊

    if payload.get("skipped"):
        return
    if not payload.get("ok"):
        print("⚠️ 設計 token 檢查未通過——前端/GUI task 驗收前要先修："
              "python3 scripts/check-design-tokens.py")
        return
    if not payload.get("confirmed", True):
        print("⚠️ 設計基準還沒有跟使用者確認過（用模板預設還是你有自己的規範？）。")
        print("   派工任何前端/GUI task 之前要先問；問過後在 harness.config.json 設")
        print("   `\"design\": { \"confirmed\": true }`。見 docs/frontend-design-defaults.md §2。")


def check_vault_state() -> None:
    """兩件只有在 session 開始時看一眼才會被發現的事。

    一、**解密殘留**：runner 把隱藏測試解密到封存庫底下，正常結束會刪掉，
    但行程被作業系統直接砍掉時（撞到用量上限、容器被回收）刪不成，明文就一直
    留在磁碟上。這裡順手清掉並說出來——「上一次執行是被砍掉的」本身就是
    verifier 該知道的事。

    二、**墓碑**：權杖遺失後 discard-sealed-task.py 會把 manifest 項目換成墓碑。
    墓碑沒有簽章（權杖都沒了，簽不了），所以它不能靠自己證明真偽——但每個 session
    開始都列出來，作廢就不會是一件安靜發生的事。
    """
    if RUN_HIDDEN.exists():
        result = subprocess.run(
            [sys.executable, str(RUN_HIDDEN), "--sweep"],
            text=True, encoding="utf-8", errors="replace",
            capture_output=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
        )
        for line in (result.stdout or "").splitlines():
            # 只轉述警告；「沒有殘留」是常態，不需要每個 session 都講一次。
            if line.startswith("⚠️") or line.startswith("   "):
                print(line)

    if not HIDDEN_MANIFEST.exists():
        return
    try:
        manifest = json.loads(HIDDEN_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("⚠️ 隱藏測試 manifest 讀不起來（檔案損壞或被改壞），"
              "驗收前請先跑 `python3 scripts/run-hidden-tests.py --list` 確認。")
        return

    discarded, resealed = [], []
    for task_id, info in sorted((manifest.get("tasks") or {}).items()):
        if not isinstance(info, dict):
            continue
        if info.get("status") == "discarded":
            discarded.append((task_id, info))
        elif info.get("discard_count"):
            resealed.append((task_id, info))

    for task_id, info in discarded:
        print(f"⚠️ task「{task_id}」的隱藏測試已作廢（權杖遺失，"
              f"作廢於 {info.get('discarded_at', '未知時間')}）——")
        print("   這個 task 目前沒有可執行的隱藏測試，要重寫一份再封存才能驗收。")
    for task_id, info in resealed:
        print(f"（task「{task_id}」曾因權杖遺失作廢過 {info['discard_count']} 次，"
              "目前這份是重寫後重新封存的；驗收報告要記載這件事）")


def main() -> int:
    parser = argparse.ArgumentParser(description="確認防作弊機制這次真的有生效")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="任何一項防護不符預期就回 exit 1（verifier-reviewer 驗收第 0 步用）",
    )
    args = parser.parse_args()

    print("===== 防作弊機制自我檢查（SessionStart）=====")

    protection = _protection()
    minimal = protection["level"] == "minimal"

    # 等級要在任何檢查之前講。「guard 不見了」與「這個專案本來就只開一層」
    # 是兩件事，同時發生時更要兩句話都看得到。
    check_protection(protection)

    if minimal:
        # minimal 沒有裝 hook，探測一定會全失敗。照跑只會每個 session 印一整片
        # 紅字，而「習慣忽略警告」比少一層保護更難修。所以這裡改成講清楚缺什麼。
        config_ok = check_config()
        check_gitignore()
        check_unprotected_hidden_dirs()
        check_vault_state()
        check_version()
        check_design()
        print("===== 結束 =====")
        return 1 if (args.strict and not config_ok) else 0

    if not GUARD.exists():
        print(f"⚠️ 找不到 {GUARD}——隱藏測試防護目前**完全沒有生效**。")
        print("在修好之前，不要把任何 task 交給 implementer，隱藏測試等同公開。")
        print("===== 結束 =====")
        return 1 if args.strict else 0

    failures = []
    for name, payload, expected in PROBES:
        actual = run_probe(payload)
        if actual != expected:
            failures.append(f"{name}（預期 exit {expected}，實際 {actual}）")

    if failures:
        print(f"⚠️ 防護未如預期運作，{len(failures)}/{len(PROBES)} 項檢查失敗：")
        for item in failures:
            print(f"  - {item}")
        print("這代表隱藏測試暫存區目前可能讀得到或改得到。")
        print("請先執行 python3 scripts/test-guards.py 找出原因，修好之前不要派工給 implementer。")
    else:
        print(f"防護正常：{len(PROBES)}/{len(PROBES)} 項檢查符合預期（隱藏測試讀寫皆被擋、一般檔案不受影響）。")

    config_ok = check_config()
    check_gitignore()
    check_unprotected_hidden_dirs()
    check_locks()
    audit_ok = check_audit_scripts()
    check_vault_state()
    check_version()
    check_design()
    print("===== 結束 =====")

    if args.strict and (failures or not config_ok or not audit_ok):
        # 子智能體是新 session，不會再跑一次 SessionStart hook；
        # verifier-reviewer 靠這個非零 exit 知道「防護在派工中途失效了」。
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
