"""由 pytest junit XML 生成测试报告：编号 / 目标 / 输入 / 期望 / 实际 / 结论（课程 Week15 用例表格式）。

用法：python scripts/make_test_report.py docs/testing/logs/junit.xml docs/testing/test-report.md
“实际”一列只来自测试运行时用 record_property 记录的观测值，不是人工填写。
"""

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EVIDENCE = {
    "test_conformance": "E2", "test_gui_smoke": "E4", "test_unit_core": "E1",
    "test_docs_consistency": "E1",
}
REQ_NAMES = {
    "R1": "已登记对端连通性验证", "R2": "白名单准入", "R3": "按角色授权服务", "R4": "白名单在线维护",
    "R5": "持久化后才确认成功", "R6": "入库校验与层级一致性", "R7": "重复实例与 UID 冲突",
    "R8": "按患者/检查/序列/实例组织", "R9": "C-FIND 分层查询", "R10": "C-MOVE 推送到已登记目的地",
    "R11": "C-GET 同关联取回", "R12": "并发接收与并发调阅", "R13": "传输中断不算事故",
    "R14": "入库时建立受保护的基线", "R15": "完整性核查与分类报告", "R16": "调阅前校验与默认阻断",
    "R17": "异常告警与正常操作零误报", "R18": "哈希链审计与截断检测", "R19": "重启持久化与启动对账",
    "R20": "管理控制台与命令行", "R21": "按成文接口文档可对接",
}


