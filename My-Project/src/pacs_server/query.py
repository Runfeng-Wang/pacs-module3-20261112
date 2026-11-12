"""C-FIND 分层查询与 C-MOVE / C-GET 匹配（R9–R11）。

只支持分层查询（不支持关系查询）。匹配方式：单值、通配符（* ?）、日期 / 时间范围、
UID 列表（反斜杠分隔）、通用匹配（空值）。PN 匹配不区分大小写（在一致性声明中说明）。
"""

from __future__ import annotations

import re
from typing import Iterator

from pydicom.dataset import Dataset
from pydicom.datadict import dictionary_VR, tag_for_keyword
from pydicom.multival import MultiValue
from pydicom.sequence import Sequence

from .integrity import INSTANCE_ROW_SQL

STATUS_IDENTIFIER_INVALID = 0xA900
STATUS_UNABLE_TO_PROCESS = 0xC000

LEVELS = ("PATIENT", "STUDY", "SERIES", "IMAGE")

PATIENT_KEYS = ("PatientName", "PatientID", "IssuerOfPatientID", "PatientBirthDate",
                "PatientSex", "NumberOfPatientRelatedStudies", "NumberOfPatientRelatedSeries",
                "NumberOfPatientRelatedInstances")
STUDY_KEYS = ("StudyInstanceUID", "StudyDate", "StudyTime", "AccessionNumber", "StudyID",
              "StudyDescription", "ReferringPhysicianName", "ModalitiesInStudy",
              "NumberOfStudyRelatedSeries", "NumberOfStudyRelatedInstances")
SERIES_KEYS = ("SeriesInstanceUID", "Modality", "SeriesNumber", "SeriesDescription",
               "NumberOfSeriesRelatedInstances")
IMAGE_KEYS = ("SOPInstanceUID", "SOPClassUID", "InstanceNumber")

UNIQUE_KEY = {"PATIENT": "PatientID", "STUDY": "StudyInstanceUID",
              "SERIES": "SeriesInstanceUID", "IMAGE": "SOPInstanceUID"}

# 分层查询：各层级必须给出的上层唯一键
REQUIRED_UPPER = {
    ("P", "PATIENT"): (), ("P", "STUDY"): ("PatientID",),
    ("P", "SERIES"): ("PatientID", "StudyInstanceUID"),
    ("P", "IMAGE"): ("PatientID", "StudyInstanceUID", "SeriesInstanceUID"),
    ("S", "STUDY"): (), ("S", "SERIES"): ("StudyInstanceUID",),
    ("S", "IMAGE"): ("StudyInstanceUID", "SeriesInstanceUID"),
}


class QueryError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _str_values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (MultiValue, list, tuple)):
        return [str(v) for v in value]
    s = str(value)
    return [s] if s != "" else []


def _wildcard_regex(pattern: str, ignore_case: bool) -> re.Pattern:
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE if ignore_case else 0)


def _match_range(pattern: str, value: str) -> bool:
    lo, hi = pattern.split("-", 1)
    if not value:
        return False
    if lo and value < lo:
        return False
    if hi and value > hi:
        return False
    return True


def match_value(keyword: str, query_values: list[str], record_value) -> bool:
    """单个属性的匹配。query_values 为空 = 通用匹配。"""
    if not query_values:
        return True
    vr = dictionary_VR(tag_for_keyword(keyword)) if tag_for_keyword(keyword) else ""
    rec_values = record_value if isinstance(record_value, list) else [str(record_value or "")]
    if vr == "UI":  # UID 列表匹配
        return any(r in query_values for r in rec_values)
    q = query_values[0] if len(query_values) == 1 else None
    if q is None:  # 多值查询（如 ModalitiesInStudy=CT\ES）：任一匹配
        return any(match_value(keyword, [qv], record_value) for qv in query_values)
    if vr in ("DA", "TM", "DT") and "-" in q:
        return any(_match_range(q, r) for r in rec_values)
    ignore_case = vr == "PN"
    if "*" in q or "?" in q:
        rx = _wildcard_regex(q, ignore_case)
        return any(rx.match(r) for r in rec_values)
    if ignore_case:
        return any(r.lower() == q.lower() for r in rec_values)
    return any(r == q for r in rec_values)


