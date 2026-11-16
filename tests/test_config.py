from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from paper_feed.config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_is_valid() -> None:
    config = load_config(ROOT / "config.example.yaml")
    assert config.summarizer.mode == "claude-code"
    assert [s.name for s in config.sources] == ["openalex", "arxiv"]


def test_defaults() -> None:
    config = parse_config({"interests": "x", "sources": {"arxiv": {"queries": ["q"]}}})
    assert config.lookback_days == 7
    assert config.summarizer.batch_size == 25
    assert config.output.highlight_score == 4


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"sources": {"arxiv": {"queries": ["q"]}}}, "interests"),
        ({"interests": "x", "sources": {}}, "sources"),
        ({"interests": "x", "sources": {"scholar": {"queries": ["q"]}}}, "未対応"),
        ({"interests": "x", "sources": {"arxiv": {"queries": []}}}, "queries"),
        (
            {
                "interests": "x",
                "sources": {"arxiv": {"queries": ["q"]}},
                "summarizer": {"mode": "gpt"},
            },
            "mode",
        ),
        (
            {"interests": "x", "sources": {"arxiv": {"queries": ["q"]}}, "lookback_days": 0},
            "lookback",
        ),
        (
            {
                "interests": "x",
                "sources": {"arxiv": {"queries": ["q"]}},
                "output": {"highlight_score": 3, "related_score": 4},
            },
            "related_score",
        ),
    ],
)
def test_invalid(raw: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="見つかりません"):
        load_config(tmp_path / "config.yaml")


def test_yaml_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"interests": "発達", "sources": {"europepmc": {"queries": ["nNOS"]}}}),
        encoding="utf-8",
    )
    assert load_config(path).interests == "発達"
