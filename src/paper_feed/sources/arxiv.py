"""arXiv: 情報・物理・数学などのプレプリント。キー不要。"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import date

import requests

from paper_feed.models import Paper, clean_text

BASE_URL = "https://export.arxiv.org/api/query"
PAGE_SIZE = 100
TIMEOUT = 60
# arXiv の利用規約で、連続リクエストの間は3秒空けるよう求められている
REQUEST_INTERVAL = 3.0

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def build_query(query: str, since: date, until: date) -> str:
    return f"({query}) AND submittedDate:[{since:%Y%m%d}0000 TO {until:%Y%m%d}2359]"


def search(
    session: requests.Session, query: str, since: date, until: date, limit: int
) -> list[Paper]:
    papers: list[Paper] = []
    start = 0
    while len(papers) < limit:
        if start > 0:
            time.sleep(REQUEST_INTERVAL)
        params: dict[str, str | int] = {
            "search_query": build_query(query, since, until),
            "start": start,
            "max_results": PAGE_SIZE,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        res = session.get(BASE_URL, params=params, timeout=TIMEOUT)
        res.raise_for_status()
        page = parse_feed(res.text)
        papers.extend(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    # 次のクエリとの間隔も空ける
    time.sleep(REQUEST_INTERVAL)
    return papers[:limit]


def _text(entry: ET.Element, path: str) -> str:
    el = entry.find(path, NS)
    return el.text if el is not None and el.text else ""


def parse_feed(xml_text: str) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers: list[Paper] = []
    for entry in root.findall("atom:entry", NS):
        title = clean_text(_text(entry, "atom:title"))
        abs_url = _text(entry, "atom:id")
        if not title or not abs_url:
            continue
        # http://arxiv.org/abs/2611.06189v1 → 2611.06189
        arxiv_id = re.sub(r"v\d+$", "", abs_url.rsplit("/abs/", 1)[-1])
        doi = _text(entry, "arxiv:doi").lower() or None
        authors = tuple(
            name.text.strip() for name in entry.findall("atom:author/atom:name", NS) if name.text
        )
        papers.append(
            Paper(
                id=f"doi:{doi}" if doi else f"arxiv:{arxiv_id}",
                title=title,
                abstract=clean_text(_text(entry, "atom:summary")),
                authors=authors,
                venue="arXiv",
                published=_text(entry, "atom:published")[:10],
                url=f"https://arxiv.org/abs/{arxiv_id}",
                source="arxiv",
                doi=doi,
            )
        )
    return papers
