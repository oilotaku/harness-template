#!/usr/bin/env python3
"""discard-sealed-task.py — 權杖遺失後，作廢一個 task 的封存並記下墓碑

## 為什麼需要這支腳本

執行權杖只在封存當下印出一次，manifest 只存指紋。所以只要握有權杖的那個
session 消失了（最常見的原因：**執行測試時撞到用量上限，session 被回收**），
密文就永遠解不開了——而封存時工作目錄裡的明文已經刪掉，連「重新封存」都做不到。

這支腳本**不是救援路徑**，它做的是相反的事：把那份再也用不到的密文移進封存庫的
`_discarded/`、清掉可能殘留的明文，在 manifest 留下一塊墓碑、往 repo 之外的封存
流水帳追加一行，然後明確告訴使用者「這個 task 要重寫一份隱藏測試」。
留後門等於留繞過方式，所以恢復的方式只有重寫，沒有第二條。

第三輪 P0-9 之前，密文是直接刪掉的。改成搬家而不是刪除，是因為墓碑可以被整份
刪掉——刪完之後「被作廢」與「從來沒封存過」的訊息一模一樣，而後者看起來只是
Orchestrator 派工打錯字。密文沒有權杖本來就解不開，留著不增加洩題風險，
卻讓「這個 task 存在過」多一份要另外動手才抹得掉的證據。

在它出現之前，撞到這個狀態的人看到的是「權杖錯誤：指紋與封存當下記錄的不符」——
一個會讓人以為只是打錯字、於是去翻找一個根本不存在的權杖的訊息。

## 墓碑為什麼可以沒有簽章

簽章金鑰由權杖推導，而權杖正是遺失的那個東西，所以墓碑簽不了章，任何人都能偽造。
這不構成新的攻擊面：墓碑的唯一效果是讓 runner **拒絕執行**（exit 2），
它永遠不可能產生假的「隱藏測試全部通過」。一個 implementer 偽造墓碑，換到的是
「驗收無法進行」而不是「驗收通過」——對他沒有好處，而且：

  - 下一次封存會把 `discard_count` 累加進**簽過章**的新項目，
    verifier-reviewer 看得到這個 task 被作廢過幾次
  - `attempts_recorded` 會原封不動帶過去，把它改小會讓 show-attempts 判定
    「有紀錄被刪」（attempts.summary 的 expected_total 比對）
  - `guard-selfcheck.py` 每個 session 開始時都會把墓碑列出來

同樣誠實記載的還有：這支腳本自己算出封存庫路徑，指令字串裡不會出現受保護路徑，
所以 `guard-hidden-tests.py` 不會擋它——任何拿得到 Bash 的子智能體都跑得動。
上面那段就是為什麼這樣仍然可以接受。

用法：
    python3 scripts/discard-sealed-task.py --task-id DEMO-001 --token-lost --confirm
    python3 scripts/discard-sealed-task.py --task-id DEMO-001 --token <權杖>   （先確認權杖到底對不對）

exit code：
    0 = 已作廢，墓碑已寫入
    1 = 拒絕作廢：manifest 裡沒有這個 task、已經是墓碑、或權杖其實是對的
    2 = 參數不足（缺 --token-lost / --confirm）
"""
import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hidden_vault as vault  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文


def summarise_previous(info: dict) -> dict:
    """墓碑裡保留的封存紀錄。

    刻意只留「說得出這個 task 曾經有過什麼」所需的欄位，不留任何能幫助解密的東西
    （密文會被移進作廢區、沒有權杖解不開，token_sha256 只是指紋，推不回權杖）。
    """
    return {
        "sealed_at": info.get("sealed_at"),
        "token_sha256": info.get("token_sha256"),
        "file_count": len(info.get("files") or []),
        "test_command": info.get("test_command"),
        "baseline": info.get("baseline"),
        "locked_tests_sha256": info.get("locked_tests_sha256"),
    }


def retire_ciphertext(info: dict, vault_dir: Path, task_id: str):
    """把密文搬到 `<vault>/_discarded/<task_id>-<時間>/`，並清掉殘留的明文。

    第三輪 P0-9 之前這裡是直接 `rmtree`，於是作廢＋刪墓碑之後，這個 task
    曾經存在過的證據就一點都不剩了。改成搬家的理由：

      - 密文沒有權杖本來就解不開，留著**不增加任何洩題風險**
      - 但它讓「這個 task 存在過」多一份要另外動手才抹得掉的證據
      - 真的要刪是另一個動作，而那個動作是吵的（目錄名字寫著 _discarded）

    殘留的**明文**仍然必須刪掉，不能跟著搬：權杖遺失前的最後一次執行，
    正是最可能被中斷、最可能把明文留在封存庫裡的那一次。
    """
    raw = info.get("task_dir")
    if not raw:
        return None, []
    task_dir = Path(raw)
    if not task_dir.is_dir():
        return None, []

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = vault_dir / vault.DISCARDED_DIR_NAME / f"{task_id}-{stamp}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(task_dir), str(target))
    except OSError:
        # 搬不動（權限、跨檔案系統）時退回原本的行為：刪掉。
        # 留著一份可能含明文的目錄，比失去證據更危險。
        shutil.rmtree(task_dir, ignore_errors=True)
        return None, [task_dir] if not task_dir.exists() else []

    purged = []
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.suffix != ".enc":
            try:
                path.unlink()
                purged.append(path)
            except OSError:
                pass
    return target, purged


