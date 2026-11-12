"""独立复核后修复的回归用例（Codex 复现：work/repro_integrity_edges.py，结果见 docs/process/independent-review/）。

问题 1：基线 JSON 被改成不可解析 / 结构无效 / 基线缺失时，核查抛异常中断、BASELINE_TAMPERED 告警为 0。
问题 2：入库事务提交后审计锚点写入失败，处理器异常返回 0xC211，索引保留但文件被删（索引指向不存在文件）。
"""

import pytest
from pydicom.uid import CTImageStorage

from conftest import store_all
from pacs_server.sim import synth, tamper
from pacs_server.sim.scu import ModalitySim, WorkstationSim, identifier


def count(svc, sql, params=()):
    return svc.db.scalar(sql, params)


def dcm_files(svc):
    return len(list(svc.config.store_dir.rglob("*.dcm")))


def cats_of(rep, uid):
    return sorted({f["category"] for f in rep["findings"] if f["sop_uid"] == uid})


@pytest.fixture
def lib5(pacs, tmp_path):
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0], "20261105")
    d = synth.ct_series(st, 5, 64, 64)
    assert store_all(pacs, "CT_SIM_1", synth.write_datasets(d, tmp_path / "ct")) == [0] * 5
    return st, d


def _corrupt(svc, uid, column, value):
    with svc.db.write() as conn:
        conn.execute(f"UPDATE baselines SET {column}=? WHERE instance_id="
                     "(SELECT id FROM instances WHERE sop_uid=?)", (value, uid))


# ---------------- 问题 1：基线不可信 ----------------
def test_r14_n2_corrupt_or_missing_baseline_reported_not_aborting(pacs, lib5, actual):
    """TC-R14-N2 | 目标: 基线 JSON 损坏 / 结构无效 / 缺失时报告 BASELINE_TAMPERED 且核查不中断 | 输入: 5 张 CT：A 的 meta_json 改成单个 “{”（Codex 复现）；B 的 tiles_json 改成 “[1]” 并翻转 1 个像素字节；C 的基线行被删除；D 的 meta_json 改成 “[]” 并把 StudyDate 改为 20261106；E 不动；然后全量核查 | 期望: 核查完成，checked=5、ok=1（E）；A={BASELINE_TAMPERED}；B={BASELINE_TAMPERED, PIXEL_CHANGED（无分块位置并注明原因）}；C={BASELINE_TAMPERED（基线记录缺失）}；D={BASELINE_TAMPERED, METADATA_CHANGED（无前值并注明原因）}；BASELINE_TAMPERED 告警 4 条"""
    svc = pacs.svc
    A, B, C, D, E = (x.SOPInstanceUID for x in lib5[1])
    _corrupt(svc, A, "meta_json", "{")
    _corrupt(svc, B, "tiles_json", "[1]")
    tamper.flip_pixel_byte(svc, B)
    with svc.db.write() as conn:
        conn.execute("DELETE FROM baselines WHERE instance_id=(SELECT id FROM instances WHERE sop_uid=?)", (C,))
    _corrupt(svc, D, "meta_json", "[]")
    tamper.set_element(svc, D, "StudyDate", "20261106")
    rep = svc.verifier.run(operator="pytest")
    by = {k: cats_of(rep, u) for k, u in zip("ABCDE", (A, B, C, D, E))}
    det = {(f["sop_uid"], f["category"]): f["details"] for f in rep["findings"]}
    actual(f"checked={rep['checked']} ok={rep['ok']} categories={by}")
    actual(f"A.problems={det[(A, 'BASELINE_TAMPERED')]['problems']} C.problems={det[(C, 'BASELINE_TAMPERED')]['problems']} "
           f"B.regions={det[(B, 'PIXEL_CHANGED')]['regions']} D.changes={det[(D, 'METADATA_CHANGED')]['changes']} "
           f"alerts={svc.alerts.count('open', 'BASELINE_TAMPERED')}")
    assert rep["checked"] == 5 and rep["ok"] == 1
    assert by == {"A": ["BASELINE_TAMPERED"], "B": ["BASELINE_TAMPERED", "PIXEL_CHANGED"],
                  "C": ["BASELINE_TAMPERED"], "D": ["BASELINE_TAMPERED", "METADATA_CHANGED"], "E": []}
    assert any("meta_json 无法解析" in p for p in det[(A, "BASELINE_TAMPERED")]["problems"])
    assert det[(C, "BASELINE_TAMPERED")]["problems"] == ["基线记录缺失"]
    assert det[(B, "PIXEL_CHANGED")]["regions"] == [] and "分块哈希不可用" in det[(B, "PIXEL_CHANGED")]["note"]
    assert det[(D, "METADATA_CHANGED")]["changes"] is None and "无法给出前值" in det[(D, "METADATA_CHANGED")]["note"]
    assert svc.alerts.count("open", "BASELINE_TAMPERED") == 4


