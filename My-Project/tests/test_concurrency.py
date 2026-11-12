"""R12：并发接收与并发调阅（实验阈值，未经医院客户确认；E2）。"""

import threading
import time

import pytest
from pydicom import dcmread
from pydicom.uid import CTImageStorage

from conftest import make_pacs, store_all
from pacs_server.sim import synth
from pacs_server.sim.scu import ModalitySim, WorkstationSim, file_dataset_bytes, identifier, sha256

pytestmark = pytest.mark.concurrency


def test_cc1_concurrent_ingest_and_retrieve(tmp_path, actual):
    """TC-CC1-P1 | 目标: 并发接收与并发取回 | 输入: CT_SIM_1/2 各 100 张 512×512 CT、ENDO_SIM_1/2 各 25 张 640×480 内镜同时发送；同时 WS_SIM_1 做 3 轮 C-FIND+C-MOVE、WS_SIM_2 做 3 轮 C-FIND+C-GET（对象为预置的 100 张检查） | 期望: 250 个 C-STORE 全 0x0000，库中新增 250；每轮取回完成 100 失败 0 且哈希一致；总耗时 ≤ 300 s 并记录实际值"""
    pacs = make_pacs(tmp_path)
    try:
        pre = synth.SynthStudy(synth.DEFAULT_PATIENTS[1], "20261110")
        pre_d = synth.ct_series(pre, 100, 128, 128)
        assert store_all(pacs, "CT_SIM_2", synth.write_datasets(pre_d, tmp_path / "pre")) == [0] * 100
        base_count = pacs.svc.db.scalar("SELECT COUNT(*) FROM instances")
        sets = {
            "CT_SIM_1": synth.write_datasets(synth.ct_series(
                synth.SynthStudy(synth.DEFAULT_PATIENTS[0]), 100, 512, 512), tmp_path / "c1"),
            "CT_SIM_2": synth.write_datasets(synth.ct_series(
                synth.SynthStudy(synth.DEFAULT_PATIENTS[2]), 100, 512, 512), tmp_path / "c2"),
            "ENDO_SIM_1": synth.write_datasets(synth.endo_series(
                synth.SynthStudy(synth.DEFAULT_PATIENTS[0]), 25), tmp_path / "e1"),
            "ENDO_SIM_2": synth.write_datasets(synth.endo_series(
                synth.SynthStudy(synth.DEFAULT_PATIENTS[3]), 25), tmp_path / "e2"),
        }
        ws1 = WorkstationSim("WS_SIM_1", pacs.ws1_port).start_scp()
        statuses: dict[str, list] = {}
        retrieve: dict[str, list] = {"WS_SIM_1": [], "WS_SIM_2": []}
        errors: list[str] = []

        def sender(ae, paths):
            try:
                statuses[ae] = store_all(pacs, ae, paths)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ae}: {exc}")

        def ws_move():
            try:
                for _ in range(3):
                    ws1.clear()
                    _, m, f = ws1.find(pacs.host, pacs.port,
                                       identifier("STUDY", StudyInstanceUID=pre.study_uid,
                                                  NumberOfStudyRelatedInstances=""))
                    _, out = ws1.move(pacs.host, pacs.port,
                                      identifier("STUDY", StudyInstanceUID=pre.study_uid), "WS_SIM_1")
                    bad = sum(1 for d in pre_d if ws1.received.get(d.SOPInstanceUID) is None or
                              ws1.received[d.SOPInstanceUID].dataset_sha256 !=
                              pacs.svc.verifier.instance_row(d.SOPInstanceUID)["dataset_sha256"])
                    retrieve["WS_SIM_1"].append((f, int(m[0].NumberOfStudyRelatedInstances),
                                                 out["final"], out["completed"], out["failed"], bad))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"WS_SIM_1: {exc}")

        def ws_get():
            try:
                w = WorkstationSim("WS_SIM_2", 0)
                for _ in range(3):
                    _, m, f = w.find(pacs.host, pacs.port,
                                     identifier("STUDY", StudyInstanceUID=pre.study_uid,
                                                NumberOfStudyRelatedInstances=""))
                    _, out = w.get(pacs.host, pacs.port,
                                   identifier("STUDY", StudyInstanceUID=pre.study_uid), [CTImageStorage])
                    bad = sum(1 for u, r in out["received"].items() if r.dataset_sha256 !=
                              pacs.svc.verifier.instance_row(u)["dataset_sha256"])
                    retrieve["WS_SIM_2"].append((f, int(m[0].NumberOfStudyRelatedInstances),
                                                 out["final"], out["completed"], out["failed"], bad))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"WS_SIM_2: {exc}")

        threads = [threading.Thread(target=sender, args=(ae, p)) for ae, p in sets.items()]
        threads += [threading.Thread(target=ws_move), threading.Thread(target=ws_get)]
        echo_latency = []
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        time.sleep(1.0)
        e0 = time.perf_counter()
        echo = WorkstationSim("WS_SIM_1", 0).echo(pacs.host, pacs.port)
        echo_latency.append(time.perf_counter() - e0)
        for t in threads:
            t.join(timeout=600)
        elapsed = time.perf_counter() - t0
        ws1.stop_scp()
        added = pacs.svc.db.scalar("SELECT COUNT(*) FROM instances") - base_count
        all_ok = all(s == [0] * len(sets[ae]) for ae, s in statuses.items()) and len(statuses) == 4
        # 发送端原样字节与库内基线一致（抽查每台设备第 1 个）
        spot = all(sha256(file_dataset_bytes(p[0])) == pacs.svc.verifier.instance_row(
            str(dcmread(p[0], stop_before_pixels=True).SOPInstanceUID))["dataset_sha256"]
            for p in sets.values())
        actual(f"elapsed={elapsed:.1f}s added={added} all_store_success={all_ok} spot_hash_ok={spot} echo_during_load={echo.statuses} latency={echo_latency[0]:.2f}s")
        actual(f"WS_SIM_1(find,n,final,completed,failed,hash_bad)={retrieve['WS_SIM_1']}")
        actual(f"WS_SIM_2={retrieve['WS_SIM_2']} errors={errors} alerts={pacs.svc.alerts.count(None)}")
        assert not errors and all_ok and added == 250 and spot
        for rounds in retrieve.values():
            assert len(rounds) == 3
            assert all(r == (0, 100, 0, 100, 0, 0) for r in rounds), rounds
        assert echo.statuses == [0] and echo_latency[0] < 2.0
        assert elapsed <= 300
        assert pacs.svc.alerts.count(None) == 0
    finally:
        pacs.svc.stop()
        pacs.svc.db.close()


