"""R9–R11：C-FIND / C-MOVE / C-GET（真实 TCP，E2）。"""

import pytest
from pydicom.uid import CTImageStorage, VLEndoscopicImageStorage

from conftest import store_all
from pacs_server.sim import synth
from pacs_server.sim.scu import WorkstationSim, identifier


@pytest.fixture
def library(pacs, tmp_path):
    """SYN0001: CT(5) + 内镜(4)；SYN0002（中文姓名）: CT(3)。"""
    p1, p2 = synth.DEFAULT_PATIENTS[0], synth.DEFAULT_PATIENTS[1]
    ct1 = synth.SynthStudy(p1, "20261105", "093000", "ACC1001", "SYNTHETIC CT CHEST")
    es1 = synth.SynthStudy(p1, "20261108", "141500", "ACC1002", "SYNTHETIC ENDOSCOPY")
    ct2 = synth.SynthStudy(p2, "20261210", "101000", "ACC2001", "SYNTHETIC CT HEAD")
    d_ct1 = synth.ct_series(ct1, 5, 64, 64)
    d_es1 = synth.endo_series(es1, 4, 48, 64)
    d_ct2 = synth.ct_series(ct2, 3, 64, 64)
    store_all(pacs, "CT_SIM_1", synth.write_datasets(d_ct1, tmp_path / "ct1"))
    store_all(pacs, "ENDO_SIM_1", synth.write_datasets(d_es1, tmp_path / "es1"))
    store_all(pacs, "CT_SIM_2", synth.write_datasets(d_ct2, tmp_path / "ct2"))
    return {"ct1": (ct1, d_ct1), "es1": (es1, d_es1), "ct2": (ct2, d_ct2)}


def ws(name="WS_SIM_1"):
    return WorkstationSim(name, 0)


def baseline_ds_sha(svc, uid):
    return svc.verifier.instance_row(uid)["dataset_sha256"]


def test_r9_p1_find_studies_by_patient_id(pacs, library, actual):
    """TC-R9-P1 | 目标: 按 PatientID 查询检查 | 输入: Study Root STUDY 级 PatientID=SYN0001 | 期望: 恰好返回 SYN0001 的 2 个检查，含 StudyInstanceUID/StudyDate/ModalitiesInStudy/NumberOfStudyRelatedInstances，最终 0x0000"""
    _, m, final = ws().find(pacs.host, pacs.port, identifier(
        "STUDY", PatientID="SYN0001", StudyInstanceUID="", StudyDate="", ModalitiesInStudy="",
        NumberOfStudyRelatedInstances=""))
    got = sorted((x.StudyDate, str(x.ModalitiesInStudy), int(x.NumberOfStudyRelatedInstances)) for x in m)
    actual(f"final=0x{final:04X} results={got}")
    assert final == 0 and got == [("20261105", "CT", 5), ("20261108", "ES", 4)]
    assert {x.StudyInstanceUID for x in m} == {library["ct1"][0].study_uid, library["es1"][0].study_uid}


def test_r9_p2_wildcard_date_range_chinese(pacs, library, actual):
    """TC-R9-P2 | 目标: 通配符、日期范围与中文姓名匹配 | 输入: PatientName=TEST*；StudyDate=20261101-20261130；PatientName=测试^患者二（ISO_IR 192） | 期望: 分别返回 SYN0001 的 2 个检查、11 月的 2 个检查、SYN0002 的 1 个检查"""
    _, a, fa = ws().find(pacs.host, pacs.port, identifier("STUDY", PatientName="TEST*",
                                                          StudyInstanceUID=""))
    _, b, fb = ws().find(pacs.host, pacs.port, identifier("STUDY", StudyDate="20261101-20261130",
                                                          StudyInstanceUID=""))
    q = identifier("STUDY", PatientName="测试^患者二", PatientID="", StudyInstanceUID="")
    q.SpecificCharacterSet = "ISO_IR 192"
    _, c, fc = ws().find(pacs.host, pacs.port, q)
    actual(f"TEST*={len(a)} 日期范围={len(b)} 中文={[(x.PatientID, str(x.PatientName)) for x in c]}")
    assert (fa, fb, fc) == (0, 0, 0)
    assert len(a) == 2 and len(b) == 2
    assert len(c) == 1 and c[0].PatientID == "SYN0002" and str(c[0].PatientName) == "测试^患者二"


