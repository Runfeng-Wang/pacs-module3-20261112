"""篡改注入工具：仅用于演示与测试，模拟“绕过系统直接改动库内数据”的攻击或事故。

每个函数都直接操作文件或数据库，绕开 PACS 的正常接口，这正是核查要发现的情形。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydicom import dcmread

from ..dicomutil import dataset_offset_of_file


def _instance_path(svc, sop_uid: str) -> Path:
    row = svc.db.query_one("SELECT rel_path FROM instances WHERE sop_uid=?", (sop_uid,))
    if row is None:
        raise KeyError(sop_uid)
    return svc.config.store_dir / row["rel_path"]


def flip_pixel_byte(svc, sop_uid: str, pixel_offset: int | None = None) -> dict:
    """翻转像素数据中的 1 个字节（默认取图像中央附近）。"""
    path = _instance_path(svc, sop_uid)
    data = bytearray(path.read_bytes())
    ds = dcmread(path)
    px = bytes(ds.PixelData)
    # 在文件中定位像素值：取像素数据末尾一段的唯一位置
    off_ds = dataset_offset_of_file(path)
    start = len(data) - len(px)   # 未压缩、像素为最后一个元素时成立（合成数据满足）
    if pixel_offset is None:
        pixel_offset = len(px) // 2 + (int(ds.Columns) * 2 if "Columns" in ds else 0)
    pos = start + pixel_offset
    assert pos >= off_ds
    data[pos] ^= 0xFF
    path.write_bytes(bytes(data))
    return {"file": str(path), "byte_offset_in_pixel_data": pixel_offset}


def set_element(svc, sop_uid: str, keyword: str, value) -> dict:
    """用 pydicom 改一个元素并写回（会改变文件字节；模拟有人直接编辑文件）。"""
    path = _instance_path(svc, sop_uid)
    ds = dcmread(path)
    before = str(ds.get(keyword, ""))
    setattr(ds, keyword, value)
    ds.save_as(path)
    return {"file": str(path), "keyword": keyword, "before": before, "after": str(value)}


def delete_file(svc, sop_uid: str) -> dict:
    path = _instance_path(svc, sop_uid)
    path.unlink()
    return {"deleted": str(path)}


def _raw_conn(svc) -> sqlite3.Connection:
    return sqlite3.connect(str(svc.config.db_path), isolation_level=None)


def edit_index_patient_name(svc, patient_id: str, new_name: str) -> dict:
    """只改数据库索引（文件不动），模拟“文件看似正常但必须报警”。"""
    con = _raw_conn(svc)
    before = con.execute("SELECT patient_name FROM patients WHERE patient_id=?",
                         (patient_id,)).fetchone()
    con.execute("UPDATE patients SET patient_name=? WHERE patient_id=?", (new_name, patient_id))
    con.close()
    return {"patient_id": patient_id, "before": before[0] if before else None, "after": new_name}


def edit_baseline_hash(svc, sop_uid: str) -> dict:
    con = _raw_conn(svc)
    con.execute("UPDATE baselines SET file_sha256=? WHERE instance_id="
                "(SELECT id FROM instances WHERE sop_uid=?)", ("0" * 64, sop_uid))
    con.close()
    return {"sop_uid": sop_uid, "file_sha256": "0" * 64}


def edit_audit_details(svc, seq: int, details_json: str = '{"tampered":true}') -> dict:
    con = _raw_conn(svc)
    con.execute("UPDATE audit SET details_json=? WHERE seq=?", (details_json, seq))
    con.close()
    return {"seq": seq}


def truncate_audit_tail(svc, n: int) -> dict:
    con = _raw_conn(svc)
    mx = con.execute("SELECT MAX(seq) FROM audit").fetchone()[0]
    con.execute("DELETE FROM audit WHERE seq>?", (mx - n,))
    con.close()
    return {"removed_from_seq": mx - n + 1, "count": n}


def truncate_anchor_tail(svc, n: int) -> dict:
    p = svc.config.anchor_path
    lines = p.read_text(encoding="ascii").splitlines()
    p.write_text("\n".join(lines[:-n]) + ("\n" if lines[:-n] else ""), encoding="ascii")
    return {"anchor_lines_removed": n}
