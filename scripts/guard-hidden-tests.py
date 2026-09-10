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

第二版改成：先用 shell 分隔符號（`;` `&&` `||` `|` 換行）把指令切成多個
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

歷史（2026-09-10 第三版，對應 docs/improvement-suggestions.md 的 P0-1 與 P0-5）：
第二版有兩條路徑在做同一件事卻用了不同標準——Bash 分支會先把相對路徑正規化
再比對，但 Edit/Write/Read/Grep/Glob 分支只把反斜線換成正斜線，然後直接對
"tests/hidden" 做字串前綴比對。結果是同一個檔案換個寫法就擋不到（實測）：
  - `/abs/path/repo/tests/hidden/x.py`——而 Read 工具的 file_path 規定
    就是要絕對路徑，等於「不能看到隱藏測試」這條保護在正常用法下完全無效
  - `./tests/hidden/x.py`、`tests//hidden/x.py`、`tests/../tests/hidden/x.py`
  - 已鎖定的公開測試同樣可以用絕對路徑覆寫
這版把所有分支統一走 `_to_repo_relative()`：吸收反斜線、`./`、多重斜線、`..`
與絕對路徑，一律換算成「相對於 repo 根目錄」的路徑後再比對；換算結果落在
repo 之外時回傳 None，代表不歸本 hook 管（例如 `/etc/hosts`）。

同一版也把腳本改成 **fail-closed**：第二版只要腳本自己丟出任何例外、或收到
無法解析的 stdin，都會以非 2 的 exit code 結束，而 Claude Code 只把 exit code 2
當成 blocking error——也就是說防護壞掉的時候是「全開」的，而且沒有任何訊號。
現在未預期的例外與無法解析的輸入一律 exit 2 並說明原因。
注意：腳本「根本沒被執行到」（找不到 python3、hook 路徑寫錯）這種情況，
腳本自己救不了，靠 `scripts/guard-selfcheck.py`（SessionStart hook）在 session
一開始就驗證防護是否真的生效，以及 `scripts/verify-locks.py` 的事後雜湊稽核。

歷史（2026-09-10 第四版，對應 P1-1）：隱藏測試改成「封存」機制——
`scripts/seal-hidden-tests.py` 會把 `tests/hidden/` 底下的檔案加密搬到 repo 之外
（見 `scripts/hidden_vault.py` 的模組說明）。這讓本 hook 的角色從「唯一防線」
降級成「縱深防禦的其中一層」：真正擋住 implementer 的是「檔案不在工作目錄裡」
（Claude Code 的檔案工具本來就以專案目錄為界）加上「內容是密文」。
本腳本仍然保護兩件事：`tests/hidden/`（封存前的暫存區），
以及封存庫路徑本身（`VAULT_DIRS`，讓誤觸時得到明確訊息而不是一堆亂碼）。

Claude Code 會把這次工具呼叫的資訊以 JSON 透過 stdin 傳入本腳本。
- Edit/Write 等工具的目標路徑在 tool_input.file_path。
- Bash 工具的指令字串在 tool_input.command。
- Read/Grep/Glob 工具用來判斷「是否在讀取 tests/hidden/」，
  分別看 tool_input.file_path / tool_input.path / tool_input.glob。
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 這裡刻意用 import（不像 utf8_output 是內嵌的）：設定檔的驗證邏輯只能有一份，
# 兩份會漂移，而漂移正是本 repo 已經吃過兩次虧的那類 bug。
#
# 但 module 層的例外**跑在底下那個頂層 try/except 之外**，會直接變成 traceback
# 加 exit 1——而 Claude Code 只把 exit 2 當成 blocking error，1 是 non-blocking，
# 工具照樣執行。也就是說「import 或設定壞掉」會變成靜默放行，正是本腳本最該避免的
# 失敗模式。所以這裡把錯誤接下來記著，等 main() 開頭再用 _block() 轉成 exit 2。
# （這個洞是 scripts/test-config.py 的 fail-closed 案例抓出來的。）
try:
    import harness_config  # noqa: E402

    _STARTUP_ERROR = None
except BaseException as _exc:  # noqa: BLE001 — 任何 import 失敗都要 fail-closed
    harness_config = None
    _STARTUP_ERROR = _exc

# Windows 主控台預設用系統 ANSI 代碼頁（英文 cp1252、繁中 cp950），印中文會丟
# UnicodeEncodeError。這支腳本刻意**不** import scripts/utf8_output.py，而是內嵌
# 同一套邏輯：它是 hook，多一個 import 就多一個失敗點，而這裡失敗的後果特別嚴重
# ——print 崩潰會讓 exit code 從 2（真正擋下）變成 1（non-blocking error，
# 工具照樣執行），也就是在 Windows 上「擋下」會悄悄變成「放行」。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

