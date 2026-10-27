# AI Annotator

Upload a reading, tell it what matters, and get back a **real annotated PDF**: highlights on the exact
words (not whole lines), underlines, outlined figures and margin notes, plus a study summary whose
quotations and page numbers come straight from the document.

- **Precise.** Claude chooses passages; deterministic code finds the exact glyphs and writes genuine
  PDF annotations. Anything that cannot be placed with certainty is reported, never guessed.
- **Simple.** Drop a file, type instructions or attach a rubric, click *Annotate document*.
- **Background processing.** Close the tab; the job keeps running on your computer and is waiting
  at its link when you come back.

Supported inputs: PDF (best), Word (.docx), text (.txt), images (.png, .jpg) and scanned PDFs (with OCR).
Initial limits: 50 MB and 50 pages per document (configurable).

> Status: first version, built and tested with synthetic and LibreOffice-generated documents and a scripted
> AI client. It has **not yet been run against the live Claude API** in development (no API key was
> available). See [docs/testing.md](docs/testing.md) for exactly what is verified and what is not.

---

## Run it on Windows (step by step)

### 1. Install the prerequisites (once)

| What | Why | Where |
|---|---|---|
| **Python 3.11 or newer** | backend | https://www.python.org/downloads/ (tick *Add python.exe to PATH*) |
| **Node.js 20 or newer** (LTS) | website | https://nodejs.org/ |
| **Git** | to get the code | https://git-scm.com/download/win |
| Tesseract OCR *(optional)* | scanned PDFs and images | https://github.com/UB-Mannheim/tesseract/wiki (Windows installer) |
| LibreOffice *(optional)* | Word files with original layout | https://www.libreoffice.org/download/ |

Optional tools are detected automatically in their default install folders. Without Tesseract, scanned
pages and images cannot be annotated (the app tells you). Without LibreOffice, Word files are converted
with a simplified layout (the app tells you).

### 2. Get the code and set it up

Open **PowerShell** and run:

```powershell
git clone https://github.com/Bellmorewebdesign/Ai-Annotator.git
cd Ai-Annotator
py scripts\setup.py
```

The setup script creates a Python environment in `backend\.venv`, installs all packages, creates your
`.env` file, and reports whether Tesseract and LibreOffice were found. It is safe to run again.

### 3. Add your Anthropic API key

Open the `.env` file in the project folder (Notepad is fine) and fill in:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Get a key at https://console.anthropic.com. The key stays on your computer and is only used by the backend.

### 4. Start the app

```powershell
py dev.py
```

This starts three things together: the API, the background worker, and the website. When you see
`Ready` from `[web]`, open **http://localhost:3000**. Press **Ctrl+C** in the window to stop everything;
an unfinished job continues the next time you start the app.

> On macOS or Linux use `python3 scripts/setup.py` and `python3 dev.py`.

---

## Using it

1. Drop a document anywhere on the page (or click the upload area).
2. Type what you want highlighted, attach the assignment sheet or rubric, or both.
3. Optionally choose density (Light, Balanced, Detailed), review categories and colors
   (*Detect from instructions* reads them from your text and rubric), and pick annotation types.
4. Click **Annotate document**. The progress page shows real steps and counts. You can close the tab.
5. On the results page: preview the annotated PDF, browse annotations and the summary, and download
   the PDF and the summary (PDF or Markdown).

Recent documents are remembered in your browser. Files are deleted from your computer automatically after
72 hours (`ANNOTATOR_RETENTION_HOURS`), or immediately with the delete button.

---

## Configuration

All settings live in `.env` (see [.env.example](.env.example) for every option with comments). The most
useful ones:

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | | Required. |
| `ANNOTATOR_MODEL` | `claude-opus-5-5` | Model for analysis and summaries. |
| `ANNOTATOR_ANALYSIS_EFFORT` | `high` | Reasoning effort for choosing passages. |
| `ANNOTATOR_MAX_UPLOAD_MB` / `ANNOTATOR_MAX_PAGES` | 50 / 50 | Upload limits. |
| `ANNOTATOR_RETENTION_HOURS` | 72 | Automatic deletion of uploads and results. |
| `ANNOTATOR_FUZZY_ACCEPT_SCORE` | 95 | How close a quote must be to the text to be drawn. |
| `ANNOTATOR_TESSERACT_CMD`, `ANNOTATOR_SOFFICE_CMD` | auto | Paths if the tools are installed somewhere unusual. |

The estimated AI cost of each job is shown under **Details** on the results page.

---

## Development

```text
backend/            Python: FastAPI API, worker, annotation engine, AI layer, tests
frontend/           Next.js + TypeScript + Tailwind + PDF.js
docs/               product spec, architecture, development plan, test report
dev.py              start everything
scripts/setup.py    one-time setup
CLAUDE.md           project rules and conventions (read this before contributing)
```

```powershell
cd backend
.venv\Scripts\python -m pytest            # 71 tests: engine accuracy, AI layer, jobs, API
.venv\Scripts\python -m ruff check annotator tests
cd ..\frontend
npm run lint; npm run typecheck; npm run build
```

Browser end-to-end test (runs the real API, worker and website with a scripted AI client):

```powershell
# terminal 1
$env:ANNOTATOR_ENV="test"; $env:ANNOTATOR_AI_PROVIDER="fake"; py dev.py
# terminal 2
cd frontend; npm run e2e -- C:\path\to\some.pdf
```

Test mode is clearly labelled in the UI and can only be enabled with `ANNOTATOR_ENV=test`.

Further reading: [architecture](docs/architecture.md) · [product spec](docs/product-spec.md) ·
[development plan and known issues](docs/development-plan.md) · [test report](docs/testing.md)

## Privacy and security notes

- The API key never leaves the backend. Uploaded documents are sent to the Anthropic API for analysis.
- Files are stored locally under `data/` with random job ids, under fixed internal names (no user-supplied
  paths), and deleted after the retention period or on request.
- Logs contain ids, counts and timings, never document text.
- There are no accounts yet: anyone who can reach the server and knows a job's link can open it. Run it on
  your own computer (the default binds to `127.0.0.1`), not on a shared network.
- No encryption at rest is implemented.
