"""C-STORE 入库流水线（R5–R8、R13、R19）。

成功语义（D8）：只有在 (1) 原样数据集字节 + B 生成的 File Meta 写入临时文件并 fsync、
(2) 读回比对哈希、(3) 原子重命名、(4) 同一事务提交索引 + 基线 + 审计 都完成后，才返回 0x0000。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .dicomutil import build_file_bytes, diff_meta, parse_file_bytes
from .integrity import _PATIENT_FIELDS, _SERIES_FIELDS, _STUDY_FIELDS, _snap_value, \
    baseline_mac, compute_baseline
from .util import canonical_json, fsync_write, now_utc, sha256_hex

LOG = logging.getLogger("pacs.store")

# ---- C-STORE 状态码（与 docs/interface/dicom-conformance-statement.md 一致）----
SUCCESS = 0x0000
OUT_OF_RESOURCES = 0xA700          # 落盘失败
AUDIT_UNAVAILABLE = 0xA701         # 审计锚点不可写：本次未入库（已补偿撤销或预先拒绝），可稍后重试
SOP_CLASS_MISMATCH = 0xA900        # 数据集 SOP Class 与请求不符
MISSING_ATTRIBUTE = 0xC001
UID_MISMATCH = 0xC002
HIERARCHY_CONFLICT = 0xC003
UNPARSEABLE = 0xC004
UID_CONFLICT = 0xC010
STORED_COPY_DAMAGED = 0xC011
INTERNAL_FAILURE = 0xC0FF          # 数据库事务失败

STATUS_TEXT = {
    SUCCESS: "Success：已持久化、建立基线且审计已锚定（不是 Storage Commitment）",
    OUT_OF_RESOURCES: "Refused: Out of Resources（落盘失败）",
    AUDIT_UNAVAILABLE: "Refused: Out of Resources（审计锚点不可写，本次未入库）",
    SOP_CLASS_MISMATCH: "Data Set does not match SOP Class",
    MISSING_ATTRIBUTE: "Cannot understand：缺少必需属性",
    UID_MISMATCH: "Cannot understand：SOP Instance UID 与请求不一致",
    HIERARCHY_CONFLICT: "Cannot understand：层级归属冲突",
    UNPARSEABLE: "Cannot understand：数据集无法解析",
    UID_CONFLICT: "Cannot understand：同 UID 不同内容（冲突，已隔离）",
    STORED_COPY_DAMAGED: "Cannot understand：已存副本完整性异常，无法确认（新数据已隔离）",
    INTERNAL_FAILURE: "Cannot understand：内部事务失败",
}


class HierarchyConflict(Exception):
    pass


@dataclass
class PeerInfo:
    ae_title: str
    ip: str
    assoc_id: str = ""


@dataclass
class IngestResult:
    status: int
    comment: str
    outcome: str
    instance_id: int | None = None


class ImageStore:
    def __init__(self, svc):
        self.svc = svc
        self.cfg = svc.config
        self.db = svc.db
        self._guard = threading.Lock()
        self._locks: dict[str, list] = {}  # uid -> [lock, refcount]

    # ---------- 按 UID 串行化 ----------
    @contextmanager
    def _uid_lock(self, uid: str):
        with self._guard:
            entry = self._locks.setdefault(uid, [threading.Lock(), 0])
            entry[1] += 1
        entry[0].acquire()
        try:
            yield
        finally:
            entry[0].release()
            with self._guard:
                entry[1] -= 1
                if entry[1] == 0:
                    self._locks.pop(uid, None)

    # ---------- 入口 ----------
    def ingest(self, raw: bytes, transfer_syntax: str, req_sop_class: str, req_sop_uid: str,
               peer: PeerInfo) -> IngestResult:
        with self._uid_lock(req_sop_uid):
            res = self._ingest(raw, transfer_syntax, req_sop_class, req_sop_uid, peer)
        LOG.info("C-STORE %s from %s -> 0x%04X %s", req_sop_uid, peer.ae_title, res.status,
                 res.outcome)
        return res

    def _audit_store(self, event: str, peer: PeerInfo, uid: str, outcome: str, details: dict):
        """失败路径上的审计是尽力而为：审计本身出错不得掩盖或改变本次应返回的状态。"""
        try:
            self.svc.audit.append(event, actor=peer.ae_title, actor_ip=peer.ip,
                                  target=f"instance:{uid}", outcome=outcome,
                                  details={"assoc": peer.assoc_id, **details})
        except Exception:  # noqa: BLE001
            LOG.exception("audit append failed during %s for %s", event, uid)

    def _alert_safe(self, *args, **kwargs):
        try:
            self.svc.alerts.raise_alert(*args, **kwargs)
        except Exception:  # noqa: BLE001
            LOG.exception("raise_alert failed: %s", args[:1])

    def _reject(self, status: int, comment: str, peer: PeerInfo, uid: str, outcome: str,
                details: dict | None = None) -> IngestResult:
        self._audit_store("STORE_REJECTED", peer, uid, "failure",
                          {"status": f"0x{status:04X}", "reason": outcome, "comment": comment,
                           **(details or {})})
        return IngestResult(status, comment[:64], outcome)

    def _ingest(self, raw, ts, req_class, req_uid, peer) -> IngestResult:
        file_bytes, _offset = build_file_bytes(raw, req_class, req_uid, ts, peer.ae_title)
        # 1) 解析与校验
        try:
            ds = parse_file_bytes(file_bytes)
            missing = [kw for kw in ("SOPClassUID", "SOPInstanceUID", "StudyInstanceUID",
                                     "SeriesInstanceUID") if not str(ds.get(kw, "") or "")]
            sop_uid = str(ds.get("SOPInstanceUID", "") or "")
            sop_class = str(ds.get("SOPClassUID", "") or "")
            baseline = compute_baseline(raw, ds, file_bytes, ts)
        except Exception as exc:  # noqa: BLE001
            return self._reject(UNPARSEABLE, f"dataset not parseable: {type(exc).__name__}",
                                peer, req_uid, "unparseable", {"error": str(exc)[:200]})
        if missing:
            return self._reject(MISSING_ATTRIBUTE, "missing " + ",".join(missing), peer, req_uid,
                                "missing-attribute", {"missing": missing})
        if sop_uid != req_uid:
            return self._reject(UID_MISMATCH, "SOPInstanceUID differs from request", peer,
                                req_uid, "uid-mismatch", {"dataset_uid": sop_uid})
        if sop_class != req_class:
            return self._reject(SOP_CLASS_MISMATCH, "SOPClassUID differs from request", peer,
                                req_uid, "sop-class-mismatch", {"dataset_class": sop_class})
        snap = json.loads(baseline.meta_json)

        # 1b) 审计锚点降级期间预先拒绝（不写文件、不写索引）；降级时先尝试补写锚点以恢复
        if not self.svc.audit.ensure_anchor_available():
            self._audit_store("STORE_REJECTED", peer, req_uid, "failure",
                              {"status": f"0x{AUDIT_UNAVAILABLE:04X}",
                               "reason": "audit-anchor-unavailable",
                               "last_error": self.svc.audit.last_anchor_error})
            self._alert_safe("AUDIT_ANCHOR_FAILURE", "audit-anchor",
                             {"stage": "pre-check", "rejected_sop_uid": req_uid,
                              "source_ae": peer.ae_title,
                              "error": self.svc.audit.last_anchor_error},
                             aggregate_key="AUDIT_ANCHOR_FAILURE")
            return IngestResult(AUDIT_UNAVAILABLE, "audit anchor unavailable; not stored",
                                "anchor-unavailable")

        # 2) 重复 / 冲突
        existing = self.svc.verifier.instance_row(req_uid)
        if existing is not None:
            return self._duplicate(existing, baseline, snap, file_bytes, peer, req_uid)

        # 3) 原样落盘：tmp -> fsync -> 读回比对 -> 原子重命名
        today = datetime.now()
        rel = f"{today:%Y}/{today:%m}/{today:%d}/{uuid.uuid4().hex}.dcm"
        final = self.cfg.store_dir / rel
        tmp = self.cfg.tmp_dir / (uuid.uuid4().hex + ".tmp")
        try:
            if self.cfg.fault_injection.get("fail_write"):
                raise OSError(28, "No space left on device (fault injection)")
            fsync_write(tmp, file_bytes)
            if self.cfg.read_back_verify and sha256_hex(tmp.read_bytes()) != baseline.file_sha256:
                raise OSError("read-back verification mismatch")
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, final)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            self._audit_store("STORE_FAILED", peer, req_uid, "failure",
                              {"stage": "write", "error": str(exc)[:200]})
            self._alert_safe("STORAGE_FAILURE", f"instance:{req_uid}",
                             {"stage": "write", "error": str(exc)[:200],
                              "source_ae": peer.ae_title},
                             aggregate_key="STORAGE_FAILURE:write", window_s=300)
            return IngestResult(OUT_OF_RESOURCES, "storage write failed", "write-failed")

        # 4) 事务：层级 + 索引 + 基线 + 审计。COMMIT 是唯一的提交点：
        #    提交前任何失败 -> 删除已写文件、返回失败；提交后绝不在未撤销索引的情况下删除文件。
        try:
            if self.cfg.fault_injection.get("fail_db"):
                self.db.fail_next_commit = True
            with self.db.write() as conn:
                notes, created = self._upsert_hierarchy_and_insert(
                    conn, ds, snap, baseline, rel, len(file_bytes), ts, peer, req_uid)
                rec = self.svc.audit.append(
                    "STORE_ACCEPTED", actor=peer.ae_title, actor_ip=peer.ip,
                    target=f"instance:{req_uid}",
                    details={"assoc": peer.assoc_id, "sop_class": req_class,
                             "transfer_syntax": ts, "dataset_sha256": baseline.dataset_sha256,
                             "study_uid": str(ds.StudyInstanceUID), **notes},
                    conn=conn)
        except HierarchyConflict as exc:
            final.unlink(missing_ok=True)
            return self._reject(HIERARCHY_CONFLICT, str(exc), peer, req_uid,
                                "hierarchy-conflict")
        except Exception as exc:  # noqa: BLE001
            final.unlink(missing_ok=True)
            self._audit_store("STORE_FAILED", peer, req_uid, "failure",
                              {"stage": "db-commit", "error": str(exc)[:200]})
            self._alert_safe("STORAGE_FAILURE", f"instance:{req_uid}",
                             {"stage": "db-commit", "error": str(exc)[:200],
                              "source_ae": peer.ae_title},
                             aggregate_key="STORAGE_FAILURE:db", window_s=300)
            return IngestResult(INTERNAL_FAILURE, "database transaction failed", "db-failed")

        # 5) 已提交：写审计锚点。锚点是成功语义的一部分（0x0000 = 已持久化 + 基线 + 审计已锚定）。
        if self.svc.audit.anchor_safely(rec):
            return IngestResult(SUCCESS, "", "stored", created["instance"])
        return self._compensate_unanchored(created, final, peer, req_uid, rec)

    def _compensate_unanchored(self, created: dict, final: Path, peer: PeerInfo, uid: str,
                               rec: dict) -> IngestResult:
        """提交后锚点写入失败：按 R5“失败不留部分数据”撤销本次入库（design D-L）。

        - 在新事务中删除本次新建的基线、实例行，以及本次新建且已无子项的序列 / 检查 / 患者行，
          并写入 STORE_ROLLED_BACK 审计（暂未锚定，恢复后补写）；提交成功后才删除文件。
        - 若撤销事务本身失败：保留文件与索引（二者一致），返回失败并在告警中注明“已入库，锚点待补”。
          设备在锚点恢复后重发，会走“相同实例幂等成功”路径。
        """
        err = self.svc.audit.last_anchor_error
        inst_id = created["instance"]
        rolled_back = False
        try:
            with self.db.write() as conn:
                conn.execute("DELETE FROM baselines WHERE instance_id=?", (inst_id,))
                conn.execute("DELETE FROM instances WHERE id=?", (inst_id,))
                for table, pk, child_sql, origin_sql in (
                    ("series", created["series"], "SELECT 1 FROM instances WHERE series_pk=?",
                     "SELECT MIN(id) FROM instances WHERE series_pk=?"),
                    ("studies", created["study"], "SELECT 1 FROM series WHERE study_pk=?",
                     "SELECT MIN(i.id) FROM instances i JOIN series s ON s.id=i.series_pk"
                     " WHERE s.study_pk=?"),
                    ("patients", created["patient"], "SELECT 1 FROM studies WHERE patient_pk=?",
                     "SELECT MIN(i.id) FROM instances i JOIN series s ON s.id=i.series_pk"
                     " JOIN studies st ON st.id=s.study_pk WHERE st.patient_pk=?"),
                ):
                    if pk is None:
                        continue
                    if conn.execute(child_sql, (pk,)).fetchone() is None:
                        conn.execute(f"DELETE FROM {table} WHERE id=?", (pk,))
                    else:  # 并发入库的其他实例已挂在该行下：保留行，改由其最早实例作为 origin
                        conn.execute(f"UPDATE {table} SET origin_instance_id=({origin_sql})"
                                     " WHERE id=?", (pk, pk))
                self.svc.audit.append(
                    "STORE_ROLLED_BACK", actor=peer.ae_title, actor_ip=peer.ip,
                    target=f"instance:{uid}", outcome="failure",
                    details={"assoc": peer.assoc_id, "reason": "audit-anchor-write-failed",
                             "store_accepted_seq": rec["seq"], "anchor_error": err,
                             "status": f"0x{AUDIT_UNAVAILABLE:04X}"},
                    conn=conn)
            rolled_back = True
        except Exception:  # noqa: BLE001
            LOG.exception("compensation after anchor failure failed for %s", uid)
        if rolled_back:
            final.unlink(missing_ok=True)
        self._alert_safe("AUDIT_ANCHOR_FAILURE", "audit-anchor",
                         {"stage": "post-commit", "sop_uid": uid, "source_ae": peer.ae_title,
                          "rolled_back": rolled_back, "store_accepted_seq": rec["seq"],
                          "error": err,
                          "state": ("本次入库已撤销：无索引、无文件" if rolled_back else
                                    "撤销失败：文件与索引均保留（一致），锚点待补；恢复后重发将幂等成功")},
                         aggregate_key="AUDIT_ANCHOR_FAILURE")
        if rolled_back:
            return IngestResult(AUDIT_UNAVAILABLE, "audit anchor unavailable; not stored",
                                "anchor-failed-rolled-back")
        return IngestResult(AUDIT_UNAVAILABLE, "audit anchor unavailable; stored, retry later",
                            "anchor-failed-kept")

    def _upsert_hierarchy_and_insert(self, conn, ds, snap, baseline, rel, size, ts, peer,
                                     uid) -> tuple[dict, dict]:
        notes: dict = {}
        pid = _snap_value(snap, "00100020")
        issuer = _snap_value(snap, "00100021")
        if not pid:
            notes["note"] = "PatientID 为空，归入空 ID 患者"
        p = conn.execute("SELECT * FROM patients WHERE patient_id=? AND issuer=?",
                         (pid, issuer)).fetchone()
        new_patient = p is None
        demo = {f: _snap_value(snap, t) for f, t in _PATIENT_FIELDS.items()}
        if new_patient:
            patient_pk = conn.execute(
                "INSERT INTO patients(patient_id, issuer, patient_name, birth_date, sex)"
                " VALUES (?,?,?,?,?)",
                (pid, issuer, demo["patient_name"], demo["birth_date"], demo["sex"])).lastrowid
        else:
            patient_pk = p["id"]
            if any((p[f] or "") != demo[f] for f in demo):
                conn.execute("UPDATE patients SET demographics_conflict=1 WHERE id=?",
                             (patient_pk,))
                notes["demographics_conflict"] = {f: [p[f], demo[f]] for f in demo
                                                  if (p[f] or "") != demo[f]}
        study_uid = _snap_value(snap, "0020000D")
        st = conn.execute("SELECT * FROM studies WHERE study_uid=?", (study_uid,)).fetchone()
        new_study = st is None
        if new_study:
            sv = {f: _snap_value(snap, t) for f, t in _STUDY_FIELDS.items()}
            study_pk = conn.execute(
                "INSERT INTO studies(study_uid, patient_pk, study_date, study_time, accession,"
                " study_id, description, referring) VALUES (?,?,?,?,?,?,?,?)",
                (study_uid, patient_pk, sv["study_date"], sv["study_time"], sv["accession"],
                 sv["study_id"], sv["description"], sv["referring"])).lastrowid
        else:
            if st["patient_pk"] != patient_pk:
                raise HierarchyConflict("Study belongs to another PatientID")
            study_pk = st["id"]
        series_uid = _snap_value(snap, "0020000E")
        se = conn.execute("SELECT * FROM series WHERE series_uid=?", (series_uid,)).fetchone()
        new_series = se is None
        if new_series:
            sv = {f: _snap_value(snap, t) for f, t in _SERIES_FIELDS.items()}
            series_pk = conn.execute(
                "INSERT INTO series(series_uid, study_pk, modality, series_number, description)"
                " VALUES (?,?,?,?,?)",
                (series_uid, study_pk, sv["modality"], sv["series_number"],
                 sv["description"])).lastrowid
        else:
            if se["study_pk"] != study_pk:
                raise HierarchyConflict("Series belongs to another Study")
            series_pk = se["id"]
        charset = _snap_value(snap, "00080005")
        inst_id = conn.execute(
            "INSERT INTO instances(sop_uid, sop_class_uid, series_pk, instance_number,"
            " transfer_syntax, charset, rel_path, file_size, received_at, source_ae, source_ip,"
            " assoc_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, _snap_value(snap, "00080016"), series_pk, _snap_value(snap, "00200013"), ts,
             charset, rel, size, now_utc(), peer.ae_title, peer.ip, peer.assoc_id)).lastrowid
        if new_patient:
            conn.execute("UPDATE patients SET origin_instance_id=? WHERE id=?", (inst_id, patient_pk))
        if new_study:
            conn.execute("UPDATE studies SET origin_instance_id=? WHERE id=?", (inst_id, study_pk))
        if new_series:
            conn.execute("UPDATE series SET origin_instance_id=? WHERE id=?", (inst_id, series_pk))
        mac = baseline_mac(self.svc.key, uid, rel, baseline)
        conn.execute(
            "INSERT INTO baselines(instance_id, file_sha256, dataset_sha256, pixel_sha256,"
            " meta_sha256, meta_json, tiles_json, created_at, hmac) VALUES (?,?,?,?,?,?,?,?,?)",
            (inst_id, baseline.file_sha256, baseline.dataset_sha256, baseline.pixel_sha256,
             baseline.meta_sha256, baseline.meta_json, baseline.tiles_json, now_utc(), mac))
        created = {"instance": inst_id, "patient": patient_pk if new_patient else None,
                   "study": study_pk if new_study else None,
                   "series": series_pk if new_series else None}
        return notes, created

    # ---------- 重复 / 冲突（R7） ----------
    def _duplicate(self, row, baseline, snap, file_bytes, peer, uid) -> IngestResult:
        ok, _ = self.svc.verifier.quick_check(row)
        if not ok:
            findings = self.svc.verifier.verify_instance(row)
            qid = self.quarantine(file_bytes, "STORED_COPY_DAMAGED", uid, peer,
                                  baseline.dataset_sha256,
                                  {"categories": sorted({f["category"] for f in findings})})
            self._alert_safe(
                "STORED_COPY_DAMAGED", f"instance:{uid}",
                {"sop_uid": uid, "source_ae": peer.ae_title, "quarantine_id": qid,
                 "stored_copy_findings": sorted({f["category"] for f in findings}),
                 "incoming_matches_baseline": baseline.dataset_sha256 == row["dataset_sha256"]},
                aggregate_key=f"STORED_COPY_DAMAGED:{uid}")
            for f in findings:
                self.svc.verifier.raise_finding_alert(f)
            return self._reject(STORED_COPY_DAMAGED, "stored copy failed integrity check",
                                peer, uid, "stored-copy-damaged", {"quarantine_id": qid})
        if baseline.dataset_sha256 == row["dataset_sha256"]:
            self._audit_store("STORE_DUPLICATE_IDENTICAL", peer, uid, "success",
                              {"dataset_sha256": baseline.dataset_sha256})
            if self.svc.audit.degraded:   # 本次审计未能锚定：不报成功（无新数据，无需撤销）
                return IngestResult(AUDIT_UNAVAILABLE, "audit anchor unavailable; retry later",
                                    "duplicate-anchor-failed", row["id"])
            return IngestResult(SUCCESS, "", "duplicate-identical", row["id"])
        if baseline.pixel_sha256 == row["pixel_sha256"] and \
                baseline.meta_sha256 == row["meta_sha256"]:
            self._audit_store("STORE_DUPLICATE_EQUIVALENT", peer, uid, "success",
                              {"incoming_dataset_sha256": baseline.dataset_sha256,
                               "stored_dataset_sha256": row["dataset_sha256"],
                               "note": "编码不同（如传输语法不同），像素与元数据快照相同；保留已存副本"})
            if self.svc.audit.degraded:
                return IngestResult(AUDIT_UNAVAILABLE, "audit anchor unavailable; retry later",
                                    "duplicate-anchor-failed", row["id"])
            return IngestResult(SUCCESS, "", "duplicate-equivalent", row["id"])

        changes = diff_meta(json.loads(row["meta_json"]), snap)
        qid = self.quarantine(file_bytes, "UID_CONFLICT", uid, peer, baseline.dataset_sha256,
                              {"pixel_changed": baseline.pixel_sha256 != row["pixel_sha256"],
                               "meta_changes": changes[:20]})
        self._alert_safe(
            "UID_CONFLICT", f"instance:{uid}",
            {"sop_uid": uid, "source_ae": peer.ae_title, "quarantine_id": qid,
             "pixel_changed": baseline.pixel_sha256 != row["pixel_sha256"],
             "meta_changes": changes[:20]},
            aggregate_key=f"UID_CONFLICT:{uid}")
        return self._reject(UID_CONFLICT, "SOP Instance UID exists with different content",
                            peer, uid, "uid-conflict", {"quarantine_id": qid})

    def quarantine(self, file_bytes: bytes, reason: str, uid: str, peer: PeerInfo | None,
                   ds_sha: str, details: dict) -> int:
        name = f"{reason.lower()}-{uuid.uuid4().hex}.dcm"
        path = self.cfg.quarantine_dir / name
        fsync_write(path, file_bytes)
        with self.db.write() as conn:
            qid = conn.execute(
                "INSERT INTO quarantine(created_at, reason, sop_uid, rel_path, source_ae,"
                " source_ip, dataset_sha256, details_json) VALUES (?,?,?,?,?,?,?,?)",
                (now_utc(), reason, uid, name, peer.ae_title if peer else "",
                 peer.ip if peer else "", ds_sha, canonical_json(details))).lastrowid
        return qid

    # ---------- 启动对账（R19） ----------
    def reconcile(self) -> dict:
        cfg = self.cfg
        removed_tmp = 0
        for t in cfg.tmp_dir.glob("*.tmp"):
            t.unlink(missing_ok=True)
            removed_tmp += 1
        on_disk = {p.relative_to(cfg.store_dir).as_posix()
                   for p in cfg.store_dir.rglob("*") if p.is_file()}
        rows = self.db.query("SELECT sop_uid, rel_path FROM instances")
        indexed = {r["rel_path"]: r["sop_uid"] for r in rows}
        orphans = sorted(on_disk - set(indexed))
        missing = sorted(set(indexed) - on_disk)
        for rel in orphans:
            src = cfg.store_dir / rel
            dst_name = f"orphan-{uuid.uuid4().hex}-{Path(rel).name}"
            shutil.move(str(src), str(cfg.quarantine_dir / dst_name))
            with self.db.write() as conn:
                qid = conn.execute(
                    "INSERT INTO quarantine(created_at, reason, rel_path, details_json)"
                    " VALUES (?,?,?,?)",
                    (now_utc(), "ORPHAN_FILE", dst_name,
                     canonical_json({"original_rel_path": rel}))).lastrowid
            self.svc.alerts.raise_alert("ORPHAN_FILE", f"file:{rel}",
                                        {"original_rel_path": rel, "quarantine_id": qid},
                                        aggregate_key=f"ORPHAN_FILE:{rel}")
        for rel in missing:
            uid = indexed[rel]
            row = self.svc.verifier.instance_row(uid)
            if row is not None:
                for f in self.svc.verifier.verify_instance(row):
                    self.svc.verifier.raise_finding_alert(f)
        summary = {"orphans": orphans, "missing": [indexed[r] for r in missing],
                   "tmp_removed": removed_tmp}
        self.svc.audit.append("RECONCILE", details=summary,
                              outcome="success" if not (orphans or missing) else "warning")
        return summary
