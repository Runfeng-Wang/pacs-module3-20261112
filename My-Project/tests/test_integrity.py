"""R14–R16：基线、完整性核查与分类、调阅前校验与阻断（E2，篡改由 sim/tamper.py 直接改文件 / 数据库注入）。"""

import hashlib

import pytest
from pydicom.uid import CTImageStorage, ImplicitVRLittleEndian

from conftest import store_all
from pacs_server.sim import synth, tamper
from pacs_server.sim.scu import ModalitySim, WorkstationSim, identifier


@pytest.fixture
def lib(pacs, tmp_path):
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0], "20261105", "093000", "ACC1001")
    d = synth.ct_series(st, 5, 128, 128)
    paths = synth.write_datasets(d, tmp_path / "ct")
    assert store_all(pacs, "CT_SIM_1", paths) == [0] * 5
    return st, d, paths


def cats(findings, uid=None):
    return sorted({f["category"] for f in findings if uid is None or f["sop_uid"] == uid})


def file_hashes(svc):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in svc.config.store_dir.rglob("*.dcm")}


def test_r14_p1_baseline_recomputable(pacs, lib, actual):
    """TC-R14-P1 | 目标: 基线可复算 | 输入: 一张 CT 入库后立即核查该实例 | 期望: 文件/数据集/像素哈希与基线一致、HMAC 通过、结果 OK"""
    uid = lib[1][0].SOPInstanceUID
    rep = pacs.svc.verifier.run({"sop_uid": uid}, operator="pytest")
    row = pacs.svc.verifier.instance_row(uid)
    actual(f"checked={rep['checked']} ok={rep['ok']} findings={cats(rep['findings'])} hmac_ok={pacs.svc.verifier.baseline_ok(row)}")
    assert rep["checked"] == 1 and rep["ok"] == 1 and rep["findings"] == []
    assert pacs.svc.verifier.baseline_ok(row)


def test_r14_n1_baseline_tampered(pacs, lib, actual):
    """TC-R14-N1 | 目标: 基线记录被改能检出 | 输入: 直接在数据库中修改该实例基线的 file_sha256 | 期望: 核查结果含 BASELINE_TAMPERED 并产生告警"""
    uid = lib[1][0].SOPInstanceUID
    tamper.edit_baseline_hash(pacs.svc, uid)
    rep = pacs.svc.verifier.run({"sop_uid": uid}, operator="pytest")
    actual(f"categories={cats(rep['findings'])} alerts={pacs.svc.alerts.count('open', 'BASELINE_TAMPERED')}")
    assert "BASELINE_TAMPERED" in cats(rep["findings"])
    assert pacs.svc.alerts.count("open", "BASELINE_TAMPERED") == 1


def test_r15_p1_no_false_positive_after_normal_ops(pacs, lib, ws1, actual):
    """TC-R15-P1 | 目标: 正常业务操作后无误报 | 输入: 多次 C-MOVE / C-GET、相同实例重发、服务重启、白名单变更后全量核查 | 期望: 异常 0、新告警 0、库内文件 SHA-256 与核查前一致"""
    st, d, paths = lib
    before = file_hashes(pacs.svc)
    for _ in range(2):
        ws1.move(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=st.study_uid), "WS_SIM_1")
        WorkstationSim("WS_SIM_2", 0).get(pacs.host, pacs.port,
                                          identifier("STUDY", StudyInstanceUID=st.study_uid),
                                          [CTImageStorage])
    store_all(pacs, "CT_SIM_1", paths)                       # 相同实例重发
    pacs.svc.whitelist.update("WS_SIM_2", description="moved to room 3", operator="pytest")
    svc = pacs.restart()
    rep = svc.verifier.run(operator="pytest")
    after = file_hashes(svc)
    actual(f"checked={rep['checked']} anomalies={rep['anomalous_instances']} alerts={svc.alerts.count(None)} files_unchanged={before == after}")
    assert rep["checked"] == 5 and rep["anomalous_instances"] == 0
    assert svc.alerts.count(None) == 0 and before == after


def test_r15_n1_pixel_changed_with_region(pacs, lib, actual):
    """TC-R15-N1 | 目标: 像素被改动 | 输入: 128×128 CT 像素区第 16640 字节（第 65 行附近）翻转 | 期望: PIXEL_CHANGED，给出帧 1、行 64–95、列 0–31 的变化区域"""
    uid = lib[1][2].SOPInstanceUID
    tamper.flip_pixel_byte(pacs.svc, uid)
    rep = pacs.svc.verifier.run(operator="pytest")
    f = [x for x in rep["findings"] if x["sop_uid"] == uid and x["category"] == "PIXEL_CHANGED"][0]
    regions = f["details"]["regions"]
    actual(f"categories={cats(rep['findings'], uid)} regions={regions} other_instances_ok={rep['ok']}")
    assert cats(rep["findings"], uid) == ["PIXEL_CHANGED"]
    assert regions == [{"frame": 1, "tiles_changed": 1, "row_range": [64, 95], "col_range": [0, 31]}]
    assert rep["ok"] == 4 and pacs.svc.alerts.count("open", "PIXEL_CHANGED") == 1


