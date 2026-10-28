"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createJob,
  getHealth,
  suggestCategories,
  type Category,
  type Density,
  type Health,
} from "@/lib/api";
import { ACCEPT, DOCUMENT_TYPES, checkFile, extensionOf, isImage } from "@/lib/files";
import { formatBytes, plural, relativeTime } from "@/lib/format";
import { inspectPdf } from "@/lib/pdf";
import { loadRecent, rememberJob, type RecentJob } from "@/lib/recent";
import { CategoriesEditor } from "./CategoriesEditor";
import {
  Button,
  Card,
  IconChevronDown,
  IconFile,
  IconPaperclip,
  IconUpload,
  IconX,
  Notice,
  Spinner,
  cx,
} from "./ui";

const FALLBACK_LIMITS = { max_upload_mb: 50, max_pages: 50, max_instruction_mb: 10, max_instruction_chars: 20000 };
const DENSITIES: { value: Density; label: string; hint: string }[] = [
  { value: "light", label: "Light", hint: "Only what you must not miss" },
  { value: "balanced", label: "Balanced", hint: "Key claims and strongest evidence" },
  { value: "detailed", label: "Detailed", hint: "Thorough, for close reading" },
];

interface PickedDocument {
  file: File;
  pages: number | null;
  checking: boolean;
  error: string | null;
}

