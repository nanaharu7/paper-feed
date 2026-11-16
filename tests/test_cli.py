from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import make_paper

import paper_feed.__main__ as cli
from paper_feed.models import Paper
from paper_feed.state import State

CONFIG = """
interests: x
sources:
  arxiv:
    queries: [q]
summarizer:
  mode: none
"""


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    state = tmp_path / "state" / "seen.json"
    state.parent.mkdir()
    state.write_text(
        json.dumps({"last_run": "2026-11-09", "seen": {"doi:10.1/1": "2026-11-09"}}),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []

    def fake_collect(
        config: Any, session: Any, since: date, until: date, state: State
    ) -> list[Paper]:
        recorded.append({"since": since, "until": until, "seen": dict(state.seen)})
        return [p for p in (make_paper(1), make_paper(2)) if not state.is_seen(p.id)]

    monkeypatch.setattr(cli, "collect", fake_collect)
    return recorded


def _state(root: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / "state" / "seen.json").read_text(encoding="utf-8"))
    return data


def test_weekly_run_uses_week_label_and_updates_state(
    root: Path, calls: list[dict[str, Any]]
) -> None:
    assert cli.main(["--root", str(root), "run", "--today", "2026-11-16"]) == 0
    assert calls[0]["since"] == date(2026, 11, 8)
    assert (root / "papers" / "2026-W46.md").exists()
    state = _state(root)
    assert state["last_run"] == "2026-11-16"
    assert "doi:10.1/2" in state["seen"]


def test_adhoc_run_includes_seen_and_keeps_state(root: Path, calls: list[dict[str, Any]]) -> None:
    before = _state(root)
    args = ["--root", str(root), "run", "--since", "2026-10-01", "--until", "2026-10-31"]
    assert cli.main(args) == 0

    assert calls[0]["since"] == date(2026, 10, 1)
    assert calls[0]["until"] == date(2026, 10, 31)
    assert calls[0]["seen"] == {}  # 処理済みも含めて取得する
    note = root / "papers" / "2026-10-01_2026-10-31.md"
    assert "2 件を取得" in note.read_text(encoding="utf-8")
    assert _state(root) == before  # 週次の記録は変わらない
    latest = json.loads((root / "state" / "latest.json").read_text(encoding="utf-8"))
    assert latest["note_path"] == "papers/2026-10-01_2026-10-31.md"


@pytest.mark.parametrize(
    "extra",
    [
        ["--until", "2026-10-31"],
        ["--since", "2026-11-01", "--until", "2026-10-31"],
        ["--since", "2026-12-01", "--today", "2026-11-16"],
    ],
)
def test_invalid_period(root: Path, extra: list[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["--root", str(root), "run", *extra])
    assert e.value.code == 2
