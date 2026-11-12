"""R5–R8、R13、R19：接收入库、成功语义、重复冲突、组织、断连、重启与对账（真实 TCP，E2）。"""

import hashlib
import json
from pathlib import Path

from pydicom import dcmread
from pydicom.uid import ImplicitVRLittleEndian, generate_uid
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind  # noqa: F401

from conftest import store_all
from pacs_server.dicomutil import IMPLEMENTATION_CLASS_UID
from pacs_server.sim import synth
from pacs_server.sim.scu import ModalitySim, WorkstationSim, file_dataset_bytes, identifier


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def n_instances(svc) -> int:
    return svc.db.scalar("SELECT COUNT(*) FROM instances")


def store_files(svc):
    return [p for p in svc.config.store_dir.rglob("*") if p.is_file()]


def test_r5_p1_stored_bytes_match_sender(pacs, ct_study, actual):
    """TC-R5-P1 | 目标: 成功入库且数据集字节与发送端一致 | 输入: CT_SIM_1 以 Explicit VR LE 发送 1 张合成 CT（按文件原样字节） | 期望: 0x0000；库中 1 条；dataset_sha256 = 发送端文件 File Meta 之后字节的 SHA-256；库内 File Meta 的 ImplementationClassUID 是 B 的"""
    path = ct_study[2][0]
    statuses = store_all(pacs, "CT_SIM_1", [path])
    row = pacs.svc.verifier.instance_row(ct_study[1][0].SOPInstanceUID)
    sender_sha = sha(file_dataset_bytes(path))
    stored = dcmread(pacs.svc.config.store_dir / row["rel_path"])
    actual(f"status={[hex(s) for s in statuses]} instances={n_instances(pacs.svc)}")
    actual(f"sender_dataset_sha256={sender_sha[:16]}… stored dataset_sha256={row['dataset_sha256'][:16]}…")
    actual(f"stored ImplementationClassUID={stored.file_meta.ImplementationClassUID}；sender={dcmread(path).file_meta.ImplementationClassUID}")
    assert statuses == [0x0000] and n_instances(pacs.svc) == 1
    assert row["dataset_sha256"] == sender_sha
    assert stored.file_meta.ImplementationClassUID == IMPLEMENTATION_CLASS_UID
    assert stored.file_meta.ImplementationClassUID != dcmread(path).file_meta.ImplementationClassUID
    assert stored.file_meta.SourceApplicationEntityTitle.strip() == "CT_SIM_1"


def test_r5_n1_write_failure_a700(pacs, ct_study, actual):
    """TC-R5-N1 | 目标: 落盘失败返回 0xA700 | 输入: 故障注入 fail_write，CT_SIM_1 发送 1 张 CT | 期望: 0xA700，库中无记录，存储目录与 tmp 无残留，告警 STORAGE_FAILURE"""
    pacs.svc.config.fault_injection["fail_write"] = True
    statuses = store_all(pacs, "CT_SIM_1", ct_study[2][:1])
    tmp_left = list(pacs.svc.config.tmp_dir.glob("*"))
    actual(f"status={[hex(s) for s in statuses]} instances={n_instances(pacs.svc)} store_files={len(store_files(pacs.svc))} tmp={len(tmp_left)}")
    assert statuses == [0xA700]
    assert n_instances(pacs.svc) == 0 and not store_files(pacs.svc) and not tmp_left
    assert pacs.svc.alerts.count("open", "STORAGE_FAILURE") == 1


def test_r5_n2_db_failure_c0ff(pacs, ct_study, actual):
    """TC-R5-N2 | 目标: 事务失败返回 0xC0FF 并清理文件 | 输入: 故障注入 fail_db，CT_SIM_1 发送 1 张 CT | 期望: 0xC0FF，库中无记录，已写文件被删除"""
    pacs.svc.config.fault_injection["fail_db"] = True
    statuses = store_all(pacs, "CT_SIM_1", ct_study[2][:1])
    actual(f"status={[hex(s) for s in statuses]} instances={n_instances(pacs.svc)} store_files={len(store_files(pacs.svc))}")
    assert statuses == [0xC0FF]
    assert n_instances(pacs.svc) == 0 and not store_files(pacs.svc)
    pacs.svc.config.fault_injection.clear()
    assert store_all(pacs, "CT_SIM_1", ct_study[2][:1]) == [0x0000]
    actual("故障解除后重发 0x0000")


