"""OpenAlex: 全分野をカバーする。無料の API キー（OPENALEX_API_KEY）を推奨。"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import requests

from paper_feed.models import Paper, clean_text

BASE_URL = "https://api.openalex.org/works"
PAGE_SIZE = 100
TIMEOUT = 60
SELECT = "id,doi,title,publication_date,abstract_inverted_index,primary_location,authorships"


def search(
    session: requests.Session, query: str, since: date, until: date, limit: int
) -> list[Paper]:
    # 登録日（from_created_date）での絞り込みは有料プラン限定のため、出版日で絞る
    params: dict[str, str | int] = {
        "search": query,
        "filter": f"from_publication_date:{since.isoformat()},"
        f"to_publication_date:{until.isoformat()}",
        "per-page": PAGE_SIZE,
        "select": SELECT,
        "cursor": "*",
    }
    api_key = os.environ.get("OPENALEX_API_KEY")
    if api_key:
        params["api_key"] = api_key

    papers: list[Paper] = []
    while len(papers) < limit:
        res = session.get(BASE_URL, params=params, timeout=TIMEOUT)
        res.raise_for_status()
        data: dict[str, Any] = res.json()
        results: list[dict[str, Any]] = data.get("results", [])
        papers.extend(p for p in map(parse_work, results) if p is not None)
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not results or not next_cursor:
            break
        params["cursor"] = next_cursor
    return papers[:limit]


def rebuild_abstract(index: dict[str, list[int]] | None) -> str:
    """OpenAlex の転置インデックス形式の要旨を文章に戻す。"""
    if not index:
        return ""
    words: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))


def parse_work(w: dict[str, Any]) -> Paper | None:
    title = clean_text(w.get("title"))
    if not title:
        return None
    doi_url: str | None = w.get("doi")
    doi = doi_url.removeprefix("https://doi.org/").lower() if doi_url else None
    work_id = str(w.get("id", "")).rsplit("/", 1)[-1]
    location = w.get("primary_location") or {}
    venue = (location.get("source") or {}).get("display_name") or ""
    authors = tuple(
        a["author"]["display_name"]
        for a in w.get("authorships", [])
        if a.get("author", {}).get("display_name")
    )
    return Paper(
        id=f"doi:{doi}" if doi else f"openalex:{work_id}",
        title=title,
        abstract=clean_text(rebuild_abstract(w.get("abstract_inverted_index"))),
        authors=authors,
        venue=venue,
        published=w.get("publication_date") or "",
        url=doi_url or location.get("landing_page_url") or str(w.get("id", "")),
        source="openalex",
        doi=doi,
    )
