"use client";

import type { PDFDocumentLoadingTask, PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";
import { useCallback, useEffect, useRef, useState } from "react";
import { loadPdfJs } from "@/lib/pdf";
import { IconChevronLeft, IconChevronRight, IconMinus, IconPlus, Spinner, cx } from "./ui";

const ZOOMS = [0.5, 0.67, 0.8, 1, 1.25, 1.5, 2, 3];

/**
 * Renders the annotated PDF itself (annotation appearance streams included), so the
 * preview is the real output file rather than a reconstruction.
 */
export function PdfViewer({ url, jump }: { url: string; jump: { page: number; nonce: number } | null }) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [baseWidth, setBaseWidth] = useState(612);
  const [containerWidth, setContainerWidth] = useState(0);
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const [current, setCurrent] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const scroller = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    let destroyed = false;
    let task: PDFDocumentLoadingTask | null = null;
    loadPdfJs()
      .then((pdfjs) => {
        if (destroyed) return null;
        task = pdfjs.getDocument({ url });
        return task.promise;
      })
      .then(async (d) => {
        if (!d || destroyed) return;
        const first = await d.getPage(1);
        setBaseWidth(first.getViewport({ scale: 1 }).width);
        setDoc(d);
      })
      .catch(() => !destroyed && setError("The preview could not be loaded. You can still download the PDF."));
    return () => {
      destroyed = true;
      void task?.destroy();
    };
  }, [url]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fitScale = containerWidth > 0 ? Math.min(1.6, Math.max(0.3, (containerWidth - 32) / baseWidth)) : 1;
  const scale = zoom === "fit" ? fitScale : zoom;

  const goTo = useCallback((page: number, smooth = true) => {
    const el = pageRefs.current[page - 1];
    el?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  }, []);

  useEffect(() => {
    if (jump && doc) goTo(Math.min(Math.max(1, jump.page), doc.numPages));
  }, [jump, doc, goTo]);

  // Track the page nearest the top of the viewport.
  useEffect(() => {
    const root = scroller.current;
    if (!root || !doc) return;
    const onScroll = () => {
      const top = root.getBoundingClientRect().top;
      let best = 1;
      let bestDist = Infinity;
      pageRefs.current.forEach((el, i) => {
        if (!el) return;
        const d = Math.abs(el.getBoundingClientRect().top - top - 16);
        if (d < bestDist) {
          bestDist = d;
          best = i + 1;
        }
      });
      setCurrent(best);
      setPageInput(String(best));
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => root.removeEventListener("scroll", onScroll);
  }, [doc]);

  const stepZoom = (dir: 1 | -1) => {
    const now = scale;
    const next = dir > 0 ? ZOOMS.find((z) => z > now + 0.01) : [...ZOOMS].reverse().find((z) => z < now - 0.01);
    if (next) setZoom(next);
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-[#efede9]">
      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-line bg-surface px-2">
        <div className="flex items-center gap-0.5">
          <ToolButton label="Previous page" onClick={() => goTo(Math.max(1, current - 1))} disabled={!doc || current <= 1}>
            <IconChevronLeft />
          </ToolButton>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const n = parseInt(pageInput, 10);
              if (doc && n >= 1 && n <= doc.numPages) goTo(n);
              else setPageInput(String(current));
            }}
            className="flex items-center gap-1 text-[13px] text-muted"
          >
            <input
              aria-label="Page number"
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value.replace(/\D/g, ""))}
              className="h-7 w-10 rounded-md border border-line bg-surface text-center tabular-nums text-ink focus:border-accent focus:outline-none"
            />
            <span className="tabular-nums">of {doc?.numPages ?? "-"}</span>
          </form>
          <ToolButton
            label="Next page"
            onClick={() => goTo(Math.min(doc?.numPages ?? 1, current + 1))}
            disabled={!doc || current >= (doc?.numPages ?? 1)}
          >
            <IconChevronRight />
          </ToolButton>
        </div>
        <div className="flex items-center gap-0.5">
          <ToolButton label="Zoom out" onClick={() => stepZoom(-1)} disabled={!doc || scale <= ZOOMS[0]}>
            <IconMinus />
          </ToolButton>
          <button
            type="button"
            onClick={() => setZoom("fit")}
            className={cx(
              "h-7 min-w-14 rounded-md px-2 text-[13px] tabular-nums transition-colors hover:bg-hover",
              zoom === "fit" ? "text-ink" : "text-muted",
            )}
            title="Fit to width"
          >
            {Math.round(scale * 100)}%
          </button>
          <ToolButton label="Zoom in" onClick={() => stepZoom(1)} disabled={!doc || scale >= ZOOMS[ZOOMS.length - 1]}>
            <IconPlus />
          </ToolButton>
        </div>
      </div>

      <div ref={scroller} className="relative min-h-0 flex-1 overflow-auto px-4 py-4">
        {error && <p className="pt-16 text-center text-sm text-muted">{error}</p>}
        {!doc && !error && (
          <div className="grid h-64 place-items-center text-muted">
            <Spinner className="size-5" />
          </div>
        )}
        {doc && (
          <div className="flex flex-col items-center gap-4">
            {Array.from({ length: doc.numPages }, (_, i) => (
              <PageView
                key={i}
                doc={doc}
                pageNumber={i + 1}
                scale={scale}
                root={scroller}
                ref={(el) => {
                  pageRefs.current[i] = el;
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolButton({
  label,
  children,
  ...props
}: { label: string; children: React.ReactNode } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...props}
      className="grid size-7 place-items-center rounded-md text-ink-2 transition-colors hover:bg-hover disabled:text-faint disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}

function PageView({
  doc,
  pageNumber,
  scale,
  root,
  ref,
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  scale: number;
  root: React.RefObject<HTMLDivElement | null>;
  ref: (el: HTMLDivElement | null) => void;
}) {
  const holder = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [page, setPage] = useState<PDFPageProxy | null>(null);
  const [visible, setVisible] = useState(pageNumber <= 2);
  const [rendered, setRendered] = useState(false);

  useEffect(() => {
    let alive = true;
    doc.getPage(pageNumber).then((p) => alive && setPage(p));
    return () => {
      alive = false;
    };
  }, [doc, pageNumber]);

  useEffect(() => {
    const el = holder.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setVisible(true), {
      root: root.current,
      rootMargin: "800px 0px",
    });
    io.observe(el);
    return () => io.disconnect();
  }, [root]);

  useEffect(() => {
    if (!page || !visible || !canvasRef.current) return;
    let task: RenderTask | null = null;
    let cancelled = false;
    loadPdfJs().then((pdfjs) => {
      const canvas = canvasRef.current;
      if (cancelled || !canvas) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      const viewport = page.getViewport({ scale: scale * dpr });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      task = page.render({ canvas, viewport, annotationMode: pdfjs.AnnotationMode.ENABLE });
      task.promise.then(
        () => !cancelled && setRendered(true),
        () => undefined, // cancelled by a newer render
      );
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [page, visible, scale]);

  const vp = page?.getViewport({ scale });
  const width = vp?.width ?? 612 * scale;
  const height = vp?.height ?? 792 * scale;

  return (
    <div
      ref={(el) => {
        holder.current = el;
        ref(el);
      }}
      data-page={pageNumber}
      className="relative shrink-0 scroll-mt-4 bg-white shadow-page"
      style={{ width, height }}
    >
      <canvas ref={canvasRef} style={{ width, height }} className="block" aria-label={`Page ${pageNumber}`} />
      {!rendered && (
        <div className="absolute inset-0 grid place-items-center text-faint">
          <Spinner />
        </div>
      )}
    </div>
  );
}