def test_r6_p1_complete_dataset(pacs, ct_study, actual):
    """TC-R6-P1 | 目标: 完整数据集入库 | 输入: CT_SIM_1 发送 5 张属性完整的合成 CT | 期望: 全部 0x0000"""
    statuses = store_all(pacs, "CT_SIM_1", ct_study[2])
    actual(f"status={[hex(s) for s in statuses]}")
    assert statuses == [0x0000] * 5


def test_r6_n1_missing_series_uid(pacs, ct_study, actual):
    """TC-R6-N1 | 目标: 缺少 SeriesInstanceUID 被拒 | 输入: 删除 SeriesInstanceUID 的数据集 | 期望: 0xC001，ErrorComment 含 SeriesInstanceUID，不入库"""
    ds = ct_study[1][0].copy()
    del ds.SeriesInstanceUID
    res = ModalitySim("CT_SIM_1").send([ds], pacs.host, pacs.port)
    status, comment = res.statuses[0]
    actual(f"status=0x{status:04X} comment={comment!r} instances={n_instances(pacs.svc)}")
    assert status == 0xC001 and "SeriesInstanceUID" in comment
    assert n_instances(pacs.svc) == 0


def test_r6_n2_uid_mismatch(pacs, ct_study, tmp_path, actual):
    """TC-R6-N2 | 目标: SOP Instance UID 与请求不一致被拒 | 输入: C-STORE 请求的 Affected SOP Instance UID 与数据集 SOPInstanceUID 不同 | 期望: 0xC002，不入库"""
    from pacs_server.dicomutil import build_file_bytes
    src = ct_study[2][0]
    meta = dcmread(src, stop_before_pixels=True).file_meta
    # File Meta 中的 MediaStorageSOPInstanceUID 即分块发送时的 Affected SOP Instance UID
    data, _ = build_file_bytes(file_dataset_bytes(src), str(meta.MediaStorageSOPClassUID),
                               generate_uid(), str(meta.TransferSyntaxUID), "CT_SIM_1")
    p = tmp_path / "mismatch.dcm"
    p.write_bytes(data)
    res = ModalitySim("CT_SIM_1").send([p], pacs.host, pacs.port)
    status, comment = res.statuses[0]
    actual(f"status=0x{status:04X} comment={comment!r}")
    assert status == 0xC002 and n_instances(pacs.svc) == 0


def test_r6_n3_series_hierarchy_conflict(pacs, ct_study, actual):
    """TC-R6-N3 | 目标: Series 归属冲突被拒 | 输入: 实例的 SeriesInstanceUID 已属于另一个 StudyInstanceUID | 期望: 0xC003，原有层级不变"""
    assert store_all(pacs, "CT_SIM_1", ct_study[2][:1]) == [0x0000]
    ds = ct_study[1][1].copy()
    ds.StudyInstanceUID = generate_uid()   # 同一 Series 挂到另一个 Study
    res = ModalitySim("CT_SIM_1").send([ds], pacs.host, pacs.port)
    status, comment = res.statuses[0]
    studies = pacs.svc.db.scalar("SELECT COUNT(*) FROM studies")
    actual(f"status=0x{status:04X} comment={comment!r} studies={studies}")
    assert status == 0xC003 and studies == 1 and n_instances(pacs.svc) == 1


