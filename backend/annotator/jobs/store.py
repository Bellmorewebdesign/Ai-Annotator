"""Durable job store on SQLite (WAL mode) with lease-based claiming.

A worker claims a job by taking a time-limited lease and renews it with a heartbeat.
If the worker dies, the lease expires and another worker (or the restarted one)
reclaims the job, up to a maximum number of attempts. All writes from a worker are
guarded by its lease so a stale worker can never overwrite a job it lost.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    phase TEXT,
    progress TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    expires_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at REAL,
    error_code TEXT,
    error_message TEXT,
    input TEXT NOT NULL DEFAULT '{}',
    options TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    metrics TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
CREATE TABLE IF NOT EXISTS phase_stats (
    phase TEXT NOT NULL,
    units REAL NOT NULL,
    seconds REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS phase_stats_phase ON phase_stats(phase, created_at);
CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    heartbeat_at REAL NOT NULL,
    started_at REAL NOT NULL
);
"""

ACTIVE = ("queued", "running")


def new_job_id() -> str:
    return secrets.token_urlsafe(16)  # 22 chars, 128 bits


@dataclass
class Job:
    id: str
    status: str
    phase: str | None
    progress: dict[str, Any]
    created_at: float
    updated_at: float
    started_at: float | None
    finished_at: float | None
    expires_at: float | None
    attempts: int
    error_code: str | None
    error_message: str | None
    input: dict[str, Any]
    options: dict[str, Any]
    result: dict[str, Any] | None
    metrics: dict[str, Any] = field(default_factory=dict)
    lease_owner: str | None = None


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], status=row["status"], phase=row["phase"], progress=json.loads(row["progress"] or "{}"),
        created_at=row["created_at"], updated_at=row["updated_at"], started_at=row["started_at"],
        finished_at=row["finished_at"], expires_at=row["expires_at"], attempts=row["attempts"],
        error_code=row["error_code"], error_message=row["error_message"], input=json.loads(row["input"] or "{}"),
        options=json.loads(row["options"] or "{}"), result=json.loads(row["result"]) if row["result"] else None,
        metrics=json.loads(row["metrics"] or "{}"), lease_owner=row["lease_owner"],
    )


