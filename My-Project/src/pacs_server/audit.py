"""哈希链审计（R18）。

每条记录：entry_hash = SHA256(prev_hash + canonical_json(字段))，hmac = HMAC(key, entry_hash)。
提交后再把 “seq<TAB>entry_hash<TAB>hmac” 追加到数据库之外的锚点文件。

能检测：中间记录被改 / 删 / 插入（链断、序号缺口）；只截断数据库尾部（锚点 seq 更大）；
        没有密钥的重算（HMAC 不符）；带外链头不在当前链中。
不能检测：持有密钥 + 写权限的整链重算；数据库与锚点被同时截断且没有带外链头可比对。

锚点写入失败（独立复核后修复，design D-L）：
- anchor_safely() 捕获写入异常，绝不把异常抛给调用方；首次失败时进入“锚点降级”状态并回调告警。
- 降级期间审计记录仍在数据库中提交（链与 HMAC 照常），只是暂未锚定；入库（C-STORE）在降级期间被预先拒绝。
- recover_anchors() 把数据库中所有未锚定的 seq 补写到锚点文件；成功后解除降级并追加 AUDIT_ANCHOR_RECOVERED。
- 边界：降级窗口内的记录在补写之前没有锚点保护（窗口内的尾部截断只能靠带外链头发现）。
"""

from __future__ import annotations

import csv
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database
from .util import canonical_json, hmac_equal, hmac_hex, now_local, now_utc, sha256_hex

GENESIS = "0" * 64


