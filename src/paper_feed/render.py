"""評価結果を Markdown の週次ノートにする。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from paper_feed.config import OutputConfig
from paper_feed.models import Assessment, Paper


@dataclass(frozen=True)
class Entry:
    paper: Paper
    assessment: Assessment | None


@dataclass(frozen=True)
class Grouped:
    highlight: list[Entry]
    related: list[Entry]
    others: list[Entry]


def group(papers: list[Paper], assessments: list[Assessment] | None, cfg: OutputConfig) -> Grouped:
    by_id = {a.paper_id: a for a in assessments or []}
    entries = [Entry(p, by_id.get(p.id)) for p in papers]
    if assessments is None:
        entries.sort(key=lambda e: e.paper.published, reverse=True)
        return Grouped([], [], entries)

    def score(e: Entry) -> int:
        return e.assessment.score if e.assessment else 0

    entries.sort(key=lambda e: (score(e), e.paper.published), reverse=True)
    return Grouped(
        highlight=[e for e in entries if score(e) >= cfg.highlight_score],
        related=[e for e in entries if cfg.related_score <= score(e) < cfg.highlight_score],
        others=[e for e in entries if score(e) < cfg.related_score],
    )


def _link_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _meta(p: Paper, a: Assessment | None) -> str:
    parts = [x for x in (p.author_label(), f"*{p.venue}*" if p.venue else "", p.published) if x]
    if a:
        parts.append(f"関連度 {a.score}")
    return " · ".join(parts)


def _detail(e: Entry) -> list[str]:
    p, a = e.paper, e.assessment
    lines = [f"### [{_link_text(p.title)}]({p.url})", "", _meta(p, a), ""]
    if a and a.summary:
        lines += [a.summary, ""]
    if a and a.reason:
        lines += [f"> 関連: {a.reason}", ""]
    return lines


def _brief(e: Entry) -> str:
    p, a = e.paper, e.assessment
    tail = " · ".join(x for x in (p.venue, f"関連度 {a.score}" if a else "") if x)
    return f"- [{_link_text(p.title)}]({p.url})" + (f" — {tail}" if tail else "")


def render(grouped: Grouped, since: date, until: date, summarized: bool) -> str:
    total = len(grouped.highlight) + len(grouped.related) + len(grouped.others)
    period = f"{since.isoformat()} 〜 {until.isoformat()}"
    lines = [
        "---",
        f"date: {until.isoformat()}",
        f'period: "{period}"',
        f"total: {total}",
    ]
    if summarized:
        lines += [f"highlight: {len(grouped.highlight)}", f"related: {len(grouped.related)}"]
    lines += ["tags: [paper-feed]", "---", "", f"# 新着論文 {period}", ""]

    if summarized:
        lines += [
            f"{total} 件を取得 / 注目 {len(grouped.highlight)} 件 / "
            f"関連あり {len(grouped.related)} 件",
            "",
        ]
        for heading, entries in (("注目", grouped.highlight), ("関連あり", grouped.related)):
            lines += [f"## {heading}", ""]
            if not entries:
                lines += ["なし", ""]
            for e in entries:
                lines += _detail(e)
        lines += [f"## その他（{len(grouped.others)} 件）", ""]
        lines += [_brief(e) for e in grouped.others]
    else:
        lines += [f"{total} 件を取得（要約なし）", ""]
        lines += [_brief(e) for e in grouped.others]
    return "\n".join(lines).rstrip() + "\n"
