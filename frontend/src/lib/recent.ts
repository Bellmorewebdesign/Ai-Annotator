// Recently submitted jobs, remembered in this browser only (there are no accounts yet).

export interface RecentJob {
  id: string;
  name: string;
  createdAt: number;
}

const KEY = "annotator.recent-jobs";
const MAX = 12;

export function loadRecent(): RecentJob[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    const list = raw ? (JSON.parse(raw) as RecentJob[]) : [];
    return Array.isArray(list) ? list.filter((j) => typeof j?.id === "string") : [];
  } catch {
    return [];
  }
}

export function rememberJob(job: RecentJob): void {
  try {
    const list = [job, ...loadRecent().filter((j) => j.id !== job.id)].slice(0, MAX);
    window.localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // Storage unavailable (private mode): the job URL still works.
  }
}

export function forgetJob(id: string): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(loadRecent().filter((j) => j.id !== id)));
  } catch {
    // ignore
  }
}