HARNESS_DIR_PREFIX = ".harness"


def _repo_root() -> Path:
    """repo 根目錄。優先用 Claude Code 提供的 CLAUDE_PROJECT_DIR，沒有才退回 cwd。

    hook 不保證以 repo 根目錄當工作目錄執行，一旦 cwd 不是 repo 根，
    「相對路徑比對」就會整組對不上，所以這裡一律換算成絕對路徑當基準。
    （本函式在 lock-tests.py / verify-locks.py / guard-selfcheck.py 各有一份
    相同實作，是刻意重複——這些腳本必須能被 hook 直接執行，不應該依賴
    彼此的 import 路徑。）
    """
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


REPO_ROOT = _repo_root()
LOCKED_LIST = REPO_ROOT / ".harness" / "locked-tests.list"
HIDDEN_MANIFEST = REPO_ROOT / ".harness" / "hidden-manifest.json"

# 受保護的隱藏測試暫存區。不再寫死 "tests/hidden"——見 scripts/harness_config.py 的
# 模組說明（P1-4：寫死路徑會讓非 Python 專案的保護悄悄失效）。
# 設定檔有問題時記下錯誤，由 main() 轉成 exit 2（fail-closed），不是靜靜退回預設值。
HIDDEN_TEST_PATTERNS = ["tests/hidden"]
if harness_config is not None:
    try:
        HIDDEN_TEST_PATTERNS = harness_config.load(REPO_ROOT)["hidden_test_paths"]
    except BaseException as _exc:  # noqa: BLE001 — 設定壞掉一律 fail-closed
        _STARTUP_ERROR = _exc


