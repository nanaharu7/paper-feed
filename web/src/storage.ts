export interface Settings {
  token: string;
  repo: string;
}

export interface PendingRun {
  runId: number;
  repo: string;
}

const SETTINGS_KEY = "paper-feed:settings";
const PENDING_KEY = "paper-feed:pending-run";

// localStorage が使えない環境（プライベートブラウズなど）では、このタブの間だけメモリに保持する
const memory = new Map<string, string>();

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    if (value === null) memory.delete(key);
    else memory.set(key, value);
  }
}

function parse(raw: string | null): unknown {
  if (raw === null) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

export function loadSettings(): Settings | null {
  const v = parse(read(SETTINGS_KEY));
  if (isRecord(v) && typeof v.token === "string" && typeof v.repo === "string") {
    return { token: v.token, repo: v.repo };
  }
  return null;
}

export function saveSettings(s: Settings | null): void {
  write(SETTINGS_KEY, s ? JSON.stringify(s) : null);
}

export function loadPendingRun(): PendingRun | null {
  const v = parse(read(PENDING_KEY));
  if (isRecord(v) && typeof v.runId === "number" && typeof v.repo === "string") {
    return { runId: v.runId, repo: v.repo };
  }
  return null;
}

export function savePendingRun(p: PendingRun | null): void {
  write(PENDING_KEY, p ? JSON.stringify(p) : null);
}

export const REPO_RE = /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/;