def test_r7_p1_identical_resend(pacs, ct_study, actual):
    """TC-R7-P1 | 目标: 相同实例重发幂等成功 | 输入: 同一实例同一字节发送两次 | 期望: 两次 0x0000，库中 1 条，审计 STORE_DUPLICATE_IDENTICAL，告警 0"""
    p = ct_study[2][0]
    s1 = store_all(pacs, "CT_SIM_1", [p])
    s2 = store_all(pacs, "CT_SIM_1", [p])
    dup = pacs.svc.audit.recent(20, "STORE_DUPLICATE_IDENTICAL")
    actual(f"s1={s1} s2={s2} instances={n_instances(pacs.svc)} dup_audit={len(dup)} alerts={pacs.svc.alerts.count(None)}")
    assert s1 == s2 == [0x0000] and n_instances(pacs.svc) == 1 and dup
    assert pacs.svc.alerts.count(None) == 0


def test_r7_p2_equivalent_encoding_resend(pacs, ct_study, tmp_path, actual):
    """TC-R7-P2 | 目标: 不同编码的同内容重发视为等价 | 输入: 先以 Explicit VR LE 入库，再以 Implicit VR LE 重发同一实例 | 期望: 0x0000，库中 1 条，已存文件不变，审计 STORE_DUPLICATE_EQUIVALENT"""
    p = ct_study[2][0]
    assert store_all(pacs, "CT_SIM_1", [p]) == [0x0000]
    row = pacs.svc.verifier.instance_row(ct_study[1][0].SOPInstanceUID)
    stored_path = pacs.svc.config.store_dir / row["rel_path"]
    before = sha(stored_path.read_bytes())
    ds = dcmread(p)
    ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    p2 = tmp_path / "implicit.dcm"
    ds.save_as(p2, implicit_vr=True, little_endian=True, enforce_file_format=True)
    res = ModalitySim("CT_SIM_1").send([p2], pacs.host, pacs.port,
                                       transfer_syntaxes=[ImplicitVRLittleEndian])
    eq = pacs.svc.audit.recent(20, "STORE_DUPLICATE_EQUIVALENT")
    actual(f"status={res.statuses} instances={n_instances(pacs.svc)} file_unchanged={before == sha(stored_path.read_bytes())} equivalent_audit={len(eq)}")
    assert res.statuses[0][0] == 0x0000 and n_instances(pacs.svc) == 1 and eq
    assert before == sha(stored_path.read_bytes())


def test_r7_n1_uid_conflict_quarantined(pacs, ct_study, tmp_path, actual):
    """TC-R7-N1 | 目标: 同 UID 不同像素被拒并隔离 | 输入: 发送与已存实例同 UID、像素不同的数据集 | 期望: 0xC010，已存文件与基线不变，隔离区 +1，告警 UID_CONFLICT"""
    p = ct_study[2][0]
    assert store_all(pacs, "CT_SIM_1", [p]) == [0x0000]
    row = pacs.svc.verifier.instance_row(ct_study[1][0].SOPInstanceUID)
    stored_path = pacs.svc.config.store_dir / row["rel_path"]
    before = sha(stored_path.read_bytes())
    ds = dcmread(p)
    px = bytearray(ds.PixelData)
    px[100] ^= 0x55
    ds.PixelData = bytes(px)
    p2 = tmp_path / "conflict.dcm"
    ds.save_as(p2, enforce_file_format=True)
    res = ModalitySim("CT_SIM_1").send([p2], pacs.host, pacs.port)
    q = pacs.svc.db.scalar("SELECT COUNT(*) FROM quarantine WHERE reason='UID_CONFLICT'")
    row2 = pacs.svc.verifier.instance_row(ct_study[1][0].SOPInstanceUID)
    actual(f"status=0x{res.statuses[0][0]:04X} quarantine={q} file_unchanged={before == sha(stored_path.read_bytes())} baseline_unchanged={row2['file_sha256'] == row['file_sha256']}")
    assert res.statuses[0][0] == 0xC010 and q == 1
    assert before == sha(stored_path.read_bytes()) and row2["file_sha256"] == row["file_sha256"]
    assert pacs.svc.alerts.count("open", "UID_CONFLICT") == 1


