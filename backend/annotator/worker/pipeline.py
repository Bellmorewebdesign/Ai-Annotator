"""The annotation pipeline executed by the worker for one job.

Every expensive step writes a checkpoint into the job's ``work/`` directory (converted
PDF, OCR output, instruction analysis, AI analysis, summary). If the worker is
interrupted, the next attempt reuses those checkpoints instead of paying for the same
AI calls again. Layout extraction is deterministic and cheap, so it is recomputed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..ai.analyze import (
    AnalysisRequest,
    AnalysisResult,
    analyze_document,
    estimate_tokens,
    suggest_categories,
    summarize,
)
from ..ai.client import AIError, CallRecord, StructuredClient
from ..ai.prompts import DEFAULT_CATEGORIES, format_document
from ..config import Settings
from ..engine.annotate import Category, place_annotations, render_and_verify
from ..engine.convert import (
    ConversionResult,
    docx_plain_text,
    docx_to_pdf,
    find_soffice,
    image_to_pdf,
    txt_file_to_pdf,
)
from ..engine.document import load_document
from ..engine.extract import PdfOpenError, open_pdf
from ..engine.ocr import find_tesseract
from ..engine.textindex import build_doc_text
from ..engine.validate import UnsupportedFile
from ..jobs.store import Job, JobStore, LeaseLost
from ..storage import JobFiles
from ..summary.render import build_summary, to_markdown, to_pdf
from .progress import Tracker

log = logging.getLogger("annotator.worker")


class PipelineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.user_message = message


class Cancelled(Exception):
    pass


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


class JobContext:
    def __init__(self, job: Job, store: JobStore, settings: Settings, worker_id: str):
        self.job = job
        self.store = store
        self.settings = settings
        self.worker_id = worker_id
        self.files = JobFiles.for_job(settings.jobs_dir, job.id)
        self.cancelled = threading.Event()
        self.tracker = Tracker.create(store, job.id, worker_id, job.metrics)
        self.calls: list[CallRecord] = [CallRecord(**c) for c in job.metrics.get("ai_calls", [])]

    def check(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled()

    def add_calls(self, records: list[CallRecord]) -> None:
        self.calls.extend(records)
        self.tracker.metrics["ai_calls"] = [c.to_dict() for c in self.calls]
        self.tracker.metrics["ai_cost_usd"] = _total_cost(self.calls)
        self.tracker.metrics["ai_tokens"] = {
            "input": sum(c.input_tokens for c in self.calls),
            "output": sum(c.output_tokens for c in self.calls),
            "cache_read": sum(c.cache_read_tokens for c in self.calls),
            "cache_write": sum(c.cache_write_tokens for c in self.calls),
        }


def _total_cost(calls: list[CallRecord]) -> float | None:
    if any(c.cost_usd is None for c in calls):
        return None
    return round(sum(c.cost_usd or 0.0 for c in calls), 4)


# ----------------------------------------------------------------------------------------


def run_job(ctx: JobContext, client_factory) -> None:
    job, s, files, tr = ctx.job, ctx.settings, ctx.files, ctx.tracker
    files.ensure()
    kind = job.input["kind"]
    source = files.source(kind)
    if not source.exists():
        raise PipelineError("missing_input", "The uploaded document is no longer available. Please upload it again.")
    warnings: list[str] = []
    tesseract = find_tesseract(s.tesseract_cmd)

    # 1. Validate and convert ------------------------------------------------------------
    tr.start("validate", total=1, unit="files")
    pdf_path, conversion = _prepare_pdf(ctx, kind, source, tesseract)
    warnings.extend(n for n in conversion.notes if conversion.method not in ("native", "text", "libreoffice"))
    _check_pdf(pdf_path, s.max_pages)
    tr.advance("validate", 1)
    ctx.check()
    tr.finish("validate", stat_units=1)

    # 2. Extract text and layout (OCR when needed) -----------------------------------
    pages_total = _page_count(pdf_path)
    tr.start("extract", total=pages_total, unit="pages")
    load = load_document(pdf_path, tesseract=tesseract, ocr_lang=s.ocr_language, ocr_cache=files.work_dir / "ocr",
                         on_page=lambda d, t: (ctx.check(), tr.advance("extract", d, t)))
    doc = load.doc
    warnings.extend(load.warnings)
    if not any(p.blocks for p in doc.pages):
        if load.skipped_pages and not tesseract:
            raise PipelineError("needs_ocr", "This document appears to be scanned. Install Tesseract OCR to annotate "
                                             "scanned documents (see the README).")
        raise PipelineError("no_text", "No readable text was found in this document.")
    dt = build_doc_text(doc)
    tr.metrics["pages"] = pages_total
    tr.metrics["ocr_pages"] = len(load.ocr_pages)
    tr.finish("extract", stat_units=max(1, pages_total - len(load.ocr_pages)) if not load.ocr_pages else None)
    if load.ocr_pages:
        ctx.store.record_phase("extract_ocr", len(load.ocr_pages), tr.durations.get("extract", 0))
    ctx.check()

    # 3. Instructions and categories --------------------------------------------------
    tokens_k = max(1.0, estimate_tokens(format_document(doc)) / 1000)
    tr.expect("analyze", tokens_k, "analyze")
    tr.start("instructions")
    client: StructuredClient | None = None

    def get_client() -> StructuredClient:
        nonlocal client
        if client is None:
            client = client_factory()
        return client

    instr = _instructions(ctx, get_client, tesseract, warnings)
    categories = [Category(**c) for c in instr["categories"]]
    tr.finish("instructions")
    ctx.check()

    opts = job.options
    req = AnalysisRequest(
        instructions=opts.get("instructions", ""), rubric=instr["rubric_text"], categories=categories,
        density=opts.get("density", "balanced"), allow_underline=opts.get("allow_underline", True),
        allow_comments=opts.get("allow_comments", True), allow_shapes=opts.get("allow_shapes", True),
    )

    # 4. AI analysis -------------------------------------------------------------------
    analysis_path = files.work_dir / "analysis.json"
    cached = _read_json(analysis_path)
    if cached:
        analysis = AnalysisResult.from_dict(cached)
        tr.start("analyze", total=1, unit="requests", detail="Reused saved analysis")
        tr.finish("analyze")
    else:
        tr.start("analyze", unit="requests")
        try:
            analysis = analyze_document(get_client(), s, doc, req,
                                        on_progress=lambda d, t: (ctx.check(), tr.advance("analyze", d, t)))
        except AIError as exc:
            raise PipelineError(exc.code, exc.user_message) from exc
        _write_json(analysis_path, analysis.to_dict())
        ctx.add_calls(analysis.calls)
        tr.finish("analyze", stat_units=tokens_k)
    ctx.check()

    # 5. Match --------------------------------------------------------------------------
    n_sel = len(analysis.selections) + len(analysis.region_selections)
    tr.start("match", total=n_sel, unit="selections")
    placement = place_annotations(doc, dt, analysis.selections, analysis.region_selections, categories,
                                  density=req.density, fuzzy_threshold=s.fuzzy_accept_score)
    tr.finish("match")
    ctx.check()

    # 6-7. Render and verify ------------------------------------------------------------
    # Rendering and verification run together: annotations that fail verification are
    # removed and the file is written again until every remaining annotation passes.
    tr.start("render", total=len(placement.specs), unit="annotations")
    outcome = render_and_verify(source if kind == "pdf" else pdf_path, files.annotated_pdf, placement, doc=doc)
    tr.finish("render")
    tr.start("verify", total=outcome.checked, unit="annotations")
    if not outcome.verified:
        raise PipelineError("verification_failed", "The annotated PDF did not pass the final quality check.")
    tr.finish("verify")
    ctx.check()

    # 8. Summary and export ------------------------------------------------------------
    tr.start("export", total=3, unit="files")
    summary_path = files.work_dir / "summary.json"
    summary_data = _read_json(summary_path)
    if summary_data is None and placement.placed:
        try:
            summary_data, record = summarize(get_client(), s, doc, req, analysis, placement.placed)
            ctx.add_calls([record])
            _write_json(summary_path, summary_data)
        except AIError as exc:
            warnings.append("The written summary could not be generated (" + exc.user_message.rstrip(".")
                            + "). The list of annotated passages is still included.")
            summary_data = None
    tr.advance("export", 1)
    name = job.input.get("name", "document")
    summary = build_summary(summary_data, placement.placed, name, analysis.notes, warnings,
                            [c.name for c in categories])
    files.summary_md.write_text(to_markdown(summary), encoding="utf-8")
    tr.advance("export", 2)
    to_pdf(summary, files.summary_pdf)
    tr.finish("export", stat_units=1 if summary_data is not None else None)

    result = _result(ctx, doc, placement, analysis, categories, warnings, summary_data, conversion, outcome)
    ctx.tracker.metrics["total_seconds"] = round(time.time() - (job.started_at or time.time()), 2)
    ctx.store.complete(job.id, ctx.worker_id, result, tr.snapshot(), tr.metrics)
    if not s.keep_work_files:
        files.clear_work()


def _prepare_pdf(ctx: JobContext, kind: str, source: Path, tesseract: str | None) -> tuple[Path, ConversionResult]:
    files = ctx.files
    if kind == "pdf":
        return source, ConversionResult(source, "native")
    converted = files.work_dir / "document.pdf"
    meta_path = files.work_dir / "conversion.json"
    meta = _read_json(meta_path)
    if converted.exists() and meta:
        return converted, ConversionResult(converted, meta["method"], meta["notes"])
    try:
        if kind == "docx":
            res = docx_to_pdf(source, converted, find_soffice(ctx.settings.soffice_cmd))
        elif kind == "txt":
            res = txt_file_to_pdf(source, converted, ctx.job.input.get("encoding") or "utf-8")
        elif kind in ("png", "jpg"):
            if not tesseract:
                raise PipelineError("needs_ocr", "Reading text from images requires Tesseract OCR, which is not "
                                                 "installed. See the README for setup.")
            res = image_to_pdf(source, converted)
        else:  # pragma: no cover - rejected at upload
            raise PipelineError("unsupported_file", "This file type is not supported.")
    except UnsupportedFile as exc:
        raise PipelineError(exc.code, str(exc)) from exc
    _write_json(meta_path, {"method": res.method, "notes": res.notes})
    return converted, res


def _check_pdf(path: Path, max_pages: int) -> None:
    try:
        pdf = open_pdf(path)
    except PdfOpenError as exc:
        raise PipelineError(exc.code, str(exc)) from exc
    try:
        n = len(pdf)
    finally:
        pdf.close()
    if n == 0:
        raise PipelineError("empty_document", "This document has no pages.")
    if n > max_pages:
        raise PipelineError("too_many_pages", f"This document has {n} pages. The current limit is {max_pages} pages.")


def _page_count(path: Path) -> int:
    pdf = open_pdf(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _instructions(ctx: JobContext, get_client, tesseract: str | None, warnings: list[str]) -> dict[str, Any]:
    """Extract rubric text and resolve categories (user-defined, detected, or defaults)."""
    path = ctx.files.work_dir / "instructions.json"
    cached = _read_json(path)
    if cached:
        return cached
    job, s = ctx.job, ctx.settings
    rubric = ""
    ikind = job.input.get("instructions_kind")
    if ikind:
        try:
            rubric = extract_instruction_text(ctx.files.instructions(ikind), ikind, job.input.get("instructions_encoding"),
                                              tesseract, s, ctx.files.work_dir)
        except (UnsupportedFile, PdfOpenError, PipelineError) as exc:
            msg = getattr(exc, "user_message", None) or str(exc)
            warnings.append(f"The attached instructions could not be read ({msg.rstrip('.')}).")
        if len(rubric) > s.max_instruction_chars:
            rubric = rubric[: s.max_instruction_chars]
            warnings.append("The attached instructions were long, so only the first part was used.")
        if ikind and not rubric.strip() and not any("attached instructions" in w for w in warnings):
            warnings.append("No readable text was found in the attached instructions.")

    opts = job.options
    categories = opts.get("categories") or []
    mode = opts.get("categories_mode", "auto")
    detected_notes: list[str] = []
    if mode == "auto" and (opts.get("instructions", "").strip() or rubric.strip()):
        try:
            data, record = suggest_categories(get_client(), s, opts.get("instructions", ""), rubric)
            ctx.add_calls([record])
            if not data["used_defaults"]:
                categories = [{k: c[k] for k in ("id", "name", "color", "description")} for c in data["categories"]]
            detected_notes = data["notes"]
        except AIError as exc:
            raise PipelineError(exc.code, exc.user_message) from exc
    if not categories:
        categories = [asdict(c) for c in DEFAULT_CATEGORIES]
    out = {"rubric_text": rubric, "categories": categories, "category_notes": detected_notes}
    _write_json(path, out)
    return out


def extract_instruction_text(path: Path, kind: str, encoding: str | None, tesseract: str | None, settings: Settings,
                             work_dir: Path) -> str:
    if kind == "txt":
        return path.read_bytes().decode(encoding or "utf-8", errors="replace")
    if kind == "docx":
        return docx_plain_text(path)
    pdf_path = path
    if kind in ("png", "jpg"):
        if not tesseract:
            raise PipelineError("needs_ocr", "reading images requires Tesseract OCR")
        pdf_path = work_dir / "instructions.pdf"
        image_to_pdf(path, pdf_path)
    res = load_document(pdf_path, tesseract=tesseract, ocr_lang=settings.ocr_language,
                        ocr_cache=work_dir / "ocr-instructions")
    if len(res.doc.pages) > settings.max_pages:
        raise PipelineError("too_many_pages", "the attached instructions have too many pages")
    return "\n\n".join(b.text for p in res.doc.pages for b in p.blocks if b.role not in ("header", "footer"))


def _result(ctx, doc, placement, analysis, categories, warnings, summary_data, conversion, outcome) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for p in placement.placed:
        counts[p.category_id] = counts.get(p.category_id, 0) + 1
    annotations = [
        {"id": p.id, "kind": p.kind, "category_id": p.category_id, "category": p.category, "color": p.color,
         "pages": sorted({i + 1 for i in p.pages}), "text": p.text[:1200], "comment": p.comment, "reason": p.reason,
         "importance": p.importance, "match": p.match, "note": p.note}
        for p in sorted(placement.placed, key=lambda p: (min(p.pages), p.id))
    ]
    return {
        "annotations": annotations,
        "categories": [{**asdict(c), "count": counts.get(c.id, 0)} for c in categories],
        "rejected": [{"id": r.id, "kind": r.kind, "quote": r.quote, "reason": r.reason} for r in placement.rejected],
        "instruction_notes": analysis.notes,
        "warnings": warnings,
        "overview": analysis.overview,
        "summary": summary_data,
        "conversion": {"method": conversion.method, "notes": conversion.notes},
        "stats": {
            "pages": len(doc.pages),
            "ocr_pages": sum(1 for p in doc.pages if p.is_ocr),
            "selections": len(analysis.selections) + len(analysis.region_selections),
            "placed": len(placement.placed),
            "approximate": sum(1 for p in placement.placed if p.match == "approximate"),
            "unmatched": len(placement.rejected),
            "verified_annotations": outcome.checked,
            "analysis_mode": analysis.mode,
        },
        "files": {
            "annotated": ctx.files.annotated_pdf.exists(),
            "summary_md": ctx.files.summary_md.exists(),
            "summary_pdf": ctx.files.summary_pdf.exists(),
        },
    }


__all__ = ["JobContext", "PipelineError", "Cancelled", "LeaseLost", "run_job"]
