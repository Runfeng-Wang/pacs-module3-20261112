"""DICOM 辅助：File Meta 生成、数据集偏移、元数据快照、像素哈希与分块哈希。

“上传时原样”的边界（Codex 第二轮风险 2）：
- B 记录的基线对象是 C-STORE 中收到的**网络数据集字节**（pynetdicom ``encoded_dataset(include_meta=False)``）。
- 库内 Part-10 文件 = 128 字节前导 + "DICM" + **B 生成的 File Meta** + 原样数据集字节。
- B 没有、也不声称收到了设备源 Part-10 文件的 File Meta 或前导；若 SCU 为传输做了语法转换，
  网络字节本来就与设备文件不同。
"""

from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path

import numpy as np
from pydicom import dcmread
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.tag import Tag
from pydicom.uid import UID
from pynetdicom.dsutils import encode_file_meta, split_dataset

from . import __version__
from .util import canonical_json, sha256_hex

# B 的实现 UID（2.25 + 由固定名称导出的 UUID，稳定、不依赖注册根）
IMPLEMENTATION_CLASS_UID = UID("2.25." + str(uuid.uuid5(uuid.NAMESPACE_URL,
                                                       "urn:pacs-b-prototype").int))
IMPLEMENTATION_VERSION_NAME = ("PACS_B_" + __version__.replace(".", "_"))[:16]

PIXEL_TAGS = {Tag(0x7FE0, 0x0010), Tag(0x7FE0, 0x0008), Tag(0x7FE0, 0x0009)}
TILE = 32

# 核查报告中标记为“关键字段”的元数据
KEY_FIELDS = {
    "PatientID", "PatientName", "PatientBirthDate", "PatientSex", "IssuerOfPatientID",
    "StudyInstanceUID", "StudyDate", "StudyTime", "AccessionNumber", "StudyID",
    "SeriesInstanceUID", "SeriesNumber", "Modality", "BodyPartExamined",
    "SOPClassUID", "SOPInstanceUID", "InstanceNumber", "ContentDate", "ContentTime",
    "AcquisitionDate", "AcquisitionTime", "ImagePositionPatient", "ImageOrientationPatient",
    "PixelSpacing", "SliceThickness", "RescaleSlope", "RescaleIntercept", "Rows", "Columns",
    "BitsAllocated", "BitsStored", "HighBit", "PixelRepresentation", "SamplesPerPixel",
    "PhotometricInterpretation", "Manufacturer", "InstitutionName", "StationName",
}


def build_file_bytes(raw_dataset: bytes, sop_class: str, sop_instance: str,
                     transfer_syntax: str, source_ae: str) -> tuple[bytes, int]:
    """返回 (Part-10 文件字节, 数据集起始偏移)。File Meta 由 B 生成。"""
    meta = FileMetaDataset()
    meta.FileMetaInformationVersion = b"\x00\x01"
    meta.MediaStorageSOPClassUID = UID(sop_class)
    meta.MediaStorageSOPInstanceUID = UID(sop_instance)
    meta.TransferSyntaxUID = UID(transfer_syntax)
    meta.ImplementationClassUID = IMPLEMENTATION_CLASS_UID
    meta.ImplementationVersionName = IMPLEMENTATION_VERSION_NAME
    if source_ae:
        meta.SourceApplicationEntityTitle = source_ae[:16]
    head = b"\x00" * 128 + b"DICM" + encode_file_meta(meta)
    return head + raw_dataset, len(head)


def dataset_offset_of_file(path: Path) -> int:
    _, offset = split_dataset(Path(path))
    return offset


def dataset_offset_of_bytes(file_bytes: bytes, tmp_dir: Path) -> int:
    """split_dataset 只接受路径；对内存字节先解析 File Meta 再求偏移。"""
    from pydicom.filereader import read_preamble, _read_file_meta_info  # noqa: PLC2701

    fp = BytesIO(file_bytes)
    read_preamble(fp, False)
    _read_file_meta_info(fp)
    return fp.tell()


def parse_file_bytes(file_bytes: bytes) -> Dataset:
    return dcmread(BytesIO(file_bytes), force=False)


def is_compressed(transfer_syntax: str) -> bool:
    try:
        return UID(transfer_syntax).is_compressed
    except Exception:
        return True


def pixel_bytes(ds: Dataset) -> bytes:
    for t in (Tag(0x7FE0, 0x0010), Tag(0x7FE0, 0x0008), Tag(0x7FE0, 0x0009)):
        if t in ds:
            v = ds[t].value
            return bytes(v) if v is not None else b""
    return b""


