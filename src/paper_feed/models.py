from __future__ import annotations

import re
from dataclasses import dataclass

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def clean_text(text: str | None) -> str:
    """HTML タグと余分な空白を取り除く。"""
    if not text:
        return ""
    return _SPACE_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()


def normalize_title(title: str) -> str:
    """取得元をまたいだ重複判定用に、タイトルを英数字だけの小文字列にする。"""
    return re.sub(r"[^0-9a-z]+", "", title.lower())


@dataclass(frozen=True)
class Paper:
    id: str
    """取得元をまたいで一意になる識別子（doi:..., pmid:..., arxiv:... など）"""
    title: str
    abstract: str
    authors: tuple[str, ...]
    venue: str
    published: str
    """YYYY-MM-DD"""
    url: str
    source: str
    doi: str | None = None

    def author_label(self, limit: int = 3) -> str:
        if not self.authors:
            return ""
        if len(self.authors) <= limit:
            return ", ".join(self.authors)
        return ", ".join(self.authors[:limit]) + " et al."


@dataclass(frozen=True)
class Assessment:
    paper_id: str
    score: int
    """関連度 1〜5"""
    summary: str
    reason: str
