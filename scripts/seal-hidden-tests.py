#!/usr/bin/env python3
"""seal-hidden-tests.py — 鎖定公開測試、把隱藏測試封存到 repo 之外並加密、簽章 manifest

設計理由（三層隔離、為什麼要加密、宣稱到哪裡為止、為什麼 manifest 要簽章）
全部寫在 scripts/hidden_vault.py 的模組說明，不在這裡重複。

由 **verifier-test-writer** 在寫完公開測試與隱藏測試之後執行，是它交付流程的最後一步。
一個指令做完三件事，順序問題就不存在了（第二輪 P0-7）：

  1. 鎖定公開測試（等同 `lock-tests.py`），並把鎖定清單的 sha256 寫進簽過章的 manifest
     —— 之後清單被整份刪掉或改寫，runner 都分得出「被動過」與「本來就沒有」
  2. 暫存區底下的隱藏測試被逐檔加密（每檔獨立金鑰）搬到封存庫
     （預設 <repo 的父目錄>/.harness-hidden/<repo 名>/），工作目錄不留明文
  3. manifest 項目用權杖推導的 HMAC 簽章，runner 驗簽通過才信任任何欄位

封存前還會檢查這批隱藏測試有沒有進過 git 歷史（第二輪 P1-7）：進了歷史的話
封存沒有意義——`git log -p` 就讀得到，指令字串裡完全不需要出現暫存區路徑。

用法：
    python3 scripts/seal-hidden-tests.py --task-id DEMO-001
    python3 scripts/seal-hidden-tests.py --task-id T2 --test-command "npx vitest run {dir}"

權杖處理規則（重要）：
  - bugfix 任務加 `--kind bugfix`（見 docs/root-cause-and-fix.md §1.5）
  - verifier-test-writer 拿到權杖後**先做基線執行**：
    `python3 scripts/run-hidden-tests.py --task-id <id> --token <權杖> --baseline`
    確認隱藏測試在沒有實作時是紅的（不計入停損次數，結果寫進 manifest）
  - 然後把權杖交回 Orchestrator，自己不要留存
  - Orchestrator 只在派工 verifier-reviewer 時，把權杖放進那個 session 的提示詞
  - 絕對不可以出現在 task-spec、公開測試、驗收報告、commit 訊息裡，
    也不可以傳給任何 implementer

權杖遺失時沒有救援路徑（manifest 只存指紋），只能重新產生一份隱藏測試再封存一次。
這是刻意的：留後門等於留繞過方式。實務上最常見的遺失原因是「執行測試時撞到用量
上限、握有權杖的 session 被回收」——那時先用 `scripts/discard-sealed-task.py`
把解不開的密文作廢、在 manifest 留下墓碑，再依同一份 task-spec 重寫一份隱藏測試
回到這裡重新封存。作廢過的 task 重新封存時，`discard_count` 會帶進**簽過章**的
新項目，所以作廢不會把歷史洗白。
"""
import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hidden_vault as vault  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
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


def _git(root: Path, *args) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout if result.returncode == 0 else ""


def git_exposure(root: Path, files) -> list:
    """這批隱藏測試有沒有被 git 追蹤、或出現在任何一個 commit 裡。

    沒有 git、或 repo 不是 git repo 時回空清單——那不是錯誤，只是沒有歷史可查。
    逐檔查而不是查整個暫存區：暫存區裡的 README.md / .gitkeep 是刻意進版控的。
    """
    if shutil.which("git") is None or not (root / ".git").exists():
        return []
    problems = []
    for source, _relative in files:
        try:
            rel = source.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if _git(root, "ls-files", "--", rel).strip():
            problems.append(f"已被 git 追蹤：{rel}")
            continue
        if _git(root, "log", "--all", "--oneline", "--", rel).strip():
            problems.append(f"曾出現在 git 歷史裡：{rel}")
    return problems


