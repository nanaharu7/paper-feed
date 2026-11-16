from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import requests

import paper_feed.__main__ as cli
from paper_feed.config import ConfigError
from paper_feed.configfile import ensure_config, read_managed, write_managed
from paper_feed.models import Probe
from paper_feed.queries import (
    Baseline,
    QueryGenerationError,
    available_sources,
    build_prompt,
    generate,
    parse_proposal,
    probe_report,
)

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 16)

CONFIG = """# 興味
interests: |
  眼優位可塑性。

  nNOS ニューロン。

# 取得元
sources:
  # 古いコメント
  europepmc:
    queries:
      - 'TITLE_ABS:(old)'

# さかのぼる日数
lookback_days: 7
summarizer:
  mode: claude-code  # サブスク
"""


class TestConfigFile:
    def test_read_managed(self) -> None:
        interests, sources = read_managed(CONFIG)
        assert interests == "眼優位可塑性。\n\nnNOS ニューロン。"
        assert sources == {"europepmc": ["TITLE_ABS:(old)"]}

    def test_write_keeps_everything_else(self) -> None:
        out = write_managed(
            CONFIG, "新しい興味", {"europepmc": ["TITLE_ABS:(a)"], "arxiv": ['abs:"it\'s"']}
        )
        assert out == CONFIG.replace(
            "interests: |\n  眼優位可塑性。\n\n  nNOS ニューロン。\n",
            "interests: |\n  新しい興味\n",
        ).replace(
            "sources:\n  # 古いコメント\n  europepmc:\n    queries:\n      - 'TITLE_ABS:(old)'\n",
            "sources:\n  europepmc:\n    queries:\n      - 'TITLE_ABS:(a)'\n"
            "  arxiv:\n    queries:\n      - 'abs:\"it''s\"'\n",
        )
        assert read_managed(out)[1]["arxiv"] == ['abs:"it\'s"']

    def test_same_interests_do_not_change_real_file(self) -> None:
        text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
        interests, sources = read_managed(text)
        assert write_managed(text, interests=interests) == text
        # sources はブロックごと作り直すため、中のコメントは消えるが値は変わらない
        assert read_managed(write_managed(text, sources=sources)) == (interests, sources)

    def test_normalizes_line_endings_and_blank_lines(self) -> None:
        out = write_managed(CONFIG, interests="\r\n一行目  \r\n   \r\n二行目\r三行目\n\n")
        assert read_managed(out)[0] == "一行目\n\n二行目\n三行目"

    def test_one_line_interests(self) -> None:
        text = "interests: old\nsources:\n  arxiv:\n    queries: [q]\n"
        out = write_managed(text, interests="a\nb")
        assert out.startswith("interests: |\n  a\n  b\nsources:")

    def test_invalid_result_is_rejected(self) -> None:
        with pytest.raises(ConfigError):
            write_managed(CONFIG, sources={})
        with pytest.raises(ConfigError):
            write_managed(CONFIG, interests="  ")

    def test_ensure_config_copies_example(self, tmp_path: Path) -> None:
        (tmp_path / "config.example.yaml").write_text(CONFIG, encoding="utf-8")
        path = ensure_config(tmp_path)
        assert path.read_text(encoding="utf-8") == CONFIG
        path.write_text("changed", encoding="utf-8")
        assert ensure_config(tmp_path).read_text(encoding="utf-8") == "changed"


def test_available_sources() -> None:
    assert available_sources({}) == ["europepmc", "arxiv"]
    assert available_sources({"OPENALEX_API_KEY": "k"}) == ["europepmc", "arxiv", "openalex"]


def test_build_prompt_lists_only_available_sources() -> None:
    prompt = build_prompt("興味 {x}", ["europepmc"], [])
    assert "興味 {x}" in prompt
    assert "TITLE_ABS" in prompt
    assert "### arxiv" not in prompt
    assert "20〜150" in prompt