def test_r7_n2_damaged_stored_copy_no_false_success(pacs, ct_study, actual):
    """TC-R7-N2 | 目标: 已存副本损坏时重发不报成功 | 输入: 篡改已存文件像素后，设备重发与原基线相同的数据 | 期望: 0xC011，告警 STORED_COPY_DAMAGED，库内文件不被自动覆盖"""
    from pacs_server.sim import tamper
    p = ct_study[2][0]
    uid = ct_study[1][0].SOPInstanceUID
    assert store_all(pacs, "CT_SIM_1", [p]) == [0x0000]
    tamper.flip_pixel_byte(pacs.svc, uid)
    row = pacs.svc.verifier.instance_row(uid)
    tampered = sha((pacs.svc.config.store_dir / row["rel_path"]).read_bytes())
    s = store_all(pacs, "CT_SIM_1", [p])
    after = sha((pacs.svc.config.store_dir / row["rel_path"]).read_bytes())
    actual(f"status={[hex(x) for x in s]} STORED_COPY_DAMAGED={pacs.svc.alerts.count('open', 'STORED_COPY_DAMAGED')} file_still_tampered={after == tampered}")
    assert s == [0xC011]
    assert pacs.svc.alerts.count("open", "STORED_COPY_DAMAGED") == 1
    assert after == tampered


def _patient_find(pacs, **keys):
    return WorkstationSim("WS_SIM_1", 0).find(pacs.host, pacs.port,
                                              identifier("PATIENT", **keys), model="P")


def test_r8_p1_same_patient_two_studies(pacs, tmp_path, actual):
    """TC-R8-P1 | 目标: 同一患者的两个检查归在一起 | 输入: SYN0001 的 CT 检查与内镜检查入库 | 期望: PATIENT 级 C-FIND 返回 1 个患者，NumberOfPatientRelatedStudies=2"""
    p1 = synth.DEFAULT_PATIENTS[0]
    ct = synth.write_datasets(synth.ct_series(synth.SynthStudy(p1), 2, 64, 64), tmp_path / "a")
    es = synth.write_datasets(synth.endo_series(synth.SynthStudy(p1), 2, 48, 64), tmp_path / "b")
    store_all(pacs, "CT_SIM_1", ct)
    store_all(pacs, "ENDO_SIM_1", es)
    _, matches, final = _patient_find(pacs, PatientID="SYN0001", PatientName="",
                                      NumberOfPatientRelatedStudies="")
    actual(f"final=0x{final:04X} patients={len(matches)} studies={[m.NumberOfPatientRelatedStudies for m in matches]}")
    assert final == 0 and len(matches) == 1 and int(matches[0].NumberOfPatientRelatedStudies) == 2


def test_r8_n1_same_name_different_id_not_merged(pacs, tmp_path, actual):
    """TC-R8-N1 | 目标: 不同 PatientID 同姓名不合并 | 输入: SYN0003 与 SYN0004 均名为 TEST^SAME 的实例入库 | 期望: PATIENT 级查询 TEST^SAME 返回 2 个患者"""
    for i, pat in enumerate(synth.DEFAULT_PATIENTS[2:4]):
        store_all(pacs, "CT_SIM_1", synth.write_datasets(
            synth.ct_series(synth.SynthStudy(pat), 1, 32, 32), tmp_path / f"p{i}"))
    _, matches, final = _patient_find(pacs, PatientName="TEST^SAME", PatientID="")
    actual(f"patients={sorted(m.PatientID for m in matches)}")
    assert final == 0 and sorted(m.PatientID for m in matches) == ["SYN0003", "SYN0004"]


def test_r8_n2_demographics_conflict_flag_only(pacs, tmp_path, actual):
    """TC-R8-N2 | 目标: 人口学不一致只标记不告警 | 输入: PatientID=SYN0001 但姓名不同的实例入库 | 期望: 入库成功，该患者 demographics_conflict=1，告警数不增加"""
    p1 = synth.DEFAULT_PATIENTS[0]
    store_all(pacs, "CT_SIM_1", synth.write_datasets(
        synth.ct_series(synth.SynthStudy(p1), 1, 32, 32), tmp_path / "a"))
    other = synth.SynthPatient("SYN0001", "TEST^RENAMED", p1.birth_date, p1.sex)
    s = store_all(pacs, "CT_SIM_1", synth.write_datasets(
        synth.ct_series(synth.SynthStudy(other), 1, 32, 32), tmp_path / "b"))
    flag = pacs.svc.db.scalar("SELECT demographics_conflict FROM patients WHERE patient_id='SYN0001'")
    actual(f"status={[hex(x) for x in s]} demographics_conflict={flag} alerts={pacs.svc.alerts.count(None)}")
    assert s == [0x0000] and flag == 1 and pacs.svc.alerts.count(None) == 0


