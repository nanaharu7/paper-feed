from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import make_paper

from paper_feed.__main__ import decide_window, unique_note_path
from paper_feed.config import Config, OutputConfig
from paper_feed.fetch import collect
from paper_feed.models import Assessment, Paper
from paper_feed.notify import (
    Highlight,
    RunSummary,
    build_payload,
    build_title,
    load_summary,
    save_summary,
)
from paper_feed.render import group, render
from paper_feed.state import State, load_state, save_state
from paper_feed.summarize import SummarizeError, assess, build_prompt, parse_results


def test_collect_dedupes(config: Config) -> None:
    def fake_search(session: Any, query: str, since: date, until: date, limit: int) -> list[Paper]:
        if query == "q1":
            return [make_paper(1), make_paper(2), make_paper(3)]
        # 同じ DOI、表記ゆれのある同じタイトル、処理済みの論文
        return [make_paper(1), make_paper(9, id="pmid:9", title="paper 2!"), make_paper(4)]

    state = State(seen={"doi:10.1/3": "2026-11-01"})
    papers = collect(
        config,
        None,
        date(2026, 11, 9),
        date(2026, 11, 16),
        state,  # type: ignore[arg-type]
        searchers={"europepmc": fake_search},
    )
    assert [p.id for p in papers] == ["doi:10.1/1", "doi:10.1/2", "doi:10.1/4"]


def test_collect_caps_newest_first(config: Config) -> None:
    def fake_search(session: Any, query: str, since: date, until: date, limit: int) -> list[Paper]:
        return [make_paper(i) for i in range(1, 6)] if query == "q1" else []

    capped = Config(**{**config.__dict__, "max_papers": 2})
    papers = collect(
        capped,
        None,
        date(2026, 11, 1),
        date(2026, 11, 16),
        State(),  # type: ignore[arg-type]
        searchers={"europepmc": fake_search},
    )
    assert [p.published for p in papers] == ["2026-11-05", "2026-11-04"]


def test_decide_window() -> None:
    today = date(2026, 11, 16)
    assert decide_window(today, None, 7) == (date(2026, 11, 9), today)
    assert decide_window(today, date(2026, 11, 9), 7) == (date(2026, 11, 8), today)
    assert decide_window(today, date(2025, 1, 1), 7) == (date(2026, 9, 17), today)


def test_state_roundtrip_and_prune(tmp_path: Path) -> None:
    state = State(last_run=date(2026, 11, 9), seen={"old": "2026-01-01"})
    state.mark_seen(["a", "old"], date(2026, 11, 16))
    state.prune(date(2026, 11, 16))
    path = tmp_path / "state" / "seen.json"
    save_state(path, state)
    loaded = load_state(path)
    assert loaded.seen == {"a": "2026-11-16"}
    assert loaded.last_run == date(2026, 11, 9)


def test_build_prompt_contains_papers() -> None:
    prompt = build_prompt("nNOS ニューロン {braces}", "ja", [make_paper(1, abstract="")])
    assert "nNOS ニューロン {braces}" in prompt
    assert "日本語" in prompt
    assert '"index": 0' in prompt
    assert "(要旨なし)" in prompt


def test_parse_results() -> None:
    papers = [make_paper(1), make_paper(2)]
    out = {
        "results": [
            {"index": 1, "score": 9, "summary": "s2", "reason": "r2"},
            {"index": 0, "score": 2, "summary": "s1", "reason": ""},
        ]
    }
    res = parse_results(out, papers)
    assert [(a.paper_id, a.score) for a in res] == [("doi:10.1/1", 2), ("doi:10.1/2", 5)]

    with pytest.raises(SummarizeError, match="欠けています"):
        parse_results({"results": [out["results"][0]]}, papers)
    with pytest.raises(SummarizeError, match="想定外"):
        parse_results([], papers)


def test_assess_batches_and_retries_once(config: Config) -> None:
    papers = [make_paper(i) for i in range(1, 4)]
    calls: list[str] = []

    def backend(prompt: str) -> object:
        calls.append(prompt)
        n = prompt.count('"index"')
        if len(calls) == 1:
            return {"results": []}  # 1回目は失敗させる
        return {
            "results": [{"index": i, "score": 3, "summary": "", "reason": ""} for i in range(n)]
        }

    res = assess(config, papers, backend)
    assert len(res) == 3
    assert len(calls) == 3  # バッチ2つ + やり直し1回


def test_assess_raises_after_second_failure(config: Config) -> None:
    with pytest.raises(SummarizeError):
        assess(config, [make_paper(1)], lambda _: {"results": []})


