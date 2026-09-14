#!/usr/bin/env python3
"""env-guard.py — 記錄並比對「預期執行環境」的指紋（Win/Linux/Mac 通用）
對應需求第 14 點：若明確告知要在其他機器運行，須向使用者確認相關資訊

設計原則：本模板不預設一定跑在哪台機器，而是「第一次執行時記錄指紋、
之後每次執行都比對」。指紋不符時不會自動判斷對錯，只負責清楚回報，
交由 Orchestrator / 使用者決定；但仍以非 0 的 exit code 表示「有不符」，
讓呼叫方（例如 scripts/init.py）能明確分辨，不用去猜文字輸出。

2026-09-09 code review 後修正：新增 arch 欄位比對時，沒有處理「舊版指紋檔
沒有 arch 欄位」的情況——若不特別處理，每次都會被誤判成「架構不符」，
即使機器根本沒換過。現在缺欄位視為「需要補齊」而不是「不符」，
補齊後直接覆寫回檔案，不當一次真正的環境變更事件。

2026-09-10（對應 docs/history/improvement-suggestions.md 的 P3-1）：舊版指紋只有
hostname/os/arch，而容器與雲端執行環境每次啟動 hostname 都是隨機的，於是這支
腳本每次都回報「不符」。一個總是誤報的守門機制比沒有更糟——它會訓練使用者
忽略警告。現在改成：偵測到容器環境時 hostname 不參與比對，改以「能力類」欄位
（os/arch/是否容器/CPU 級距/記憶體級距）判斷是不是同一台機器。
比對規則的完整理由見 scripts/env_fingerprint.py。

同一版新增 `--update`：確認過確實換了機器之後，用一行指令把目前環境設為新基準。
在此之前只能手動改 .harness/env-fingerprint.json，但那個檔案被 guard hook 擋著
不給編輯，等於沒有正當管道。

用法：
    python3 scripts/env-guard.py            # 比對（沒有基準時建立）
    python3 scripts/env-guard.py --update   # 把目前環境設為新的基準指紋

exit code：
    0 = 相符（或首次建立基準、或剛完成 --update）
    1 = 不符 —— Orchestrator 應停止派工並向使用者確認
"""
import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_fingerprint  # noqa: E402
import machine_facts  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def _repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


REPO_ROOT = _repo_root()
FINGERPRINT_FILE = REPO_ROOT / ".harness" / "env-fingerprint.json"


def current_fingerprint() -> dict:
    return env_fingerprint.build(
        hostname=socket.gethostname(),
        os_name=platform.system(),
        arch=platform.machine(),
        container=machine_facts.in_container(),
        cpu_count=machine_facts.cpu_count(),
        memory_mb=machine_facts.memory_mb(),
        hostname_stable=machine_facts.hostname_is_stable(),
    )


