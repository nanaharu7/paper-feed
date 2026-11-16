"""LLM で論文の関連度判定と要約を行う。"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from importlib import resources
from typing import Any

from paper_feed.config import Config, SummarizerConfig
from paper_feed.models import Assessment, Paper

log = logging.getLogger(__name__)

# 要旨が極端に長い論文でプロンプトが膨らまないようにする上限（文字数）
ABSTRACT_LIMIT = 3000
CLAUDE_CODE_TIMEOUT = 900
DEFAULT_API_MODEL = "claude-opus-5-5"
# サーバー側の拒否時フォールバックに対応しているモデル
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2"}

LANGUAGE_NAMES = {"ja": "日本語", "en": "English"}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "score", "summary", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

# プロンプトを受け取り、SCHEMA に沿った JSON（パース済み）を返す関数
Backend = Callable[[str], object]


class SummarizeError(RuntimeError):
    pass


def build_prompt(interests: str, language: str, papers: list[Paper]) -> str:
    template = resources.files("paper_feed").joinpath("prompt.md").read_text(encoding="utf-8")
    items = [
        {
            "index": i,
            "title": p.title,
            "venue": p.venue,
            "abstract": p.abstract[:ABSTRACT_LIMIT] or "(要旨なし)",
        }
        for i, p in enumerate(papers)
    ]
    return template.format(
        interests=interests,
        language=LANGUAGE_NAMES.get(language, language),
        papers=json.dumps(items, ensure_ascii=False, indent=1),
    )


def parse_results(output: object, papers: list[Paper]) -> list[Assessment]:
    """LLM の出力を検証し、全論文分の評価がそろっていなければ例外にする。"""
    if not isinstance(output, dict) or not isinstance(output.get("results"), list):
        raise SummarizeError(f"想定外の出力形式です: {str(output)[:200]}")
    by_index: dict[int, Assessment] = {}
    for item in output["results"]:
        if not isinstance(item, dict):
            continue
        idx, score = item.get("index"), item.get("score")
        if not isinstance(idx, int) or not 0 <= idx < len(papers) or not isinstance(score, int):
            continue
        by_index[idx] = Assessment(
            paper_id=papers[idx].id,
            score=min(max(score, 1), 5),
            summary=str(item.get("summary", "")).strip(),
            reason=str(item.get("reason", "")).strip(),
        )
    missing = [i for i in range(len(papers)) if i not in by_index]
    if missing:
        raise SummarizeError(f"{len(missing)} 件の評価が欠けています（index: {missing[:10]}）")
    return [by_index[i] for i in range(len(papers))]


def claude_code_backend(model: str | None) -> Backend:
    exe = shutil.which("claude")
    if exe is None:
        raise SummarizeError(
            "claude コマンドが見つかりません（npm i -g @anthropic-ai/claude-code）"
        )

    def call(prompt: str) -> object:
        cmd = [exe, "-p", "--tools", "", "--output-format", "json"]
        cmd += ["--json-schema", json.dumps(SCHEMA)]
        if model:
            cmd += ["--model", model]
        # 作業フォルダの CLAUDE.md などを読み込ませないよう、空のフォルダで実行する
        with tempfile.TemporaryDirectory() as cwd:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=cwd,
                timeout=CLAUDE_CODE_TIMEOUT,
            )
        if proc.returncode != 0:
            raise SummarizeError(
                f"claude -p が終了コード {proc.returncode} で失敗しました: "
                f"{(proc.stderr or proc.stdout)[:500]}"
            )
        result = json.loads(proc.stdout)
        if result.get("is_error"):
            raise SummarizeError(f"claude -p がエラーを返しました: {str(result)[:500]}")
        if result.get("structured_output") is None:
            raise SummarizeError(f"構造化出力がありません: {str(result.get('result'))[:500]}")
        return result["structured_output"]

    return call


def api_backend(model: str | None) -> Backend:
    import anthropic

    client = anthropic.Anthropic()
    model_id = model or DEFAULT_API_MODEL

    def call(prompt: str) -> object:
        extra: dict[str, Any] = {}
        if model_id in FALLBACK_MODELS:
            # 安全分類器による誤った拒否のとき、サーバー側で別モデルに回す
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        response = client.beta.messages.create(
            model=model_id,
            max_tokens=16000,
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            messages=[{"role": "user", "content": prompt}],
            **extra,
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise SummarizeError(f"応答が途中で止まりました（stop_reason={response.stop_reason}）")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise SummarizeError("応答にテキストがありません")
        return json.loads(text)

    return call


def make_backend(cfg: SummarizerConfig) -> Backend:
    if cfg.mode == "claude-code":
        return claude_code_backend(cfg.model)
    if cfg.mode == "api":
        return api_backend(cfg.model)
    raise ValueError(f"要約しないモードでは backend を作りません: {cfg.mode}")


def assess(config: Config, papers: list[Paper], backend: Backend) -> list[Assessment]:
    """batch_size 件ずつ LLM に渡して評価する。失敗したバッチは1回だけやり直す。"""
    results: list[Assessment] = []
    size = config.summarizer.batch_size
    for start in range(0, len(papers), size):
        batch = papers[start : start + size]
        prompt = build_prompt(config.interests, config.summarizer.language, batch)
        log.info("要約中: %d〜%d / %d 件", start + 1, start + len(batch), len(papers))
        try:
            results.extend(parse_results(backend(prompt), batch))
        except (SummarizeError, json.JSONDecodeError) as e:
            log.warning("要約に失敗したのでやり直します: %s", e)
            results.extend(parse_results(backend(prompt), batch))
    return results
