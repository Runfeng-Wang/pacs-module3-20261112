"""核心函数单元测试（E1）：配置、白名单校验、审计链、告警聚合、匹配函数、元数据 / 分块差异。"""

import pytest

from pacs_server.alerts import AlertManager
from pacs_server.audit import AuditLog
from pacs_server.config import PacsConfig
from pacs_server.db import Database
from pacs_server.dicomutil import diff_meta, diff_tiles, meta_hash
from pacs_server.query import match_value
from pacs_server.whitelist import Whitelist, WhitelistError, validate_ae_title


@pytest.fixture
def core(tmp_path):
    db = Database(tmp_path / "t.db")
    audit = AuditLog(db, b"k" * 32, tmp_path / "anchor.log")
    yield db, audit, AlertManager(db, audit), Whitelist(db, audit)
    db.close()


def test_config_roundtrip(tmp_path, actual):
    """TC-U-CFG-1 | 目标: 配置保存与加载一致 | 输入: 修改 port、retrieve_policy 后 save 再 load | 期望: 值一致，非法策略被拒"""
    c = PacsConfig(runtime_dir=tmp_path, port=12345, retrieve_policy="allow")
    c.save()
    c2 = PacsConfig.load(tmp_path)
    actual(f"port={c2.port} policy={c2.retrieve_policy}")
    assert (c2.port, c2.retrieve_policy) == (12345, "allow")
    (tmp_path / "pacs.json").write_text('{"retrieve_policy": "maybe"}', encoding="utf-8")
    with pytest.raises(ValueError):
        PacsConfig.load(tmp_path)


def test_schema_created(core, actual):
    """TC-U-DB-1 | 目标: 数据库表结构创建 | 输入: 新建 Database | 期望: 包含 peers/patients/studies/series/instances/baselines/alerts/audit 等表"""
    db = core[0]
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    actual(f"tables={sorted(names)}")
    assert {"peers", "patients", "studies", "series", "instances", "baselines", "quarantine",
            "alerts", "audit", "verifications", "findings"} <= names


@pytest.mark.parametrize("bad", ["A" * 17, "BAD\\AE", "", "   ", "TAB\tAE", "中文AE"])
def test_r4_n1_invalid_ae_title(bad, actual):
    """TC-R4-N1 | 目标: 非法 AE Title 被拒绝 | 输入: 17 字符 / 含反斜杠 / 空 / 全空格 / 控制字符 / 非 ASCII | 期望: 抛出 WhitelistError"""
    with pytest.raises(WhitelistError) as ei:
        validate_ae_title(bad)
    actual(f"{bad!r} -> {ei.value}")


def test_r4_n1_invalid_peer_entries_rejected(core, actual):
    """TC-R4-N1b | 目标: 非法白名单条目不保存 | 输入: 重复 AE、IP 999.1.1.1、端口 70000、未知角色、MOVE_DEST 无端口 | 期望: 全部拒绝，白名单只有最初 1 条"""
    wl = core[3]
    wl.add("WS_A", "127.0.0.1", ["QUERY"])
    cases = [dict(ae_title="WS_A", host="127.0.0.1", roles=["QUERY"]),
             dict(ae_title="WS_B", host="999.1.1.1", roles=["QUERY"]),
             dict(ae_title="WS_C", host="127.0.0.1", roles=["QUERY"], port=70000),
             dict(ae_title="WS_D", host="127.0.0.1", roles=["ADMIN"]),
             dict(ae_title="WS_E", host="127.0.0.1", roles=["MOVE_DEST"])]
    msgs = []
    for c in cases:
        with pytest.raises(WhitelistError) as ei:
            wl.add(**c)
        msgs.append(str(ei.value))
    actual("；".join(msgs))
    assert [p.ae_title for p in wl.list()] == ["WS_A"]


