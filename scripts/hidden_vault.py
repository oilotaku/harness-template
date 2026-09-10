#!/usr/bin/env python3
"""hidden_vault.py — 隱藏測試「封存庫」的共用邏輯（seal / run 兩支腳本共用）

對應 docs/history/improvement-suggestions.md 的 P1-1，以及第二輪報告的
P0-6 / P0-7 / P1-5 / P2-9（manifest 版本 2）。

## 為什麼需要封存庫

在此之前，隱藏測試就躺在 `tests/hidden/`——也就是 implementer 的工作目錄裡，
唯一的防線是 `guard-hidden-tests.py` 逐一列舉路徑寫法去攔截。攻擊面是無限的
（絕對路徑、`./`、`bash -c`、變數展開、自己寫一支腳本去讀……），防守面是列舉的，
這場仗結構上就贏不了。

封存庫分成幾層，每一層擋的是不同的東西：

1. **搬出工作目錄**：封存後檔案在 repo 之外。Claude Code 的 Read/Edit/Write/
   Grep/Glob 本來就以專案目錄為界，這一層直接讓這些工具「碰不到」。
2. **內容加密**：implementer 手上有 Bash，可以自己寫一支 `leak.py` 去讀任何路徑，
   這是攔不完的。所以封存時內容會被加密，直接讀到的都是密文。
3. **執行權杖**：解密金鑰由權杖推導，權杖只在封存當下印出一次，
   由 Orchestrator 轉交給 `verifier-reviewer`；implementer 的上下文裡沒有它。
4. **manifest 簽章（版本 2 新增）**：runner 信任的每一個欄位（`test_command`、
   `task_dir`、`files`、鎖定清單的 sha256、基線結果）都用權杖推導的 HMAC 簽過。
   第二輪審視實測：`.harness/` 只擋「工具層」的寫入，implementer 用 Write 寫一支
   腳本再用 Bash 跑（指令字串裡沒有任何受保護路徑），就能把 `test_command`
   換成一支印「OK」的假程式，verifier 用正確權杖會得到「全部通過」。
   權杖是整套機制裡唯一 implementer 拿不到的東西，所以也是唯一能當簽章金鑰的東西。

## 關於這裡的「加密」

用的是 SHA-256 keystream 的 XOR，不是 AES。這是刻意的取捨：只用得到 Python
標準函式庫——這個模板不應該為了防作弊機制而要求使用者先 `pip install` 什麼東西。

版本 1 的金鑰只跟權杖有關，所以同一個 task 的每個檔案共用同一條 keystream——
教科書第一頁的 two-time pad：`C1 xor C2 == P1 xor P2`，拿公開測試的檔頭當 crib
就能還原隱藏測試的開頭（第二輪 P1-5，實測成立）。版本 2 把檔案路徑混進金鑰推導，
每個檔案各自一條 keystream。

**這套機制擋的是什麼、不擋什麼**（誠實版本，第二輪 P1-8）：它擋的是「順手看一眼」
「習慣性搜整個 repo」「用工具的正常用法碰到」這類**非刻意**的洩題，並讓**刻意**的
繞過留下痕跡（簽章不符、稽核不符）。它不擋一個「決心繞過、而且跟 verifier 共用
同一個 OS 使用者」的子智能體——權杖會經過 Claude Code 的 transcript，解密後的明文
在測試執行期間短暫存在於暫存目錄，那需要獨立的使用者或容器，超出本模板範圍。
"""
import hashlib
import hmac
import json
import os
import secrets
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_config  # noqa: E402

# 版本 2：每檔獨立金鑰 + manifest 項目簽章。runner 遇到版本 1 一律要求重新封存——
# 相容等於留一條「把 signature 刪掉就回到沒簽章」的路。
MANIFEST_VERSION = 2
KEYSTREAM_INFO = b"harness-hidden-tests-v2"
MANIFEST_MAC_INFO = b"harness-manifest-mac-v1"

# 測試指令範本裡唯三會被代換的 placeholder。用 str.replace 逐一代換而不是
# str.format——第二輪 P2-9：Go 的 `-run '^Test{Foo}$'` 這種字面大括號會讓
# format 丟 KeyError，runner 以 traceback 結束、exit 1，被 verifier 讀成「有測試失敗」。
PLACEHOLDERS = ("dir", "repo", "python")

# 預設測試指令由 harness_config 提供（可用 harness.config.json 覆寫）。
DEFAULT_TEST_COMMAND = harness_config.DEFAULT_HIDDEN_TEST_COMMAND


# --------------------------------------------------------------------- 路徑


def repo_root() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        return Path(raw).resolve()
    except OSError:
        return Path.cwd()


def manifest_path(root: Path = None) -> Path:
    root = root or repo_root()
    return root / ".harness" / "hidden-manifest.json"


def locked_list_path(root: Path = None) -> Path:
    root = root or repo_root()
    return root / ".harness" / "locked-tests.list"


