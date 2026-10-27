# Architecture

```
 Browser (Next.js + PDF.js)
     |  multipart upload, JSON polling, file downloads
     v
 FastAPI (backend/annotator/api)  --->  SQLite job store (data/annotator.db)
     |                                        ^
     | writes inputs to data/jobs/<id>/       | claims jobs with a lease + heartbeat
     v                                        |
 Local file storage  <-------------------  Worker process (backend/annotator/worker)
                                              |
                                              +-- Engine: convert -> extract -> layout -> regions
                                              +-- AI: Claude analysis (structured JSON) -> summary
                                              +-- Engine: match -> geometry -> render -> verify
```

Three processes run locally: the Next.js dev server, the FastAPI API and the worker. `python dev.py`
starts all three. The API never does heavy work inside a request; the worker owns the pipeline, so
closing the browser tab has no effect on processing.

## Pipeline (worker)

| # | Phase | Work units reported |
|---|---|---|
| 1 | Upload complete | - |
| 2 | Validation and conversion | input files |
| 3 | Text and layout extraction (OCR when needed) | pages |
| 4 | Reading instructions | instruction files / category detection |
| 5 | AI document analysis | AI requests (sections) |
| 6 | Matching selections to text | selections |
| 7 | Applying annotations | annotations |
| 8 | Quality verification | annotations / pages |
| 9 | Summary and export | AI request + files |

Progress values are real counts of completed units. The ETA is computed from measured throughput of
this job plus historical per-phase rates stored in SQLite; the UI shows "Estimating..." until there
is enough data.

### Checkpointing and recovery

- Jobs are claimed with a lease (`lease_expires_at`) that a heartbeat thread renews. If the worker
  dies, the lease expires and the next worker run reclaims the job (up to `ANNOTATOR_MAX_JOB_ATTEMPTS`).
- Expensive results are checkpointed to the job's `work/` directory: OCR output per page, the AI
  analysis response, and the summary response. A resumed job does not pay for Claude calls twice.
- Layout extraction is deterministic and fast, so it is recomputed instead of serialized.

## Annotation engine (backend/annotator/engine)

The single most important design rule: **reasoning is separated from rendering**. Claude decides
*what* to annotate; deterministic code decides *where*.

1. **Extraction** (`extract.py`, PDFium via pypdfium2). Every glyph with its loose box in PDF user
   space, font size, weight, origin and text matrix. Simulated-bold duplicate glyphs are removed.
   Characters are grouped by text direction (0/90/180/270 degrees); each group gets a
   `FrameTransform` into a text-aligned frame. This one abstraction handles page `/Rotate`, landscape
   pages and sideways tables.
2. **Layout** (`layout.py`). Line fragments from content order with an em-based gap limit, merged
   per visual line; column gutters from the x-projection of narrow lines (accepted only with
   substantial text on both sides); bands split by full-width lines; columns read left to right per
   band; paragraphs from gaps, indents, short last lines and size changes. Roles: heading, caption,
   footnote, header, footer, references.
3. **Text index** (`textindex.py`). Reading-order document text where each character maps back to a
   glyph. Words hyphenated across lines are joined. A *skeleton* view (letters and digits only,
   case-folded, NFKD, accents and ligatures decomposed) makes matching robust to whitespace,
   typographic quotes, dashes, ligatures and hyphenation.
4. **Regions** (`regions.py`). Embedded images (high confidence), clustered vector graphics
   (medium), ruled tables (medium) and their captions. Low-confidence regions are never boxed.
5. **Matching** (`match.py`). The AI returns `{passage_id, quote, occurrence}`. Exact skeleton match
   on word boundaries inside the passage; then continuing into following passages; then elsewhere on
   the page / document only if unique; then near-exact fuzzy (>= 95 by default) snapped to whole
   words and flagged. Everything else is reported as unmatched and not drawn.
6. **Geometry** (`geometry.py`). One rectangle per line fragment, from the first to the last
   selected glyph (never the whole line), clipped to the line's slot (so adjacent-line highlights do
   not overlap) and to the crop box. Converted back to user space with the frame transform and
   emitted as Acrobat-order QuadPoints.
7. **Rendering** (`render.py`, pikepdf/qpdf). Real `/Highlight`, `/Underline`, `/Square` and
   `/Circle` annotations appended to `/Annots`; page content streams are untouched. Each annotation
   has an explicit appearance stream (Multiply blend for highlights) for consistent rendering across
   pdf.js, Acrobat, Chrome/Edge (PDFium) and Preview. Comments are stored in `/Contents`.
8. **Verification** (`verify.py`). Re-opens the output: page count and page content unchanged,
   every annotation present, inside the crop box, quads inside the rect, and the text PDFium reads
   under each quad equals the intended text. Every page is rendered once to prove the file is
   readable. Failed annotations are removed and reported.

### OCR (`ocr.py`)