def test_r9_n1_no_match_empty_success(pacs, library, actual):
    """TC-R9-N1 | 目标: 无匹配时返回空结果且成功 | 输入: PatientID=NOPE | 期望: 没有 0xFF00 响应，最终 0x0000"""
    _, m, final = ws().find(pacs.host, pacs.port, identifier("STUDY", PatientID="NOPE",
                                                             StudyInstanceUID=""))
    actual(f"matches={len(m)} final=0x{final:04X}")
    assert m == [] and final == 0


def test_r9_n2_missing_upper_unique_key(pacs, library, actual):
    """TC-R9-N2 | 目标: 缺少上层唯一键被拒 | 输入: Study Root SERIES 级查询不带 StudyInstanceUID | 期望: 最终 0xA900"""
    _, m, final = ws().find(pacs.host, pacs.port, identifier("SERIES", SeriesInstanceUID="",
                                                             Modality="CT"))
    actual(f"matches={len(m)} final=0x{final:04X}")
    assert m == [] and final == 0xA900


def test_r9_n3_find_cancel(pacs, library, actual):
    """TC-R9-N3 | 目标: 查询中途取消 | 输入: IMAGE 级查询（5 条匹配，测试节流 0.2 s/条），收到第 1 条后发 C-CANCEL | 期望: 最终 0xFE00，收到少于 5 条"""
    pacs.svc.config.test_pending_delay_s = 0.2
    st, d = library["ct1"]
    _, m, final = ws().find(pacs.host, pacs.port, identifier(
        "IMAGE", StudyInstanceUID=st.study_uid, SeriesInstanceUID=d[0].SeriesInstanceUID,
        SOPInstanceUID=""), cancel_after=1)
    actual(f"received={len(m)} final=0x{final:04X}")
    assert final == 0xFE00 and len(m) < 5


def test_r10_p1_move_study_counts_and_hashes(pacs, tmp_path, ws1, actual):
    """TC-R10-P1 | 目标: 推送整个检查到已登记工作站 | 输入: 100 张（128×128）CT 检查，WS_SIM_1 请求 C-MOVE 到 WS_SIM_1 | 期望: 最终 0x0000，完成 100、失败 0；收到的每个数据集哈希等于基线 dataset_sha256"""
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0])
    d = synth.ct_series(st, 100, 128, 128)
    store_all(pacs, "CT_SIM_1", synth.write_datasets(d, tmp_path / "ct"))
    res, out = ws1.move(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=st.study_uid),
                        "WS_SIM_1")
    mism = [x.SOPInstanceUID for x in d
            if ws1.received.get(x.SOPInstanceUID) is None
            or ws1.received[x.SOPInstanceUID].dataset_sha256 != baseline_ds_sha(pacs.svc, x.SOPInstanceUID)]
    actual(f"final=0x{out['final']:04X} completed={out['completed']} failed={out['failed']} received={len(ws1.received)} hash_mismatch={len(mism)}")
    assert out["final"] == 0 and out["completed"] == 100 and out["failed"] == 0
    assert len(ws1.received) == 100 and mism == []
    res_audit = pacs.svc.audit.recent(50, "DICOM_MOVE_RESULT")
    assert res_audit, "缺少 DICOM_MOVE_RESULT 审计"


def test_r10_n1_unknown_destination_no_outbound(pacs, library, monkeypatch, actual):
    """TC-R10-N1 | 目标: 未登记目的地被拒且不外连 | 输入: WS_SIM_1 请求 C-MOVE 到未登记的 UNKNOWN_WS | 期望: 最终 0xA801，服务器未调用出站 associate，告警 UNKNOWN_MOVE_DESTINATION"""
    calls = []
    orig = pacs.svc.dicom.ae.associate
    monkeypatch.setattr(pacs.svc.dicom.ae, "associate",
                        lambda *a, **k: calls.append((a, k)) or orig(*a, **k))
    _, out = ws().move(pacs.host, pacs.port,
                       identifier("STUDY", StudyInstanceUID=library["ct1"][0].study_uid),
                       "UNKNOWN_WS")
    actual(f"final=0x{out['final']:04X} outbound_associate_calls={len(calls)} alerts={pacs.svc.alerts.count('open', 'UNKNOWN_MOVE_DESTINATION')}")
    assert out["final"] == 0xA801 and calls == []
    assert pacs.svc.alerts.count("open", "UNKNOWN_MOVE_DESTINATION") == 1


