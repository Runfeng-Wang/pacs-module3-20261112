"""文档与代码一致性（课程 Week16 要求“文档与代码一致性检查”）。"""

import re
from pathlib import Path

from pacs_server import netserver, store
from pacs_server.dicomutil import IMPLEMENTATION_CLASS_UID, IMPLEMENTATION_VERSION_NAME

ROOT = Path(__file__).resolve().parents[1]
CS = (ROOT / "docs" / "interface" / "dicom-conformance-statement.md").read_text(encoding="utf-8")
APPX = (ROOT / "docs" / "interface" / "storage-sop-classes.md").read_text(encoding="utf-8")


def test_doc_status_codes_match_code(actual):
    """TC-DOC-1 | 目标: 接口文档中的 C-STORE 状态码与代码常量一致 | 输入: store.py / netserver.py 中的状态常量；一致性声明第 4 节表格 | 期望: 代码中每个状态码都出现在文档表格中，文档表格没有代码不存在的码"""
    section = CS.split("## 4. C-STORE")[1].split("## 5.")[0]
    doc_codes = {int(c, 16) for c in re.findall(r"^\| (0x[0-9A-F]{4}) \|", section, re.M)}
    code_codes = set(store.STATUS_TEXT) | {netserver.STATUS_NOT_AUTHORIZED}
    actual(f"doc={sorted(hex(c) for c in doc_codes)} code={sorted(hex(c) for c in code_codes)}")
    assert doc_codes == code_codes


def test_doc_implementation_uid_and_storage_list(actual):
    """TC-DOC-2 | 目标: 实现 UID 与存储类附录和代码一致 | 输入: dicomutil 常量、netserver.STORAGE_UIDS、附录 A | 期望: 一致性声明写明的实现 UID / 版本名与代码相同；附录 A 的 UID 集合等于 B 实际支持的存储类"""
    appx_uids = set(re.findall(r"^\| \d+ \| ([0-9.]+) \|", APPX, re.M))
    actual(f"impl_uid_in_doc={str(IMPLEMENTATION_CLASS_UID) in CS} appendix={len(appx_uids)} code={len(netserver.STORAGE_UIDS)}")
    assert str(IMPLEMENTATION_CLASS_UID) in CS and IMPLEMENTATION_VERSION_NAME in CS
    assert appx_uids == netserver.STORAGE_UIDS


def test_doc_transfer_syntax_order(actual):
    """TC-DOC-3 | 目标: 文档中的传输语法顺序与代码一致 | 输入: 一致性声明 3.1 表格；netserver.STORAGE_TRANSFER_SYNTAXES | 期望: 顺序完全一致"""
    section = CS.split("### 3.1")[1].split("## 4.")[0]
    doc = re.findall(r"\| \d \| [^|]+ \| ([0-9.]+) \|", section)
    code = [str(t) for t in netserver.STORAGE_TRANSFER_SYNTAXES]
    actual(f"doc={doc}")
    assert doc == code


def test_spec_scenarios_match_tests(actual):
    """TC-DOC-4 | 目标: OpenSpec 场景编号与自动化测试一一对应，需求文档覆盖 R1–R21 | 输入: specs/*/spec.md 的 Scenario 编号、tests/ 的 docstring 编号、../模块三_需求定义文档.md | 期望: 每个 spec 场景都有同编号测试；每个 TC-R/CC 测试都在 spec 中；需求文档第 9、10 节包含 R1–R21"""
    spec_ids = set()
    for p in (ROOT / "openspec" / "changes" / "add-pacs-image-server" / "specs").rglob("spec.md"):
        spec_ids |= set(re.findall(r"#### Scenario: (TC-[A-Z0-9-]+)", p.read_text(encoding="utf-8")))
    test_ids = set()
    for p in (ROOT / "tests").glob("test_*.py"):
        test_ids |= set(re.findall(r'"""(TC-(?:R\d+|CC\d)-[PN]\d+)\b', p.read_text(encoding="utf-8")))
    req_doc = (ROOT.parent / "模块三_需求定义文档.md").read_text(encoding="utf-8")
    sec9 = req_doc.split("## 9.")[1].split("## 10.")[0]
    sec10 = req_doc.split("## 10.")[1].split("## 11.")[0]
    missing_req = [f"R{i}" for i in range(1, 22)
                   if f"| R{i} |" not in sec9 or not re.search(rf"TC-R{i}-|\(R{i}\)|（R{i}）", sec10)]
    actual(f"spec={len(spec_ids)} tests={len(test_ids)} spec_only={sorted(spec_ids - test_ids)} test_only={sorted(test_ids - spec_ids)} missing_in_req_doc={missing_req}")
    assert spec_ids == test_ids
    assert not missing_req
