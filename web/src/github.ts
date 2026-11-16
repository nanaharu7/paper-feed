const API = "https://api.github.com";
export const WORKFLOW_FILE = "feed.yml";

export class GitHubError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "GitHubError";
    this.status = status;
  }
}

export interface Client {
  token: string;
  /** owner/name */
  repo: string;
  fetchFn?: typeof fetch;
}

export interface RunInfo {
  id: number;
  status: string;
  conclusion: string | null;
  htmlUrl: string;
  createdAt: string;
}

export interface Step {
  name: string;
  status: string;
  conclusion: string | null;
}

export interface NoteFile {
  name: string;
  path: string;
}

export interface RunInputs {
  since: string;
  until: string;
  notify: boolean;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown, field: string): string {
  if (typeof v !== "string") throw new GitHubError(`想定外の応答です（${field}）`, 0);
  return v;
}

function num(v: unknown, field: string): number {
  if (typeof v !== "number") throw new GitHubError(`想定外の応答です（${field}）`, 0);
  return v;
}

function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

function errorMessage(status: number): string {
  switch (status) {
    case 401:
      return "トークンが無効か、期限が切れています";
    case 403:
      return "トークンの権限が足りません（Actions: 読み書き、Contents: 読み取りが必要です）";
    case 404:
      return "リポジトリまたはファイルが見つかりません（トークンの対象リポジトリも確認してください）";
    case 422:
      return "実行の指定が受け付けられませんでした";
    default:
      return `GitHub API のエラーです（${String(status)}）`;
  }
}

async function request(
  c: Client,
  path: string,
  init: RequestInit = {},
  accept = "application/vnd.github+json",
): Promise<Response> {
  const fetchFn = c.fetchFn ?? fetch;
  const res = await fetchFn(`${API}${path}`, {
    ...init,
    // Contents API の応答はブラウザに60秒キャッシュされるため、実行直後の最新を読めるよう無効にする
    cache: "no-store",
    headers: {
      Accept: accept,
      Authorization: `Bearer ${c.token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!res.ok) throw new GitHubError(errorMessage(res.status), res.status);
  return res;
}

async function json(c: Client, path: string): Promise<unknown> {
  return (await request(c, path)).json() as Promise<unknown>;
}

const repoPath = (c: Client): string => `/repos/${encodePath(c.repo)}`;

export async function getDefaultBranch(c: Client): Promise<string> {
  const data = await json(c, repoPath(c));
  if (!isRecord(data)) throw new GitHubError("想定外の応答です（repository）", 0);
  return str(data.default_branch, "default_branch");
}

/** paper-feed のワークフローがあるリポジトリか確かめる */
export async function checkWorkflow(c: Client): Promise<void> {
  await json(c, `${repoPath(c)}/actions/workflows/${WORKFLOW_FILE}`);
}

/** ワークフローを起動する。GitHub が実行 ID を返したときはそれを返す */
export async function dispatch(c: Client, ref: string, inputs: RunInputs): Promise<number | null> {
  const res = await request(c, `${repoPath(c)}/actions/workflows/${WORKFLOW_FILE}/dispatches`, {
    method: "POST",
    body: JSON.stringify({
      ref,
      inputs: { since: inputs.since, until: inputs.until, notify: String(inputs.notify) },
      return_run_details: true,
    }),
  });
  if (res.status === 204) return null;
  const data: unknown = await res.json();
  return isRecord(data) && typeof data.workflow_run_id === "number" ? data.workflow_run_id : null;
}

function parseRun(data: unknown): RunInfo {
  if (!isRecord(data)) throw new GitHubError("想定外の応答です（run）", 0);
  return {
    id: num(data.id, "id"),
    status: str(data.status, "status"),
    conclusion: typeof data.conclusion === "string" ? data.conclusion : null,
    htmlUrl: str(data.html_url, "html_url"),
    createdAt: str(data.created_at, "created_at"),
  };
}

/** 実行 ID が返らなかったときに、起動時刻以降の手動実行を探す */
export async function findRunSince(c: Client, startedAt: Date): Promise<number | null> {
  const data = await json(
    c,
    `${repoPath(c)}/actions/workflows/${WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page=5`,
  );
  if (!isRecord(data) || !Array.isArray(data.workflow_runs)) return null;
  // 端末と GitHub の時計のずれを見込んで少しさかのぼる
  const threshold = startedAt.getTime() - 30_000;
  const run = data.workflow_runs
    .map(parseRun)
    .find((r) => new Date(r.createdAt).getTime() >= threshold);
  return run?.id ?? null;
}

export async function getRun(c: Client, runId: number): Promise<RunInfo> {
  return parseRun(await json(c, `${repoPath(c)}/actions/runs/${String(runId)}`));
}

export async function getSteps(c: Client, runId: number): Promise<Step[]> {
  const data = await json(c, `${repoPath(c)}/actions/runs/${String(runId)}/jobs`);
  if (!isRecord(data) || !Array.isArray(data.jobs)) return [];
  const job: unknown = data.jobs[0];
  if (!isRecord(job) || !Array.isArray(job.steps)) return [];
  return job.steps.filter(isRecord).map((s) => ({
    name: str(s.name, "step.name"),
    status: str(s.status, "step.status"),
    conclusion: typeof s.conclusion === "string" ? s.conclusion : null,
  }));
}

export async function readFile(c: Client, path: string, ref: string): Promise<string> {
  const res = await request(
    c,
    `${repoPath(c)}/contents/${encodePath(path)}?ref=${encodeURIComponent(ref)}`,
    {},
    "application/vnd.github.raw+json",
  );
  return res.text();
}

export async function listNotes(c: Client, ref: string, dir = "papers"): Promise<NoteFile[]> {
  let data: unknown;
  try {
    data = await json(c, `${repoPath(c)}/contents/${encodePath(dir)}?ref=${encodeURIComponent(ref)}`);
  } catch (e) {
    // まだ一度も実行していないリポジトリには papers/ がない
    if (e instanceof GitHubError && e.status === 404) return [];
    throw e;
  }
  if (!Array.isArray(data)) return [];
  return data
    .filter(isRecord)
    .filter((f) => f.type === "file" && typeof f.name === "string" && f.name.endsWith(".md"))
    .map((f) => ({ name: str(f.name, "name"), path: str(f.path, "path") }));
}
