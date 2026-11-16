import { describe, expect, it, vi } from "vitest";

import {
  type Client,
  GitHubError,
  dispatch,
  findRunSince,
  getSteps,
  listNotes,
  readFile,
} from "./github";

function mockFetch(...responses: Response[]) {
  const fn = vi.fn<typeof fetch>();
  for (const r of responses) fn.mockResolvedValueOnce(r);
  return fn;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function client(fetchFn: typeof fetch): Client {
  return { token: "t", repo: "akigun/paper-feed-neuro", fetchFn };
}

describe("dispatch", () => {
  it("入力を文字列で送り、返ってきた実行 ID を返す", async () => {
    const fetchFn = mockFetch(json({ workflow_run_id: 42, run_url: "u", html_url: "h" }));
    const id = await dispatch(client(fetchFn), "main", {
      since: "2026-10-01",
      until: "",
      notify: false,
    });
    expect(id).toBe(42);
    const [url, init] = fetchFn.mock.calls[0] ?? [];
    expect(url).toBe(
      "https://api.github.com/repos/akigun/paper-feed-neuro/actions/workflows/feed.yml/dispatches",
    );
    expect(init?.method).toBe("POST");
    expect(typeof init?.body).toBe("string");
    expect(JSON.parse(init?.body as string)).toEqual({
      ref: "main",
      inputs: { since: "2026-10-01", until: "", notify: "false" },
      return_run_details: true,
    });
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer t");
  });

  it("204 のときは null", async () => {
    const fetchFn = mockFetch(new Response(null, { status: 204 }));
    expect(
      await dispatch(client(fetchFn), "main", { since: "", until: "", notify: true }),
    ).toBeNull();
  });

  it("権限不足はわかるメッセージにする", async () => {
    const fetchFn = mockFetch(json({ message: "Resource not accessible" }, 403));
    await expect(
      dispatch(client(fetchFn), "main", { since: "", until: "", notify: true }),
    ).rejects.toThrow(/権限が足りません/);
  });
});

describe("findRunSince", () => {
  const run = (id: number, createdAt: string) => ({
    id,
    status: "queued",
    conclusion: null,
    html_url: "h",
    created_at: createdAt,
  });

  it("起動時刻以降に作られた実行を返す", async () => {
    const fetchFn = mockFetch(
      json({ workflow_runs: [run(2, "2026-11-16T06:00:05Z"), run(1, "2026-11-16T05:00:00Z")] }),
    );
    expect(await findRunSince(client(fetchFn), new Date("2026-11-16T06:00:00Z"))).toBe(2);
  });

  it("見つからなければ null", async () => {
    const fetchFn = mockFetch(json({ workflow_runs: [] }));
    expect(await findRunSince(client(fetchFn), new Date())).toBeNull();
  });
});

it("getSteps は最初のジョブのステップを返す", async () => {
  const fetchFn = mockFetch(
    json({ jobs: [{ steps: [{ name: "Commit", status: "completed", conclusion: "success" }] }] }),
  );
  expect(await getSteps(client(fetchFn), 1)).toEqual([
    { name: "Commit", status: "completed", conclusion: "success" },
  ]);
});

it("readFile は raw で取得し、キャッシュを使わない", async () => {
  const fetchFn = mockFetch(new Response("# note"));
  expect(await readFile(client(fetchFn), "papers/2026-W46.md", "main")).toBe("# note");
  const [url, init] = fetchFn.mock.calls[0] ?? [];
  expect(url).toBe(
    "https://api.github.com/repos/akigun/paper-feed-neuro/contents/papers/2026-W46.md?ref=main",
  );
  expect(new Headers(init?.headers).get("Accept")).toBe("application/vnd.github.raw+json");
  expect(init?.cache).toBe("no-store");
});

describe("listNotes", () => {
  it("Markdown ファイルだけを返す", async () => {
    const fetchFn = mockFetch(
      json([
        { type: "file", name: "2026-W46.md", path: "papers/2026-W46.md" },
        { type: "file", name: "x.json", path: "papers/x.json" },
        { type: "dir", name: "sub.md", path: "papers/sub.md" },
      ]),
    );
    expect(await listNotes(client(fetchFn), "main")).toEqual([
      { name: "2026-W46.md", path: "papers/2026-W46.md" },
    ]);
  });

  it("papers/ がなければ空", async () => {
    const fetchFn = mockFetch(json({ message: "Not Found" }, 404));
    expect(await listNotes(client(fetchFn), "main")).toEqual([]);
  });

  it("それ以外のエラーはそのまま投げる", async () => {
    const fetchFn = mockFetch(json({}, 401));
    await expect(listNotes(client(fetchFn), "main")).rejects.toBeInstanceOf(GitHubError);
  });
});