def test_r8_n3_empty_patient_id_grouped(pacs, tmp_path, actual):
    """TC-R8-N3 | 目标: PatientID 为空时归入空 ID 患者并审计注明 | 输入: PatientID 为空（Type 2）的 CT 入库 | 期望: 0x0000，患者表中有 patient_id='' 的记录，STORE_ACCEPTED 审计含“PatientID 为空”"""
    pat = synth.SynthPatient("", "ANONYMOUS^SYNTH")
    s = store_all(pacs, "CT_SIM_1", synth.write_datasets(
        synth.ct_series(synth.SynthStudy(pat), 1, 32, 32), tmp_path / "a"))
    rows = pacs.svc.db.query("SELECT * FROM patients WHERE patient_id=''")
    note = json.loads(pacs.svc.audit.recent(20, "STORE_ACCEPTED")[0]["details_json"]).get("note", "")
    actual(f"status={[hex(x) for x in s]} empty_id_patients={len(rows)} note={note!r}")
    assert s == [0x0000] and len(rows) == 1 and "PatientID 为空" in note


def _abort_once(pacs, tmp_path):
    ds = synth.ct_series(synth.SynthStudy(synth.DEFAULT_PATIENTS[0]), 1, 512, 512)[0]
    path = synth.write_datasets([ds], tmp_path / "big")[0]
    res = ModalitySim("CT_SIM_1").send_then_abort(ds, pacs.host, pacs.port, pdus_before_abort=3)
    import time
    time.sleep(0.5)
    return ds, path, res


def test_r13_n1_aborted_partial_not_visible(pacs, tmp_path, actual):
    """TC-R13-N1 | 目标: 中断时未完成的实例不可见 | 输入: 512×512 CT 以 4 KB PDU 发送 3 个 P-DATA 后 A-ABORT | 期望: 库中无该实例、存储与 tmp 目录无半截文件、C-FIND 查不到、审计有 ASSOCIATION_ABORTED、告警 0"""
    ds, _, res = _abort_once(pacs, tmp_path)
    aborted = pacs.svc.audit.recent(20, "ASSOCIATION_ABORTED")
    files = len(store_files(pacs.svc)) + len(list(pacs.svc.config.tmp_dir.glob("*")))
    _, matches, _ = WorkstationSim("WS_SIM_1", 0).find(
        pacs.host, pacs.port, identifier("IMAGE", StudyInstanceUID=ds.StudyInstanceUID,
                                         SeriesInstanceUID=ds.SeriesInstanceUID, SOPInstanceUID=""))
    actual(f"abort={res.statuses} instances={n_instances(pacs.svc)} files={files} find={len(matches)} aborted_audit={len(aborted)} alerts={pacs.svc.alerts.count(None)}")
    assert n_instances(pacs.svc) == 0 and files == 0 and len(matches) == 0 and aborted
    assert pacs.svc.alerts.count(None) == 0


def test_r13_p1_resend_after_abort(pacs, tmp_path, actual):
    """TC-R13-P1 | 目标: 传输中断后重发成功且无告警 | 输入: 同上中断后，设备重连并重发同一实例 | 期望: 重发 0x0000，库中 1 条，告警 0"""
    ds, path, _ = _abort_once(pacs, tmp_path)
    s = store_all(pacs, "CT_SIM_1", [path])
    actual(f"重发 status={[hex(x) for x in s]} instances={n_instances(pacs.svc)} alerts={pacs.svc.alerts.count(None)}")
    assert s == [0x0000] and n_instances(pacs.svc) == 1 and pacs.svc.alerts.count(None) == 0


