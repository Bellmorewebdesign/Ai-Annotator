// Typed client for the AI Annotator backend (FastAPI).

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type Density = "light" | "balanced" | "detailed";

export interface Category {
  id: string;
  name: string;
  color: string;
  description: string;
}

export interface Health {
  status: string;
  ai: { configured: boolean; test_mode: boolean; model: string };
  ocr: { available: boolean; language: string };
  docx_conversion: "libreoffice" | "simplified";
  worker: { alive: boolean };
  limits: { max_upload_mb: number; max_pages: number; max_instruction_mb: number; max_instruction_chars: number };
  retention_hours: number;
  default_categories: Category[];
  palette: Record<string, string>;
}

export interface PhaseView {
  key: string;
  label: string;
  status: "pending" | "active" | "done" | "skipped";
  done: number;
  total: number | null;
  unit: string | null;
  detail: string | null;
}

export interface AnnotationView {
  id: string;
  kind: "highlight" | "underline" | "box" | "circle";
  category_id: string;
  category: string;
  color: string;
  pages: number[];
  text: string;
  comment: string;
  reason: string;
  importance: string;
  match: "exact" | "relocated" | "approximate" | "region" | "caption";
  note: string;
}

export interface SummaryPoint {
  text: string;
  annotation_ids: string[];
}

export interface SummaryData {
  title: string;
  purpose: string;
  sections: { heading: string; points: SummaryPoint[] }[];
  rubric_alignment: { criterion: string; assessment: string; annotation_ids: string[] }[];
  takeaways: string[];
}

export interface JobResult {
  annotations: AnnotationView[];
  categories: (Category & { count: number })[];
  rejected: { id: string; kind: string; quote: string; reason: string }[];
  instruction_notes: { kind: string; message: string }[];
  warnings: string[];
  overview: string;
  summary: SummaryData | null;
  conversion: { method: string; notes: string[] };
  stats: {
    pages: number;
    ocr_pages: number;
    selections: number;
    placed: number;
    approximate: number;
    unmatched: number;
    verified_annotations: number;
    analysis_mode: string;
  };
}

export interface Job {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
  expires_at: number | null;
  attempts: number;
  document: { name: string; size: number; kind: string; kind_label: string; pages: number | null };
  instructions_file: { name: string; size: number } | null;
  options: { density: Density; instructions: string; categories_mode: string };
  progress: { phases?: PhaseView[]; current?: string; eta_seconds?: number | null; eta_basis?: string };
  queue_position: number | null;
  worker_alive: boolean | null;
  error: { code: string; message: string } | null;
  result: JobResult | null;
  metrics: {
    pages?: number;
    ocr_pages?: number;
    total_seconds?: number;
    ai_cost_usd?: number | null;
    ai_tokens?: { input: number; output: number; cache_read: number; cache_write: number };
    ai_requests?: number;
  };
  files: { annotated?: string; summary_md?: string; summary_pdf?: string };
  test_mode: boolean;
}

export interface CategorySuggestion {
  categories: (Category & { color_from_instructions?: boolean })[];
  notes: string[];
  wants_underline: boolean;
  wants_comments: boolean;
  used_defaults: boolean;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
  ) {
    super(message);
  }
}

const OFFLINE_MESSAGE =
  "Can't reach the AI Annotator server. Make sure it is running (python dev.py) and try again.";

async function parse<T>(res: Response): Promise<T> {
  if (res.ok) return (await res.json()) as T;
  let message = "Something went wrong. Please try again.";
  let code = "error";
  try {
    const body = await res.json();
    if (body?.error?.message) {
      message = body.error.message;
      code = body.error.code ?? code;
    }
  } catch {
    // Non-JSON error body: keep the generic message.
  }
  throw new ApiError(message, res.status, code);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(OFFLINE_MESSAGE, 0, "offline");
  }
  return parse<T>(res);
}

export const getHealth = () => request<Health>("/api/health");
export const getJob = (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`);
export const deleteJob = (id: string) =>
  request<{ deleted: boolean }>(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });

export function fileUrl(path: string, inline = false): string {
  return `${API_BASE}${path}${inline ? "?inline=true" : ""}`;
}

export async function suggestCategories(instructions: string, file: File | null): Promise<CategorySuggestion> {
  const form = new FormData();
  form.set("instructions", instructions);
  if (file) form.set("instructions_file", file);
  return request<CategorySuggestion>("/api/categories/suggest", { method: "POST", body: form });
}

export interface CreateJobInput {
  document: File;
  instructions: string;
  instructionsFile: File | null;
  options: {
    density: Density;
    categories: Category[];
    categories_mode: "auto" | "custom";
    allow_underline: boolean;
    allow_comments: boolean;
    allow_shapes: boolean;
  };
}

/** Upload with real progress reporting (fetch cannot report upload progress). */
export function createJob(input: CreateJobInput, onProgress?: (fraction: number) => void): Promise<Job> {
  const form = new FormData();
  form.set("document", input.document);
  form.set("instructions", input.instructions);
  form.set("options", JSON.stringify(input.options));
  if (input.instructionsFile) form.set("instructions_file", input.instructionsFile);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/jobs`);
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      const body = xhr.response;
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Job);
      else
        reject(
          new ApiError(
            body?.error?.message ?? "The upload failed. Please try again.",
            xhr.status,
            body?.error?.code ?? "error",
          ),
        );
    };
    xhr.onerror = () => reject(new ApiError(OFFLINE_MESSAGE, 0, "offline"));
    xhr.send(form);
  });
}
