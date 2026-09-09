#!/usr/bin/env python3
"""guard-hidden-tests.py — PreToolUse hook（取代 bash 版本，Win/Linux/Mac 通用）
阻擋任何對 tests/hidden/ 的存取（讀取與寫入），以及對 .harness/ 或已鎖定
公開測試的寫入，強制實作者/檢驗者分離（黃金法則第 1 條）。

歷史（2026-09-09 code review 後的第二版）：第一版只用「整個指令字串裡有沒有
出現保護路徑的子字串」+「整個指令字串裡有沒有出現寫入類關鍵字」這種粗略比對，
被抓到多個可繞過的洞：
  - `rm -rf tests/hidden`（沒有結尾斜線）不會命中 "tests/hidden/" 子字串
  - `cd tests/public && rm test_x.py` 這種先切目錄再用相對路徑的寫法
  - `sed --in-place`（長選項）沒被舊的 regex 命中
  - `python3 -c "open('.harness/x','w')..."` 這類直譯器一行指令
  - 完全沒擋「讀取」隱藏測試（`cat tests/hidden/x.py`、Read 工具），
    但黃金法則第 1 條明講「不能修改『或看到』」
  - 舊版 `>{1,2}` 判斷只看「整段指令裡有沒有出現 > 」，導致
    `diff a.py b.py 2>&1` 這種純讀取、只是把 stderr 併進 stdout 的
    無關重導向被誤判成寫入而擋下

這版改成：先用 shell 分隔符號（`;` `&&` `||` `|` 換行）把指令切成多個
「簡單指令」segment，對每個 segment 用 shlex 斷詞，並追蹤 `cd` 造成的
虛擬工作目錄，藉此把相對路徑正確解析成相對於 repo 根目錄的路徑，
再判斷該路徑是否落在受保護路徑之下（用路徑元件比對，而不是子字串比對）。

仍然不是完整的 shell parser（無法處理變數展開、command substitution、
極端的引號巢狀等），目的是大幅提高繞過成本，不是做到密不透風。
無法結構化解析的部分（例如直譯器一行指令 `-c`/`-e` 內嵌的腳本），
採用保守策略：只要該 segment 命中受保護路徑的字面文字，一律擋下，
不去嘗試理解腳本內容實際做了什麼。

同一輪修正也順手抓到兩個新版自己引入的問題，一併修正：
1. 一開始把「讀取類指令」跟「寫入類指令」的目標共用同一個保護判斷，
   導致 implementer 連 `cat` 已經交給他的公開測試都會被擋（公開測試
   「鎖定」只代表不能寫入，不代表不能讀取）——現在讀取類候選只檢查
   `tests/hidden/`，不檢查 `.harness/`/鎖定清單。
2. `tests/hidden/` 若對所有 Edit/Write 一律擋死，verifier-test-writer
   自己也無法建立隱藏測試檔案，整個機制根本無法啟動——現在 Write 工具
   對「尚不存在」的路徑放行（允許建立），只擋「修改/覆蓋已存在的檔案」，
   Edit 工具因為天生只能對既有檔案操作，維持一律擋死不受影響。
   對應的取捨是：`verifier-reviewer` 事後也讀不到 `tests/hidden/`
   （hook 無法區分呼叫者），因此它必須靠**執行**測試而不是打開檔案來
   驗收，細節見 `.claude/agents/verifier-reviewer.md`。

Claude Code 會把這次工具呼叫的資訊以 JSON 透過 stdin 傳入本腳本。
- Edit/Write 等工具的目標路徑在 tool_input.file_path。
- Bash 工具的指令字串在 tool_input.command。
- Read/Grep/Glob 工具用來判斷「是否在讀取 tests/hidden/」，
  分別看 tool_input.file_path / tool_input.path / tool_input.glob。
"""
import json
import re
import shlex
import sys
from pathlib import Path

LOCKED_LIST = Path(".harness/locked-tests.list")

HIDDEN_TESTS_PREFIX = "tests/hidden"
HARNESS_DIR_PREFIX = ".harness"

# 這些工具只要目標落在 tests/hidden/ 之下就一律擋（讀寫都擋，見模組說明）。
READ_TOOL_PATH_FIELDS = {
    "Read": ("file_path",),
    "Grep": ("path", "glob"),
    "Glob": ("path", "glob", "pattern"),
    "NotebookEdit": ("notebook_path",),
}

# 會造成寫入/變更的指令動詞（第一個 token，已剝除 sudo/環境變數前綴）。
# 這些動詞後面的每個非旗標參數都視為候選的「寫入目標」。
WRITE_VERBS = {"rm", "mv", "cp", "tee", "truncate", "install", "ln", "patch", "rsync"}

