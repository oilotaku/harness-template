#!/usr/bin/env python3
"""skill_policy.py — 依專案目標決定要安裝哪些 skill

## 問題

Claude Code 的 skill 不是免費的：**每個已啟用 skill 的名稱與描述都會進入
每一個 session 的上下文**。以本模板的結構（一個 task 至少 3 個不共用上下文的
session）來說，它的乘數跟 `CLAUDE.md` 同一級——裝十個「看起來可能有用」的 skill，
等於每個 task 都替它們付十次錢（見 docs/token-strategy.md §1）。

第二個問題更隱蔽：**skill 是指令，會誘導 agent 的行為**。給 verifier 裝一個
會產生程式碼的 skill，它就有理由「順手把問題修好」——而檢驗者一旦動手改實作，
整條獨立驗證鏈就斷了（黃金法則第 3 條）。這不能靠自律，要靠機制。

## 解法

一份專案自己的 `skills.catalog.json`（會進版控，它描述的是這個專案的判斷），
記錄「哪些 skill、什麼情況下該裝、給誰」。本模組負責讀它、比對專案目標、
輸出建議清單，並強制一條寫死的規則：

    **檢驗者角色永遠拿不到 `produces_code: true` 的 skill。**

即使 catalog 明確把它列給 verifier 也一樣——那是設定寫錯，不是需求。

## 邊界（很重要，不要誤解這支腳本能做什麼）

它**不會**幫你安裝任何東西。安裝 skill 是把檔案放進 `.claude/skills/<name>/SKILL.md`
或安裝對應的 plugin，那是人（或 Orchestrator 明確執行）的動作。
這支腳本只做「決定」與「留下決定的理由」。

## 為什麼設定錯誤要丟例外

跟 `harness_config.py` 同一個道理：catalog 打錯字如果只是靜靜略過那一筆，
使用者會以為某個 skill 已經被納入考慮，實際上從來沒有。沉默的失效比報錯糟。
"""
import json
import os
from pathlib import Path

CATALOG_FILENAME = "skills.catalog.json"

# 允許出現在 catalog 裡的角色名稱。用固定清單而不是自由字串，
# 是為了讓「打錯角色名 → 這個 skill 從此不會被任何人拿到」變成錯誤而不是靜默。
ROLES = ("orchestrator", "implementer", "verifier")

# 比對專案目標時看的欄位。每一項都是「清單對清單，有交集就算命中」。
MATCH_FIELDS = ("deliverables", "languages", "frameworks", "keywords")

ENTRY_FIELDS = {"name", "why", "when", "roles", "produces_code"}


class CatalogError(Exception):
    """catalog 有問題。一律往上丟，不要退回「空清單」——那會看起來像「沒有適用的 skill」。"""


def catalog_path(root=None) -> Path:
    if root is None:
        raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
        try:
            root = Path(raw).resolve()
        except OSError:
            root = Path.cwd()
    return Path(root) / CATALOG_FILENAME


def load(root=None) -> dict:
    """讀出 catalog。檔案不存在時回 `{"skills": []}` 並附一句警告。

    「沒有 catalog」跟「catalog 壞掉」是兩件事：前者是還沒設定（正常起始狀態），
    後者是設定錯了（必須報錯）。
    """
    path = catalog_path(root)
    if not path.exists():
        return {"skills": [], "warnings": [f"找不到 {CATALOG_FILENAME}，沒有任何 skill 會被建議。"]}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CatalogError(f"{CATALOG_FILENAME} 讀不到或不是合法 JSON：{exc}")

    if not isinstance(raw, dict):
        raise CatalogError(f"{CATALOG_FILENAME} 的最外層必須是物件")
    unknown_top = set(raw) - {"skills"}
    if unknown_top:
        raise CatalogError(f"{CATALOG_FILENAME} 有未知的頂層欄位：{sorted(unknown_top)}")
    entries = raw.get("skills")
    if not isinstance(entries, list):
        raise CatalogError(f"{CATALOG_FILENAME} 的 skills 必須是陣列")

    seen = set()
    for index, entry in enumerate(entries):
        _validate_entry(entry, index, seen)

    return {"skills": entries, "warnings": []}


