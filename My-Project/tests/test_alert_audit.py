"""R17–R18：告警零误报与聚合确认、哈希链审计与截断检测。"""

import threading
import time

from pydicom.uid import CTImageStorage

from conftest import store_all
from pacs_server.sim import synth, tamper
from pacs_server.sim.scu import ModalitySim, WorkstationSim, identifier


def test_r17_p1_normal_flow_zero_alerts(pacs, tmp_path, ws1, actual):
    """TC-R17-P1 | 目标: 正常演示流程零告警 | 输入: 登记、C-ECHO、2 台设备并发 C-STORE、C-FIND、C-MOVE、C-GET、相同实例重发、传输中断后重发、全量核查 | 期望: 告警表为空"""
    st1 = synth.SynthStudy(synth.DEFAULT_PATIENTS[0])
    st2 = synth.SynthStudy(synth.DEFAULT_PATIENTS[1])
    p1 = synth.write_datasets(synth.ct_series(st1, 6, 64, 64), tmp_path / "a")
    p2 = synth.write_datasets(synth.endo_series(st2, 4, 48, 64), tmp_path / "b")
    assert ModalitySim("CT_SIM_1").echo(pacs.host, pacs.port).statuses == [0]
    results = {}
    t1 = threading.Thread(target=lambda: results.setdefault("ct", store_all(pacs, "CT_SIM_1", p1)))
    t2 = threading.Thread(target=lambda: results.setdefault("es", store_all(pacs, "ENDO_SIM_1", p2)))
    t1.start(); t2.start(); t1.join(); t2.join()
    WorkstationSim("WS_SIM_1", 0).find(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=""))
    ws1.move(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=st1.study_uid), "WS_SIM_1")
    WorkstationSim("WS_SIM_2", 0).get(pacs.host, pacs.port,
                                      identifier("STUDY", StudyInstanceUID=st1.study_uid), [CTImageStorage])
    store_all(pacs, "CT_SIM_1", p1[:2])
    extra = synth.ct_series(st1, 1, 256, 256)[0]
    ModalitySim("CT_SIM_1").send_then_abort(extra, pacs.host, pacs.port, 2)
    time.sleep(0.3)
    store_all(pacs, "CT_SIM_1", synth.write_datasets([extra], tmp_path / "c"))
    rep = pacs.svc.verifier.run(operator="pytest")
    alerts = pacs.svc.alerts.list()
    actual(f"stores ct={results['ct']} es={results['es']} verify_anomalies={rep['anomalous_instances']} alerts={[a['type'] for a in alerts]}")
    assert results["ct"] == [0] * 6 and results["es"] == [0] * 4
    assert rep["anomalous_instances"] == 0 and alerts == []


def test_r17_n1_alert_aggregation_and_ack(pacs, actual):
    """TC-R17-N1 | 目标: 异常产生告警且同类聚合、确认留痕 | 输入: ROGUE 连续 3 次尝试关联，随后管理员确认该告警 | 期望: 1 条 UNREGISTERED_PEER，count=3，含 AE/IP 与建议处置；确认后 acknowledged，审计 ALERT_ACK"""
    for _ in range(3):
        ModalitySim("ROGUE").echo(pacs.host, pacs.port)
    rows = pacs.svc.alerts.list()
    a = rows[0]
    import json
    d = json.loads(a["details_json"])
    ok = pacs.svc.alerts.ack(a["id"], "admin-zhang(示例操作员)")
    a2 = pacs.svc.alerts.list()[0]
    acks = pacs.svc.audit.recent(10, "ALERT_ACK")
    actual(f"alerts={len(rows)} type={a['type']} count={a['count']} details={d} suggestion={a['suggestion'][:20]}… status={a2['status']} ack_audit={len(acks)}")
    assert len(rows) == 1 and a["type"] == "UNREGISTERED_PEER" and a["count"] == 3
    assert d["calling_ae"] == "ROGUE" and d["ip"] == "127.0.0.1" and a["suggestion"]
    assert ok and a2["status"] == "acknowledged" and len(acks) == 1


