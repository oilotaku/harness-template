#!/usr/bin/env python3
"""test-env-guard.py — env-guard.py 與 env_fingerprint.py 的回歸測試

對應 docs/improvement-suggestions.md 的 P3-1。

這批測試守的是一件容易被忽略的事：**守門機制不能一直誤報**。
一個每次都喊「環境不符」的 env-guard，會訓練使用者忽略警告，
結果是真的換了機器的那一次也被跳過——比沒有守門機制更糟。
所以這裡兩個方向都要測：該擋的要擋（真的換機器），不該吵的不能吵（容器重建）。

比對規則是純函式（`env_fingerprint.compare`），可以直接 import 進來用合成資料
測邊界；只有「檔案怎麼讀寫、exit code 對不對」才需要真的跑子行程。

用法：python3 scripts/test-env-guard.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_fingerprint  # noqa: E402
import machine_facts  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent
ENV_GUARD = SCRIPTS_DIR / "env-guard.py"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def run_guard(cwd: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENV_GUARD), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(cwd),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)},
    )


def fingerprint_file(cwd: Path) -> Path:
    return cwd / ".harness" / "env-fingerprint.json"


def write_baseline(cwd: Path, **overrides) -> dict:
    """寫一份以「目前這台機器」為基礎、再套用 overrides 的基準指紋。"""
    baseline = current()
    baseline.update(overrides)
    path = fingerprint_file(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    return baseline


def current() -> dict:
    import platform
    import socket

    return env_fingerprint.build(
        hostname=socket.gethostname(),
        os_name=platform.system(),
        arch=platform.machine(),
        container=machine_facts.in_container(),
        cpu_count=machine_facts.cpu_count(),
        memory_mb=machine_facts.memory_mb(),
        hostname_stable=machine_facts.hostname_is_stable(),
    )


# --------------------------------------------------- 級距（避免精確值誤報）


@case("CPU 級距：邊界值落在正確的級距")
def _(tmp: Path):
    expected = {1: "1", 2: "2", 3: "3-4", 4: "3-4", 5: "5-8", 8: "5-8", 16: "9-16", 64: "33+"}
    for count, label in expected.items():
        assert env_fingerprint.cpu_bucket(count) == label, f"{count} → {env_fingerprint.cpu_bucket(count)}，預期 {label}"
    assert env_fingerprint.cpu_bucket(None) == "未知"


@case("記憶體級距：同一台機器回報 7.8GB 或 8.0GB 都落在同一級距")
def _(tmp: Path):
    # 這正是舊版誤報的來源之一：雲端環境同規格機器的記憶體回報值會浮動，
    # 精確比對會把它當成「換了機器」。
    assert env_fingerprint.memory_bucket(7987) == env_fingerprint.memory_bucket(8192)
    assert env_fingerprint.memory_bucket(None) == "未知"
    assert env_fingerprint.memory_bucket(1024) == "<2GB"
    assert env_fingerprint.memory_bucket(131072) == "64GB+"


# ------------------------------------------------- 比對規則（不該吵的不能吵）


@case("容器環境：hostname 每次不同不算環境變更")
def _(tmp: Path):
    recorded = env_fingerprint.build("abc123", "Linux", "x86_64", True, 8, 16384)
    now = env_fingerprint.build("def456", "Linux", "x86_64", True, 8, 16384)
    mismatches, missing, skipped = env_fingerprint.compare(recorded, now)
    assert not mismatches, f"容器換 hostname 不該算不符：{mismatches}"
    assert not missing, missing
    assert any(key == "hostname" for key, _ in skipped), "應該明確說明 hostname 被略過"


@case("CI／Codespaces 這類環境：hostname_stable=False 也不比對 hostname")
def _(tmp: Path):
    recorded = env_fingerprint.build(
        "runner-1", "Linux", "x86_64", False, 4, 16384, hostname_stable=False
    )
    now = env_fingerprint.build(
        "runner-2", "Linux", "x86_64", False, 4, 16384, hostname_stable=False
    )
    mismatches, _missing, skipped = env_fingerprint.compare(recorded, now)
    assert not mismatches, f"非容器但 hostname 不穩定時也不該報不符：{mismatches}"
    assert skipped


@case("同一台機器記憶體回報浮動（同級距）不算環境變更")
def _(tmp: Path):
    recorded = env_fingerprint.build("box", "Linux", "x86_64", False, 8, 16000)
    now = env_fingerprint.build("box", "Linux", "x86_64", False, 8, 16300)
    mismatches, _missing, _skipped = env_fingerprint.compare(recorded, now)
    assert not mismatches, f"同級距內的浮動不該算不符：{mismatches}"


# --------------------------------------------------- 比對規則（該擋的要擋）


@case("實體機換 hostname：算環境變更")
def _(tmp: Path):
    recorded = env_fingerprint.build("laptop", "Linux", "x86_64", False, 8, 16384)
    now = env_fingerprint.build("server", "Linux", "x86_64", False, 8, 16384)
    mismatches, _missing, _skipped = env_fingerprint.compare(recorded, now)
    assert [m[0] for m in mismatches] == ["hostname"], mismatches


@case("作業系統或架構改變：算環境變更")
def _(tmp: Path):
    recorded = env_fingerprint.build("box", "Linux", "x86_64", False, 8, 16384)
    now = env_fingerprint.build("box", "Darwin", "arm64", False, 8, 16384)
    mismatches, _missing, _skipped = env_fingerprint.compare(recorded, now)
    keys = {m[0] for m in mismatches}
    assert keys == {"os", "arch"}, mismatches


@case("CPU 或記憶體跨級距：算環境變更（Orchestrator 的平行度依據變了）")
def _(tmp: Path):
    recorded = env_fingerprint.build("box", "Linux", "x86_64", False, 16, 32768)
    now = env_fingerprint.build("box", "Linux", "x86_64", False, 2, 4096)
    mismatches, _missing, _skipped = env_fingerprint.compare(recorded, now)
    keys = {m[0] for m in mismatches}
    assert keys == {"cpu_bucket", "memory_bucket"}, mismatches


@case("實體機搬進容器：算環境變更（container 欄位不符），但不重複用 hostname 再報一次")
def _(tmp: Path):
    recorded = env_fingerprint.build("laptop", "Linux", "x86_64", False, 8, 16384)
    now = env_fingerprint.build("abc123", "Linux", "x86_64", True, 8, 16384)
    mismatches, _missing, _skipped = env_fingerprint.compare(recorded, now)
    assert [m[0] for m in mismatches] == ["container"], mismatches


# --------------------------------------------------------------- 向後相容


@case("舊版指紋檔（只有 hostname/os/arch）：缺欄位視為需要補齊，不是不符")
def _(tmp: Path):
    recorded = {"hostname": "box", "os": "Linux", "arch": "x86_64"}
    now = env_fingerprint.build("box", "Linux", "x86_64", False, 8, 16384)
    mismatches, missing, _skipped = env_fingerprint.compare(recorded, now)
    assert not mismatches, f"舊格式不該被判成不符：{mismatches}"
    assert set(missing) == {"container", "cpu_bucket", "memory_bucket", "hostname_stable"}, missing


# ------------------------------------------------------------- 端對端行為


@case("首次執行：建立基準並回傳 0")
def _(tmp: Path):
    result = run_guard(tmp)
    assert result.returncode == 0, f"exit={result.returncode} {result.stdout}{result.stderr}"
    assert fingerprint_file(tmp).exists(), "沒有寫出指紋檔"
    assert "首次執行" in result.stdout, result.stdout


@case("同一台機器連續執行兩次：第二次仍然回傳 0（不會自己誤報）")
def _(tmp: Path):
    run_guard(tmp)
    result = run_guard(tmp)
    assert result.returncode == 0, f"exit={result.returncode} {result.stdout}"
    assert "正常" in result.stdout, result.stdout


@case("真的換了機器：回傳 1 並告知用 --update 更新基準")
def _(tmp: Path):
    write_baseline(tmp, os="SomeOtherOS", arch="alpha-64")
    result = run_guard(tmp)
    assert result.returncode == 1, f"exit={result.returncode} {result.stdout}"
    assert "不符" in result.stdout, result.stdout
    assert "--update" in result.stdout, "應該告訴使用者怎麼更新基準"


@case("--update 後再比對：回傳 0，且保留原本記錄供對照")
def _(tmp: Path):
    write_baseline(tmp, os="SomeOtherOS")
    updated = run_guard(tmp, "--update")
    assert updated.returncode == 0, f"exit={updated.returncode} {updated.stdout}"
    assert "SomeOtherOS" in updated.stdout, "應該印出原本記錄的環境供對照"

    after = run_guard(tmp)
    assert after.returncode == 0, f"更新後仍不符：{after.stdout}"


@case("hostname 不穩定的環境端對端：基準是別的 hostname，不該回報不符")
def _(tmp: Path):
    # 只改 hostname 與 hostname_stable，其餘沿用目前這台機器的值。
    # 不能連 container 一起改——那會產生一個「主機↔容器」的不符，
    # 而那個不符是正確行為，會蓋掉這個案例真正想驗的東西。
    write_baseline(tmp, hostname="old-random-id", hostname_stable=False)
    result = run_guard(tmp)
    assert result.returncode == 0, f"hostname 不穩定的環境換 hostname 竟被判成不符：{result.stdout}"
    assert "不列入比對" in result.stdout, result.stdout


@case("舊版指紋檔端對端：自動補欄位、回傳 0，且補完之後檔案是新格式")
def _(tmp: Path):
    import platform
    import socket

    path = fingerprint_file(tmp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "hostname": socket.gethostname(),
                "os": platform.system(),
                "arch": platform.machine(),
            }
        ),
        encoding="utf-8",
    )

    result = run_guard(tmp)
    assert result.returncode == 0, f"exit={result.returncode} {result.stdout}"
    assert "自動補上" in result.stdout, result.stdout

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "cpu_bucket" in saved and "hostname_stable" in saved, saved

    again = run_guard(tmp)
    assert again.returncode == 0, f"補完欄位後又報不符：{again.stdout}"
    assert "自動補上" not in again.stdout, "補齊應該只發生一次"


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
