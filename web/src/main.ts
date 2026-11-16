import "./style.css";

import {
  type Client,
  GitHubError,
  type RunInfo,
  type Step,
  checkWorkflow,
  dispatch,
  findRunSince,
  getDefaultBranch,
  getRun,
  getSteps,
  listNotes,
  readFile,
} from "./github";
import { parseNote, renderNoteHtml, sortNotes } from "./note";
import { PRESETS, type Period, type PresetId, presetPeriod, validatePeriod } from "./period";
import {
  REPO_RE,
  type Settings,
  loadPendingRun,
  loadSettings,
  savePendingRun,
  saveSettings,
} from "./storage";

const POLL_INTERVAL_MS = 4000;
const RUN_TIMEOUT_MS = 30 * 60 * 1000;
const TOKEN_URL = "https://github.com/settings/personal-access-tokens/new";

const STEP_LABELS: Record<string, string> = {
  "Fetch and summarize": "論文の取得と要約",
  Commit: "ノートの保存",
  "Notify Discord": "Discord への通知",
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

function errorText(e: unknown): string {
  if (e instanceof GitHubError) return e.message;
  if (e instanceof Error) return `エラー: ${e.message}`;
  return "不明なエラーが起きました";
}

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("#app が見つかりません");
const root: HTMLDivElement = app;

let settings: Settings | null = loadSettings();
let tab: "run" | "notes" = "run";
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
          h("li", {}, "Permissions で Actions を Read and write、Contents を Read-only にする"),
          h("li", {}, "作成したトークンを上に貼り付ける"),
        ),
        h("p", { class: "note" }, "トークンはこの端末のブラウザにだけ保存され、GitHub 以外には送信されません。"),
      ),
      settings && h("button", { class: "link", onclick: () => { renderMain(); } }, "キャンセル"),
      settings &&
        h(
          "button",
          {
            class: "link danger",
            onclick: () => {
              settings = null;
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
  root.replaceChildren(
    h(
      "header",
      {},
      h("h1", {}, "paper-feed"),
      h("span", { class: "repo" }, settings.repo),
      h("button", { class: "icon", "aria-label": "設定", onclick: () => { renderSetup(); } }, "⚙"),
    ),
    h(
      "nav",
      { class: "tabs" },
      h("button", { class: tab === "run" ? "active" : "", onclick: () => { tab = "run"; renderMain(); } }, "実行"),
      h("button", { class: tab === "notes" ? "active" : "", onclick: () => { tab = "notes"; renderMain(); } }, "ノート"),
    ),
    content,
  );
  if (tab === "run") renderRunTab(content);
  else void renderNotesTab(content);
}

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
    void startRun(period, statusArea, resultArea).finally(() => { runButton.disabled = false; });
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
  if (pending && pending.repo === settings?.repo) {
    runButton.disabled = true;
    void watchRun(pending.runId, statusArea, resultArea).finally(() => { runButton.disabled = false; });
  }
}

async function startRun(period: Period, statusArea: HTMLElement, resultArea: HTMLElement): Promise<void> {
  const c = client();
  resultArea.replaceChildren();
  statusArea.replaceChildren(progressCard("起動しています…", []));
  try {
    const branch = await getDefaultBranch(c);
    const startedAt = new Date();
    let runId = await dispatch(c, branch, { ...period, notify });
    // 実行 ID が返らない場合、GitHub に実行が現れるまで少し待って探す
    for (let i = 0; runId === null && i < 10; i++) {
      await sleep(3000);
      runId = await findRunSince(c, startedAt);
    }
    if (runId === null) throw new Error("実行が見つかりませんでした。Actions の画面を確認してください");
    savePendingRun({ runId, repo: c.repo });
    await watchRun(runId, statusArea, resultArea);
  } catch (e) {
    statusArea.replaceChildren(h("p", { class: "status error" }, errorText(e)));
  }
}

async function watchRun(runId: number, statusArea: HTMLElement, resultArea: HTMLElement): Promise<void> {
  const c = client();
  const gen = generation;
  const deadline = Date.now() + RUN_TIMEOUT_MS;
  try {
    for (;;) {
      const run = await getRun(c, runId);
      const steps = await getSteps(c, runId);
      if (gen !== generation) return;
      statusArea.replaceChildren(progressCard(runHeadline(run, steps), steps, run.htmlUrl));
      if (run.status === "completed") {
        savePendingRun(null);
        if (run.conclusion === "success") await showLatest(c, resultArea);
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

function runHeadline(run: RunInfo, steps: Step[]): string {
  if (run.status === "completed") {
    return run.conclusion === "success" ? "完了しました" : `失敗しました（${run.conclusion ?? "不明"}）`;
  }
  if (run.status === "queued" || run.status === "waiting" || run.status === "pending") return "順番待ちです…";
  const current = steps.find((s) => s.status === "in_progress");
  return current ? `${STEP_LABELS[current.name] ?? "準備"}中…` : "準備中…";
}

function progressCard(headline: string, steps: Step[], url?: string): HTMLElement {
  const items = Object.entries(STEP_LABELS).map(([name, label]) => {
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
