"""PyQt5 管理控制台（R20）：服务状态、白名单、影像库、完整性核查、告警、审计。

操作员姓名只记录、不认证（本期无登录 / RBAC）。审计页只读：没有任何修改或删除入口。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QImage, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFormLayout,
                             QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMainWindow, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                             QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

from ..config import PacsConfig
from ..whitelist import ROLES, WhitelistError

TABS = ["服务状态", "白名单", "影像库", "完整性核查", "告警", "审计"]


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    return t


def _fill(t: QTableWidget, rows: list[list], colors: list | None = None):
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            item = QTableWidgetItem("" if v is None else str(v))
            if colors and colors[r]:
                item.setBackground(QColor(colors[r]))
            t.setItem(r, c, item)


def _first(v, default: float) -> float:
    if v is None:
        return default
    try:
        from pydicom.multival import MultiValue
        if isinstance(v, (MultiValue, list, tuple)):
            v = v[0]
        return float(v)
    except (TypeError, ValueError, IndexError):
        return default


def render_preview(ds, size: int = 256) -> QImage | None:
    """未压缩 CT（按窗宽窗位）、灰度或 RGB 的预览；压缩数据不预览。"""
    try:
        if ds.file_meta.TransferSyntaxUID.is_compressed:
            return None
        rows, cols = int(ds.Rows), int(ds.Columns)
        spp = int(getattr(ds, "SamplesPerPixel", 1))
        bits = int(ds.BitsAllocated)
        raw = bytes(ds.PixelData)
        if spp == 3 and bits == 8:
            arr = np.frombuffer(raw, np.uint8)[: rows * cols * 3].reshape(rows, cols, 3).copy()
            img = QImage(arr.data, cols, rows, cols * 3, QImage.Format_RGB888).copy()
        else:
            dtype = {8: np.uint8, 16: np.int16 if int(ds.PixelRepresentation) else np.uint16}[bits]
            arr = np.frombuffer(raw, dtype)[: rows * cols].reshape(rows, cols).astype(np.float32)
            arr = arr * float(getattr(ds, "RescaleSlope", 1) or 1) + float(
                getattr(ds, "RescaleIntercept", 0) or 0)
            wc = _first(getattr(ds, "WindowCenter", None), float(arr.mean()))
            ww = _first(getattr(ds, "WindowWidth", None), float(np.ptp(arr) or 1))
            g = np.clip((arr - (wc - ww / 2)) / max(ww, 1) * 255, 0, 255).astype(np.uint8)
            g = np.ascontiguousarray(g)
            img = QImage(g.data, cols, rows, cols, QImage.Format_Grayscale8).copy()
        return img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:  # noqa: BLE001
        return None


class ConsoleWindow(QMainWindow):
    def __init__(self, svc, operator: str = "gui-operator"):
        super().__init__()
        self.svc = svc
        self.setWindowTitle(f"PACS B 管理控制台（实验原型）— {svc.config.runtime_dir}")
        self.resize(1200, 760)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.operator = QLineEdit(operator)
        self._build_status()
        self._build_peers()
        self._build_library()
        self._build_verify()
        self._build_alerts()
        self._build_audit()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_live)
        self.timer.start(2000)
        self.refresh_all()

    def op(self) -> str:
        return self.operator.text().strip() or "gui-operator"

    # ---------------- 服务状态 ----------------
    def _build_status(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        box = QGroupBox("服务")
        grid = QGridLayout(box)
        self.status_labels: dict[str, QLabel] = {}
        keys = [("ae_title", "AE Title"), ("host", "监听地址"), ("port", "端口"), ("running", "运行中"),
                ("active_associations", "活动关联"), ("max_associations", "关联上限"),
                ("retrieve_policy", "调阅异常策略"), ("patients", "患者"), ("studies", "检查"),
                ("series", "序列"), ("instances", "实例"), ("open_alerts", "未处理告警"),
                ("audit_head", "审计链头"), ("audit_anchor_ok", "审计锚点正常")]
        for i, (k, label) in enumerate(keys):
            grid.addWidget(QLabel(label + "："), i, 0)
            lab = QLabel("-")
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(lab, i, 1)
            self.status_labels[k] = lab
        lay.addWidget(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("操作员（只记录、不认证）："))
        row.addWidget(self.operator)
        self.btn_start = QPushButton("启动服务")
        self.btn_stop = QPushButton("停止服务")
        self.btn_start.clicked.connect(self.start_service)
        self.btn_stop.clicked.connect(self.stop_service)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        lay.addLayout(row)
        note = QLabel("说明：本控制台为实验原型；告警只在本地显示，不对外推送；所有数据均为合成数据。")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        self.tabs.addTab(w, TABS[0])

    def start_service(self):
        self.svc.start(operator=self.op())
        self.refresh_all()

    def stop_service(self):
        self.svc.stop(operator=self.op())
        self.refresh_all()

    def refresh_status(self):
        st = self.svc.status()
        for k, lab in self.status_labels.items():
            v = st.get(k)
            lab.setText("是" if v is True else "否" if v is False else str(v))
        self.status_labels["audit_anchor_ok"].setStyleSheet(
            "" if st.get("audit_anchor_ok", True) else "color: #b00020; font-weight: bold")
        self.status_labels["open_alerts"].setStyleSheet(
            "color: #b00020; font-weight: bold" if st["open_alerts"] else "")
        self.tabs.setTabText(4, f"告警 ({st['open_alerts']})" if st["open_alerts"] else "告警")

    # ---------------- 白名单 ----------------
    def _build_peers(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.peer_table = _table(["AE Title", "IP", "端口", "角色", "启用", "说明"])
        self.peer_table.itemSelectionChanged.connect(self._peer_selected)
        lay.addWidget(self.peer_table)
        form_box = QGroupBox("新增 / 修改条目")
        form = QFormLayout(form_box)
        self.f_ae, self.f_ip, self.f_port, self.f_desc = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        form.addRow("AE Title", self.f_ae)
        form.addRow("IP", self.f_ip)
        form.addRow("端口（MOVE_DEST 必填）", self.f_port)
        roles_row = QHBoxLayout()
        self.f_roles = {r: QCheckBox(r) for r in ROLES}
        for cb in self.f_roles.values():
            roles_row.addWidget(cb)
        rw = QWidget()
        rw.setLayout(roles_row)
        form.addRow("角色", rw)
        form.addRow("说明", self.f_desc)
        lay.addWidget(form_box)
        btns = QHBoxLayout()
        for text, fn in (("新增", self.peer_add), ("保存修改", self.peer_update),
                         ("启用", lambda: self.peer_enable(True)),
                         ("停用", lambda: self.peer_enable(False)), ("移除条目", self.peer_remove)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        lay.addLayout(btns)
        self.peer_msg = QLabel("")
        self.peer_msg.setWordWrap(True)
        lay.addWidget(self.peer_msg)
        self.tabs.addTab(w, TABS[1])

    def _peer_form(self) -> dict:
        port = self.f_port.text().strip()
        return {"ae_title": self.f_ae.text(), "host": self.f_ip.text().strip(),
                "port": int(port) if port.isdigit() else (port or None),
                "roles": [r for r, cb in self.f_roles.items() if cb.isChecked()],
                "description": self.f_desc.text()}

    def _peer_msg(self, text: str, ok: bool):
        self.peer_msg.setText(text)
        self.peer_msg.setStyleSheet("color: #1b5e20" if ok else "color: #b00020; font-weight: bold")

    def peer_add(self):
        f = self._peer_form()
        try:
            self.svc.whitelist.add(f["ae_title"], f["host"], f["roles"], port=f["port"],
                                   description=f["description"], operator=self.op())
            self._peer_msg(f"已新增 {f['ae_title'].strip()}（新关联立即生效）", True)
        except WhitelistError as exc:
            self._peer_msg(f"未保存：{exc}", False)
        self.refresh_peers()

    def peer_update(self):
        f = self._peer_form()
        try:
            self.svc.whitelist.update(f["ae_title"], operator=self.op(), host=f["host"] or None,
                                      port=f["port"], roles=f["roles"] or None,
                                      description=f["description"])
            self._peer_msg(f"已修改 {f['ae_title'].strip()}", True)
        except WhitelistError as exc:
            self._peer_msg(f"未保存：{exc}", False)
        self.refresh_peers()

    def peer_enable(self, enabled: bool):
        try:
            self.svc.whitelist.set_enabled(self.f_ae.text(), enabled, operator=self.op())
            self._peer_msg(("已启用 " if enabled else "已停用 ") + self.f_ae.text().strip(), True)
        except WhitelistError as exc:
            self._peer_msg(f"失败：{exc}", False)
        self.refresh_peers()

    def peer_remove(self):
        try:
            self.svc.whitelist.remove(self.f_ae.text(), operator=self.op())
            self._peer_msg("已移除 " + self.f_ae.text().strip(), True)
        except WhitelistError as exc:
            self._peer_msg(f"失败：{exc}", False)
        self.refresh_peers()

    def _peer_selected(self):
        r = self.peer_table.currentRow()
        if r < 0 or self.peer_table.item(r, 0) is None:
            return
        self.f_ae.setText(self.peer_table.item(r, 0).text())
        self.f_ip.setText(self.peer_table.item(r, 1).text())
        self.f_port.setText(self.peer_table.item(r, 2).text())
        roles = self.peer_table.item(r, 3).text().split(",")
        for role, cb in self.f_roles.items():
            cb.setChecked(role in roles)
        self.f_desc.setText(self.peer_table.item(r, 5).text())

    def refresh_peers(self):
        peers = self.svc.whitelist.list()
        _fill(self.peer_table, [[p.ae_title, p.host, p.port or "", ",".join(p.roles),
                                 "是" if p.enabled else "否", p.description] for p in peers],
              [None if p.enabled else "#eeeeee" for p in peers])

    # ---------------- 影像库 ----------------
    def _build_library(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.lib_filter = QLineEdit()
        self.lib_filter.setPlaceholderText("按 PatientID 或姓名过滤")
        b = QPushButton("刷新")
        b.clicked.connect(self.refresh_library)
        row.addWidget(self.lib_filter)
        row.addWidget(b)
        lay.addLayout(row)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["患者 / 检查 / 序列 / 实例", "信息"])
        self.tree.setColumnWidth(0, 330)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        split.addWidget(self.tree)
        right = QWidget()
        rl = QVBoxLayout(right)
        self.preview = QLabel("选择实例以预览（管理用途，非诊断显示）")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(280, 280)
        self.preview_info = QTextEdit()
        self.preview_info.setReadOnly(True)
        rl.addWidget(self.preview)
        rl.addWidget(self.preview_info)
        split.addWidget(right)
        split.setSizes([700, 400])
        lay.addWidget(split)
        self.tabs.addTab(w, TABS[2])

    def refresh_library(self):
        self.tree.clear()
        flt = self.lib_filter.text().strip().lower()
        db = self.svc.db
        for p in db.query("SELECT * FROM patients ORDER BY patient_id"):
            if flt and flt not in p["patient_id"].lower() and flt not in p["patient_name"].lower():
                continue
            label = f"{p['patient_id'] or '(空 ID)'}  {p['patient_name']}"
            info = f"{p['birth_date']} {p['sex']}" + ("  ⚠人口学不一致" if p["demographics_conflict"] else "")
            pi = QTreeWidgetItem([label, info])
            self.tree.addTopLevelItem(pi)
            for st in db.query("SELECT * FROM studies WHERE patient_pk=? ORDER BY study_date", (p["id"],)):
                si = QTreeWidgetItem([f"检查 {st['study_date']} {st['description']}",
                                      f"Acc={st['accession']} UID={st['study_uid']}"])
                pi.addChild(si)
                for se in db.query("SELECT * FROM series WHERE study_pk=?", (st["id"],)):
                    n = db.scalar("SELECT COUNT(*) FROM instances WHERE series_pk=?", (se["id"],))
                    sei = QTreeWidgetItem([f"序列 {se['series_number']} {se['modality']} {se['description']}",
                                           f"{n} 张"])
                    si.addChild(sei)
                    for ins in db.query("SELECT sop_uid, instance_number FROM instances WHERE series_pk=?"
                                        " ORDER BY CAST(instance_number AS INTEGER)", (se["id"],)):
                        it = QTreeWidgetItem([f"实例 {ins['instance_number']}", ins["sop_uid"]])
                        it.setData(0, Qt.UserRole, ins["sop_uid"])
                        sei.addChild(it)
            pi.setExpanded(True)

    def _tree_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        uid = items[0].data(0, Qt.UserRole)
        if uid:
            self.show_instance(uid)

    def show_instance(self, uid: str):
        from ..dicomutil import parse_file_bytes
        row = self.svc.verifier.instance_row(uid)
        if row is None:
            return
        path = self.svc.config.store_dir / row["rel_path"]
        try:
            ds = parse_file_bytes(path.read_bytes())
            img = render_preview(ds)
        except Exception as exc:  # noqa: BLE001
            ds, img = None, None
            self.preview_info.setPlainText(f"无法读取：{exc}")
        if img is not None:
            self.preview.setPixmap(QPixmap.fromImage(img))
        else:
            self.preview.setText("压缩数据或无法预览")
        if ds is not None:
            self.preview_info.setPlainText(
                f"SOP Instance UID: {uid}\n模态: {row['modality']}  传输语法: {row['transfer_syntax']}\n"
                f"来源: {row['source_ae']}@{row['source_ip']}  接收: {row['received_at']}\n"
                f"dataset_sha256: {row['dataset_sha256']}\npixel_sha256: {row['pixel_sha256']}")
        self.svc.audit.append("ADMIN_VIEW", actor=self.op(), target=f"instance:{uid}",
                              details={"via": "gui-preview"})

    # ---------------- 完整性核查 ----------------
    def _build_verify(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        b_all = QPushButton("全量核查")
        b_all.clicked.connect(lambda: self.run_verify(None))
        b_sel = QPushButton("核查影像库中选中的检查")
        b_sel.clicked.connect(self.verify_selected)
        row.addWidget(b_all)
        row.addWidget(b_sel)
        lay.addLayout(row)
        self.verify_summary = QLabel("尚未运行")
        lay.addWidget(self.verify_summary)
        self.verify_table = _table(["类别", "患者", "检查 UID", "实例 UID", "字段 / 位置 / 前后值"])
        lay.addWidget(self.verify_table)
        self.tabs.addTab(w, TABS[3])

    def verify_selected(self):
        items = self.tree.selectedItems()
        if not items:
            self.verify_summary.setText("请先在“影像库”中选择一个检查")
            return
        text = items[0].text(1)
        uid = text.split("UID=")[-1] if "UID=" in text else None
        self.run_verify({"study_uid": uid} if uid else None)

    def run_verify(self, scope):
        rep = self.svc.verifier.run(scope, trigger="manual", operator=self.op())
        self.verify_summary.setText(
            f"核查 {rep['checked']} 个实例：正常 {rep['ok']}，异常实例 {rep['anomalous_instances']}"
            f"（核查编号 {rep['verification_id']}）")
        rows = []
        for f in rep["findings"]:
            d = {k: v for k, v in f["details"].items() if k != "context"}
            rows.append([f["category"], f["patient_id"], f["study_uid"], f["sop_uid"],
                         json.dumps(d, ensure_ascii=False, default=str)[:300]])
        _fill(self.verify_table, rows, ["#ffe0e0"] * len(rows))
        self.refresh_alerts()
        self.refresh_status()
        return rep

    # ---------------- 告警 ----------------
    def _build_alerts(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.alert_table = _table(["ID", "首次时间", "级别", "类型", "对象", "次数", "状态", "建议处置"])
        lay.addWidget(self.alert_table)
        row = QHBoxLayout()
        b = QPushButton("确认选中告警")
        b.clicked.connect(self.ack_selected)
        row.addWidget(b)
        self.alert_detail = QTextEdit()
        self.alert_detail.setReadOnly(True)
        self.alert_table.itemSelectionChanged.connect(self._alert_selected)
        lay.addLayout(row)
        lay.addWidget(self.alert_detail)
        self.tabs.addTab(w, TABS[4])

    def refresh_alerts(self):
        rows = self.svc.alerts.list()
        self._alert_rows = rows
        sev = {"high": "#ffcdd2", "medium": "#fff3c4", "low": None}
        _fill(self.alert_table, [[a["id"], a["created_at"], a["severity"], a["type"], a["subject"],
                                  a["count"], a["status"], a["suggestion"]] for a in rows],
              [sev.get(a["severity"]) if a["status"] == "open" else "#eeeeee" for a in rows])

    def _alert_selected(self):
        r = self.alert_table.currentRow()
        if 0 <= r < len(getattr(self, "_alert_rows", [])):
            a = self._alert_rows[r]
            self.alert_detail.setPlainText(json.dumps(json.loads(a["details_json"]),
                                                      ensure_ascii=False, indent=2))

    def ack_selected(self):
        r = self.alert_table.currentRow()
        if 0 <= r < len(getattr(self, "_alert_rows", [])):
            self.svc.alerts.ack(self._alert_rows[r]["id"], self.op())
        self.refresh_alerts()
        self.refresh_status()

    # ---------------- 审计（只读） ----------------
    def _build_audit(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        for text, fn in (("刷新", self.refresh_audit), ("校验审计链", self.verify_audit),
                         ("导出 CSV", self.export_audit)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.audit_msg = QLabel("")
        self.audit_msg.setWordWrap(True)
        lay.addWidget(self.audit_msg)
        self.audit_table = _table(["seq", "本地时间", "事件", "行为者", "IP", "对象", "结果", "详情"])
        lay.addWidget(self.audit_table)
        self.audit_tab = w
        self.tabs.addTab(w, TABS[5])

    def refresh_audit(self):
        rows = self.svc.audit.recent(300)
        _fill(self.audit_table, [[r["seq"], r["ts_local"], r["event_type"], r["actor"], r["actor_ip"],
                                  r["target"], r["outcome"], r["details_json"][:200]] for r in rows])

    def verify_audit(self):
        rep = self.svc.verify_audit(operator=self.op())
        self.audit_msg.setText(
            (f"审计链完整：{rep.entries} 条，链头 {rep.head_seq}:{rep.head_hash}" if rep.ok else
             "审计链校验失败：" + "；".join(rep.problems[:3])))
        self.audit_msg.setStyleSheet("" if rep.ok else "color: #b00020; font-weight: bold")
        self.refresh_audit()
        return rep

    def export_audit(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "导出审计", "audit-export.csv", "CSV (*.csv)")
        if path:
            n = self.svc.export_audit(Path(path), operator=self.op())
            self.audit_msg.setText(f"已导出 {n} 条到 {path}")

    # ---------------- 刷新 ----------------
    def refresh_live(self):
        self.refresh_status()
        if self.tabs.currentIndex() == 4:
            self.refresh_alerts()

    def refresh_all(self):
        self.refresh_status()
        self.refresh_peers()
        self.refresh_library()
        self.refresh_alerts()
        self.refresh_audit()


def run_gui(runtime: Path, autostart: bool = False) -> int:
    from ..service import PacsService
    app = QApplication.instance() or QApplication(sys.argv)
    if sys.platform == "win32":
        from PyQt5.QtGui import QFont
        app.setFont(QFont("Microsoft YaHei", 9))
    svc = PacsService(PacsConfig.load(runtime))
    win = ConsoleWindow(svc)
    if autostart:
        win.start_service()
    win.show()
    code = app.exec_()
    svc.stop(operator=win.op())
    svc.db.close()
    return code
