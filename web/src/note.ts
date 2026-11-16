import DOMPurify from "dompurify";
import { marked } from "marked";

import type { NoteFile } from "./github";

export interface ParsedNote {
  meta: Record<string, string>;
  body: string;
}

const FRONTMATTER_RE = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;

export function parseNote(markdown: string): ParsedNote {
  const match = FRONTMATTER_RE.exec(markdown);
  if (!match) return { meta: {}, body: markdown };
  const meta: Record<string, string> = {};
  for (const line of (match[1] ?? "").split(/\r?\n/)) {
    const i = line.indexOf(":");
    if (i <= 0) continue;
    meta[line.slice(0, i).trim()] = line
      .slice(i + 1)
      .trim()
      .replace(/^"(.*)"$/, "$1");
  }
  return { meta, body: markdown.slice(match[0].length) };
}

let hookInstalled = false;

function installLinkHook(): void {
  if (hookInstalled) return;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node instanceof HTMLAnchorElement) {
      node.target = "_blank";
      node.rel = "noopener noreferrer";
    }
  });
  hookInstalled = true;
}

/** ノートの Markdown を、無害化した HTML にする（論文タイトルや要約は外部由来のため） */
export function renderNoteHtml(body: string): string {
  installLinkHook();
  const html = marked.parse(body, { async: false, gfm: true });
  return DOMPurify.sanitize(html);
}

/** ISO 週（例: 2026, 46）の月曜日 */
export function isoWeekMonday(year: number, week: number): Date {
  // 1月4日は必ず第1週に含まれる
  const jan4 = new Date(year, 0, 4);
  const week1Monday = new Date(year, 0, 4 - ((jan4.getDay() + 6) % 7));
  return new Date(week1Monday.getFullYear(), week1Monday.getMonth(), week1Monday.getDate() + (week - 1) * 7);
}

const md = (d: Date): string => `${String(d.getMonth() + 1)}/${String(d.getDate())}`;
const parseYmd = (s: string): Date => {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y ?? 0, (m ?? 1) - 1, d ?? 1);
};

export interface NoteEntry extends NoteFile {
  label: string;
  /** 並べ替え用（まとめた期間の最終日、YYYY-MM-DD） */
  sortKey: string;
}

const WEEK_RE = /^(\d{4})-W(\d{2})(-\d+)?\.md$/;
const RANGE_RE = /^(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})(-\d+)?\.md$/;

function ymd(d: Date): string {
  return `${String(d.getFullYear())}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function describeNote(file: NoteFile): NoteEntry {
  const week = WEEK_RE.exec(file.name);
  if (week) {
    const monday = isoWeekMonday(Number(week[1]), Number(week[2]));
    const sunday = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 6);
    return {
      ...file,
      label: `${week[1] ?? ""}年 第${String(Number(week[2]))}週（${md(monday)}〜${md(sunday)}）`,
      sortKey: ymd(sunday),
    };
  }
  const range = RANGE_RE.exec(file.name);
  if (range) {
    const since = parseYmd(range[1] ?? "");
    const until = parseYmd(range[2] ?? "");
    return {
      ...file,
      label: `${md(since)}〜${md(until)}（期間指定）`,
      sortKey: range[2] ?? "",
    };
  }
  return { ...file, label: file.name.replace(/\.md$/, ""), sortKey: file.name };
}

export function sortNotes(files: NoteFile[]): NoteEntry[] {
  return files
    .map(describeNote)
    .sort((a, b) => b.sortKey.localeCompare(a.sortKey) || b.name.localeCompare(a.name));
}
