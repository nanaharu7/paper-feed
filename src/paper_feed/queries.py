"""興味の説明文から、取得元ごとの検索クエリを LLM に作らせ、実際の件数で調整する。"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from importlib import resources
from typing import Any

import requests

from paper_feed.sources import PROBERS, ProbeFn

log = logging.getLogger(__name__)

TARGET_MIN = 20
TARGET_MAX = 150
MAX_ROUNDS = 3
WINDOW_DAYS = 7
SAMPLE_TITLES = 8
MAX_QUERIES_PER_SOURCE = 4

SOURCE_GUIDES = {
    "europepmc": """### europepmc（Europe PMC: PubMed・PMC・bioRxiv・medRxiv など。生命科学・医学）
- 必ず全体を TITLE_ABS:( ... ) で囲み、タイトルと要旨だけを検索する
  （付けないと本文まで検索して、関係ない論文が大量に混ざる）
- AND / OR / NOT は大文字。フレーズは "..."。括弧でまとめられる
- 例: TITLE_ABS:(("ocular dominance" OR "monocular deprivation") AND (cortex OR cortical))""",
    "arxiv": """### arxiv（arXiv: 情報・物理・数学・定量生物学などのプレプリント）
- ti: タイトル、abs: 要旨、cat: カテゴリ（例: cat:q-bio.NC, cat:cs.LG）を使う
- AND / OR / ANDNOT は大文字。フレーズは "..."。括弧でまとめられる
- 例: (abs:"ocular dominance" OR abs:"critical period") AND cat:q-bio.NC""",
    "openalex": """### openalex（OpenAlex: 全分野。人文・社会科学も含む）