# 純讀取/檢視類指令：不會寫入，但會把內容印出來或打開編輯器，
# 只要目標命中 tests/hidden/ 就視為「看到隱藏測試」而擋下。
READ_VIEW_VERBS = {
    "cat", "less", "more", "head", "tail", "tac", "nl", "strings",
    "xxd", "od", "hexdump", "bat", "vim", "vi", "nano", "emacs", "view",
    "grep", "egrep", "fgrep", "awk",
}

# 直譯器一行指令：無法安全解析腳本內容實際做了什麼，只要 segment 裡
# 出現受保護路徑的字面文字就保守擋下（見模組說明）。
INLINE_INTERPRETER_VERBS = {"python", "python3", "node", "perl", "ruby"}
INLINE_FLAGS = {"-c", "-e"}

SEGMENT_SPLIT = re.compile(r"&&|\|\||[;\n]|(?<!\|)\|(?!\|)")
REDIRECT_PATTERN = re.compile(r"(?:^|\s)\d*>{1,2}\s*(\S+)")


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _load_locked() -> set:
    if not LOCKED_LIST.exists():
        return set()
    return {
        line.strip().replace("\\", "/")
        for line in LOCKED_LIST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _is_hidden_tests_path(path: str) -> bool:
    normalized = path.rstrip("/")
    return normalized == HIDDEN_TESTS_PREFIX or normalized.startswith(HIDDEN_TESTS_PREFIX + "/")


def _is_harness_path(path: str) -> bool:
    normalized = path.rstrip("/")
    return normalized == HARNESS_DIR_PREFIX or normalized.startswith(HARNESS_DIR_PREFIX + "/")


def _is_protected_write_target(path: str, locked: set) -> bool:
    if not path:
        return False
    if _is_hidden_tests_path(path) or _is_harness_path(path):
        return True
    return path.rstrip("/") in locked


def _check_file_path(raw_path: str, locked: set, tool_name: str = None):
    """涵蓋 Edit/Write/MultiEdit 等會寫入檔案的工具。"""
    target = _normalize(raw_path)

    if _is_hidden_tests_path(target):
        # 只擋「修改/覆蓋既有檔案」，不擋「第一次建立」——否則連合法的
        # verifier-test-writer 自己都無法建立隱藏測試檔案，整個機制會
        # 無法啟動。Edit 工具本來就只能對已存在的檔案操作，天然只會落在
        # 「檔案已存在」這個會被擋下的分支；Write 工具則額外檢查檔案是否
        # 已存在來分辨「建立」與「覆蓋」。
        if tool_name == "Write" and not Path(target).exists():
            return None
        return "拒絕：不可修改（或用 Write 覆蓋已存在的）tests/hidden/ 底下的檔案。"

    if _is_harness_path(target):
        return (
            f"拒絕：「{target}」屬於 harness 治理檔案（鎖定清單/環境指紋），"
            "不可由一般編輯動作寫入，只能由對應腳本（lock-tests.py / env-guard.py）產生。"
        )

    if target.rstrip("/") in locked:
        return f"拒絕：「{target}」已被檢驗者鎖定為公開測試，實作者不可修改。"

    return None


def _check_read_tool(tool_name: str, tool_input: dict):
    """涵蓋 Read/Grep/Glob/NotebookEdit：只擋 tests/hidden/，不擋 .harness/
    或已鎖定的公開測試——那些只需要寫入保護，讀取無害。"""
    fields = READ_TOOL_PATH_FIELDS.get(tool_name)
    if not fields:
        return None
    for field in fields:
        value = tool_input.get(field)
        if isinstance(value, str) and value and _is_hidden_tests_path(_normalize(value)):
            return (
                "拒絕：實作者子智能體不可讀取或搜尋 tests/hidden/ 目錄"
                "（隱藏驗收測試，黃金法則第 1 條——不能修改，也不能看到）。"
            )
    return None


def _resolve(cwd: str, token: str) -> str:
    """把 token 相對 cwd 解析成正規化路徑（用來判斷是否落在受保護路徑下，
    不處理跳出 repo 之外的絕對路徑語意）。"""
    token = token.strip()
    if not token:
        return ""
    if token.startswith("/"):
        combined = token
    elif cwd:
        combined = f"{cwd}/{token}"
    else:
        combined = token

    parts = []
    for part in combined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _strip_prefixes(tokens):
    """剝掉 sudo、環境變數指派（VAR=value）等不影響「真正動詞」判斷的前綴。"""
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "sudo":
            i += 1
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
            i += 1
            continue
        break
    return tokens[i:]


def _extract_redirect_targets(raw_segment: str):
    targets = []
    for match in REDIRECT_PATTERN.finditer(raw_segment):
        target = match.group(1)
        # `2>&1`、`>&2` 這類 fd 對接不是檔案路徑，排除掉，
        # 避免重蹈舊版把 `2>&1` 誤判成「寫入」的覆轍。
        if target.startswith("&"):
            continue
        targets.append(target.strip("\"'"))
    return targets


def _check_bash_command(command: str, locked: set):
    normalized = _normalize(command)
    segments = [s for s in SEGMENT_SPLIT.split(normalized) if s.strip()]

    cwd = ""
    for raw_segment in segments:
        try:
            tokens = shlex.split(raw_segment, posix=True)
        except ValueError:
            tokens = raw_segment.split()
        tokens = _strip_prefixes(tokens)
        if not tokens:
            continue

        verb = tokens[0]
        args = tokens[1:]

        if verb == "cd" and args:
            cwd = _resolve(cwd, args[0])
            continue

        # 寫入類候選：受完整保護（tests/hidden/ + .harness/ + 已鎖定的公開測試）。
        # 讀取類候選：只受 tests/hidden/ 保護——已鎖定的公開測試本來就是要
        # 交給 implementer 讀的，「鎖定」只代表不能被寫入，不代表不能被讀取，
        # 不能套用跟寫入一樣的判斷，否則會連公開測試都讀不到。
        write_candidates = []
        read_candidates = []

        if verb in WRITE_VERBS:
            write_candidates.extend(a for a in args if not a.startswith("-"))
        elif verb == "sed":
            has_inplace = any(
                a == "-i" or a.startswith("-i") or a == "--in-place" or a.startswith("--in-place")
                for a in args
            )
            if has_inplace:
                write_candidates.extend(a for a in args if not a.startswith("-"))
        elif verb == "dd":
            for a in args:
                if a.startswith("of="):
                    write_candidates.append(a[len("of="):])
        elif verb == "git" and args and args[0] == "rm":
            write_candidates.extend(a for a in args[1:] if not a.startswith("-"))
        elif verb in READ_VIEW_VERBS:
            read_candidates.extend(a for a in args if not a.startswith("-"))

        for target in write_candidates:
            resolved = _resolve(cwd, target)
            if _is_protected_write_target(resolved, locked):
                return (
                    "拒絕：偵測到 Bash 指令對受保護路徑（tests/hidden/、.harness/ 或"
                    f"已鎖定的公開測試）執行了寫入類操作（`{verb}` 目標：「{target}」），"
                    "一律擋下。若為誤判，請改用不涉及這些路徑的方式完成，"
                    "或請 Orchestrator / verifier 協助處理。"
                )

        for target in read_candidates:
            resolved = _resolve(cwd, target)
            if _is_hidden_tests_path(resolved):
                return (
                    "拒絕：偵測到 Bash 指令讀取/搜尋 tests/hidden/ 目錄"
                    f"（`{verb}` 目標：「{target}」），一律擋下（黃金法則第 1 條——"
                    "不能修改，也不能看到）。若為誤判，請改用不涉及這個路徑的方式完成，"
                    "或請 Orchestrator / verifier 協助處理。"
                )

        for target in _extract_redirect_targets(raw_segment):
            resolved = _resolve(cwd, target)
            if _is_protected_write_target(resolved, locked):
                return (
                    "拒絕：偵測到 Bash 指令用重導向（>/>>）寫入受保護路徑"
                    f"（目標：「{target}」），一律擋下。若為誤判，請改用不涉及這些路徑的"
                    "方式完成，或請 Orchestrator / verifier 協助處理。"
                )

        if verb in INLINE_INTERPRETER_VERBS and any(f in args for f in INLINE_FLAGS):
            if (
                HIDDEN_TESTS_PREFIX in raw_segment
                or HARNESS_DIR_PREFIX + "/" in raw_segment
                or any(locked_path in raw_segment for locked_path in locked)
            ):
                return (
                    "拒絕：偵測到直譯器一行指令（python/node/perl -c/-e）的內容提到"
                    "受保護路徑，無法安全解析腳本實際做了什麼，保守擋下。"
                    "若為誤判，請改用不涉及這些路徑的方式完成，"
                    "或請 Orchestrator / verifier 協助處理。"
                )

    return None


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(payload, dict):
        # 合法 JSON 但不是物件（例如 null、陣列、數字），視為不適用本檢查，放行。
        sys.exit(0)

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    locked = _load_locked()

    reason = None
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and command:
            reason = _check_bash_command(command, locked)
    elif tool_name in READ_TOOL_PATH_FIELDS:
        reason = _check_read_tool(tool_name, tool_input)
    else:
        raw_path = tool_input.get("file_path")
        if isinstance(raw_path, str) and raw_path:
            reason = _check_file_path(raw_path, locked, tool_name)

    if reason:
        print(reason, file=sys.stderr)
        # exit code 2 才會被 Claude Code 視為 blocking error 並真正擋下工具呼叫；
        # exit code 1 只是 non-blocking error，動作仍會繼續執行。
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