def test_r15_n5_single_instance_error_isolated(pacs, lib5, monkeypatch, actual):
    """TC-R15-N5 | 目标: 单实例核查过程异常不阻断其他实例 | 输入: 让第 3 张 CT 的核查过程抛出意外异常（注入），然后全量核查 | 期望: 核查完成，checked=5、ok=4；该实例报告 VERIFY_ERROR（含异常类型），并告警 VERIFY_ERROR 1 条"""
    svc = pacs.svc
    bad = lib5[1][2].SOPInstanceUID
    orig = svc.verifier._verify_instance

    def flaky(row):
        if row["sop_uid"] == bad:
            raise RuntimeError("injected verifier failure")
        return orig(row)

    monkeypatch.setattr(svc.verifier, "_verify_instance", flaky)
    rep = svc.verifier.run(operator="pytest")
    f = [x for x in rep["findings"] if x["sop_uid"] == bad]
    actual(f"checked={rep['checked']} ok={rep['ok']} findings={[(x['category'], x['details']['error']) for x in f]} alerts={svc.alerts.count('open', 'VERIFY_ERROR')}")
    assert rep["checked"] == 5 and rep["ok"] == 4
    assert [x["category"] for x in f] == ["VERIFY_ERROR"] and "RuntimeError" in f[0]["details"]["error"]
    assert svc.alerts.count("open", "VERIFY_ERROR") == 1


def test_r16_n2_corrupt_baseline_blocked_on_retrieve(pacs, lib5, ws1, actual):
    """TC-R16-N2 | 目标: 基线损坏 / 缺失的实例在调阅时被阻断，且取回不中断 | 输入: 第 1 张 meta_json 改成 “{”，第 2 张基线行删除，然后对整个检查执行 C-MOVE | 期望: 最终 0xB000，完成 3、失败 2，Failed SOP Instance UID List 恰为这两张；WS 未收到这两张；RETRIEVE_BLOCKED 告警 2 条"""
    svc = pacs.svc
    a, b = lib5[1][0].SOPInstanceUID, lib5[1][1].SOPInstanceUID
    _corrupt(svc, a, "meta_json", "{")
    with svc.db.write() as conn:
        conn.execute("DELETE FROM baselines WHERE instance_id=(SELECT id FROM instances WHERE sop_uid=?)", (b,))
    _, out = ws1.move(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=lib5[0].study_uid), "WS_SIM_1")
    actual(f"final=0x{out['final']:04X} completed={out['completed']} failed={out['failed']} failed_uids_ok={sorted(out['failed_uids']) == sorted([a, b])} received_bad={[u for u in (a, b) if u in ws1.received]} blocked_alerts={svc.alerts.count('open', 'RETRIEVE_BLOCKED')}")
    assert out["final"] == 0xB000 and out["completed"] == 3 and out["failed"] == 2
    assert sorted(out["failed_uids"]) == sorted([a, b]) and a not in ws1.received and b not in ws1.received
    assert svc.alerts.count("open", "RETRIEVE_BLOCKED") == 2


# ---------------- 问题 2：审计锚点写入失败 ----------------
def _fail_anchor_for(svc, monkeypatch, event_types=None):
    """注入锚点写入失败。event_types=None 表示一律失败；否则只在批次含这些事件类型时失败。"""
    orig = svc.audit.anchor

    def fake(rec):
        recs = rec if isinstance(rec, list) else [rec]
        if event_types is None or any(
                svc.db.scalar("SELECT event_type FROM audit WHERE seq=?", (r["seq"],)) in event_types
                for r in recs):
            raise OSError("injected: audit anchor write failed")
        return orig(rec)

    monkeypatch.setattr(svc.audit, "anchor", fake)
    return orig