@dataclass
class AuditReport:
    ok: bool
    entries: int
    head_seq: int
    head_hash: str
    problems: list[str] = field(default_factory=list)
    first_break_seq: int | None = None
    anchor_max_seq: int = 0
    unanchored_entries: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class AuditLog:
    def __init__(self, db: Database, key: bytes, anchor_path: Path):
        self.db = db
        self.key = key
        self.anchor_path = Path(anchor_path)
        self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        self._anchor_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.degraded = False                 # 锚点降级：存在未锚定的已提交记录
        self.last_anchor_error: str | None = None
        self.degraded_since: str | None = None
        self.on_anchor_failure = None         # 回调(error: str)：首次进入降级时调用（由 service 设置为告警）

    # ---------- 写入 ----------
    def _fields(self, row: dict) -> dict:
        return {k: row[k] for k in ("seq", "ts_utc", "ts_local", "event_type", "actor",
                                    "actor_ip", "target", "outcome", "details_json")}

    def _entry_hash(self, prev_hash: str, fields: dict) -> str:
        return sha256_hex((prev_hash + canonical_json(fields)).encode("utf-8"))

    def append(self, event_type: str, *, actor: str = "system", actor_ip: str = "",
               target: str = "", outcome: str = "success", details: dict | None = None,
               conn=None) -> dict:
        """追加一条审计记录。传入 conn 时并入调用方的事务（例如入库事务），否则自开事务。

        返回 {"seq", "entry_hash", "hmac"}；调用方事务提交后应调用 anchor_safely(rec)。
        """
        if conn is None:
            with self.db.write() as c:
                rec = self._insert(c, event_type, actor, actor_ip, target, outcome, details)
            self.anchor_safely(rec)
            return rec
        return self._insert(conn, event_type, actor, actor_ip, target, outcome, details)

    def _insert(self, conn, event_type, actor, actor_ip, target, outcome, details) -> dict:
        last = conn.execute("SELECT seq, entry_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = last["entry_hash"] if last else GENESIS
        seq = (last["seq"] + 1) if last else 1
        row = {
            "seq": seq, "ts_utc": now_utc(), "ts_local": now_local(),
            "event_type": event_type, "actor": actor or "", "actor_ip": actor_ip or "",
            "target": target or "", "outcome": outcome,
            "details_json": canonical_json(details or {}),
        }
        entry_hash = self._entry_hash(prev_hash, self._fields(row))
        mac = hmac_hex(self.key, entry_hash)
        conn.execute(
            "INSERT INTO audit(seq, ts_utc, ts_local, event_type, actor, actor_ip, target, outcome,"
            " details_json, prev_hash, entry_hash, hmac) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (seq, row["ts_utc"], row["ts_local"], event_type, row["actor"], row["actor_ip"],
             row["target"], outcome, row["details_json"], prev_hash, entry_hash, mac))
        return {"seq": seq, "entry_hash": entry_hash, "hmac": mac}

    def anchor_safely(self, rec: dict | list[dict]) -> bool:
        """写锚点；失败时进入降级状态并返回 False，不向调用方抛异常（避免失败审计 / 告警的递归异常）。"""
        try:
            self.anchor(rec)
            return True
        except Exception as exc:  # noqa: BLE001
            with self._state_lock:
                first = not self.degraded
                self.degraded = True
                self.last_anchor_error = f"{type(exc).__name__}: {exc}"[:300]
                if first:
                    self.degraded_since = now_utc()
            if first and self.on_anchor_failure is not None:
                try:
                    self.on_anchor_failure(self.last_anchor_error)
                except Exception:  # noqa: BLE001
                    pass
            return False

    def missing_anchor_seqs(self) -> list[int]:
        anchored = set(self._read_anchors())
        return [r["seq"] for r in self.db.query("SELECT seq FROM audit ORDER BY seq")
                if r["seq"] not in anchored]

    def recover_anchors(self) -> tuple[bool, int]:
        """把数据库中所有未锚定的已提交记录补写到锚点文件。返回 (是否成功, 补写条数)。"""
        missing = set(self.missing_anchor_seqs())
        recs = [{"seq": r["seq"], "entry_hash": r["entry_hash"], "hmac": r["hmac"]}
                for r in self.db.query("SELECT seq, entry_hash, hmac FROM audit ORDER BY seq")
                if r["seq"] in missing]
        try:
            if recs:
                self.anchor(recs)
        except Exception as exc:  # noqa: BLE001
            with self._state_lock:
                self.last_anchor_error = f"{type(exc).__name__}: {exc}"[:300]
            return False, 0
        with self._state_lock:
            was, since, err = self.degraded, self.degraded_since, self.last_anchor_error
            self.degraded = False
            self.degraded_since = None
        if was:
            self.append("AUDIT_ANCHOR_RECOVERED", outcome="info",
                        details={"backfilled": len(recs), "degraded_since": since,
                                 "last_error": err})
        return True, len(recs)

    def ensure_anchor_available(self) -> bool:
        """未降级时直接返回 True；降级时尝试补写缺失锚点，成功即恢复。"""
        if not self.degraded:
            return True
        ok, _ = self.recover_anchors()
        return ok

    def anchor(self, rec: dict | list[dict]) -> None:
        """底层写入（失败会抛异常）。业务代码请使用 anchor_safely()。"""
        recs = rec if isinstance(rec, list) else [rec]
        with self._anchor_lock:
            with open(self.anchor_path, "a", encoding="ascii") as fh:
                for r in recs:
                    fh.write(f"{r['seq']}\t{r['entry_hash']}\t{r['hmac']}\n")
                fh.flush()
                os.fsync(fh.fileno())

    # ---------- 查询 ----------
    def head(self) -> tuple[int, str]:
        row = self.db.query_one("SELECT seq, entry_hash FROM audit ORDER BY seq DESC LIMIT 1")
        return (row["seq"], row["entry_hash"]) if row else (0, GENESIS)

    def head_string(self) -> str:
        seq, h = self.head()
        return f"{seq}:{h}"

    def recent(self, limit: int = 200, event_type: str | None = None) -> list:
        if event_type:
            return self.db.query("SELECT * FROM audit WHERE event_type=? ORDER BY seq DESC LIMIT ?",
                                 (event_type, limit))
        return self.db.query("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))

    def _read_anchors(self) -> dict[int, tuple[str, str]]:
        anchors: dict[int, tuple[str, str]] = {}
        if not self.anchor_path.exists():
            return anchors
        for line in self.anchor_path.read_text(encoding="ascii").splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 3 and parts[0].isdigit():
                anchors[int(parts[0])] = (parts[1], parts[2])
        return anchors

    # ---------- 校验 ----------
    def verify(self, expect_head: str | None = None) -> AuditReport:
        rows = self.db.query("SELECT * FROM audit ORDER BY seq ASC")
        problems: list[str] = []
        first_break: int | None = None
        prev_hash = GENESIS
        expected_seq = 1
        chain: dict[int, str] = {}

        def brk(seq: int, msg: str):
            nonlocal first_break
            problems.append(msg)
            if first_break is None:
                first_break = seq

        for r in rows:
            d = dict(r)
            if d["seq"] != expected_seq:
                brk(d["seq"], f"序号缺口：期望 {expected_seq}，实际 {d['seq']}（中间记录被删除）")
            if d["prev_hash"] != prev_hash:
                brk(d["seq"], f"第 {d['seq']} 条 prev_hash 与上一条不符（链断）")
            recomputed = self._entry_hash(d["prev_hash"], self._fields(d))
            if recomputed != d["entry_hash"]:
                brk(d["seq"], f"第 {d['seq']} 条内容与 entry_hash 不符（记录被修改）")
            if not hmac_equal(hmac_hex(self.key, d["entry_hash"]), d["hmac"]):
                brk(d["seq"], f"第 {d['seq']} 条 HMAC 不符（无密钥重算或篡改）")
            chain[d["seq"]] = d["entry_hash"]
            prev_hash = d["entry_hash"]
            expected_seq = d["seq"] + 1

        head_seq = rows[-1]["seq"] if rows else 0
        head_hash = rows[-1]["entry_hash"] if rows else GENESIS

        anchors = self._read_anchors()
        anchor_max = max(anchors) if anchors else 0
        if anchor_max > head_seq:
            brk(head_seq + 1, f"尾部截断：锚点最大 seq={anchor_max} 大于数据库最大 seq={head_seq}")
        for seq, (h, mac) in sorted(anchors.items()):
            if seq in chain and chain[seq] != h:
                brk(seq, f"第 {seq} 条与锚点记录的 entry_hash 不一致")
            if not hmac_equal(hmac_hex(self.key, h), mac):
                problems.append(f"锚点第 {seq} 行 HMAC 不符（锚点文件被改）")
        unanchored = sum(1 for seq in chain if seq not in anchors)

        if expect_head:
            try:
                s, h = expect_head.split(":", 1)
                s = int(s)
                if chain.get(s) != h:
                    brk(s, f"带外链头 {s}:{h[:12]}… 不在当前链中（可能被整体截断或重算）")
            except ValueError:
                problems.append(f"带外链头格式错误：{expect_head}（应为 SEQ:HASH）")

        return AuditReport(ok=not problems, entries=len(rows), head_seq=head_seq,
                           head_hash=head_hash, problems=problems, first_break_seq=first_break,
                           anchor_max_seq=anchor_max, unanchored_entries=unanchored)

    # ---------- 导出 ----------
    def export_csv(self, path: Path) -> int:
        rows = self.db.query("SELECT * FROM audit ORDER BY seq ASC")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            cols = ["seq", "ts_utc", "ts_local", "event_type", "actor", "actor_ip", "target",
                    "outcome", "details_json", "prev_hash", "entry_hash", "hmac"]
            w.writerow(cols)
            for r in rows:
                w.writerow([r[c] for c in cols])
        return len(rows)


def details_of(row) -> dict:
    try:
        return json.loads(row["details_json"])
    except Exception:
        return {}
