#!/usr/bin/env python3
"""test-scan-json.py — 掃描腳本機器可讀輸出的回歸測試（P3-2）

這批測試守三件事：

1. **`--json` 的 stdout 只能有 JSON。** 混進一行給人看的中文，
   Orchestrator 那邊就是解析失敗——而且是那種「昨天還好好的」的無聲退化。
2. **平行度換算是純函式且有邊界測試。** 之前它只是文件裡的一句經驗法則，
   同一台機器每次可能判出不同數字，而且沒有任何東西驗證得了。
3. **掃描不完整時不准給出建議連接埠區間。** 「沒看到」不等於「沒被佔用」，
   給一個看起來很有把握的區間比不給更危險。

用法：python3 scripts/test-scan-json.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capacity  # noqa: E402
import scan_cache  # noqa: E402
import utf8_output  # noqa: E402

utf8_output.enable()  # Windows 主控台預設用 ANSI 代碼頁，不先切 UTF-8 會印不出中文

SCRIPTS_DIR = Path(__file__).resolve().parent

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def run_script(cwd: Path, script_name: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script_name), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(cwd),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)},
    )


def load_module(script_name: str):
    """把帶連字號、不能直接 import 的腳本載進來，測它裡面的純函式。"""
    path = SCRIPTS_DIR / script_name
    spec = importlib.util.spec_from_file_location(script_name.replace("-", "_").replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_json_stdout(result: subprocess.CompletedProcess):
    assert result.returncode in (0, 1), f"非預期的 exit code {result.returncode}：{result.stderr}"
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AssertionError(
            f"--json 的 stdout 不是合法 JSON（{exc}）。實際輸出前 200 字：{result.stdout[:200]!r}"
        )


# --------------------------------------------------------------------------
# machine-profile.py
# --------------------------------------------------------------------------

@case("machine-profile --json 的 stdout 只有 JSON")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "machine-profile.py", "--json"))
    assert report["scan"] == "machine", report


@case("machine-profile --json 含 Orchestrator 需要的所有欄位")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "machine-profile.py", "--json"))
    for key in (
        "os", "arch", "cpu_count", "cpu_bucket", "memory_mb", "memory_bucket",
        "disk_free_gb", "container", "max_parallel_agents",
        "max_parallel_heavy_tasks", "default_parallel_agents", "limited_by",
    ):
        assert key in report, f"缺少欄位 {key}：{sorted(report)}"


@case("machine-profile 的預設平行度恆為 1（機器容量是上限，不是建議值）")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "machine-profile.py", "--json"))
    assert report["default_parallel_agents"] == 1, report


@case("machine-profile 沒有 --json 時仍印中文報表（人看的模式沒被改壞）")
def _(tmp: Path):
    result = run_script(tmp, "machine-profile.py")
    assert result.returncode == 0, result.stderr
    assert "===== 機器效能掃描 =====" in result.stdout, result.stdout[:200]
    assert "上限" in result.stdout, "人看的輸出應說明那是容量上限而非建議值"


# --------------------------------------------------------------------------
# capacity.py：平行度換算
# --------------------------------------------------------------------------

@case("平行度：CPU 充裕但記憶體不足時，由記憶體決定")
def _(tmp: Path):
    limits = capacity.parallel_limits(16, 4096)
    assert limits["limited_by"] == "memory", limits
    assert limits["max_parallel_agents"] == 2, limits


@case("平行度：記憶體充裕時由 CPU 決定")
def _(tmp: Path):
    limits = capacity.parallel_limits(4, 65536)
    assert limits["limited_by"] == "cpu", limits
    assert limits["max_parallel_agents"] == 4, limits


@case("平行度：讀不到 CPU 核心數時保守回 1，而不是猜一個數字")
def _(tmp: Path):
    limits = capacity.parallel_limits(None, 65536)
    assert limits["max_parallel_agents"] == 1, limits
    assert limits["basis"] == "cpu_unknown", limits


@case("平行度：讀不到記憶體時退回只看 CPU，並標示依據")
def _(tmp: Path):
    limits = capacity.parallel_limits(8, None)
    assert limits["basis"] == "cpu_only", limits
    assert limits["max_parallel_agents"] == 8, limits


@case("平行度：極小機器也至少回 1，不會回 0 或負數")
def _(tmp: Path):
    for cpu, mem in ((1, 512), (1, 1024), (2, 2048), (1, 2048)):
        limits = capacity.parallel_limits(cpu, mem)
        assert limits["max_parallel_agents"] >= 1, (cpu, mem, limits)
        assert limits["max_parallel_heavy_tasks"] >= 1, (cpu, mem, limits)


# --------------------------------------------------------------------------
# capacity.py：連接埠建議
# --------------------------------------------------------------------------

@case("連接埠建議：避開已佔用的區間")
def _(tmp: Path):
    assert capacity.port_range_suggestion([8150]) == [8200, 8299]
    assert capacity.port_range_suggestion([]) == [8100, 8199]


@case("連接埠建議：常見服務埠不影響搜尋範圍（本來就在 8100 以下）")
def _(tmp: Path):
    assert capacity.port_range_suggestion([5432, 6379, 8080]) == [8100, 8199]


@case("連接埠建議：全部佔滿時回 None，而不是硬給一段")
def _(tmp: Path):
    occupied = list(range(capacity.PORT_SEARCH_START, capacity.PORT_SEARCH_END + 1, capacity.PORT_WINDOW))
    assert capacity.port_range_suggestion(occupied) is None


# --------------------------------------------------------------------------
# service-scan.py
# --------------------------------------------------------------------------

@case("service-scan --json 的 stdout 只有 JSON")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "service-scan.py", "--json"))
    assert report["scan"] == "services", report


@case("service-scan --json 一定要有 complete 旗標與 occupied_ports")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "service-scan.py", "--json"))
    assert isinstance(report["complete"], bool), report
    assert isinstance(report["occupied_ports"], list), report
    assert "suggested_port_range" in report, sorted(report)


@case("service-scan：掃描不完整時不給建議區間")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "service-scan.py", "--json"))
    if not report["complete"]:
        assert report["suggested_port_range"] is None, (
            "掃描不完整卻給了建議區間——「沒看到」不等於「沒被佔用」"
        )


@case("service-scan：建議區間不可包含任何已佔用的埠")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "service-scan.py", "--json"))
    suggestion = report["suggested_port_range"]
    if suggestion:
        start, end = suggestion
        clashes = [p for p in report["occupied_ports"] if start <= p <= end]
        assert not clashes, f"建議區間 {suggestion} 與已佔用的埠 {clashes} 重疊"


@case("service-scan 的連接埠解析不會把 IPv4 位址的每一段當成埠號")
def _(tmp: Path):
    # 這是實作過程中真的踩到的 bug：第一版的 regex 是 `[:.](\d{1,5})\b`，
    # 於是 `127.0.0.1:33697` 會解析出 0、0、1、33697 四個「埠號」。
    module = load_module("service-scan.py")
    sample = "tcp   LISTEN 0 4096   127.0.0.1:33697   0.0.0.0:*"
    assert module.parse_listening_ports(sample) == [33697], module.parse_listening_ports(sample)


@case("service-scan 的連接埠解析涵蓋三平台的輸出格式")
def _(tmp: Path):
    module = load_module("service-scan.py")
    samples = {
        "ss": ("tcp   LISTEN 0 511   [::]:8080   [::]:*", 8080),
        "lsof": ("nginx 123 root 6u IPv4 0x1 0t0 TCP 127.0.0.1:5432 (LISTEN)", 5432),
        "netstat-windows": ("  TCP    0.0.0.0:135    0.0.0.0:0    LISTENING    968", 135),
        "netstat-macos": ("tcp4  0  0  127.0.0.1.6379   *.*   LISTEN", 6379),
    }
    for label, (line, expected) in samples.items():
        ports = module.parse_listening_ports(line)
        assert expected in ports, f"{label}：{line!r} 應解析出 {expected}，實際得到 {ports}"


@case("service-scan 沒有 --json 時仍印中文報表")
def _(tmp: Path):
    result = run_script(tmp, "service-scan.py")
    assert result.returncode == 0, result.stderr
    assert "===== 既有服務／連接埠掃描 =====" in result.stdout, result.stdout[:200]


# --------------------------------------------------------------------------
# env-guard.py
# --------------------------------------------------------------------------

@case("env-guard --json 首次執行回 created，exit 0")
def _(tmp: Path):
    result = run_script(tmp, "env-guard.py", "--json")
    report = parse_json_stdout(result)
    assert report["status"] == "created", report
    assert result.returncode == 0, result.returncode


@case("env-guard --json 第二次執行回 ok，exit 0")
def _(tmp: Path):
    run_script(tmp, "env-guard.py", "--json")
    result = run_script(tmp, "env-guard.py", "--json")
    report = parse_json_stdout(result)
    assert report["status"] == "ok", report
    assert result.returncode == 0, result.returncode


@case("env-guard --json 指紋不符時回 mismatch，且 exit code 仍是 1")
def _(tmp: Path):
    run_script(tmp, "env-guard.py", "--json")
    path = tmp / ".harness" / "env-fingerprint.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    recorded["os"] = "PlanNine"
    path.write_text(json.dumps(recorded, ensure_ascii=False), encoding="utf-8")

    result = run_script(tmp, "env-guard.py", "--json")
    report = parse_json_stdout(result)
    assert report["status"] == "mismatch", report
    assert result.returncode == 1, "JSON 模式不可以吃掉 exit code——呼叫方靠它分辨"
    assert any(item["field"] == "os" for item in report["mismatches"]), report


@case("env-guard 人看的模式與 --json 對同一件事的判定一致")
def _(tmp: Path):
    run_script(tmp, "env-guard.py")
    path = tmp / ".harness" / "env-fingerprint.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    recorded["arch"] = "vax"
    path.write_text(json.dumps(recorded, ensure_ascii=False), encoding="utf-8")

    human = run_script(tmp, "env-guard.py")
    # 人看的模式會把不符寫回檔案嗎？不會——所以兩邊看到的是同一份記錄。
    machine = run_script(tmp, "env-guard.py", "--json")
    assert human.returncode == machine.returncode == 1, (human.returncode, machine.returncode)
    assert json.loads(machine.stdout)["status"] == "mismatch"
    assert "狀態：不符" in human.stdout, human.stdout[:200]


# --------------------------------------------------------------------------
# 快取
# --------------------------------------------------------------------------

@case("掃描腳本會把結果寫進 .harness/last-scan.json")
def _(tmp: Path):
    run_script(tmp, "machine-profile.py", "--json")
    cache = tmp / ".harness" / "last-scan.json"
    assert cache.exists(), "掃描完應該留下快取檔"
    document = json.loads(cache.read_text(encoding="utf-8"))
    assert "machine" in document["sections"], document["sections"].keys()


@case("兩支掃描腳本各自寫入不同區塊，不會互相覆蓋")
def _(tmp: Path):
    run_script(tmp, "machine-profile.py", "--json")
    run_script(tmp, "service-scan.py", "--json")
    document = json.loads((tmp / ".harness" / "last-scan.json").read_text(encoding="utf-8"))
    assert {"machine", "services"} <= set(document["sections"]), document["sections"].keys()


@case("能力指紋不同時快取一律不可重用（黃金法則第 5 條）")
def _(tmp: Path):
    run_script(tmp, "machine-profile.py", "--json")
    cache = tmp / ".harness" / "last-scan.json"
    document = json.loads(cache.read_text(encoding="utf-8"))
    document["capability_key"] = "os=PlanNine|arch=vax"
    cache.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    assert scan_cache.load(root=tmp) is None, "換了機器還重用快取，等於跳過強制掃描"


@case("快取檔壞掉時回 None，而不是半套資料")
def _(tmp: Path):
    (tmp / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp / ".harness" / "last-scan.json").write_text("{ 這不是 JSON", encoding="utf-8")
    assert scan_cache.load(root=tmp) is None


@case("快取檔不存在時回 None")
def _(tmp: Path):
    assert scan_cache.load(root=tmp) is None


@case("快取可重用時帶 age_seconds，讓呼叫方自己判斷夠不夠新")
def _(tmp: Path):
    scan_cache.write("machine", {"cpu_count": 4}, root=tmp)
    document = scan_cache.load(root=tmp)
    assert document is not None, "剛寫入的快取應可重用"
    assert isinstance(document["age_seconds"], float), document.get("age_seconds")


# --------------------------------------------------------------------------
# init.py
# --------------------------------------------------------------------------

@case("init --json 把三個區塊合併成一份文件，stdout 只有 JSON")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "init.py", "--json"))
    assert set(report["sections"]) == {"machine", "services", "env_guard"}, report["sections"].keys()
    assert report["status"] == "ok", report


@case("init --json 在指紋不符時回 env_mismatch 且 exit 1")
def _(tmp: Path):
    run_script(tmp, "env-guard.py", "--json")
    path = tmp / ".harness" / "env-fingerprint.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    recorded["os"] = "PlanNine"
    path.write_text(json.dumps(recorded, ensure_ascii=False), encoding="utf-8")

    result = run_script(tmp, "init.py", "--json")
    report = parse_json_stdout(result)
    assert report["status"] == "env_mismatch", report
    assert result.returncode == 1, result.returncode


@case("init --json 的每個區塊都不含解析失敗的 error 欄位")
def _(tmp: Path):
    report = parse_json_stdout(run_script(tmp, "init.py", "--json"))
    for key, payload in report["sections"].items():
        assert "error" not in payload, f"{key} 區塊解析失敗：{payload.get('error')}"


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