def save(fingerprint: dict) -> None:
    FINGERPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    FINGERPRINT_FILE.write_text(
        json.dumps(fingerprint, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def print_fingerprint(fingerprint: dict, indent: str = "  ") -> None:
    for line in env_fingerprint.describe(fingerprint):
        print(f"{indent}{line}")


def baseline_is_tracked():
    """基準指紋檔會不會跟著 repo 一起走。

    第三輪 P1-11：`.harness/` 在 `.gitignore` 裡（這是對的，它是每台機器的當下
    狀態），但在「每個 session 都重新 clone」的執行環境裡——容器、CI、遠端 agent
    沙箱——這代表基準**永遠不會跨 session 存在**，於是每次都走 `created` 分支：
    寫一份新基準、印「狀態：正常」、什麼都沒比對。守門機制在那裡等同停用，
    而且沒有任何訊號。

    回傳 True／False／None（沒有 git 或不是 repo，判斷不出來）。
    刻意不去猜「這是不是拋棄式環境」——那是列舉式判斷，下一種環境就會漏
    （本模板第一輪 P3-1 列舉容器特徵，第三輪就在一個全都沒命中的環境裡失效）。
    這裡只回答一個查得出來的事實，把「所以要不要擔心」留給訊息本身講清楚。
    """
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        return None
    try:
        relative = FINGERPRINT_FILE.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return None
    result = subprocess.run(
        ["git", "check-ignore", "-q", relative],
        cwd=str(REPO_ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode != 0


def evaluate(update: bool) -> dict:
    """比對（或更新）指紋，回傳純資料結果。

    先算出結果再決定怎麼呈現，是為了讓 `--json` 跟人看的輸出走同一條邏輯——
    兩邊各寫一次的話，遲早會出現「JSON 說相符、文字說不符」這種最糟的狀況。
    """
    current = current_fingerprint()
    result = {
        "scan": "env-guard",
        "status": None,
        "exit_code": 0,
        "fingerprint": current,
        "previous": None,
        "mismatches": [],
        "filled_missing": [],
        "skipped": [],
        "baseline_tracked": None,
    }

    if update and FINGERPRINT_FILE.exists():
        result["previous"] = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8"))
        save(current)
        result["status"] = "updated"
        return result

    if not FINGERPRINT_FILE.exists():
        save(current)
        result["status"] = "created"
        result["baseline_tracked"] = baseline_is_tracked()
        return result

    recorded = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8"))
    mismatches, missing, skipped = env_fingerprint.compare(recorded, current)

    if missing:
        # 舊版指紋檔沒有這些欄位，視為「需要補齊」而不是「不符」——
        # 沒有紀錄過的東西無從比對起。
        recorded.update({key: current[key] for key in missing})
        save(recorded)
        result["filled_missing"] = list(missing)

    result["previous"] = recorded
    result["skipped"] = [reason for _key, reason in skipped]
    result["mismatches"] = [
        {"field": key, "label": label, "recorded": before, "current": after}
        for key, label, before, after in mismatches
    ]
    result["status"] = "mismatch" if mismatches else "ok"
    result["exit_code"] = 1 if mismatches else 0
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="記錄並比對預期執行環境的指紋")
    parser.add_argument(
        "--update",
        action="store_true",
        help="把目前環境設為新的基準指紋（確認過確實換了機器之後才用）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="輸出機器可讀的 JSON（stdout 只有 JSON；exit code 與人看的模式相同）",
    )
    args = parser.parse_args()

    result = evaluate(args.update)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return result["exit_code"]

    return print_human(result)


def print_human(result: dict) -> int:
    print("===== 環境指紋守門 =====")
    current = result["fingerprint"]

    if result["status"] == "updated":
        previous = result["previous"]
        print("已把目前環境設為新的基準指紋。")
        print("原本記錄的環境：")
        print_fingerprint(previous)
        print("新的基準：")
        print_fingerprint(current)
        print()
        print("提醒：換機器通常也代表可用資源、既有服務、連接埠都不一樣了，")
        print("請一併重新執行 scripts/machine-profile.py 與 scripts/service-scan.py。")
        print("===== 結束 =====")
        return 0

    if result["status"] == "created":
        print("尚未記錄過預期執行環境，已將目前環境設為基準：")
        print_fingerprint(current)
        if not current["hostname_stable"]:
            print()
            print("（偵測到主機名稱不穩定的執行環境（容器／K8s／Codespaces／CI）：")
            print("  主機名稱每次重建都會變，因此之後不列入比對，")
            print("  改以作業系統／架構／CPU 與記憶體級距判斷是不是同一種執行環境）")
        # 第三輪 P1-11：這裡以前印「狀態：正常（首次執行）」。那句話是錯的——
        # 首次執行**什麼都沒比對**，守門這一次完全沒有生效。而在每個 session
        # 都重新 clone 的環境裡（容器、CI、遠端 agent 沙箱），因為 .harness/
        # 被 gitignore，每一次都會是首次執行：機制永遠不會生效，而且永遠在說「正常」。
        print("狀態：**本次沒有比對任何東西**——基準是這一次才建立的。")
        print("      環境指紋守門要到下一次執行才會真的生效。")
        if result.get("baseline_tracked") is False:
            print()
            print("⚠️ 基準指紋檔在 .gitignore 裡（這是對的，它是每台機器的當下狀態），")
            print("   但這也代表它**不會跟著 repo 走**。如果這個執行環境每個 session")
            print("   都重新 clone（容器、CI、遠端 agent 沙箱），那麼每次都會是「首次執行」，")
            print("   環境指紋守門在這裡**等同停用**——黃金法則第 6 條不會有機會觸發。")
            print("   要在這類環境真的守得住，基準必須放在會跨 session 保留的地方")
            print("   （Orchestrator 的跨 session 記憶，見 docs/memory-management.md）。")
        print("===== 結束 =====")
        return 0

    if result["filled_missing"]:
        labels = "、".join(
            env_fingerprint.FIELD_LABELS.get(key, key) for key in result["filled_missing"]
        )
        print(f"（偵測到舊版指紋檔缺少欄位：{labels}，已自動補上目前值，不視為不符）")

    for reason in result["skipped"]:
        print(f"（{reason}）")

    if result["mismatches"]:
        for item in result["mismatches"]:
            print(f"⚠️ {item['label']}不符：記錄為「{item['recorded']}」，目前為「{item['current']}」")
        print()
        print("狀態：不符 —— 這台機器可能跟先前規劃時不同。")
        print("請 Orchestrator 停止自動派工，向使用者確認：")
        print("  1) 這是否為刻意換到的新機器／遠端伺服器？")
        print("  2) 若是，這台機器的資源限制、既有服務、用途為何？")
        print("  3) 確認後執行 `python3 scripts/env-guard.py --update` 更新基準指紋。")
        print("===== 結束 =====")
        return 1

    print("狀態：正常（與記錄的預期環境一致）")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
