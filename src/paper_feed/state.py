"""処理済みの論文と前回の実行日を記録し、毎週の重複を防ぐ。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# これより古い記録は捨てる（取得期間よりは十分長くする）
RETENTION_DAYS = 180


@dataclass
class State:
    last_run: date | None = None
    seen: dict[str, str] = field(default_factory=dict)
    """論文ID → 初めて処理した日（YYYY-MM-DD）"""

    def is_seen(self, paper_id: str) -> bool:
        return paper_id in self.seen

    def mark_seen(self, paper_ids: list[str], today: date) -> None:
        for pid in paper_ids:
            self.seen.setdefault(pid, today.isoformat())

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}


def load_state(path: Path) -> State:
    if not path.exists():
        return State()
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    last_run = raw.get("last_run")
    seen = raw.get("seen", {})
    if not isinstance(seen, dict):
        raise ValueError(f"{path} の seen が壊れています")
    return State(
        last_run=date.fromisoformat(last_run) if last_run else None,
        seen={str(k): str(v) for k, v in seen.items()},
    )


def save_state(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_run": state.last_run.isoformat() if state.last_run else None,
        "seen": dict(sorted(state.seen.items())),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
