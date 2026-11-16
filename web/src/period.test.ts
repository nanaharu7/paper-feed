import { describe, expect, it } from "vitest";

import { formatDate, presetPeriod, validatePeriod } from "./period";

// 2026-11-18（水）
const WED = new Date(2026, 10, 18);

describe("presetPeriod", () => {
  it("今週は月曜から今日まで", () => {
    expect(presetPeriod("thisWeek", WED)).toEqual({ since: "2026-11-16", until: "2026-11-18" });
  });

  it("先週は前の週の月曜から日曜まで", () => {
    expect(presetPeriod("lastWeek", WED)).toEqual({ since: "2026-11-09", until: "2026-11-15" });
    // 日曜に実行しても、その日を含む週の前の週になる
    expect(presetPeriod("lastWeek", new Date(2026, 10, 22))).toEqual({
      since: "2026-11-09",
      until: "2026-11-15",
    });
  });

  it("先月は前月の1日から末日まで（年をまたぐ場合も）", () => {
    expect(presetPeriod("lastMonth", WED)).toEqual({ since: "2026-10-01", until: "2026-10-31" });
    expect(presetPeriod("lastMonth", new Date(2027, 0, 5))).toEqual({
      since: "2026-12-01",
      until: "2026-12-31",
    });
  });

  it("前回の続きと期間指定は期間を決めない", () => {
    expect(presetPeriod("continue", WED)).toBeNull();
    expect(presetPeriod("custom", WED)).toBeNull();
  });
});

describe("validatePeriod", () => {
  it.each([
    [{ since: "", until: "" }, "開始日を入力してください"],
    [{ since: "2026-11-10", until: "2026/11/12" }, "終了日の形式が正しくありません"],
    [{ since: "2026-11-12", until: "2026-11-10" }, "開始日は終了日以前にしてください"],
    [{ since: "2026-11-19", until: "" }, "開始日は終了日以前にしてください"],
  ])("%o はエラー", (period, message) => {
    expect(validatePeriod(period, WED)).toBe(message);
  });

  it("正しい期間は null", () => {
    expect(validatePeriod({ since: "2026-10-01", until: "" }, WED)).toBeNull();
  });
});

it("formatDate はローカル時刻で書式化する", () => {
  expect(formatDate(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
});
