"""完整性基线与核查（R14、R15、R16）。

基线在入库时由 B 计算，对象是 B 收到的网络数据集字节及由此写出的文件。
核查只读：MUST NOT 修改任何库内文件。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .dicomutil import (diff_meta, diff_tiles, meta_hash, meta_snapshot, parse_file_bytes,
                        pixel_bytes, tile_hashes)
from .util import canonical_json, hmac_equal, hmac_hex, now_utc, sha256_hex

LOG = logging.getLogger("pacs.integrity")

CATEGORIES = ("FILE_MISSING", "UNREADABLE", "PIXEL_CHANGED", "METADATA_CHANGED",
              "STRUCTURE_CHANGED", "INDEX_MISMATCH", "BASELINE_TAMPERED", "VERIFY_ERROR")


@dataclass
class Baseline:
    file_sha256: str
    dataset_sha256: str
    pixel_sha256: str
    meta_sha256: str
    meta_json: str
    tiles_json: str


def compute_baseline(raw_dataset: bytes, ds, file_bytes: bytes, transfer_syntax: str) -> Baseline:
    px = pixel_bytes(ds)
    snap = meta_snapshot(ds)
    tiles = tile_hashes(ds, transfer_syntax, px)
    return Baseline(
        file_sha256=sha256_hex(file_bytes),
        dataset_sha256=sha256_hex(raw_dataset),
        pixel_sha256=sha256_hex(px) if px else "",
        meta_sha256=meta_hash(snap),
        meta_json=canonical_json(snap),
        tiles_json=canonical_json(tiles),
    )


def baseline_mac(key: bytes, sop_uid: str, rel_path: str, b) -> str:
    """b 可以是 Baseline 或数据库行。"""
    g = (lambda k: b[k]) if not isinstance(b, Baseline) else (lambda k: getattr(b, k))
    msg = "|".join([sop_uid, rel_path, g("file_sha256"), g("dataset_sha256"), g("pixel_sha256"),
                    g("meta_sha256"), sha256_hex(g("meta_json").encode()),
                    sha256_hex(g("tiles_json").encode())])
    return hmac_hex(key, msg)


# 索引字段 -> 元数据快照中的标签
_PATIENT_FIELDS = {"patient_name": "00100010", "birth_date": "00100030", "sex": "00100040"}
_STUDY_FIELDS = {"study_date": "00080020", "study_time": "00080030", "accession": "00080050",
                 "study_id": "00200010", "description": "00081030", "referring": "00080090"}
_SERIES_FIELDS = {"modality": "00080060", "series_number": "00200011",
                  "description": "0008103E"}

INSTANCE_ROW_SQL = """
SELECT i.*, s.series_uid, s.id AS series_id, s.origin_instance_id AS series_origin,
       s.modality, s.series_number, s.description AS series_description,
       st.study_uid, st.id AS study_id_pk, st.origin_instance_id AS study_origin,
       st.study_date, st.study_time, st.accession, st.study_id, st.description AS study_description,
       st.referring,
       p.patient_id, p.issuer, p.id AS patient_pk, p.origin_instance_id AS patient_origin,
       p.patient_name, p.birth_date, p.sex, p.demographics_conflict,
       b.file_sha256, b.dataset_sha256, b.pixel_sha256, b.meta_sha256, b.meta_json, b.tiles_json,
       b.hmac AS baseline_hmac