def lock_public_tests(root: Path) -> int:
    """鎖定公開測試（第二輪 P0-7：封存前一定先鎖，順序問題就消失了）。"""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "lock-tests.py")],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(root)},
    )
    for line in (result.stdout or "").splitlines():
        print(f"  {line}")
    if result.returncode != 0:
        print((result.stderr or "").rstrip(), file=sys.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="鎖定公開測試、封存隱藏測試到 repo 之外並加密、簽章 manifest")
    parser.add_argument("--task-id", required=True, help="這批隱藏測試對應的 task_id")
    parser.add_argument(
        "--kind",
        choices=vault.TASK_KINDS,
        default=vault.DEFAULT_TASK_KIND,
        help=(
            "task 種類。bugfix＝修 bug 的任務：公開測試是被回報的那一個最小重現，"
            "隱藏測試是同一個根因的其他變體，用來驗「修的是根因還是只修了那一個 case」"
            "（見 docs/root-cause-and-fix.md）"
        ),
    )
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

    exposure = git_exposure(root, files)
    if exposure:
        print("拒絕封存：這批隱藏測試已經在 git 歷史裡，封存沒有意義。", file=sys.stderr)
        for problem in exposure:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "implementer 用 `git log -p` / `git show` 就讀得到，指令字串裡完全不需要出現暫存區路徑，\n"
            "封存腳本刪掉工作目錄裡的明文對歷史沒有作用。請換一批測試，並確認 .gitignore\n"
            "排除了暫存區（本模板預設已排除 tests/hidden/*）。",
            file=sys.stderr,
        )
        return 1

    try:
        vault_dir = vault.resolve_vault_dir(root)
        vault.assert_outside_repo(vault_dir, root)
    except ValueError as exc:
        print(f"拒絕封存：{exc}", file=sys.stderr)
        return 1

    print("先鎖定公開測試：")
    if lock_public_tests(root) != 0:
        print("拒絕封存：鎖定公開測試失敗，封存必須建立在已鎖定的清單上。", file=sys.stderr)
        return 1
    locked_sha = vault.sha256_file(vault.locked_list_path(root))

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
        target.write_bytes(vault.transform(plaintext, token, relative))
        entries.append(
            {
                "path": relative,
                "sha256": vault.sha256_bytes(plaintext),
                "bytes": len(plaintext),
            }
        )

    # 驗收用的程式碼跟隱藏測試一起封存（第三輪 P0-8 (a)）。放在密文之後、
    # manifest 之前：複製失敗時還沒寫 manifest，不會留下「指到不存在的 runner」的項目。
    runner_files = vault.copy_runner(vault_dir, Path(__file__).resolve().parent)

    manifest = vault.load_manifest(root)
    previous_version = manifest.get("version")
    previous_entry = manifest.get("tasks", {}).get(args.task_id) or {}
    was_discarded = vault.is_discarded(previous_entry)
    manifest["version"] = vault.MANIFEST_VERSION
    entry = {
        # 重新封存同一個 task 時把 runner 已記錄的嘗試筆數帶過來——attempts.json 是
        # 跨封存累積的，計數歸零會讓帶權杖的 show-attempts 誤判成「有紀錄被刪」。
        # 這個值來自舊 entry（舊權杖簽的，這裡驗不了）；被亂改的後果只會是
        # 「次數不可信 → should_stop 未知」，永遠不會變成假的「沒有失敗過」。
        "attempts_recorded": int(previous_entry.get("attempts_recorded") or 0),
        # 在簽章範圍內：implementer 改不掉，所以 runner 可以信任它來決定怎麼判讀失敗。
        "kind": args.kind,
        # 這個 task 被作廢過幾次（權杖遺失）。帶進**簽過章**的項目裡，作廢才不會
        # 把歷史洗白——墓碑本身沒有簽章，但一旦重新封存，這個數字就受簽章保護了。
        "discard_count": int(previous_entry.get("discard_count") or 0),
        "sealed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault_dir": str(vault_dir),
        "task_dir": str(task_dir),
        "test_command": args.test_command or vault.test_command(root),
        "token_sha256": vault.token_fingerprint(token),
        "files": entries,
        # 封存當下鎖定清單的 sha256。之後清單被刪或被改，runner 都分得出來。
        "locked_tests_sha256": locked_sha,
        # 第三輪 P0-8 (a)：驗收時會執行的程式碼也一起封存，並把每個檔案的 sha256
        # 放進簽章範圍。簽章金鑰由權杖推導，implementer 沒有權杖，所以改了封存版
        # 程式碼就一定對不上——而 repo 裡那一份他改得到，改了也不再是驗收執行的那一份。
        "runner_files": runner_files,
        # 基線執行（--baseline）之後才會填：證明這份隱藏測試在沒有實作時是紅的。
        "baseline": None,
    }
    entry["signature"] = vault.sign_entry(entry, token)
    manifest["tasks"][args.task_id] = entry
    vault.save_manifest(manifest, root)

    # 封存流水帳（第三輪 P0-9）：manifest 那一筆被整份刪掉時，runner 還有第二個
    # 地方可以問「這個 task 封存過沒有」。記的是權杖**指紋**，不是權杖。
    vault.append_sealed_log(vault_dir, args.task_id, "sealed", entry["token_sha256"])

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
    if was_discarded:
        print(f"（這個 task 先前因權杖遺失被作廢過，累計 {entry['discard_count']} 次；"
              "墓碑已由這次封存取代）")
        print("（作廢次數與已記錄的嘗試筆數都已帶進新項目，並在簽章範圍內——"
              "verifier-reviewer 要把「曾經作廢」寫進驗收報告）")
    if args.kind == "bugfix":
        print("種類：**bugfix**——這批隱藏測試是「同一個根因的其他變體」，")
        print("      被回報的那一個最小重現應該放在**公開測試**裡（implementer 要看得到）。")
    print(f"來源暫存區：{staging_labels}")
    print(f"已封存 {len(entries)} 個隱藏測試檔案（每檔獨立金鑰）：")
    for item in entries:
        print(f"  - {item['path']}（{item['bytes']} bytes）")
    print(f"封存位置（repo 之外、內容已加密）：{task_dir}")
    print(f"驗收用的程式碼也已封存（{len(runner_files)} 個檔案，sha256 已進簽章範圍）：")
    print(f"  {vault.runner_dir(vault_dir)}")
    print(f"manifest：{vault.manifest_path(root).relative_to(root)}（項目已用權杖簽章）")
    if previous_version not in (None, vault.MANIFEST_VERSION):
        print(f"（manifest 從版本 {previous_version} 升到 {vault.MANIFEST_VERSION}；"
              "舊版本封存的其他 task 沒有簽章，要重新封存才跑得動）")
    print()
    print("工作目錄裡已經沒有隱藏測試的明文。這套機制擋的是「順手看一眼」「習慣性搜整個 repo」")
    print("這類非刻意的洩題；它不擋一個決心繞過、且跟 verifier 共用同一個 OS 使用者的")
    print("子智能體——那需要獨立的使用者或容器，超出本模板範圍")
    print("（見 scripts/hidden_vault.py 模組說明）。")
    print()
    print("=" * 60)
    print(f"執行權杖（只會出現這一次）：{token}")
    print("=" * 60)
    print()
    sealed_runner = vault.runner_dir(vault_dir) / "run-hidden-tests.py"
    print("接下來：")
    print("  1. 立刻做基線執行，證明這份隱藏測試在沒有實作時是紅的（不計入停損次數）：")
    print(f'     python3 "{sealed_runner}" --task-id {args.task_id} --token <權杖> --baseline')
    print("  2. 把這串權杖交回 Orchestrator，你自己不要留存。")
    print("  3. Orchestrator 只在派工 verifier-reviewer 時把它放進那個 session。")
    print("  4. 絕對不要寫進 task-spec、公開測試、驗收報告或 commit 訊息，")
    print("     更不要交給任何 implementer——那等於把隱藏測試變成可查詢的 oracle。")
    print()
    print("verifier-reviewer 驗收時執行（**用這個絕對路徑，不要用 repo 裡那一份**）：")
    print(f'  python3 "{sealed_runner}" --task-id {args.task_id} --token <權杖>')
    print()
    print("為什麼不是 `python3 scripts/run-hidden-tests.py`：repo 裡那一份 implementer")
    print("改得到——第三輪實測，覆寫它之後用完全正確的權杖驗收會得到 exit 0 與")
    print("「隱藏測試全部通過」，而三項事後稽核都回報正常。封存版在 repo 之外，")
    print("而且它的 sha256 在簽過章的 manifest 裡，改了就跑不動。")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
