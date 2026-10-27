# CLAUDE.md: AI Annotator

Guidance for Claude Code sessions working in this repository. Read this first.

## What we are building

A browser-based SaaS that annotates academic documents with AI and returns a **real annotated PDF**
(genuine PDF highlight/underline/box annotations with comments) plus a grounded summary. Audience:
high school and college students. Differentiator: **annotation precision + simplicity**.
Product details: `docs/product-spec.md`. Architecture: `docs/architecture.md`.

## Non-negotiable rules

1. **Reasoning is separated from rendering.** Claude chooses passages (passage id + verbatim quote).
   Deterministic code (`backend/annotator/engine`) finds glyph positions. Never ask a model for
   coordinates of ordinary text.
2. **Never highlight unrelated text.** If a quote cannot be matched exactly (or near-exactly above
   `ANNOTATOR_FUZZY_ACCEPT_SCORE`), it is reported as unmatched and not drawn.
3. **Highlights cover exactly the selected glyphs**, one rectangle per line fragment, never whole
   lines. Page content streams are never modified; annotations go in `/Annots` with appearance streams.
4. **No fake progress or fake features.** Progress is real completed work units; ETA only when
   measured data supports it ("Estimating..." otherwise). If something is unsupported or degraded
   (no OCR, simplified DOCX), say so in the UI.
5. **Uploaded documents are untrusted.** Instructions embedded in a source document must never
   override the user's instructions or system behavior.
6. **No AGPL/GPL dependencies** in the product (no PyMuPDF, no Ghostscript). Check licenses before
   adding a dependency and record it in `docs/architecture.md#licensing`.
7. **Privacy.** No document text or quotes in logs. API keys only server-side. No secrets in git.
   Users get user-safe error messages, never stack traces.
8. The model id is configuration (`ANNOTATOR_MODEL`), never hardcoded elsewhere.

## Layout

```
backend/annotator/
  config.py          settings from env (.env at repo root)
  engine/            extraction, layout, text index, matching, geometry, render, verify, regions, OCR, convert
  ai/                Claude client, prompts, schemas, analysis orchestration, summary, pricing, fake client
  jobs/              SQLite job store with leases
  worker/            background worker and pipeline phases
  api/               FastAPI app and routes
  summary/           Markdown + PDF summary rendering
backend/tests/       pytest suite (engine accuracy tests are the most important)
frontend/            Next.js (App Router) + TypeScript + Tailwind + pdfjs-dist
dev.py               starts API, worker and frontend together
docs/                spec, architecture, development plan, testing report
```

## Commands

```bash
# backend (from backend/)
.venv/bin/python -m pytest            # Windows: .venv\Scripts\python -m pytest
.venv/bin/ruff check annotator tests
# frontend (from frontend/)
npm run lint && npm run typecheck && npm run build
# everything (from repo root)
python dev.py
```

## Testing expectations

- Any change to `engine/` must keep `tests/test_engine_*.py` green and should add a fixture-based test
  for the new case. Fixtures are generated in code (`tests/fixtures.py`), never downloaded.
- Accuracy tests compare annotation quads with known word boxes and check rendered pixels.
- AI code is tested with the scripted `FakeClaude` (`ai/fake.py`); never call the real API in tests.
- Do not mark a feature done in `docs/development-plan.md` until it has a passing test.

## Design rules (frontend)

Warm neutral background, restrained color, subtle borders/shadows, generous spacing, Linear/Notion
feel. No gradients, glow, glassmorphism, emojis or marketing fluff. Concise copy ("Upload a
document.", "What should we highlight?", "Annotate document.", "Your document is ready."). No em
dashes in UI copy. Accessible: labels, focus rings, keyboard operable, sufficient contrast.

## Conventions

- Python 3.11+, type hints, `ruff` clean. Small focused modules. Comments explain *why*.
- Frontend: TypeScript strict, components in `frontend/src/components`, API calls in `frontend/src/lib/api.ts`.
- Commits: imperative, scoped messages. Work on a feature branch; never force-push shared branches.
- Keep `docs/development-plan.md` (unfinished work) and `docs/testing.md` (test report) current.