def test_r5_n3_anchor_failure_after_commit_rolls_back(pacs, tmp_path, monkeypatch, actual):
    """TC-R5-N3 | 目标: 事务提交后锚点写入失败时撤销本次入库，不留索引与文件 | 输入: 先正常入库 SYN0001 的 1 张 CT；只让“STORE_ACCEPTED”记录的锚点写入失败，再发送 SYN0002 新检查的 1 张 CT；恢复后重发；再对已有序列的另一张注入一次同样的失败 | 期望: 失败时返回 0xA701；SYN0002 的患者 / 检查 / 序列 / 实例行与文件都不存在；审计有 STORE_ACCEPTED 及引用其 seq 的 STORE_ROLLED_BACK；告警 AUDIT_ANCHOR_FAILURE；状态 audit_anchor_ok=False。恢复后重发 0x0000，补写锚点、审计校验 ok 且未锚定 0；对已有序列再次失败时只撤销该实例、保留序列，恢复重发后全量核查 0 异常"""
    svc = pacs.svc
    s1 = synth.SynthStudy(synth.DEFAULT_PATIENTS[0])
    d = synth.write_datasets(synth.ct_series(s1, 3, 64, 64), tmp_path / "s1")
    s2 = synth.SynthStudy(synth.DEFAULT_PATIENTS[1])
    e = synth.write_datasets(synth.ct_series(s2, 1, 64, 64), tmp_path / "s2")
    assert store_all(pacs, "CT_SIM_1", [d[0]]) == [0]
    orig = _fail_anchor_for(svc, monkeypatch, {"STORE_ACCEPTED"})
    st1 = store_all(pacs, "CT_SIM_1", [e[0]])
    rows = {t: count(svc, f"SELECT COUNT(*) FROM {t}") for t in ("patients", "studies", "series", "instances")}
    rb = svc.audit.recent(50, "STORE_ROLLED_BACK")
    import json
    rb_d = json.loads(rb[0]["details_json"]) if rb else {}
    acc_seq = svc.db.scalar("SELECT MAX(seq) FROM audit WHERE event_type='STORE_ACCEPTED'")
    degraded_flag = svc.status()["audit_anchor_ok"]
    actual(f"失败时 status={[hex(x) for x in st1]} rows={rows} files={dcm_files(svc)} rolled_back_ref={rb_d.get('store_accepted_seq')}/accepted_seq={acc_seq} anchor_alert={svc.alerts.count('open', 'AUDIT_ANCHOR_FAILURE')} audit_anchor_ok={degraded_flag}")
    assert st1 == [0xA701]
    assert rows == {"patients": 1, "studies": 1, "series": 1, "instances": 1} and dcm_files(svc) == 1
    assert rb and rb_d["store_accepted_seq"] == acc_seq and rb_d["reason"] == "audit-anchor-write-failed"
    assert svc.alerts.count("open", "AUDIT_ANCHOR_FAILURE") == 1 and degraded_flag is False
    # 恢复：写入恢复后重发，自动补写锚点
    monkeypatch.setattr(svc.audit, "anchor", orig)
    st2 = store_all(pacs, "CT_SIM_1", [e[0], d[1]])
    av = svc.verify_audit(operator="pytest")
    rec = svc.audit.recent(200, "AUDIT_ANCHOR_RECOVERED")
    actual(f"恢复后 status={[hex(x) for x in st2]} instances={count(svc, 'SELECT COUNT(*) FROM instances')} files={dcm_files(svc)} audit_ok={av.ok} unanchored={av.unanchored_entries} recovered_audit={len(rec)} audit_anchor_ok={svc.status()['audit_anchor_ok']}")
    assert st2 == [0, 0] and count(svc, "SELECT COUNT(*) FROM instances") == 3 and dcm_files(svc) == 3
    assert av.ok and av.unanchored_entries == 0 and rec and svc.status()["audit_anchor_ok"] is True
    # 已有序列中的实例：只撤销实例，不删除序列 / 检查 / 患者
    _fail_anchor_for(svc, monkeypatch, {"STORE_ACCEPTED"})
    st3 = store_all(pacs, "CT_SIM_1", [d[2]])
    series_rows = count(svc, "SELECT COUNT(*) FROM series")
    inst3 = count(svc, "SELECT COUNT(*) FROM instances")
    monkeypatch.setattr(svc.audit, "anchor", orig)
    st4 = store_all(pacs, "CT_SIM_1", [d[2]])
    rep = svc.verifier.run(operator="pytest")
    actual(f"已有序列 status={[hex(x) for x in st3]} series={series_rows} instances={inst3}；恢复重发 {[hex(x) for x in st4]}；核查异常={rep['anomalous_instances']}")
    assert st3 == [0xA701] and series_rows == 2 and inst3 == 3
    assert st4 == [0] and rep["anomalous_instances"] == 0 and rep["checked"] == 4


