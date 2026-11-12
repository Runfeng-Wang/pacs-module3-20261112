"""持久化告警（R17）。只在本地呈现（GUI / CLI / 日志），不对外推送。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .audit import AuditLog
from .db import Database
from .util import canonical_json, now_utc

LOG = logging.getLogger("pacs.alerts")

# 告警类型 -> (级别, 默认建议处置)
ALERT_TYPES: dict[str, tuple[str, str]] = {
    "UNREGISTERED_PEER": ("medium", "核实该设备是否应接入；如应接入，由信息科登记白名单后再连接"),
    "PEER_ADDRESS_MISMATCH": ("medium", "核实设备 IP 是否变更或存在 AE Title 冒用；必要时更新白名单或排查网络"),
    "PEER_DISABLED": ("medium", "该设备已被停用；如需恢复请由信息科启用并留存变更原因"),
    "WRONG_CALLED_AE": ("low", "对端配置的被叫 AE Title 错误，请按接口文档修正"),
    "UNAUTHORIZED_RETRIEVE": ("high", "非检索角色的对端请求了 MOVE/GET 上下文，可能为未授权导出尝试，请核查"),
    "UNKNOWN_MOVE_DESTINATION": ("high", "C-MOVE 目的地未登记、已停用或不具备 MOVE_DEST 角色，未发送任何影像；请核查请求方"),
    "PIXEL_CHANGED": ("high", "隔离该实例并从可信来源（备份或设备重发）恢复；排查服务器文件系统访问记录"),
    "METADATA_CHANGED": ("high", "核对变化字段的前后值；隔离该实例并从可信来源恢复；排查访问记录"),
    "STRUCTURE_CHANGED": ("medium", "文件编码或 File Meta 变化而像素和元数据未变；核查是否有未经系统的文件处理"),
    "FILE_MISSING": ("high", "库内文件缺失；从备份恢复或请设备重发，并排查删除来源"),
    "UNREADABLE": ("high", "库内文件无法解析；隔离并从可信来源恢复"),
    "INDEX_MISMATCH": ("high", "数据库索引与入库基线不一致（文件看似正常）；核查数据库直接修改的来源"),
    "BASELINE_TAMPERED": ("high", "基线记录 HMAC 不符，基线本身可能被改；以带外记录核对并排查数据库访问"),
    "RETRIEVE_BLOCKED": ("high", "调阅时发现异常影像并已阻断发送；请先处理对应的完整性告警"),
    "UID_CONFLICT": ("medium", "同 UID 不同内容的重发已拒绝并隔离；请设备方核实（DICOM 要求内容变化必须使用新 UID）"),
    "STORED_COPY_DAMAGED": ("high", "重发时发现已存副本损坏，未报成功；新数据已隔离，需人工判断后恢复"),
    "STORAGE_FAILURE": ("high", "入库落盘或事务失败（系统故障，不属于篡改）；检查磁盘空间、权限与数据库状态"),
    "AUDIT_CHAIN_BROKEN": ("high", "审计链校验失败；以带外链头核对，排查数据库直接修改"),
    "AUDIT_ANCHOR_FAILURE": ("high", "审计锚点文件不可写（磁盘 / 权限 / 介质故障）：入库已暂停（返回 0xA701），记录仍写入数据库；修复后系统会自动补写锚点并恢复"),
    "VERIFY_ERROR": ("medium", "该实例核查过程异常，已跳过且未影响其他实例；请人工检查该实例与其基线记录"),
    "ORPHAN_FILE": ("medium", "存储目录中有无索引的文件（可能为崩溃残留，也可能为植入），已移入隔离区，需人工判断"),
}


class AlertManager:
    def __init__(self, db: Database, audit: AuditLog, default_window_s: int = 60):
        self.db = db
        self.audit = audit
        self.default_window_s = default_window_s

    def raise_alert(self, alert_type: str, subject: str, details: dict, *,
                    aggregate_key: str | None = None, window_s: int | None = None,
                    severity: str | None = None, suggestion: str | None = None) -> int:
        """生成或聚合告警，返回告警 id。

        aggregate_key 相同、状态为 open 的告警会累加 count；window_s 不为 None 时只在窗口内聚合。
        """
        sev, sugg = ALERT_TYPES.get(alert_type, ("medium", "请人工核查"))
        sev = severity or sev
        sugg = suggestion or sugg
        now = now_utc()
        rec = None
        with self.db.write() as conn:
            row = None
            if aggregate_key:
                row = conn.execute(
                    "SELECT id, last_seen_at FROM alerts WHERE aggregate_key=? AND status='open'"
                    " ORDER BY id DESC LIMIT 1", (aggregate_key,)).fetchone()
                if row is not None and window_s is not None:
                    last = datetime.fromisoformat(row["last_seen_at"])
                    if (datetime.now(timezone.utc) - last).total_seconds() > window_s:
                        row = None
            if row is not None:
                conn.execute("UPDATE alerts SET count=count+1, last_seen_at=?, details_json=?"
                             " WHERE id=?", (now, canonical_json(details), row["id"]))
                alert_id = row["id"]
                rec = self.audit.append("ALERT_AGGREGATED", target=subject, outcome="info",
                                        details={"alert_id": alert_id, "type": alert_type},
                                        conn=conn)
            else:
                cur = conn.execute(
                    "INSERT INTO alerts(created_at, last_seen_at, severity, type, subject,"
                    " details_json, suggestion, status, count, aggregate_key)"
                    " VALUES (?,?,?,?,?,?,?, 'open', 1, ?)",
                    (now, now, sev, alert_type, subject, canonical_json(details), sugg,
                     aggregate_key))
                alert_id = cur.lastrowid
                rec = self.audit.append("ALERT_RAISED", target=subject, outcome="warning",
                                        details={"alert_id": alert_id, "type": alert_type,
                                                 "severity": sev}, conn=conn)
        self.audit.anchor_safely(rec)
        LOG.warning("ALERT %s [%s] %s %s", alert_type, sev, subject, details)
        return alert_id

    def ack(self, alert_id: int, operator: str) -> bool:
        with self.db.write() as conn:
            row = conn.execute("SELECT status FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if row is None or row["status"] != "open":
                return False
            conn.execute("UPDATE alerts SET status='acknowledged', ack_by=?, ack_at=? WHERE id=?",
                         (operator, now_utc(), alert_id))
            rec = self.audit.append("ALERT_ACK", actor=operator, target=f"alert:{alert_id}",
                                    details={"alert_id": alert_id}, conn=conn)
        self.audit.anchor_safely(rec)
        return True

    def list(self, status: str | None = None, limit: int = 500) -> list:
        if status:
            return self.db.query("SELECT * FROM alerts WHERE status=? ORDER BY id DESC LIMIT ?",
                                 (status, limit))
        return self.db.query("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))

    def count(self, status: str | None = "open", alert_type: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM alerts WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if alert_type:
            sql += " AND type=?"
            params.append(alert_type)
        return int(self.db.scalar(sql, tuple(params)) or 0)


def alert_details(row) -> dict:
    return json.loads(row["details_json"])
