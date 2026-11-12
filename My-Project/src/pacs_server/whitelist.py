"""白名单：输入校验、在线维护、准入判定（R2、R4）。

身份要素 = Calling AE Title（去首尾空格、区分大小写）+ 源 IP（精确匹配）。
这是准入控制而非认证：同网段可伪造 AE + IP；本期无 TLS（实验基线假设，未经医院客户确认）。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from .audit import AuditLog
from .db import Database
from .util import now_utc

ROLES = ("STORE", "QUERY", "RETRIEVE", "MOVE_DEST")

# A-ASSOCIATE-RJ 诊断：source=1 service-user
REASON_CALLING_AE_NOT_RECOGNIZED = 3
REASON_CALLED_AE_NOT_RECOGNIZED = 7


class WhitelistError(ValueError):
    pass


def validate_ae_title(value: str) -> str:
    if value is None:
        raise WhitelistError("AE Title 不能为空")
    v = value.strip()
    if not v:
        raise WhitelistError("AE Title 不能为空或全为空格")
    if len(v) > 16:
        raise WhitelistError(f"AE Title 超过 16 个字符：{len(v)}")
    if "\\" in v:
        raise WhitelistError("AE Title 不能包含反斜杠")
    if any(ord(c) < 0x20 or ord(c) > 0x7E for c in v):
        raise WhitelistError("AE Title 只能包含可打印 ASCII 字符（不含控制字符）")
    return v


def validate_host(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except (ValueError, AttributeError):
        raise WhitelistError(f"IP 地址不合法：{value!r}（本期只接受精确 IPv4/IPv6 地址）")


def validate_port(value) -> int | None:
    if value in (None, "", 0):
        return None
    try:
        p = int(value)
    except (TypeError, ValueError):
        raise WhitelistError(f"端口不合法：{value!r}")
    if not 1 <= p <= 65535:
        raise WhitelistError(f"端口超出范围 1–65535：{p}")
    return p


def validate_roles(roles) -> list[str]:
    if isinstance(roles, str):
        roles = [r for r in roles.replace(",", " ").split() if r]
    out = []
    for r in roles:
        r = r.strip().upper()
        if r not in ROLES:
            raise WhitelistError(f"未知角色：{r}（可选：{', '.join(ROLES)}）")
        if r not in out:
            out.append(r)
    return out


@dataclass
class Peer:
    ae_title: str
    host: str
    port: int | None
    roles: list[str]
    enabled: bool
    description: str = ""

    def has(self, role: str) -> bool:
        return role in self.roles

    @classmethod
    def from_row(cls, row) -> "Peer":
        return cls(ae_title=row["ae_title"], host=row["host"], port=row["port"],
                   roles=[r for r in row["roles"].split(",") if r], enabled=bool(row["enabled"]),
                   description=row["description"])

    def to_dict(self) -> dict:
        return {"ae_title": self.ae_title, "host": self.host, "port": self.port,
                "roles": list(self.roles), "enabled": self.enabled, "description": self.description}


@dataclass
class Admission:
    accepted: bool
    peer: Peer | None
    reason: int = 0           # A-ASSOCIATE-RJ diagnostic
    alert_type: str = ""      # 拒绝时的告警类型
    message: str = ""


class Whitelist:
    def __init__(self, db: Database, audit: AuditLog):
        self.db = db
        self.audit = audit

    # ---------- 查询 ----------
    def get(self, ae_title: str) -> Peer | None:
        row = self.db.query_one("SELECT * FROM peers WHERE ae_title=?", (ae_title.strip(),))
        return Peer.from_row(row) if row else None

    def list(self) -> list[Peer]:
        return [Peer.from_row(r) for r in self.db.query("SELECT * FROM peers ORDER BY ae_title")]

    # ---------- 维护 ----------
    def add(self, ae_title: str, host: str, roles, port=None, description: str = "",
            enabled: bool = True, operator: str = "admin") -> Peer:
        ae = validate_ae_title(ae_title)
        h = validate_host(host)
        p = validate_port(port)
        rl = validate_roles(roles)
        if not rl:
            raise WhitelistError("至少需要一个角色")
        if "MOVE_DEST" in rl and p is None:
            raise WhitelistError("具有 MOVE_DEST 角色的条目必须登记端口")
        now = now_utc()
        with self.db.write() as conn:
            if conn.execute("SELECT 1 FROM peers WHERE ae_title=?", (ae,)).fetchone():
                raise WhitelistError(f"AE Title 已存在：{ae}")
            conn.execute("INSERT INTO peers(ae_title, host, port, roles, enabled, description,"
                         " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                         (ae, h, p, ",".join(rl), int(enabled), description, now, now))
            peer = Peer(ae, h, p, rl, enabled, description)
            rec = self.audit.append("PEER_ADDED", actor=operator, target=f"peer:{ae}",
                                    details={"after": peer.to_dict()}, conn=conn)
        self.audit.anchor_safely(rec)
        return peer

    def update(self, ae_title: str, operator: str = "admin", **fields) -> Peer:
        ae = ae_title.strip()
        before = self.get(ae)
        if before is None:
            raise WhitelistError(f"不存在的 AE Title：{ae}")
        after = Peer(**before.to_dict())
        if "host" in fields and fields["host"] is not None:
            after.host = validate_host(fields["host"])
        if "port" in fields:
            after.port = validate_port(fields["port"])
        if "roles" in fields and fields["roles"] is not None:
            after.roles = validate_roles(fields["roles"])
            if not after.roles:
                raise WhitelistError("至少需要一个角色")
        if "enabled" in fields and fields["enabled"] is not None:
            after.enabled = bool(fields["enabled"])
        if "description" in fields and fields["description"] is not None:
            after.description = str(fields["description"])
        if "MOVE_DEST" in after.roles and after.port is None:
            raise WhitelistError("具有 MOVE_DEST 角色的条目必须登记端口")
        with self.db.write() as conn:
            conn.execute("UPDATE peers SET host=?, port=?, roles=?, enabled=?, description=?,"
                         " updated_at=? WHERE ae_title=?",
                         (after.host, after.port, ",".join(after.roles), int(after.enabled),
                          after.description, now_utc(), ae))
            rec = self.audit.append("PEER_UPDATED", actor=operator, target=f"peer:{ae}",
                                    details={"before": before.to_dict(), "after": after.to_dict()},
                                    conn=conn)
        self.audit.anchor_safely(rec)
        return after

    def set_enabled(self, ae_title: str, enabled: bool, operator: str = "admin") -> Peer:
        return self.update(ae_title, operator=operator, enabled=enabled)

    def remove(self, ae_title: str, operator: str = "admin") -> None:
        ae = ae_title.strip()
        before = self.get(ae)
        if before is None:
            raise WhitelistError(f"不存在的 AE Title：{ae}")
        with self.db.write() as conn:
            conn.execute("DELETE FROM peers WHERE ae_title=?", (ae,))
            rec = self.audit.append("PEER_REMOVED", actor=operator, target=f"peer:{ae}",
                                    details={"before": before.to_dict()}, conn=conn)
        self.audit.anchor_safely(rec)

    # ---------- 准入 ----------
    def admit(self, calling_ae: str, called_ae: str, ip: str, server_ae: str) -> Admission:
        calling = (calling_ae or "").strip()
        if (called_ae or "").strip() != server_ae.strip():
            return Admission(False, None, REASON_CALLED_AE_NOT_RECOGNIZED, "WRONG_CALLED_AE",
                             f"被叫 AE {called_ae!r} 不是 {server_ae!r}")
        peer = self.get(calling) if calling else None
        if peer is None:
            return Admission(False, None, REASON_CALLING_AE_NOT_RECOGNIZED, "UNREGISTERED_PEER",
                             f"未登记的 AE {calling!r}")
        try:
            ip_norm = str(ipaddress.ip_address(ip))
        except ValueError:
            ip_norm = ip
        if peer.host != ip_norm:
            return Admission(False, peer, REASON_CALLING_AE_NOT_RECOGNIZED,
                             "PEER_ADDRESS_MISMATCH",
                             f"AE {calling!r} 登记 IP 为 {peer.host}，实际来源 {ip_norm}")
        if not peer.enabled:
            return Admission(False, peer, REASON_CALLING_AE_NOT_RECOGNIZED, "PEER_DISABLED",
                             f"AE {calling!r} 已停用")
        return Admission(True, peer, 0, "", "accepted")

    def move_destination(self, ae_title: str) -> tuple[Peer | None, str]:
        """返回 (目的地, 不可用原因)。只从白名单取地址，绝不信任请求方提供的地址。"""
        peer = self.get(ae_title or "")
        if peer is None:
            return None, "目的地未登记"
        if not peer.enabled:
            return None, "目的地已停用"
        if not peer.has("MOVE_DEST") or not peer.port:
            return None, "目的地不具备 MOVE_DEST 角色或未登记端口"
        return peer, ""
