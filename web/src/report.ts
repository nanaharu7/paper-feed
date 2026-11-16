export const QUERIES_REPORT_PATH = "state/queries.json";

export const SOURCE_NAMES: Record<string, string> = {
  europepmc: "Europe PMC",
  arxiv: "arXiv",
  openalex: "OpenAlex",
};

export interface QueryResult {
  query: string;
  count: number;
  titles: string[];
  error: string | null;
}

export interface QueriesReport {
  since: string;
  until: string;
  total: number;
  targetMin: number;
  targetMax: number;
  withinTarget: boolean;
  restoredFrom: string | null;
  sources: { name: string; queries: QueryResult[] }[];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

const asString = (v: unknown): string => (typeof v === "string" ? v : "");
const asNumber = (v: unknown): number => (typeof v === "number" ? v : 0);

/** paper-feed queries が書き出す state/queries.json を読む。形が違えば null */
export function parseQueriesReport(data: unknown): QueriesReport | null {
  if (!isRecord(data) || !Array.isArray(data.sources)) return null;
  return {
    since: asString(data.since),
    until: asString(data.until),
    total: asNumber(data.total),
    targetMin: asNumber(data.target_min),
    targetMax: asNumber(data.target_max),
    withinTarget: data.within_target === true,
    restoredFrom: typeof data.restored_from === "string" ? data.restored_from : null,
    sources: data.sources.filter(isRecord).map((s) => ({
      name: asString(s.name),
      queries: (Array.isArray(s.queries) ? s.queries : []).filter(isRecord).map((q) => ({
        query: asString(q.query),
        count: asNumber(q.count),
        titles: Array.isArray(q.titles) ? q.titles.filter((t) => typeof t === "string") : [],
        error: typeof q.error === "string" ? q.error : null,
      })),
    })),
  };
}

/** 変更履歴に出す commit メッセージを読みやすくする */
export function describeConfigCommit(message: string): string {
  return message.replace(/^chore\(config\):\s*/, "");
}
