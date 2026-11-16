"""Europe PMC: PubMed / PMC / bioRxiv / medRxiv などを横断検索できる。キー不要。"""

from __future__ import annotations

from datetime import date
from typing import Any

import requests

from paper_feed.models import Paper, clean_text

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PAGE_SIZE = 100
TIMEOUT = 60


def build_query(query: str, since: date, until: date) -> str:
    # FIRST_IDATE は Europe PMC に登録された日。出版日より遅れが少なく、新着の取りこぼしが減る
    return f"({query}) AND FIRST_IDATE:[{since.isoformat()} TO {until.isoformat()}]"


def search(
    session: requests.Session, query: str, since: date, until: date, limit: int
) -> list[Paper]:
    papers: list[Paper] = []
    cursor = "*"
    while len(papers) < limit:
        params: dict[str, str | int] = {
            "query": build_query(query, since, until),
            "format": "json",
            "resultType": "core",
            "pageSize": PAGE_SIZE,
            "cursorMark": cursor,
        }
        res = session.get(BASE_URL, params=params, timeout=TIMEOUT)
        res.raise_for_status()
        data: dict[str, Any] = res.json()
        results: list[dict[str, Any]] = data.get("resultList", {}).get("result", [])
        papers.extend(p for p in map(parse_record, results) if p is not None)
        next_cursor = data.get("nextCursorMark")
        if not results or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return papers[:limit]


def parse_record(r: dict[str, Any]) -> Paper | None:
    title = clean_text(r.get("title"))
    if not title:
        return None
    doi = r.get("doi")
    pmid = r.get("pmid")
    source = r.get("source", "")
    rec_id = r.get("id", "")
    if doi:
        pid = f"doi:{doi.lower()}"
        url = f"https://doi.org/{doi}"
    elif pmid:
        pid = f"pmid:{pmid}"
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    else:
        pid = f"epmc:{source}:{rec_id}"
        url = f"https://europepmc.org/article/{source}/{rec_id}"

    authors_raw = r.get("authorList", {}).get("author", [])
    authors = tuple(a["fullName"] for a in authors_raw if a.get("fullName"))
    if not authors and r.get("authorString"):
        authors = tuple(s.strip() for s in r["authorString"].rstrip(".").split(",") if s.strip())

    venue = (
        r.get("journalInfo", {}).get("journal", {}).get("title")
        or r.get("bookOrReportDetails", {}).get("publisher")
        or source
    )
    return Paper(
        id=pid,
        title=title,
        abstract=clean_text(r.get("abstractText")),
        authors=authors,
        venue=venue,
        published=r.get("firstPublicationDate") or r.get("firstIndexDate") or "",
        url=url,
        source="europepmc",
        doi=doi.lower() if doi else None,
    )