export function UploadWorkspace() {
  const router = useRouter();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [doc, setDoc] = useState<PickedDocument | null>(null);
  const [instructions, setInstructions] = useState("");
  const [rubric, setRubric] = useState<File | null>(null);
  const [rubricError, setRubricError] = useState<string | null>(null);
  const [density, setDensity] = useState<Density>("balanced");
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoriesMode, setCategoriesMode] = useState<"auto" | "custom">("auto");
  const [showCategories, setShowCategories] = useState(false);
  const [allowUnderline, setAllowUnderline] = useState(true);
  const [allowComments, setAllowComments] = useState(true);
  const [allowShapes, setAllowShapes] = useState(true);
  const [detecting, setDetecting] = useState(false);
  const [detectNotes, setDetectNotes] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [recent, setRecent] = useState<RecentJob[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const rubricInput = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);

  const limits = health?.limits ?? FALLBACK_LIMITS;

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((h) => {
        if (cancelled) return;
        setHealth(h);
        setCategories((c) => (c.length ? c : h.default_categories));
      })
      .catch((e: ApiError) => !cancelled && setHealthError(e.message));
    // Reading localStorage must happen after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRecent(loadRecent());
    return () => {
      cancelled = true;
    };
  }, []);

  const pickDocument = useCallback(
    async (file: File) => {
      setSubmitError(null);
      const err = checkFile(file, limits.max_upload_mb);
      setDoc({ file, pages: null, checking: !err && extensionOf(file.name) === "pdf", error: err });
      if (err || extensionOf(file.name) !== "pdf") return;
      const res = await inspectPdf(file);
      setDoc((cur) => {
        if (!cur || cur.file !== file) return cur;
        if (!res.ok)
          return {
            ...cur,
            checking: false,
            error:
              res.reason === "password"
                ? "This PDF is password-protected. Remove the password and try again."
                : "This PDF could not be opened. It may be damaged.",
          };
        const tooMany = res.pages > limits.max_pages;
        return {
          ...cur,
          checking: false,
          pages: res.pages,
          error: tooMany ? `This PDF has ${res.pages} pages. The current limit is ${limits.max_pages} pages.` : null,
        };
      });
    },
    [limits.max_pages, limits.max_upload_mb],
  );

  const pickRubric = (file: File) => {
    const err = checkFile(file, limits.max_instruction_mb);
    setRubric(err ? null : file);
    setRubricError(err);
    setDetectNotes([]);
  };

  // Full-page drag and drop.
  useEffect(() => {
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const onEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current += 1;
      setDragging(true);
    };
    const onOver = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault();
    };
    const onLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      const file = e.dataTransfer?.files?.[0];
      if (file && !submitting) void pickDocument(file);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [pickDocument, submitting]);

  const detect = async () => {
    setDetecting(true);
    setDetectNotes([]);
    try {
      const res = await suggestCategories(instructions, rubric);
      if (res.used_defaults) {
        setDetectNotes(["No categories were named in your instructions, so the defaults are kept.", ...res.notes]);
      } else {
        setCategories(res.categories.map(({ id, name, color, description }) => ({ id, name, color, description })));
        setCategoriesMode("custom");
        setDetectNotes(res.notes);
        if (res.wants_underline) setAllowUnderline(true);
        if (res.wants_comments) setAllowComments(true);
      }
      setShowCategories(true);
    } catch (e) {
      setDetectNotes([(e as ApiError).message]);
    } finally {
      setDetecting(false);
    }
  };

  const aiReady = !!health?.ai.configured;
  const needsOcr = !!doc && isImage(doc.file.name) && health && !health.ocr.available;
  const canSubmit = !!doc && !doc.error && !doc.checking && !submitting && aiReady && !needsOcr;

  const submit = async () => {
    if (!doc || !canSubmit) return;
    setSubmitting(true);
    setSubmitError(null);
    setUploadProgress(0);
    try {
      const job = await createJob(
        {
          document: doc.file,
          instructions,
          instructionsFile: rubric,
          options: {
            density,
            categories: categoriesMode === "custom" ? categories : [],
            categories_mode: categoriesMode,
            allow_underline: allowUnderline,
            allow_comments: allowComments,
            allow_shapes: allowShapes,
          },
        },
        setUploadProgress,
      );
      rememberJob({ id: job.id, name: job.document.name, createdAt: job.created_at });
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      setSubmitError((e as ApiError).message);
      setSubmitting(false);
    }
  };

  const ext = doc ? extensionOf(doc.file.name) : "";

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-24 pt-10 sm:px-6 sm:pt-14">
      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-50 grid animate-fade-in place-items-center bg-canvas/85 backdrop-blur-[2px]">
          <div className="rounded-2xl border-2 border-dashed border-ink/30 bg-surface px-10 py-8 text-center shadow-raised">
            <IconUpload className="mx-auto size-6 text-ink" />
            <p className="mt-2 text-[15px] font-medium">Drop to upload</p>
            <p className="mt-0.5 text-[13px] text-muted">PDF, Word, text or image</p>
          </div>
        </div>
      )}

      <div className="mb-8">
        <h1 className="text-[26px] font-semibold tracking-tight text-ink sm:text-[28px]">Annotate a document</h1>
        <p className="mt-1.5 max-w-xl text-[15px] leading-6 text-muted">
          Upload a reading, tell us what matters, and get back a PDF with highlights on the exact words.
        </p>
      </div>

      {healthError && (
        <Notice tone="danger" title="Can't reach the server" className="mb-4">
          {healthError}
        </Notice>
      )}
      {health && !health.ai.configured && (
        <Notice tone="danger" title="AI is not set up yet" className="mb-4">
          Add your Anthropic API key to the <code className="font-mono text-[12px]">.env</code> file as{" "}
          <code className="font-mono text-[12px]">ANTHROPIC_API_KEY</code>, then restart the app.
        </Notice>
      )}
      {health?.ai.test_mode && (
        <Notice tone="warn" title="Test mode" className="mb-4">
          AI responses are scripted for automated testing. Results are not real annotations.
        </Notice>
      )}

      <div className="space-y-4">
        {/* Document */}
        <Card className="p-1.5">
          {!doc ? (
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="group flex w-full flex-col items-center rounded-[10px] border border-dashed border-line-strong px-6 py-12 text-center transition-colors hover:border-ink/40 hover:bg-subtle/60"
            >
              <span className="grid size-11 place-items-center rounded-full bg-subtle text-ink-2 transition-colors group-hover:bg-surface">
                <IconUpload className="size-5" />
              </span>
              <span className="mt-3 text-[15px] font-medium text-ink">Upload a document</span>
              <span className="mt-1 text-[13px] text-muted">
                Drop it anywhere on this page, or click to choose.
              </span>
              <span className="mt-3 text-xs text-faint">
                PDF, Word, text or image · up to {limits.max_upload_mb} MB and {limits.max_pages} pages
              </span>
            </button>
          ) : (
            <div className="flex items-center gap-3 px-3 py-3">
              <span
                className={cx(
                  "grid size-10 shrink-0 place-items-center rounded-lg",
                  doc.error ? "bg-danger-soft text-danger" : "bg-subtle text-ink-2",
                )}
              >
                <IconFile className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink" title={doc.file.name}>
                  {doc.file.name}
                </p>
                <p className="mt-0.5 flex items-center gap-1.5 text-[13px] text-muted">
                  {DOCUMENT_TYPES[ext] ?? "File"} · {formatBytes(doc.file.size)}
                  {doc.checking && (
                    <>
                      {" "}
                      · <Spinner className="size-3" /> counting pages
                    </>
                  )}
                  {doc.pages !== null && <> · {plural(doc.pages, "page")}</>}
                </p>
              </div>
              <Button type="button" size="sm" variant="ghost" onClick={() => fileInput.current?.click()} disabled={submitting}>
                Replace
              </Button>
              <button
                type="button"
                aria-label="Remove document"
                disabled={submitting}
                onClick={() => setDoc(null)}
                className="grid size-8 place-items-center rounded-md text-faint transition-colors hover:bg-hover hover:text-ink"
              >
                <IconX />
              </button>
            </div>
          )}
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            tabIndex={-1}
            aria-label="Choose a document"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void pickDocument(f);
              e.target.value = "";
            }}
          />
        </Card>
        {doc?.error && <Notice tone="danger">{doc.error}</Notice>}
        {doc && isImage(doc.file.name) && health && (
          <Notice tone={health.ocr.available ? "neutral" : "danger"}>
            {health.ocr.available
              ? "Text in images is read with OCR. Handwriting and low-quality photos may be read incorrectly."
              : "Reading images requires Tesseract OCR, which is not installed. See the README for setup."}
          </Notice>
        )}
        {doc && ext === "docx" && health?.docx_conversion === "simplified" && (
          <Notice tone="warn">
            LibreOffice is not installed, so Word files are converted with a simplified layout. Install LibreOffice
            or save as PDF to keep the original formatting.
          </Notice>
        )}

        {/* Instructions */}
        <Card className="p-5">
          <label htmlFor="instructions" className="text-sm font-medium text-ink">
            What should we highlight?
          </label>
          <textarea
            id="instructions"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
            }}
            maxLength={limits.max_instruction_chars}
            rows={5}
            placeholder={
              "For example: I'm writing an argumentative essay on social media and teen mental health. " +
              "Highlight the main claims in yellow, statistics in green, and counterarguments in pink. " +
              "Add short notes on how I could use each piece of evidence."
            }
            className="mt-2 block w-full resize-y rounded-lg border border-line bg-surface px-3.5 py-3 text-[14px] leading-6 text-ink placeholder:text-faint transition-colors hover:border-line-strong focus:border-accent focus:outline-none focus:ring-3 focus:ring-accent/10"
          />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              {rubric ? (
                <span className="inline-flex min-w-0 items-center gap-1.5 rounded-md border border-line bg-subtle py-1 pl-2 pr-1 text-[13px] text-ink-2">
                  <IconPaperclip className="size-3.5 shrink-0" />
                  <span className="truncate" title={rubric.name}>
                    {rubric.name}
                  </span>
                  <span className="shrink-0 text-faint">{formatBytes(rubric.size)}</span>
                  <button
                    type="button"
                    aria-label="Remove attached instructions"
                    onClick={() => setRubric(null)}
                    className="grid size-5 shrink-0 place-items-center rounded text-faint hover:bg-hover hover:text-ink"
                  >
                    <IconX className="size-3" />
                  </button>
                </span>
              ) : (
                <Button type="button" size="sm" onClick={() => rubricInput.current?.click()}>
                  <IconPaperclip className="size-3.5" />
                  Attach instructions
                </Button>
              )}
              {!rubric && <span className="hidden text-xs text-faint sm:inline">Assignment sheet or rubric</span>}
            </div>
            <span className="text-xs tabular-nums text-faint">
              {instructions.length > limits.max_instruction_chars * 0.8 &&
                `${instructions.length.toLocaleString()} / ${limits.max_instruction_chars.toLocaleString()}`}
            </span>
          </div>
          {rubricError && <Notice tone="danger" className="mt-3">{rubricError}</Notice>}
          <input
            ref={rubricInput}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            tabIndex={-1}
            aria-label="Attach instructions"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) pickRubric(f);
              e.target.value = "";
            }}
          />
        </Card>

        {/* Settings */}
        <Card className="divide-y divide-line">
          <div className="p-5">
            <h2 id="density-label" className="text-sm font-medium text-ink">Density</h2>
            <div role="radiogroup" aria-labelledby="density-label" className="mt-2.5 grid grid-cols-3 gap-1 rounded-lg bg-subtle p-1">
              {DENSITIES.map((d) => (
                <button
                  key={d.value}
                  type="button"
                  role="radio"
                  aria-checked={density === d.value}
                  onClick={() => setDensity(d.value)}
                  className={cx(
                    "rounded-md px-2 py-2 text-center transition-all duration-150",
                    density === d.value ? "bg-surface shadow-card" : "hover:bg-hover",
                  )}
                >
                  <span className={cx("block text-[13px] font-medium", density === d.value ? "text-ink" : "text-ink-2")}>
                    {d.label}
                  </span>
                  <span className="mt-0.5 hidden text-xs text-muted sm:block">{d.hint}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="p-5">
            <button
              type="button"
              onClick={() => setShowCategories((s) => !s)}
              aria-expanded={showCategories}
              className="flex w-full items-center justify-between gap-3 text-left"
            >
              <span>
                <span className="block text-sm font-medium text-ink">Categories and colors</span>
                <span className="mt-1 flex flex-wrap items-center gap-1.5">
                  {categories.slice(0, 8).map((c) => (
                    <span key={c.id} className="inline-flex items-center gap-1 text-xs text-muted">
                      <span className="size-2.5 rounded-sm ring-1 ring-black/10" style={{ background: c.color }} />
                      {c.name}
                    </span>
                  ))}
                </span>
              </span>
              <IconChevronDown className={cx("size-4 shrink-0 text-muted transition-transform", showCategories && "rotate-180")} />
            </button>
            {showCategories && health && (
              <div className="mt-4 animate-fade-in">
                {categoriesMode === "auto" && (
                  <p className="mb-3 text-[13px] text-muted">
                    If your instructions name categories or colors, we use those. Otherwise we use the ones below.
                    Editing them here means we use exactly what you set.
                  </p>
                )}
                <CategoriesEditor
                  categories={categories}
                  palette={health.palette}
                  onChange={(next) => {
                    setCategories(next);
                    setCategoriesMode("custom");
                  }}
                  canDetect={!!instructions.trim() || !!rubric}
                  detecting={detecting}
                  onDetect={detect}
                  detectNotes={detectNotes}
                  aiReady={aiReady}
                />
              </div>
            )}
          </div>

          <div className="p-5" role="group" aria-labelledby="types-label">
            <h2 id="types-label" className="text-sm font-medium text-ink">Annotation types</h2>
            <p className="mt-0.5 text-[13px] text-muted">Highlights are always included.</p>
            <div className="mt-3 grid gap-2.5 sm:grid-cols-3">
              {[
                { label: "Underline key terms", checked: allowUnderline, set: setAllowUnderline },
                { label: "Margin notes", checked: allowComments, set: setAllowComments },
                { label: "Outline figures and tables", checked: allowShapes, set: setAllowShapes },
              ].map((o) => (
                <label key={o.label} className="flex cursor-pointer items-center gap-2 text-[13px] text-ink-2">
                  <input
                    type="checkbox"
                    checked={o.checked}
                    onChange={(e) => o.set(e.target.checked)}
                    className="size-4 rounded border-line-strong accent-ink"
                  />
                  {o.label}
                </label>
              ))}
            </div>
          </div>
        </Card>

        {submitError && <Notice tone="danger">{submitError}</Notice>}
        {health && health.ai.configured && !health.worker.alive && (
          <Notice tone="warn" title="The background worker is not running">
            Jobs will wait in the queue until it starts. Run <code className="font-mono text-[12px]">python dev.py</code>{" "}
            to start everything.
          </Notice>
        )}

        <div className="flex flex-col-reverse items-stretch gap-3 pt-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-center text-[13px] text-muted sm:text-left">
            You can close this tab while it works.
          </p>
          <Button type="button" variant="primary" size="lg" disabled={!canSubmit} onClick={() => void submit()} className="sm:min-w-[200px]">
            {submitting ? (
              <>
                <Spinner />
                {uploadProgress < 1 ? `Uploading ${Math.round(uploadProgress * 100)}%` : "Starting"}
              </>
            ) : (
              "Annotate document"
            )}
          </Button>
        </div>
      </div>

      {recent.length > 0 && (
        <section className="mt-14" aria-labelledby="recent-heading">
          <h2 id="recent-heading" className="text-[13px] font-medium text-muted">
            Recent documents
          </h2>
          <ul className="mt-2 divide-y divide-line rounded-xl border border-line bg-surface">
            {recent.map((r) => (
              <li key={r.id}>
                <Link
                  href={`/jobs/${r.id}`}
                  className="flex items-center gap-3 px-4 py-3 text-sm transition-colors first:rounded-t-xl last:rounded-b-xl hover:bg-subtle"
                >
                  <IconFile className="size-4 shrink-0 text-faint" />
                  <span className="min-w-0 flex-1 truncate text-ink">{r.name}</span>
                  <span className="shrink-0 text-xs text-faint">{relativeTime(r.createdAt)}</span>
                </Link>
              </li>
            ))}
          </ul>
          {health && (
            <p className="mt-2 text-xs text-faint">
              Remembered in this browser. Files are deleted from this computer after {health.retention_hours} hours.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
