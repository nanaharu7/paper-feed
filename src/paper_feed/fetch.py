from __future__ import annotations

import logging
from datetime import date

import requests

from paper_feed.config import Config
from paper_feed.models import Paper, normalize_title
from paper_feed.sources import SEARCHERS, SearchFn
from paper_feed.state import State

log = logging.getLogger(__name__)


def collect(
    config: Config,
    session: requests.Session,
    since: date,
    until: date,
    state: State,
    searchers: dict[str, SearchFn] = SEARCHERS,
) -> list[Paper]:
    """全取得元・全クエリから集め、重複と処理済みを除いて返す。"""
    found: list[Paper] = []
    for source in config.sources:
        search = searchers[source.name]
        for query in source.queries:
            papers = search(session, query, since, until, config.max_papers)
            log.info("%s: %d 件 (%s)", source.name, len(papers), query)
            found.extend(papers)

    unique: list[Paper] = []
    ids: set[str] = set()
    titles: set[str] = set()
    for p in found:
        key = normalize_title(p.title)
        if p.id in ids or key in titles or state.is_seen(p.id):
            continue
        ids.add(p.id)
        titles.add(key)
        unique.append(p)

    if len(unique) > config.max_papers:
        log.warning(
            "候補が %d 件あったため、新しい順に %d 件に絞りました", len(unique), config.max_papers
        )
        unique.sort(key=lambda p: p.published, reverse=True)
        unique = unique[: config.max_papers]
    return unique
