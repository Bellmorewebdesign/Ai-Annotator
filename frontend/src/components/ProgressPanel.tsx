"use client";

import { useEffect, useState } from "react";
import type { Job, PhaseView } from "@/lib/api";
import { formatDuration, formatElapsed, plural } from "@/lib/format";
import { Card, IconCheck, Notice, Spinner, cx } from "./ui";

function phaseCount(p: PhaseView): string | null {
  if (p.status === "skipped") return p.detail ?? "Skipped";
  if (p.detail) return p.detail;
  if (p.total == null) return null;
  switch (p.unit) {
    case "pages":
      return p.status === "done" ? plural(p.total, "page") : `${p.done} of ${p.total} pages`;
    case "requests":
      return p.total > 1 ? `${p.done} of ${p.total} sections` : null;
    case "selections":
      return p.status === "done" ? plural(p.total, "passage") : null;
    case "annotations":
      return p.status === "done" ? plural(p.total, "annotation") : null;
    default:
      return null;
  }
}

function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function ProgressPanel({ job, connectionError }: { job: Job; connectionError: string | null }) {
  const now = useNow();
  const phases = job.progress.phases ?? [];
  const elapsed = job.started_at ? now / 1000 - job.started_at : 0;
  const eta = job.progress.eta_seconds;
  const queued = job.status === "queued";

  return (
    <div className="mx-auto w-full max-w-xl px-4 pb-20 pt-12 sm:px-6 sm:pt-16">
      <p className="truncate text-[13px] text-muted" title={job.document.name}>
        {job.document.name}
      </p>
      <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">
        {queued ? "Waiting to start" : "Annotating your document"}
      </h1>
      <p className="mt-1.5 text-sm text-muted" aria-live="polite">
        {queued ? (
          job.queue_position ? (
            `${plural(job.queue_position, "document")} ahead of this one.`
          ) : (
            "Next in line."
          )
        ) : (
          <>
            {formatElapsed(elapsed)} elapsed
            <span className="mx-1.5 text-faint">·</span>
            {eta != null ? `${formatDuration(eta)} remaining` : "Estimating..."}
          </>
        )}
      </p>

      <Card className="mt-6 overflow-hidden">
        <ol className="divide-y divide-line">
          {phases.map((p) => {
            const count = phaseCount(p);
            const active = p.status === "active";
            const fraction = active && p.total && p.total > 1 ? p.done / p.total : null;
            return (
              <li key={p.key} className={cx("relative px-5 py-3", active && "bg-subtle/60")}>
                <div className="flex items-center gap-3">
                  <StepIcon status={p.status} />
                  <span
                    className={cx(
                      "flex-1 text-sm",
                      p.status === "pending" && "text-faint",
                      p.status === "skipped" && "text-faint line-through",
                      active && "font-medium text-ink",
                      p.status === "done" && "text-ink-2",
                    )}
                  >
                    {p.label}
                  </span>
                  {count && <span className="text-[13px] tabular-nums text-muted">{count}</span>}
                </div>
                {fraction !== null && (
                  <div
                    className="ml-8 mt-2 h-1 overflow-hidden rounded-full bg-line"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={p.total ?? 0}
                    aria-valuenow={p.done}
                    aria-label={p.label}
                  >
                    <div className="h-full rounded-full bg-ink transition-[width] duration-500" style={{ width: `${fraction * 100}%` }} />
                  </div>
                )}
              </li>
            );
          })}
          {phases.length === 0 && (
            <li className="flex items-center gap-3 px-5 py-3 text-sm text-muted">
              <Spinner /> Preparing
            </li>
          )}
        </ol>
      </Card>

      <div className="mt-4 space-y-3">
        {job.worker_alive === false && (
          <Notice tone="warn" title="The background worker is not running">
            Processing is paused. Start it again with <code className="font-mono text-[12px]">python dev.py</code>;
            this job will continue from where it stopped.
          </Notice>
        )}
        {connectionError && <Notice tone="warn">{connectionError}</Notice>}
        <p className="text-[13px] leading-5 text-muted">
          You can close this tab. Processing continues on this computer as long as AI Annotator is running. Come back
          to this page or find it under Recent documents.
        </p>
      </div>
    </div>
  );
}

function StepIcon({ status }: { status: PhaseView["status"] }) {
  if (status === "done")
    return (
      <span className="grid size-5 place-items-center rounded-full bg-ink text-white">
        <IconCheck className="size-3" />
      </span>
    );
  if (status === "active")
    return (
      <span className="grid size-5 place-items-center text-ink">
        <Spinner className="size-4" />
      </span>
    );
  return <span className="mx-0.5 size-4 rounded-full border border-line-strong" />;
}
