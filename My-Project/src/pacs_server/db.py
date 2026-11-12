"""SQLite 数据库：表结构、写锁与查询帮助函数。

并发策略（design D-J）：进程内一个共享连接 + RLock；写操作用 BEGIN IMMEDIATE 并尽量短；
WAL 模式允许另一个进程（例如独立运行的 GUI）同时读。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS peers (
    id INTEGER PRIMARY KEY,
    ae_title TEXT NOT NULL UNIQUE,
    host TEXT NOT NULL,
    port INTEGER,
    roles TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY,
    patient_id TEXT NOT NULL,
    issuer TEXT NOT NULL DEFAULT '',
    patient_name TEXT NOT NULL DEFAULT '',
    birth_date TEXT NOT NULL DEFAULT '',
    sex TEXT NOT NULL DEFAULT '',
    demographics_conflict INTEGER NOT NULL DEFAULT 0,
    origin_instance_id INTEGER,
    UNIQUE (patient_id, issuer)
);
CREATE TABLE IF NOT EXISTS studies (
    id INTEGER PRIMARY KEY,
    study_uid TEXT NOT NULL UNIQUE,
    patient_pk INTEGER NOT NULL REFERENCES patients(id),
    study_date TEXT NOT NULL DEFAULT '',
    study_time TEXT NOT NULL DEFAULT '',
    accession TEXT NOT NULL DEFAULT '',
    study_id TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    referring TEXT NOT NULL DEFAULT '',
    origin_instance_id INTEGER
);
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY,
    series_uid TEXT NOT NULL UNIQUE,
    study_pk INTEGER NOT NULL REFERENCES studies(id),
    modality TEXT NOT NULL DEFAULT '',
    series_number TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    origin_instance_id INTEGER
);
CREATE TABLE IF NOT EXISTS instances (
    id INTEGER PRIMARY KEY,
    sop_uid TEXT NOT NULL UNIQUE,
    sop_class_uid TEXT NOT NULL,
    series_pk INTEGER NOT NULL REFERENCES series(id),
    instance_number TEXT NOT NULL DEFAULT '',
    transfer_syntax TEXT NOT NULL,
    charset TEXT NOT NULL DEFAULT '',
    rel_path TEXT NOT NULL UNIQUE,
    file_size INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    source_ae TEXT NOT NULL,
    source_ip TEXT NOT NULL,
    assoc_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS baselines (
    instance_id INTEGER PRIMARY KEY REFERENCES instances(id),
    file_sha256 TEXT NOT NULL,
    dataset_sha256 TEXT NOT NULL,
    pixel_sha256 TEXT NOT NULL,
    meta_sha256 TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    tiles_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    hmac TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    sop_uid TEXT NOT NULL DEFAULT '',
    rel_path TEXT NOT NULL,
    source_ae TEXT NOT NULL DEFAULT '',
    source_ip TEXT NOT NULL DEFAULT '',
    dataset_sha256 TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    type TEXT NOT NULL,
    subject TEXT NOT NULL,
    details_json TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    count INTEGER NOT NULL DEFAULT 1,
    aggregate_key TEXT,
    ack_by TEXT,
    ack_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_key ON alerts(aggregate_key, status);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    ts_local TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_ip TEXT NOT NULL,
    target TEXT NOT NULL,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    hmac TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,
    operator TEXT NOT NULL,
    scope TEXT NOT NULL,
    checked INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 0,
    anomalies INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    verification_id INTEGER REFERENCES verifications(id),
    detected_at TEXT NOT NULL,
    instance_id INTEGER,
    sop_uid TEXT NOT NULL,
    category TEXT NOT NULL,
    details_json TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                     isolation_level=None, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
        # 仅测试使用：提交前抛错以模拟事务失败
        self.fail_next_commit = False

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """进程级写锁 + BEGIN IMMEDIATE；异常时回滚。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                if self.fail_next_commit:
                    self.fail_next_commit = False
                    raise sqlite3.OperationalError("fault injection: commit failed")
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
