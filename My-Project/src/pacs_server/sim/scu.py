"""本机回环模拟对端（真实 DICOM over TCP，基于 pynetdicom）。

- ModalitySim：影像设备（CT / 内镜）的存储 SCU
- WorkstationSim：阅片工作站，带存储 SCP（作为 C-MOVE 目的地），并提供 FIND / MOVE / GET / ECHO SCU
注意：两端都使用 pynetdicom，只能证明“符合本库对标准的实现”，不等同于跨实现互操作或跨组联调。
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian
from pynetdicom import AE, StoragePresentationContexts, _config, build_context, build_role, evt
from pynetdicom.dimse_messages import C_STORE_RQ
from pynetdicom.dimse_primitives import C_STORE
from pynetdicom.dsutils import encode
from pynetdicom.sop_class import (StudyRootQueryRetrieveInformationModelFind,
                                  StudyRootQueryRetrieveInformationModelGet,
                                  StudyRootQueryRetrieveInformationModelMove,
                                  PatientRootQueryRetrieveInformationModelFind,
                                  PatientRootQueryRetrieveInformationModelGet,
                                  PatientRootQueryRetrieveInformationModelMove, Verification)

# 以文件路径发送时，按原样发送文件中 File Meta 之后的数据集字节（不解码再编码）
_config.STORE_SEND_CHUNKED_DATASET = True

FIND = {"S": StudyRootQueryRetrieveInformationModelFind,
        "P": PatientRootQueryRetrieveInformationModelFind}
MOVE = {"S": StudyRootQueryRetrieveInformationModelMove,
        "P": PatientRootQueryRetrieveInformationModelMove}
GET = {"S": StudyRootQueryRetrieveInformationModelGet,
       "P": PatientRootQueryRetrieveInformationModelGet}

DEFAULT_TS = [ExplicitVRLittleEndian, ImplicitVRLittleEndian]


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_dataset_bytes(path: Path) -> bytes:
    """文件中 File Meta 之后的数据集字节（发送端视角的“原样字节”）。"""
    from pynetdicom.dsutils import split_dataset
    _, offset = split_dataset(Path(path))
    return Path(path).read_bytes()[offset:]


@dataclass
class AssocResult:
    established: bool
    rejected: bool = False
    result: int | None = None
    source: int | None = None
    reason: int | None = None
    statuses: list = field(default_factory=list)
    accepted_abstract: list = field(default_factory=list)
    rejected_abstract: list = field(default_factory=list)


def _new_ae(ae_title: str) -> AE:
    ae = AE(ae_title=ae_title)
    ae.acse_timeout = 30
    ae.dimse_timeout = 60
    ae.network_timeout = 60
    return ae


def _assoc_result(assoc) -> AssocResult:
    res = AssocResult(established=assoc.is_established, rejected=assoc.is_rejected)
    if assoc.is_rejected and assoc.acceptor.primitive is not None:
        prim = assoc.acceptor.primitive
        res.result, res.source, res.reason = prim.result, prim.result_source, prim.diagnostic
    if assoc.is_established:
        res.accepted_abstract = sorted({str(c.abstract_syntax) for c in assoc.accepted_contexts})
        res.rejected_abstract = sorted({str(c.abstract_syntax) for c in assoc.rejected_contexts})
    return res


class ModalitySim:
    def __init__(self, ae_title: str, called_ae: str = "PACS_B"):
        self.ae_title = ae_title
        self.called_ae = called_ae

    def _ae_for(self, sop_classes, transfer_syntaxes=None) -> AE:
        ae = _new_ae(self.ae_title)
        # 每种传输语法单独一个表示上下文，便于按文件原有语法原样发送
        for uid in sorted(set(sop_classes)):
            for ts in transfer_syntaxes or DEFAULT_TS:
                ae.add_requested_context(uid, [ts])
        return ae

    def echo(self, host: str, port: int, called_ae: str | None = None) -> AssocResult:
        ae = _new_ae(self.ae_title)
        ae.add_requested_context(Verification)
        assoc = ae.associate(host, port, ae_title=called_ae or self.called_ae)
        res = _assoc_result(assoc)
        if assoc.is_established:
            st = assoc.send_c_echo()
            res.statuses.append(int(st.Status) if "Status" in st else None)
            assoc.release()
        return res

    def send(self, items: list, host: str, port: int, called_ae: str | None = None,
             transfer_syntaxes=None, extra_contexts: list | None = None) -> AssocResult:
        """items：文件路径（按原样字节发送）或 Dataset（由 pynetdicom 编码）。

        statuses 中每项为 (status, error_comment)。
        """
        classes = []
        for it in items:
            if isinstance(it, Dataset):
                classes.append(str(it.SOPClassUID))
            else:
                classes.append(str(dcmread(it, stop_before_pixels=True).file_meta.MediaStorageSOPClassUID))
        ae = self._ae_for(classes, transfer_syntaxes)
        for cx in extra_contexts or []:
            ae.add_requested_context(*cx) if isinstance(cx, tuple) else ae.add_requested_context(cx)
        assoc = ae.associate(host, port, ae_title=called_ae or self.called_ae)
        res = _assoc_result(assoc)
        if not assoc.is_established:
            return res
        for it in items:
            try:
                st = assoc.send_c_store(it if isinstance(it, Dataset) else Path(it))
                status = int(st.Status) if "Status" in st else None
                comment = str(st.get("ErrorComment", "") or "")
            except Exception as exc:  # noqa: BLE001
                status, comment = None, f"{type(exc).__name__}: {exc}"
            res.statuses.append((status, comment))
        assoc.release()
        return res

    def send_then_abort(self, ds: Dataset, host: str, port: int, pdus_before_abort: int = 3,
                        max_pdu: int = 4096) -> AssocResult:
        """发送 C-STORE 的一部分 P-DATA 后主动 A-ABORT（可重复的传输中断注入）。"""
        ae = self._ae_for([str(ds.SOPClassUID)])
        ae.maximum_pdu_size = max_pdu
        assoc = ae.associate(host, port, ae_title=self.called_ae, max_pdu=max_pdu)
        res = _assoc_result(assoc)
        if not assoc.is_established:
            return res
        cx = next(c for c in assoc.accepted_contexts if c.abstract_syntax == ds.SOPClassUID)
        tsyn = cx.transfer_syntax[0]
        req = C_STORE()
        req.MessageID = 1
        req.AffectedSOPClassUID = ds.SOPClassUID
        req.AffectedSOPInstanceUID = ds.SOPInstanceUID
        req.Priority = 2
        req.DataSet = BytesIO(encode(ds, tsyn.is_implicit_VR, tsyn.is_little_endian))
        msg = C_STORE_RQ()
        msg.primitive_to_message(req)
        peer_max = assoc.acceptor.maximum_length or max_pdu
        sent = 0
        for pdata in msg.encode_msg(cx.context_id, min(max_pdu, peer_max)):
            if sent >= pdus_before_abort:
                break
            assoc.dul.send_pdu(pdata)
            sent += 1
        time.sleep(0.3)
        assoc.abort()
        res.statuses.append(("aborted-after-pdus", sent))
        return res


@dataclass
class Received:
    sop_uid: str
    sop_class: str
    transfer_syntax: str
    dataset_sha256: str
    pixel_sha256: str


class WorkstationSim:
    """阅片工作站模拟：存储 SCP（C-MOVE 目的地）+ Q/R SCU。"""

    def __init__(self, ae_title: str, port: int, called_ae: str = "PACS_B",
                 host: str = "127.0.0.1", storage_ts=None):
        self.ae_title = ae_title
        self.port = port
        self.host = host
        self.called_ae = called_ae
        self.storage_ts = storage_ts or DEFAULT_TS
        self.received: dict[str, Received] = {}
        self._lock = threading.Lock()
        self._server = None

    # ---------- 存储 SCP ----------
    def _on_store(self, event):
        raw = event.encoded_dataset(include_meta=False)
        ds = event.dataset
        px = bytes(ds.PixelData) if "PixelData" in ds else b""
        rec = Received(str(ds.SOPInstanceUID), str(ds.SOPClassUID),
                       str(event.context.transfer_syntax), sha256(raw), sha256(px) if px else "")
        with self._lock:
            self.received[rec.sop_uid] = rec
        return 0x0000

    def start_scp(self):
        ae = _new_ae(self.ae_title)
        for cx in StoragePresentationContexts:
            ae.add_supported_context(cx.abstract_syntax, self.storage_ts)
        ae.add_supported_context(Verification)
        self._server = ae.start_server((self.host, self.port), block=False,
                                       evt_handlers=[(evt.EVT_C_STORE, self._on_store)])
        self._scp_ae = ae
        return self

    def stop_scp(self):
        if self._server:
            self._server.shutdown()
            self._scp_ae.shutdown()
            self._server = None

    def clear(self):
        with self._lock:
            self.received.clear()

    # ---------- SCU ----------
    def _assoc(self, contexts, host, port, ext_neg=None, handlers=None, called_ae=None):
        ae = _new_ae(self.ae_title)
        for c in contexts:
            if isinstance(c, tuple):
                ae.add_requested_context(*c)
            else:
                ae.add_requested_context(c)
        return ae.associate(host, port, ae_title=called_ae or self.called_ae,
                            ext_neg=ext_neg, evt_handlers=handlers)

    def echo(self, host, port, called_ae=None) -> AssocResult:
        assoc = self._assoc([Verification], host, port, called_ae=called_ae)
        res = _assoc_result(assoc)
        if assoc.is_established:
            st = assoc.send_c_echo()
            res.statuses.append(int(st.Status))
            assoc.release()
        return res

    def find(self, host, port, identifier: Dataset, model: str = "S",
             cancel_after: int | None = None) -> tuple[AssocResult, list[Dataset], int | None]:
        assoc = self._assoc([FIND[model]], host, port)
        res = _assoc_result(assoc)
        matches, final = [], None
        if not assoc.is_established:
            return res, matches, final
        responses = assoc.send_c_find(identifier, FIND[model], msg_id=1)
        for status, ident in responses:
            if status is None:
                break
            code = int(status.Status)
            if code in (0xFF00, 0xFF01):
                matches.append(ident)
                if cancel_after is not None and len(matches) == cancel_after:
                    cx_id = next(c.context_id for c in assoc.accepted_contexts
                                 if c.abstract_syntax == FIND[model])
                    assoc.send_c_cancel(1, cx_id)
            else:
                final = code
        assoc.release()
        return res, matches, final

    def move(self, host, port, identifier: Dataset, dest_ae: str, model: str = "S"):
        assoc = self._assoc([MOVE[model]], host, port)
        res = _assoc_result(assoc)
        out = {"final": None, "completed": None, "failed": None, "warning": None,
               "failed_uids": [], "pending": 0}
        if not assoc.is_established:
            return res, out
        for status, ident in assoc.send_c_move(identifier, dest_ae, MOVE[model]):
            if status is None:
                break
            code = int(status.Status)
            if code == 0xFF00:
                out["pending"] += 1
                continue
            out["final"] = code
            out["completed"] = status.get("NumberOfCompletedSuboperations")
            out["failed"] = status.get("NumberOfFailedSuboperations")
            out["warning"] = status.get("NumberOfWarningSuboperations")
            if ident is not None and "FailedSOPInstanceUIDList" in ident:
                v = ident.FailedSOPInstanceUIDList
                out["failed_uids"] = [v] if isinstance(v, str) else [str(x) for x in v]
        assoc.release()
        return res, out

    def get(self, host, port, identifier: Dataset, sop_classes: list[str], model: str = "S",
            role_for: list[str] | None = None, transfer_syntaxes=None,
            cancel_after: int | None = None):
        """C-GET。role_for：为哪些存储类协商 SCP 角色（默认全部 sop_classes）。"""
        role_for = sop_classes if role_for is None else role_for
        tss = transfer_syntaxes or self.storage_ts
        contexts = [GET[model]] + [(uid, tss) for uid in sorted(set(sop_classes))]
        ext = [build_role(uid, scp_role=True) for uid in sorted(set(role_for))]
        received_here: dict[str, Received] = {}

        def on_store(event):
            raw = event.encoded_dataset(include_meta=False)
            ds = event.dataset
            px = bytes(ds.PixelData) if "PixelData" in ds else b""
            received_here[str(ds.SOPInstanceUID)] = Received(
                str(ds.SOPInstanceUID), str(ds.SOPClassUID), str(event.context.transfer_syntax),
                sha256(raw), sha256(px) if px else "")
            return 0x0000

        assoc = self._assoc(contexts, host, port, ext_neg=ext,
                            handlers=[(evt.EVT_C_STORE, on_store)])
        res = _assoc_result(assoc)
        out = {"final": None, "completed": None, "failed": None, "warning": None,
               "failed_uids": [], "received": received_here, "pending": 0}
        if not assoc.is_established:
            return res, out
        cx_id = next(c.context_id for c in assoc.accepted_contexts
                     if c.abstract_syntax == GET[model])
        for status, ident in assoc.send_c_get(identifier, GET[model], msg_id=7):
            if status is None:
                break
            code = int(status.Status)
            if code == 0xFF00:
                out["pending"] += 1
                if cancel_after is not None and out["pending"] == cancel_after:
                    assoc.send_c_cancel(7, cx_id)
                continue
            out["final"] = code
            out["completed"] = status.get("NumberOfCompletedSuboperations")
            out["failed"] = status.get("NumberOfFailedSuboperations")
            out["warning"] = status.get("NumberOfWarningSuboperations")
            if ident is not None and "FailedSOPInstanceUIDList" in ident:
                v = ident.FailedSOPInstanceUIDList
                out["failed_uids"] = [v] if isinstance(v, str) else [str(x) for x in v]
        assoc.release()
        return res, out


def identifier(level: str, **keys) -> Dataset:
    ds = Dataset()
    ds.QueryRetrieveLevel = level
    for k, v in keys.items():
        setattr(ds, k, v)
    return ds


def propose_only(ae_title: str, host: str, port: int, contexts: list, called_ae="PACS_B"):
    """只协商、不发送：用于检查表示上下文的接受/拒绝结果。"""
    ae = _new_ae(ae_title)
    for c in contexts:
        if isinstance(c, tuple):
            ae.add_requested_context(*c)
        else:
            ae.add_requested_context(c)
    assoc = ae.associate(host, port, ae_title=called_ae)
    res = _assoc_result(assoc)
    if assoc.is_established:
        res.rejected_abstract = sorted({str(c.abstract_syntax) for c in assoc.rejected_contexts})
        assoc.release()
    return res