def test_r5_n4_persistent_anchor_failure_then_recovery(pacs, tmp_path, monkeypatch, actual):
    """TC-R5-N4 | 目标: 锚点持续不可写时入库被预先拒绝、只读操作照常，恢复后自动补写并可重发成功 | 输入: 一律让锚点写入失败（同 Codex 复现）后：C-STORE 1 张 CT、C-ECHO、C-FIND、审计校验；然后恢复写入并重发 | 期望: 故障期间 C-STORE 返回 0xA701，索引 0 行、文件 0 个、无处理器异常；ECHO 0x0000，FIND 最终 0x0000；审计校验链 ok 但未锚定 > 0；恢复后重发 0x0000，库中 1 条、文件 1 个，审计校验 ok 且未锚定 0"""
    svc = pacs.svc
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0])
    p = synth.write_datasets(synth.ct_series(st, 1, 32, 32), tmp_path / "ct")
    orig = _fail_anchor_for(svc, monkeypatch, None)
    s1 = store_all(pacs, "CT_SIM_1", p)
    echo = WorkstationSim("WS_SIM_1", 0).echo(pacs.host, pacs.port)
    _, _, ffinal = WorkstationSim("WS_SIM_1", 0).find(pacs.host, pacs.port, identifier("STUDY", StudyInstanceUID=""))
    during = svc.verify_audit(operator="pytest")
    actual(f"故障中 store={[hex(x) for x in s1]} instances={count(svc, 'SELECT COUNT(*) FROM instances')} files={dcm_files(svc)} echo={echo.statuses} find_final=0x{ffinal:04X} audit_chain_ok={during.ok} unanchored={during.unanchored_entries}")
    assert s1 == [0xA701] and count(svc, "SELECT COUNT(*) FROM instances") == 0 and dcm_files(svc) == 0
    assert echo.statuses == [0] and ffinal == 0 and during.ok and during.unanchored_entries > 0
    monkeypatch.setattr(svc.audit, "anchor", orig)
    s2 = store_all(pacs, "CT_SIM_1", p)
    after = svc.verify_audit(operator="pytest")
    actual(f"恢复后 store={[hex(x) for x in s2]} instances={count(svc, 'SELECT COUNT(*) FROM instances')} files={dcm_files(svc)} audit_ok={after.ok} unanchored={after.unanchored_entries} anchor_alert_count={svc.alerts.list()[0]['count'] if svc.alerts.list() else 0}")
    assert s2 == [0] and count(svc, "SELECT COUNT(*) FROM instances") == 1 and dcm_files(svc) == 1
    assert after.ok and after.unanchored_entries == 0


def test_r18_n4_anchor_failure_does_not_crash_other_operations(pacs, monkeypatch, actual):
    """TC-R18-N4 | 目标: 锚点写入失败不使非入库操作抛异常，记录照常入库并在恢复后补锚 | 输入: 一律让锚点写入失败后：新增白名单条目、产生一条告警、未登记 ROGUE 连接；然后恢复并执行审计校验 | 期望: 三项操作都不抛异常（ROGUE 仍收到 RJ(1,1,3)），对应审计 PEER_ADDED / ALERT_RAISED / ASSOCIATION_REJECTED 已写入数据库；告警 AUDIT_ANCHOR_FAILURE；恢复后审计校验 ok、未锚定 0，并记录 AUDIT_ANCHOR_RECOVERED"""
    svc = pacs.svc
    orig = _fail_anchor_for(svc, monkeypatch, None)
    svc.whitelist.add("WS_NEW", "127.0.0.1", ["QUERY"], operator="pytest")
    svc.alerts.raise_alert("STORAGE_FAILURE", "test", {"note": "pytest"}, aggregate_key="t")
    r = ModalitySim("ROGUE").echo(pacs.host, pacs.port)
    types = {row["event_type"] for row in svc.audit.recent(50)}
    missing_before = len(svc.audit.missing_anchor_seqs())
    monkeypatch.setattr(svc.audit, "anchor", orig)
    rep = svc.verify_audit(operator="pytest")
    rec = svc.audit.recent(50, "AUDIT_ANCHOR_RECOVERED")
    actual(f"rogue_rj=({r.result},{r.source},{r.reason}) db_events⊇={sorted(types & {'PEER_ADDED', 'ALERT_RAISED', 'ASSOCIATION_REJECTED'})} unanchored_before={missing_before} anchor_alert={svc.alerts.count('open', 'AUDIT_ANCHOR_FAILURE')} after_ok={rep.ok} unanchored_after={rep.unanchored_entries} recovered={len(rec)}")
    assert r.rejected and (r.result, r.source, r.reason) == (1, 1, 3)
    assert {"PEER_ADDED", "ALERT_RAISED", "ASSOCIATION_REJECTED"} <= types and missing_before > 0
    assert svc.alerts.count("open", "AUDIT_ANCHOR_FAILURE") == 1
    assert rep.ok and rep.unanchored_entries == 0 and rec
