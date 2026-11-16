import { describe, expect, it } from "vitest";

import { describeNote, isoWeekMonday, parseNote, renderNoteHtml, sortNotes } from "./note";

const NOTE = [
  "---",
  "date: 2026-11-16",
  'period: "2026-11-09 〜 2026-11-16"',
  "total: 4",
  "---",
  "",
  "# 新着論文",
  "",
  "### [Title](https://doi.org/10.1/x)",
  "",
].join("\n");

describe("parseNote", () => {
  it("frontmatter と本文を分ける", () => {
    const { meta, body } = parseNote(NOTE);
    expect(meta).toEqual({ date: "2026-11-16", period: "2026-11-09 〜 2026-11-16", total: "4" });
    expect(body.startsWith("\n# 新着論文")).toBe(true);
  });

  it("frontmatter がなければ全体が本文", () => {
    expect(parseNote("# a")).toEqual({ meta: {}, body: "# a" });
  });
});

describe("renderNoteHtml", () => {
  it("スクリプトや危険な属性を取り除く", () => {
    const html = renderNoteHtml(
      '<script>alert(1)</script><img src=x onerror="alert(1)">\n\n[x](javascript:alert(1))',
    );
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).toContain("<a");
    expect(html).not.toContain('href="javascript:');
  });

  it("リンクは新しいタブで開く", () => {
    const html = renderNoteHtml("[t](https://example.com)");
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });
});

describe("ノート一覧", () => {
  it("ISO 週の月曜を求める", () => {
    expect(isoWeekMonday(2026, 46)).toEqual(new Date(2026, 10, 9));
    expect(isoWeekMonday(2026, 53)).toEqual(new Date(2026, 11, 28));
    expect(isoWeekMonday(2027, 1)).toEqual(new Date(2027, 0, 4));
  });

  it("ファイル名から表示名を作る", () => {
    expect(describeNote({ name: "2026-W46.md", path: "papers/2026-W46.md" }).label).toBe(
      "2026年 第46週（11/9〜11/15）",
    );
    expect(describeNote({ name: "2026-10-01_2026-10-31.md", path: "p" }).label).toBe(
      "10/1〜10/31（期間指定）",
    );
    expect(describeNote({ name: "memo.md", path: "p" }).label).toBe("memo");
  });

  it("まとめた期間の新しい順に並べる", () => {
    const names = sortNotes([
      { name: "2026-10-01_2026-10-31.md", path: "a" },
      { name: "2026-W46.md", path: "b" },
      { name: "2026-W43.md", path: "c" },
    ]).map((n) => n.name);
    // 第43週は 10/19〜10/25 なので、10/31 で終わる期間指定より古い
    expect(names).toEqual(["2026-W46.md", "2026-10-01_2026-10-31.md", "2026-W43.md"]);
  });
});
