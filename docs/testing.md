# Testing and Test Report

## How to run

```bash
make test                                            # backend suite
make lint                                            # ruff, eslint, TypeScript
cd frontend && npm run build
cd frontend && npm run e2e -- <document.pdf>        # needs the stack running in test mode (see README)
```

CI (`.github/workflows/ci.yml`) runs on Ubuntu: the backend suite on Python 3.11 and 3.12 (with Tesseract
and LibreOffice), the frontend lint/typecheck/build, and an end-to-end job that runs `./install.sh --yes`
and `./start.sh --prod` on a fresh machine, then the browser test.

## Principles

- Fixtures are generated in code (`backend/tests/fixtures.py`) with **known word positions**, so tests
  compare annotation geometry against ground truth instead of eyeballing output.
- Rendering is checked at the pixel level: the source and annotated PDFs are rendered with PDFium and
  the difference is measured inside the target word boxes (must change) and on neighbouring words
  (must not change).
- The production verifier (`engine/verify.py`) is itself tested by feeding it deliberately misplaced
  geometry.
- AI behaviour is tested with a scripted client (`ai/fake.py`) and a stubbed SDK; the real API is never
  called in tests.

## Report (latest run)

Environment: Linux, Python 3.13, Tesseract 5.3.4, LibreOffice 24.2, Node 22, Chromium (Playwright).

| Suite | Tests | Result |
|---|---|---|
| `test_engine_highlight.py` (geometry and pixel accuracy) | 18 | all pass |
| `test_engine_inputs.py` (regions, OCR, conversion, validation) | 17 | all pass |
| `test_ai.py` (prompts, parsing, long documents, summary, client retries) | 16 | all pass |
| `test_jobs.py` (API, worker, recovery, retention, ETA) | 20 | all pass |
| **Backend total** | **71** | **71 passed, 0 skipped** |
| Frontend lint, typecheck, production build | | pass |
| Browser end-to-end (`frontend/e2e/smoke.mjs`, production build) | 9 checks | pass |

### Continuous integration

GitHub Actions run 4 (commit `e0e8a93`) passed backend tests on Ubuntu (Tesseract and LibreOffice
installed, nothing skipped), frontend lint/typecheck/build and the browser end-to-end test. (That run also
included a Windows job, since removed because the project targets Linux.)

The first CI runs were valuable: different fonts on the runners exposed a real reading-order bug (the
short second line of a wrapped full-width title was filed into the left column), now fixed and covered by
a test whose title always wraps. They also showed that PDFium substitutes different system fonts for
non-embedded standard fonts on different systems, so glyph-box edges can differ by about 1 pt; accuracy
tests therefore assert "covers the target words and does not reach the neighbouring words" rather than a
fixed edge tolerance.

GitHub Actions run 8 (commit `a577c6a`) passed every Linux job: backend tests on Python 3.11 and 3.12,
frontend lint/typecheck/build, and the fresh-runner job that runs `./install.sh --yes`, `./start.sh --prod`
and the browser test. That job caught a real setup bug the hand test missed: `scripts/setup.py` checked for
optional tools using the system Python, which only worked where that Python happened to have Pillow
installed. It now runs the check inside the project's virtual environment.

### Linux setup verification (Ubuntu 24.04)

On a fresh `git clone`:

- `./install.sh --yes` installed system packages, created the venv, installed Python and website
  packages, created `.env` (mode 600) and found Tesseract and LibreOffice.
- With no Node.js on `PATH`, `./install.sh` downloaded Node.js 22.22.0 into `.tools/node`; the SHA-256
  checksum check passed and everything else used that copy.
- `./start.sh --prod` (test mode) built and served the website; the browser end-to-end test passed.
- `./start.sh --lan`: the website answered on the machine's network address, the API returned the matching
  CORS header, and the browser end-to-end test passed when run through the network address (the browser
  found the API without a configured URL).
- Stopping with SIGTERM/Ctrl+C left no API, worker or website processes behind.

Performance: a 50-page two-column document is extracted in ~1.6 s, indexed in 0.2 s, and 150 annotations
are matched, rendered and verified in ~1 s. The prompt for such a document is ~34k tokens (single AI pass).

### Coverage of the required cases

