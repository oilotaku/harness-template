#!/usr/bin/env python3
"""setup.py — 一鍵設定精靈：把「clone 之後要手寫設定 + 記一串指令」壓成跑一支腳本

## 為什麼要這支

在此之前，一個 clone 下來的人要先讀 docs/getting-started.md（很長），
再自己判斷：我的專案是什麼語言？測試放哪？要不要開 hook？版本號放哪？
——這些決定**不做也得做**，而規格沒寫的時候新手只能猜，或乾脆整套不用。

這支腳本把「入門」這件事變成一個指令：

  1. 偵測專案語言（Python / Node / Go / Rust），據此填測試路徑與測試指令
  2. 問幾個真正需要人決定的問題（保護等級、CI 驗收、有無圖形介面、releases 歸檔），其餘給安全預設
  3. 產生 harness.config.json（**絕不覆蓋既有的**，除非 --force）
  4. 建立版本號（呼叫 version.py --init）
  5. 印出「你的下一個指令」——照順序，複製貼上就能跑通第一次 seal→verify

它刻意**不做**機器/服務掃描（那是 init.py 的事）與封存（那是 verifier 的事）——
把設定跟掃描分開，各自能單獨重跑，壞了也好定位。

## 用法

    python3 scripts/setup.py                 # 互動式（會問幾個問題）
    python3 scripts/setup.py --yes           # 全用預設，不問（給 CI / 自動化）
    python3 scripts/setup.py --level minimal # 指定保護等級
    python3 scripts/setup.py --lang node     # 指定語言（偵測不準時）
    python3 scripts/setup.py --force         # 覆蓋既有的 harness.config.json

設計原則跟這個 repo 其他地方一致：**絕不靜默覆蓋**（既有設定要 --force 才動）、
**產生的設定一定先用 harness_config.load() 驗過**（不把壞設定留給使用者）。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent


# 每種語言的測試慣例與版本來源。偵測不準時可用 --lang 覆寫。
# 對照 docs/multi-language-support.md：那裡列的就是這幾組。
LANG_PROFILES = {
    "python": {
        "label": "Python",
        "public": ["tests/public"],
        "hidden": ["tests/hidden"],
        "command": "{python} -m unittest discover -s {dir} -p 'test_*.py' -v",
        "version": {"file": "VERSION", "format": "plain"},
        "trusted_command": True,
    },
    "node": {
        "label": "Node / TypeScript（vitest）",
        "public": ["tests/public"],
        "hidden": ["tests/hidden"],
        "command": "npx vitest run --dir {dir}",
        "version": {"file": "package.json", "format": "json", "key": "version"},
        "trusted_command": True,
    },
    "go": {
        "label": "Go",
        # Go 要求測試跟被測程式同 package，所以暫存區另外開一個目錄。
        "public": ["internal/testsuite/public"],
        "hidden": ["internal/testsuite/hidden"],
        "command": "go test {dir}/...",
        "version": {"file": "VERSION", "format": "plain"},
        "trusted_command": True,
    },
    "rust": {
        "label": "Rust",
        "public": ["tests/public"],
        "hidden": ["tests/hidden"],
        # docs/multi-language-support.md 沒有給 Rust 的範例——cargo 的測試是編進 crate 的，
        # 不像其他語言能直接對一個目錄跑。所以這裡填的是**佔位**，標成不可信，
        # 讓下面的流程明確提醒使用者去確認，而不是假裝我猜得準。
        "command": "{python} -m unittest discover -s {dir} -p 'test_*.py' -v",
        "version": {"file": "Cargo.toml", "format": "toml", "section": "package"},
        "trusted_command": False,
    },
    "unknown": {
        "label": "未知（找不到語言標記檔）",
        "public": ["tests/public"],
        "hidden": ["tests/hidden"],
        "command": "{python} -m unittest discover -s {dir} -p 'test_*.py' -v",
        "version": {"file": "VERSION", "format": "plain"},
        "trusted_command": False,
    },
}

# 偵測順序有意義：一個 repo 可能同時有 package.json 與 .py（前端 + 腳本），
# 以「哪個是主要測試對象」為準。這裡把編譯型語言的標記排在前面，
# 因為它們的測試慣例最不像預設值，猜錯的代價最大。
DETECT_ORDER = [
    ("go", ["go.mod"]),
    ("rust", ["Cargo.toml"]),
    ("node", ["package.json"]),
    ("python", ["pyproject.toml", "setup.py", "setup.cfg"]),
]


def detect_language(root: Path) -> str:
    for lang, markers in DETECT_ORDER:
        if any((root / marker).exists() for marker in markers):
            return lang
    # 沒有標記檔，但有 .py 也算 Python（很多腳本專案沒有 pyproject）。
    if any(root.glob("*.py")) or any(root.glob("**/*.py")):
        return "python"
    return "unknown"


def ask(prompt: str, default: str, choices=None, interactive=True) -> str:
    """問一個問題。非互動（--yes 或沒有 tty）時直接回預設，並把選擇印出來。

    非互動時**印出用了什麼預設**，不是靜默——「以為自己選了 X、其實是預設 Y」
    正是這個 repo 最想避免的沉默失效。
    """
    if not interactive:
        print(f"  {prompt} → {default}（預設）")
        return default
    hint = f"[{'/'.join(choices)}]" if choices else f"[{default}]"
    try:
        answer = input(f"  {prompt} {hint}：").strip()
    except EOFError:
        print(f"（沒有輸入，用預設 {default}）")
        return default
    if not answer:
        return default
    if choices and answer not in choices:
        print(f"  「{answer}」不是有效選項，用預設 {default}。")
        return default
    return answer


def build_config(lang: str, level: str, ci: bool, design_gui: bool, archive: bool) -> dict:
    profile = LANG_PROFILES[lang]
    # 複製 version：profile["version"] 是 LANG_PROFILES 裡的共用 dict，
    # 直接塞 archive 會污染模組層級的設定（下一次呼叫就帶著上一次的選擇）。
    version = dict(profile["version"])
    if not archive:
        # 只有「關掉」需要寫出來——省略等於預設開啟（見 docs/versioning.md §4.5）。
        version["archive"] = False
    config = {
        "$comment": "由 scripts/setup.py 產生。可以手改；改完直接跑任何一支腳本，設定壞掉會 fail-closed 報錯（見 docs/multi-language-support.md）。",
        "public_test_paths": profile["public"],
        "hidden_test_paths": profile["hidden"],
        "hidden_test_command": profile["command"],
        "version": version,
        "protection": {"level": level, "ci_verification": ci},
    }
    if not design_gui:
        # 沒有圖形介面就明確關掉——不要逼 CLI / 函式庫維護一份用不到的色票。
        config["design"] = False
    else:
        # 有圖形介面：保留預設 token 檔，但標成「還沒跟使用者確認過」，
        # 讓 guard-selfcheck 每個 session 提醒到有人真的回答為止。
        config["design"] = {"tokens": "design.tokens.json", "confirmed": False}
    return config


def write_config(root: Path, config: dict) -> None:
    path = harness_config.config_path(root)
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def init_version(root: Path) -> None:
    """呼叫 version.py --init 建立版本號（沒有才建，version.py 自己會判斷）。"""
    script = SCRIPTS_DIR / "version.py"
    if not script.exists():
        return
    subprocess.run(
        [sys.executable, str(script), "--init"],
        cwd=str(root),
        env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(root)},
    )


def print_next_steps(config: dict, lang: str) -> None:
    hidden = config["hidden_test_paths"][0]
    level = config["protection"]["level"]
    ci = config["protection"]["ci_verification"]

    print()
    print("=" * 60)
    print("設定完成。你的下一步（照順序，複製貼上就能跑通第一次驗收）：")
    print("=" * 60)
    print()
    print("1. 掃描機器與環境（決定平行度、確認環境指紋）：")
    print("     python3 scripts/init.py")
    print()
    print(f"2. 把第一個 task 的隱藏測試寫進 {hidden}/，然後封存：")
    print("     python3 scripts/seal-hidden-tests.py --task-id T-001")
    print("   （封存會印出一串**只出現一次**的權杖，與封存版 runner 的絕對路徑。）")
    print()
    print("3. 證明測試在「還沒有實作」時是紅的（用上一步印的絕對路徑）：")
    print('     python3 "<封存版 runner>" --task-id T-001 --token <權杖> --baseline')
    print()
    print("4. 交給 implementer 實作；完成後用同一個權杖驗收：")
    print('     python3 "<封存版 runner>" --task-id T-001 --token <權杖>')
    if ci:
        print()
        print("   （你開了 CI 驗收：正式驗收是 PR 上那個 check。封存後還要把")
        print("     ci/sealed/ commit 進預設分支、權杖存進 repository secret，")
        print("     見 docs/ci-verification.md。）")
    print()
    print("想先看它整套跑一次、不動到你的專案：")
    print("     templates/examples/demo-fizzbuzz/（一份完整的範例 task）")
    print()
    print("完整流程與每一步的「怎麼確認成功了」：docs/getting-started.md")
    if level == "minimal":
        print()
        print(f"提醒：你選了 protection.level = minimal——{harness_config_minimal_note()}")


def harness_config_minimal_note() -> str:
    # 不 import hidden_vault（那會拉進一堆封存相依）；這句話跟它的 MINIMAL_NOTICE 同義。
    return ("只有第 1 層（實體隔離）生效，不必裝 hook；代價是公開測試被改不會被抓到。"
            "見 docs/protection-levels.md。")


def main() -> int:
    parser = argparse.ArgumentParser(description="一鍵設定精靈：偵測語言、產生 harness.config.json、建立版本號")
    parser.add_argument("--yes", "-y", action="store_true", help="全用預設，不互動發問（給 CI / 自動化）")
    parser.add_argument("--lang", choices=[k for k in LANG_PROFILES if k != "unknown"],
                        help="指定專案語言（偵測不準時用）")
    parser.add_argument("--level", choices=list(harness_config.PROTECTION_LEVELS),
                        help="保護等級：minimal（只留實體隔離）或 full（四層）")
    parser.add_argument("--ci", dest="ci", action="store_true", help="開啟 CI 驗收（protection.ci_verification）")
    parser.add_argument("--gui", dest="gui", action="store_true", help="這個專案有圖形介面（保留設計 token 檢查）")
    parser.add_argument("--no-gui", dest="gui", action="store_false", help="這個專案沒有圖形介面（關掉設計 token 檢查）")
    parser.add_argument("--archive", dest="archive", action="store_true", help="升版時把原始碼另存一份到 releases/（預設）")
    parser.add_argument("--no-archive", dest="archive", action="store_false", help="不做 releases/ 歸檔（避免 repo 隨版本膨脹）")
    parser.add_argument("--force", action="store_true", help="覆蓋既有的 harness.config.json")
    parser.set_defaults(gui=None, ci=None, archive=None)
    args = parser.parse_args()

    root = harness_config.repo_root()
    interactive = (not args.yes) and sys.stdin.isatty()

    print("===== Harness 設定精靈 =====")
    print(f"專案目錄：{root}")

    config_file = harness_config.config_path(root)
    if config_file.exists() and not args.force:
        print()
        print(f"⚠️ 已經有 {harness_config.CONFIG_FILENAME} 了——不覆蓋（要覆蓋請加 --force）。")
        try:
            existing = harness_config.load(root)
            print(f"   目前保護等級：{existing['protection']['level']}"
                  f"（CI 驗收：{'開' if existing['protection']['ci_verification'] else '關'}）")
            print(f"   隱藏測試暫存區：{'、'.join(existing['hidden_test_paths'])}")
            print("   設定沒問題。直接看下一步：")
            print_next_steps(existing, "（既有設定）")
            return 0
        except harness_config.ConfigError as exc:
            print(f"   但它有問題：{exc}", file=sys.stderr)
            print("   修好它，或用 --force 讓精靈重新產生一份。", file=sys.stderr)
            return 1

    # 1. 語言
    lang = args.lang or detect_language(root)
    profile = LANG_PROFILES[lang]
    print()
    if args.lang:
        print(f"語言（你指定）：{profile['label']}")
    else:
        print(f"偵測到的語言：{profile['label']}")
        if interactive and lang != "unknown":
            chosen = ask("這對嗎？直接 Enter 接受，或輸入 python/node/go/rust",
                         lang, interactive=interactive)
            if chosen in LANG_PROFILES:
                lang = chosen
                profile = LANG_PROFILES[lang]
    if not profile["trusted_command"]:
        print(f"⚠️ {profile['label']} 的測試指令我沒有可靠範例，先填了預設佔位。")
        print("   產生設定後**務必**確認 hidden_test_command 跑得起來，見 docs/multi-language-support.md。")

    # 2. 保護等級（真正需要人決定的問題之一）
    if args.level:
        level = args.level
        print(f"\n保護等級（你指定）：{level}")
    else:
        print()
        print("保護等級：")
        print("  full    —— 四層全開（要裝 .claude/settings.json 的 hook）")
        print("  minimal —— 只留實體隔離（不必裝 hook；公開測試被改不會被抓到）")
        level = ask("選哪個？", "full", choices=["full", "minimal"], interactive=interactive)

    # 3. CI 驗收
    if args.ci is not None:
        ci = args.ci
        print(f"CI 驗收（你指定）：{'開' if ci else '關'}")
    else:
        ci_ans = ask("要不要用 GitHub Actions 做正式驗收？（把驗收搬到 implementer 碰不到的地方）",
                     "n", choices=["y", "n"], interactive=interactive)
        ci = ci_ans == "y"

    # 4. 圖形介面
    if args.gui is not None:
        design_gui = args.gui
        print(f"圖形介面（你指定）：{'有' if design_gui else '沒有'}")
    else:
        gui_ans = ask("這個專案有圖形介面（要驗色彩對比之類的設計基準）嗎？",
                      "n", choices=["y", "n"], interactive=interactive)
        design_gui = gui_ans == "y"

    # 5. releases 歸檔（預設開；問一次而不是靜默套用——git tag 已能取到任一版，
    #    但每升一版複製一份原始碼會讓 repo 線性膨脹，值不值得由使用者決定，見 docs/versioning.md §4.5）
    if args.archive is not None:
        archive = args.archive
        print(f"releases/ 歸檔（你指定）：{'開' if archive else '關'}")
    else:
        archive_ans = ask("每次升版要不要把原始碼另存一份到 releases/？（不會用 git 也能拿到某一版；關掉可避免 repo 隨版本膨脹）",
                          "y", choices=["y", "n"], interactive=interactive)
        archive = archive_ans == "y"

    # 6. 產生設定，並**一定先驗過**再落地
    config = build_config(lang, level, ci, design_gui, archive)
    write_config(root, config)
    try:
        harness_config.load(root)
    except harness_config.ConfigError as exc:
        print(f"\n❌ 產生的設定沒通過驗證：{exc}", file=sys.stderr)
        print("這是精靈自己的 bug，請回報。設定檔已寫出，你可以手動修。", file=sys.stderr)
        return 1

    print()
    print(f"✅ 已產生 {harness_config.CONFIG_FILENAME}：")
    print(f"   語言：{profile['label']}")
    print(f"   公開測試：{'、'.join(config['public_test_paths'])}")
    print(f"   隱藏測試暫存區：{'、'.join(config['hidden_test_paths'])}")
    print(f"   測試指令：{config['hidden_test_command']}")
    print(f"   保護等級：{level}（CI 驗收：{'開' if ci else '關'}）")
    print(f"   設計 token 檢查：{'開（未確認）' if design_gui else '關'}")
    print(f"   releases/ 歸檔：{'開（每次升版另存原始碼）' if archive else '關（只靠 git 版本歷史）'}")

    # 7. 版本號
    print()
    print(">>> 建立版本號（version.py --init）")
    init_version(root)

    # 8. 下一步
    print_next_steps(config, lang)
    print()
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