def _value_repr(elem):
    if elem.VR == "SQ":
        items = [meta_snapshot(item) for item in elem.value]
        return {"sq": len(items), "sha256": sha256_hex(canonical_json(items).encode())}
    v = elem.value
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        return "hex:" + b.hex() if len(b) <= 64 else "sha256:" + sha256_hex(b)
    if v is None:
        return ""
    try:
        from pydicom.multival import MultiValue
        if isinstance(v, (MultiValue, list, tuple)):
            return [str(x) for x in v]
    except Exception:
        pass
    return str(v)


def meta_snapshot(ds: Dataset) -> dict:
    """非像素元数据快照：{"GGGGEEEE": {"kw", "vr", "v"}}，不含 File Meta 与像素数据。"""
    out: dict[str, dict] = {}
    for elem in ds:
        tag = elem.tag
        if tag.group == 0x0002 or tag in PIXEL_TAGS:
            continue
        out[f"{tag.group:04X}{tag.element:04X}"] = {
            "kw": elem.keyword or "", "vr": str(elem.VR), "v": _value_repr(elem)}
    return out


def meta_hash(snapshot: dict) -> str:
    """只对 (keyword, value) 求哈希；VR 仅用于展示（Implicit/Explicit VR 下 VR 推断可能不同）。"""
    core = {k: {"kw": v.get("kw", ""), "v": v.get("v")} for k, v in snapshot.items()}
    return sha256_hex(canonical_json(core).encode("utf-8"))


def tile_hashes(ds: Dataset, transfer_syntax: str, raw_pixels: bytes) -> dict:
    """未压缩像素的分块哈希，用于报告变化位置；压缩数据只记录整体像素哈希。"""
    if not raw_pixels or is_compressed(transfer_syntax):
        return {"supported": False, "reason": "compressed-or-no-pixel"}
    try:
        rows, cols = int(ds.Rows), int(ds.Columns)
        spp = int(getattr(ds, "SamplesPerPixel", 1) or 1)
        bits = int(ds.BitsAllocated)
        frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
        planar = int(getattr(ds, "PlanarConfiguration", 0) or 0)
    except Exception:
        return {"supported": False, "reason": "missing-image-attributes"}
    if bits % 8 or planar != 0:
        return {"supported": False, "reason": "unsupported-layout"}
    bps = bits // 8
    row_bytes = cols * spp * bps
    frame_size = rows * row_bytes
    if len(raw_pixels) < frame_size * frames:
        return {"supported": False, "reason": "short-pixel-data"}
    arr = np.frombuffer(raw_pixels[: frame_size * frames], dtype=np.uint8).reshape(
        frames, rows, row_bytes)
    grid = []
    px = spp * bps
    for f in range(frames):
        fr = []
        for ty in range(0, rows, TILE):
            line = []
            for tx in range(0, cols, TILE):
                block = arr[f, ty:ty + TILE, tx * px:(tx + TILE) * px]
                line.append(sha256_hex(block.tobytes())[:16])
            fr.append(line)
        grid.append(fr)
    return {"supported": True, "tile": TILE, "rows": rows, "cols": cols, "frames": frames,
            "grid": grid}


def diff_tiles(before: dict, after: dict) -> list[dict]:
    """比较分块哈希，返回每帧变化区域（像素坐标，闭区间）。"""
    if not (before.get("supported") and after.get("supported")):
        return []
    if (before["rows"], before["cols"], before["frames"]) != (after["rows"], after["cols"],
                                                              after["frames"]):
        return [{"frame": None, "note": "图像尺寸或帧数变化"}]
    t = before["tile"]
    regions = []
    for f, (gb, ga) in enumerate(zip(before["grid"], after["grid"])):
        changed = [(y, x) for y, (lb, la) in enumerate(zip(gb, ga))
                   for x, (hb, ha) in enumerate(zip(lb, la)) if hb != ha]
        if changed:
            ys = [c[0] for c in changed]
            xs = [c[1] for c in changed]
            regions.append({
                "frame": f + 1, "tiles_changed": len(changed),
                "row_range": [min(ys) * t, min(before["rows"], (max(ys) + 1) * t) - 1],
                "col_range": [min(xs) * t, min(before["cols"], (max(xs) + 1) * t) - 1],
            })
    return regions


def diff_meta(before: dict, after: dict) -> list[dict]:
    changes = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b == a:
            continue
        kw = (b or a or {}).get("kw", "")
        changes.append({
            "tag": f"({key[:4]},{key[4:]})", "keyword": kw, "key_field": kw in KEY_FIELDS,
            "before": None if b is None else b.get("v"),
            "after": None if a is None else a.get("v"),
        })
    return changes
