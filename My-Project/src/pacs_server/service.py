"""PacsService：组装各组件，负责启停、启动对账、周期扫描与调阅前校验。"""

from __future__ import annotations

import getpass
import logging
import threading
from pathlib import Path

from pydicom.dataset import Dataset

from .alerts import AlertManager
from .audit import AuditLog
from .config import PacsConfig
from .db import Database
from .dicomutil import parse_file_bytes
from .integrity import Verifier
from .query import QueryEngine
from .store import ImageStore
from .util import load_or_create_key
from .whitelist import Whitelist

LOG = logging.getLogger("pacs.service")


def os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unknown"


class PacsService:
    def __init__(self, config: PacsConfig):
        config.ensure_dirs()
        self.config = config
        self.key = load_or_create_key(config.key_dir)
        self.db = Database(config.db_path)
        self.audit = AuditLog(self.db, self.key, config.anchor_path)
        self.alerts = AlertManager(self.db, self.audit, config.alert_aggregate_window_s)
        self.audit.on_anchor_failure = self._on_anchor_failure
        self.whitelist = Whitelist(self.db, self.audit)
        self.verifier = Verifier(self)
        self.store = ImageStore(self)
        self.query = QueryEngine(self)
        self.dicom = None
        self.running = False
        self._scan_stop = threading.Event()
        self._scan_thread: threading.Thread | None = None

    def _on_anchor_failure(self, error: str) -> None:
        """首次进入锚点降级时告警（告警自身的审计也无法锚定，但不会再次触发回调）。"""
        self.alerts.raise_alert("AUDIT_ANCHOR_FAILURE", "audit-anchor",
                                {"stage": "anchor-write", "error": error,
                                 "anchor_path": str(self.config.anchor_path),
                                 "effect": "入库暂停（0xA701）；只读操作照常，记录写入数据库，恢复后补写锚点"},
                                aggregate_key="AUDIT_ANCHOR_FAILURE")

    @classmethod
    def open(cls, runtime_dir: Path | str) -> "PacsService":
        return cls(PacsConfig.load(runtime_dir))

    # ---------- 启停 ----------
    def start(self, operator: str | None = None) -> dict:
        from .netserver import DicomServer

        if self.running:
            return {}
        summary = self.store.reconcile()
        self.dicom = DicomServer(self)
        self.dicom.start()
        self.running = True
        self.audit.append("SERVICE_START", actor=operator or os_user(),
                          details={"ae_title": self.config.ae_title, "host": self.config.host,
                                   "port": self.config.port,
                                   "retrieve_policy": self.config.retrieve_policy,
                                   "reconcile": {"orphans": len(summary["orphans"]),
                                                 "missing": len(summary["missing"])}})
        if self.config.periodic_scan_minutes > 0:
            self._scan_stop.clear()
            self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True,
                                                 name="PacsPeriodicScan")
            self._scan_thread.start()
        return summary

    def stop(self, operator: str | None = None) -> None:
        if not self.running:
            return
        self._scan_stop.set()
        if self.dicom:
            self.dicom.stop()
        self.running = False
        self.audit.append("SERVICE_STOP", actor=operator or os_user())

    def close(self) -> None:
        self.stop()
        self.db.close()

    def _scan_loop(self):
        interval = self.config.periodic_scan_minutes * 60
        while not self._scan_stop.wait(interval):
            try:
                self.verifier.run(trigger="periodic", operator="scheduler")
            except Exception:  # noqa: BLE001
                LOG.exception("periodic scan failed")

    def status(self) -> dict:
        db = self.db
        return {
            "ae_title": self.config.ae_title, "host": self.config.host, "port": self.config.port,
            "running": self.running,
            "active_associations": self.dicom.active_associations() if self.dicom else 0,
            "max_associations": self.config.max_associations,
            "retrieve_policy": self.config.retrieve_policy,
            "patients": db.scalar("SELECT COUNT(*) FROM patients"),
            "studies": db.scalar("SELECT COUNT(*) FROM studies"),
            "series": db.scalar("SELECT COUNT(*) FROM series"),
            "instances": db.scalar("SELECT COUNT(*) FROM instances"),
            "open_alerts": self.alerts.count("open"),
            "audit_head": self.audit.head_string(),
            "audit_anchor_ok": not self.audit.degraded,
        }

    # ---------- 审计校验（R18） ----------
    def verify_audit(self, expect_head: str | None = None, operator: str | None = None):
        self.audit.ensure_anchor_available()   # 降级时先尝试补写缺失锚点
        rep = self.audit.verify(expect_head)
        if not rep.ok:
            self.alerts.raise_alert("AUDIT_CHAIN_BROKEN", "audit",
                                    {"problems": rep.problems[:20],
                                     "first_break_seq": rep.first_break_seq,
                                     "head_seq": rep.head_seq,
                                     "anchor_max_seq": rep.anchor_max_seq},
                                    aggregate_key="AUDIT_CHAIN_BROKEN")
        self.audit.append("AUDIT_VERIFY", actor=operator or os_user(),
                          outcome="success" if rep.ok else "failure",
                          details={"ok": rep.ok, "entries": rep.entries,
                                   "head": f"{rep.head_seq}:{rep.head_hash}",
                                   "problems": len(rep.problems),
                                   "expect_head": expect_head or ""})
        return rep

    def export_audit(self, path: Path, operator: str | None = None) -> int:
        n = self.audit.export_csv(path)
        self.audit.append("AUDIT_EXPORT", actor=operator or os_user(),
                          target=str(path), details={"rows": n})
        return n

    # ---------- 调阅前校验（R16） ----------
    def retrieval_dataset(self, row, requester: str, op: str) -> Dataset:
        ok, data = self.verifier.quick_check(row)
        if ok:
            return parse_file_bytes(data)
        findings = self.verifier.verify_instance(row)
        for f in findings:
            self.verifier.raise_finding_alert(f)
        cats = sorted({f["category"] for f in findings})
        policy = self.config.retrieve_policy
        send_anyway = policy == "allow" and data is not None
        self.alerts.raise_alert(
            "RETRIEVE_BLOCKED" if not send_anyway else "RETRIEVE_BLOCKED",
            f"instance:{row['sop_uid']}",
            {"sop_uid": row["sop_uid"], "patient_id": row["patient_id"],
             "study_uid": row["study_uid"], "requester": requester, "operation": op,
             "policy": policy, "sent": send_anyway, "categories": cats},
            aggregate_key=f"RETRIEVE_BLOCKED:{row['sop_uid']}:{requester}",
            severity="high",
            suggestion=("调阅时发现异常影像并已阻断发送；请先处理对应的完整性告警" if not send_anyway
                        else "策略为 allow：异常影像已发送并告警，请通知调阅方该影像未通过完整性校验"))
        self.audit.append("RETRIEVE_INTEGRITY_FAILED", actor=requester,
                          target=f"instance:{row['sop_uid']}", outcome="failure",
                          details={"operation": op, "policy": policy, "sent": send_anyway,
                                   "categories": cats})
        if send_anyway:
            return parse_file_bytes(data)
        # 占位数据集：只有 SOP Class/Instance UID、没有 File Meta。pynetdicom 的 send_c_store
        # 在发出任何字节之前因缺少 TransferSyntaxUID 抛异常 -> 计为失败子操作，
        # UID 进入 Failed SOP Instance UID List（已在 pynetdicom 3.0.4 源码中核实）。
        placeholder = Dataset()
        placeholder.SOPClassUID = row["sop_class_uid"]
        placeholder.SOPInstanceUID = row["sop_uid"]
        return placeholder
