from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from paper_feed.config import ConfigError, load_config, parse_config
from paper_feed.configfile import CONFIG_FILE, ensure_config, read_managed, write_managed
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
from paper_feed.queries import (
    SCHEMA as QUERY_SCHEMA,
)
from paper_feed.queries import (
    Baseline,
    QueryGenerationError,
    available_sources,
    generate,
    probe_report,
)
from paper_feed.render import group, render
from paper_feed.sources import new_session
from paper_feed.state import State, load_state, save_state
from paper_feed.summarize import SummarizeError, assess, make_backend

log = logging.getLogger("paper_feed")

STATE_FILE = Path("state/seen.json")
SUMMARY_FILE = Path("state/latest.json")
QUERIES_REPORT = Path("state/queries.json")
COMMIT_REF_RE = re.compile(r"^[0-9a-f]{7,40}$")
# 前回の実行から間が空いても、さかのぼるのはこの日数まで
MAX_WINDOW_DAYS = 60


def decide_window(today: date, last_run: date | None, lookback_days: int) -> tuple[date, date]:
    """取得期間を決める。前回の実行日から1日重ねて取り、重複は処理済みの記録で除く。"""
    since = last_run - timedelta(days=1) if last_run else today - timedelta(days=lookback_days)
    since = max(since, today - timedelta(days=MAX_WINDOW_DAYS))
    return since, today


def week_label(until: date) -> str:
    """まとめた週の ISO 週番号（例: 2026-W46）。

    月曜に実行すると前の週の分をまとめることになるため、実行日の前日が属する週を使う。
    """
    year, week, _ = (until - timedelta(days=1)).isocalendar()
    return f"{year}-W{week:02d}"


def unique_note_path(directory: Path, stem: str) -> Path:
    path = directory / f"{stem}.md"
    n = 2
    while path.exists():
        path = directory / f"{stem}-{n}.md"
        n += 1
    return path


def cmd_run(args: argparse.Namespace) -> int:
    root: Path = args.root
    config = load_config(root / args.config)
    today: date = args.today or date.today()
    state = load_state(root / STATE_FILE)

    # --since を指定したときは、その期間だけを一度きりで処理する（週次の記録は変えない）
    adhoc = args.since is not None
    if adhoc:
        since, until = args.since, args.until or today
        stem = f"{since.isoformat()}_{until.isoformat()}"
        log.info("任意期間で実行します（処理済みの論文も含め、週次の記録は更新しません）")
    else:
        since, until = decide_window(today, state.last_run, config.lookback_days)
        stem = week_label(until)
    log.info("取得期間: %s 〜 %s", since, until)

    papers = collect(config, new_session(), since, until, State() if adhoc else state)
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
        note_path = unique_note_path(root / config.output.dir, stem)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(render(grouped, since, until, summarized), encoding="utf-8")
        note_rel = note_path.relative_to(root).as_posix()
        log.info("書き出し: %s", note_rel)

    if not adhoc:
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


def config_at(root: Path, ref: str) -> str:
    """指定した commit の時点の config.yaml を読む（Actions の checkout に履歴が必要）"""
    if not COMMIT_REF_RE.match(ref):
        raise ConfigError(f"戻す先の commit の指定が正しくありません: {ref}")
    proc = subprocess.run(
        ["git", "show", f"{ref}:{CONFIG_FILE}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip()[:200]
        raise ConfigError(f"{ref[:7]} の時点の {CONFIG_FILE} を読めません: {detail}")
    return proc.stdout


def cmd_queries(args: argparse.Namespace) -> int:
    root: Path = args.root
    today: date = args.today or date.today()
    existed = (root / CONFIG_FILE).exists()
    path = ensure_config(root)
    text = path.read_text(encoding="utf-8")
    old_interests, old_sources = read_managed(text)
    session = new_session()

    if args.restore:
        interests, sources = read_managed(config_at(root, args.restore))
        if not interests or not sources:
            raise ConfigError(f"{args.restore[:7]} の時点の設定に、興味か検索条件がありません")
        new_text = write_managed(text, interests, sources)
        report = probe_report(session, sources, today, restored_from=args.restore)
        message = f"chore(config): {args.restore[:7]} の時点の興味と検索条件に戻す"
    else:
        changed = bool(args.interests and args.interests.strip())
        if changed:
            text = write_managed(text, interests=args.interests)
        interests, _ = read_managed(text)
        if not interests:
            raise ConfigError("興味の説明文がありません。--interests で指定してください")
        config = parse_config(yaml.safe_load(text))
        if config.summarizer.mode == "none":
            raise ConfigError("summarizer.mode が none のときは、検索条件を自動で作れません")
        backend = make_backend(config.summarizer, QUERY_SCHEMA)
        allowed = available_sources()
        # 作り直すときは今の検索条件を土台にして、結果が毎回大きく変わらないようにする
        base_sources = {k: v for k, v in old_sources.items() if k in allowed}
        baseline = (
            Baseline(old_interests, probe_report(session, base_sources, today))
            if existed and old_interests and base_sources
            else None
        )
        report = generate(interests, backend, session, today, allowed, baseline=baseline)
        new_text = write_managed(text, sources=report.usable_sources())
        message = (
            "chore(config): 興味を変更し検索条件に反映する"
            if changed
            else "chore(config): 検索条件を作り直す"
        )

    path.write_text(new_text, encoding="utf-8")
    report_path = root / QUERIES_REPORT
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_json(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    log.info("検索条件を書き込みました（直近7日で合計 %d 件）", report.total)
    if args.message_file:
        Path(args.message_file).write_text(message + "\n", encoding="utf-8")
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
    run.add_argument(
        "--since",
        type=date.fromisoformat,
        help="任意期間で一度だけ実行するときの開始日（YYYY-MM-DD）",
    )
    run.add_argument(
        "--until", type=date.fromisoformat, help="任意期間の終了日（YYYY-MM-DD、省略すると今日）"
    )
    run.set_defaults(func=cmd_run)

    notify = sub.add_parser("notify", help="直前の run の結果を Discord に送る")
    notify.set_defaults(func=cmd_notify)

    queries = sub.add_parser("queries", help="興味の説明文から検索条件を作り、config.yaml に書く")
    queries.add_argument("--interests", help="興味の説明文を書き換えてから作る")
    queries.add_argument("--restore", help="指定した commit の時点の興味と検索条件に戻す")
    queries.add_argument("--today", type=date.fromisoformat, help="実行日を上書きする（テスト用）")
    queries.add_argument("--message-file", help="commit メッセージを書き出すファイル")
    queries.set_defaults(func=cmd_queries)

    args = parser.parse_args(argv)
    if args.command == "queries" and args.interests and args.restore:
        queries.error("--interests と --restore は同時に指定できません")
    if args.command == "run":
        today = args.today or date.today()
        if args.until and not args.since:
            run.error("--until を使うときは --since も指定してください")
        if args.since and args.since > (args.until or today):
            run.error("--since は --until（省略時は今日）以前の日付にしてください")
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
    except (QueryGenerationError, SummarizeError) as e:
        log.error("%s", e)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
