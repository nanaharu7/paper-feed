import "./style.css";

import { CONFIG_PATH, EXAMPLE_PATH, getInterests, getSources } from "./config";
import {
  type Client,
  type Commit,
  GitHubError,
  QUERIES_WORKFLOW_FILE,
  type RunInfo,
  type Step,
  WORKFLOW_FILE,
  checkWorkflow,
  dispatchWorkflow,
  findRunSince,
  getDefaultBranch,
  getRun,
  getSteps,
  listCommits,
  listNotes,
  readFile,
} from "./github";
import { parseNote, renderNoteHtml, sortNotes } from "./note";
import { PRESETS, type Period, type PresetId, presetPeriod, validatePeriod } from "./period";
import {
  QUERIES_REPORT_PATH,
  type QueriesReport,
  SOURCE_NAMES,
  describeConfigCommit,
  parseQueriesReport,
} from "./report";
import {
  REPO_RE,
  type RunKind,
  type Settings,
  loadPendingRun,
  loadSettings,
  savePendingRun,
  saveSettings,
} from "./storage";

const POLL_INTERVAL_MS = 4000;
const RUN_TIMEOUT_MS = 30 * 60 * 1000;
const TOKEN_URL = "https://github.com/settings/personal-access-tokens/new";

/** ワークフローのステップ名 → 画面に出す名前 */
type StepLabels = Record<string, string>;

interface RunSpec {
  kind: RunKind;
  workflowFile: string;
  labels: StepLabels;
}

const FEED_RUN: RunSpec = {
  kind: "feed",
  workflowFile: WORKFLOW_FILE,
  labels: {
    "Fetch and summarize": "論文の取得と要約",
    Commit: "ノートの保存",
    "Notify Discord": "Discord への通知",
  },
};

const QUERY_RUN: RunSpec = {
  kind: "queries",
  workflowFile: QUERIES_WORKFLOW_FILE,
  labels: {
    "Generate queries": "検索条件の作成と件数の確認",
    Commit: "設定の保存",
  },
};

type Child = Node | string | null | false | undefined;
type Props = Record<string, string | boolean | ((e: Event) => void)>;

function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Props = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (typeof value === "function") el.addEventListener(key.replace(/^on/, ""), value);
    else if (typeof value === "boolean") el.toggleAttribute(key, value);
    else el.setAttribute(key, value);
  }
  for (const child of children) {
    if (child === null || child === false || child === undefined) continue;
    el.append(child);
  }
  return el;
}

const present = (child: Child): child is Node | string =>
  child !== null && child !== false && child !== undefined;

function errorText(e: unknown): string {
  if (e instanceof GitHubError) return e.message;
  if (e instanceof Error) return `エラー: ${e.message}`;
  return "不明なエラーが起きました";
}

interface InterestsDraft {
  /** 読み込んだときの config.yaml（まだなければ config.example.yaml）の内容 */
  content: string;
  branch: string;
  text: string;
  /** config.yaml がまだなく、config.example.yaml をもとに作る場合は false */
  exists: boolean;
}

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("#app が見つかりません");
const root: HTMLDivElement = app;

let settings: Settings | null = loadSettings();
let tab: "run" | "notes" | "interests" = "run";
// タブを切り替えても、保存前の編集内容と読み込んだときの config.yaml を保持する
let interestsDraft: InterestsDraft | null = null;
let preset: PresetId = "continue";
let custom: Period = { since: "", until: "" };
let notify = true;
// 画面を切り替えたら古いポーリングを止めるための世代番号
let generation = 0;

const client = (): Client => {
  if (!settings) throw new Error("設定がありません");
  return { token: settings.token, repo: settings.repo };
};

// ---- 設定画面 ----

