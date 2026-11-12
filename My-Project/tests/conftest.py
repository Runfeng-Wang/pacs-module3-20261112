"""测试夹具：在临时 runtime 中启动真实 PACS（127.0.0.1 随机端口），登记模拟对端。

每个测试的 docstring 写成 “编号 | 目标 | 输入 | 期望”，由 autouse 夹具记入 junit 属性 doc；
测试用 actual(...) 记录实际观测值，scripts/make_test_report.py 据此生成用例表。
"""

from __future__ import annotations

import logging
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pacs_server.config import PacsConfig  # noqa: E402
from pacs_server.service import PacsService  # noqa: E402
from pacs_server.sim import synth  # noqa: E402
from pacs_server.sim.scu import ModalitySim, WorkstationSim  # noqa: E402

logging.getLogger("pynetdicom").setLevel(logging.CRITICAL)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Pacs:
    svc: PacsService
    host: str
    port: int
    ws1_port: int
    ws2_port: int
    runtime: Path

    def restart(self):
        self.svc.stop()
        self.svc.db.close()
        self.svc = PacsService(PacsConfig.load(self.runtime))
        self.svc.config.port = self.port
        self.svc.start(operator="pytest")
        return self.svc


PEERS = [
    ("CT_SIM_1", "127.0.0.1", ["STORE"], None),
    ("CT_SIM_2", "127.0.0.1", ["STORE"], None),
    ("ENDO_SIM_1", "127.0.0.1", ["STORE"], None),
    ("ENDO_SIM_2", "127.0.0.1", ["STORE"], None),
    ("CT_SPOOF", "10.0.0.5", ["STORE"], None),
]


def make_pacs(tmp_path: Path, **cfg_overrides) -> Pacs:
    runtime = tmp_path / "runtime"
    cfg = PacsConfig(runtime_dir=runtime, port=free_port())
    for k, v in cfg_overrides.items():
        setattr(cfg, k, v)
    cfg.save()
    svc = PacsService(cfg)
    ws1, ws2 = free_port(), free_port()
    for ae, host, roles, port in PEERS:
        svc.whitelist.add(ae, host, roles, port=port, operator="pytest")
    svc.whitelist.add("WS_SIM_1", "127.0.0.1", ["QUERY", "RETRIEVE", "MOVE_DEST"], port=ws1,
                      operator="pytest")
    svc.whitelist.add("WS_SIM_2", "127.0.0.1", ["QUERY", "RETRIEVE", "MOVE_DEST"], port=ws2,
                      operator="pytest")
    svc.start(operator="pytest")
    return Pacs(svc, "127.0.0.1", cfg.port, ws1, ws2, runtime)


@pytest.fixture
def pacs(tmp_path):
    p = make_pacs(tmp_path)
    yield p
    p.svc.stop()
    p.svc.db.close()


@pytest.fixture
def ws1(pacs):
    w = WorkstationSim("WS_SIM_1", pacs.ws1_port).start_scp()
    yield w
    w.stop_scp()


@pytest.fixture
def ws2(pacs):
    w = WorkstationSim("WS_SIM_2", pacs.ws2_port).start_scp()
    yield w
    w.stop_scp()


@pytest.fixture
def ct_study(tmp_path):
    """SYN0001 的小型 CT 检查（5 张 128×128，Explicit VR LE）。"""
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0], "20261105", "093000", "ACC1001",
                          "SYNTHETIC CT CHEST")
    dsets = synth.ct_series(st, 5, 128, 128)
    paths = synth.write_datasets(dsets, tmp_path / "ct")
    return st, dsets, paths


@pytest.fixture
def ct_sim():
    return ModalitySim("CT_SIM_1")


@pytest.fixture(autouse=True)
def _doc_property(request, record_property):
    doc = (request.function.__doc__ or "").strip()
    record_property("doc", " ".join(doc.split()))


@pytest.fixture
def actual(record_property):
    parts: list[str] = []

    def _rec(text: str):
        parts.append(str(text))
        record_property("actual", "；".join(parts))

    return _rec


def store_all(pacs: Pacs, ae: str, paths) -> list:
    res = ModalitySim(ae).send(list(paths), pacs.host, pacs.port)
    assert res.established, f"{ae} 关联未建立: {res}"
    return [s for s, _ in res.statuses]