Scanned pages and images are rendered at 300 DPI and passed to the Tesseract CLI (TSV output). Word
boxes become synthetic glyphs (characters spread across each word box), and Tesseract's own
block/paragraph/line structure becomes the page layout. Mean word confidence is stored per page and
low-confidence pages are flagged to the user. Without Tesseract installed, scanned pages are skipped
with a visible warning rather than silently producing nothing.

### Conversion (`convert.py`)

DOCX: LibreOffice headless (isolated profile, timeout). Without LibreOffice, a simplified
text-only rendering is produced and flagged. TXT: rendered with ReportLab using bundled DejaVu fonts.
Images: EXIF-corrected, embedded on a page sized from the image DPI, then OCR'd.

## AI layer (backend/annotator/ai)

- Anthropic Python SDK, model ids from `ANNOTATOR_MODEL` / `ANNOTATOR_LIGHT_MODEL`.
- Structured output with `output_config.format` (JSON schema) and Pydantic validation.
- Streaming requests (`get_final_message`) to avoid HTTP timeouts on long outputs.
- Adaptive thinking with configurable effort per call type.
- Server-side refusal fallback (`fallbacks: "default"`) enabled by default where supported.
- **Prompt caching**: the system prompt and the document are an identical prefix for the analysis
  call and the summary call, so the second call reads the document from cache (~5% of input price)
  instead of resending it at full price.
- **Long documents**: when the estimated prompt exceeds `ANNOTATOR_SINGLE_PASS_TOKEN_LIMIT`, a
  planning pass over a condensed outline produces a document-wide reading plan; sections are then
  analysed with that plan as context, with bounded concurrency.
- Retries with exponential backoff and `retry-after` support for 429/5xx/overloaded; clear,
  user-safe error messages; non-retryable errors fail fast.
- Token usage and estimated cost per call are recorded on the job.
- Logs never include document text or quotes.
- Uploaded documents are wrapped as untrusted content; the system prompt instructs Claude to ignore
  any instructions embedded in them. Only the user's typed instructions and attached rubric direct
  the annotation.

### Summary

Generated after matching, from verified annotations only. The model writes prose and cites
annotation ids; quotations and page numbers in the summary are filled in by code from verified
data, so the summary cannot contain fabricated quotes or page references.

## Job store and API

SQLite (WAL mode) via the standard library. Job ids are 128-bit random URL-safe tokens. Inputs are
stored under fixed internal names (`input/source.<ext>`), so user-supplied filenames never touch the
filesystem; the sanitized original name is kept only for display.

Key endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Capabilities (AI configured, OCR, DOCX conversion, limits) |
| POST | `/api/jobs` | Multipart: `document`, optional `instructions_file`, `instructions`, `options` (JSON) |
| GET | `/api/jobs/{id}` | Status, phase, progress, ETA, results |
| GET | `/api/jobs/{id}/files/{kind}` | `annotated`, `summary-md`, `summary-pdf`, `original` |
| DELETE | `/api/jobs/{id}` | Delete job and all files |
| POST | `/api/categories/suggest` | Detect categories/colors from instructions and rubric |
| POST | `/api/documents/inspect` | Validate a file and report type, size, page count |

### Scaling path

The storage layer (`storage.py`) and job store are small interfaces. Moving to cloud means: object
storage for `data/jobs`, Postgres for jobs (same lease semantics), workers on a queue, and direct-to-
bucket presigned uploads for very large files. The API's upload handler already streams to disk in
chunks and enforces limits while streaming.

## Licensing

The product is intended to be proprietary, so only permissive or weak-copyleft (file-level)
dependencies are used:

| Dependency | License | Use |
|---|---|---|
| pypdfium2 / PDFium | Apache-2.0 / BSD-3 | Text extraction, rendering, verification |
| pikepdf / qpdf | MPL-2.0 / Apache-2.0 | Writing annotations |
| ReportLab | BSD | TXT rendering, summary PDF |
| Pillow | MIT-CMU (HPND) | Images |
| python-docx | MIT | DOCX fallback text extraction |
| rapidfuzz | MIT | Near-exact quote matching |
| FastAPI, Uvicorn, Pydantic | MIT / BSD | API |
| anthropic | MIT | Claude API |
| Tesseract (external binary) | Apache-2.0 | OCR |
| LibreOffice (external binary) | MPL-2.0 | DOCX conversion (invoked as a separate program) |
| pdfjs-dist | Apache-2.0 | Browser preview |
| Next.js, React, Tailwind | MIT | Frontend |
| DejaVu fonts | Bitstream Vera / public domain | Bundled fonts |

**Deliberately avoided:** PyMuPDF/MuPDF (AGPL-3.0 or commercial license) despite its convenience,
and Ghostscript (AGPL). MPL-2.0 (pikepdf) only requires sharing modifications to pikepdf's own files.
