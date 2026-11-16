from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from paper_feed.sources import arxiv, europepmc, openalex


class FakeResponse:
    def __init__(self, payload: Any = None, text: str = "") -> None:
        self._payload = payload
        self.text = text

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], timeout: int) -> FakeResponse:
        self.calls.append(dict(params))
        return self.responses.pop(0)


EPMC_MED = {
    "id": "42975863",
    "source": "MED",
    "pmid": "42975863",
    "doi": "10.1007/S12264-026-01740-0",
    "title": "Microcircuit <i>reconfigurations</i> in V1",
    "authorList": {"author": [{"fullName": "Yao L"}, {"fullName": "Shang Z"}]},
    "journalInfo": {"journal": {"title": "Neuroscience bulletin"}},
    "abstractText": "<h4>Background</h4><p>Early   experience.</p>",
    "firstPublicationDate": "2026-11-03",
}
EPMC_PPR = {
    "id": "PPR1",
    "source": "PPR",
    "title": "A preprint",
    "authorString": "Kim A, Lee B.",
    "bookOrReportDetails": {"publisher": "bioRxiv"},
    "firstPublicationDate": "2026-11-11",
}


def test_europepmc_parse() -> None:
    p = europepmc.parse_record(EPMC_MED)
    assert p is not None
    assert p.id == "doi:10.1007/s12264-026-01740-0"
    assert p.title == "Microcircuit reconfigurations in V1"
    assert p.abstract == "Background Early experience."
    assert p.venue == "Neuroscience bulletin"
    assert p.authors == ("Yao L", "Shang Z")

    pp = europepmc.parse_record(EPMC_PPR)
    assert pp is not None
    assert pp.id == "epmc:PPR:PPR1"
    assert pp.venue == "bioRxiv"
    assert pp.authors == ("Kim A", "Lee B")
    assert pp.url == "https://europepmc.org/article/PPR/PPR1"


def test_europepmc_search_paginates_and_stops() -> None:
    session = FakeSession(
        [
            FakeResponse({"resultList": {"result": [EPMC_MED]}, "nextCursorMark": "c2"}),
            FakeResponse({"resultList": {"result": [EPMC_PPR]}, "nextCursorMark": "c2"}),
        ]
    )
    papers = europepmc.search(session, "q", date(2026, 11, 9), date(2026, 11, 16), 10)  # type: ignore[arg-type]
    assert len(papers) == 2
    assert session.calls[0]["query"] == "(q) AND FIRST_IDATE:[2026-11-09 TO 2026-11-16]"
    assert session.calls[1]["cursorMark"] == "c2"


def test_openalex_parse() -> None:
    w = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.5/ABC",
        "title": "Work",
        "publication_date": "2026-11-10",
        "abstract_inverted_index": {"world": [1], "hello": [0]},
        "primary_location": {"source": {"display_name": "Nature"}, "landing_page_url": "x"},
        "authorships": [{"author": {"display_name": "Ann"}}],
    }
    p = openalex.parse_work(w)
    assert p is not None
    assert p.id == "doi:10.5/abc"
    assert p.abstract == "hello world"
    assert p.venue == "Nature"
    assert p.url == "https://doi.org/10.5/ABC"


def test_openalex_uses_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    session = FakeSession([FakeResponse({"results": [], "meta": {}})])
    openalex.search(session, "q", date(2026, 11, 9), date(2026, 11, 16), 10)  # type: ignore[arg-type]
    assert session.calls[0]["api_key"] == "k"
    assert session.calls[0]["filter"] == (
        "from_publication_date:2026-11-09,to_publication_date:2026-11-16"
    )


ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2611.06189v2</id>
    <published>2026-11-04T09:02:52Z</published>
    <title>A minimal E/I
      circuit model</title>
    <summary>Recovery from amblyopia.</summary>
    <author><name>Jane Roe</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/solv-int/9901001v1</id>
    <published>1999-01-01T00:00:00Z</published>
    <title>Old</title>
    <summary>s</summary>
    <arxiv:doi>10.9/OLD</arxiv:doi>
  </entry>
</feed>"""


def test_arxiv_parse() -> None:
    papers = arxiv.parse_feed(ARXIV_XML)
    assert papers[0].id == "arxiv:2611.06189"
    assert papers[0].title == "A minimal E/I circuit model"
    assert papers[0].published == "2026-11-04"
    assert papers[0].authors == ("Jane Roe",)
    assert papers[1].id == "doi:10.9/old"
    assert papers[1].url == "https://arxiv.org/abs/solv-int/9901001"


def test_arxiv_query() -> None:
    assert arxiv.build_query("all:x", date(2026, 11, 9), date(2026, 11, 16)) == (
        "(all:x) AND submittedDate:[202611090000 TO 202611162359]"
    )