function renderSetup(message?: string): void {
  generation++;
  const repoInput = h("input", {
    type: "text",
    inputmode: "url",
    autocapitalize: "off",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "owner/paper-feed",
    value: settings?.repo ?? "",
  });
  const tokenInput = h("input", {
    type: "password",
    autocomplete: "off",
    placeholder: "github_pat_...",
    value: settings?.token ?? "",
  });
  const status = h("p", { class: "status" }, message);
  const submit = h("button", { class: "primary", type: "submit" }, "保存");

  const form = h(
    "form",
    {
      class: "card",
      onsubmit: (e) => {
        e.preventDefault();
        void save();
      },
    },
    h("label", {}, "リポジトリ", repoInput),
    h("label", {}, "トークン", tokenInput),
    submit,
    status,
  );

  async function save(): Promise<void> {
    const repo = repoInput.value.trim();
    const token = tokenInput.value.trim();
    if (!REPO_RE.test(repo)) {
      status.textContent = "リポジトリは owner/name の形で入力してください";
      return;
    }
    if (!token) {
      status.textContent = "トークンを入力してください";
      return;
    }
    submit.disabled = true;
    status.textContent = "確認中…";
    try {
      await checkWorkflow({ token, repo });
      settings = { token, repo };
      saveSettings(settings);
      interestsDraft = null;
      renderMain();
    } catch (e) {
      status.textContent = errorText(e);
      submit.disabled = false;
    }
  }

  root.replaceChildren(
    h("header", {}, h("h1", {}, "paper-feed")),
    h(
      "main",
      {},
      form,
      h(
        "section",
        { class: "card help" },
        h("h2", {}, "トークンの作り方"),
        h(
          "ol",
          {},
          h(
            "li",
            {},
            h("a", { href: TOKEN_URL, target: "_blank", rel: "noopener" }, "Fine-grained token の作成画面"),
            " を開く",
          ),
          h("li", {}, "Repository access で、自分の paper-feed リポジトリだけを選ぶ"),
          h("li", {}, "Permissions で Actions と Contents を Read and write にする"),
          h("li", {}, "作成したトークンを上に貼り付ける"),
        ),
        h("p", { class: "note" }, "トークンはこの端末のブラウザにだけ保存され、GitHub 以外には送信されません。"),
        h("p", { class: "note" }, "初めて使うときは、保存したあと「興味」タブで興味の説明文を書いて「保存して検索条件に反映」を押してください。"),
      ),
      settings && h("button", { class: "link", onclick: () => { renderMain(); } }, "キャンセル"),
      settings &&
        h(
          "button",
          {
            class: "link danger",
            onclick: () => {
              settings = null;
              interestsDraft = null;
              saveSettings(null);
              savePendingRun(null);
              renderSetup("ログアウトしました");
            },
          },
          "この端末からトークンを削除",
        ),
    ),
  );
}

// ---- メイン画面 ----

function renderMain(): void {
  generation++;
  if (!settings) {
    renderSetup();
    return;
  }
  const content = h("main", {});
  const tabButton = (id: typeof tab, label: string): HTMLButtonElement =>
    h("button", { class: tab === id ? "active" : "", onclick: () => { tab = id; renderMain(); } }, label);
  root.replaceChildren(
    h(
      "header",
      {},
      h("h1", {}, "paper-feed"),
      h("span", { class: "repo" }, settings.repo),
      h("button", { class: "icon", "aria-label": "設定", onclick: () => { renderSetup(); } }, "⚙"),
    ),
    h("nav", { class: "tabs" }, tabButton("run", "実行"), tabButton("notes", "ノート"), tabButton("interests", "興味")),
    content,
  );
  if (tab === "run") renderRunTab(content);
  else if (tab === "notes") void renderNotesTab(content);
  else void renderInterestsTab(content);
}

// ---- 実行タブ ----

