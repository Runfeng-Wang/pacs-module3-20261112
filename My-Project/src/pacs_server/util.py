"""小工具：时间、哈希、HMAC 密钥、规范 JSON。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def now_local() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hmac_hex(key: bytes, message: str | bytes) -> str:
    if isinstance(message, str):
        message = message.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def hmac_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def load_or_create_key(key_dir: Path) -> bytes:
    """读取 HMAC 密钥；不存在时生成 32 字节随机密钥。

    信任边界：密钥与数据在同一台机器（只是不同目录），只能防“只能改数据目录、拿不到密钥”的攻击者。
    """
    key_dir.mkdir(parents=True, exist_ok=True)
    path = key_dir / "hmac.key"
    if path.exists():
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    key = secrets.token_bytes(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(key.hex(), encoding="ascii")
    os.replace(tmp, path)
    return key


def fsync_write(path: Path, data: bytes) -> None:
    """写文件并 fsync（落盘语义的基础）。"""
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