def test_r19_p1_restart_persists(pacs, ct_study, actual):
    """TC-R19-P1 | 目标: 重启后数据仍在 | 输入: 5 张 CT 入库后停止服务，再用同一 runtime 启动 | 期望: C-FIND 可查到、C-GET 可取回、核查 0 异常"""
    store_all(pacs, "CT_SIM_1", ct_study[2])
    svc = pacs.restart()
    st = ct_study[0]
    _, matches, final = WorkstationSim("WS_SIM_2", 0).find(
        pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=st.study_uid,
                                         NumberOfStudyRelatedInstances=""))
    _, out = WorkstationSim("WS_SIM_2", 0).get(
        pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=st.study_uid),
        [str(ct_study[1][0].SOPClassUID)])
    rep = svc.verifier.run(operator="pytest")
    actual(f"find={len(matches)} instances={matches[0].NumberOfStudyRelatedInstances if matches else None} get_completed={out['completed']} verify_anomalies={rep['anomalous_instances']}")
    assert final == 0 and int(matches[0].NumberOfStudyRelatedInstances) == 5
    assert out["final"] == 0 and out["completed"] == 5 and rep["anomalous_instances"] == 0


def test_r19_n1_orphan_file_quarantined(pacs, ct_study, actual):
    """TC-R19-N1 | 目标: 孤儿文件被发现并隔离 | 输入: 服务停止期间在存储目录放入一个无索引的 DICOM 文件，再启动 | 期望: 告警 ORPHAN_FILE，文件移入隔离区，C-FIND 查不到"""
    pacs.svc.stop()
    orphan = pacs.svc.config.store_dir / "2026" / "11" / "12" / "planted.dcm"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(Path(ct_study[2][0]).read_bytes())
    svc = pacs.restart()
    q = svc.db.query("SELECT * FROM quarantine WHERE reason='ORPHAN_FILE'")
    _, matches, _ = WorkstationSim("WS_SIM_1", 0).find(
        pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=ct_study[0].study_uid))
    actual(f"ORPHAN_FILE={svc.alerts.count('open', 'ORPHAN_FILE')} orphan_exists={orphan.exists()} quarantine={len(q)} find={len(matches)}")
    assert svc.alerts.count("open", "ORPHAN_FILE") == 1 and not orphan.exists() and len(q) == 1
    assert (svc.config.quarantine_dir / q[0]["rel_path"]).exists() and len(matches) == 0


def test_r5_p2_endoscopy_and_secondary_capture(pacs, tmp_path, actual):
    """TC-R5-P2 | 目标: 内镜（VL Endoscopic）与二次捕获（SC）入库 | 输入: ENDO_SIM_1 发送 2 张 RGB 内镜与 2 张 SC | 期望: 全部 0x0000，SOP 类与模态正确，dataset_sha256 与发送端一致"""
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0])
    paths = synth.write_datasets(synth.endo_series(st, 2, 48, 64) + synth.sc_series(st, 2, 32, 32),
                                 tmp_path / "es")
    statuses = store_all(pacs, "ENDO_SIM_1", paths)
    rows = pacs.svc.db.query("SELECT i.sop_class_uid, s.modality FROM instances i JOIN series s ON s.id=i.series_pk")
    same = all(sha(file_dataset_bytes(p)) == pacs.svc.verifier.instance_row(
        str(dcmread(p, stop_before_pixels=True).SOPInstanceUID))["dataset_sha256"] for p in paths)
    actual(f"status={[hex(s) for s in statuses]} classes={sorted({(r['sop_class_uid'], r['modality']) for r in rows})} hash_equal={same}")
    assert statuses == [0] * 4 and same
    assert {(r["sop_class_uid"], r["modality"]) for r in rows} == {
        ("1.2.840.10008.5.1.4.1.1.77.1.1", "ES"), ("1.2.840.10008.5.1.4.1.1.7", "OT")}