- キーワードと "フレーズ" を AND / OR / NOT（大文字）でつなぐ。タイトル・要旨・本文が対象
- 例: "language acquisition" AND (bilingual OR multilingual)""",
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": list(SOURCE_GUIDES)},
                    "queries": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "queries"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sources"],
    "additionalProperties": False,
}

Backend = Callable[[str], object]


class QueryGenerationError(RuntimeError):
    pass


@dataclass
class QueryResult:
    query: str
    count: int = 0
    titles: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SourceResult:
    name: str
    queries: list[QueryResult]


@dataclass
class Report:
    """クエリの作成・復元の結果。アプリでの表示用に state/queries.json に保存する。"""

    created: str
    since: str
    until: str
    target_min: int
    target_max: int
    rounds: int
    sources: list[SourceResult]
    restored_from: str | None = None

    @property
    def total(self) -> int:
        return sum(q.count for s in self.sources for q in s.queries)

    @property
    def has_error(self) -> bool:
        return any(q.error for s in self.sources for q in s.queries)

    @property
    def within_target(self) -> bool:
        return self.target_min <= self.total <= self.target_max

    def usable_sources(self) -> dict[str, list[str]]:
        """エラーになったクエリを除き、クエリが1本以上ある取得元だけを返す。"""
        usable = {s.name: [q.query for q in s.queries if not q.error] for s in self.sources}
        return {name: qs for name, qs in usable.items() if qs}

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(total=self.total, within_target=self.within_target)
        return data


def available_sources(env: Mapping[str, str] = os.environ) -> list[str]:
    # OpenAlex はキーなしだと同じ IP の利用者全体で小さな枠を共有するため、Actions では使えない
    names = ["europepmc", "arxiv"]
    if env.get("OPENALEX_API_KEY"):
        names.append("openalex")
    return names


def _history_text(history: list[Report]) -> str:
    if not history:
        return ""
    parts = ["## これまでの試行（目標に合わなかったので作り直す）"]
    for i, report in enumerate(history, 1):
        parts.append(f"### 試行 {i}: 合計 {report.total} 件")
        for s in report.sources:
            for q in s.queries:
                status = f"エラー: {q.error}" if q.error else f"{q.count} 件"
                parts.append(f"- {s.name}: `{q.query}` → {status}")
                parts += [f"  - {t}" for t in q.titles[:5]]
    parts.append(
        "件数が目標より少なければ条件をゆるめ、多ければ絞る。拾った論文のタイトルが興味と"
        "ずれていれば、キーワードを見直す。"
    )
    return "\n".join(parts)


def build_prompt(interests: str, sources: list[str], history: list[Report]) -> str:
    template = (
        resources.files("paper_feed").joinpath("queries_prompt.md").read_text(encoding="utf-8")
    )
    return template.format(
        interests=interests,
        source_guides="\n\n".join(SOURCE_GUIDES[name] for name in sources),
        target_min=TARGET_MIN,
        target_max=TARGET_MAX,
        history=_history_text(history),
    )


def parse_proposal(output: object, allowed: list[str]) -> dict[str, list[str]]:
    if not isinstance(output, dict) or not isinstance(output.get("sources"), list):
        raise QueryGenerationError(f"想定外の出力形式です: {str(output)[:200]}")
    proposal: dict[str, list[str]] = {}
    for item in output["sources"]:
        if not isinstance(item, dict) or item.get("name") not in allowed:
            continue
        name = str(item["name"])
        queries = item.get("queries")
        if not isinstance(queries, list):
            continue
        cleaned = list(
            dict.fromkeys(q.strip() for q in queries if isinstance(q, str) and q.strip())
        )
        proposal[name] = proposal.get(name, []) + cleaned
    proposal = {k: v[:MAX_QUERIES_PER_SOURCE] for k, v in proposal.items() if v}
    if not proposal:
        raise QueryGenerationError("クエリが1本も作られませんでした")
    return proposal


def probe_sources(
    session: requests.Session,
    sources: dict[str, list[str]],
    since: date,
    until: date,
    probers: Mapping[str, ProbeFn] = PROBERS,
) -> list[SourceResult]:
    results: list[SourceResult] = []
    for name, queries in sources.items():
        items: list[QueryResult] = []
        for query in queries:
            try:
                probe = probers[name](session, query, since, until, SAMPLE_TITLES)
            except requests.RequestException as e:
                # 書式の誤りなどで検索できなかったクエリは、作り直しのときに LLM に伝える
                log.warning("%s の検索に失敗しました: %s (%s)", name, query, e)
                items.append(QueryResult(query, error=str(e)[:200]))
                continue
            log.info("%s: %d 件 (%s)", name, probe.count, query)
            items.append(QueryResult(query, probe.count, list(probe.titles)))
        results.append(SourceResult(name, items))
    return results


def _distance(report: Report) -> int:
    """目標の件数範囲からどれだけ外れているか（エラーがあれば大きく不利にする）"""
    gap = max(report.target_min - report.total, report.total - report.target_max, 0)
    return gap + (10_000 if report.has_error else 0)


def generate(
    interests: str,
    backend: Backend,
    session: requests.Session,
    today: date,
    sources: list[str],
    probers: Mapping[str, ProbeFn] = PROBERS,
) -> Report:
    """クエリを作り、件数が目標に入るまで最大 MAX_ROUNDS 回作り直す。

    最後まで目標に入らなかった場合は、最も目標に近かった回の結果を返す。
    """
    since, until = today - timedelta(days=WINDOW_DAYS), today
    history: list[Report] = []
    best: Report | None = None
    for round_no in range(1, MAX_ROUNDS + 1):
        log.info("クエリを作成中（%d 回目）", round_no)
        proposal = parse_proposal(backend(build_prompt(interests, sources, history)), sources)
        report = Report(
            created=today.isoformat(),
            since=since.isoformat(),
            until=until.isoformat(),
            target_min=TARGET_MIN,
            target_max=TARGET_MAX,
            rounds=round_no,
            sources=probe_sources(session, proposal, since, until, probers),
        )
        log.info("合計 %d 件（目標 %d〜%d 件）", report.total, TARGET_MIN, TARGET_MAX)
        if best is None or _distance(report) < _distance(best):
            best = report
        if report.within_target and not report.has_error:
            break
        history.append(report)
    assert best is not None
    best.rounds = round_no
    if not best.usable_sources():
        raise QueryGenerationError("使えるクエリを作れませんでした")
    return best


def probe_report(
    session: requests.Session,
    sources: dict[str, list[str]],
    today: date,
    restored_from: str | None = None,
    probers: Mapping[str, ProbeFn] = PROBERS,
) -> Report:
    """LLM を使わず、今のクエリの件数だけを調べる（復元したときの表示用）。"""
    since, until = today - timedelta(days=WINDOW_DAYS), today
    return Report(
        created=today.isoformat(),
        since=since.isoformat(),
        until=until.isoformat(),
        target_min=TARGET_MIN,
        target_max=TARGET_MAX,
        rounds=0,
        sources=probe_sources(session, sources, since, until, probers),
        restored_from=restored_from,
    )