function renderRunTab(container: HTMLElement): void {
  const statusArea = h("section", {});
  const resultArea = h("section", {});

  const chips = h(
    "div",
    { class: "chips" },
    ...PRESETS.map((p) =>
      h(
        "button",
        {
          class: p.id === preset ? "chip active" : "chip",
          onclick: () => {
            preset = p.id;
            renderMain();
          },
        },
        p.label,
      ),
    ),
  );

  const today = new Date();
  const fixed = presetPeriod(preset, today);
  // value 属性で入れると iOS の「リセット」がその値に戻すだけになるため、プロパティで設定する
  const sinceInput = h("input", { type: "date" });
  const untilInput = h("input", { type: "date" });
  sinceInput.value = custom.since;
  untilInput.value = custom.until;
  for (const event of ["input", "change"]) {
    sinceInput.addEventListener(event, () => { custom = { ...custom, since: sinceInput.value }; });
    untilInput.addEventListener(event, () => { custom = { ...custom, until: untilInput.value }; });
  }

  const periodView =
    preset === "custom"
      ? h(
          "div",
          { class: "dates" },
          h("label", {}, "開始日", sinceInput),
          h("label", {}, "終了日（空なら今日）", untilInput),
        )
      : h(
          "p",
          { class: "period" },
          fixed ? `${fixed.since} 〜 ${fixed.until}` : "前回の実行日から今日まで（週次と同じ動き）",
        );

  const notifyInput = h("input", { type: "checkbox", checked: notify });
  notifyInput.addEventListener("change", () => { notify = notifyInput.checked; });

  const runButton = h("button", { class: "primary" }, "実行する");
  runButton.addEventListener("click", () => {
    const period = preset === "custom" ? custom : (fixed ?? { since: "", until: "" });
    if (preset === "custom") {
      const error = validatePeriod(period, today);
      if (error) {
        statusArea.replaceChildren(h("p", { class: "status error" }, error));
        return;
      }
    }
    runButton.disabled = true;
    resultArea.replaceChildren();
    void startWorkflow(
      FEED_RUN,
      { since: period.since, until: period.until, notify: String(notify) },
      statusArea,
      () => showLatest(client(), resultArea),
    ).finally(() => { runButton.disabled = false; });
  });

  container.replaceChildren(
    h(
      "section",
      { class: "card" },
      h("h2", {}, "期間"),
      chips,
      periodView,
      h("label", { class: "toggle" }, notifyInput, "Discord に通知する"),
      runButton,
    ),
    statusArea,
    resultArea,
  );

  const pending = loadPendingRun();
  if (pending?.kind === "feed" && pending.repo === settings?.repo) {
    runButton.disabled = true;
    void watchRun(pending.runId, FEED_RUN, statusArea, () => showLatest(client(), resultArea)).finally(
      () => { runButton.disabled = false; },
    );
  }
}

async function showLatest(c: Client, resultArea: HTMLElement): Promise<void> {
  const branch = await getDefaultBranch(c);
  const latest: unknown = JSON.parse(await readFile(c, "state/latest.json", branch));
  const notePath =
    typeof latest === "object" && latest !== null && "note_path" in latest ? latest.note_path : null;
  if (typeof notePath !== "string") {
    resultArea.replaceChildren(h("p", { class: "card" }, "この期間の新着はありませんでした"));
    return;
  }
  await showNote(c, branch, notePath, resultArea);
}

// ---- ワークフローの起動と進み具合 ----

async function startWorkflow(
  spec: RunSpec,
  inputs: Record<string, string>,
  statusArea: HTMLElement,
  onSuccess: () => Promise<void>,
): Promise<void> {
  const c = client();
  statusArea.replaceChildren(progressCard("起動しています…", [], spec.labels));
  try {
    const branch = await getDefaultBranch(c);
    const startedAt = new Date();
    let runId = await dispatchWorkflow(c, spec.workflowFile, branch, inputs);
    // 実行 ID が返らない場合、GitHub に実行が現れるまで少し待って探す
    for (let i = 0; runId === null && i < 10; i++) {
      await sleep(3000);
      runId = await findRunSince(c, startedAt, spec.workflowFile);
    }
    if (runId === null) throw new Error("実行が見つかりませんでした。Actions の画面を確認してください");
    savePendingRun({ runId, repo: c.repo, kind: spec.kind });
    await watchRun(runId, spec, statusArea, onSuccess);
  } catch (e) {
    statusArea.replaceChildren(h("p", { class: "status error" }, errorText(e)));
  }
}

