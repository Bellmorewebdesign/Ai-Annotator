"""Logging configuration. Log lines contain ids, counts and timings, never document text."""

from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level = os.environ.get("ANNOTATOR_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The HTTP clients log request URLs at INFO; keep them quiet.
    for noisy in ("httpx", "httpx2", "anthropic", "pikepdf"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