class TestBaselinePrompt:
    def _baseline(self, interests: str) -> Baseline:
        report = probe_report(
            None,  # type: ignore[arg-type]
            {"europepmc": ["keep me"]},
            TODAY,
            probers={"europepmc": _prober({"keep me": 12})},
        )
        return Baseline(interests, report)

    def test_shows_before_text_and_current_queries(self) -> None:
        prompt = build_prompt("新しい興味", ["europepmc"], [], self._baseline("古い興味"))
        assert "<before>\n古い興味\n</before>" in prompt
        assert "`keep me` → 12 件" in prompt
        assert "文字を一切変えずにそのまま残す" in prompt

    def test_unchanged_interests(self) -> None:
        prompt = build_prompt("同じ", ["europepmc"], [], self._baseline("同じ"))
        assert "興味の説明文は変わっていない。" in prompt
        assert "<before>" not in prompt

    def test_no_baseline_section_without_baseline(self) -> None:
        assert "今の検索条件" not in build_prompt("興味", ["europepmc"], [])

    def test_generate_passes_baseline_to_every_round(self) -> None:
        prompts: list[str] = []

        def backend(prompt: str) -> object:
            prompts.append(prompt)
            return {"sources": [{"name": "europepmc", "queries": ["small"]}]}

        generate(
            "新しい興味",
            backend,
            None,  # type: ignore[arg-type]
            TODAY,
            ["europepmc"],
            probers={"europepmc": _prober({"small": 1})},
            baseline=self._baseline("古い興味"),
        )
        assert len(prompts) == 3
        assert all("`keep me` → 12 件" in p for p in prompts)


def test_parse_proposal() -> None:
    out = {
        "sources": [
            {"name": "europepmc", "queries": [" a ", "a", "", *"bcdefghij"]},
            {"name": "openalex", "queries": ["x"]},
            {"name": "arxiv", "queries": []},
        ]
    }
    assert parse_proposal(out, ["europepmc", "arxiv"]) == {"europepmc": [*"abcdefgh"]}
    with pytest.raises(QueryGenerationError):
        parse_proposal({"sources": []}, ["europepmc"])


def _prober(counts: dict[str, int]) -> Any:
    def probe(session: Any, query: str, since: date, until: date, n: int) -> Probe:
        if query == "broken":
            raise requests.HTTPError("400 Bad Request")
        return Probe(counts.get(query, 0), (f"title of {query}",))

    return probe


class TestGenerate:
    def test_stops_when_within_target(self) -> None:
        prompts: list[str] = []

        def backend(prompt: str) -> object:
            prompts.append(prompt)
            return {"sources": [{"name": "europepmc", "queries": ["good"]}]}

        report = generate(
            "興味",
            backend,
            None,
            TODAY,
            ["europepmc"],  # type: ignore[arg-type]
            probers={"europepmc": _prober({"good": 50})},
        )
        assert len(prompts) == 1
        assert report.total == 50
        assert report.within_target
        assert report.rounds == 1
        assert report.usable_sources() == {"europepmc": ["good"]}

    def test_feeds_back_counts_and_errors(self) -> None:
        answers = [
            {"sources": [{"name": "europepmc", "queries": ["narrow", "broken"]}]},
            {"sources": [{"name": "europepmc", "queries": ["good"]}]},
        ]
        prompts: list[str] = []

        def backend(prompt: str) -> object:
            prompts.append(prompt)
            return answers[len(prompts) - 1]

        report = generate(
            "興味",
            backend,
            None,
            TODAY,
            ["europepmc"],  # type: ignore[arg-type]
            probers={"europepmc": _prober({"narrow": 3, "good": 40})},
        )
        assert "`narrow` → 3 件" in prompts[1]
        assert "`broken` → エラー: 400 Bad Request" in prompts[1]
        assert "title of narrow" in prompts[1]
        assert report.usable_sources() == {"europepmc": ["good"]}
        assert report.rounds == 2

    def test_returns_closest_when_never_within_target(self) -> None:
        answers = iter(["huge", "small", "bigger"])

        def backend(prompt: str) -> object:
            return {"sources": [{"name": "europepmc", "queries": [next(answers)]}]}

        report = generate(
            "興味",
            backend,
            None,
            TODAY,
            ["europepmc"],  # type: ignore[arg-type]
            probers={"europepmc": _prober({"huge": 5000, "small": 12, "bigger": 400})},
        )
        assert report.usable_sources() == {"europepmc": ["small"]}
        assert report.rounds == 3

    def test_fails_when_no_usable_query(self) -> None:
        def backend(prompt: str) -> object:
            return {"sources": [{"name": "europepmc", "queries": ["broken"]}]}

        with pytest.raises(QueryGenerationError):
            generate(
                "興味",
                backend,
                None,
                TODAY,
                ["europepmc"],  # type: ignore[arg-type]
                probers={"europepmc": _prober({})},
            )