class LeaseLost(Exception):
    """The worker no longer holds the job (expired lease, or the job was deleted)."""


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
        finally:
            conn.close()

    # --- API side ---------------------------------------------------------------------

    def create(self, job_id: str, input_info: dict[str, Any], options: dict[str, Any],
               retention_hours: int) -> Job:
        now = time.time()
        expires = now + retention_hours * 3600 if retention_hours > 0 else None
        with self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id, status, phase, progress, created_at, updated_at, expires_at, input, options)"
                " VALUES (?, 'queued', 'upload', ?, ?, ?, ?, ?, ?)",
                (job_id, json.dumps({}), now, now, expires, json.dumps(input_info), json.dumps(options)),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> Job | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def delete(self, job_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0

    def queue_position(self, job: Job) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND created_at < ?",
                            (job.created_at,)).fetchone()
        return int(row[0])

    def expired(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        with self._conn() as c:
            rows = c.execute("SELECT id FROM jobs WHERE expires_at IS NOT NULL AND expires_at < ? AND status NOT IN"
                             " ('running')", (now,)).fetchall()
        return [r[0] for r in rows]

    # --- Worker side ------------------------------------------------------------------

    def claim_next(self, worker_id: str, lease_seconds: int, max_attempts: int) -> Job | None:
        now = time.time()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                while True:
                    row = c.execute(
                        "SELECT id, attempts FROM jobs WHERE status = 'queued' OR (status = 'running' AND"
                        " lease_expires_at < ?) ORDER BY created_at LIMIT 1", (now,)).fetchone()
                    if row is None:
                        c.execute("COMMIT")
                        return None
                    if row["attempts"] >= max_attempts:
                        c.execute(
                            "UPDATE jobs SET status = 'failed', error_code = 'interrupted', error_message = ?,"
                            " lease_owner = NULL, lease_expires_at = NULL, finished_at = ?, updated_at = ?"
                            " WHERE id = ?",
                            ("Processing was interrupted repeatedly and has stopped. Please try again.",
                             now, now, row["id"]))
                        continue
                    c.execute(
                        "UPDATE jobs SET status = 'running', attempts = attempts + 1, lease_owner = ?,"
                        " lease_expires_at = ?, started_at = COALESCE(started_at, ?), updated_at = ?,"
                        " error_code = NULL, error_message = NULL WHERE id = ?",
                        (worker_id, now + lease_seconds, now, now, row["id"]))
                    c.execute("COMMIT")
                    job = self.get(row["id"])
                    return job
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def _guarded(self, job_id: str, worker_id: str, sql: str, params: tuple) -> None:
        with self._conn() as c:
            cur = c.execute(sql + " WHERE id = ? AND lease_owner = ? AND status = 'running'",
                            params + (job_id, worker_id))
        if cur.rowcount == 0:
            raise LeaseLost(job_id)

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        now = time.time()
        self._guarded(job_id, worker_id, "UPDATE jobs SET lease_expires_at = ?", (now + lease_seconds,))

    def update_progress(self, job_id: str, worker_id: str, phase: str, progress: dict[str, Any],
                        metrics: dict[str, Any] | None = None) -> None:
        now = time.time()
        if metrics is None:
            self._guarded(job_id, worker_id, "UPDATE jobs SET phase = ?, progress = ?, updated_at = ?",
                          (phase, json.dumps(progress), now))
        else:
            self._guarded(job_id, worker_id, "UPDATE jobs SET phase = ?, progress = ?, metrics = ?, updated_at = ?",
                          (phase, json.dumps(progress), json.dumps(metrics), now))

    def complete(self, job_id: str, worker_id: str, result: dict[str, Any], progress: dict[str, Any],
                 metrics: dict[str, Any]) -> None:
        now = time.time()
        self._guarded(job_id, worker_id,
                      "UPDATE jobs SET status = 'completed', phase = 'done', result = ?, progress = ?, metrics = ?,"
                      " finished_at = ?, updated_at = ?, lease_owner = NULL, lease_expires_at = NULL",
                      (json.dumps(result), json.dumps(progress), json.dumps(metrics), now, now))

    def fail(self, job_id: str, worker_id: str, code: str, message: str, progress: dict[str, Any] | None,
             metrics: dict[str, Any] | None) -> None:
        now = time.time()
        self._guarded(job_id, worker_id,
                      "UPDATE jobs SET status = 'failed', error_code = ?, error_message = ?, progress = ?,"
                      " metrics = ?, finished_at = ?, updated_at = ?, lease_owner = NULL, lease_expires_at = NULL",
                      (code, message, json.dumps(progress or {}), json.dumps(metrics or {}), now, now))

    def release(self, job_id: str, worker_id: str) -> None:
        """Give a job back to the queue (graceful shutdown) without counting the attempt."""
        with self._conn() as c:
            c.execute("UPDATE jobs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,"
                      " attempts = MAX(attempts - 1, 0), updated_at = ? WHERE id = ? AND lease_owner = ?",
                      (time.time(), job_id, worker_id))

    # --- Metrics for ETAs -------------------------------------------------------------

    def record_phase(self, phase: str, units: float, seconds: float) -> None:
        if units <= 0 or seconds < 0:
            return
        with self._conn() as c:
            c.execute("INSERT INTO phase_stats (phase, units, seconds, created_at) VALUES (?, ?, ?, ?)",
                      (phase, units, seconds, time.time()))

    def phase_rate(self, phase: str, min_samples: int = 2) -> float | None:
        """Median seconds per unit for a phase over recent jobs, or None without enough data."""
        with self._conn() as c:
            rows = c.execute("SELECT units, seconds FROM phase_stats WHERE phase = ? ORDER BY created_at DESC"
                             " LIMIT 25", (phase,)).fetchall()
        if len(rows) < min_samples:
            return None
        return statistics.median(r["seconds"] / r["units"] for r in rows if r["units"] > 0)

    # --- Worker liveness --------------------------------------------------------------

    def worker_heartbeat(self, worker_id: str, started_at: float) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO workers (id, heartbeat_at, started_at) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE"
                      " SET heartbeat_at = excluded.heartbeat_at", (worker_id, time.time(), started_at))
            c.execute("DELETE FROM workers WHERE heartbeat_at < ?", (time.time() - 3600,))

    def worker_alive(self, within_seconds: float = 20.0) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT MAX(heartbeat_at) FROM workers").fetchone()
        return bool(row[0]) and (time.time() - row[0]) <= within_seconds

    def worker_stopped(self, worker_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