def _validate_entry(entry, index, seen) -> None:
    where = f"skills[{index}]"
    if not isinstance(entry, dict):
        raise CatalogError(f"{where} 必須是物件")

    unknown = set(entry) - ENTRY_FIELDS
    if unknown:
        raise CatalogError(f"{where} 有未知欄位：{sorted(unknown)}（可用：{sorted(ENTRY_FIELDS)}）")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CatalogError(f"{where} 缺少 name")
    if name in seen:
        raise CatalogError(f"{where} 的 name「{name}」重複了")
    seen.add(name)

    if not isinstance(entry.get("why"), str) or not entry["why"].strip():
        # 沒寫理由的 skill 之後沒有人敢移除——因為沒人知道當初為什麼裝。
        raise CatalogError(f"{where}（{name}）缺少 why：要寫清楚為什麼這個專案需要它")

    roles = entry.get("roles")
    if not isinstance(roles, list) or not roles:
        raise CatalogError(f"{where}（{name}）缺少 roles")
    bad_roles = [role for role in roles if role not in ROLES]
    if bad_roles:
        raise CatalogError(f"{where}（{name}）有未知角色：{bad_roles}（可用：{list(ROLES)}）")

    if not isinstance(entry.get("produces_code"), bool):
        raise CatalogError(
            f"{where}（{name}）缺少 produces_code（true/false）："
            "這個欄位決定它會不會被擋在檢驗者之外，不可以省略"
        )

    when = entry.get("when")
    if not isinstance(when, dict):
        raise CatalogError(f"{where}（{name}）缺少 when")
    unknown_when = set(when) - set(MATCH_FIELDS)
    if unknown_when:
        raise CatalogError(f"{where}（{name}）的 when 有未知欄位：{sorted(unknown_when)}")
    for field in MATCH_FIELDS:
        values = when.get(field, [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise CatalogError(f"{where}（{name}）的 when.{field} 必須是字串陣列")


def _normalize(values) -> set:
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


def recommend(goal: dict, role: str, root=None, catalog=None) -> dict:
    """依專案目標與角色，回傳該裝哪些 skill。

    goal 的欄位就是 MATCH_FIELDS：deliverables / languages / frameworks / keywords。
    任何一欄有交集就算命中；`when` 四欄全空的 skill 永遠不會被自動建議
    （避免「反正裝著也不會怎樣」的東西悄悄變成常駐成本）。
    """
    if role not in ROLES:
        raise ValueError(f"未知角色：{role}（可用：{list(ROLES)}）")

    document = catalog if catalog is not None else load(root)
    goal_sets = {field: _normalize(goal.get(field)) for field in MATCH_FIELDS}

    recommended, excluded = [], []
    for entry in document["skills"]:
        matched = [
            field
            for field in MATCH_FIELDS
            if goal_sets[field] & _normalize(entry["when"].get(field))
        ]
        if not matched:
            continue

        if role not in entry["roles"]:
            excluded.append({
                "name": entry["name"],
                "reason": f"catalog 沒有把這個 skill 分給 {role}（分給了 {entry['roles']}）",
            })
            continue

        # 寫死的規則：檢驗者不能拿到會產生程式碼的 skill。
        # catalog 就算寫了也不算數——那是設定寫錯，不是需求。
        if role == "verifier" and entry["produces_code"]:
            excluded.append({
                "name": entry["name"],
                "reason": "檢驗者不可取得會產生程式碼的 skill：一旦它動手改實作，"
                          "獨立驗證鏈就斷了（CLAUDE.md 黃金法則第 3 條）",
            })
            continue

        recommended.append({
            "name": entry["name"],
            "why": entry["why"],
            "matched_on": matched,
        })

    return {
        "role": role,
        "goal": {field: sorted(goal_sets[field]) for field in MATCH_FIELDS},
        "recommend": sorted(recommended, key=lambda item: item["name"]),
        "excluded": sorted(excluded, key=lambda item: item["name"]),
        "warnings": list(document.get("warnings") or []),
        # 這支腳本只做決定，不做安裝——不要讓呼叫方誤會。
        "install_hint": "安裝方式：把 skill 放進 .claude/skills/<name>/SKILL.md 或安裝對應 plugin。"
                        "本腳本不會替你安裝任何東西。",
    }
