"""合成 DICOM 生成器（仅用于开发、测试、演示；不含任何真实患者数据）。

- CT Image Storage：MONOCHROME2，16 位有符号，Rescale -1024/1，几何体模（空气 / 水 / 骨 / 软组织插件）
- VL Endoscopic Image Storage：RGB 8 位合成图案
- Secondary Capture Image Storage：MONOCHROME2 8 位
所有实例带 InstitutionName = "SYNTHETIC TEST DATA"，便于识别。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import (CTImageStorage, ExplicitVRLittleEndian, ImplicitVRLittleEndian,
                         PYDICOM_IMPLEMENTATION_UID, SecondaryCaptureImageStorage,
                         VLEndoscopicImageStorage, generate_uid)


@dataclass
class SynthPatient:
    patient_id: str
    name: str
    birth_date: str = "19800101"
    sex: str = "O"
    charset: str | None = None   # 中文姓名使用 "ISO_IR 192"


DEFAULT_PATIENTS = [
    SynthPatient("SYN0001", "TEST^PATIENT01", "19700101", "M"),
    SynthPatient("SYN0002", "测试^患者二", "19850615", "F", "ISO_IR 192"),
    SynthPatient("SYN0003", "TEST^SAME", "19900101", "O"),
    SynthPatient("SYN0004", "TEST^SAME", "19910101", "O"),
]


@dataclass
class SynthStudy:
    patient: SynthPatient
    study_date: str = "20261105"
    study_time: str = "093000"
    accession: str = ""
    description: str = "SYNTHETIC STUDY"
    study_uid: str = field(default_factory=generate_uid)


def _base(ds_class: str, study: SynthStudy, modality: str, series_uid: str, series_number: int,
          instance_number: int, series_description: str) -> Dataset:
    p = study.patient
    ds = Dataset()
    if p.charset:
        ds.SpecificCharacterSet = p.charset
    ds.SOPClassUID = ds_class
    ds.SOPInstanceUID = generate_uid()
    ds.StudyDate = study.study_date
    ds.SeriesDate = study.study_date
    ds.ContentDate = study.study_date
    ds.StudyTime = study.study_time
    ds.ContentTime = study.study_time
    ds.AccessionNumber = study.accession
    ds.Modality = modality
    ds.Manufacturer = "SYNTHETIC"
    ds.InstitutionName = "SYNTHETIC TEST DATA"
    ds.ReferringPhysicianName = ""
    ds.StationName = "SIM"
    ds.StudyDescription = study.description
    ds.SeriesDescription = series_description
    ds.PatientName = p.name
    ds.PatientID = p.patient_id
    ds.PatientBirthDate = p.birth_date
    ds.PatientSex = p.sex
    ds.StudyInstanceUID = study.study_uid
    ds.SeriesInstanceUID = series_uid
    ds.StudyID = "1"
    ds.SeriesNumber = series_number
    ds.InstanceNumber = instance_number
    return ds


def _file_meta(ds: Dataset, ts) -> None:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = ds.SOPClassUID
    meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    meta.TransferSyntaxUID = ts
    meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
    ds.file_meta = meta


def ct_series(study: SynthStudy, n: int = 10, rows: int = 512, cols: int = 512,
              series_number: int = 1, ts=ExplicitVRLittleEndian) -> list[Dataset]:
    series_uid = generate_uid()
    frame_uid = generate_uid()
    yy, xx = np.mgrid[0:rows, 0:cols]
    cy, cx = rows / 2, cols / 2
    r = np.hypot(yy - cy, xx - cx)
    out = []
    for i in range(n):
        ds = _base(CTImageStorage, study, "CT", series_uid, series_number, i + 1,
                   "SYNTHETIC CT AXIAL")
        ds.FrameOfReferenceUID = frame_uid
        ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
        ds.KVP = "120"
        ds.SliceThickness = "5.0"
        ds.PixelSpacing = ["0.7", "0.7"]
        ds.ImagePositionPatient = ["-179.2", "-179.2", f"{-i * 5.0:.1f}"]
        ds.ImageOrientationPatient = ["1", "0", "0", "0", "1", "0"]
        ds.SliceLocation = f"{-i * 5.0:.1f}"
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.Rows, ds.Columns = rows, cols
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
        ds.PixelRepresentation = 1
        ds.RescaleIntercept = "-1024"
        ds.RescaleSlope = "1"
        ds.RescaleType = "HU"
        ds.WindowCenter = "40"
        ds.WindowWidth = "400"
        hu = np.full((rows, cols), -1000, dtype=np.int16)          # 空气
        hu[r < rows * 0.42] = 0                                     # 水
        hu[r < rows * 0.08] = 40 + 10 * (i % 5)                     # 软组织插件
        bone = np.hypot(yy - cy, xx - (cx + rows * 0.22)) < rows * 0.05
        hu[bone] = 700 + 20 * i                                     # 骨插件
        rng = np.random.default_rng(1000 + i)
        hu = hu + rng.integers(-5, 6, size=hu.shape, dtype=np.int16)
        ds.PixelData = (hu + 1024).astype("<i2").tobytes()
        _file_meta(ds, ts)
        out.append(ds)
    return out


def endo_series(study: SynthStudy, n: int = 5, rows: int = 480, cols: int = 640,
                series_number: int = 1, ts=ExplicitVRLittleEndian) -> list[Dataset]:
    series_uid = generate_uid()
    yy, xx = np.mgrid[0:rows, 0:cols]
    out = []
    for i in range(n):
        ds = _base(VLEndoscopicImageStorage, study, "ES", series_uid, series_number, i + 1,
                   "SYNTHETIC ENDOSCOPY")
        ds.ImageType = ["ORIGINAL", "PRIMARY"]
        ds.AcquisitionDateTime = study.study_date + study.study_time
        ds.SamplesPerPixel = 3
        ds.PhotometricInterpretation = "RGB"
        ds.PlanarConfiguration = 0
        ds.Rows, ds.Columns = rows, cols
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 8, 8, 7
        ds.PixelRepresentation = 0
        ds.LossyImageCompression = "00"
        img = np.zeros((rows, cols, 3), dtype=np.uint8)
        rr = np.hypot(yy - rows / 2, xx - cols / 2)
        img[..., 0] = np.clip(200 - rr * 0.6 + 10 * i, 0, 255).astype(np.uint8)
        img[..., 1] = np.clip(90 + 40 * np.sin(xx / (25 + i)), 0, 255).astype(np.uint8)
        img[..., 2] = np.clip(70 + 30 * np.cos(yy / 30), 0, 255).astype(np.uint8)
        img[rr > min(rows, cols) * 0.48] = 0
        ds.PixelData = img.tobytes()
        _file_meta(ds, ts)
        out.append(ds)
    return out


def sc_series(study: SynthStudy, n: int = 2, rows: int = 256, cols: int = 256,
              ts=ExplicitVRLittleEndian) -> list[Dataset]:
    series_uid = generate_uid()
    out = []
    for i in range(n):
        ds = _base(SecondaryCaptureImageStorage, study, "OT", series_uid, 99, i + 1,
                   "SYNTHETIC SC")
        ds.ConversionType = "WSD"
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.Rows, ds.Columns = rows, cols
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 8, 8, 7
        ds.PixelRepresentation = 0
        ds.PixelData = (np.add.outer(np.arange(rows), np.arange(cols)) % 256).astype(
            np.uint8).tobytes()
        _file_meta(ds, ts)
        out.append(ds)
    return out


def write_datasets(datasets: list[Dataset], out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for ds in datasets:
        ts = ds.file_meta.TransferSyntaxUID
        p = out_dir / f"{ds.Modality}_{ds.SOPInstanceUID}.dcm"
        ds.save_as(p, implicit_vr=(ts == ImplicitVRLittleEndian), little_endian=True,
                   enforce_file_format=True)
        paths.append(p)
    return paths


def demo_dataset(out_dir: Path, ct_slices: int = 20, endo_frames: int = 5,
                 ct_size: int = 512) -> dict[str, list[Path]]:
    """演示用数据：SYN0001 的 CT 与内镜检查、SYN0002（中文姓名）的 CT 检查。"""
    p1, p2 = DEFAULT_PATIENTS[0], DEFAULT_PATIENTS[1]
    ct1 = SynthStudy(p1, "20261105", "093000", "ACC1001", "SYNTHETIC CT CHEST")
    es1 = SynthStudy(p1, "20261108", "141500", "ACC1002", "SYNTHETIC ENDOSCOPY")
    ct2 = SynthStudy(p2, "20261110", "101000", "ACC2001", "SYNTHETIC CT HEAD")
    out_dir = Path(out_dir)
    return {
        "CT_SYN0001": write_datasets(ct_series(ct1, ct_slices, ct_size, ct_size),
                                     out_dir / "ct_syn0001"),
        "ES_SYN0001": write_datasets(endo_series(es1, endo_frames), out_dir / "es_syn0001"),
        "CT_SYN0002": write_datasets(ct_series(ct2, max(2, ct_slices // 4), ct_size, ct_size),
                                     out_dir / "ct_syn0002"),
    }
