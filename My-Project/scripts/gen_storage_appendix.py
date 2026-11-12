"""生成 docs/interface/storage-sop-classes.md：B 作为 Storage SCP 声明支持的全部存储类。

用法：.venv\\Scripts\\python.exe scripts\\gen_storage_appendix.py
"""

from pathlib import Path

from pynetdicom import StoragePresentationContexts

TESTED = {
    "1.2.840.10008.5.1.4.1.1.2": "E2 + E3（pynetdicom 回环 + DCMTK 3.7.1）",
    "1.2.840.10008.5.1.4.1.1.77.1.1": "E2（pynetdicom 回环，TC-R5-P2 / TC-R11-P1）",
    "1.2.840.10008.5.1.4.1.1.7": "E2（pynetdicom 回环，TC-R5-P2）",
}

out = Path(__file__).resolve().parents[1] / "docs" / "interface" / "storage-sop-classes.md"
lines = [
    "# 附录 A：B 作为 Storage SCP 声明支持的存储 SOP 类",
    "",
    "由 `scripts/gen_storage_appendix.py` 根据 pynetdicom 3.0.4 的 `StoragePresentationContexts` 生成。",
    "B 对所有这些类**原样保存**，不解析像素语义。证据级别：E0 = 仅声明未实测；E1 = 单元级；",
    "E2 = pynetdicom 真实 TCP 回环；E3 = DCMTK 独立实现回环。",
    "",
    "| # | SOP Class UID | 名称 | 证据级别 |",
    "|---|---|---|---|",
]
for i, cx in enumerate(sorted(StoragePresentationContexts, key=lambda c: str(c.abstract_syntax)), 1):
    uid = str(cx.abstract_syntax)
    lines.append(f"| {i} | {uid} | {cx.abstract_syntax.name} | {TESTED.get(uid, 'E0')} |")
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(StoragePresentationContexts)} classes)")