def esc(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def parse_doc(doc: str) -> dict:
    parts = [p.strip() for p in doc.split("|")]
    out = {"id": parts[0] if parts else "", "goal": "", "input": "", "expect": ""}
    for p in parts[1:]:
        for key, label in (("goal", "目标"), ("input", "输入"), ("expect", "期望")):
            if p.startswith(label):
                out[key] = p.split(":", 1)[-1].split("：", 1)[-1].strip()
    return out


def req_of(tc: str) -> str:
    m = re.match(r"TC-(R\d+)", tc)
    if m:
        return m.group(1)
    if tc.startswith("TC-CC"):
        return "R12"
    return ""


def tool_versions() -> dict:
    v = {}
    try:
        import numpy, pydicom, pynetdicom  # noqa: E401
        from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        v.update(python=sys.version.split()[0], pydicom=pydicom.__version__,
                 pynetdicom=pynetdicom.__version__, numpy=numpy.__version__,
                 PyQt5=f"{PYQT_VERSION_STR} (Qt {QT_VERSION_STR})")
    except Exception as exc:  # noqa: BLE001
        v["error"] = str(exc)
    dcmtk = ROOT.parents[1] / "tools" / "dcmtk" / "dcmtk-3.7.1-win64-dynamic" / "bin" / "echoscu.exe"
    if dcmtk.exists():
        try:
            out = subprocess.run([str(dcmtk), "--version"], capture_output=True, text=True, timeout=20)
            v["DCMTK"] = out.stdout.splitlines()[0].strip("$ ")
        except Exception:  # noqa: BLE001
            v["DCMTK"] = "present"
    else:
        v["DCMTK"] = "不存在（E3 用例将被跳过）"
    return v


def main(junit: Path, out: Path) -> int:
    tree = ET.parse(junit)
    cases = []
    for tc in tree.iter("testcase"):
        props = {p.get("name"): p.get("value") for p in tc.iter("property")}
        mod = tc.get("classname", "").split(".")[-1]
        status = "通过"
        detail = ""
        for tag, label in (("failure", "失败"), ("error", "错误"), ("skipped", "跳过")):
            el = tc.find(tag)
            if el is not None:
                status = label
                detail = (el.get("message") or "")[:200]
        d = parse_doc(props.get("doc", ""))
        name = tc.get("name", "")
        param = re.search(r"\[(.*)\]$", name)
        tcid = d["id"] + (f"[{param.group(1)}]" if param else "")
        ev = EVIDENCE.get(mod, "E2")
        if mod == "test_conformance" and "dcmtk" in name:
            ev = "E3"
        cases.append({"id": tcid, "goal": d["goal"], "input": d["input"], "expect": d["expect"],
                      "actual": props.get("actual", "") + (f"（{detail}）" if detail else ""),
                      "status": status, "time": float(tc.get("time", 0)), "module": mod,
                      "evidence": ev, "req": req_of(d["id"])})
    total = len(cases)
    by = defaultdict(int)
    for c in cases:
        by[c["status"]] += 1
    vers = tool_versions()
    L = [
        "# 测试报告（自动生成）",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 来源：`{junit.relative_to(ROOT).as_posix()}`（pytest junit XML），原始日志见 `docs/testing/logs/`",
        "- 环境：" + "；".join(f"{k} {v}" for k, v in vers.items()),
        f"- 结果：共 {total} 个用例；" + "，".join(f"{k} {v}" for k, v in sorted(by.items())),
        "",
        "**证据级别**：E1 = 单元 / 核心函数；E2 = pynetdicom 真实 TCP 回环（同一库两端）；"
        "E3 = DCMTK 3.7.1 独立实现回环；E4 = PyQt5 离屏自动化冒烟。"
        "**H（真人操作验收）与 X（跨组跨主机联调）均未执行**，本报告不包含这两类结论。",
        "",
        "“实际”一列是测试运行时程序记录的观测值；“结论”取自 pytest 的判定。所有数据都是合成数据，对端都运行在 127.0.0.1。",
        "",
        "## 1. 系统测试（按需求编号）",
        "",
        "| 编号 | 目标 | 输入 | 期望 | 实际 | 结论 | 证据 |",
        "|---|---|---|---|---|---|---|",
    ]
    sys_cases = sorted([c for c in cases if c["req"]],
                       key=lambda c: (int(c["req"][1:]), c["id"]))
    for c in sys_cases:
        L.append(f"| {esc(c['id'])} | {esc(c['goal'])} | {esc(c['input'])} | {esc(c['expect'])} | "
                 f"{esc(c['actual'])} | {c['status']} | {c['evidence']} |")
    L += ["", "## 2. 核心函数测试与文档一致性", "",
          "| 编号 | 目标 | 输入 | 期望 | 实际 | 结论 | 证据 |", "|---|---|---|---|---|---|---|"]
    for c in sorted([c for c in cases if not c["req"]], key=lambda c: c["id"]):
        L.append(f"| {esc(c['id'])} | {esc(c['goal'])} | {esc(c['input'])} | {esc(c['expect'])} | "
                 f"{esc(c['actual'])} | {c['status']} | {c['evidence']} |")
    L += ["", "## 3. 需求覆盖矩阵（每条需求至少一正一反）", "",
          "| 需求 | 名称 | 正向用例 | 反向用例 | 全部通过 |", "|---|---|---|---|---|"]
    for r, nm in REQ_NAMES.items():
        cs = [c for c in cases if c["req"] == r]
        pos = [c["id"] for c in cs if re.search(r"-P\d", c["id"])]
        neg = [c["id"] for c in cs if re.search(r"-N\d", c["id"])]
        ok = bool(cs) and all(c["status"] == "通过" for c in cs) and pos and neg
        L.append(f"| {r} | {nm} | {', '.join(pos) or '—'} | {', '.join(neg) or '—'} | "
                 f"{'是' if ok else '否'} |")
    L += ["", "## 4. 未执行的验证（如实列出）", "",
          "- **H 真人 GUI 验收**：未执行。离屏截图（`docs/evidence/screenshots/`）只证明页面能渲染数据，不代表可用性验收。",
          "- **X 跨组联调**：未执行。A / C 组的真实对端不存在；协议与模板见 `docs/interface/cross-group-integration-plan.md`。",
          "- **医院真实负载**：并发阈值是实验参数（TC-CC1-P1），不是医院 SLA。",
          "",
          "## 5. 独立复核后修复",
          "",
          "Codex 独立复核实际复现了 2 个异常处理问题（基线 JSON 损坏时核查中断；提交后锚点写入失败时索引指向不存在的文件）。"
          "修复后新增回归用例 TC-R14-N2、TC-R15-N5、TC-R16-N2、TC-R5-N3、TC-R5-N4、TC-R18-N4（`tests/test_independent_review_fixes.py`）。"
          "修复前 / 后的复现结果见 `docs/process/independent-review/`；复核前的测试记录保留在 `docs/testing/history/run1-before-independent-review/`。",
          ""]
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {out}: {total} cases {dict(by)}")
    return 0 if by.get("失败", 0) == 0 and by.get("错误", 0) == 0 else 1


if __name__ == "__main__":
    j = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "testing" / "logs" / "junit.xml"
    o = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "testing" / "test-report.md"
    sys.exit(main(j.resolve(), o.resolve()))
