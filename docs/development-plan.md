# Development Plan

Status legend: [x] done and tested, [~] partially done (see notes), [ ] not started.
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
- [ ] Repository structure, configuration, `.env.example`
- [ ] Backend (FastAPI) and frontend (Next.js) integration
- [ ] Upload UI with validation

### Milestone 2: Annotation engine
- [~] PDF extraction, layout and reading order (prototype verified on a two-column sample)
- [ ] Claude analysis with structured output
- [ ] Exact matching and geometry
- [ ] Real PDF highlight annotations and export
- [ ] Accuracy tests (synthetic fixtures + rendered pixel checks)

### Milestone 3: Advanced annotations
- [ ] Custom categories and colors, category detection from instructions
- [ ] Underlines
- [ ] Comments (`/Contents` on annotations)
- [ ] Boxes and circles for validated regions
- [ ] Instruction document processing
- [ ] DOCX, TXT, PNG, JPG, scanned PDF
- [ ] Expanded-margin output (widened page with notes beside the text): next milestone

### Milestone 4: User experience
- [ ] Polished interface, background processing, persistence, real progress, PDF preview, results

### Milestone 5: Quality and optimization
- [ ] End-to-end test (API + worker + fake AI)
- [ ] Long-document planning pass and sections
- [ ] API cost reporting
- [ ] Error recovery (leases, checkpoints)
- [ ] Windows setup documentation

## Next milestones (proposed)

1. Expanded-margin mode with FreeText notes laid out beside the original page.
2. Visual understanding of figures: send cropped figure images to Claude to decide whether a figure
   matters and to describe it in notes.
3. Searchable OCR output (invisible text layer) so scanned outputs are selectable.
4. Accounts, per-user job lists and authorization on job URLs.
5. Cloud deployment: object storage, Postgres, queue-backed workers, presigned uploads.
6. Evaluation harness: a labelled corpus of real papers with gold annotations to compare models,
   effort levels and prompts on selection quality and cost.

See `docs/testing.md` for the current test report and known issues.
