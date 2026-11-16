import { parseDocument } from "yaml";

export const CONFIG_PATH = "config.yaml";
export const EXAMPLE_PATH = "config.example.yaml";

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

function parse(text: string): ReturnType<typeof parseDocument> {
  const doc = parseDocument(text);
  const first = doc.errors[0];
  if (first) throw new ConfigError(`config.yaml を読めません: ${first.message}`);
  return doc;
}

export function getInterests(text: string): string {
  const value: unknown = parse(text).get("interests");
  return typeof value === "string" ? value.replace(/\n+$/, "") : "";
}

/** 取得元ごとの検索クエリ */
export function getSources(text: string): Record<string, string[]> {
  const sources: unknown = parse(text).toJS();
  const raw = typeof sources === "object" && sources !== null && "sources" in sources ? sources.sources : null;
  const result: Record<string, string[]> = {};
  if (typeof raw !== "object" || raw === null) return result;
  for (const [name, body] of Object.entries(raw as Record<string, unknown>)) {
    const queries: unknown = typeof body === "object" && body !== null && "queries" in body ? body.queries : null;
    if (Array.isArray(queries)) result[name] = queries.filter((q): q is string => typeof q === "string");
  }
  return result;
}