def test_cc3_association_limit(tmp_path, actual):
    """TC-CC3-N1 | 目标: 超过关联上限被临时拒绝 | 输入: max_associations=8，同时保持 9 个关联 | 期望: 至少 1 个收到 A-ASSOCIATE-RJ (2,3,2)；已建立的关联仍能 C-ECHO；释放后重试成功；拒绝被审计"""
    pacs = make_pacs(tmp_path, max_associations=8)
    try:
        from pynetdicom import AE
        from pynetdicom.sop_class import Verification
        held, rejected = [], []
        for _ in range(9):
            ae = AE(ae_title="WS_SIM_1")
            ae.add_requested_context(Verification)
            a = ae.associate(pacs.host, pacs.port, ae_title="PACS_B")
            if a.is_established:
                held.append(a)
            elif a.is_rejected:
                p = a.acceptor.primitive
                rejected.append((p.result, p.result_source, p.diagnostic))
        still = [int(held[0].send_c_echo().Status)] if held else []
        for a in held:
            a.release()
        time.sleep(0.5)
        retry = WorkstationSim("WS_SIM_1", 0).echo(pacs.host, pacs.port)
        rj_audit = [r for r in pacs.svc.audit.recent(100, "ASSOCIATION_REJECTED") if '"reason":2' in r["details_json"]]
        actual(f"held={len(held)} rejected={rejected} echo_on_held={still} retry={retry.statuses} rj_audit={len(rj_audit)}")
        assert len(held) == 8 and rejected and all(r == (2, 3, 2) for r in rejected)
        assert still == [0] and retry.statuses == [0] and rj_audit
    finally:
        pacs.svc.stop()
        pacs.svc.db.close()


def test_cc4_same_uid_race(pacs, tmp_path, actual):
    """TC-CC4-N1 | 目标: 同 UID 并发竞争 | 输入: 两个 SCU 同时发送同一 SOP Instance UID：(a) 内容相同；(b) 内容不同 | 期望: (a) 两者成功、库中 1 条；(b) 恰好 1 个 0x0000、1 个 0xC010，库中 1 条"""
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0])
    d = synth.ct_series(st, 2, 256, 256)
    p_same = synth.write_datasets([d[0]], tmp_path / "same")[0]
    other = d[1].copy()
    other.SOPInstanceUID = d[1].SOPInstanceUID
    a_path = synth.write_datasets([d[1]], tmp_path / "a")[0]
    ds_b = dcmread(a_path)
    px = bytearray(ds_b.PixelData)
    px[0] ^= 0xFF
    ds_b.PixelData = bytes(px)
    b_path = tmp_path / "b.dcm"
    ds_b.save_as(b_path, enforce_file_format=True)

    def race(paths):
        barrier = threading.Barrier(2)
        out = {}

        def go(name, p):
            barrier.wait()
            out[name] = ModalitySim(name).send([p], pacs.host, pacs.port).statuses[0][0]

        ts = [threading.Thread(target=go, args=(n, p)) for n, p in zip(("CT_SIM_1", "CT_SIM_2"), paths)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return out

    same = race([p_same, p_same])
    n_same = pacs.svc.db.scalar("SELECT COUNT(*) FROM instances WHERE sop_uid=?", (d[0].SOPInstanceUID,))
    diff = race([a_path, b_path])
    n_diff = pacs.svc.db.scalar("SELECT COUNT(*) FROM instances WHERE sop_uid=?", (d[1].SOPInstanceUID,))
    actual(f"相同内容={same} 库中={n_same}；不同内容={diff} 库中={n_diff}")
    assert sorted(same.values()) == [0, 0] and n_same == 1
    assert sorted(diff.values()) == [0, 0xC010] and n_diff == 1
