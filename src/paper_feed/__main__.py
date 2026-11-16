from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from paper_feed.config import ConfigError, load_config
from paper_feed.fetch import collect
from paper_feed.notify import (
    Highlight,
    RunSummary,
    build_payload,
    load_summary,
    note_url,
    save_summary,
    send,
)
from paper_feed.render import group, render
from paper_feed.sources import new_session
from paper_feed.state import load_state, save_state
from paper_feed.summarize import assess, make_backend

log = logging.getLogger("paper_feed")

STATE_FILE = Path("state/seen.json")
SUMMARY_FILE = Path("state/latest.json")
# 前回の実行から間が空いても、さかのぼるのはこの日数まで
MAX_WINDOW_DAYS = 60


def decide_window(today: date, last_run: date | None, lookback_days: int) -> tuple[date, date]:
    """取得期間を決める。前回の実行日から1日重ねて取り、重複は処理済みの記録で除く。"""
    since = last_run - timedelta(days=1) if last_run else today - timedelta(days=lookback_days)
    since = max(since, today - timedelta(days=MAX_WINDOW_DAYS))
    return since, today


def unique_note_path(directory: Path, today: date) -> Path:
    path = directory / f"{today.isoformat()}.md"
    n = 2
    while path.exists():
        path = directory / f"{today.isoformat()}-{n}.md"
        n += 1
    return path


def cmd_run(args: argparse.Namespace) -> int:
    root: Path = args.root
    config = load_config(root / args.config)
    today: date = args.today or date.today()
    state = load_state(root / STATE_FILE)
    since, until = decide_window(today, state.last_run, config.lookback_days)
    log.info("取得期間: %s 〜 %s", since, until)

    papers = collect(config, new_session(), since, until, state)
    log.info("新着候補: %d 件", len(papers))
    if args.fetch_only:
        for p in papers:
            print(f"- {p.published} [{p.venue}] {p.title}")
        return 0

    summarized = config.summarizer.mode != "none" and bool(papers)
    assessments = assess(config, papers, make_backend(config.summarizer)) if summarized else None
    grouped = group(papers, assessments, config.output)

    note_rel: str | None = None
    if papers:
        note_path = unique_note_path(root / config.output.dir, today)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(render(grouped, since, until, summarized), encoding="utf-8")
        note_rel = note_path.relative_to(root).as_posix()
        log.info("書き出し: %s", note_rel)

    # ノートを書けた後で処理済みにする（途中で失敗したら次回やり直せるように）
    state.mark_seen([p.id for p in papers], today)
    state.prune(today)
    state.last_run = today
    save_state(root / STATE_FILE, state)

    save_summary(
        root / SUMMARY_FILE,
        RunSummary(
            since=since.isoformat(),
            until=until.isoformat(),
            total=len(papers),
            summarized=summarized,
            note_path=note_rel,
            related_count=len(grouped.related),
            highlights=[
                Highlight(e.paper.title, e.paper.url, e.assessment.score, e.assessment.summary)
                for e in grouped.highlight
                if e.assessment
            ],
            top=next(
                (
                    Highlight(e.paper.title, e.paper.url, e.assessment.score, e.assessment.summary)
                    # group() で関連度の高い順に並んでいるので、先頭が最も高い
                    for e in grouped.highlight + grouped.related
                    if e.assessment
                ),
                None,
            ),
        ),
    )
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        log.info("DISCORD_WEBHOOK_URL が未設定なので通知しません")
        return 0
    summary = load_summary(args.root / SUMMARY_FILE)
    send(webhook, build_payload(summary, note_url(summary.note_path)), new_session())
    log.info("Discord に通知しました")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paper-feed")
    parser.add_argument("--root", type=Path, default=Path("."), help="リポジトリのルート")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="新着を取得・要約してノートを書く")
    run.add_argument("--config", default="config.yaml")
    run.add_argument("--today", type=date.fromisoformat, help="実行日を上書きする（テスト用）")
    run.add_argument(
        "--fetch-only", action="store_true", help="取得結果を表示するだけで、何も書き込まない"
    )
    run.set_defaults(func=cmd_run)

    notify = sub.add_parser("notify", help="直前の run の結果を Discord に送る")
    notify.set_defaults(func=cmd_notify)

    args = parser.parse_args(argv)
    # Windows の既定（cp932）だと日本語ログや論文タイトルが化けるため UTF-8 にそろえる
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        code: int = args.func(args)
    except ConfigError as e:
        log.error("設定エラー: %s", e)
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