| Requirement | Test(s) | Status |
|---|---|---|
| Exact word-level highlighting | `test_partial_phrase_highlights_only_those_words`, `test_single_word_highlight` | Verified (geometry within 0.6 pt, neighbours untouched in pixels) |
| Partial sentences | same | Verified |
| Text across multiple lines | `test_selection_across_lines_has_one_quad_per_line` | Verified (one quad per line, no vertical overlap) |
| Two-column layouts | `test_two_column_reading_order_and_highlights`, `test_libreoffice_two_column_justified_document` | Verified, including justified text and hyphenation from a real PDF producer |
| Multiple highlights on one page | several | Verified |
| Repeated quotations | `test_repeated_quotation_uses_cited_passage`, `test_ambiguous_quotation_without_valid_passage_is_rejected` | Verified (cited passage wins; ambiguous quotes are not drawn) |
| Rotated pages | `test_rotated_pages` | Verified for `/Rotate 90` with normal text and for sideways (landscape) content |
| Nonstandard fonts | `test_ligatures_accents_quotes_and_simulated_bold` | Verified: embedded TrueType, ligatures (fi, ti), accents, curly quotes, Courier, simulated bold |
| Words hyphenated across lines | `test_hyphenated_word_across_lines` | Verified (hyphen included in the highlight) |
| Footnotes, captions, numbered headings | `test_footnotes_captions_and_numbered_headings` | Verified |
| Tables | `test_figure_and_table_regions_are_detected_with_captions`, `test_box_and_circle_annotations_render_on_regions` | Verified for ruled tables |
| Embedded images | same | Verified (box geometry equals the image bounds) |
| Long paragraphs across a page break | `test_selection_spanning_a_page_break` | Verified (one annotation per page) |
| OCR output | `test_scanned_pdf_is_ocrd_and_highlights_land_on_words`, `test_image_input_is_converted_and_ocrd` | Verified on clean synthetic scans |
| Unsupported formats, empty, legacy .doc | `test_unsupported_files_are_rejected`, `test_upload_validation_errors` | Verified |
| Corrupted / encrypted PDFs | `test_corrupted_and_encrypted_pdfs_fail_cleanly`, upload tests | Verified |
| API failures | `test_client_*`, `test_ai_failure_marks_job_failed_with_safe_message` | Verified (retries, backoff, auth errors, refusal, bad JSON) |
| Interrupted background jobs / recovery | `test_interrupted_job_is_recovered_by_another_worker`, `test_job_fails_after_too_many_interruptions`, `test_resumed_job_reuses_saved_ai_analysis` | Verified; resumed jobs do not repeat AI calls |
| File downloads | `test_end_to_end_pdf_job`, e2e | Verified |
| PDF output readability | every render test (verifier renders every page) | Verified with PDFium; pdf.js rendering verified in the browser e2e |
| Fabricated quotes never drawn | `test_fabricated_quotation_is_never_drawn`, `test_near_exact_quote_is_accepted_and_flagged` | Verified |
| Layout preserved | `test_existing_annotations_and_content_are_preserved` | Verified (page content streams byte-identical, existing annotations kept) |

## What is not verified yet (honest limitations)

1. **Live Claude API.** No API key was available during development. The client follows the documented
   SDK shapes (structured output, streaming, caching, fallbacks) and is unit-tested with a stubbed SDK,
   but the first real run may surface integration issues. **Selection quality** (whether Claude picks
   the right passages and quotes verbatim reliably) has not been measured. Next step: run a set of real
   papers with real instructions and review results; build an evaluation set (see development plan).
2. **Real academic PDFs.** Network policy blocked downloading papers (arXiv). Layout handling was tested on
   synthetic reportlab PDFs and a LibreOffice-generated two-column paper. LaTeX, InDesign and
   publisher PDFs (complex floats, math, three-column layouts, marginalia) still need testing.
3. **OCR quality** was tested on clean synthetic scans only. Photos of pages, skewed scans and handwriting
   will be worse; confidence is reported to the user.
4. **Viewer coverage.** Output rendering was checked with PDFium (Chrome/Edge engine) and pdf.js. Adobe
   Acrobat and macOS Preview were not available; the annotations use standard types with explicit
   appearance streams, which those viewers support.
5. **Vector figures** (charts drawn with paths) use clustering heuristics with medium confidence; only
   embedded images have high confidence. Unreliable regions fall back to highlighting the caption.
6. **Linux distributions.** The install and start path was verified by hand on Ubuntu 24.04 (see
   below) and runs in CI on a fresh Ubuntu runner. The `dnf`, `pacman` and `zypper` branches of
   `install.sh` use the standard package names but have not been run. Windows and macOS are not
   supported targets.
7. **Right-to-left and vertical scripts** are not handled specially.
