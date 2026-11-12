"""R1–R4：连通、白名单准入、角色授权、白名单在线维护（真实 TCP 回环，证据级别 E2）。"""

from pynetdicom.sop_class import (StudyRootQueryRetrieveInformationModelFind,
                                  StudyRootQueryRetrieveInformationModelMove, Verification)

from conftest import store_all
from pacs_server.sim.scu import ModalitySim, WorkstationSim, identifier, propose_only


def _audit_types(svc, n=50):
    return [r["event_type"] for r in svc.audit.recent(n)]


def test_r1_p1_echo_ok(pacs, actual):
    """TC-R1-P1 | 目标: 已登记工作站 C-ECHO 成功 | 输入: WS_SIM_1@127.0.0.1 以被叫 AE PACS_B 发 C-ECHO | 期望: 状态 0x0000，审计有 DICOM_ECHO"""
    res = WorkstationSim("WS_SIM_1", 0).echo(pacs.host, pacs.port)
    actual(f"established={res.established} statuses={[hex(s) for s in res.statuses]}")
    assert res.established and res.statuses == [0x0000]
    assert "DICOM_ECHO" in _audit_types(pacs.svc)
    actual("审计含 DICOM_ECHO")


def test_r1_n1_wrong_called_ae(pacs, actual):
    """TC-R1-N1 | 目标: 被叫 AE 错误被拒 | 输入: WS_SIM_1 以被叫 AE WRONG_AE 请求关联 | 期望: A-ASSOCIATE-RJ (1,1,7)，审计 ASSOCIATION_REJECTED"""
    res = WorkstationSim("WS_SIM_1", 0).echo(pacs.host, pacs.port, called_ae="WRONG_AE")
    actual(f"rejected={res.rejected} rj=({res.result},{res.source},{res.reason})")
    assert res.rejected and (res.result, res.source, res.reason) == (1, 1, 7)
    assert "ASSOCIATION_REJECTED" in _audit_types(pacs.svc)


def test_r2_p1_registered_accepted(pacs, actual):
    """TC-R2-P1 | 目标: 登记设备被接受 | 输入: CT_SIM_1@127.0.0.1（已登记）请求关联 | 期望: 关联建立"""
    res = ModalitySim("CT_SIM_1").echo(pacs.host, pacs.port)
    actual(f"established={res.established}")
    assert res.established


def test_r2_n1_unregistered_rejected_alert(pacs, actual):
    """TC-R2-N1 | 目标: 未登记 AE 被拒并告警 | 输入: ROGUE 请求关联 | 期望: RJ(1,1,3)，告警 UNREGISTERED_PEER"""
    res = ModalitySim("ROGUE").echo(pacs.host, pacs.port)
    actual(f"rj=({res.result},{res.source},{res.reason})")
    assert res.rejected and (res.result, res.source, res.reason) == (1, 1, 3)
    n = pacs.svc.alerts.count("open", "UNREGISTERED_PEER")
    actual(f"UNREGISTERED_PEER 告警数={n}")
    assert n == 1


def test_r2_n2_ip_mismatch(pacs, actual):
    """TC-R2-N2 | 目标: AE 已登记但 IP 不符被拒 | 输入: 登记为 10.0.0.5 的 CT_SPOOF 从 127.0.0.1 请求关联 | 期望: RJ(1,1,3)，告警 PEER_ADDRESS_MISMATCH"""
    res = ModalitySim("CT_SPOOF").echo(pacs.host, pacs.port)
    actual(f"rj=({res.result},{res.source},{res.reason})")
    assert res.rejected and (res.result, res.source, res.reason) == (1, 1, 3)
    assert pacs.svc.alerts.count("open", "PEER_ADDRESS_MISMATCH") == 1
    actual("PEER_ADDRESS_MISMATCH 告警 1 条")


def test_r2_n3_disabled_rejected(pacs, actual):
    """TC-R2-N3 | 目标: 停用条目被拒 | 输入: 停用 CT_SIM_2 后请求关联 | 期望: RJ(1,1,3)，告警 PEER_DISABLED"""
    pacs.svc.whitelist.set_enabled("CT_SIM_2", False, operator="pytest")
    res = ModalitySim("CT_SIM_2").echo(pacs.host, pacs.port)
    actual(f"rj=({res.result},{res.source},{res.reason})")
    assert res.rejected and (res.result, res.source, res.reason) == (1, 1, 3)
    assert pacs.svc.alerts.count("open", "PEER_DISABLED") == 1


