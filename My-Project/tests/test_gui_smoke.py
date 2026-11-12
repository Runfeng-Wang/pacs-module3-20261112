"""R20：PyQt5 管理控制台离屏冒烟（证据级别 E4：自动化冒烟，不等同于真人验收 H）。"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 平台默认没有字体库：指向 Windows 字体目录，否则截图中文字为空白
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PyQt5.QtWidgets import QApplication, QPushButton  # noqa: E402

from conftest import store_all  # noqa: E402
from pacs_server.gui.app import TABS, ConsoleWindow  # noqa: E402
from pacs_server.sim import synth, tamper  # noqa: E402
from pacs_server.sim.scu import ModalitySim  # noqa: E402

pytestmark = pytest.mark.gui
SHOTS = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "screenshots"


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtGui import QFont
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei", 9))
    yield app


@pytest.fixture
def console(qapp, pacs, tmp_path):
    p1, p2 = synth.DEFAULT_PATIENTS[0], synth.DEFAULT_PATIENTS[1]
    st = synth.SynthStudy(p1, "20261105", "093000", "ACC1001", "SYNTHETIC CT CHEST")
    ct = synth.ct_series(st, 4, 256, 256)
    store_all(pacs, "CT_SIM_1", synth.write_datasets(ct, tmp_path / "ct"))
    store_all(pacs, "ENDO_SIM_1", synth.write_datasets(
        synth.endo_series(synth.SynthStudy(p1, "20261108", description="SYNTHETIC ENDOSCOPY"), 2),
        tmp_path / "es"))
    store_all(pacs, "CT_SIM_2", synth.write_datasets(
        synth.ct_series(synth.SynthStudy(p2, "20261110", description="SYNTHETIC CT HEAD"), 2, 128, 128),
        tmp_path / "ct2"))
    ModalitySim("ROGUE").echo(pacs.host, pacs.port)          # 产生一条告警
    tamper.flip_pixel_byte(pacs.svc, ct[1].SOPInstanceUID)    # 供核查页显示
    win = ConsoleWindow(pacs.svc, operator="smoke-test")
    win.resize(1280, 800)
    yield win, ct
    win.timer.stop()
    win.close()


def test_r20_p1_all_tabs_render_with_data(console, qapp, actual):
    """TC-R20-P1 | 目标: 离屏冒烟：六个页面都显示测试数据并截图 | 输入: QT_QPA_PLATFORM=offscreen，有测试数据的 runtime，依次切换六页，在影像库选中一张 CT、在核查页运行全量核查 | 期望: 白名单行数、患者树节点、告警行、审计行、核查异常行均 > 0；每页保存截图"""
    win, ct = console
    SHOTS.mkdir(parents=True, exist_ok=True)
    win.refresh_all()
    # 影像库：选中第一张 CT 显示预览
    root = win.tree.topLevelItem(0)
    inst = root.child(0).child(0).child(0)
    win.tree.setCurrentItem(inst)
    win.show_instance(inst.data(0, 0x0100))
    rep = win.run_verify(None)
    win.verify_audit()
    counts = {
        "peers": win.peer_table.rowCount(), "patients": win.tree.topLevelItemCount(),
        "alerts": win.alert_table.rowCount(), "audit": win.audit_table.rowCount(),
        "findings": win.verify_table.rowCount(),
    }
    saved = []
    for i, name in enumerate(TABS):
        win.tabs.setCurrentIndex(i)
        qapp.processEvents()
        path = SHOTS / f"{i + 1:02d}-{name}.png"
        assert win.grab().save(str(path)), f"截图失败 {path}"
        saved.append(path.name)
    has_preview = win.preview.pixmap() is not None and not win.preview.pixmap().isNull()
    view_audit = len(win.svc.audit.recent(50, "ADMIN_VIEW"))
    actual(f"counts={counts} preview={has_preview} ADMIN_VIEW 审计={view_audit} screenshots={saved}")
    assert all(v > 0 for v in counts.values()), counts
    assert has_preview and view_audit >= 1 and rep["anomalous_instances"] == 1


def test_r20_n1_gui_rejects_invalid_peer(console, actual):
    """TC-R20-N1 | 目标: 界面拒绝非法白名单输入 | 输入: 在白名单表单提交 17 个字符的 AE Title | 期望: 显示错误信息，白名单行数不变"""
    win, _ = console
    before = win.peer_table.rowCount()
    win.f_ae.setText("A" * 17)
    win.f_ip.setText("127.0.0.1")
    win.f_roles["STORE"].setChecked(True)
    win.peer_add()
    actual(f"message={win.peer_msg.text()!r} rows_before={before} rows_after={win.peer_table.rowCount()}")
    assert "16" in win.peer_msg.text() and win.peer_table.rowCount() == before


def test_r20_n2_audit_tab_read_only(console, actual):
    """TC-R20-N2 | 目标: 审计页没有修改或删除入口 | 输入: 检查审计页全部按钮文字与表格编辑属性 | 期望: 不存在删除 / 编辑 / 修改类按钮，表格不可编辑"""
    win, _ = console
    texts = [b.text() for b in win.audit_tab.findChildren(QPushButton)]
    editable = int(win.audit_table.editTriggers())
    actual(f"buttons={texts} editTriggers={editable}")
    assert not any(k in t for t in texts for k in ("删除", "编辑", "修改", "清空"))
    assert editable == 0
