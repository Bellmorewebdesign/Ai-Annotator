# Development Plan

Status legend: [x] implemented and tested, [~] partially done (see notes), [ ] not started.
This file is the running record of unfinished work and known issues. Keep it current.

## Major technical risks (identified up front)

1. **Reading order in multi-column PDFs.** Wrong order breaks quotes that wrap lines. Mitigated by
   gutter detection + band ordering, and by matching that tolerates passages split across columns.
2. **AI quoting fidelity.** Models paraphrase. Mitigated by passage ids, verbatim-quote instructions,
   skeleton matching and a strict fuzzy threshold; unmatched quotes are never drawn.
3. **Annotation rendering differences across viewers.** Mitigated by explicit appearance streams.
4. **Coordinate systems** (rotation, crop boxes, sideways text). Mitigated by the frame transform and
   rotated-page tests.
5. **OCR quality.** Tesseract varies by scan quality; confidence is measured and surfaced.
6. **Windows setup friction** (Tesseract, LibreOffice). Both are optional; the app reports what is
   available through `/api/health` and the UI.
7. **Cost of long documents.** Prompt caching between analysis and summary; planning pass + sections
   only for very long documents.

## Milestones

### Milestone 1: Foundation
- [x] Repository structure, configuration, `.env.example`
- [x] Backend (FastAPI) and frontend (Next.js) integration
- [x] Upload UI with validation (client-side checks + server-side content validation)

### Milestone 2: Annotation engine
- [x] PDF extraction, layout and reading order (PDFium; columns, bands, roles)
- [~] Claude analysis with structured output: implemented and unit-tested with a stubbed SDK;
      **not yet run against the live API** (no key during development)
- [x] Exact matching and geometry
- [x] Real PDF highlight annotations and export
- [x] Accuracy tests (synthetic fixtures + rendered pixel checks + independent verifier)

### Milestone 3: Advanced annotations
- [x] Custom categories and colors, category detection from instructions
- [x] Underlines
- [x] Comments (`/Contents` on annotations; shown in the results list)
- [x] Boxes and circles for validated regions (images high confidence, ruled tables/vector figures medium)
- [x] Instruction document processing (PDF, DOCX, TXT, images via OCR)
- [x] DOCX (LibreOffice, flagged fallback), TXT, PNG, JPG, scanned PDF (Tesseract)
- [ ] Expanded-margin output (widened page with notes beside the text): next milestone

### Milestone 4: User experience
- [x] Polished interface, background processing, persistence, real progress, PDF preview, results

### Milestone 5: Quality and optimization
- [x] End-to-end test (browser + API + worker + scripted AI), in CI
- [x] Long-document planning pass and sections (tested with the scripted client)
- [x] API cost reporting (per call and per job)
- [x] Error recovery (leases, heartbeats, checkpoints, attempt limits)
- [x] Windows setup documentation (not yet executed by hand on Windows; CI covers backend tests)

## Known issues and gaps

- Live Claude behaviour is unmeasured: selection quality, verbatim-quote rate, cost per page. First task
  once a key is available: run 10 to 20 real papers and record unmatched-quote rates and costs.
- Comments are stored on the highlight (`/Contents`). Viewers show them on hover/click; there is no
  visible margin icon. The PDF.js preview in the app does not show popups on hover (notes are listed
  in the sidebar instead).
- Vector-figure detection is heuristic; tables without horizontal rules are not detected as regions.
- Headers/footers are detected by repetition; a single-page document's running header may be treated as body.
- Uploads are bounded by Content-Length plus a streaming check; requests without Content-Length are
  spooled by the server before the size check (acceptable locally, revisit for cloud).
- Job links are the only access control (no accounts).
- ETA appears only after at least two completed jobs have recorded throughput for the AI phases.

## Next milestones (proposed)

1. **Live API validation and an evaluation harness**: a labelled corpus of real papers with gold
   annotations to compare models, effort levels and prompts on selection quality and cost.
2. Expanded-margin mode with FreeText notes laid out beside the original page (non-overlapping layout).
3. Visual understanding of figures: send cropped figure images to Claude to decide whether a figure
   matters and to describe it in notes.
4. Searchable OCR output (invisible text layer) so scanned outputs are selectable.
5. Accounts, per-user job lists and authorization on job URLs.
6. Cloud deployment: object storage, Postgres, queue-backed workers, presigned uploads for large files.
