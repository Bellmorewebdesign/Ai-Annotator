"""Real progress tracking and measured ETAs.

Progress is reported as completed work units per phase (pages, AI requests,
selections). ETAs combine this job's measured throughput in the current phase with
historical per-phase rates from previous jobs. When a phase that dominates the
runtime (AI analysis, summary) has no history yet, no ETA is given and the UI
shows "Estimating...".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..jobs.store import JobStore

PHASES: list[tuple[str, str]] = [
    ("upload", "Upload complete"),
    ("validate", "Checking the document"),
    ("extract", "Reading text and layout"),
    ("instructions", "Reading your instructions"),
    ("analyze", "Analyzing the document"),
    ("match", "Finding the selected passages"),
    ("render", "Applying highlights and notes"),
    ("verify", "Checking annotation accuracy"),
    ("export", "Writing the summary"),
]
# Phases whose duration must be known (from history) before an ETA is trustworthy.
DOMINANT = {"analyze", "export"}


@dataclass
class PhaseState:
    key: str
    label: str
    status: str = "pending"  # pending | active | done | skipped
    done: int = 0
    total: int | None = None
    unit: str | None = None
    started: float | None = None
    finished: float | None = None
    detail: str | None = None


@dataclass
class Tracker:
    store: JobStore
    job_id: str
    worker_id: str
    phases: dict[str, PhaseState] = field(default_factory=dict)
    # Expected units for phases that have not started yet: phase -> (units, stat key)
    expected: dict[str, tuple[float, str]] = field(default_factory=dict)
    durations: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    _last_push: float = 0.0

    @classmethod
    def create(cls, store: JobStore, job_id: str, worker_id: str, metrics: dict[str, Any] | None = None) -> Tracker:
        t = cls(store=store, job_id=job_id, worker_id=worker_id,
                phases={k: PhaseState(k, label) for k, label in PHASES}, metrics=dict(metrics or {}))
        t.durations = dict(t.metrics.get("phase_seconds", {}))
        up = t.phases["upload"]
        up.status = "done"
        return t

    # --- updates ------------------------------------------------------------------------

    def start(self, key: str, total: int | None = None, unit: str | None = None, detail: str | None = None) -> None:
        p = self.phases[key]
        p.status, p.done, p.total, p.unit, p.detail = "active", 0, total, unit, detail
        p.started = time.time()
        self.push(force=True)

    def advance(self, key: str, done: int, total: int | None = None, detail: str | None = None) -> None:
        p = self.phases[key]
        p.done = done
        if total is not None:
            p.total = total
        if detail is not None:
            p.detail = detail
        self.push()

    def finish(self, key: str, stat_units: float | None = None, stat_key: str | None = None) -> None:
        p = self.phases[key]
        p.status = "done"
        p.finished = time.time()
        if p.total is not None:
            p.done = p.total
        elapsed = p.finished - (p.started or p.finished)
        self.durations[key] = round(self.durations.get(key, 0.0) + elapsed, 2)
        if stat_units:
            self.store.record_phase(stat_key or key, stat_units, elapsed)
        self.push(force=True)

    def skip(self, key: str, detail: str | None = None) -> None:
        p = self.phases[key]
        p.status, p.detail = "skipped", detail
        self.push(force=True)

    def expect(self, key: str, units: float, stat_key: str | None = None) -> None:
        self.expected[key] = (units, stat_key or key)

    # --- reporting ----------------------------------------------------------------------

    def current(self) -> str:
        for k, _ in PHASES:
            if self.phases[k].status == "active":
                return k
        for k, _ in PHASES:
            if self.phases[k].status == "pending":
                return k
        return "done"

    def eta(self) -> tuple[float | None, str]:
        now = time.time()
        remaining = 0.0
        for k, _ in PHASES:
            p = self.phases[k]
            if p.status in ("done", "skipped"):
                continue
            units, stat_key = self.expected.get(k, (1.0, k))
            rate = self.store.phase_rate(stat_key)
            if p.status == "active":
                elapsed = now - (p.started or now)
                if p.total and p.done > 0:
                    remaining += elapsed / p.done * (p.total - p.done)
                    continue
                if rate is None:
                    if k in DOMINANT:
                        return None, "not enough history"
                    continue
                remaining += max(0.0, rate * units - elapsed)
            else:
                if rate is None:
                    if k in DOMINANT:
                        return None, "not enough history"
                    continue
                remaining += rate * units
        return now + remaining, "measured"

    def snapshot(self) -> dict[str, Any]:
        eta_at, basis = self.eta()
        return {
            "phases": [
                {"key": p.key, "label": p.label, "status": p.status, "done": p.done, "total": p.total,
                 "unit": p.unit, "detail": p.detail}
                for p in (self.phases[k] for k, _ in PHASES)
            ],
            "current": self.current(),
            "eta_at": eta_at,
            "eta_basis": basis,
        }

    def push(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_push < 0.5:
            return
        self._last_push = now
        self.metrics["phase_seconds"] = self.durations
        self.store.update_progress(self.job_id, self.worker_id, self.current(), self.snapshot(), self.metrics)