def _vault_dirs() -> list:
    """已封存隱藏測試的存放位置（P1-1），一律在 repo 之外，因此不能靠
    repo 相對路徑比對，要用絕對路徑判斷。

    封存內容本身是加密的（見 scripts/hidden_vault.py），所以就算這裡漏掉一條
    路徑寫法，讀到的也只是密文——這一層純粹是縱深防禦，讓「不小心碰到」
    也會得到明確的拒絕訊息，而不是一堆看不懂的亂碼。

    來源有三個，全部納入：預設位置、HARNESS_HIDDEN_DIR 覆寫、以及 manifest 裡
    實際記錄過的位置（涵蓋「封存時有覆寫、現在沒設環境變數」這種情況）。
    """
    dirs = []

    def add(raw):
        if not raw:
            return
        try:
            resolved = Path(raw).resolve()
        except OSError:
            return
        if resolved not in dirs:
            dirs.append(resolved)

    add(REPO_ROOT.parent / ".harness-hidden" / REPO_ROOT.name)
    add(os.environ.get("HARNESS_HIDDEN_DIR"))

    if HIDDEN_MANIFEST.exists():
        try:
            manifest = json.loads(HIDDEN_MANIFEST.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        for info in (manifest.get("tasks") or {}).values():
            if isinstance(info, dict):
                add(info.get("vault_dir"))
                add(info.get("task_dir"))

    return dirs


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

SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

VAULT_DIRS = _vault_dirs()

VAULT_REASON = (
    "拒絕：這個路徑屬於隱藏測試封存庫（repo 之外，內容已加密）。"
    "唯一的合法入口是 `python3 scripts/run-hidden-tests.py --task-id <id> --token <權杖>`，"
    "而權杖只會交給 verifier-reviewer。若你是 implementer——隱藏測試對你不可見是刻意設計，"
    "請只依 task-spec 與公開測試實作（黃金法則第 1 條）。"
)


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _to_absolute(raw: str, cwd: Path = None):
    """把任何寫法的路徑解析成絕對路徑（吸收反斜線、`./`、多重斜線、`..`）。

    `cwd` 是虛擬工作目錄（Bash 分支追蹤 `cd` 用），預設為 repo 根目錄。
    無法解析時回傳 None。
    """
    token = _normalize(raw).strip().strip("\"'")
    if not token:
        return None

    candidate = Path(token)
    if not candidate.is_absolute():
        candidate = (cwd or REPO_ROOT) / token

    try:
        return candidate.resolve()
    except OSError:
        return None


def _to_repo_relative(raw: str, cwd: Path = None):
    """把任何寫法的路徑換算成「相對於 repo 根目錄」的 posix 路徑。

    第二版就是因為 Edit/Write/Read 這條路徑少了正規化這一步，
    才會被絕對路徑整組繞過（P0-1）。

    回傳值：
      - 字串：repo 相對路徑（`""` 代表 repo 根目錄本身）
      - None：落在 repo 之外或無法解析，不歸本 hook 的「repo 內保護」管
        （repo 之外另有封存庫的判斷，見 `_is_vault_path`）
    """
    resolved = _to_absolute(raw, cwd)
    if resolved is None:
        return None
    try:
        relative = resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return None
    return "" if relative == "." else relative


def _is_vault_path(raw: str, cwd: Path = None) -> bool:
    """這個路徑是不是落在隱藏測試封存庫底下（P1-1）。"""
    resolved = _to_absolute(raw, cwd)
    if resolved is None:
        return False
    for vault_dir in VAULT_DIRS:
        if resolved == vault_dir or vault_dir in resolved.parents:
            return True
    return False


def _parse_locked_line(line: str):
    """解析鎖定清單的一行，回傳路徑（沒有內容就回傳 None）。

    支援兩種格式：
      - 新格式（P1-2 起）：`<sha256>  <路徑>`，雜湊由 verify-locks.py 事後稽核用
      - 舊格式：純路徑一行一個（沒有雜湊，仍然可以擋寫入，只是無法事後驗證）
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(None, 1)
    if len(parts) == 2 and SHA256_PATTERN.match(parts[0]):
        return parts[1].strip().replace("\\", "/")
    return line.replace("\\", "/")


def _load_locked() -> set:
    if not LOCKED_LIST.exists():
        return set()
    locked = set()
    for line in LOCKED_LIST.read_text(encoding="utf-8").splitlines():
        path = _parse_locked_line(line)
        if path:
            locked.add(path)
    return locked


def _is_hidden_tests_path(rel) -> bool:
    if rel is None or harness_config is None:
        # harness_config 載入失敗時 main() 已經無條件擋下，走不到這裡；
        # 保守回傳 False 只是避免在那個路徑上再丟一次例外。
        return False
    return harness_config.matches(rel, HIDDEN_TEST_PATTERNS)


def _is_harness_path(rel) -> bool:
    if rel is None:
        return False
    normalized = rel.rstrip("/")
    return normalized == HARNESS_DIR_PREFIX or normalized.startswith(HARNESS_DIR_PREFIX + "/")


def _is_protected_write_target(rel, locked: set) -> bool:
    if not rel:
        return False
    if _is_hidden_tests_path(rel) or _is_harness_path(rel):
        return True
    return rel.rstrip("/") in locked


def _check_file_path(raw_path: str, locked: set, tool_name: str = None):
    """涵蓋 Edit/Write/MultiEdit 等會寫入檔案的工具。"""
    if _is_vault_path(raw_path):
        return VAULT_REASON

    target = _to_repo_relative(raw_path)
    if target is None:
        # 落在 repo 之外的路徑不歸本 hook 管（例如 /tmp、使用者家目錄）。
        return None

    if _is_hidden_tests_path(target):
        # 只擋「修改/覆蓋既有檔案」，不擋「第一次建立」——否則連合法的
        # verifier-test-writer 自己都無法建立隱藏測試檔案，整個機制會
        # 無法啟動。Edit 工具本來就只能對已存在的檔案操作，天然只會落在
        # 「檔案已存在」這個會被擋下的分支；Write 工具則額外檢查檔案是否
        # 已存在來分辨「建立」與「覆蓋」。
        if tool_name == "Write" and not (REPO_ROOT / target).exists():
            return None
        return (
            "拒絕：不可修改（或用 Write 覆蓋已存在的）隱藏測試暫存區底下的檔案"
            f"（受保護路徑：{'、'.join(HIDDEN_TEST_PATTERNS)}）。"
        )

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
        if not isinstance(value, str) or not value:
            continue
        if _is_vault_path(value):
            return VAULT_REASON
        if _is_hidden_tests_path(_to_repo_relative(value)):
            return (
                "拒絕：實作者子智能體不可讀取或搜尋隱藏測試暫存區"
                f"（受保護路徑：{'、'.join(HIDDEN_TEST_PATTERNS)}）"
                "——隱藏驗收測試，黃金法則第 1 條：不能修改，也不能看到。"
            )
    return None


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

    cwd = REPO_ROOT  # 虛擬工作目錄（絕對路徑），跟著 `cd` 走
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
            cwd = _to_absolute(args[0], cwd) or cwd
            continue

        # 封存庫（P1-1）：repo 之外，且沒有任何正當理由需要直接碰它——
        # 唯一的合法入口是 scripts/run-hidden-tests.py，而它不會把封存路徑
        # 寫進指令字串。因此這裡不分動詞，只要任何一個參數指到封存庫就擋。
        for token in args:
            if not token.startswith("-") and _is_vault_path(token, cwd):
                return VAULT_REASON
        for vault_dir in VAULT_DIRS:
            if str(vault_dir) in raw_segment:
                return VAULT_REASON

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
            if _is_protected_write_target(_to_repo_relative(target, cwd), locked):
                return (
                    "拒絕：偵測到 Bash 指令對受保護路徑（隱藏測試暫存區、.harness/ 或"
                    f"已鎖定的公開測試）執行了寫入類操作（`{verb}` 目標：「{target}」），"
                    "一律擋下。若為誤判，請改用不涉及這些路徑的方式完成，"
                    "或請 Orchestrator / verifier 協助處理。"
                )

        for target in read_candidates:
            if _is_hidden_tests_path(_to_repo_relative(target, cwd)):
                return (
                    "拒絕：偵測到 Bash 指令讀取/搜尋隱藏測試暫存區"
                    f"（`{verb}` 目標：「{target}」），一律擋下（黃金法則第 1 條——"
                    "不能修改，也不能看到）。若為誤判，請改用不涉及這個路徑的方式完成，"
                    "或請 Orchestrator / verifier 協助處理。"
                )

        for target in _extract_redirect_targets(raw_segment):
            if _is_protected_write_target(_to_repo_relative(target, cwd), locked):
                return (
                    "拒絕：偵測到 Bash 指令用重導向（>/>>）寫入受保護路徑"
                    f"（目標：「{target}」），一律擋下。若為誤判，請改用不涉及這些路徑的"
                    "方式完成，或請 Orchestrator / verifier 協助處理。"
                )

        if verb in INLINE_INTERPRETER_VERBS and any(f in args for f in INLINE_FLAGS):
            literal_prefixes = [
                # 萬用字元之後的部分無法當字面文字比對，取前面固定的那段就好。
                pattern.split("*")[0].split("?")[0].rstrip("/")
                for pattern in HIDDEN_TEST_PATTERNS
            ]
            if (
                any(prefix and prefix in raw_segment for prefix in literal_prefixes)
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


def check_payload(payload) -> str:
    """回傳擋下的理由（字串）或 None（放行）。抽成純函式方便測試直接呼叫。"""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    locked = _load_locked()

    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and command:
            return _check_bash_command(command, locked)
        return None

    if tool_name in READ_TOOL_PATH_FIELDS:
        return _check_read_tool(tool_name, tool_input)

    raw_path = tool_input.get("file_path")
    if isinstance(raw_path, str) and raw_path:
        return _check_file_path(raw_path, locked, tool_name)

    return None


def _block(reason: str) -> None:
    try:
        print(reason, file=sys.stderr)
    except (UnicodeEncodeError, OSError):
        # 連理由都印不出來，也絕對不能因此放行：exit code 2 才是真正的攔截，
        # 訊息只是附帶說明。退回純 ASCII 讓使用者至少知道發生了什麼事。
        try:
            sys.stderr.write(
                "BLOCKED by guard-hidden-tests.py "
                "(reason could not be encoded for this console)\n"
            )
        except OSError:
            pass
    # exit code 2 才會被 Claude Code 視為 blocking error 並真正擋下工具呼叫；
    # exit code 1 只是 non-blocking error，動作仍會繼續執行。
    sys.exit(2)


def main() -> None:
    if _STARTUP_ERROR is not None:
        _block(
            f"拒絕：防護設定載入失敗（{type(_STARTUP_ERROR).__name__}: {_STARTUP_ERROR}）。"
            "在修好之前一律擋下——設定壞掉時退回預設值等於讓一個 typo 就能關掉防護，"
            f"請修正 {getattr(harness_config, 'CONFIG_FILENAME', 'harness.config.json')} 後再試。"
        )

    raw = sys.stdin.read()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # fail-closed：舊版這裡是 exit 0（放行）。但「收到看不懂的輸入」代表
        # 這支腳本對這次工具呼叫失去判斷能力，放行等於在防護失效時默默全開。
        _block(
            "拒絕：防護腳本收到無法解析的輸入（stdin 不是合法 JSON），"
            "無法判斷這次工具呼叫是否碰到受保護路徑，為安全起見擋下。"
        )

    if not isinstance(payload, dict):
        _block(
            "拒絕：防護腳本收到非預期的輸入格式（JSON 最外層不是物件），"
            "無法判斷這次工具呼叫是否碰到受保護路徑，為安全起見擋下。"
        )

    reason = check_payload(payload)
    if reason:
        _block(reason)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — 刻意攔截全部：防護腳本必須 fail-closed
        _block(
            f"拒絕：防護腳本發生未預期錯誤（{type(exc).__name__}: {exc}），為安全起見擋下本次操作。"
            "這代表防作弊機制目前不可信，請先修復 scripts/guard-hidden-tests.py"
            "（可用 python3 scripts/test-guards.py 確認）再繼續。"
        )