async function watchRun(
  runId: number,
  spec: RunSpec,
  statusArea: HTMLElement,
  onSuccess: () => Promise<void>,
): Promise<void> {
  const c = client();
  const gen = generation;
  const deadline = Date.now() + RUN_TIMEOUT_MS;
  try {
    for (;;) {
      const run = await getRun(c, runId);
      const steps = await getSteps(c, runId);
      if (gen !== generation) return;
      statusArea.replaceChildren(
        progressCard(runHeadline(run, steps, spec.labels), steps, spec.labels, run.htmlUrl),
      );
      if (run.status === "completed") {
        savePendingRun(null);
        if (run.conclusion === "success") await onSuccess();
        return;
      }
      if (Date.now() > deadline) throw new Error("時間がかかりすぎているため確認をやめました");
      await sleep(POLL_INTERVAL_MS);
      if (gen !== generation) return;
    }
  } catch (e) {
    if (gen === generation) statusArea.replaceChildren(h("p", { class: "status error" }, errorText(e)));
  }
}

function runHeadline(run: RunInfo, steps: Step[], labels: StepLabels): string {
  if (run.status === "completed") {
    return run.conclusion === "success" ? "完了しました" : `失敗しました（${run.conclusion ?? "不明"}）`;
  }
  if (run.status === "queued" || run.status === "waiting" || run.status === "pending") return "順番待ちです…";
  const current = steps.find((s) => s.status === "in_progress");
  return current ? `${labels[current.name] ?? "準備"}中…` : "準備中…";
}

function progressCard(headline: string, steps: Step[], labels: StepLabels, url?: string): HTMLElement {
  const items = Object.entries(labels).map(([name, label]) => {
    const step = steps.find((s) => s.name === name);
    let mark = "・";
    let cls = "";
    if (step?.status === "in_progress") { mark = "…"; cls = "doing"; }
    else if (step?.conclusion === "success") { mark = "✓"; cls = "done"; }
    else if (step?.conclusion === "skipped") { mark = "−"; cls = "skipped"; }
    else if (step?.conclusion === "failure") { mark = "✕"; cls = "failed"; }
    return h("li", { class: cls }, h("span", { class: "mark" }, mark), label);
  });
  return h(
    "div",
    { class: "card" },
    h("p", { class: "headline" }, headline),
    h("ul", { class: "steps" }, ...items),
    url && h("a", { href: url, target: "_blank", rel: "noopener", class: "small" }, "GitHub で実行ログを見る"),
  );
}

// ---- 興味タブ ----

async function loadInterestsDraft(c: Client): Promise<InterestsDraft> {
  const branch = await getDefaultBranch(c);
  try {
    const content = await readFile(c, CONFIG_PATH, branch);
    return { content, branch, text: getInterests(content), exists: true };
  } catch (e) {
    if (!(e instanceof GitHubError && e.status === 404)) throw e;
  }
  // まだ config.yaml がないリポジトリでは、queries ワークフローが config.example.yaml から作る
  const example = await readFile(c, EXAMPLE_PATH, branch);
  return { content: example, branch, text: "", exists: false };
}

async function loadReport(c: Client, branch: string): Promise<QueriesReport | null> {
  try {
    return parseQueriesReport(JSON.parse(await readFile(c, QUERIES_REPORT_PATH, branch)));
  } catch (e) {
    if (e instanceof GitHubError && e.status === 404) return null;
    throw e;
  }
}

