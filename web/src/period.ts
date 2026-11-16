export type PresetId = "continue" | "thisWeek" | "lastWeek" | "lastMonth" | "custom";

export interface Period {
  since: string;
  until: string;
}

export const PRESETS: readonly { id: PresetId; label: string }[] = [
  { id: "continue", label: "前回の続き" },
  { id: "thisWeek", label: "今週" },
  { id: "lastWeek", label: "先週" },
  { id: "lastMonth", label: "先月" },
  { id: "custom", label: "期間を指定" },
];

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** ローカル時刻の日付を YYYY-MM-DD にする（toISOString は UTC になるため使わない） */
export function formatDate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${String(d.getFullYear())}-${m}-${day}`;
}

function addDays(d: Date, days: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
}

function mondayOf(d: Date): Date {
  // getDay(): 日曜=0 なので、月曜からの経過日数は (getDay() + 6) % 7
  return addDays(d, -((d.getDay() + 6) % 7));
}

/** プリセットの期間を返す。"continue" と "custom" は期間を決めないので null */
export function presetPeriod(id: PresetId, today: Date): Period | null {
  switch (id) {
    case "thisWeek":
      return { since: formatDate(mondayOf(today)), until: formatDate(today) };
    case "lastWeek": {
      const monday = addDays(mondayOf(today), -7);
      return { since: formatDate(monday), until: formatDate(addDays(monday, 6)) };
    }
    case "lastMonth": {
      const first = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      const last = new Date(today.getFullYear(), today.getMonth(), 0);
      return { since: formatDate(first), until: formatDate(last) };
    }
    case "continue":
    case "custom":
      return null;
  }
}

/** 期間指定の入力を検証し、問題があればエラーメッセージを返す */
export function validatePeriod(period: Period, today: Date): string | null {
  if (!DATE_RE.test(period.since)) return "開始日を入力してください";
  if (period.until && !DATE_RE.test(period.until)) return "終了日の形式が正しくありません";
  const until = period.until || formatDate(today);
  if (period.since > until) return "開始日は終了日以前にしてください";
  return null;
}
