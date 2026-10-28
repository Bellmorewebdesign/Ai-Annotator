# AI Annotator

Upload a reading, tell it what matters, and get back a **real annotated PDF**: highlights on the exact
words (not whole lines), underlines, outlined figures and margin notes, plus a study summary whose
quotations and page numbers come straight from the document.

- **Precise.** Claude chooses passages; deterministic code finds the exact glyphs and writes genuine
  PDF annotations. Anything that cannot be placed with certainty is reported, never guessed.
- **Simple.** Drop a file, type instructions or attach a rubric, click *Annotate document*.
- **Background processing.** Close the tab; the job keeps running on the server and is waiting at its
  link when you come back.

Supported inputs: PDF (best), Word (.docx), text (.txt), images (.png, .jpg) and scanned PDFs (with OCR).
Initial limits: 50 MB and 50 pages per document (configurable).

> Status: first version. Tested on Linux with synthetic and LibreOffice-generated documents and a scripted
> AI client. It has **not yet been run against the live Claude API** (no API key was available during
> development). See [docs/testing.md](docs/testing.md) for exactly what is verified and what is not.

---

## Quick start (Linux)

Two commands: one to install, one to start.

```bash
git clone https://github.com/Bellmorewebdesign/Ai-Annotator.git
cd Ai-Annotator
./install.sh
```

Then put your Anthropic API key in the `.env` file that the installer created:

```bash
nano .env        # set ANTHROPIC_API_KEY=sk-ant-...
```

Start the app:

```bash
./start.sh
```

Open **http://localhost:3000**. Press **Ctrl+C** in the terminal to stop; an unfinished job continues the
next time you start the app.

Get an API key at https://console.anthropic.com. It stays on your machine and is only used by the backend.

### What `./install.sh` does

1. Installs system packages with your package manager (it asks for your sudo password):
   Python venv support, **Tesseract OCR** (scanned PDFs and images) and **LibreOffice Writer**
   (Word files with their original layout). Supported: `apt` (Ubuntu, Debian), `dnf` (Fedora),
   `pacman` (Arch), `zypper` (openSUSE).
2. Uses your Node.js if it is 20.9 or newer. Otherwise it downloads the official Node.js 22 LTS into
   `.tools/node` inside the project (checksum verified, no sudo, nothing installed system-wide).
   Ubuntu 24.04's own `nodejs` package (version 18) is too old for the website, so this is the usual case.
3. Creates the Python environment in `backend/.venv`, installs all Python and website packages, and
   creates `.env` from `.env.example` (readable only by you).

It is safe to run again (for example after `git pull`). Options:

| Option | Effect |
|---|---|
| `--yes` | No prompts. |
| `--no-system` | Skip system packages (you install Tesseract/LibreOffice yourself, or do without them). |
| `--no-libreoffice` | Skip LibreOffice (a few hundred MB). Word files then use a simplified layout. |

Requirements: a 64-bit Linux (x86-64 or ARM64) with Python 3.11 or newer (Ubuntu 22.04+, Debian 12+,
Fedora 38+). **No GPU is needed.** On Ubuntu 22.04, install Python 3.11 first:
`sudo apt install python3.11 python3.11-venv`.

### Ways to start

| Command | Use it for |
|---|---|
| `./start.sh` | Everyday use and development (website reloads when code changes). |
| `./start.sh --prod` | Faster pages: builds the website first (usually well under a minute), then serves it. |
| `./start.sh --lan` | Open the app from other computers on your network, e.g. run it on a server PC and use it from a laptop. Prints the addresses to use. Implies `--prod`. |
| `./start.sh --api-port 8100 --web-port 3100` | Different ports if 8000/3000 are taken. |

`make install`, `make start`, `make start-prod`, `make test` and `make lint` are shortcuts for the same.

> `--lan` has no login. Anyone who can reach the server's address can use the app and open results by
> their link. Only use it on a network you trust, and do not expose these ports to the internet.

### Running on a headless server

`./start.sh --lan` works over SSH. To keep it running after you log out, use `tmux`
(`tmux new -s annotator`, run `./start.sh --lan`, then detach with Ctrl+B, D). A systemd service is a
planned addition (see [docs/development-plan.md](docs/development-plan.md)).

---

## Using it

1. Drop a document anywhere on the page (or click the upload area).
2. Type what you want highlighted, attach the assignment sheet or rubric, or both.
3. Optionally choose density (Light, Balanced, Detailed), review categories and colors
   (*Detect from instructions* reads them from your text and rubric), and pick annotation types.