class QueryEngine:
    def __init__(self, svc):
        self.svc = svc
        self.db = svc.db

    # ---------- 取得某层级的扁平记录 ----------
    def _records(self, level: str) -> list[dict]:
        db = self.db
        if level == "PATIENT":
            rows = db.query("""
                SELECT p.*, (SELECT COUNT(*) FROM studies st WHERE st.patient_pk=p.id) AS n_st,
                  (SELECT COUNT(*) FROM series s JOIN studies st ON s.study_pk=st.id
                     WHERE st.patient_pk=p.id) AS n_se,
                  (SELECT COUNT(*) FROM instances i JOIN series s ON i.series_pk=s.id
                     JOIN studies st ON s.study_pk=st.id WHERE st.patient_pk=p.id) AS n_in
                FROM patients p ORDER BY p.id""")
            return [self._patient_rec(r) | {
                "NumberOfPatientRelatedStudies": str(r["n_st"]),
                "NumberOfPatientRelatedSeries": str(r["n_se"]),
                "NumberOfPatientRelatedInstances": str(r["n_in"])} for r in rows]
        if level == "STUDY":
            rows = db.query("""
                SELECT st.*, p.patient_id, p.issuer, p.patient_name, p.birth_date, p.sex,
                  (SELECT COUNT(*) FROM series s WHERE s.study_pk=st.id) AS n_se,
                  (SELECT COUNT(*) FROM instances i JOIN series s ON i.series_pk=s.id
                     WHERE s.study_pk=st.id) AS n_in,
                  (SELECT GROUP_CONCAT(DISTINCT s.modality) FROM series s WHERE s.study_pk=st.id) AS mods,
                  (SELECT i.charset FROM instances i JOIN series s ON i.series_pk=s.id
                     WHERE s.study_pk=st.id LIMIT 1) AS charset
                FROM studies st JOIN patients p ON p.id=st.patient_pk ORDER BY st.id""")
            return [self._patient_rec(r) | self._study_rec(r) | {
                "ModalitiesInStudy": sorted([m for m in (r["mods"] or "").split(",") if m]),
                "NumberOfStudyRelatedSeries": str(r["n_se"]),
                "NumberOfStudyRelatedInstances": str(r["n_in"])} for r in rows]
        if level == "SERIES":
            rows = db.query("""
                SELECT s.*, s.description AS series_description, st.study_uid, st.study_date,
                  st.study_time, st.accession, st.study_id, st.description AS study_description,
                  st.referring, p.patient_id, p.issuer, p.patient_name, p.birth_date, p.sex,
                  (SELECT COUNT(*) FROM instances i WHERE i.series_pk=s.id) AS n_in
                FROM series s JOIN studies st ON st.id=s.study_pk
                JOIN patients p ON p.id=st.patient_pk ORDER BY s.id""")
            return [self._patient_rec(r) | self._study_rec(r, "study_description") |
                    self._series_rec(r) | {"NumberOfSeriesRelatedInstances": str(r["n_in"])}
                    for r in rows]
        rows = db.query(INSTANCE_ROW_SQL + " ORDER BY i.id")
        return [self._patient_rec(r) | self._study_rec(r, "study_description") |
                self._series_rec(r) | {"SOPInstanceUID": r["sop_uid"],
                                       "SOPClassUID": r["sop_class_uid"],
                                       "InstanceNumber": r["instance_number"],
                                       "_row": r} for r in rows]

    @staticmethod
    def _patient_rec(r) -> dict:
        return {"PatientID": r["patient_id"], "IssuerOfPatientID": r["issuer"],
                "PatientName": r["patient_name"], "PatientBirthDate": r["birth_date"],
                "PatientSex": r["sex"]}

    @staticmethod
    def _study_rec(r, desc_col: str = "description") -> dict:
        return {"StudyInstanceUID": r["study_uid"], "StudyDate": r["study_date"],
                "StudyTime": r["study_time"], "AccessionNumber": r["accession"],
                "StudyID": r["study_id"], "StudyDescription": r[desc_col],
                "ReferringPhysicianName": r["referring"]}

    @staticmethod
    def _series_rec(r) -> dict:
        return {"SeriesInstanceUID": r["series_uid"], "Modality": r["modality"],
                "SeriesNumber": r["series_number"],
                "SeriesDescription": r["series_description"]}

    # ---------- 校验与匹配 ----------
    @staticmethod
    def _level(identifier: Dataset, model: str) -> str:
        level = str(identifier.get("QueryRetrieveLevel", "") or "").strip().upper()
        if level not in LEVELS or (model, level) not in REQUIRED_UPPER:
            raise QueryError(STATUS_IDENTIFIER_INVALID,
                             f"QueryRetrieveLevel {level!r} 不适用于该信息模型")
        return level

    def _query_terms(self, identifier: Dataset) -> list[tuple[str, list[str]]]:
        terms = []
        for elem in identifier:
            kw = elem.keyword
            if not kw or kw in ("QueryRetrieveLevel", "SpecificCharacterSet") or elem.VR == "SQ":
                continue
            terms.append((kw, _str_values(elem.value)))
        return terms

    def _matching(self, model: str, identifier: Dataset) -> tuple[str, list[dict], list]:
        level = self._level(identifier, model)
        terms = self._query_terms(identifier)
        given = {kw: vals for kw, vals in terms}
        for req in REQUIRED_UPPER[(model, level)]:
            if not given.get(req):
                raise QueryError(STATUS_IDENTIFIER_INVALID,
                                 f"{level} 级分层查询缺少上层唯一键 {req}")
        records = self._records(level)
        out = [rec for rec in records
               if all(match_value(kw, vals, rec[kw]) for kw, vals in terms if kw in rec)]
        return level, out, terms

    # ---------- C-FIND ----------
    def find(self, model: str, identifier: Dataset) -> Iterator[Dataset]:
        level, matches, terms = self._matching(model, identifier)
        for rec in matches:
            ds = Dataset()
            ds.SpecificCharacterSet = "ISO_IR 192"
            ds.QueryRetrieveLevel = level
            ds.RetrieveAETitle = self.svc.config.ae_title
            keys = [kw for kw, _ in terms]
            if UNIQUE_KEY[level] not in keys:
                keys.append(UNIQUE_KEY[level])
            for kw in keys:
                tag = tag_for_keyword(kw)
                if tag is None:
                    continue
                if dictionary_VR(tag) == "SQ":
                    setattr(ds, kw, Sequence([]))
                    continue
                val = rec.get(kw, "")
                if isinstance(val, list):
                    val = val if len(val) != 1 else val[0]
                setattr(ds, kw, val if val not in (None,) else "")
            yield ds

    def count_matches(self, model: str, identifier: Dataset) -> int:
        return len(self._matching(model, identifier)[1])

    # ---------- C-MOVE / C-GET ----------
    def resolve_retrieve(self, model: str, identifier: Dataset) -> list:
        level = self._level(identifier, model)
        uk = UNIQUE_KEY[level]
        vals = _str_values(identifier.get(uk, ""))
        if not vals:
            raise QueryError(STATUS_IDENTIFIER_INVALID, f"{level} 级取回缺少唯一键 {uk}")
        col = {"PATIENT": "p.patient_id", "STUDY": "st.study_uid", "SERIES": "s.series_uid",
               "IMAGE": "i.sop_uid"}[level]
        placeholders = ",".join("?" * len(vals))
        return self.db.query(INSTANCE_ROW_SQL + f" WHERE {col} IN ({placeholders}) ORDER BY i.id",
                             tuple(vals))