def test_r18_p1_audit_entries_and_chain_ok(pacs, tmp_path, actual):
    """TC-R18-P1 | 目标: 操作与审计一一对应且链完整 | 输入: 1 次 C-ECHO、1 次 C-STORE、1 次 C-FIND 后执行审计校验 | 期望: 依次出现 DICOM_ECHO、STORE_ACCEPTED、DICOM_FIND；校验 ok 并给出链头"""
    start = pacs.svc.audit.head()[0]
    WorkstationSim("WS_SIM_1", 0).echo(pacs.host, pacs.port)
    store_all(pacs, "CT_SIM_1", synth.write_datasets(
        synth.ct_series(synth.SynthStudy(synth.DEFAULT_PATIENTS[0]), 1, 32, 32), tmp_path / "a"))
    WorkstationSim("WS_SIM_1", 0).find(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=""))
    rows = pacs.svc.db.query("SELECT seq, event_type FROM audit WHERE seq>? ORDER BY seq", (start,))
    seq_of = {}
    for r in rows:
        seq_of.setdefault(r["event_type"], r["seq"])
    rep = pacs.svc.verify_audit(operator="pytest")
    actual(f"order ECHO={seq_of.get('DICOM_ECHO')} STORE={seq_of.get('STORE_ACCEPTED')} FIND={seq_of.get('DICOM_FIND')} verify_ok={rep.ok} head={rep.head_seq}:{rep.head_hash[:12]}…")
    assert seq_of["DICOM_ECHO"] < seq_of["STORE_ACCEPTED"] < seq_of["DICOM_FIND"]
    assert rep.ok and rep.unanchored_entries == 0


def test_r18_n1_middle_entry_modified(pacs, actual):
    """TC-R18-N1 | 目标: 中间记录被修改 | 输入: 直接修改第 k=3 条审计记录的 details 后校验 | 期望: 校验失败，第一个断点为第 3 条，告警 AUDIT_CHAIN_BROKEN"""
    tamper.edit_audit_details(pacs.svc, 3)
    rep = pacs.svc.verify_audit(operator="pytest")
    actual(f"ok={rep.ok} first_break={rep.first_break_seq} problems={rep.problems[:2]}")
    assert not rep.ok and rep.first_break_seq == 3
    assert pacs.svc.alerts.count("open", "AUDIT_CHAIN_BROKEN") == 1


def test_r18_n2_tail_truncated(pacs, actual):
    """TC-R18-N2 | 目标: 尾部被截断 | 输入: 删除数据库最后 3 条审计（锚点不动）后校验 | 期望: 校验失败，报告锚点最大 seq 大于数据库最大 seq"""
    for i in range(3):
        pacs.svc.audit.append("TEST_EVENT", details={"i": i})
    head_before = pacs.svc.audit.head()[0]
    tamper.truncate_audit_tail(pacs.svc, 3)
    rep = pacs.svc.audit.verify()
    actual(f"ok={rep.ok} db_head={rep.head_seq} anchor_max={rep.anchor_max_seq} problems={rep.problems[:1]}")
    assert not rep.ok and rep.anchor_max_seq == head_before and rep.head_seq == head_before - 3
    assert any("尾部截断" in p for p in rep.problems)


def test_r18_n3_external_head_detects_double_truncation(pacs, actual):
    """TC-R18-N3 | 目标: 与带外链头不一致 | 输入: 记录带外链头后再追加 3 条；同时截断数据库与锚点的最后 5 条；分别不带 / 带带外链头校验 | 期望: 不带链头时校验通过（检测边界）；带链头时失败，报告带外链头不在当前链中"""
    for i in range(4):
        pacs.svc.audit.append("TEST_EVENT", details={"i": i})
    external = pacs.svc.audit.head_string()
    for i in range(3):
        pacs.svc.audit.append("TEST_EVENT", details={"j": i})
    tamper.truncate_audit_tail(pacs.svc, 5)
    tamper.truncate_anchor_tail(pacs.svc, 5)
    plain = pacs.svc.audit.verify()
    with_head = pacs.svc.audit.verify(expect_head=external)
    actual(f"不带链头 ok={plain.ok}；带链头 ok={with_head.ok} problems={with_head.problems[:1]}")
    assert plain.ok, "同时截断数据库与锚点而无带外链头时，本地检测不到（已写入边界）"
    assert not with_head.ok and any("带外链头" in p for p in with_head.problems)