4. Click **Annotate document**. The progress page shows real steps and counts. You can close the tab.
5. On the results page: preview the annotated PDF, browse annotations and the summary, and download
   the PDF and the summary (PDF or Markdown).

Recent documents are remembered in your browser. Files are deleted from the server automatically after
72 hours (`ANNOTATOR_RETENTION_HOURS`), or immediately with the delete button.

---

## Configuration

All settings live in `.env`; [.env.example](.env.example) documents every one. The most useful:

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | | Required. |
| `ANNOTATOR_MODEL` | `claude-opus-5-5` | Model for analysis and summaries. |
| `ANNOTATOR_ANALYSIS_EFFORT` | `high` | Reasoning effort for choosing passages. |
| `ANNOTATOR_MAX_UPLOAD_MB` / `ANNOTATOR_MAX_PAGES` | 50 / 50 | Upload limits. |
| `ANNOTATOR_DATA_DIR` | `./data` | Where uploads, results and the job database live. |
| `ANNOTATOR_RETENTION_HOURS` | 72 | Automatic deletion of uploads and results. |
| `ANNOTATOR_OCR_LANGUAGE` | `eng` | Tesseract languages, e.g. `eng+deu` (install `tesseract-ocr-deu`). |
| `ANNOTATOR_FUZZY_ACCEPT_SCORE` | 95 | How close a quote must be to the text to be drawn. |

The estimated AI cost of each job is shown under **Details** on the results page.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "AI is not set up yet" | Set `ANTHROPIC_API_KEY` in `.env`, then restart `./start.sh`. |
| "The background worker is not running" | `./start.sh` was stopped or crashed; start it again. Jobs resume. |
| Port already in use | `./start.sh --api-port 8100 --web-port 3100`, or stop the other program (`ss -ltnp \| grep 3000`). |
| Scanned PDFs or images rejected | Tesseract is missing: rerun `./install.sh` or `sudo apt install tesseract-ocr`. |
| Word files look plain | LibreOffice is missing: rerun `./install.sh` without `--no-libreoffice`. |
| `npm` or `node` not found | Rerun `./install.sh`; it installs a local Node.js if needed. |
| Can't open from another computer | Use `./start.sh --lan` and the printed address; allow ports 3000 and 8000 in your firewall (`sudo ufw allow 3000,8000/tcp`). |

---

## Development

```text
backend/            Python: FastAPI API, worker, annotation engine, AI layer, tests
frontend/           Next.js + TypeScript + Tailwind + PDF.js
docs/               product spec, architecture, development plan, test report
install.sh          one-command setup (Linux)
start.sh            start API, worker and website (wraps dev.py)
CLAUDE.md           project rules and conventions (read this before contributing)
```

```bash
make test                                  # backend: engine accuracy, AI layer, jobs, API
make lint                                  # ruff + eslint + TypeScript
cd frontend && npm run build               # production build of the website
```

Browser end-to-end test (real API, worker and website, with a scripted AI client so no API cost):

```bash
ANNOTATOR_ENV=test ANNOTATOR_AI_PROVIDER=fake ./start.sh     # terminal 1
cd frontend && npx playwright install chromium               # terminal 2, once
cd frontend && npm run e2e -- /path/to/some.pdf              # terminal 2
```

Test mode is clearly labelled in the UI and can only be enabled with `ANNOTATOR_ENV=test`.

CI (GitHub Actions) runs the backend suite on Python 3.11 and 3.12, the frontend checks, and a full
`./install.sh` + `./start.sh --prod` + browser test on a fresh Ubuntu machine for every push.

Further reading: [architecture](docs/architecture.md) · [product spec](docs/product-spec.md) ·
[development plan and known issues](docs/development-plan.md) · [test report](docs/testing.md)

## Hardware and local AI

Everything runs on the CPU; a GPU is not used or required. Annotation analysis currently uses the
Anthropic API. The AI layer is provider-based, so local models (for example on an NVIDIA RTX 3080 or
2060 Super) can be added later as an optional provider; see
[docs/architecture.md](docs/architecture.md#local-ai-and-gpus-future).

## Privacy and security notes

- The API key never leaves the backend. Uploaded documents are sent to the Anthropic API for analysis.
- Files are stored under `data/` with random job ids, under fixed internal names (no user-supplied
  paths), and deleted after the retention period or on request.
- Logs contain ids, counts and timings, never document text.
- There are no accounts yet: anyone who can reach the server and knows a job's link can open it. By
  default everything listens only on `127.0.0.1`; `--lan` opens it to your network.
- No encryption at rest is implemented.