def test_r10_n2_disabled_destination(pacs, library, ws2, actual):
    """TC-R10-N2 | 目标: 目的地停用后被拒 | 输入: 停用 WS_SIM_2 后，WS_SIM_1 请求 C-MOVE 到 WS_SIM_2 | 期望: 最终 0xA801，WS_SIM_2 未收到影像，告警 UNKNOWN_MOVE_DESTINATION"""
    pacs.svc.whitelist.set_enabled("WS_SIM_2", False, operator="pytest")
    _, out = ws().move(pacs.host, pacs.port,
                       identifier("STUDY", StudyInstanceUID=library["ct1"][0].study_uid), "WS_SIM_2")
    actual(f"final=0x{out['final']:04X} ws2_received={len(ws2.received)}")
    assert out["final"] == 0xA801 and len(ws2.received) == 0
    assert pacs.svc.alerts.count("open", "UNKNOWN_MOVE_DESTINATION") == 1


def test_r11_p1_get_series(pacs, tmp_path, actual):
    """TC-R11-P1 | 目标: C-GET 取回整个序列 | 输入: 25 张内镜序列，WS_SIM_2 SERIES 级 C-GET | 期望: 最终 0x0000，完成 25，收到的数据集哈希等于基线"""
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0])
    d = synth.endo_series(st, 25, 120, 160)
    store_all(pacs, "ENDO_SIM_1", synth.write_datasets(d, tmp_path / "es"))
    _, out = ws("WS_SIM_2").get(pacs.host, pacs.port, identifier(
        "SERIES", StudyInstanceUID=st.study_uid, SeriesInstanceUID=d[0].SeriesInstanceUID),
        [VLEndoscopicImageStorage])
    rec = out["received"]
    mism = [u for u, r in rec.items() if r.dataset_sha256 != baseline_ds_sha(pacs.svc, u)]
    actual(f"final=0x{out['final']:04X} completed={out['completed']} received={len(rec)} hash_mismatch={len(mism)}")
    assert out["final"] == 0 and out["completed"] == 25 and len(rec) == 25 and mism == []


def test_r11_n1_get_without_scp_role(pacs, library, actual):
    """TC-R11-N1 | 目标: 未协商存储 SCP 角色时子操作失败 | 输入: WS_SIM_2 C-GET SYN0001 的 CT 检查，但不为 CT Image Storage 协商 SCP 角色 | 期望: 最终 0xA702，失败数 = 5，Failed SOP Instance UID List 含这 5 个实例"""
    st, d = library["ct1"]
    _, out = ws("WS_SIM_2").get(pacs.host, pacs.port,
                                identifier("STUDY", StudyInstanceUID=st.study_uid),
                                [CTImageStorage], role_for=[])
    actual(f"final=0x{out['final']:04X} failed={out['failed']} failed_uids={len(out['failed_uids'])}")
    assert out["final"] == 0xA702 and out["failed"] == 5
    assert sorted(out["failed_uids"]) == sorted(x.SOPInstanceUID for x in d)


def test_r11_n2_get_cancel(pacs, tmp_path, actual):
    """TC-R11-N2 | 目标: C-GET 取回中途取消 | 输入: 10 张 CT（测试节流 0.2 s/张），收到第 1 个子操作后发 C-CANCEL | 期望: 最终 0xFE00，完成数 < 10"""
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0])
    d = synth.ct_series(st, 10, 64, 64)
    store_all(pacs, "CT_SIM_1", synth.write_datasets(d, tmp_path / "ct"))
    pacs.svc.config.test_pending_delay_s = 0.2
    _, out = ws("WS_SIM_2").get(pacs.host, pacs.port,
                                identifier("STUDY", StudyInstanceUID=st.study_uid),
                                [CTImageStorage], cancel_after=1)
    actual(f"final=0x{out['final']:04X} completed={out['completed']}")
    assert out["final"] == 0xFE00 and out["completed"] < 10
