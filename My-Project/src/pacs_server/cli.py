"""命令行入口：pacs <子命令>。runtime 目录默认 ./runtime，可用 --runtime 或环境变量 PACS_RUNTIME 指定。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .config import PacsConfig


def _runtime(args) -> Path:
    return Path(args.runtime or os.environ.get("PACS_RUNTIME") or "runtime").resolve()


def _svc(args):
    from .service import PacsService
    return PacsService(PacsConfig.load(_runtime(args)))


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


# ---------------- 子命令 ----------------
def cmd_init(args):
    cfg = PacsConfig.load(_runtime(args))
    if args.ae:
        cfg.ae_title = args.ae
    if args.port:
        cfg.port = args.port
    if args.host:
        cfg.host = args.host
    if args.policy:
        cfg.retrieve_policy = args.policy
    cfg.save()
    from .util import load_or_create_key
    load_or_create_key(cfg.key_dir)
    print(f"runtime 已初始化：{cfg.runtime_dir}")
    _print(cfg.to_json())


def cmd_serve(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("pynetdicom").setLevel(logging.WARNING)
    svc = _svc(args)
    summary = svc.start(operator=args.operator)
    print(f"PACS {svc.config.ae_title} 正在 {svc.config.host}:{svc.config.port} 监听（Ctrl+C 停止）")
    print(f"启动对账：{summary}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop(operator=args.operator)
        print("已停止")


def cmd_status(args):
    import socket
    svc = _svc(args)
    st = svc.status()
    # CLI 不运行服务：running / active_associations 只反映本进程，改为探测端口是否在监听
    st.pop("running", None)
    st.pop("active_associations", None)
    with socket.socket() as s:
        s.settimeout(1.0)
        st["port_listening"] = s.connect_ex((svc.config.host, svc.config.port)) == 0
    _print(st)


def cmd_peer(args):
    svc = _svc(args)
    wl = svc.whitelist
    op = args.operator
    from .whitelist import WhitelistError
    try:
        if args.action == "list":
            _print([p.to_dict() for p in wl.list()])
        elif args.action == "add":
            _print(wl.add(args.ae, args.ip, args.roles, port=args.port,
                          description=args.description or "", operator=op).to_dict())
        elif args.action == "update":
            fields = {}
            if args.ip:
                fields["host"] = args.ip
            if args.port is not None:
                fields["port"] = args.port
            if args.roles:
                fields["roles"] = args.roles
            if args.description is not None:
                fields["description"] = args.description
            _print(wl.update(args.ae, operator=op, **fields).to_dict())
        elif args.action in ("enable", "disable"):
            _print(wl.set_enabled(args.ae, args.action == "enable", operator=op).to_dict())
        elif args.action == "remove":
            wl.remove(args.ae, operator=op)
            print(f"已删除 {args.ae}")
    except WhitelistError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(2)


def cmd_verify(args):
    svc = _svc(args)
    scope = {k: v for k, v in (("study_uid", args.study), ("sop_uid", args.sop),
                               ("patient_id", args.patient)) if v}
    rep = svc.verifier.run(scope or None, trigger="manual", operator=args.operator)
    print(f"核查 {rep['checked']} 个实例：正常 {rep['ok']}，异常实例 {rep['anomalous_instances']}")
    for f in rep["findings"]:
        d = dict(f["details"])
        d.pop("context", None)
        print(f"- [{f['category']}] 患者 {f['patient_id']} 检查 {f['study_uid']} 实例 {f['sop_uid']}")
        print("  " + json.dumps(d, ensure_ascii=False, default=str)[:600])
    sys.exit(1 if rep["findings"] else 0)


def cmd_alerts(args):
    svc = _svc(args)
    if args.action == "ack":
        ok = svc.alerts.ack(args.id, args.operator)
        print("已确认" if ok else "未找到该 open 告警")
        return
    rows = svc.alerts.list(args.status)
    for r in rows:
        print(f"#{r['id']} {r['created_at']} [{r['severity']}] {r['type']} {r['subject']} "
              f"x{r['count']} {r['status']}\n   建议：{r['suggestion']}\n   详情：{r['details_json'][:400]}")
    print(f"共 {len(rows)} 条")


def cmd_audit(args):
    svc = _svc(args)
    if args.action == "verify":
        rep = svc.verify_audit(args.expect_head, operator=args.operator)
        _print(rep.to_dict())
        sys.exit(0 if rep.ok else 1)
    elif args.action == "head":
        print(svc.audit.head_string())
    elif args.action == "export":
        n = svc.export_audit(Path(args.out), operator=args.operator)
        print(f"已导出 {n} 条到 {args.out}")
    elif args.action == "tail":
        for r in reversed(svc.audit.recent(args.n)):
            print(f"{r['seq']:>6} {r['ts_local']} {r['event_type']:<26} {r['actor']:<12} "
                  f"{r['actor_ip']:<15} {r['target'][:40]:<40} {r['outcome']} {r['details_json'][:160]}")


def cmd_synth(args):
    from .sim import synth
    out = synth.demo_dataset(Path(args.out), ct_slices=args.ct, endo_frames=args.endo,
                             ct_size=args.size)
    for k, v in out.items():
        print(f"{k}: {len(v)} 个文件 -> {v[0].parent}")


def cmd_sim(args):
    from .sim.scu import ModalitySim, WorkstationSim, identifier
    host, port = args.host, args.port
    if args.action == "echo":
        print(ModalitySim(args.ae, args.called).echo(host, port))
    elif args.action == "store":
        files = []
        for f in args.files:
            p = Path(f)
            files += sorted(p.rglob("*.dcm")) if p.is_dir() else [p]
        res = ModalitySim(args.ae, args.called).send(files, host, port)
        print(f"established={res.established} rejected={res.rejected} rj=({res.result},{res.source},{res.reason})")
        for f, (st, c) in zip(files, res.statuses):
            print(f"  {f.name}: 0x{(st if st is not None else 0xFFFF):04X} {c}")
    elif args.action == "find":
        keys = dict(k.split("=", 1) for k in args.key)
        res, matches, final = WorkstationSim(args.ae, 0, args.called).find(
            host, port, identifier(args.level, **keys), model=args.model)
        print(f"established={res.established} final={final if final is None else hex(final)} matches={len(matches)}")
        for m in matches:
            print("  " + " | ".join(f"{e.keyword}={e.value}" for e in m if e.keyword))
    elif args.action in ("move", "get"):
        keys = dict(k.split("=", 1) for k in args.key)
        ws = WorkstationSim(args.ae, args.listen or 0, args.called)
        if args.action == "move":
            if args.listen:
                ws.start_scp()
            res, out = ws.move(host, port, identifier(args.level, **keys), args.dest or args.ae,
                               model=args.model)
            time.sleep(0.5)
            if args.listen:
                ws.stop_scp()
                out["received_by_local_scp"] = len(ws.received)
        else:
            from pydicom.uid import CTImageStorage, SecondaryCaptureImageStorage, VLEndoscopicImageStorage
            res, out = ws.get(host, port, identifier(args.level, **keys),
                              [CTImageStorage, VLEndoscopicImageStorage, SecondaryCaptureImageStorage],
                              model=args.model)
            out["received"] = len(out["received"])
        print(f"established={res.established}")
        _print({k: (hex(v) if k == "final" and v is not None else v) for k, v in out.items()})


def cmd_tamper(args):
    from .sim import tamper
    svc = _svc(args)
    print("【演示用篡改注入：绕过 PACS 直接改库内数据】")
    if args.kind == "pixel":
        _print(tamper.flip_pixel_byte(svc, args.sop))
    elif args.kind == "studydate":
        _print(tamper.set_element(svc, args.sop, "StudyDate", args.value or "20260101"))
    elif args.kind == "delete":
        _print(tamper.delete_file(svc, args.sop))
    elif args.kind == "index-name":
        _print(tamper.edit_index_patient_name(svc, args.patient, args.value or "FAKE^NAME"))


def cmd_gui(args):
    from .gui.app import run_gui
    sys.exit(run_gui(_runtime(args), autostart=args.autostart))


def cmd_demo(args):
    from .demo import run_demo
    sys.exit(run_demo(_runtime(args), keep=args.keep))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pacs", description="实验性 PACS 图像服务器原型（模块三项目 B）")
    p.add_argument("--runtime", help="runtime 目录（默认 ./runtime 或环境变量 PACS_RUNTIME）")
    p.add_argument("--operator", default=os.environ.get("PACS_OPERATOR") or "cli-operator",
                   help="操作员名（只记录、不认证）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="初始化 runtime 与配置")
    s.add_argument("--ae"); s.add_argument("--port", type=int); s.add_argument("--host")
    s.add_argument("--policy", choices=["block", "allow"])
    s.set_defaults(func=cmd_init)
    sub.add_parser("serve", help="前台运行 DICOM SCP").set_defaults(func=cmd_serve)
    sub.add_parser("status", help="显示状态与计数").set_defaults(func=cmd_status)

    s = sub.add_parser("peer", help="白名单维护")
    s.add_argument("action", choices=["list", "add", "update", "enable", "disable", "remove"])
    s.add_argument("--ae"); s.add_argument("--ip"); s.add_argument("--port", type=int)
    s.add_argument("--roles", nargs="*", help="STORE QUERY RETRIEVE MOVE_DEST")
    s.add_argument("--description")
    s.set_defaults(func=cmd_peer)

    s = sub.add_parser("verify", help="完整性核查（有异常时退出码 1）")
    s.add_argument("--study"); s.add_argument("--sop"); s.add_argument("--patient")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("alerts", help="告警列表 / 确认")
    s.add_argument("action", choices=["list", "ack"], nargs="?", default="list")
    s.add_argument("--status", choices=["open", "acknowledged"])
    s.add_argument("--id", type=int)
    s.set_defaults(func=cmd_alerts)

    s = sub.add_parser("audit", help="审计校验 / 链头 / 导出 / 查看")
    s.add_argument("action", choices=["verify", "head", "export", "tail"])
    s.add_argument("--expect-head", help="带外记录的链头 SEQ:HASH")
    s.add_argument("--out", default="audit-export.csv")
    s.add_argument("-n", type=int, default=30)
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("synth", help="生成合成 DICOM（不含真实患者数据）")
    s.add_argument("out"); s.add_argument("--ct", type=int, default=20)
    s.add_argument("--endo", type=int, default=5); s.add_argument("--size", type=int, default=512)
    s.set_defaults(func=cmd_synth)

    s = sub.add_parser("sim", help="本机回环模拟对端（真实 DICOM）")
    s.add_argument("action", choices=["echo", "store", "find", "move", "get"])
    s.add_argument("--ae", required=True); s.add_argument("--called", default="PACS_B")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=11112)
    s.add_argument("--level", default="STUDY"); s.add_argument("--model", default="S", choices=["S", "P"])
    s.add_argument("--key", action="append", default=[], help="K=V，可重复")
    s.add_argument("--dest", help="C-MOVE 目的地 AE"); s.add_argument("--listen", type=int,
                                                                  help="本地存储 SCP 端口（move 时）")
    s.add_argument("files", nargs="*")
    s.set_defaults(func=cmd_sim)

    s = sub.add_parser("tamper", help="演示用篡改注入（绕过系统直接改库）")
    s.add_argument("kind", choices=["pixel", "studydate", "delete", "index-name"])
    s.add_argument("--sop"); s.add_argument("--patient"); s.add_argument("--value")
    s.set_defaults(func=cmd_tamper)

    s = sub.add_parser("gui", help="PyQt5 管理控制台")
    s.add_argument("--autostart", action="store_true", help="打开后立即启动内嵌服务")
    s.set_defaults(func=cmd_gui)

    s = sub.add_parser("demo", help="端到端演示脚本（新建 runtime，打印每一步证据）")
    s.add_argument("--keep", action="store_true", help="保留已有 runtime（默认清空后重建）")
    s.set_defaults(func=cmd_demo)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