def test_r15_n2_study_date_changed(pacs, lib, actual):
    """TC-R15-N2 | 目标: 检查日期被改动 | 输入: 文件 StudyDate 20261105 → 20261106 | 期望: METADATA_CHANGED，字段 (0008,0020) StudyDate，前 20261105 后 20261106，关键字段"""
    uid = lib[1][0].SOPInstanceUID
    tamper.set_element(pacs.svc, uid, "StudyDate", "20261106")
    rep = pacs.svc.verifier.run({"sop_uid": uid}, operator="pytest")
    f = [x for x in rep["findings"] if x["category"] == "METADATA_CHANGED"][0]
    ch = [c for c in f["details"]["changes"] if c["keyword"] == "StudyDate"][0]
    actual(f"categories={cats(rep['findings'])} change={ch}")
    assert ch == {"tag": "(0008,0020)", "keyword": "StudyDate", "key_field": True,
                  "before": "20261105", "after": "20261106"}
    assert "PIXEL_CHANGED" not in cats(rep["findings"])
    assert f["details"]["context"]["last_recorded_access"] is not None


def test_r15_n3_file_missing(pacs, lib, actual):
    """TC-R15-N3 | 目标: 文件被删除 | 输入: 删除一个库内文件后核查 | 期望: FILE_MISSING"""
    uid = lib[1][4].SOPInstanceUID
    tamper.delete_file(pacs.svc, uid)
    rep = pacs.svc.verifier.run(operator="pytest")
    actual(f"categories={cats(rep['findings'], uid)}")
    assert cats(rep["findings"], uid) == ["FILE_MISSING"]
    assert pacs.svc.alerts.count("open", "FILE_MISSING") == 1


def test_r15_n4_index_mismatch(pacs, lib, actual):
    """TC-R15-N4 | 目标: 只改数据库索引、文件看似正常也要报警 | 输入: 仅把数据库中 SYN0001 的 PatientName 改为 FAKE^NAME | 期望: INDEX_MISMATCH，给出索引值 FAKE^NAME 与基线值 TEST^PATIENT01"""
    tamper.edit_index_patient_name(pacs.svc, "SYN0001", "FAKE^NAME")
    rep = pacs.svc.verifier.run(operator="pytest")
    f = [x for x in rep["findings"] if x["category"] == "INDEX_MISMATCH"]
    diffs = f[0]["details"]["diffs"] if f else []
    actual(f"categories={cats(rep['findings'])} diffs={diffs}")
    assert cats(rep["findings"]) == ["INDEX_MISMATCH"]
    assert {"field": "patients.patient_name", "tag": "(0010,0010)", "index_value": "FAKE^NAME",
            "baseline_value": "TEST^PATIENT01"} in diffs


def test_r16_p1_untampered_move_ok(pacs, lib, ws1, actual):
    """TC-R16-P1 | 目标: 未改动的实例正常发送 | 输入: 对未改动检查执行 C-MOVE | 期望: 全部发送成功，没有 RETRIEVE_BLOCKED"""
    _, out = ws1.move(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=lib[0].study_uid),
                      "WS_SIM_1")
    actual(f"final=0x{out['final']:04X} completed={out['completed']} blocked_alerts={pacs.svc.alerts.count(None, 'RETRIEVE_BLOCKED')}")
    assert out["final"] == 0 and out["completed"] == 5
    assert pacs.svc.alerts.count(None, "RETRIEVE_BLOCKED") == 0


def test_r16_p2_lossless_ts_conversion_no_alert(pacs, lib, actual):
    """TC-R16-P2 | 目标: 无损传输语法转换不告警 | 输入: 库内 Explicit VR LE 实例被只接受 Implicit VR LE 的工作站 C-GET | 期望: 成功；收到 Implicit 编码；像素字节哈希 = 基线 pixel_sha256；库内文件不变；告警 0"""
    before = file_hashes(pacs.svc)
    _, out = WorkstationSim("WS_SIM_2", 0).get(
        pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=lib[0].study_uid),
        [CTImageStorage], transfer_syntaxes=[ImplicitVRLittleEndian])
    rec = out["received"]
    px_ok = all(r.pixel_sha256 == pacs.svc.verifier.instance_row(u)["pixel_sha256"] for u, r in rec.items())
    tss = sorted({r.transfer_syntax for r in rec.values()})
    actual(f"final=0x{out['final']:04X} completed={out['completed']} ts={tss} pixel_equal={px_ok} files_unchanged={before == file_hashes(pacs.svc)} alerts={pacs.svc.alerts.count(None)}")
    assert out["final"] == 0 and out["completed"] == 5 and tss == [str(ImplicitVRLittleEndian)]
    assert px_ok and before == file_hashes(pacs.svc) and pacs.svc.alerts.count(None) == 0


def test_r16_n1_tampered_instance_blocked(pacs, lib, ws1, actual):
    """TC-R16-N1 | 目标: 被篡改的实例被阻断 | 输入: 检查中 1 张像素被改后执行 C-MOVE | 期望: 该实例未发送；最终 0xB000，失败 1，Failed SOP Instance UID List 含该实例；告警 RETRIEVE_BLOCKED"""
    uid = lib[1][1].SOPInstanceUID
    tamper.flip_pixel_byte(pacs.svc, uid)
    _, out = ws1.move(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=lib[0].study_uid),
                      "WS_SIM_1")
    actual(f"final=0x{out['final']:04X} completed={out['completed']} failed={out['failed']} failed_uids={out['failed_uids']} received_tampered={uid in ws1.received}")
    assert out["final"] == 0xB000 and out["completed"] == 4 and out["failed"] == 1
    assert out["failed_uids"] == [uid] and uid not in ws1.received
    assert pacs.svc.alerts.count("open", "RETRIEVE_BLOCKED") == 1
    assert pacs.svc.alerts.count("open", "PIXEL_CHANGED") == 1