def test_r3_p1_query_role_can_find(pacs, ct_study, actual):
    """TC-R3-P1 | 目标: 具备 QUERY 角色的工作站可 C-FIND | 输入: WS_SIM_1 提议 Study Root FIND 并查询 STUDY 级 | 期望: 上下文被接受，最终状态 0x0000"""
    store_all(pacs, "CT_SIM_1", ct_study[2])
    res, matches, final = WorkstationSim("WS_SIM_1", 0).find(
        pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=""))
    actual(f"established={res.established} matches={len(matches)} final=0x{final:04X}")
    assert res.established and final == 0x0000 and len(matches) == 1


def test_r3_n1_store_only_cannot_move(pacs, actual):
    """TC-R3-N1 | 目标: 只有 STORE 角色的设备不能取回 | 输入: CT_SIM_1 提议 Study Root MOVE 上下文 | 期望: 该上下文被拒，告警 UNAUTHORIZED_RETRIEVE"""
    res = propose_only("CT_SIM_1", pacs.host, pacs.port,
                       [Verification, StudyRootQueryRetrieveInformationModelMove])
    move_uid = str(StudyRootQueryRetrieveInformationModelMove)
    actual(f"established={res.established} accepted={res.accepted_abstract} rejected={res.rejected_abstract}")
    assert res.established and move_uid in res.rejected_abstract
    assert move_uid not in res.accepted_abstract
    assert pacs.svc.alerts.count("open", "UNAUTHORIZED_RETRIEVE") == 1
    actual("UNAUTHORIZED_RETRIEVE 告警 1 条")


def test_r3_n2_store_only_cannot_find(pacs, actual):
    """TC-R3-N2 | 目标: 只有 STORE 角色的设备不能查询 | 输入: CT_SIM_1 提议 Study Root FIND 上下文 | 期望: 上下文被拒，审计 CONTEXT_DENIED"""
    res = propose_only("CT_SIM_1", pacs.host, pacs.port,
                       [Verification, StudyRootQueryRetrieveInformationModelFind])
    find_uid = str(StudyRootQueryRetrieveInformationModelFind)
    actual(f"rejected={res.rejected_abstract}")
    assert find_uid in res.rejected_abstract
    assert "CONTEXT_DENIED" in _audit_types(pacs.svc)
    # FIND 被拒只审计、不告警（检索类才告警）
    assert pacs.svc.alerts.count("open", "UNAUTHORIZED_RETRIEVE") == 0


def test_r4_p1_add_peer_effective_without_restart(pacs, ct_study, actual):
    """TC-R4-P1 | 目标: 新增条目立即生效 | 输入: 运行中新增 ENDO_SIM_3（STORE），随后发送 C-STORE | 期望: 不重启即 0x0000，审计 PEER_ADDED"""
    before = ModalitySim("ENDO_SIM_3").echo(pacs.host, pacs.port)
    assert before.rejected
    pacs.svc.whitelist.add("ENDO_SIM_3", "127.0.0.1", ["STORE"], operator="pytest")
    statuses = store_all(pacs, "ENDO_SIM_3", ct_study[2][:1])
    actual(f"新增前 rejected={before.rejected}；新增后 status={[hex(s) for s in statuses]}")
    assert statuses == [0x0000]
    row = [r for r in pacs.svc.audit.recent(200, "PEER_ADDED") if "ENDO_SIM_3" in r["target"]]
    assert row, "缺少 PEER_ADDED 审计"


def test_r4_n2_disable_then_rejected(pacs, actual):
    """TC-R4-N2 | 目标: 停用后新关联被拒 | 输入: 停用 CT_SIM_1 后再次请求关联 | 期望: RJ(1,1,3)，审计 PEER_UPDATED 含 enabled 前后值"""
    assert ModalitySim("CT_SIM_1").echo(pacs.host, pacs.port).established
    pacs.svc.whitelist.set_enabled("CT_SIM_1", False, operator="pytest")
    res = ModalitySim("CT_SIM_1").echo(pacs.host, pacs.port)
    import json
    upd = pacs.svc.audit.recent(50, "PEER_UPDATED")[0]
    d = json.loads(upd["details_json"])
    actual(f"rj=({res.result},{res.source},{res.reason}) before.enabled={d['before']['enabled']} after.enabled={d['after']['enabled']}")
    assert res.rejected and res.reason == 3
    assert d["before"]["enabled"] is True and d["after"]["enabled"] is False
