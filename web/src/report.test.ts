import { describe, expect, it } from "vitest";

import { describeConfigCommit, parseQueriesReport } from "./report";

describe("parseQueriesReport", () => {
  it("paper-feed queries の出力を読む", () => {
    const report = parseQueriesReport({
      created: "2026-11-16",
      since: "2026-11-09",
      until: "2026-11-16",
      target_min: 20,
      target_max: 150,
      rounds: 2,
      restored_from: null,
      total: 38,
      within_target: true,
      sources: [
        {
          name: "europepmc",
          queries: [
            { query: "q1", count: 30, titles: ["t1", 2], error: null },
            { query: "q2", count: 0, titles: [], error: "400 Bad Request" },
          ],
        },
      ],
    });
    expect(report).toEqual({
      since: "2026-11-09",
      until: "2026-11-16",
      total: 38,
      targetMin: 20,
      targetMax: 150,
      withinTarget: true,
      restoredFrom: null,
      sources: [
        {
          name: "europepmc",
          queries: [
            { query: "q1", count: 30, titles: ["t1"], error: null },
            { query: "q2", count: 0, titles: [], error: "400 Bad Request" },
          ],
        },
      ],
    });
  });

  it("形が違えば null", () => {
    expect(parseQueriesReport(null)).toBeNull();
    expect(parseQueriesReport({ total: 1 })).toBeNull();
  });
});

it("describeConfigCommit は種類の接頭辞を外す", () => {
  expect(describeConfigCommit("chore(config): 検索条件を作り直す")).toBe("検索条件を作り直す");
  expect(describeConfigCommit("feat: 神経科学向けの設定を追加する")).toBe(
    "feat: 神経科学向けの設定を追加する",
  );
});