def test_audit_chain_unit(core, actual):
    """TC-U-AUD-1 | 目标: 审计哈希链与 HMAC | 输入: 追加 5 条后校验；用错误密钥校验 | 期望: 正确密钥 ok；错误密钥报 HMAC 不符"""
    db, audit = core[0], core[1]
    for i in range(5):
        audit.append("UNIT", details={"i": i})
    ok = audit.verify()
    other = AuditLog(db, b"x" * 32, audit.anchor_path).verify()
    actual(f"ok={ok.ok} entries={ok.entries} wrong_key_ok={other.ok}")
    assert ok.ok and ok.entries == 5 and not other.ok
    assert any("HMAC" in p for p in other.problems)


def test_audit_gap_detected(core, actual):
    """TC-U-AUD-2 | 目标: 删除中间记录可检出 | 输入: 删除 seq=2 | 期望: 报告序号缺口"""
    db, audit = core[0], core[1]
    for i in range(4):
        audit.append("UNIT", details={"i": i})
    db._conn.execute("DELETE FROM audit WHERE seq=2")
    rep = audit.verify()
    actual(f"ok={rep.ok} first_break={rep.first_break_seq}")
    assert not rep.ok and rep.first_break_seq == 3 and any("缺口" in p for p in rep.problems)


def test_alert_aggregation_window(core, actual):
    """TC-U-ALR-1 | 目标: 告警聚合与时间窗口 | 输入: 同 key 连续 3 次；窗口 0 秒后再 1 次 | 期望: 第一批聚合为 count=3；超窗后新建告警"""
    alerts = core[2]
    for _ in range(3):
        alerts.raise_alert("UNREGISTERED_PEER", "peer:X", {}, aggregate_key="k", window_s=60)
    n1 = alerts.list()
    import time
    time.sleep(0.01)
    alerts.raise_alert("UNREGISTERED_PEER", "peer:X", {}, aggregate_key="k", window_s=0)
    n2 = alerts.list()
    actual(f"after3={[(a['count']) for a in n1]} after_window={[(a['count']) for a in n2]}")
    assert len(n1) == 1 and n1[0]["count"] == 3 and len(n2) == 2


def test_match_value_rules(actual):
    """TC-U-QRY-1 | 目标: C-FIND 匹配规则 | 输入: 通配符、PN 大小写、日期范围、UID 列表、通用匹配 | 期望: 与一致性声明一致"""
    assert match_value("PatientName", ["test*"], "TEST^PATIENT01")      # PN 不区分大小写
    assert not match_value("PatientID", ["syn0001"], "SYN0001")         # LO 区分大小写
    assert match_value("StudyDate", ["20261101-20261130"], "20261105")
    assert not match_value("StudyDate", ["-20261031"], "20261105")
    assert match_value("StudyInstanceUID", ["1.2", "1.3"], "1.3")
    assert match_value("PatientID", [], "anything")
    assert match_value("ModalitiesInStudy", ["ES"], ["CT", "ES"])
    assert match_value("PatientID", ["SYN000?"], "SYN0007")
    actual("8 条匹配断言全部成立")


def test_meta_and_tile_diff(actual):
    """TC-U-INT-1 | 目标: 元数据差异与分块定位 | 输入: 改一个字段；改一个分块 | 期望: 给出字段前后值；给出变化区域"""
    a = {"00080020": {"kw": "StudyDate", "vr": "DA", "v": "20261105"}}
    b = {"00080020": {"kw": "StudyDate", "vr": "DA", "v": "20261106"}}
    d = diff_meta(a, b)
    vr_only = {"00080020": {"kw": "StudyDate", "vr": "UN", "v": "20261105"}}
    tb = {"supported": True, "tile": 32, "rows": 64, "cols": 64, "frames": 1,
          "grid": [[["a", "b"], ["c", "d"]]]}
    ta = {**tb, "grid": [[["a", "b"], ["c", "X"]]]}
    r = diff_tiles(tb, ta)
    actual(f"meta_diff={d} tile_regions={r}")
    assert d[0]["before"] == "20261105" and d[0]["after"] == "20261106" and d[0]["key_field"]
    assert meta_hash(a) == meta_hash(vr_only)  # VR 推断差异不影响内容等价判断
    assert r == [{"frame": 1, "tiles_changed": 1, "row_range": [32, 63], "col_range": [32, 63]}]