def _assessments(papers: list[Paper], scores: list[int]) -> list[Assessment]:
    return [
        Assessment(p.id, s, f"要約{s}", f"理由{s}") for p, s in zip(papers, scores, strict=True)
    ]


def test_render_groups() -> None:
    papers = [make_paper(1, title="Ocular [dominance]"), make_paper(2), make_paper(3)]
    grouped = group(papers, _assessments(papers, [5, 3, 1]), OutputConfig())
    md = render(grouped, date(2026, 11, 9), date(2026, 11, 16), summarized=True)
    assert "3 件を取得 / 注目 1 件 / 関連あり 1 件" in md
    assert "### [Ocular \\[dominance\\]](https://doi.org/10.1/1)" in md
    assert "> 関連: 理由5" in md
    assert "## その他（1 件）" in md
    assert md.startswith("---\ndate: 2026-11-16\n")


def test_render_without_summary() -> None:
    papers = [make_paper(1), make_paper(2)]
    md = render(group(papers, None, OutputConfig()), date(2026, 11, 9), date(2026, 11, 16), False)
    assert "2 件を取得（要約なし）" in md
    assert md.index("Paper 2") < md.index("Paper 1")


def test_unique_note_path(tmp_path: Path) -> None:
    today = date(2026, 11, 16)
    first = unique_note_path(tmp_path, today)
    first.write_text("x", encoding="utf-8")
    assert unique_note_path(tmp_path, today).name == "2026-11-16-2.md"


def test_summary_roundtrip_and_payload(tmp_path: Path) -> None:
    summary = RunSummary(
        since="2026-11-09",
        until="2026-11-16",
        total=5,
        summarized=True,
        note_path="papers/2026-11-16.md",
        related_count=2,
        highlights=[Highlight("T [x]", "https://u", 5, "要約" * 200)],
    )
    path = tmp_path / "latest.json"
    save_summary(path, summary)
    loaded = load_summary(path)
    assert loaded == summary

    payload = build_payload(loaded, "https://github.com/o/r/blob/main/papers/2026-11-16.md")
    embed = payload["embeds"][0]
    assert embed["url"].endswith("2026-11-16.md")
    assert "注目 1 件 / 関連あり 2 件" in embed["description"]
    assert "**[T (x)](https://u)**" in embed["description"]
    assert len(embed["description"]) < 4000
    json.dumps(payload)


def test_payload_when_empty() -> None:
    summary = RunSummary("2026-11-09", "2026-11-16", 0, False, None)
    assert "新着はありません" in build_payload(summary, None)["embeds"][0]["description"]


def test_title_includes_top_paper() -> None:
    top = Highlight("Microcircuit reconfigurations in V1", "https://u", 3, "")

    # 関連ありが1件だけ（タイトルに出す論文のみ）
    only = RunSummary("2026-11-09", "2026-11-16", 4, True, None, related_count=1, top=top)
    assert build_title(only) == (
        "新着論文 11/9〜11/16｜Microcircuit reconfigurations in V1（全4件）"
    )

    # 注目1件 + 関連あり2件 → ほか関連2件
    h = Highlight("Top", "https://t", 5, "")
    many = RunSummary("2026-11-09", "2026-11-16", 9, True, None, 2, [h], top=h)
    assert build_title(many) == "新着論文 11/9〜11/16｜Top ほか関連2件（全9件）"

    # 関連する論文がない週
    none = RunSummary("2026-11-09", "2026-11-16", 3, True, None)
    assert build_title(none) == "新着論文 11/9〜11/16（全3件）"

    # 新着がない週
    assert build_title(RunSummary("2026-11-09", "2026-11-16", 0, False, None)) == (
        "新着論文 11/9〜11/16｜新着なし"
    )


def test_long_title_keeps_counts() -> None:
    h = Highlight("x" * 400, "u", 5, "")
    summary = RunSummary("2026-11-09", "2026-11-16", 20, True, None, 4, [h], top=h)
    title = build_title(summary)
    assert len(title) == 256
    assert title.endswith("… ほか関連4件（全20件）")


def test_load_summary_without_top(tmp_path: Path) -> None:
    # top を追加する前に書かれた latest.json も読めること
    path = tmp_path / "latest.json"
    path.write_text(
        json.dumps(
            {
                "since": "2026-11-09",
                "until": "2026-11-16",
                "total": 0,
                "summarized": False,
                "note_path": None,
                "related_count": 0,
                "highlights": [],
            }
        ),
        encoding="utf-8",
    )
    assert load_summary(path).top is None