def test_probe_report_json() -> None:
    report = probe_report(
        None,  # type: ignore[arg-type]
        {"europepmc": ["a"]},
        TODAY,
        restored_from="abc1234",
        probers={"europepmc": _prober({"a": 30})},
    )
    data = report.to_json()
    assert data["total"] == 30
    assert data["within_target"] is True
    assert data["restored_from"] == "abc1234"
    assert data["since"] == "2026-11-09"
    assert data["sources"][0]["queries"][0] == {
        "query": "a",
        "count": 30,
        "titles": ["title of a"],
        "error": None,
    }


class TestCli:
    @pytest.fixture
    def repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        (tmp_path / "config.example.yaml").write_text(CONFIG, encoding="utf-8")
        self.baselines: list[Baseline | None] = []
        monkeypatch.setattr(cli, "generate", self._fake_generate)
        monkeypatch.setattr(cli, "make_backend", lambda cfg, schema: lambda p: {})
        monkeypatch.setattr(
            cli,
            "probe_report",
            lambda session, sources, today, restored_from=None: probe_report(
                session, sources, today, restored_from, probers={"europepmc": _prober({"q": 7})}
            ),
        )
        return tmp_path

    def _fake_generate(
        self,
        interests: str,
        backend: Any,
        session: Any,
        today: date,
        sources: Any,
        baseline: Baseline | None = None,
    ) -> Any:
        self.baselines.append(baseline)
        return probe_report(
            session,
            {"europepmc": [f"q for {interests[:4]}"]},
            today,
            probers={"europepmc": _prober({})},
        )

    def test_regeneration_uses_current_queries_as_baseline(self, repo: Path) -> None:
        base = ["--root", str(repo), "queries", "--interests"]
        assert cli.main([*base, "最初の興味"]) == 0
        # config.yaml を作った最初の回は、例の検索条件を土台にしない
        assert self.baselines == [None]

        assert cli.main([*base, "次の興味"]) == 0
        baseline = self.baselines[1]
        assert baseline is not None
        assert baseline.interests == "最初の興味"
        assert baseline.report.sources[0].queries[0].query == "q for 最初の興"

    def test_creates_config_and_writes_queries(self, repo: Path) -> None:
        msg = repo / "msg.txt"
        args = [
            "--root",
            str(repo),
            "queries",
            "--interests",
            "臨界期の制御",
            "--message-file",
            str(msg),
        ]
        assert cli.main(args) == 0
        interests, sources = read_managed((repo / "config.yaml").read_text(encoding="utf-8"))
        assert interests == "臨界期の制御"
        assert sources == {"europepmc": ["q for 臨界期の"]}
        assert msg.read_text(encoding="utf-8") == "chore(config): 興味を変更し検索条件に反映する\n"
        report = json.loads((repo / "state" / "queries.json").read_text(encoding="utf-8"))
        assert report["sources"][0]["name"] == "europepmc"

    def test_restore_from_commit(self, repo: Path) -> None:
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "config.yaml").write_text(CONFIG, encoding="utf-8")
        subprocess.run([*git, "add", "config.yaml"], cwd=repo, check=True)
        subprocess.run([*git, "commit", "-qm", "old"], cwd=repo, check=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        (repo / "config.yaml").write_text(
            write_managed(CONFIG, "新しい興味", {"arxiv": ["abs:x"]}), encoding="utf-8"
        )

        msg = repo / "msg.txt"
        args = ["--root", str(repo), "queries", "--restore", sha, "--message-file", str(msg)]
        assert cli.main(args) == 0
        assert read_managed((repo / "config.yaml").read_text(encoding="utf-8")) == read_managed(
            CONFIG
        )
        assert msg.read_text(encoding="utf-8").startswith(f"chore(config): {sha[:7]} の時点")
        report = json.loads((repo / "state" / "queries.json").read_text(encoding="utf-8"))
        assert report["restored_from"] == sha

    @pytest.mark.parametrize("ref", ["main; rm -rf /", "HEAD", "zzzzzzz"])
    def test_restore_rejects_invalid_ref(self, repo: Path, ref: str) -> None:
        assert cli.main(["--root", str(repo), "queries", "--restore", ref]) == 2

    def test_interests_and_restore_are_exclusive(self, repo: Path) -> None:
        with pytest.raises(SystemExit):
            cli.main(["--root", str(repo), "queries", "--interests", "a", "--restore", "abc1234"])
