"""Background worker process: ``python -m annotator.worker``.

Claims queued jobs (or jobs whose previous worker died), runs the pipeline, renews
its lease with a heartbeat, deletes expired jobs, and gives its job back to the
queue on a graceful shutdown (Ctrl+C) so nothing is silently lost.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time

from ..ai.client import make_client
from ..config import Settings, get_settings
from ..jobs.store import JobStore, LeaseLost
from ..logging_setup import configure_logging
from ..storage import JobFiles
from .pipeline import Cancelled, JobContext, PipelineError, run_job

log = logging.getLogger("annotator.worker")
CLEANUP_INTERVAL = 600


class Worker:
    def __init__(self, settings: Settings, client_factory=None):
        self.settings = settings
        self.store = JobStore(settings.db_path)
        self.id = f"{socket.gethostname()}-{os.getpid()}-{int(time.time())}"
        self.started_at = time.time()
        self.stop = threading.Event()
        self.client_factory = client_factory or (lambda: make_client(settings))
        self._last_cleanup = 0.0

    def run_forever(self) -> None:
        log.info("Worker %s started", self.id)
        self.store.worker_heartbeat(self.id, self.started_at)
        try:
            while not self.stop.is_set():
                self.store.worker_heartbeat(self.id, self.started_at)
                self.cleanup_expired()
                if not self.run_once():
                    self.stop.wait(self.settings.worker_poll_seconds)
        finally:
            self.store.worker_stopped(self.id)
            log.info("Worker %s stopped", self.id)

    def run_once(self) -> bool:
        """Process one job if available. Returns True if a job was processed."""
        job = self.store.claim_next(self.id, self.settings.lease_seconds, self.settings.max_job_attempts)
        if job is None:
            return False
        log.info("Job %s claimed (attempt %s)", job.id, job.attempts)
        ctx = JobContext(job, self.store, self.settings, self.id)
        beat = threading.Thread(target=self._heartbeat, args=(ctx,), daemon=True)
        beat.start()
        try:
            run_job(ctx, self.client_factory)
            log.info("Job %s completed", job.id)
        except (Cancelled, LeaseLost):
            log.info("Job %s stopped (deleted or lease lost)", job.id)
            if self.store.get(job.id) is None:
                JobFiles.for_job(self.settings.jobs_dir, job.id).delete()
        except PipelineError as exc:
            log.info("Job %s failed: %s", job.id, exc.code)
            self._fail(ctx, exc.code, exc.user_message)
        except KeyboardInterrupt:
            self.store.release(job.id, self.id)
            raise
        except Exception:
            log.exception("Job %s crashed", job.id)
            self._fail(ctx, "internal_error", "Something went wrong while processing this document. Please try again.")
        finally:
            ctx.cancelled.set()
            beat.join(timeout=5)
        return True

    def _fail(self, ctx: JobContext, code: str, message: str) -> None:
        try:
            self.store.fail(ctx.job.id, self.id, code, message, ctx.tracker.snapshot(), ctx.tracker.metrics)
        except LeaseLost:
            pass

    def _heartbeat(self, ctx: JobContext) -> None:
        interval = max(2.0, self.settings.lease_seconds / 3)
        while not ctx.cancelled.wait(interval):
            try:
                self.store.heartbeat(ctx.job.id, self.id, self.settings.lease_seconds)
                self.store.worker_heartbeat(self.id, self.started_at)
            except LeaseLost:
                ctx.cancelled.set()
                return
            except Exception:  # pragma: no cover - transient database error
                log.warning("Heartbeat failed for job %s", ctx.job.id)

    def cleanup_expired(self, force: bool = False) -> int:
        now = time.time()
        if not force and now - self._last_cleanup < CLEANUP_INTERVAL:
            return 0
        self._last_cleanup = now
        removed = 0
        for job_id in self.store.expired(now):
            JobFiles.for_job(self.settings.jobs_dir, job_id).delete()
            self.store.delete(job_id)
            removed += 1
        if removed:
            log.info("Deleted %s expired job(s)", removed)
        return removed


def main() -> None:
    configure_logging()
    settings = get_settings()
    worker = Worker(settings)

    def handle(signum, frame):  # pragma: no cover - signal handling
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, handle)
    if hasattr(signal, "SIGBREAK"):  # Windows: dev.py stops processes with Ctrl+Break
        signal.signal(signal.SIGBREAK, handle)
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        log.info("Shutting down; any job in progress was returned to the queue.")


if __name__ == "__main__":  # pragma: no cover
    main()
