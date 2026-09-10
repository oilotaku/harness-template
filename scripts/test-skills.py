#!/usr/bin/env python3
"""test-skills.py — skill 安裝決策的回歸測試

這批測試守三件事：

1. **檢驗者永遠拿不到會產生程式碼的 skill。** 即使 catalog 明確把它列給
   verifier 也一樣——檢驗者一旦動手改實作，獨立驗證鏈就斷了。
   這是寫死在程式碼裡的規則，不是設定，所以要有測試釘住它。
2. **catalog 壞掉一律報錯，不退回空清單。** 空清單看起來像「這個目標不需要
   skill」，那是最糟的失效方式。
3. **`when` 全空的 skill 永遠不會被自動建議。** 避免「反正裝著也不會怎樣」
   悄悄變成每個 session 都要付的常駐成本。

用法：python3 scripts/test-skills.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import skill_policy  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def write_catalog(tmp: Path, document) -> Path:
    path = tmp / skill_policy.CATALOG_FILENAME
    if isinstance(document, str):
        path.write_text(document, encoding="utf-8")
    else:
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def entry(name="pdf", why="測試用", when=None, roles=None, produces_code=False) -> dict:
    return {
        "name": name,
        "why": why,
        "when": {"deliverables": ["pdf"]} if when is None else when,
        "roles": ["implementer"] if roles is None else roles,
        "produces_code": produces_code,
    }


def run_cli(tmp: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "suggest-skills.py"), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(tmp),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp)},
    )


def expect_error(tmp: Path, document, hint: str) -> None:
    write_catalog(tmp, document)
    try:
        skill_policy.load(root=tmp)
    except skill_policy.CatalogError:
        return
    raise AssertionError(f"{hint}：應該報錯卻通過了")


# --------------------------------------------------------------------------
# 寫死的安全規則
# --------------------------------------------------------------------------

@case("檢驗者拿不到會產生程式碼的 skill，即使 catalog 明確列給它")
def _(tmp: Path):
    catalog = {"skills": [entry(name="codegen", roles=["verifier"], produces_code=True)]}
    result = skill_policy.recommend({"deliverables": ["pdf"]}, "verifier", catalog=catalog)
    assert result["recommend"] == [], result["recommend"]
    assert result["excluded"][0]["name"] == "codegen", result["excluded"]
    assert "獨立驗證鏈" in result["excluded"][0]["reason"], result["excluded"]


@case("檢驗者可以拿到不產生程式碼的 skill")
def _(tmp: Path):
    catalog = {"skills": [entry(name="checklist", roles=["verifier"], produces_code=False)]}
    result = skill_policy.recommend({"deliverables": ["pdf"]}, "verifier", catalog=catalog)
    assert [item["name"] for item in result["recommend"]] == ["checklist"], result


@case("同一個會產生程式碼的 skill，implementer 拿得到、verifier 拿不到")
def _(tmp: Path):
    catalog = {"skills": [entry(name="pdf", roles=["implementer", "verifier"], produces_code=True)]}
    impl = skill_policy.recommend({"deliverables": ["pdf"]}, "implementer", catalog=catalog)
    veri = skill_policy.recommend({"deliverables": ["pdf"]}, "verifier", catalog=catalog)
    assert [i["name"] for i in impl["recommend"]] == ["pdf"], impl
    assert veri["recommend"] == [], veri


# --------------------------------------------------------------------------
# 比對規則
# --------------------------------------------------------------------------

@case("四個比對欄位任一有交集就算命中")
def _(tmp: Path):
    catalog = {"skills": [entry(when={"keywords": ["mcp"]})]}
    result = skill_policy.recommend({"keywords": ["MCP"]}, "implementer", catalog=catalog)
    assert result["recommend"], result
    assert result["recommend"][0]["matched_on"] == ["keywords"], result


@case("比對不分大小寫，也忽略前後空白")
def _(tmp: Path):
    catalog = {"skills": [entry(when={"languages": ["Python"]})]}
    result = skill_policy.recommend({"languages": ["  python "]}, "implementer", catalog=catalog)
    assert result["recommend"], result


@case("when 四欄全空的 skill 永遠不會被自動建議")
def _(tmp: Path):
    catalog = {"skills": [entry(name="always-on", when={})]}
    for goal in ({}, {"deliverables": ["pdf"]}, {"keywords": ["anything"]}):
        result = skill_policy.recommend(goal, "implementer", catalog=catalog)
        assert result["recommend"] == [], (goal, result["recommend"])


@case("沒命中的 skill 不會出現在排除清單裡（排除清單只放「命中但不給」）")
def _(tmp: Path):
    catalog = {"skills": [entry(name="pdf", when={"deliverables": ["pdf"]})]}
    result = skill_policy.recommend({"deliverables": ["pptx"]}, "implementer", catalog=catalog)
    assert result["recommend"] == [] and result["excluded"] == [], result


@case("catalog 沒把 skill 分給這個角色時，會說明是分給了誰")
def _(tmp: Path):
    catalog = {"skills": [entry(name="pdf", roles=["orchestrator"])]}
    result = skill_policy.recommend({"deliverables": ["pdf"]}, "implementer", catalog=catalog)
    assert result["excluded"][0]["name"] == "pdf", result
    assert "orchestrator" in result["excluded"][0]["reason"], result


@case("未知角色直接報錯")
def _(tmp: Path):
    try:
        skill_policy.recommend({}, "reviewer-ish", catalog={"skills": []})
    except ValueError:
        return
    raise AssertionError("未知角色應該報錯")


# --------------------------------------------------------------------------
# catalog 驗證（一律 fail-closed）
# --------------------------------------------------------------------------

@case("沒有 catalog 時回空清單並附警告，不是報錯（那是正常起始狀態）")
def _(tmp: Path):
    document = skill_policy.load(root=tmp)
    assert document["skills"] == [], document
    assert document["warnings"], "找不到 catalog 應該要有警告，不能靜靜當成沒事"


@case("catalog 不是合法 JSON 時報錯，不退回空清單")
def _(tmp: Path):
    expect_error(tmp, "{ 這不是 JSON", "壞掉的 JSON")


@case("最外層不是物件時報錯")
def _(tmp: Path):
    expect_error(tmp, [], "最外層是陣列")


@case("頂層有未知欄位時報錯（打錯字不該被靜靜忽略）")
def _(tmp: Path):
    expect_error(tmp, {"skills": [], "skils": []}, "頂層打錯字")


@case("skill 條目有未知欄位時報錯")
def _(tmp: Path):
    bad = entry()
    bad["role"] = ["implementer"]  # 正確欄位是 roles
    expect_error(tmp, {"skills": [bad]}, "條目打錯字")


@case("缺 why 時報錯（沒有理由的 skill 之後沒人敢移除）")
def _(tmp: Path):
    bad = entry()
    del bad["why"]
    expect_error(tmp, {"skills": [bad]}, "缺 why")


@case("缺 produces_code 時報錯（它決定會不會被擋在檢驗者之外）")
def _(tmp: Path):
    bad = entry()
    del bad["produces_code"]
    expect_error(tmp, {"skills": [bad]}, "缺 produces_code")


@case("produces_code 不是布林時報錯")
def _(tmp: Path):
    expect_error(tmp, {"skills": [dict(entry(), produces_code="true")]}, "produces_code 是字串")


@case("角色名稱打錯時報錯（不然這個 skill 從此不會被任何人拿到）")
def _(tmp: Path):
    expect_error(tmp, {"skills": [entry(roles=["implementor"])]}, "角色打錯字")


@case("name 重複時報錯")
def _(tmp: Path):
    expect_error(tmp, {"skills": [entry(), entry()]}, "name 重複")


@case("when 有未知欄位或非字串陣列時報錯")
def _(tmp: Path):
    expect_error(tmp, {"skills": [entry(when={"deliverable": ["pdf"]})]}, "when 打錯字")
    expect_error(tmp, {"skills": [entry(when={"deliverables": "pdf"})]}, "when 不是陣列")


@case("附的範例 catalog 本身是合法的")
def _(tmp: Path):
    example = SCRIPTS_DIR.parent / "templates" / "skills.catalog.example.json"
    (tmp / skill_policy.CATALOG_FILENAME).write_text(
        example.read_text(encoding="utf-8"), encoding="utf-8"
    )
    document = skill_policy.load(root=tmp)
    assert document["skills"], "範例 catalog 是空的"


@case("範例 catalog 裡沒有把會產生程式碼的 skill 分給檢驗者")
def _(tmp: Path):
    example = json.loads(
        (SCRIPTS_DIR.parent / "templates" / "skills.catalog.example.json").read_text(encoding="utf-8")
    )
    offenders = [
        item["name"]
        for item in example["skills"]
        if item["produces_code"] and "verifier" in item["roles"]
    ]
    assert not offenders, f"範例本身示範了錯誤用法：{offenders}"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

@case("CLI --json 的 stdout 只有 JSON")
def _(tmp: Path):
    write_catalog(tmp, {"skills": [entry()]})
    result = run_cli(tmp, "--role", "implementer", "--deliverable", "pdf", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["results"][0]["recommend"][0]["name"] == "pdf", payload


@case("CLI --role all 一次回三個角色")
def _(tmp: Path):
    write_catalog(tmp, {"skills": [entry()]})
    payload = json.loads(run_cli(tmp, "--role", "all", "--deliverable", "pdf", "--json").stdout)
    assert [r["role"] for r in payload["results"]] == list(skill_policy.ROLES), payload


@case("CLI 在 catalog 壞掉時回 exit 2，不是回「沒有建議」")
def _(tmp: Path):
    write_catalog(tmp, "{ 壞掉")
    result = run_cli(tmp, "--role", "implementer", "--deliverable", "pdf", "--json")
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert result.stdout.strip() == "", "壞掉時不該印出任何看起來像結果的東西"


@case("CLI 沒有 --json 時印中文報表，並說明自己不會安裝任何東西")
def _(tmp: Path):
    write_catalog(tmp, {"skills": [entry()]})
    result = run_cli(tmp, "--role", "implementer", "--deliverable", "pdf")
    assert result.returncode == 0, result.stderr
    assert "===== skill 安裝建議 =====" in result.stdout, result.stdout[:200]
    assert "不會替你安裝" in result.stdout, "必須寫明它只做決定，不做安裝"


@case("CLI 找不到 catalog 時仍正常結束，並印出警告")
def _(tmp: Path):
    result = run_cli(tmp, "--role", "implementer", "--deliverable", "pdf")
    assert result.returncode == 0, result.stderr
    assert "找不到" in result.stdout, result.stdout[:200]


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as base:
        for index, (name, fn) in enumerate(CASES):
            case_dir = Path(base) / f"case-{index}"
            case_dir.mkdir()
            try:
                fn(case_dir)
                print(f"PASS  {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"FAIL  {name}\n      {e}")

    print()
    if failures:
        print(f"{len(failures)}/{len(CASES)} 案例失敗：")
        for name in failures:
            print(f"  - {name}")
        return 1

    print(f"全部 {len(CASES)} 案例通過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
