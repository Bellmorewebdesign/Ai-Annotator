import { formatBytes } from "./format";

export const DOCUMENT_TYPES: Record<string, string> = {
  pdf: "PDF",
  docx: "Word document",
  txt: "Text file",
  png: "PNG image",
  jpg: "JPEG image",
  jpeg: "JPEG image",
};

export const ACCEPT = ".pdf,.docx,.txt,.png,.jpg,.jpeg,application/pdf,text/plain,image/png,image/jpeg";

export function extensionOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

/** Quick client-side checks. The server validates file contents again. */
export function checkFile(file: File, maxMb: number): string | null {
  const ext = extensionOf(file.name);
  if (ext === "doc") return "Older .doc files are not supported. Save it as .docx or PDF and try again.";
  if (!(ext in DOCUMENT_TYPES)) return "This file type is not supported. Use PDF, DOCX, TXT, PNG or JPG.";
  if (file.size === 0) return "This file is empty.";
  if (file.size > maxMb * 1024 * 1024)
    return `This file is ${formatBytes(file.size)}. The current limit is ${maxMb} MB.`;
  return null;
}

export function isImage(name: string): boolean {
  return ["png", "jpg", "jpeg"].includes(extensionOf(name));
}
