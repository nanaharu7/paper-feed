# paper-feed

興味のあるトピックの新着論文を毎週自動で集め、Claude で関連度の判定と要約をして、Markdown のノートにまとめるツールです。GitHub Actions で動くので、PC を起動しておく必要はありません。

- 取得元: OpenAlex（全分野）、arXiv、Europe PMC（PubMed・bioRxiv・medRxiv など）
- 要約: Claude のサブスク（Pro / Max）、Anthropic API、要約なし、から選べる
- 出力: `papers/2026-W46.md` のように、ISO 週番号の名前で週ごとのノートを追加
- 通知: Discord に、その週の注目論文を送る（任意）

## 出力の例

```markdown
# 新着論文 2026-11-09 〜 2026-11-16

12 件を取得 / 注目 2 件 / 関連あり 3 件

## 注目

### [論文タイトル](https://doi.org/...)

著者 · *雑誌名* · 2026-11-12 · 関連度 5

（2〜3文の要約）

> 関連: 興味のどこに関係するか
```

## 使い方

### 1. 自分用のリポジトリを作る

このページ右上の **Use this template → Create a new repository** から、自分のアカウントにリポジトリを作ります。興味の内容を公開したくなければ private にしてください。

### 2. `config.yaml` を作る

`config.example.yaml` を `config.yaml` という名前でコピーし、`interests`（興味の説明文）と `sources`（取得元と検索クエリ）を書き換えて commit します。

### 3. Secrets を登録する

リポジトリの **Settings → Secrets and variables → Actions** に登録します。

| 名前 | 必要なとき | 取得方法 |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | `mode: claude-code` | Claude Code をインストールした PC で `claude setup-token` を実行 |
| `ANTHROPIC_API_KEY` | `mode: api` | [Claude Console](https://platform.claude.com/) で発行 |
| `DISCORD_WEBHOOK_URL` | Discord に通知したいとき | Discord のチャンネル設定 → 連携サービス → ウェブフック |
| `OPENALEX_API_KEY` | `openalex` を使うとき（推奨） | [OpenAlex](https://openalex.org/settings/api) で無料発行 |

トークンやキーは自分のものを使い、他人と共有しないでください。

### 4. 動かしてみる

**Actions → paper-feed → Run workflow** で手動実行できます。以降は毎週月曜 7:00（日本時間）に自動で動きます。

ノートの名前は、まとめた週の ISO 週番号です。月曜の実行では前の週（例: 11/9〜11/15 なら `2026-W46.md`）になります。

### 任意の期間で一度だけ実行する

**Run workflow** の画面で開始日（`since`）と終了日（`until`、空なら今日）を入れて実行すると、その期間だけをまとめた `papers/2026-10-01_2026-10-31.md` のようなノートができます。

- 週次で処理済みの論文も含めて取得します
- 週次の記録（`state/seen.json`）は変えないので、毎週の実行には影響しません
- `notify` のチェックを外すと Discord に通知しません
- 1回に扱う件数は `max_papers` までです。長い期間で超えた分は、新しい順に切り捨てられます

手元では `paper-feed run --since 2026-10-01 --until 2026-10-31` で同じことができます。

## 検索クエリの書き方

クエリは取得元ごとの書式で、そのまま API に渡されます。キーワード検索で広めに拾い、`interests` をもとに Claude が関連度で絞り込む、という使い方を想定しています。

| 取得元 | 書き方の例 | 備考 |
|---|---|---|
| `europepmc` | `TITLE_ABS:("ocular dominance" OR "monocular deprivation")` | `TITLE_ABS:` を付けないと本文まで検索して関係ない論文が増える |
| `openalex` | `"knowledge distillation" OR "model compression"` | タイトル・要旨・本文を検索。期間は出版日で絞る |
| `arxiv` | `abs:"quantization" AND cat:cs.LG` | `ti:` タイトル / `abs:` 要旨 / `cat:` カテゴリ |

まず手元で取得結果だけを確認すると、クエリを調整しやすくなります。

```sh
pip install .
paper-feed run --fetch-only
```

## 設定項目

`config.example.yaml` のコメントを参照してください。主なものは次のとおりです。

- `summarizer.mode`: `claude-code` / `api` / `none`
- `summarizer.model`: 使うモデル（省略可）
- `output.highlight_score` / `output.related_score`: 「注目」「関連あり」に分ける関連度の境目

## 仕組み

1. 前回の実行日から今日までに登録された論文を、各取得元・各クエリで集める
2. DOI とタイトルで重複を除き、過去に処理した論文（`state/seen.json`）も除く
3. Claude に興味の説明文と論文の一覧を渡し、関連度（1〜5）と要約を JSON で受け取る
4. ノートを `papers/` に書き出し、commit・push してから Discord に通知する

途中で失敗した場合は処理済みとして記録されないので、次回の実行で同じ期間を取り直します。

## テンプレートの更新を取り込む

```sh
git remote add template https://github.com/nanaharu7/paper-feed.git
git fetch template
git merge template/main --allow-unrelated-histories
```

## 開発

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```
