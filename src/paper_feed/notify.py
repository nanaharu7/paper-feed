"""Discord の Webhook に週次のまとめを送る。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

TIMEOUT = 30
DESCRIPTION_LIMIT = 4000
# Discord の埋め込みタイトルの上限は256文字
TITLE_LIMIT = 256
SUMMARY_LIMIT = 200
MAX_LISTED = 10


@dataclass(frozen=True)
class Highlight:
    title: str
    url: str
    score: int
    summary: str


@dataclass(frozen=True)
class RunSummary:
    """run の結果。commit 後の notify で使うため、ファイルに書き出しておく。"""

    since: str
    until: str
    total: int
    summarized: bool
    note_path: str | None
    related_count: int = 0
    highlights: list[Highlight] = field(default_factory=list)
    top: Highlight | None = None
    """「注目」か「関連あり」の中で最も関連度が高い論文。通知のタイトルに使う"""


def save_summary(path: Path, summary: RunSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def load_summary(path: Path) -> RunSummary:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    highlights = [Highlight(**h) for h in raw.pop("highlights", [])]
    top = raw.pop("top", None)
    return RunSummary(**raw, highlights=highlights, top=Highlight(**top) if top else None)


def note_url(note_path: str | None) -> str | None:
    """GitHub Actions 上なら、リポジトリ上のノートへのリンクを作る。"""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    ref = os.environ.get("GITHUB_REF_NAME")
    if not (note_path and server and repo and ref):
        return None
    return f"{server}/{repo}/blob/{ref}/{note_path}"


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _month_day(iso: str) -> str:
    """2026-11-09 → 11/9"""
    _, month, day = iso.split("-")
    return f"{int(month)}/{int(day)}"


def build_title(s: RunSummary) -> str:
    """例: 新着論文 11/9〜11/16｜<最も関連度が高い論文> ほか関連2件（全4件）"""
    head = f"新着論文 {_month_day(s.since)}〜{_month_day(s.until)}"
    if s.total == 0:
        return f"{head}｜新着なし"
    tail = f"（全{s.total}件）"
    if not s.top:
        return head + tail
    rest = len(s.highlights) + s.related_count - 1
    if rest > 0:
        tail = f" ほか関連{rest}件" + tail
    # 長い論文タイトルだけを切り詰め、件数の部分は必ず残す
    room = TITLE_LIMIT - len(head) - len("｜") - len(tail)
    return f"{head}｜{_shorten(s.top.title, room)}{tail}"


def build_payload(s: RunSummary, url: str | None) -> dict[str, Any]:
    title = build_title(s)
    if s.total == 0:
        return {"embeds": [{"title": title, "description": "新着はありませんでした"}]}

    if s.summarized:
        lines = [
            f"{s.total} 件を取得 / 注目 {len(s.highlights)} 件 / 関連あり {s.related_count} 件"
        ]
        for h in s.highlights[:MAX_LISTED]:
            title_text = _shorten(h.title, 150).replace("[", "(").replace("]", ")")
            lines += ["", f"**[{title_text}]({h.url})**（関連度 {h.score}）"]
            if h.summary:
                lines.append(_shorten(h.summary, SUMMARY_LIMIT))
        if len(s.highlights) > MAX_LISTED:
            lines += ["", f"ほか {len(s.highlights) - MAX_LISTED} 件"]
    else:
        lines = [f"{s.total} 件を取得（要約なし）"]

    embed: dict[str, Any] = {
        "title": title,
        "description": _shorten("\n".join(lines), DESCRIPTION_LIMIT),
    }
    if url:
        embed["url"] = url
    return {"embeds": [embed]}


def send(webhook_url: str, payload: dict[str, Any], session: requests.Session) -> None:
    res = session.post(webhook_url, json=payload, timeout=TIMEOUT)
    res.raise_for_status()
