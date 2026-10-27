"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { deleteJob, fileUrl, type AnnotationView, type Job, type JobResult } from "@/lib/api";
import { formatElapsed, pagesLabel, plural } from "@/lib/format";
import { forgetJob } from "@/lib/recent";
import { PdfViewer } from "./PdfViewer";
import {
  Button,
  IconCheck,
  IconChevronDown,
  IconDownload,
  IconPlus,
  IconTrash,
  Notice,
  buttonClass,
  cx,
} from "./ui";

type Tab = "summary" | "annotations" | "details";

const KIND_LABEL: Record<AnnotationView["kind"], string> = {
  highlight: "Highlight",
  underline: "Underline",
  box: "Box",
  circle: "Circle",
};

export function ResultsView({ job }: { job: Job }) {
  const result = job.result as JobResult;
  const router = useRouter();
  const [tab, setTab] = useState<Tab>(result.summary ? "summary" : "annotations");
  const [jump, setJump] = useState<{ page: number; nonce: number } | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const byId = useMemo(() => new Map(result.annotations.map((a) => [a.id, a])), [result.annotations]);

  const show = (a: AnnotationView) => {
    setActiveId(a.id);
    setJump({ page: a.pages[0], nonce: Date.now() });
  };

  const remove = async () => {
    if (!window.confirm("Delete this document and its results from this computer?")) return;
    setDeleting(true);
    try {
      await deleteJob(job.id);
    } finally {
      forgetJob(job.id);
      router.push("/");
    }
  };

  const issues = result.warnings.length + result.instruction_notes.length + (result.stats.unmatched > 0 ? 1 : 0);
  const visibleCats = result.categories.filter((c) => c.count > 0);

  return (
    <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col px-4 pb-6 pt-6 sm:px-6">
      {/* Header */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-[13px] font-medium text-success">
            <span className="grid size-4 place-items-center rounded-full bg-success text-white">
              <IconCheck className="size-2.5" />
            </span>
            Your document is ready.
          </p>
          <h1 className="mt-1.5 truncate text-[22px] font-semibold tracking-tight text-ink" title={job.document.name}>
            {job.document.name}
          </h1>
          <p className="mt-1 text-[13px] text-muted">
            {plural(result.annotations.length, "annotation")} · {plural(result.stats.pages, "page")}
            {job.metrics.total_seconds != null && <> · finished in {formatElapsed(job.metrics.total_seconds)}</>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <a href={fileUrl(job.files.annotated ?? "")} className={buttonClass("primary")} download>
            <IconDownload />
            Download annotated PDF
          </a>
          <SummaryMenu job={job} />
          <Link href="/" className={buttonClass("ghost", "md", "hidden sm:inline-flex")}>
            <IconPlus />
            New document
          </Link>
          <Button variant="danger" onClick={remove} disabled={deleting} aria-label="Delete document">
            <IconTrash />
          </Button>
        </div>
      </div>

      {job.test_mode && (
        <Notice tone="warn" className="mt-4">
          Test mode: these annotations were produced by a scripted test client, not by the AI.
        </Notice>
      )}

      {/* Body */}
      <div className="mt-5 grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="h-[72vh] min-h-[420px] lg:h-[calc(100vh-11rem)]">
          <PdfViewer url={fileUrl(job.files.annotated ?? "", true)} jump={jump} />
        </div>

        <aside className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-surface lg:h-[calc(100vh-11rem)]">
          {visibleCats.length > 0 && (
            <div className="border-b border-line px-4 py-3">
              <ul className="flex flex-wrap gap-1.5" aria-label="Legend">
                {visibleCats.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setFilter((f) => (f === c.id ? null : c.id));
                        setTab("annotations");
                      }}
                      aria-pressed={filter === c.id}
                      className={cx(
                        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors",
                        filter === c.id ? "border-ink bg-subtle text-ink" : "border-line text-ink-2 hover:bg-subtle",
                      )}
                    >
                      <span className="size-2.5 rounded-sm ring-1 ring-black/10" style={{ background: c.color }} />
                      {c.name}
                      <span className="tabular-nums text-faint">{c.count}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div role="tablist" aria-label="Results" className="flex shrink-0 gap-1 border-b border-line px-2 pt-2">
            {(
              [
                ["summary", "Summary"],
                ["annotations", `Annotations`],
                ["details", issues ? `Details (${issues})` : "Details"],
              ] as [Tab, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className={cx(
                  "-mb-px border-b-2 px-2.5 pb-2 pt-1 text-[13px] font-medium transition-colors",
                  tab === key ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto" role="tabpanel">
            {tab === "summary" && <SummaryTab result={result} byId={byId} onCite={show} />}
            {tab === "annotations" && (
              <AnnotationList
                items={result.annotations.filter((a) => !filter || a.category_id === filter)}
                activeId={activeId}
                onSelect={show}
                filtered={!!filter}
                onClear={() => setFilter(null)}
              />
            )}
            {tab === "details" && <DetailsTab job={job} result={result} />}
          </div>
        </aside>
      </div>
    </div>
  );
}

function SummaryMenu({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <Button onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="menu">
        Download summary
        <IconChevronDown className="size-3.5" />
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 top-11 z-20 w-44 animate-rise rounded-lg border border-line bg-surface p-1 shadow-raised">
          <a role="menuitem" href={fileUrl(job.files.summary_pdf ?? "")} download className="block rounded-md px-3 py-2 text-[13px] text-ink hover:bg-subtle">
            Summary as PDF
          </a>
          <a role="menuitem" href={fileUrl(job.files.summary_md ?? "")} download className="block rounded-md px-3 py-2 text-[13px] text-ink hover:bg-subtle">
            Summary as Markdown
          </a>
        </div>
      )}
    </div>
  );
}

function Cites({ ids, byId, onCite }: { ids: string[]; byId: Map<string, AnnotationView>; onCite: (a: AnnotationView) => void }) {
  const refs = ids.map((i) => byId.get(i)).filter((a): a is AnnotationView => !!a);
  if (!refs.length) return null;
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {refs.map((a) => (
        <button
          key={a.id}
          type="button"
          onClick={() => onCite(a)}
          title={a.text}
          className="rounded border border-line px-1.5 text-[11px] leading-[18px] text-muted transition-colors hover:border-line-strong hover:text-ink"
        >
          {pagesLabel(a.pages)}
        </button>
      ))}
    </span>
  );
}

function SummaryTab({
  result,
  byId,
  onCite,
}: {
  result: JobResult;
  byId: Map<string, AnnotationView>;
  onCite: (a: AnnotationView) => void;
}) {
  const s = result.summary;
  if (!s)
    return (
      <div className="p-4">
        <Notice tone="warn">
          A written summary isn&apos;t available for this document. The annotation list and the downloadable summary
          still include every annotated passage.
        </Notice>
      </div>
    );
  return (
    <div className="space-y-5 px-4 py-4 text-[13.5px] leading-6 text-ink-2">
      {s.purpose && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">Main argument</h3>
          <p className="mt-1.5 text-ink">{s.purpose}</p>
        </section>
      )}
      {s.sections.map((sec) => (
        <section key={sec.heading}>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">{sec.heading}</h3>
          <ul className="mt-1.5 space-y-2">
            {sec.points.map((p, i) => (
              <li key={i} className="relative pl-3.5 before:absolute before:left-0 before:top-[11px] before:size-1 before:rounded-full before:bg-line-strong">
                {p.text}
                <Cites ids={p.annotation_ids} byId={byId} onCite={onCite} />
              </li>
            ))}
          </ul>
        </section>
      ))}
      {s.rubric_alignment.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">Rubric alignment</h3>
          <ul className="mt-1.5 space-y-2">
            {s.rubric_alignment.map((r, i) => (
              <li key={i}>
                <span className="font-medium text-ink">{r.criterion}.</span> {r.assessment}
                <Cites ids={r.annotation_ids} byId={byId} onCite={onCite} />
              </li>
            ))}
          </ul>
        </section>
      )}
      {s.takeaways.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">Key takeaways</h3>
          <ul className="mt-1.5 space-y-1.5">
            {s.takeaways.map((t, i) => (
              <li key={i} className="relative pl-3.5 before:absolute before:left-0 before:top-[11px] before:size-1 before:rounded-full before:bg-line-strong">
                {t}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function AnnotationList({
  items,
  activeId,
  onSelect,
  filtered,
  onClear,
}: {
  items: AnnotationView[];
  activeId: string | null;
  onSelect: (a: AnnotationView) => void;
  filtered: boolean;
  onClear: () => void;
}) {
  if (!items.length)
    return <p className="px-4 py-8 text-center text-sm text-muted">No annotations{filtered ? " in this category" : ""}.</p>;
  return (
    <div>
      {filtered && (
        <button type="button" onClick={onClear} className="w-full border-b border-line px-4 py-2 text-left text-xs text-accent hover:bg-subtle">
          Show all categories
        </button>
      )}
      <ul className="divide-y divide-line">
        {items.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              onClick={() => onSelect(a)}
              className={cx(
                "flex w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-subtle",
                activeId === a.id && "bg-subtle",
              )}
            >
              <span className="mt-1 w-1 shrink-0 self-stretch rounded-full" style={{ background: a.color }} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-[11.5px] text-muted">
                  <span className="font-medium text-ink-2">{pagesLabel(a.pages)}</span>
                  <span>·</span>
                  <span className="truncate">{a.category}</span>
                  {a.kind !== "highlight" && (
                    <>
                      <span>·</span>
                      <span>{KIND_LABEL[a.kind]}</span>
                    </>
                  )}
                  {(a.match === "approximate" || a.match === "caption") && (
                    <span title={a.note} className="ml-auto shrink-0 rounded bg-warn-soft px-1.5 text-[10.5px] font-medium text-warn">
                      {a.match === "approximate" ? "Close match" : "Caption"}
                    </span>
                  )}
                </span>
                <span className="mt-1 line-clamp-3 block text-[13px] leading-5 text-ink">{a.text}</span>
                {a.comment && <span className="mt-1 block text-[12.5px] leading-5 text-muted">{a.comment}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DetailsTab({ job, result }: { job: Job; result: JobResult }) {
  const [showRejected, setShowRejected] = useState(false);
  const m = job.metrics;
  const tokens = m.ai_tokens;
  const rows: [string, string][] = [
    ["Pages", String(result.stats.pages)],
    ...(result.stats.ocr_pages ? ([["Pages read with OCR", String(result.stats.ocr_pages)]] as [string, string][]) : []),
    ["Passages selected by AI", String(result.stats.selections)],
    ["Annotations placed", String(result.stats.placed)],
    ["Close matches (flagged)", String(result.stats.approximate)],
    ["Not drawn", String(result.stats.unmatched)],
    ["Analysis", result.stats.analysis_mode === "sectioned" ? "Planned, in sections" : "Whole document at once"],
    ...(m.total_seconds != null ? ([["Processing time", formatElapsed(m.total_seconds)]] as [string, string][]) : []),
    ["AI requests", String(m.ai_requests ?? 0)],
    ...(tokens
      ? ([
          ["Tokens (input / output)", `${tokens.input.toLocaleString()} / ${tokens.output.toLocaleString()}`],
          ["Tokens read from cache", tokens.cache_read.toLocaleString()],
        ] as [string, string][])
      : []),
    ["Estimated AI cost", m.ai_cost_usd == null ? "Unknown for this model" : `$${m.ai_cost_usd.toFixed(3)}`],
  ];
  return (
    <div className="space-y-4 px-4 py-4">
      {result.instruction_notes.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">About your instructions</h3>
          <ul className="mt-2 space-y-2">
            {result.instruction_notes.map((n, i) => (
              <li key={i}>
                <Notice tone={n.kind === "conflict" ? "warn" : "neutral"}>{n.message}</Notice>
              </li>
            ))}
          </ul>
        </section>
      )}
      {result.warnings.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">Processing notes</h3>
          <ul className="mt-2 space-y-2">
            {result.warnings.map((w, i) => (
              <li key={i}>
                <Notice tone="warn">{w}</Notice>
              </li>
            ))}
          </ul>
        </section>
      )}
      {result.rejected.length > 0 && (
        <section>
          <button
            type="button"
            onClick={() => setShowRejected((s) => !s)}
            aria-expanded={showRejected}
            className="flex w-full items-center justify-between text-left"
          >
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Not drawn ({result.rejected.length})
            </span>
            <IconChevronDown className={cx("size-3.5 text-faint transition-transform", showRejected && "rotate-180")} />
          </button>
          <p className="mt-1 text-[12.5px] leading-5 text-muted">
            Selections that could not be placed with certainty are left out rather than highlighted in the wrong place.
          </p>
          {showRejected && (
            <ul className="mt-2 space-y-2">
              {result.rejected.map((r) => (
                <li key={r.id} className="rounded-lg bg-subtle px-3 py-2 text-[12.5px] leading-5">
                  <span className="line-clamp-2 text-ink-2">&ldquo;{r.quote}&rdquo;</span>
                  <span className="mt-0.5 block text-muted">{r.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">Processing</h3>
        <dl className="mt-2 divide-y divide-line rounded-lg border border-line text-[13px]">
          {rows.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3 px-3 py-2">
              <dt className="text-muted">{k}</dt>
              <dd className="text-right tabular-nums text-ink">{v}</dd>
            </div>
          ))}
        </dl>
        {job.expires_at && (
          <p className="mt-2 text-xs text-faint">
            Files are deleted from this computer on {new Date(job.expires_at * 1000).toLocaleString()}.
          </p>
        )}
      </section>
    </div>
  );
}
