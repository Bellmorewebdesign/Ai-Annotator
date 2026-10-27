"""Local file storage for job inputs, working files and outputs.

User-supplied names never reach the filesystem: files are stored under fixed names
inside a directory named after the job id, and job ids are validated strictly before
any path is built. Swapping this module for object storage is the cloud migration path.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


class InvalidJobId(ValueError):
    pass


def check_job_id(job_id: str) -> str:
    if not JOB_ID_RE.match(job_id or ""):
        raise InvalidJobId("Invalid job id")
    return job_id


@dataclass(frozen=True)
class JobFiles:
    root: Path

    @classmethod
    def for_job(cls, jobs_dir: Path, job_id: str) -> JobFiles:
        root = (jobs_dir / check_job_id(job_id)).resolve()
        if root.parent != jobs_dir.resolve():  # defence in depth against traversal
            raise InvalidJobId("Invalid job id")
        return cls(root)

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    def source(self, kind: str) -> Path:
        return self.input_dir / f"source.{kind}"

    def instructions(self, kind: str) -> Path:
        return self.input_dir / f"instructions.{kind}"

    @property
    def annotated_pdf(self) -> Path:
        return self.output_dir / "annotated.pdf"

    @property
    def summary_md(self) -> Path:
        return self.output_dir / "summary.md"

    @property
    def summary_pdf(self) -> Path:
        return self.output_dir / "summary.pdf"

    def ensure(self) -> None:
        for d in (self.input_dir, self.work_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)

    def delete(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def clear_work(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)
