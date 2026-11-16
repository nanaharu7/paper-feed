from __future__ import annotations

from collections.abc import Callable
from datetime import date

import requests

from paper_feed.models import Paper, Probe
from paper_feed.sources import arxiv, europepmc, openalex

SearchFn = Callable[[requests.Session, str, date, date, int], list[Paper]]
ProbeFn = Callable[[requests.Session, str, date, date, int], Probe]

SEARCHERS: dict[str, SearchFn] = {
    "europepmc": europepmc.search,
    "openalex": openalex.search,
    "arxiv": arxiv.search,
}

PROBERS: dict[str, ProbeFn] = {
    "europepmc": europepmc.probe,
    "openalex": openalex.probe,
    "arxiv": arxiv.probe,
}

USER_AGENT = "paper-feed/0.1 (+https://github.com/nanaharu7/paper-feed)"


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session