async function renderInterestsTab(container: HTMLElement, message?: string): Promise<void> {
  const gen = generation;
  const c = client();
  if (!interestsDraft) {
    container.replaceChildren(h("p", { class: "status" }, "読み込み中…"));
    try {
      interestsDraft = await loadInterestsDraft(c);
    } catch (e) {
      if (gen === generation) container.replaceChildren(h("p", { class: "status error" }, errorText(e)));
      return;
    }
    if (gen !== generation) return;
  }
  const draft = interestsDraft;
  const original = draft.exists ? getInterests(draft.content) : "";

  const textarea = h("textarea", {
    rows: "8",
    spellcheck: "false",
    placeholder:
      "例: 発達期の視覚野で、経験によって神経回路がどう変わるか。特に抑制性の介在ニューロンの役割に関心がある。",
  });
  textarea.value = draft.text;
  const status = h("p", { class: message ? "status ok" : "status" }, message);
  const statusArea = h("section", {});
  const generate = h("button", { class: "primary" }, "保存して検索条件に反映");
  const queriesCard = h("section", { class: "card" }, h("p", { class: "status" }, "読み込み中…"));
  const historyCard = h("section", { class: "card" }, h("p", { class: "status" }, "読み込み中…"));

  const showDirty = (): void => {
    status.className = "status";
    status.textContent = draft.text !== original ? "保存していない変更があります" : "";
  };
  if (!message) showDirty();
  textarea.addEventListener("input", () => {
    draft.text = textarea.value;
    showDirty();
  });

  const setBusy = (busy: boolean): void => {
    generate.disabled = busy;
    for (const b of historyCard.querySelectorAll("button")) b.disabled = busy;
  };

  const afterQueries = async (done: string): Promise<void> => {
    interestsDraft = null;
    if (gen === generation) await renderInterestsTab(container, done);
  };

  generate.addEventListener("click", () => {
    if (!draft.text.trim()) {
      status.className = "status error";
      status.textContent = "興味の説明文を入力してください";
      return;
    }
    setBusy(true);
    void startWorkflow(QUERY_RUN, { interests: draft.text.trim(), restore: "" }, statusArea, () =>
      afterQueries("保存して、検索条件に反映しました"),
    ).finally(() => { setBusy(false); });
  });

  container.replaceChildren(
    h(
      "section",
      { class: "card" },
      h("h2", {}, "興味の説明文"),
      h(
        "p",
        { class: "period" },
        "何に関心があるかを文章で具体的に書いてください。論文データベースの検索条件はこの文章から自動で作られ、拾った論文の関連度もこの文章をもとに判定されます。",
      ),
      textarea,
      generate,
      status,
    ),
    statusArea,
    queriesCard,
    historyCard,
    h(
      "button",
      {
        class: "link",
        onclick: () => {
          interestsDraft = null;
          void renderInterestsTab(container);
        },
      },
      "GitHub から読み込み直す（編集中の内容は消えます）",
    ),
  );

  void (async () => {
    try {
      const report = draft.exists ? await loadReport(c, draft.branch) : null;
      if (gen === generation) queriesCard.replaceChildren(...queriesView(draft, report).filter(present));
    } catch (e) {
      if (gen === generation) queriesCard.replaceChildren(h("p", { class: "status error" }, errorText(e)));
    }
  })();

  void (async () => {
    try {
      const commits = draft.exists ? await listCommits(c, CONFIG_PATH, draft.branch) : [];
      if (gen !== generation) return;
      historyCard.replaceChildren(
        ...historyView(commits, (commit) => {
          const label = `${formatDateTime(commit.date)}「${describeConfigCommit(commit.message)}」`;
          if (!window.confirm(`${label}の時点の興味と検索条件に戻しますか？`)) return;
          setBusy(true);
          void startWorkflow(QUERY_RUN, { interests: "", restore: commit.sha }, statusArea, () =>
            afterQueries("元に戻しました"),
          ).finally(() => { setBusy(false); });
        }).filter(present),
      );
    } catch (e) {
      if (gen === generation) historyCard.replaceChildren(h("p", { class: "status error" }, errorText(e)));
    }
  })();

  const pending = loadPendingRun();
  if (pending?.kind === "queries" && pending.repo === settings?.repo) {
    setBusy(true);
    void watchRun(pending.runId, QUERY_RUN, statusArea, () => afterQueries("完了しました")).finally(
      () => { setBusy(false); },
    );
  }
}

