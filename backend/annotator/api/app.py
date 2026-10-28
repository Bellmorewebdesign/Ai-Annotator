"""FastAPI application: uploads, job status, downloads, deletion and category detection.

Heavy work never runs in request handlers; the worker process owns the pipeline.
Errors returned to clients are short, user-safe messages without internal details.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..ai.analyze import suggest_categories
from ..ai.client import AIError, make_client
from ..ai.prompts import DEFAULT_CATEGORIES, PALETTE
from ..config import Settings, get_settings
from ..engine.convert import find_soffice
from ..engine.extract import PdfOpenError, open_pdf
from ..engine.ocr import find_tesseract
from ..engine.validate import KIND_LABELS, UnsupportedFile, sanitize_filename, sniff
from ..jobs.store import Job, JobStore, new_job_id
from ..logging_setup import configure_logging
from ..storage import InvalidJobId, JobFiles, check_job_id
from ..worker.pipeline import PipelineError, extract_instruction_text

log = logging.getLogger("annotator.api")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
CHUNK = 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


class CategoryIn(BaseModel):
    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=40)
    color: str
    description: str = Field(default="", max_length=200)

    @field_validator("color")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX.match(v):
            raise ValueError("color must be a hex value like #FFE066")
        return v.upper()


class JobOptions(BaseModel):
    density: str = Field(default="balanced", pattern=r"^(light|balanced|detailed)$")
    categories: list[CategoryIn] = Field(default_factory=list, max_length=12)
    categories_mode: str = Field(default="auto", pattern=r"^(auto|custom)$")
    allow_underline: bool = True
    allow_comments: bool = True
    allow_shapes: bool = True


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def _save_upload(upload: UploadFile, dest: Path, limit: int, label: str) -> int:
    size = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as out:
        while True:
            chunk = await upload.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise ApiError(413, "file_too_large", f"The {label} is larger than {limit // (1024 * 1024)} MB.")
            out.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise ApiError(400, "empty_file", f"The {label} is empty.")
    return size


def _pdf_pages(path: Path) -> int:
    pdf = open_pdf(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    store = JobStore(settings.db_path)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="AI Annotator API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["*"], expose_headers=["Content-Disposition"])
    app.state.settings = settings
    app.state.store = store

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return _err(exc.status, exc.code, exc.message)

    @app.exception_handler(InvalidJobId)
    async def _bad_id(request: Request, exc: InvalidJobId):
        return _err(404, "not_found", "This job does not exist or has been deleted.")

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return _err(422, "invalid_request", "Some of the submitted fields are invalid.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return _err(exc.status_code, "http_error", str(exc.detail) if exc.status_code < 500 else "Server error.")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # pragma: no cover - safety net
        log.exception("Unhandled error on %s", request.url.path)
        return _err(500, "internal_error", "Something went wrong. Please try again.")

    @app.middleware("http")
    async def _limit_body(request: Request, call_next):
        if request.method == "POST":
            length = request.headers.get("content-length")
            limit = settings.max_upload_bytes + settings.max_instruction_bytes + 2 * 1024 * 1024
            if length and length.isdigit() and int(length) > limit:
                return _err(413, "file_too_large", f"The upload is larger than {settings.max_upload_mb} MB.")
        return await call_next(request)

    # --- health --------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        soffice = find_soffice(settings.soffice_cmd)
        return {
            "status": "ok",
            "ai": {"configured": settings.ai_configured, "test_mode": settings.fake_ai_enabled,
                   "model": settings.model},
            "ocr": {"available": find_tesseract(settings.tesseract_cmd) is not None, "language": settings.ocr_language},
            "docx_conversion": "libreoffice" if soffice else "simplified",
            "worker": {"alive": store.worker_alive()},
            "limits": {"max_upload_mb": settings.max_upload_mb, "max_pages": settings.max_pages,
                       "max_instruction_mb": settings.max_instruction_mb,
                       "max_instruction_chars": settings.max_instruction_chars},
            "retention_hours": settings.retention_hours,
            "default_categories": [asdict(c) for c in DEFAULT_CATEGORIES],
            "palette": PALETTE,
        }

    # --- jobs ----------------------------------------------------------------------------

    @app.post("/api/jobs", status_code=201)
    async def create_job(
        document: Annotated[UploadFile, File()],
        instructions: Annotated[str, Form()] = "",
        options: Annotated[str, Form()] = "{}",
        instructions_file: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, Any]:
        if not settings.ai_configured:
            raise ApiError(503, "ai_not_configured",
                           "The AI service is not configured. Add ANTHROPIC_API_KEY to the .env file and restart.")
        if len(instructions) > settings.max_instruction_chars:
            raise ApiError(400, "instructions_too_long",
                           f"Instructions are limited to {settings.max_instruction_chars:,} characters.")
        try:
            opts = JobOptions.model_validate(json.loads(options or "{}"))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ApiError(422, "invalid_options", "The annotation settings are invalid.") from exc
        if opts.categories_mode == "custom" and not opts.categories:
            raise ApiError(422, "invalid_options", "Add at least one category.")
        ids = [c.id for c in opts.categories]
        if len(ids) != len(set(ids)):
            raise ApiError(422, "invalid_options", "Category ids must be unique.")

        job_id = new_job_id()
        files = JobFiles.for_job(settings.jobs_dir, job_id)
        files.ensure()
        try:
            upload_tmp = files.input_dir / "upload.tmp"
            size = await _save_upload(document, upload_tmp, settings.max_upload_bytes, "document")
            try:
                sniffed = sniff(upload_tmp)
            except UnsupportedFile as exc:
                raise ApiError(415, exc.code, str(exc)) from exc
            source = files.source(sniffed.kind)
            upload_tmp.replace(source)
            name = sanitize_filename(document.filename or "", fallback=f"document.{sniffed.kind}")
            info: dict[str, Any] = {"name": name, "size": size, "kind": sniffed.kind,
                                    "kind_label": KIND_LABELS[sniffed.kind], "encoding": sniffed.text_encoding}
            if sniffed.kind == "pdf":
                try:
                    pages = _pdf_pages(source)
                except PdfOpenError as exc:
                    raise ApiError(422, exc.code, str(exc)) from exc
                if pages == 0:
                    raise ApiError(422, "empty_document", "This PDF has no pages.")
                if pages > settings.max_pages:
                    raise ApiError(422, "too_many_pages",
                                   f"This PDF has {pages} pages. The current limit is {settings.max_pages} pages.")
                info["pages"] = pages
            if sniffed.kind in ("png", "jpg") and not find_tesseract(settings.tesseract_cmd):
                raise ApiError(422, "needs_ocr", "Reading images requires Tesseract OCR, which is not installed on "
                                                 "this computer. See the README for setup.")

            if instructions_file is not None and instructions_file.filename:
                itmp = files.input_dir / "instructions.tmp"
                isize = await _save_upload(instructions_file, itmp, settings.max_instruction_bytes,
                                           "instructions file")
                try:
                    isniff = sniff(itmp)
                except UnsupportedFile as exc:
                    raise ApiError(415, exc.code, "Attached instructions: " + str(exc)) from exc
                itmp.replace(files.instructions(isniff.kind))
                info.update({"instructions_name": sanitize_filename(instructions_file.filename),
                             "instructions_size": isize, "instructions_kind": isniff.kind,
                             "instructions_encoding": isniff.text_encoding})
        except BaseException:
            files.delete()
            raise

        opts_dict = opts.model_dump()
        opts_dict["instructions"] = instructions.strip()
        job = store.create(job_id, info, opts_dict, settings.retention_hours)
        log.info("Job %s created kind=%s size=%s", job_id, info["kind"], size)
        return _job_view(job, store, settings)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = store.get(check_job_id(job_id))
        if job is None:
            raise ApiError(404, "not_found", "This job does not exist or has been deleted.")
        return _job_view(job, store, settings)

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, Any]:
        check_job_id(job_id)
        existed = store.delete(job_id)
        JobFiles.for_job(settings.jobs_dir, job_id).delete()
        if not existed:
            raise ApiError(404, "not_found", "This job does not exist or has been deleted.")
        return {"deleted": True}

    @app.get("/api/jobs/{job_id}/files/{kind}")
    def download(job_id: str, kind: str, inline: bool = False):
        job = store.get(check_job_id(job_id))
        if job is None:
            raise ApiError(404, "not_found", "This job does not exist or has been deleted.")
        if job.status != "completed":
            raise ApiError(409, "not_ready", "This file is not ready yet.")
        files = JobFiles.for_job(settings.jobs_dir, job_id)
        stem = Path(job.input.get("name", "document")).stem or "document"
        mapping = {
            "annotated": (files.annotated_pdf, "application/pdf", f"{stem} (annotated).pdf"),
            "summary-md": (files.summary_md, "text/markdown; charset=utf-8", f"{stem} summary.md"),
            "summary-pdf": (files.summary_pdf, "application/pdf", f"{stem} summary.pdf"),
        }
        if kind not in mapping:
            raise ApiError(404, "not_found", "Unknown file.")
        path, media, filename = mapping[kind]
        if not path.exists():
            raise ApiError(404, "not_found", "This file is no longer available.")
        return FileResponse(path, media_type=media, filename=filename,
                            content_disposition_type="inline" if inline else "attachment",
                            headers={"Cache-Control": "no-store"})

    # --- categories ----------------------------------------------------------------------

    @app.post("/api/categories/suggest")
    async def suggest(
        instructions: Annotated[str, Form()] = "",
        instructions_file: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, Any]:
        if not settings.ai_configured:
            raise ApiError(503, "ai_not_configured", "The AI service is not configured.")
        rubric = ""
        if instructions_file is not None and instructions_file.filename:
            tmpdir = Path(tempfile.mkdtemp(prefix="annotator-suggest-"))
            try:
                tmp = tmpdir / "upload"
                await _save_upload(instructions_file, tmp, settings.max_instruction_bytes, "instructions file")
                try:
                    s = sniff(tmp)
                except UnsupportedFile as exc:
                    raise ApiError(415, exc.code, str(exc)) from exc
                path = tmpdir / f"instructions.{s.kind}"
                tmp.replace(path)
                try:
                    rubric = extract_instruction_text(path, s.kind, s.text_encoding,
                                                      find_tesseract(settings.tesseract_cmd), settings, tmpdir)
                except (UnsupportedFile, PdfOpenError, PipelineError) as exc:
                    msg = getattr(exc, "user_message", None) or str(exc)
                    raise ApiError(422, "unreadable_instructions",
                                   f"The attached instructions could not be read ({msg.rstrip('.')}).") from exc
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        if not instructions.strip() and not rubric.strip():
            raise ApiError(400, "no_instructions", "Type instructions or attach a file first.")
        try:
            data, _ = suggest_categories(make_client(settings), settings, instructions[: settings.max_instruction_chars],
                                         rubric[: settings.max_instruction_chars])
        except AIError as exc:
            raise ApiError(502, exc.code, exc.user_message) from exc
        return data

    return app


def _job_view(job: Job, store: JobStore, settings: Settings) -> dict[str, Any]:
    now = time.time()
    progress = dict(job.progress or {})
    eta_at = progress.pop("eta_at", None)
    progress["eta_seconds"] = max(0, round(eta_at - now)) if eta_at and job.status == "running" else None
    base = f"/api/jobs/{job.id}/files"
    files = {}
    if job.status == "completed" and job.result:
        files = {"annotated": f"{base}/annotated", "summary_md": f"{base}/summary-md",
                 "summary_pdf": f"{base}/summary-pdf"}
    metrics = {k: v for k, v in (job.metrics or {}).items() if k != "ai_calls"}
    metrics["ai_requests"] = len((job.metrics or {}).get("ai_calls", []))
    return {
        "id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "expires_at": job.expires_at,
        "attempts": job.attempts,
        "document": {k: job.input.get(k) for k in ("name", "size", "kind", "kind_label", "pages")},
        "instructions_file": ({"name": job.input.get("instructions_name"), "size": job.input.get("instructions_size")}
                              if job.input.get("instructions_name") else None),
        "options": job.options,
        "progress": progress,
        "queue_position": store.queue_position(job) if job.status == "queued" else None,
        "worker_alive": store.worker_alive() if job.status in ("queued", "running") else None,
        "error": {"code": job.error_code, "message": job.error_message} if job.status == "failed" else None,
        "result": job.result,
        "metrics": metrics,
        "files": files,
        "test_mode": settings.fake_ai_enabled,
    }


def get_app() -> FastAPI:  # uvicorn factory: uvicorn annotator.api.app:get_app --factory
    return create_app()
