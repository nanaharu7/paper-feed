"""config.yaml のうち、アプリやワークフローが管理する interests と sources だけを書き換える。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from paper_feed.config import ConfigError, parse_config

CONFIG_FILE = "config.yaml"
EXAMPLE_FILE = "config.example.yaml"

_TOP_LEVEL_RE = re.compile(r"^(?:[^\s#].*|#.*)$")


def ensure_config(root: Path) -> Path:
    """config.yaml がなければ config.example.yaml から作る。"""
    path = root / CONFIG_FILE
    if not path.exists():
        example = root / EXAMPLE_FILE
        if not example.exists():
            raise ConfigError(f"{CONFIG_FILE} も {EXAMPLE_FILE} も見つかりません")
        shutil.copyfile(example, path)
    return path


def read_managed(text: str) -> tuple[str, dict[str, list[str]]]:
    """interests と、取得元ごとのクエリを取り出す。"""
    data: Any = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ConfigError("config.yaml の形式が正しくありません")
    interests = data.get("interests")
    sources: dict[str, list[str]] = {}
    raw_sources = data.get("sources")
    if isinstance(raw_sources, dict):
        for name, body in raw_sources.items():
            queries = body.get("queries") if isinstance(body, dict) else None
            if isinstance(queries, list):
                sources[str(name)] = [q for q in queries if isinstance(q, str)]
    return (interests.strip() if isinstance(interests, str) else ""), sources


def _block_span(lines: list[str], key: str) -> tuple[int, int] | None:
    """トップレベルのキーの行から、その値が終わる行までの範囲 [start, end) を返す。

    値の終わりは、次にインデントなしで始まる行（次のキーやトップレベルのコメント）の手前。
    その直前の空行は値に含めない。
    """
    start = next((i for i, line in enumerate(lines) if line.startswith(f"{key}:")), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and not _TOP_LEVEL_RE.match(lines[end]):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return start, end


def _replace_block(text: str, key: str, block: str) -> str:
    lines = text.splitlines()
    new_lines = block.rstrip("\n").split("\n")
    span = _block_span(lines, key)
    if span is None:
        lines += ["", *new_lines]
    else:
        start, end = span
        lines[start:end] = new_lines
    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def format_interests(interests: str) -> str:
    body = [f"  {line}" if line.strip() else "" for line in interests.strip().splitlines()]
    return "\n".join(["interests: |", *body])


def format_sources(sources: dict[str, list[str]]) -> str:
    lines = ["sources:"]
    for name, queries in sources.items():
        lines += [f"  {name}:", "    queries:"]
        lines += [f"      - {_quote(q)}" for q in queries]
    return "\n".join(lines)


def write_managed(
    text: str,
    interests: str | None = None,
    sources: dict[str, list[str]] | None = None,
) -> str:
    """指定した項目だけを差し替える。他の行（コメントや空行を含む）はそのまま残す。"""
    out = text
    if interests is not None:
        if not interests.strip():
            raise ConfigError("興味の説明文が空です")
        out = _replace_block(out, "interests", format_interests(interests))
    if sources is not None:
        out = _replace_block(out, "sources", format_sources(sources))

    # 書き換えた結果が設定として正しく読めることと、意図した値になっていることを確かめる
    parse_config(yaml.safe_load(out))
    got_interests, got_sources = read_managed(out)
    if interests is not None and got_interests != interests.strip():
        raise ConfigError("interests を正しく書き換えられませんでした")
    if sources is not None and got_sources != sources:
        raise ConfigError("sources を正しく書き換えられませんでした")
    return out