function queriesView(draft: InterestsDraft, report: QueriesReport | null): Child[] {
  const heading = h("h2", {}, "検索条件");
  if (!draft.exists) {
    return [
      heading,
      h("p", { class: "status" }, "まだありません。興味の説明文を書いて「保存して検索条件に反映」を押してください。"),
    ];
  }
  if (!report) {
    return [
      heading,
      h("p", { class: "status" }, "件数はまだ調べていません。「保存して検索条件に反映」で作り直すと表示されます。"),
      ...Object.entries(getSources(draft.content)).flatMap(([name, queries]) => [
        h("h3", {}, SOURCE_NAMES[name] ?? name),
        h("ul", { class: "queries" }, ...queries.map((q) => h("li", {}, h("code", {}, q)))),
      ]),
    ];
  }
  const range = `${String(report.targetMin)}〜${String(report.targetMax)}`;
  return [
    heading,
    h("p", { class: "period" }, `直近7日（${report.since}〜${report.until}）で合計 ${String(report.total)} 件（目安 ${range} 件）`),
    report.restoredFrom && h("p", { class: "period" }, `${report.restoredFrom.slice(0, 7)} の時点に戻した設定です`),
    !report.withinTarget &&
      h("p", { class: "status error" }, "目安の件数から外れています。興味の説明文を具体的にしてから作り直すと改善することがあります。"),
    ...report.sources.flatMap((s) => [
      h("h3", {}, SOURCE_NAMES[s.name] ?? s.name),
      ...s.queries.map((q) =>
        h(
          "details",
          { class: "query" },
          h(
            "summary",
            {},
            h("span", { class: "count" }, q.error ? "エラー" : `${String(q.count)} 件`),
            h("code", {}, q.query),
          ),
          q.error ? h("p", { class: "status error" }, q.error) : h("ul", {}, ...q.titles.map((t) => h("li", {}, t))),
        ),
      ),
    ]),
  ];
}

function historyView(commits: Commit[], onRestore: (commit: Commit) => void): Child[] {
  const heading = h("h2", {}, "変更履歴");
  if (commits.length === 0) return [heading, h("p", { class: "status" }, "まだありません")];
  return [
    heading,
    h(
      "ul",
      { class: "history" },
      ...commits.map((commit, i) =>
        h(
          "li",
          {},
          h("span", { class: "when" }, formatDateTime(commit.date)),
          h("span", { class: "what" }, describeConfigCommit(commit.message)),
          i === 0
            ? h("span", { class: "current" }, "現在")
            : h("button", { class: "chip", onclick: () => { onRestore(commit); } }, "この時点に戻す"),
        ),
      ),
    ),
  ];
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${String(d.getFullYear())}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ---- ノートタブ ----

async function showNote(c: Client, branch: string, path: string, area: HTMLElement): Promise<void> {
  const { body } = parseNote(await readFile(c, path, branch));
  const article = h("article", { class: "note" });
  article.innerHTML = renderNoteHtml(body);
  const url = `https://github.com/${c.repo}/blob/${encodeURIComponent(branch)}/${path}`;
  area.replaceChildren(
    article,
    h("a", { href: url, target: "_blank", rel: "noopener", class: "small" }, "GitHub で開く"),
  );
}

async function renderNotesTab(container: HTMLElement): Promise<void> {
  const gen = generation;
  container.replaceChildren(h("p", { class: "status" }, "読み込み中…"));
  try {
    const c = client();
    const branch = await getDefaultBranch(c);
    const notes = sortNotes(await listNotes(c, branch));
    if (gen !== generation) return;
    if (notes.length === 0) {
      container.replaceChildren(h("p", { class: "card" }, "まだノートがありません"));
      return;
    }
    const list = h(
      "ul",
      { class: "note-list" },
      ...notes.map((n) =>
        h(
          "li",
          {},
          h(
            "button",
            {
              onclick: () => {
                const view = h("section", {}, h("p", { class: "status" }, "読み込み中…"));
                container.replaceChildren(
                  h("button", { class: "link", onclick: () => { void renderNotesTab(container); } }, "← 一覧に戻る"),
                  view,
                );
                showNote(c, branch, n.path, view).catch((e: unknown) => {
                  view.replaceChildren(h("p", { class: "status error" }, errorText(e)));
                });
              },
            },
            n.label,
          ),
        ),
      ),
    );
    container.replaceChildren(list);
  } catch (e) {
    if (gen === generation) container.replaceChildren(h("p", { class: "status error" }, errorText(e)));
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

if (settings) renderMain();
else renderSetup();
