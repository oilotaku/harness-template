#!/usr/bin/env python3
"""test-design.py — 設計 token 與對比度檢查的離線回歸測試

這批測試守的是四件事：
  1. 對比度算得對（拿 WCAG 有明確答案的組合驗）
  2. **模板附的預設色票本身通過 AA**——這條最重要：色票會被人調，
     調到跌破門檻時要有東西會紅
  3. 結構退化抓得到（少角色、明暗不一致、偷偷加色階、值不合法）
  4. 沒有圖形介面的專案關得掉，而且不是靠「檔案剛好不存在」關掉

用法：python3 scripts/test-design.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import design_tokens as tokens  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
CHECKER = SCRIPTS_DIR / "check-design-tokens.py"
DEFAULT_TOKENS = REPO_ROOT / "design.tokens.json"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def base_tokens() -> dict:
    """以模板附的預設 token 當起點，測試再各自改壞其中一處。"""
    return json.loads(DEFAULT_TOKENS.read_text(encoding="utf-8"))


def make_project(tmp: Path, data=None, config=None) -> Path:
    repo = tmp / "project"
    repo.mkdir(parents=True, exist_ok=True)
    if data is not None:
        (repo / "design.tokens.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    if config is not None:
        (repo / "harness.config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )
    return repo


def run(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=str(repo),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


# --------------------------------------------------------------- 對比度算得對


@case("對比度計算對得上 WCAG 的已知值（黑白 21、同色 1）")
def _(tmp: Path):
    assert round(tokens.contrast_ratio("#000000", "#FFFFFF"), 2) == 21.0
    assert round(tokens.contrast_ratio("#FFFFFF", "#000000"), 2) == 21.0, "前後景對調應該一樣"
    assert round(tokens.contrast_ratio("#777777", "#777777"), 2) == 1.0


@case("短格式 #rgb 等同 #rrggbb")
def _(tmp: Path):
    assert tokens.parse_hex("#fff") == tokens.parse_hex("#FFFFFF")
    assert tokens.parse_hex("#0a0") == (0, 170, 0)


@case("不合法的顏色字串會被擋下，不是默默算成黑色")
def _(tmp: Path):
    for bad in ("fff", "#ggg", "#12345", "rgb(0,0,0)", "", None):
        try:
            tokens.parse_hex(bad)
        except tokens.TokenError:
            continue
        raise AssertionError(f"「{bad}」竟然被當成合法顏色")


@case("三種層級的門檻就是 WCAG AA 的規定（4.5 / 3.0 / 3.0）")
def _(tmp: Path):
    assert tokens.threshold_for("text") == 4.5
    assert tokens.threshold_for("large-text") == 3.0
    assert tokens.threshold_for("non-text") == 3.0


# ------------------------------------------------- 模板附的預設色票不能退化


@case("模板附的預設 token 本身結構完整、而且每一組配對都達 AA")
def _(tmp: Path):
    data = base_tokens()
    assert tokens.check_structure(data) == [], tokens.check_structure(data)
    results = tokens.check_contrast(data)
    failures = [r for r in results if not r["passed"]]
    assert not failures, f"預設色票有 {len(failures)} 組不達標：{failures}"
    assert len(results) >= 20, f"配對數量太少（{len(results)}），涵蓋不了主要組合"


@case("預設 token 明暗兩套的角色完全一致（切主題換值不換名字）")
def _(tmp: Path):
    colors = base_tokens()["color"]
    light = {k for k in colors["light"] if not k.startswith("$")}
    dark = {k for k in colors["dark"] if not k.startswith("$")}
    assert light == dark, f"只在 light：{light - dark}；只在 dark：{dark - light}"


# ------------------------------------------------------------- 結構退化抓得到


@case("少一個角色會被抓到（少了就會逼實作者自己發明一個顏色）")
def _(tmp: Path):
    data = base_tokens()
    del data["color"]["light"]["border"]
    problems = tokens.check_structure(data)
    assert any("border" in p for p in problems), problems


@case("偷偷加一個未定義的角色會被抓到（角色一多就退化成色階）")
def _(tmp: Path):
    data = base_tokens()
    data["color"]["light"]["blue-500"] = "#1F4FD8"
    data["color"]["dark"]["blue-500"] = "#9CB6FF"
    problems = tokens.check_structure(data)
    assert any("blue-500" in p for p in problems), problems


@case("明暗兩套角色不一致會被抓到")
def _(tmp: Path):
    data = base_tokens()
    del data["color"]["dark"]["focus"]
    problems = tokens.check_structure(data)
    assert any("不一致" in p or "focus" in p for p in problems), problems


@case("間距/字級沒有由小到大排序、或有重複值，會被抓到")
def _(tmp: Path):
    data = base_tokens()
    data["spacing"]["scale"] = [0, 8, 4, 16]
    assert any("排序" in p for p in tokens.check_structure(data)), "亂序沒被抓到"

    data = base_tokens()
    data["typography"]["scale"] = [12, 14, 14, 16]
    assert any("重複" in p for p in tokens.check_structure(data)), "重複值沒被抓到"


@case("沒有 contrast_pairs 會被抓到（沒宣告就等於沒在檢查對比度）")
def _(tmp: Path):
    data = base_tokens()
    del data["contrast_pairs"]
    assert any("contrast_pairs" in p for p in tokens.check_structure(data))


# ----------------------------------------------------------- CLI 行為


@case("對比度不足時 exit 1，而且指名是哪個主題的哪一組")
def _(tmp: Path):
    data = base_tokens()
    data["color"]["light"]["on-surface-muted"] = "#C8CDD4"  # 太淺，畫在白底上讀不到
    repo = make_project(tmp, data=data)

    result = run(repo)
    assert result.returncode == 1, result.stdout
    combined = result.stdout + result.stderr
    assert "on-surface-muted" in combined and "light" in combined, combined
    assert "4.5" in combined, "沒有講出門檻是多少"


@case("結構有問題時不報對比度數字（算出來的數字沒有意義）")
def _(tmp: Path):
    data = base_tokens()
    del data["color"]["light"]["primary"]
    repo = make_project(tmp, data=data)

    result = run(repo)
    assert result.returncode == 1, result.stdout
    assert "結構" in (result.stdout + result.stderr), result.stderr


@case("全部通過時會講出「餘裕最小」的那一組（卡在邊緣的下次就會跌破）")
def _(tmp: Path):
    repo = make_project(tmp, data=base_tokens())
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "餘裕最小" in result.stdout, result.stdout


@case("--show 會列出每一組配對，不只不合格的")
def _(tmp: Path):
    repo = make_project(tmp, data=base_tokens())
    result = run(repo, "--show")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("on ") >= 20, result.stdout


@case("--json 只吐合法 JSON")
def _(tmp: Path):
    repo = make_project(tmp, data=base_tokens())
    payload = json.loads(run(repo, "--json").stdout)
    assert payload["ok"] is True and payload["pairs_checked"] >= 20, payload

    broken = base_tokens()
    broken["color"]["light"]["on-primary"] = "#8FA8F0"  # 對比不足
    repo2 = make_project(tmp / "broken", data=broken)
    result = run(repo2, "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False and payload["contrast_failures"], payload


# --------------------------------------------------------- 沒有 GUI 的專案


@case("design: false 的專案略過檢查並 exit 0（不逼 CLI 專案維護色票）")
def _(tmp: Path):
    repo = make_project(tmp, config={"design": False})  # 刻意不放 token 檔
    result = run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "略過" in result.stdout, result.stdout


@case("沒關掉卻找不到 token 檔時 exit 1，而且訊息要指出 design: false 這條路")
def _(tmp: Path):
    repo = make_project(tmp)
    result = run(repo)
    assert result.returncode == 1, result.stdout
    combined = result.stdout + result.stderr
    assert "design" in combined and "false" in combined, combined


@case("token 檔可以換位置")
def _(tmp: Path):
    repo = make_project(tmp, config={"design": {"tokens": "ui/tokens.json"}})
    target = repo / "ui" / "tokens.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(base_tokens(), ensure_ascii=False), encoding="utf-8")

    result = run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ui/tokens.json" in result.stdout, result.stdout


@case("design 區塊打錯欄位名時 fail-closed，不是靜靜用預設值")
def _(tmp: Path):
    repo = make_project(tmp, data=base_tokens(), config={"design": {"token": "x.json"}})
    result = run(repo)
    assert result.returncode == 1, result.stdout
    assert "無法辨識" in (result.stdout + result.stderr), result.stderr


@case("token 檔不是合法 JSON 時明確報錯")
def _(tmp: Path):
    repo = make_project(tmp)
    (repo / "design.tokens.json").write_text("{ 壞掉的 json", encoding="utf-8")
    result = run(repo)
    assert result.returncode == 1, result.stdout
    assert "JSON" in (result.stdout + result.stderr), result.stderr


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