def main() -> int:
    parser = argparse.ArgumentParser(
        description="權杖遺失後作廢一個 task 的封存（沒有救援路徑，只能重寫）"
    )
    parser.add_argument("--task-id", required=True, help="要作廢的 task_id")
    parser.add_argument(
        "--token",
        help="若你手上還有權杖，帶進來先確認它到底對不對——對的話這支腳本會拒絕作廢",
    )
    parser.add_argument(
        "--token-lost",
        action="store_true",
        help="明確聲明權杖已經遺失。這是破壞性操作，不提供預設值",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="確認你知道作廢之後這個 task 必須重寫一份隱藏測試",
    )
    args = parser.parse_args()

    root = vault.repo_root()
    manifest = vault.load_manifest(root)
    info = manifest.get("tasks", {}).get(args.task_id)

    print("===== 作廢已封存的隱藏測試 =====")

    if not info:
        print(f"manifest 裡沒有 task「{args.task_id}」，沒有東西可以作廢。", file=sys.stderr)
        print("可用 `python3 scripts/run-hidden-tests.py --list` 查看有哪些 task。", file=sys.stderr)
        return 1

    if vault.is_discarded(info):
        print(f"task「{args.task_id}」已經是作廢狀態"
              f"（作廢於 {info.get('discarded_at', '未知時間')}），不需要再作廢一次。")
        print("要恢復驗收能力，請重寫一份隱藏測試再執行 seal-hidden-tests.py。")
        return 1

    # 先救「其實只是打錯字」這種情況：權杖是對的就沒有理由摧毀封存。
    if args.token:
        if vault.token_fingerprint(args.token) == info.get("token_sha256"):
            print("拒絕作廢：你給的權杖是**對的**，這個 task 不需要作廢。")
            print(f"  python3 scripts/run-hidden-tests.py --task-id {args.task_id} --token <權杖>")
            return 1
        print("（你給的權杖指紋不符，確實不是這個 task 的權杖）")

    if not args.token_lost or not args.confirm:
        print("這是破壞性操作：密文會被移進作廢區、再也解不開，之後**只能重寫一份隱藏測試**。",
              file=sys.stderr)
        print("確定權杖已經遺失的話，兩個旗標都要給：", file=sys.stderr)
        print(f"  python3 scripts/discard-sealed-task.py --task-id {args.task_id} "
              "--token-lost --confirm", file=sys.stderr)
        return 2

    previous = summarise_previous(info)
    vault_dir = Path(info.get("vault_dir") or vault.resolve_vault_dir(root))
    retired, purged = retire_ciphertext(info, vault_dir, args.task_id)

    tombstone = {
        "status": vault.DISCARDED_STATUS,
        "discarded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "token-lost",
        # 跨封存累積的兩個計數，都要原封不動帶過去：
        # attempts_recorded 少了會被 show-attempts 判成「有紀錄被刪」，
        # discard_count 是「這個 task 被作廢過幾次」的唯一紀錄。
        "attempts_recorded": int(info.get("attempts_recorded") or 0),
        "discard_count": int(info.get("discard_count") or 0) + 1,
        "previous": previous,
    }
    manifest["tasks"][args.task_id] = tombstone
    vault.save_manifest(manifest, root)
    vault.append_sealed_log(vault_dir, args.task_id, "discarded")

    print(f"task_id：{args.task_id}")
    if retired:
        print(f"密文已移到作廢區：{retired}")
        print("（密文沒有權杖本來就解不開，留著不增加洩題風險；"
              "但它讓「這個 task 存在過」多一份要另外動手才抹得掉的證據）")
        if purged:
            print(f"順帶清掉 {len(purged)} 個殘留的明文檔案（上一次執行若被中斷會留下）。")
    else:
        print("（封存目錄本來就不在了，只寫入墓碑）")
    print(f"manifest 已記下墓碑（累計作廢 {tombstone['discard_count']} 次，"
          f"沿用嘗試次數 {tombstone['attempts_recorded']} 筆）。")
    print()
    print("接下來——恢復的方式只有重寫，沒有第二條：")
    print("  1. Orchestrator 帶著**同一份 task-spec** 重新派工 verifier-test-writer")
    print("     （task-spec 不用改；要改的話那是另一回事，不是這次作廢造成的）")
    print("  2. 寫完公開測試與隱藏測試後：")
    print(f"     python3 scripts/seal-hidden-tests.py --task-id {args.task_id}")
    print("  3. 新權杖立刻做一次基線執行，確認新的隱藏測試在實作前是紅的：")
    print(f"     python3 scripts/run-hidden-tests.py --task-id {args.task_id} --token <新權杖> --baseline")
    print("  4. 把新權杖交回 Orchestrator。")
    print()
    print("⚠️ implementer 這段期間**不需要重做**：作廢的是測試，不是實作。")
    print("   重新封存後的驗收要照常跑，而且驗收報告要記載這個 task 曾經作廢過。")
    print("===== 結束 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
