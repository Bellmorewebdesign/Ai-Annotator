# AI Annotator: Product Specification (v1)

## Summary

AI Annotator turns a document plus a student's instructions into a **genuinely annotated PDF**: real
PDF highlight, underline, box and comment annotations placed on the exact words that matter, plus a
grounded summary. Our differentiator is **precision combined with simplicity**. No Zotero, no
Python scripts, no copying AI output into a PDF editor.

Primary audience: high school and college students who read and annotate academic material.

## Core flow

1. Open the site.
2. Drop a document anywhere on the page (or click to choose one).
3. Type instructions, attach an instruction document (rubric, assignment sheet), or both.
4. Optionally adjust density (Light, Balanced, Detailed) and categories/colors.
5. Click **Annotate document**.
6. Watch real progress (phase, completed work units, measured ETA when trustworthy).
7. Close the tab if desired; processing continues on the server/worker.
8. Return via the job URL (or "Recent documents") to a completed job.
9. Preview the actual annotated PDF, then download it and the summary.

## Supported inputs (v1)

| Format | Pipeline | Notes |
|---|---|---|
| PDF (text) | Native extraction (PDFium) | Strongest support. Primary target. |
| PDF (scanned) | Per-page OCR (Tesseract) | Requires Tesseract installed. OCR confidence shown. |
| DOCX | Convert to PDF (LibreOffice) | Fallback without LibreOffice: simplified text layout, clearly flagged. |
| TXT | Rendered to a clean PDF | UTF-8, UTF-16 and Windows-1252 detected. |
| PNG / JPG | Image to PDF page + OCR | Requires Tesseract. Limitations shown to the user. |

Limits (configurable): 50 MB upload, 50 pages. Instruction attachments: 10 MB, same formats.

Output: annotated **PDF**; summary as **Markdown** and **PDF**.

## Annotation types

- **Precision highlights**: exact words/phrases/sentences, across lines, columns and pages.
- **Underlines**: key terms and short passages.
- **Margin notes**: the comment text is stored on the highlight itself (`/Contents`), which every
  major viewer shows as the annotation's note. Expanded-margin output is a documented next milestone.
- **Boxes and circles**: only on visual regions whose geometry is reliable (embedded images,
  vector figure clusters, ruled tables). Otherwise the caption is highlighted and the figure is
  flagged instead of drawing a guessed box.

## Categories and colors

Users define categories (name, color, optional description). Defaults (used when the user does not
customize): Main claims, Evidence, Counterarguments, Methods, Key terms, Limitations. Categories can
be detected from the typed instructions and the attached rubric before submission so the user can
review them. Conflicting instructions are surfaced in the results.

## Density

| Setting | Intent |
|---|---|
| Light | Only the most important passages; roughly 1 to 2 per page. |
| Balanced | Key claims and the strongest evidence; roughly 2 to 4 per page. |
| Detailed | Thorough coverage for close reading; roughly 4 to 8 per page. |

Density is guidance for selectivity, never a quota. The engine also caps the share of highlighted
text so the output never becomes a wall of color.

## Accuracy guarantees

- The AI never supplies coordinates for text. It cites a passage id and quotes the text verbatim;
  deterministic code finds the exact glyphs.
- A quote that cannot be found exactly (or near-exactly above a strict threshold) is **not drawn**;
  it is reported as unmatched.
- After rendering, every annotation is re-verified against the output file (text under each quad,
  page bounds, untouched page content).

## Results page

Completion status, document name, PDF.js preview of the real output, pagination and zoom, color
legend, counts by category, the summary, the list of annotations (click to jump), warnings
(unmatched quotes, OCR quality, conversions), downloads, start another job, delete job.

## Privacy

API keys stay server-side. Files live under the local data directory with random job ids and are
deleted after a configurable retention period (default 72 hours) or on request. No accounts in v1.

## Out of scope (v1)

Payments, accounts, dashboards, referral programs, mobile apps, marketing site, collaboration,
live editing, 1 GB documents, chatbot features.