def staging_dirs(root: Path = None) -> list:
    """封存前的暫存區（可能不只一個）：verifier-test-writer 把隱藏測試寫在這裡，
    寫完執行 seal-hidden-tests.py 才搬出去。

    路徑來自 harness.config.json 的 `hidden_test_paths`，預設 ["tests/hidden"]。
    回傳的是「設定裡列到的目錄」，不論存不存在——呼叫端要自己處理不存在的情況。
    """
    root = root or repo_root()
    config = harness_config.load(root)
    dirs = []
    for pattern in config["hidden_test_paths"]:
        if "*" in pattern or "?" in pattern:
            dirs.extend(sorted(p for p in root.glob(pattern) if p.is_dir()))
        else:
            dirs.append(root / pattern)
    return dirs


def test_command(root: Path = None) -> str:
    return harness_config.load(root or repo_root())["hidden_test_command"]


def default_vault_dir(root: Path = None) -> Path:
    root = root or repo_root()
    return root.parent / ".harness-hidden" / root.name


def resolve_vault_dir(root: Path = None) -> Path:
    """封存庫位置。可用 HARNESS_HIDDEN_DIR 覆寫（例如 repo 的父目錄不可寫時）。"""
    root = root or repo_root()
    override = os.environ.get("HARNESS_HIDDEN_DIR")
    vault = Path(override).resolve() if override else default_vault_dir(root)
    return vault


def assert_outside_repo(vault: Path, root: Path) -> None:
    """封存庫若落在 repo 裡面，整個機制就失去意義，必須擋下來。"""
    try:
        vault.relative_to(root)
    except ValueError:
        return
    raise ValueError(
        f"封存庫「{vault}」落在 repo（{root}）裡面，這樣 implementer 仍然讀得到，"
        "等於沒有隔離。請把 HARNESS_HIDDEN_DIR 設到 repo 之外的路徑。"
    )


# --------------------------------------------------------------------- 加解密


def derive_key(token: str, relative_path: str) -> bytes:
    """每個檔案各自一把金鑰（第二輪 P1-5）。

    路徑混進推導之後，兩個檔案的 keystream 不同，`C1 xor C2` 不再等於 `P1 xor P2`。
    路徑用封存內的相對路徑（posix 分隔），seal 與 run 兩端算出來的才會一致。
    """
    return hashlib.sha256(
        KEYSTREAM_INFO + token.encode("utf-8") + b"\0" + relative_path.encode("utf-8")
    ).digest()


def keystream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:length])


def transform(data: bytes, token: str, relative_path: str) -> bytes:
    """XOR keystream 是對稱的：同一支函式負責加密與解密。"""
    stream = keystream(derive_key(token, relative_path), len(data))
    return bytes(a ^ b for a, b in zip(data, stream))


def new_token() -> str:
    return secrets.token_hex(16)


def token_fingerprint(token: str) -> str:
    """存進 manifest 的是權杖的指紋，不是權杖本身——manifest 在 repo 裡，
    implementer 讀得到（`.harness/` 只擋寫入，不擋讀取）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path):
    """檔案的 sha256；檔案不存在回 None（呼叫端要分清楚「沒有」與「不同」）。"""
    if not path.is_file():
        return None
    return sha256_bytes(path.read_bytes())


# ------------------------------------------------------------------- 簽章


def mac_key(token: str) -> bytes:
    """簽章金鑰跟加密金鑰分開推導，不共用同一把。"""
    return hashlib.sha256(MANIFEST_MAC_INFO + token.encode("utf-8")).digest()


def _canonical(body: dict) -> bytes:
    # sort_keys=True：簽章要對 JSON 重排不敏感，否則 save_manifest 的 sort_keys
    # 一寫回去就自己把簽章弄壞。
    return json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_entry(entry: dict, token: str) -> str:
    body = {k: v for k, v in entry.items() if k != "signature"}
    return hmac.new(mac_key(token), _canonical(body), "sha256").hexdigest()


def verify_entry(entry: dict, token: str) -> bool:
    expected = sign_entry(entry, token)
    provided = entry.get("signature")
    if not isinstance(provided, str):
        return False
    return hmac.compare_digest(expected, provided)


# ------------------------------------------------------------------- 測試指令


def render_test_command(template: str, substitutions: dict) -> list:
    """把指令範本斷詞後逐一代換三個 placeholder，回傳 argv。

    先斷詞再代換：反過來的話，路徑裡只要有空白（Windows 的 C:\\Program Files\\…）
    就會被 shlex 拆成兩個參數。用 str.replace 而不是 format：範本裡任何其他的
    `{…}` 都是字面內容（第二輪 P2-9），不是我們的 placeholder。
    """
    argv = []
    for word in shlex.split(template):
        for name in PLACEHOLDERS:
            word = word.replace("{" + name + "}", str(substitutions[name]))
        argv.append(word)
    return argv


# ------------------------------------------------------------------- manifest


def load_manifest(root: Path = None) -> dict:
    path = manifest_path(root)
    if not path.exists():
        return {"version": MANIFEST_VERSION, "tasks": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("tasks", {})
    return data


def save_manifest(data: dict, root: Path = None) -> None:
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
