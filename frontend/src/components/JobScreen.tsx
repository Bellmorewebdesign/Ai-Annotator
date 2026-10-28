"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, getJob, type Job } from "@/lib/api";
import { forgetJob, rememberJob } from "@/lib/recent";
import { ProgressPanel } from "./ProgressPanel";
import { ResultsView } from "./ResultsView";
import { Card, IconAlert, IconArrowLeft, Notice, Spinner, buttonClass } from "./ui";

const ACTIVE_POLL_MS = 1500;
const HIDDEN_POLL_MS = 6000;

export function JobScreen({ id }: { id: string }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const next = await getJob(id);
        if (stopped) return;
        setJob(next);
        setError(null);
        rememberJob({ id: next.id, name: next.document.name, createdAt: next.created_at });
        if (next.status === "completed" || next.status === "failed") return;
      } catch (e) {
        if (stopped) return;
        const err = e as ApiError;
        setError(err);
        if (err.status === 404) {
          forgetJob(id);
          return;
        }
      }
      const delay = document.visibilityState === "visible" ? ACTIVE_POLL_MS : HIDDEN_POLL_MS;
      timer.current = setTimeout(poll, delay);
    };
    void poll();
    const onVisible = () => {
      if (document.visibilityState === "visible" && timer.current) {
        clearTimeout(timer.current);
        void poll();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      if (timer.current) clearTimeout(timer.current);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [id]);

  if (error?.status === 404) {
    return (
      <CenteredMessage
        title="This job no longer exists"
        body="It may have been deleted, or its files were removed after the retention period."
      />
    );
  }
  if (!job) {
    if (error) return <CenteredMessage title="Can't load this job" body={error.message} />;
    return (
      <div className="grid flex-1 place-items-center text-muted">
        <Spinner className="size-5" />
      </div>
    );
  }

  if (job.status === "completed" && job.result) return <ResultsView job={job} />;
  if (job.status === "failed") return <FailedView job={job} />;
  return <ProgressPanel job={job} connectionError={error?.message ?? null} />;
}

function CenteredMessage({ title, body }: { title: string; body: string }) {
  return (
    <div className="mx-auto w-full max-w-md px-4 pt-24 text-center">
      <IconAlert className="mx-auto size-6 text-muted" />
      <h1 className="mt-3 text-lg font-semibold text-ink">{title}</h1>
      <p className="mt-1.5 text-sm text-muted">{body}</p>
      <Link href="/" className={buttonClass("primary", "md", "mt-6")}>
        Annotate a document
      </Link>
    </div>
  );
}

function FailedView({ job }: { job: Job }) {
  return (
    <div className="mx-auto w-full max-w-xl px-4 pt-14 sm:px-6">
      <Link href="/" className="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink">
        <IconArrowLeft className="size-3.5" /> New document
      </Link>
      <Card className="mt-4 p-6">
        <p className="truncate text-[13px] text-muted">{job.document.name}</p>
        <h1 className="mt-1 text-lg font-semibold text-ink">We couldn&apos;t annotate this document</h1>
        <Notice tone="danger" className="mt-4">
          {job.error?.message ?? "Something went wrong while processing this document."}
        </Notice>
        <div className="mt-5 flex gap-2">
          <Link href="/" className={buttonClass("primary")}>
            Try another document
          </Link>
        </div>
      </Card>
    </div>
  );
}
