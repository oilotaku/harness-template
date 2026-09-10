#!/usr/bin/env python3
"""hidden_vault.py — 隱藏測試「封存庫」的共用邏輯（seal / run 兩支腳本共用）

對應 docs/improvement-suggestions.md 的 P1-1。

## 為什麼需要封存庫

在此之前，隱藏測試就躺在 `tests/hidden/`——也就是 implementer 的工作目錄裡，
唯一的防線是 `guard-hidden-tests.py` 逐一列舉路徑寫法去攔截。攻擊面是無限的
（絕對路徑、`./`、`bash -c`、變數展開、自己寫一支腳本去讀……），防守面是列舉的，
這場仗結構上就贏不了。

封存庫改成三層，每一層擋的是不同的東西：

1. **搬出工作目錄**：封存後檔案在 repo 之外。Claude Code 的 Read/Edit/Write/
   Grep/Glob 本來就以專案目錄為界，這一層直接讓這些工具「碰不到」，
   不需要任何字串比對——連「不指定 path 的全 repo 搜尋」也搜不到了。
2. **內容加密**：只搬走不夠。implementer 手上有 Bash，可以自己寫一支
   `leak.py` 去讀任何路徑，這是攔不完的。所以封存時內容會被加密，
   直接 `cat` 或用腳本讀到的都是密文。**路徑不再需要是秘密。**
3. **執行權杖**：解密金鑰由權杖推導，權杖只在封存當下印出一次，
   由 Orchestrator 轉交給 `verifier-reviewer`；implementer 的上下文裡沒有它，
   所以也沒辦法靠「反覆執行隱藏測試、讀失敗訊息」把答案逼出來（P0-4）。

## 關於這裡的「加密」

用的是 SHA-256 keystream 的 XOR，不是 AES，也沒有訊息驗證碼。這是刻意的取捨：

- **目的**：讓「直接讀檔案」拿不到明文。威脅模型是「一個沒有權杖的子智能體」，
  不是密碼分析者，也不是能修改封存檔案的攻擊者。
- **不宣稱**：這不是抵抗密碼分析的加密，不要拿它保護真正的機密資料。
- **完整性**：明文的 sha256 記在 manifest 裡，解密後會比對，
  封存檔案被亂改會被抓到（這是完整性檢查，不是防偽造）。

選 XOR keystream 是因為只用得到 Python 標準函式庫——這個模板不應該為了
防作弊機制而要求使用者先 `pip install` 什麼東西。
"""
import hashlib
import json
import os
import secrets
from pathlib import Path

MANIFEST_VERSION = 1
KEYSTREAM_INFO = b"harness-hidden-tests-v1"

# {python} 會被換成執行 runner 的直譯器本身（sys.executable）。不寫死 python3 是因為
# Windows 上常常只有 python.exe；而且用同一個直譯器，測試環境才跟 runner 一致。
DEFAULT_TEST_COMMAND = "{python} -m unittest discover -s {dir} -p 'test_*.py' -v"


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


def staging_dir(root: Path = None) -> Path:
    """封存前的暫存區：verifier-test-writer 仍然把隱藏測試寫在這裡，
    寫完執行 seal-hidden-tests.py 才搬出去。"""
    root = root or repo_root()
    return root / "tests" / "hidden"


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


def derive_key(token: str) -> bytes:
    return hashlib.sha256(KEYSTREAM_INFO + token.encode("utf-8")).digest()


def keystream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:length])


def transform(data: bytes, token: str) -> bytes:
    """XOR keystream 是對稱的：同一支函式負責加密與解密。"""
    stream = keystream(derive_key(token), len(data))
    return bytes(a ^ b for a, b in zip(data, stream))


def new_token() -> str:
    return secrets.token_hex(16)


def token_fingerprint(token: str) -> str:
    """存進 manifest 的是權杖的指紋，不是權杖本身——manifest 在 repo 裡，
    implementer 讀得到（`.harness/` 只擋寫入，不擋讀取）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
