from __future__ import annotations

import pytest

from paper_feed.config import Config, OutputConfig, SourceConfig, SummarizerConfig
from paper_feed.models import Paper


def make_paper(n: int, **kw: object) -> Paper:
    base: dict[str, object] = {
        "id": f"doi:10.1/{n}",
        "title": f"Paper {n}",
        "abstract": f"Abstract {n}",
        "authors": ("A", "B"),
        "venue": "J Neurosci",
        "published": f"2026-11-{n:02d}",
        "url": f"https://doi.org/10.1/{n}",
        "source": "europepmc",
        "doi": f"10.1/{n}",
    }
    base.update(kw)
    return Paper(**base)  # type: ignore[arg-type]


@pytest.fixture
def config() -> Config:
    return Config(
        interests="眼優位可塑性",
        sources=(SourceConfig("europepmc", ("q1", "q2")),),
        max_papers=100,
        summarizer=SummarizerConfig(batch_size=2),
        output=OutputConfig(),
    )
