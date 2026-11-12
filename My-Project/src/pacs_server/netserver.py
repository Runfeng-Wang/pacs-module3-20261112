"""DICOM 网络层（B 作为 SCP）：准入、表示上下文授权、ECHO/STORE/FIND/MOVE/GET 处理器。

准入实现（design D-G）：用 PacsRequestHandler 替换每个新关联的 ACSE 为 PacsACSE。
PacsACSE 在 pynetdicom 原有协商之前做白名单判定，不通过时发送真实的 A-ASSOCIATE-RJ；
通过后按角色裁剪 supported_contexts，再交给父类协商（父类负责关联上限检查 RJ 2,3,2）。
依赖 pynetdicom 3.0.4 内部方法 ACSE._negotiate_as_acceptor，版本已锁定。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid

from pydicom.dataset import Dataset
from pydicom.uid import (ExplicitVRBigEndian, ExplicitVRLittleEndian, ImplicitVRLittleEndian,
                         JPEG2000, JPEG2000Lossless, JPEGBaseline8Bit, JPEGLosslessSV1,
                         JPEGLSLossless, RLELossless)
from pynetdicom import AE, StoragePresentationContexts, build_context, evt
from pynetdicom.acse import ACSE
from pynetdicom.dimse_messages import C_GET_RSP, C_MOVE_RSP
from pynetdicom.sop_class import (PatientRootQueryRetrieveInformationModelFind,
                                  PatientRootQueryRetrieveInformationModelGet,
                                  PatientRootQueryRetrieveInformationModelMove,
                                  StudyRootQueryRetrieveInformationModelFind,
                                  StudyRootQueryRetrieveInformationModelGet,
                                  StudyRootQueryRetrieveInformationModelMove, Verification)
from pynetdicom.transport import RequestHandler, ThreadedAssociationServer

from .dicomutil import IMPLEMENTATION_CLASS_UID, IMPLEMENTATION_VERSION_NAME, parse_file_bytes
from .query import QueryError
from .store import PeerInfo

LOG = logging.getLogger("pacs.net")

# 接受方按此顺序选择传输语法（pynetdicom 以接受方顺序为准）；一律原样保存、不转码
STORAGE_TRANSFER_SYNTAXES = [
    ExplicitVRLittleEndian, ImplicitVRLittleEndian, JPEGLosslessSV1, JPEGLSLossless,
    JPEG2000Lossless, RLELossless, JPEGBaseline8Bit, JPEG2000, ExplicitVRBigEndian,
]
QR_TRANSFER_SYNTAXES = [ExplicitVRLittleEndian, ImplicitVRLittleEndian]

FIND_MODELS = {str(PatientRootQueryRetrieveInformationModelFind): "P",
               str(StudyRootQueryRetrieveInformationModelFind): "S"}
MOVE_MODELS = {str(PatientRootQueryRetrieveInformationModelMove): "P",
               str(StudyRootQueryRetrieveInformationModelMove): "S"}
GET_MODELS = {str(PatientRootQueryRetrieveInformationModelGet): "P",
              str(StudyRootQueryRetrieveInformationModelGet): "S"}
STORAGE_UIDS = {str(cx.abstract_syntax) for cx in StoragePresentationContexts}

STATUS_NOT_AUTHORIZED = 0x0124   # PS3.7 Annex C 通用失败码 Refused: Not Authorized（纵深防御）
STATUS_PENDING = 0xFF00
STATUS_CANCEL = 0xFE00


def allowed_abstract_syntaxes(peer) -> set[str]:
    allowed = {str(Verification)}
    if peer.has("STORE") or peer.has("RETRIEVE"):
        # RETRIEVE 需要存储上下文供 C-GET 子操作使用（对端以 SCP 角色接收）
        allowed |= STORAGE_UIDS
    if peer.has("QUERY"):
        allowed |= set(FIND_MODELS)
    if peer.has("RETRIEVE"):
        allowed |= set(MOVE_MODELS) | set(GET_MODELS)
    return allowed


class PacsACSE(ACSE):
    def _negotiate_as_acceptor(self) -> None:  # noqa: D401
        server: "DicomServer" = self.assoc._pacs_server
        rq = self.requestor.primitive
        calling = str(rq.calling_ae_title or "").strip()
        called = str(rq.called_ae_title or "").strip()
        ip = self.assoc.requestor.address
        decision = server.whitelist_admit(self.assoc, calling, called, ip)
        if not decision.accepted:
            self.requestor.ae_title = calling
            self.assoc._pacs_rejected = True
            LOG.info("Rejecting association from %s@%s: %s", calling, ip, decision.message)
            self.send_reject(0x01, 0x01, decision.reason)
            evt.trigger(self.assoc, evt.EVT_REJECTED, {})
            self.assoc.kill()
            return
        allowed = allowed_abstract_syntaxes(decision.peer)
        proposed = {str(cx.abstract_syntax) for cx in rq.presentation_context_definition_list}
        denied = sorted(proposed - allowed - {u for u in proposed if u not in server.supported_uids})
        self.acceptor.supported_contexts = [cx for cx in self.acceptor.supported_contexts
                                            if str(cx.abstract_syntax) in allowed]
        if denied:
            server.on_contexts_denied(self.assoc, decision.peer, ip, denied)
        super()._negotiate_as_acceptor()


class PacsRequestHandler(RequestHandler):
    def _create_association(self):
        assoc = super()._create_association()
        assoc.acse = PacsACSE(assoc)
        assoc._pacs_server = self.server._pacs_server
        assoc._pacs_rejected = False
        assoc._pacs_peer = None
        assoc._pacs_id = uuid.uuid4().hex[:12]
        return assoc


class DicomServer:
    def __init__(self, svc):
        self.svc = svc
        cfg = svc.config
        ae = AE(ae_title=cfg.ae_title)
        ae.maximum_associations = cfg.max_associations
        ae.acse_timeout = cfg.acse_timeout
        ae.dimse_timeout = cfg.dimse_timeout
        ae.network_timeout = cfg.network_timeout
        ae.maximum_pdu_size = cfg.max_pdu
        ae.implementation_class_uid = IMPLEMENTATION_CLASS_UID
        ae.implementation_version_name = IMPLEMENTATION_VERSION_NAME
        ae.require_called_aet = False   # 被叫 AE 由 PacsACSE 自己检查并返回 RJ(1,1,7)
        ae.add_supported_context(Verification, QR_TRANSFER_SYNTAXES)
        for cx in StoragePresentationContexts:
            ae.add_supported_context(cx.abstract_syntax, STORAGE_TRANSFER_SYNTAXES,
                                     scu_role=True, scp_role=True)
        for uid in list(FIND_MODELS) + list(MOVE_MODELS) + list(GET_MODELS):
            ae.add_supported_context(uid, QR_TRANSFER_SYNTAXES)
        self.supported_uids = {str(cx.abstract_syntax) for cx in ae.supported_contexts}
        self.ae = ae
        self.server = None
        self._thread = None

    # ---------- 启停 ----------
    def start(self) -> None:
        cfg = self.svc.config
        handlers = [
            (evt.EVT_C_ECHO, self.handle_echo), (evt.EVT_C_STORE, self.handle_store),
            (evt.EVT_C_FIND, self.handle_find), (evt.EVT_C_MOVE, self.handle_move),
            (evt.EVT_C_GET, self.handle_get), (evt.EVT_ACCEPTED, self.on_accepted),
            (evt.EVT_RELEASED, self.on_released), (evt.EVT_ABORTED, self.on_aborted),
            (evt.EVT_REJECTED, self.on_rejected), (evt.EVT_DIMSE_SENT, self.on_dimse_sent),
        ]
        server = self.ae.make_server((cfg.host, cfg.port), evt_handlers=handlers,
                                     server_class=ThreadedAssociationServer,
                                     request_handler=PacsRequestHandler)
        server._pacs_server = self
        self.server = server
        self._thread = threading.Thread(target=server.serve_forever, name="PacsAcceptor",
                                        daemon=True)
        self._thread.start()
        self.ae._servers.append(server)
        LOG.info("DICOM SCP %s listening on %s:%s", cfg.ae_title, cfg.host, cfg.port)

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server = None
        self.ae.shutdown()

    def active_associations(self) -> int:
        return len([a for a in self.ae.active_associations if a.is_acceptor])

    # ---------- 准入 ----------
    def whitelist_admit(self, assoc, calling, called, ip):
        svc = self.svc
        d = svc.whitelist.admit(calling, called, ip, svc.config.ae_title)
        if d.accepted:
            assoc._pacs_peer = d.peer
            return d
        svc.audit.append("ASSOCIATION_REJECTED", actor=calling, actor_ip=ip,
                         target=f"called:{called}", outcome="failure",
                         details={"result": 1, "source": 1, "reason": d.reason,
                                  "alert_type": d.alert_type, "message": d.message,
                                  "assoc": assoc._pacs_id})
        svc.alerts.raise_alert(d.alert_type, f"peer:{calling}@{ip}",
                               {"calling_ae": calling, "called_ae": called, "ip": ip,
                                "reason": d.message},
                               aggregate_key=f"{d.alert_type}:{calling}:{ip}",
                               window_s=svc.config.alert_aggregate_window_s)
        return d

    def on_contexts_denied(self, assoc, peer, ip, denied: list[str]):
        svc = self.svc
        svc.audit.append("CONTEXT_DENIED", actor=peer.ae_title, actor_ip=ip,
                         target=f"assoc:{assoc._pacs_id}", outcome="failure",
                         details={"denied_abstract_syntaxes": denied, "roles": peer.roles})
        retrieve = [u for u in denied if u in MOVE_MODELS or u in GET_MODELS]
        if retrieve:
            svc.alerts.raise_alert("UNAUTHORIZED_RETRIEVE", f"peer:{peer.ae_title}@{ip}",
                                   {"peer": peer.ae_title, "ip": ip, "roles": peer.roles,
                                    "denied": retrieve, "stage": "association-negotiation"},
                                   aggregate_key=f"UNAUTHORIZED_RETRIEVE:{peer.ae_title}",
                                   window_s=svc.config.alert_aggregate_window_s)

    # ---------- 关联事件 ----------
    def _peer_info(self, assoc) -> PeerInfo:
        return PeerInfo(ae_title=str(assoc.requestor.ae_title).strip(),
                        ip=assoc.requestor.address, assoc_id=getattr(assoc, "_pacs_id", ""))

    def on_accepted(self, event):
        p = self._peer_info(event.assoc)
        cxs = sorted({str(cx.abstract_syntax.name) for cx in event.assoc.accepted_contexts})
        self.svc.audit.append("ASSOCIATION_ACCEPTED", actor=p.ae_title, actor_ip=p.ip,
                              target=f"assoc:{p.assoc_id}",
                              details={"accepted_contexts": len(event.assoc.accepted_contexts),
                                       "rejected_contexts": len(event.assoc.rejected_contexts),
                                       "abstract_syntaxes": cxs[:20]})

    def on_released(self, event):
        p = self._peer_info(event.assoc)
        self.svc.audit.append("ASSOCIATION_RELEASED", actor=p.ae_title, actor_ip=p.ip,
                              target=f"assoc:{p.assoc_id}")

    def on_aborted(self, event):
        # 课程原文：设备断连重连不算事故 -> 只审计，不告警
        p = self._peer_info(event.assoc)
        self.svc.audit.append("ASSOCIATION_ABORTED", actor=p.ae_title, actor_ip=p.ip,
                              target=f"assoc:{p.assoc_id}", outcome="info",
                              details={"note": "传输中断不算事故；未完成的实例不入库"})

    def on_rejected(self, event):
        assoc = event.assoc
        if getattr(assoc, "_pacs_rejected", False):
            return  # 白名单拒绝已在 whitelist_admit 中审计
        p = self._peer_info(assoc)
        self.svc.audit.append("ASSOCIATION_REJECTED", actor=p.ae_title, actor_ip=p.ip,
                              target=f"assoc:{p.assoc_id}", outcome="failure",
                              details={"result": 2, "source": 3, "reason": 2,
                                       "message": "local-limit-exceeded（关联数达到上限）"})

    def on_dimse_sent(self, event):
        msg = event.message
        if not isinstance(msg, (C_MOVE_RSP, C_GET_RSP)):
            return
        cs = msg.command_set
        status = int(cs.Status)
        if status in (STATUS_PENDING, 0xFF01):
            return
        p = self._peer_info(event.assoc)
        failed = []
        ds = getattr(msg, "data_set", None)
        if ds is not None:
            try:
                from pynetdicom.dsutils import decode
                ctx = next((c for c in event.assoc.accepted_contexts
                            if c.context_id == msg.context_id), None)
                implicit = ctx.transfer_syntax[0].is_implicit_VR if ctx else True
                ident = decode(ds, implicit, True)
                val = ident.get("FailedSOPInstanceUIDList", None)
                if val is None:
                    failed = []
                elif isinstance(val, str):
                    failed = [val] if val else []
                else:
                    failed = [str(u) for u in val]
            except Exception:  # noqa: BLE001
                failed = ["<undecodable>"]
        kind = "DICOM_MOVE_RESULT" if isinstance(msg, C_MOVE_RSP) else "DICOM_GET_RESULT"
        self.svc.audit.append(kind, actor=p.ae_title, actor_ip=p.ip,
                              target=f"assoc:{p.assoc_id}",
                              outcome="success" if status == 0 else "warning",
                              details={"status": f"0x{status:04X}",
                                       "completed": int(cs.get("NumberOfCompletedSuboperations", 0) or 0),
                                       "failed": int(cs.get("NumberOfFailedSuboperations", 0) or 0),
                                       "warning": int(cs.get("NumberOfWarningSuboperations", 0) or 0),
                                       "remaining": int(cs.get("NumberOfRemainingSuboperations", 0) or 0),
                                       "failed_uids": failed})

    # ---------- 处理器 ----------
    def _peer(self, event):
        return getattr(event.assoc, "_pacs_peer", None)

    def handle_echo(self, event):
        p = self._peer_info(event.assoc)
        self.svc.audit.append("DICOM_ECHO", actor=p.ae_title, actor_ip=p.ip,
                              target=f"assoc:{p.assoc_id}")
        return 0x0000

    def handle_store(self, event):
        peer = self._peer(event)
        p = self._peer_info(event.assoc)
        req = event.request
        status = Dataset()
        if peer is None or not peer.has("STORE"):
            self.svc.audit.append("STORE_REJECTED", actor=p.ae_title, actor_ip=p.ip,
                                  target=f"instance:{req.AffectedSOPInstanceUID}",
                                  outcome="failure", details={"reason": "not-authorized"})
            status.Status = STATUS_NOT_AUTHORIZED
            status.ErrorComment = "peer has no STORE role"
            return status
        raw = event.encoded_dataset(include_meta=False)
        ts = str(event.context.transfer_syntax)
        try:
            res = self.svc.store.ingest(raw, ts, str(req.AffectedSOPClassUID),
                                        str(req.AffectedSOPInstanceUID), p)
        except Exception:  # noqa: BLE001  安全网：入库流水线已保证提交前失败不留数据
            LOG.exception("unexpected ingest failure for %s", req.AffectedSOPInstanceUID)
            from .store import INTERNAL_FAILURE
            status.Status = INTERNAL_FAILURE
            status.ErrorComment = "internal processing failure"
            return status
        status.Status = res.status
        if res.comment:
            status.ErrorComment = res.comment[:64]
        return status

    def _delay(self):
        d = self.svc.config.test_pending_delay_s
        if d:
            time.sleep(d)

    def handle_find(self, event):
        peer = self._peer(event)
        p = self._peer_info(event.assoc)
        model = FIND_MODELS.get(str(event.request.AffectedSOPClassUID), "S")
        identifier = event.identifier
        keys = {e.keyword: ("" if e.value is None else str(e.value)) for e in identifier
                if e.keyword}
        if peer is None or not peer.has("QUERY"):
            self.svc.audit.append("DICOM_FIND", actor=p.ae_title, actor_ip=p.ip,
                                  outcome="failure", details={"reason": "not-authorized"})
            yield STATUS_NOT_AUTHORIZED, None
            return
        try:
            matches = list(self.svc.query.find(model, identifier))
        except QueryError as exc:
            self.svc.audit.append("DICOM_FIND", actor=p.ae_title, actor_ip=p.ip,
                                  outcome="failure",
                                  details={"model": model, "keys": keys, "status":
                                           f"0x{exc.status:04X}", "error": exc.message})
            yield exc.status, None
            return
        sent = 0
        for ds in matches:
            if event.is_cancelled:
                self.svc.audit.append("DICOM_FIND", actor=p.ae_title, actor_ip=p.ip,
                                      outcome="cancelled",
                                      details={"model": model, "keys": keys, "sent": sent,
                                               "matches": len(matches)})
                yield STATUS_CANCEL, None
                return
            yield STATUS_PENDING, ds
            sent += 1
            self._delay()
        self.svc.audit.append("DICOM_FIND", actor=p.ae_title, actor_ip=p.ip,
                              details={"model": model, "keys": keys, "matches": len(matches)})

    def _retrieve_rows(self, model, identifier, p, kind):
        try:
            return self.svc.query.resolve_retrieve(model, identifier), None
        except QueryError as exc:
            self.svc.audit.append(kind, actor=p.ae_title, actor_ip=p.ip, outcome="failure",
                                  details={"status": f"0x{exc.status:04X}", "error": exc.message})
            return None, exc

    def _contexts_for(self, rows):
        out, seen = [], set()
        for r in rows:
            uid = r["sop_class_uid"]
            if uid in seen:
                continue
            seen.add(uid)
            tss = []
            for t in (r["transfer_syntax"], str(ExplicitVRLittleEndian), str(ImplicitVRLittleEndian)):
                if t not in tss:
                    tss.append(t)
            out.append(build_context(uid, tss))
        return out[:128]

    def handle_move(self, event):
        svc = self.svc
        peer = self._peer(event)
        p = self._peer_info(event.assoc)
        model = MOVE_MODELS.get(str(event.request.AffectedSOPClassUID), "S")
        dest_ae = str(event.move_destination or "").strip()
        if peer is None or not peer.has("RETRIEVE"):
            svc.alerts.raise_alert("UNAUTHORIZED_RETRIEVE", f"peer:{p.ae_title}@{p.ip}",
                                   {"peer": p.ae_title, "stage": "handler", "destination": dest_ae},
                                   aggregate_key=f"UNAUTHORIZED_RETRIEVE:{p.ae_title}",
                                   window_s=svc.config.alert_aggregate_window_s)
            yield None, None
            return
        dest, why = svc.whitelist.move_destination(dest_ae)
        if dest is None:
            svc.audit.append("DICOM_MOVE", actor=p.ae_title, actor_ip=p.ip,
                             target=f"dest:{dest_ae}", outcome="failure",
                             details={"status": "0xA801", "reason": why,
                                      "note": "未发起任何出站关联"})
            svc.alerts.raise_alert("UNKNOWN_MOVE_DESTINATION", f"dest:{dest_ae}",
                                   {"requester": p.ae_title, "ip": p.ip, "destination": dest_ae,
                                    "reason": why},
                                   aggregate_key=f"UNKNOWN_MOVE_DESTINATION:{p.ae_title}:{dest_ae}",
                                   window_s=svc.config.alert_aggregate_window_s)
            yield None, None
            return
        rows, err = self._retrieve_rows(model, event.identifier, p, "DICOM_MOVE")
        if err is not None:
            # 目的地已登记：pynetdicom 会先与之建立关联，然后返回 A900
            yield dest.host, dest.port
            yield 1
            yield err.status, None
            return
        uids = [r["sop_uid"] for r in rows]
        svc.audit.append("DICOM_MOVE", actor=p.ae_title, actor_ip=p.ip, target=f"dest:{dest_ae}",
                         details={"destination": f"{dest.ae_title}@{dest.host}:{dest.port}",
                                  "matches": len(rows), "instances": uids})
        yield dest.host, dest.port, {"contexts": self._contexts_for(rows)}
        yield len(rows)
        for r in rows:
            if event.is_cancelled:
                yield STATUS_CANCEL, None
                return
            yield STATUS_PENDING, svc.retrieval_dataset(r, requester=p.ae_title, op="C-MOVE")

    def handle_get(self, event):
        svc = self.svc
        peer = self._peer(event)
        p = self._peer_info(event.assoc)
        model = GET_MODELS.get(str(event.request.AffectedSOPClassUID), "S")
        if peer is None or not peer.has("RETRIEVE"):
            yield 1
            yield STATUS_NOT_AUTHORIZED, None
            return
        rows, err = self._retrieve_rows(model, event.identifier, p, "DICOM_GET")
        if err is not None:
            yield 1
            yield err.status, None
            return
        uids = [r["sop_uid"] for r in rows]
        svc.audit.append("DICOM_GET", actor=p.ae_title, actor_ip=p.ip,
                         target=f"assoc:{p.assoc_id}",
                         details={"matches": len(rows), "instances": uids})
        yield len(rows)
        for r in rows:
            if event.is_cancelled:
                yield STATUS_CANCEL, None
                return
            yield STATUS_PENDING, svc.retrieval_dataset(r, requester=p.ae_title, op="C-GET")
            self._delay()
