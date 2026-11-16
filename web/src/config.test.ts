import { describe, expect, it } from "vitest";

import { getInterests, getSources } from "./config";

const CONFIG = [
  "# 興味の説明文",
  "interests: |",
  "  眼優位可塑性。",
  "  nNOS ニューロン。",
  "",
  "sources:",
  "  europepmc:",
  "    queries:",
  "      - 'TITLE_ABS:(\"ocular dominance\")'",
  "",
  "summarizer:",
  "  mode: claude-code  # サブスクで要約",
  "",
].join("\n");

describe("getInterests", () => {
  it("末尾の改行を除いて返す", () => {
    expect(getInterests(CONFIG)).toBe("眼優位可塑性。\nnNOS ニューロン。");
  });

  it("interests がなければ空文字", () => {
    expect(getInterests("sources: {}\n")).toBe("");
  });
});

it("getSources は取得元ごとのクエリを返す", () => {
  expect(getSources(CONFIG)).toEqual({ europepmc: ['TITLE_ABS:("ocular dominance")'] });
  expect(getSources("interests: a\n")).toEqual({});
});

it("壊れた YAML は読めないと伝える", () => {
  expect(() => getInterests("interests: [\n")).toThrow(/config.yaml を読めません/);
});
