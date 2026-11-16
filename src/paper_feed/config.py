from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml

SummarizerMode = Literal["claude-code", "api", "none"]
SUMMARIZER_MODES: tuple[SummarizerMode, ...] = ("claude-code", "api", "none")
SOURCE_NAMES = ("europepmc", "openalex", "arxiv")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    name: str
    queries: tuple[str, ...]


@dataclass(frozen=True)
class SummarizerConfig:
    mode: SummarizerMode = "claude-code"
    model: str | None = None
    language: str = "ja"
    batch_size: int = 25


@dataclass(frozen=True)
class OutputConfig:
    dir: str = "papers"
    highlight_score: int = 4
    related_score: int = 3


@dataclass(frozen=True)
class Config:
    interests: str
    sources: tuple[SourceConfig, ...]
    lookback_days: int = 7
    max_papers: int = 150
    summarizer: SummarizerConfig = SummarizerConfig()
    output: OutputConfig = OutputConfig()


def _mapping(value: object, where: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where} はマッピングで書いてください")
    return cast(dict[str, object], value)


def _int(value: object, where: str, default: int, minimum: int = 1) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{where} は {minimum} 以上の整数にしてください")
    return value


def _str(value: object, where: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} は空でない文字列にしてください")
    return value.strip()


def parse_config(raw: object) -> Config:
    data = _mapping(raw, "config.yaml")

    interests = data.get("interests")
    if not isinstance(interests, str) or not interests.strip():
        raise ConfigError("interests に興味の説明文を書いてください")

    sources_raw = _mapping(data.get("sources"), "sources")
    sources: list[SourceConfig] = []
    for name, body in sources_raw.items():
        if name not in SOURCE_NAMES:
            raise ConfigError(
                f"未対応の取得元です: {name}（使えるもの: {', '.join(SOURCE_NAMES)}）"
            )
        queries = _mapping(body, f"sources.{name}").get("queries")
        if (
            not isinstance(queries, list)
            or not queries
            or not all(isinstance(q, str) and q.strip() for q in queries)
        ):
            raise ConfigError(f"sources.{name}.queries に検索クエリを1つ以上書いてください")
        sources.append(SourceConfig(name, tuple(q.strip() for q in queries)))
    if not sources:
        raise ConfigError("sources に取得元を1つ以上書いてください")

    s = _mapping(data.get("summarizer"), "summarizer")
    mode = s.get("mode", "claude-code")
    if mode not in SUMMARIZER_MODES:
        raise ConfigError(
            f"summarizer.mode は {' / '.join(SUMMARIZER_MODES)} のどれかにしてください"
        )
    model = s.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ConfigError("summarizer.model は文字列にしてください")
    summarizer = SummarizerConfig(
        mode=mode,
        model=model.strip() if isinstance(model, str) else None,
        language=_str(s.get("language"), "summarizer.language", "ja"),
        batch_size=_int(s.get("batch_size"), "summarizer.batch_size", 25),
    )

    o = _mapping(data.get("output"), "output")
    output = OutputConfig(
        dir=_str(o.get("dir"), "output.dir", "papers"),
        highlight_score=_int(o.get("highlight_score"), "output.highlight_score", 4),
        related_score=_int(o.get("related_score"), "output.related_score", 3),
    )
    if output.related_score > output.highlight_score:
        raise ConfigError("output.related_score は highlight_score 以下にしてください")

    return Config(
        interests=interests.strip(),
        sources=tuple(sources),
        lookback_days=_int(data.get("lookback_days"), "lookback_days", 7),
        max_papers=_int(data.get("max_papers"), "max_papers", 150),
        summarizer=summarizer,
        output=output,
    )


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(
            f"{path} が見つかりません。"
            "queries ワークフローに興味の説明文を入れて実行すると作られます"
        )
    with path.open(encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f))