FROM instances i
JOIN series s ON s.id = i.series_pk
JOIN studies st ON st.id = s.study_pk
JOIN patients p ON p.id = st.patient_pk
LEFT JOIN baselines b ON b.instance_id = i.id
"""


def load_baseline_meta(text) -> tuple[dict | None, str | None]:
    """解析基线元数据快照；格式或结构无效时返回 (None, 原因)，绝不抛异常。"""
    if text is None:
        return None, "缺失"
    try:
        v = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, f"无法解析（{type(exc).__name__}）"
    if not isinstance(v, dict) or not all(
            isinstance(k, str) and isinstance(e, dict) and "v" in e for k, e in v.items()):
        return None, "结构无效（应为 {标签: {kw, vr, v}}）"
    return v, None


def load_baseline_tiles(text) -> tuple[dict | None, str | None]:
    """解析基线分块哈希；格式或结构无效时返回 (None, 原因)。"""
    if text is None:
        return None, "缺失"
    try:
        v = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, f"无法解析（{type(exc).__name__}）"
    if not isinstance(v, dict) or "supported" not in v:
        return None, "结构无效（应为含 supported 的对象）"
    if v["supported"]:
        grid = v.get("grid")
        ok = (all(isinstance(v.get(k), int) for k in ("tile", "rows", "cols", "frames"))
              and isinstance(grid, list) and len(grid) == v["frames"]
              and all(isinstance(fr, list) and all(isinstance(line, list) for line in fr)
                      for fr in grid))
        if not ok:
            return None, "结构无效（分块网格不完整）"
    return v, None


def _snap_value(snap: dict, tag: str) -> str:
    v = snap.get(tag, {}).get("v", "")
    if isinstance(v, list):
        return "\\".join(v)
    return "" if v is None else str(v)


class Verifier:
    def __init__(self, svc):
        self.svc = svc

    # ---------- 行读取 ----------
    def instance_rows(self, scope: dict | None = None) -> list:
        sql = INSTANCE_ROW_SQL
        params: list = []
        if scope:
            conds = []
            for key, col in (("sop_uid", "i.sop_uid"), ("series_uid", "s.series_uid"),
                             ("study_uid", "st.study_uid"), ("patient_id", "p.patient_id")):
                if scope.get(key):
                    conds.append(f"{col}=?")
                    params.append(scope[key])
            if conds:
                sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY i.id"
        return self.svc.db.query(sql, tuple(params))

    def instance_row(self, sop_uid: str):
        rows = self.instance_rows({"sop_uid": sop_uid})
        return rows[0] if rows else None

    # ---------- 核查单个实例 ----------
    def _ident(self, row) -> dict:
        return {"patient_id": row["patient_id"], "study_uid": row["study_uid"],
                "series_uid": row["series_uid"], "sop_uid": row["sop_uid"]}

    def baseline_ok(self, row) -> bool:
        if row["baseline_hmac"] is None:
            return False
        return hmac_equal(baseline_mac(self.svc.key, row["sop_uid"], row["rel_path"], row),
                          row["baseline_hmac"])

    def quick_check(self, row) -> tuple[bool, bytes | None]:
        """调阅 / 重发前的快速校验：基线 HMAC + 文件哈希。返回 (是否通过, 文件字节)。"""
        path = self.svc.config.store_dir / row["rel_path"]
        if not self.baseline_ok(row):
            return False, None
        try:
            data = path.read_bytes()
        except OSError:
            return False, None
        return sha256_hex(data) == row["file_sha256"], data

    def verify_instance(self, row) -> list[dict]:
        """单实例核查，带隔离：任何意外异常都转成 VERIFY_ERROR，不中断批量核查或取回（独立复核后修复）。"""
        try:
            return self._verify_instance(row)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("verify_instance failed for %s", row["sop_uid"])
            return [{"category": "VERIFY_ERROR", "instance_id": row["id"], **self._ident(row),
                     "details": {"error": f"{type(exc).__name__}: {exc}"[:300],
                                 "note": "该实例核查过程异常，已跳过；其他实例继续核查。需人工检查"}}]

    def _verify_instance(self, row) -> list[dict]:
        findings: list[dict] = []
        ident = self._ident(row)

        def add(cat: str, **details):
            findings.append({"category": cat, "instance_id": row["id"], **ident,
                             "details": details})

        path = self.svc.config.store_dir / row["rel_path"]
        # 基线是不可信输入：先判定缺失 / HMAC / JSON 格式与结构，再决定能做哪些比对
        if row["baseline_hmac"] is None:
            add("BASELINE_TAMPERED", problems=["基线记录缺失"],
                note="没有可比对的基线：只检查文件是否存在，不做内容与索引比对")
            if not path.exists():
                add("FILE_MISSING", path=str(row["rel_path"]))
            ctx = self._context(row, path)
            for f in findings:
                f["details"].setdefault("context", ctx)
            return findings
        base_trusted = self.baseline_ok(row)
        base_meta, meta_err = load_baseline_meta(row["meta_json"])
        tiles_before, tiles_err = load_baseline_tiles(row["tiles_json"])
        problems = [] if base_trusted else ["基线记录 HMAC 不符"]
        if meta_err:
            problems.append(f"meta_json {meta_err}")
        if tiles_err:
            problems.append(f"tiles_json {tiles_err}")
        if problems:
            add("BASELINE_TAMPERED", problems=problems,
                note="基线本身不可信；以下比对结果仅供参考，无法解析的部分已跳过")

        if not path.exists():
            add("FILE_MISSING", path=str(row["rel_path"]))
        else:
            data = path.read_bytes()
            if sha256_hex(data) != row["file_sha256"]:
                try:
                    ds = parse_file_bytes(data)
                    px = pixel_bytes(ds)
                    snap = meta_snapshot(ds)
                except Exception as exc:  # noqa: BLE001
                    add("UNREADABLE", error=f"{type(exc).__name__}: {exc}"[:300])
                else:
                    px_sha = sha256_hex(px) if px else ""
                    changed_any = False
                    if px_sha != row["pixel_sha256"]:
                        changed_any = True
                        regions = []
                        if tiles_before is not None:
                            regions = diff_tiles(tiles_before,
                                                 tile_hashes(ds, row["transfer_syntax"], px))
                        note = "" if regions else (
                            "基线分块哈希不可用，只能报告整体像素变化" if tiles_before is None else
                            "压缩数据或布局不支持分块定位，只能报告整体像素变化")
                        add("PIXEL_CHANGED", baseline_pixel_sha256=row["pixel_sha256"],
                            current_pixel_sha256=px_sha, regions=regions, note=note)
                    if meta_hash(snap) != row["meta_sha256"]:
                        changed_any = True
                        if base_meta is not None:
                            add("METADATA_CHANGED", changes=diff_meta(base_meta, snap))
                        else:
                            add("METADATA_CHANGED", changes=None,
                                note="基线元数据快照不可用，只能确认元数据哈希不符，无法给出前值")
                    if not changed_any and not problems:
                        add("STRUCTURE_CHANGED", baseline_file_sha256=row["file_sha256"],
                            current_file_sha256=sha256_hex(data),
                            note="像素与元数据快照未变，文件编码或 File Meta 变化")
        if base_meta is not None:
            findings.extend(self._index_findings(row, base_meta, ident))
        if findings:
            ctx = self._context(row, path)
            for f in findings:
                f["details"].setdefault("context", ctx)
        return findings

    def _index_findings(self, row, snap: dict, ident: dict) -> list[dict]:
        diffs = []

        def cmp(field, idx_val, tag):
            base_val = _snap_value(snap, tag)
            if (idx_val or "") != base_val:
                diffs.append({"field": field, "tag": f"({tag[:4]},{tag[4:]})",
                              "index_value": idx_val, "baseline_value": base_val})

        cmp("instances.sop_uid", row["sop_uid"], "00080018")
        cmp("instances.sop_class_uid", row["sop_class_uid"], "00080016")
        cmp("instances.instance_number", row["instance_number"], "00200013")
        cmp("series.series_uid", row["series_uid"], "0020000E")
        cmp("studies.study_uid", row["study_uid"], "0020000D")
        cmp("patients.patient_id", row["patient_id"], "00100020")
        if row["patient_origin"] == row["id"]:
            for f, t in _PATIENT_FIELDS.items():
                cmp(f"patients.{f}", row[f], t)
        if row["study_origin"] == row["id"]:
            for f, t in _STUDY_FIELDS.items():
                col = "study_description" if f == "description" else f
                cmp(f"studies.{f}", row[col], t)
        if row["series_origin"] == row["id"]:
            for f, t in _SERIES_FIELDS.items():
                col = "series_description" if f == "description" else f
                cmp(f"series.{f}", row[col], t)
        if not diffs:
            return []
        return [{"category": "INDEX_MISMATCH", "instance_id": row["id"], **ident,
                 "details": {"diffs": diffs}}]

    def _context(self, row, path: Path) -> dict:
        last = self.svc.db.query_one(
            "SELECT seq, ts_utc, event_type, actor, actor_ip FROM audit"
            " WHERE target=? OR details_json LIKE ? ORDER BY seq DESC LIMIT 1",
            (f"instance:{row['sop_uid']}", f"%{row['sop_uid']}%"))
        ctx = {"detected_at": now_utc(),
               "last_recorded_access": dict(last) if last else None,
               "received_at": row["received_at"], "source_ae": row["source_ae"]}
        try:
            mtime = datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc).isoformat()
            ctx["file_mtime_untrusted"] = mtime
        except OSError:
            pass
        return ctx

    # ---------- 批量核查 ----------
    def run(self, scope: dict | None = None, trigger: str = "manual",
            operator: str = "system") -> dict:
        rows = self.instance_rows(scope)
        started = now_utc()
        with self.svc.db.write() as conn:
            vid = conn.execute("INSERT INTO verifications(started_at, trigger, operator, scope)"
                               " VALUES (?,?,?,?)",
                               (started, trigger, operator, canonical_json(scope or {}))).lastrowid
        all_findings: list[dict] = []
        ok = 0
        for row in rows:
            fs = self.verify_instance(row)
            if fs:
                all_findings.extend(fs)
            else:
                ok += 1
        affected = {f["sop_uid"] for f in all_findings}
        with self.svc.db.write() as conn:
            for f in all_findings:
                conn.execute("INSERT INTO findings(verification_id, detected_at, instance_id,"
                             " sop_uid, category, details_json) VALUES (?,?,?,?,?,?)",
                             (vid, now_utc(), f["instance_id"], f["sop_uid"], f["category"],
                              canonical_json(f["details"])))
            conn.execute("UPDATE verifications SET finished_at=?, checked=?, ok=?, anomalies=?"
                         " WHERE id=?", (now_utc(), len(rows), ok, len(affected), vid))
            rec = self.svc.audit.append(
                "VERIFY_RUN", actor=operator, target=f"verification:{vid}",
                outcome="success" if not all_findings else "warning",
                details={"trigger": trigger, "scope": scope or {}, "checked": len(rows), "ok": ok,
                         "anomalous_instances": len(affected),
                         "categories": sorted({f["category"] for f in all_findings})},
                conn=conn)
        self.svc.audit.anchor_safely(rec)
        for f in all_findings:
            self.raise_finding_alert(f)
        return {"verification_id": vid, "checked": len(rows), "ok": ok,
                "anomalous_instances": len(affected), "findings": all_findings}

    def raise_finding_alert(self, f: dict) -> int:
        subject = f"instance:{f['sop_uid']}"
        details = {k: f[k] for k in ("patient_id", "study_uid", "series_uid", "sop_uid")}
        details.update({k: v for k, v in f["details"].items()})
        return self.svc.alerts.raise_alert(f["category"], subject, details,
                                           aggregate_key=f"{f['category']}:{f['sop_uid']}")
