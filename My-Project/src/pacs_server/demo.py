"""端到端演示（pacs demo）：本机回环 + 合成数据，逐步打印证据并自检。

说明：这是本机模拟演示，不是跨组联调；对端都是本项目的 pynetdicom 模拟器。
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from pydicom.uid import CTImageStorage, VLEndoscopicImageStorage

from .config import PacsConfig


def _step(n, title):
    print(f"\n=== 步骤 {n}：{title} ===")


def run_demo(runtime: Path, keep: bool = False) -> int:
    logging.getLogger("pynetdicom").setLevel(logging.CRITICAL)
    # 告警已在步骤中打印并持久化；不再向 stderr 重复输出
    logging.getLogger("pacs").setLevel(logging.ERROR)
    runtime = Path(runtime).resolve()
    if runtime.exists() and not keep:
        if not (runtime / "pacs.json").exists() and any(runtime.iterdir()):
            print(f"拒绝清空 {runtime}：目录非空且不是 PACS runtime（缺少 pacs.json）")
            return 2
        shutil.rmtree(runtime)
    from .service import PacsService
    from .sim import synth, tamper
    from .sim.scu import ModalitySim, WorkstationSim, identifier

    cfg = PacsConfig.load(runtime)
    cfg.save()
    svc = PacsService(cfg)
    checks: list[tuple[str, bool]] = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        print(f"  [{'通过' if cond else '失败'}] {name}")

    host, port = cfg.host, cfg.port
    try:
        _step(1, "登记白名单（AE + IP + 角色）")
        wl = svc.whitelist
        for ae, roles, p in (("CT_SIM_1", ["STORE"], None), ("ENDO_SIM_1", ["STORE"], None),
                             ("WS_SIM_1", ["QUERY", "RETRIEVE", "MOVE_DEST"], port + 1),
                             ("WS_SIM_2", ["QUERY", "RETRIEVE", "MOVE_DEST"], port + 2)):
            if wl.get(ae) is None:
                wl.add(ae, "127.0.0.1", roles, port=p, operator="demo")
        for peer in wl.list():
            print(f"  {peer.ae_title:<11} {peer.host:<10} 端口={peer.port} 角色={','.join(peer.roles)}")

        _step(2, f"启动 PACS {cfg.ae_title}@{host}:{port}")
        print(f"  启动对账：{svc.start(operator='demo')}")

        _step(3, "生成合成数据（SYN0001 CT 20 张 512×512、内镜 5 张；SYN0002 中文姓名 CT 5 张）")
        data = synth.demo_dataset(runtime / "demo-input", ct_slices=20, endo_frames=5)
        for k, v in data.items():
            print(f"  {k}: {len(v)} 个文件")

        _step(4, "CT 与内镜设备并发 C-STORE")
        results = {}

        def send(ae, files):
            results[ae] = ModalitySim(ae).send(files, host, port)

        ts = [threading.Thread(target=send, args=("CT_SIM_1", data["CT_SYN0001"] + data["CT_SYN0002"])),
              threading.Thread(target=send, args=("ENDO_SIM_1", data["ES_SYN0001"]))]
        t0 = time.perf_counter()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        for ae, r in results.items():
            codes = sorted({s for s, _ in r.statuses})
            print(f"  {ae}: {len(r.statuses)} 个实例，状态集合 {[hex(c) for c in codes]}")
        print(f"  耗时 {time.perf_counter() - t0:.1f}s；库内实例数 {svc.status()['instances']}")
        check("全部 C-STORE 返回 0x0000",
              all(s == 0 for r in results.values() for s, _ in r.statuses))

        _step(5, "工作站 C-FIND（Study Root，PatientID=SYN0001）")
        _, matches, final = WorkstationSim("WS_SIM_1", 0).find(host, port, identifier(
            "STUDY", PatientID="SYN0001", StudyInstanceUID="", StudyDate="", ModalitiesInStudy="",
            NumberOfStudyRelatedInstances=""))
        for m in matches:
            print(f"  检查 {m.StudyDate} {m.ModalitiesInStudy} 张数={m.NumberOfStudyRelatedInstances} UID={m.StudyInstanceUID}")
        check("C-FIND 返回 2 个检查且最终 0x0000", final == 0 and len(matches) == 2)
        ct_study = next(m.StudyInstanceUID for m in matches if str(m.ModalitiesInStudy) == "CT")

        _step(6, "C-MOVE 到已登记工作站 WS_SIM_1，并比对收到的哈希")
        ws1 = WorkstationSim("WS_SIM_1", port + 1).start_scp()
        _, out = ws1.move(host, port, identifier("STUDY", StudyInstanceUID=ct_study), "WS_SIM_1")
        same = all(r.dataset_sha256 == svc.verifier.instance_row(u)["dataset_sha256"]
                   for u, r in ws1.received.items())
        print(f"  final=0x{out['final']:04X} 完成={out['completed']} 失败={out['failed']} 收到={len(ws1.received)} 哈希一致={same}")
        check("C-MOVE 完成 20、失败 0、哈希一致",
              out["final"] == 0 and out["completed"] == 20 and same)

        _step(7, "C-GET（WS_SIM_2 取回内镜检查）")
        es_study = next(m.StudyInstanceUID for m in matches if str(m.ModalitiesInStudy) == "ES")
        _, g = WorkstationSim("WS_SIM_2", 0).get(host, port, identifier("STUDY", StudyInstanceUID=es_study),
                                                 [VLEndoscopicImageStorage])
        print(f"  final=0x{g['final']:04X} 完成={g['completed']}")
        check("C-GET 完成 5", g["final"] == 0 and g["completed"] == 5)

        _step(8, "未登记设备 ROGUE 尝试连接；越权目的地 C-MOVE")
        r = ModalitySim("ROGUE").echo(host, port)
        print(f"  ROGUE: rejected={r.rejected} RJ=({r.result},{r.source},{r.reason})")
        _, bad = WorkstationSim("WS_SIM_1", 0).move(host, port, identifier("STUDY", StudyInstanceUID=ct_study),
                                                    "UNKNOWN_WS")
        print(f"  C-MOVE 到 UNKNOWN_WS: final=0x{bad['final']:04X}")
        check("ROGUE 被 RJ(1,1,3) 拒绝", r.rejected and r.reason == 3)
        check("未登记目的地返回 0xA801", bad["final"] == 0xA801)

        _step(9, "全量完整性核查（正常操作之后，期望 0 异常）")
        rep = svc.verifier.run(operator="demo")
        print(f"  核查 {rep['checked']}，异常实例 {rep['anomalous_instances']}")
        check("正常操作后 0 异常", rep["anomalous_instances"] == 0)

        _step(10, "【篡改注入】绕过 PACS 改 1 张 CT 的像素、另 1 张的 StudyDate，然后核查")
        ct_rows = svc.verifier.instance_rows({"study_uid": ct_study})
        t1 = tamper.flip_pixel_byte(svc, ct_rows[2]["sop_uid"])
        t2 = tamper.set_element(svc, ct_rows[4]["sop_uid"], "StudyDate", "20261106")
        print(f"  已改像素：{Path(t1['file']).name}；已改 StudyDate：{t2['before']} -> {t2['after']}")
        rep = svc.verifier.run(operator="demo")
        for f in rep["findings"]:
            d = f["details"]
            extra = d.get("regions") or [c for c in d.get("changes", []) if c.get("key_field")]
            print(f"  [{f['category']}] 实例 {f['sop_uid'][-12:]} -> {extra}")
        cats = sorted({f["category"] for f in rep["findings"]})
        check("检出 PIXEL_CHANGED 与 METADATA_CHANGED", cats == ["METADATA_CHANGED", "PIXEL_CHANGED"])

        _step(11, "再次 C-MOVE：异常影像被阻断")
        ws1.clear()
        _, out2 = ws1.move(host, port, identifier("STUDY", StudyInstanceUID=ct_study), "WS_SIM_1")
        print(f"  final=0x{out2['final']:04X} 完成={out2['completed']} 失败={out2['failed']} 失败列表={len(out2['failed_uids'])}")
        check("阻断 2 张：0xB000、失败 2", out2["final"] == 0xB000 and out2["failed"] == 2)
        ws1.stop_scp()

        _step(12, "审计链校验与导出")
        a = svc.verify_audit(operator="demo")
        print(f"  ok={a.ok} 条数={a.entries} 链头={a.head_seq}:{a.head_hash[:16]}…（可带外记录）")
        n = svc.export_audit(runtime / "audit-export.csv", operator="demo")
        print(f"  已导出 {n} 条 -> {runtime / 'audit-export.csv'}")
        check("审计链完整", a.ok)

        _step(13, "当前告警")
        for al in svc.alerts.list():
            print(f"  #{al['id']} [{al['severity']}] {al['type']} {al['subject'][:60]} x{al['count']}")
        print(f"\n状态：{svc.status()}")
    finally:
        svc.stop(operator="demo")
        svc.db.close()
    passed = sum(1 for _, ok in checks if ok)
    print(f"\n演示自检：{passed}/{len(checks)} 通过")
    return 0 if passed == len(checks) else 1
