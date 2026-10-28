// Lazy PDF.js loader. The worker is copied to /public by scripts/copy-pdf-worker.mjs.

type PdfJs = typeof import("pdfjs-dist");

let loader: Promise<PdfJs> | null = null;

export function loadPdfJs(): Promise<PdfJs> {
  if (!loader) {
    loader = import("pdfjs-dist").then((pdfjs) => {
      pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
      return pdfjs;
    });
  }
  return loader;
}

export type PdfInspection =
  | { ok: true; pages: number }
  | { ok: false; reason: "password" | "unreadable" };

export async function inspectPdf(file: File): Promise<PdfInspection> {
  const pdfjs = await loadPdfJs();
  const data = new Uint8Array(await file.arrayBuffer());
  const task = pdfjs.getDocument({ data });
  try {
    const doc = await task.promise;
    return { ok: true, pages: doc.numPages };
  } catch (err) {
    const name = (err as { name?: string })?.name;
    return { ok: false, reason: name === "PasswordException" ? "password" : "unreadable" };
  } finally {
    void task.destroy();
  }
}
