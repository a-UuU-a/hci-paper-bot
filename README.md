# HCI Paper Recommendation Bot

**HCIの論文を毎日1本、日本語要約とともにSlackへ届けるBot。**

[![Tests](https://github.com/a-UuU-a/hci-paper-bot/actions/workflows/test.yml/badge.svg)](https://github.com/a-UuU-a/hci-paper-bot/actions/workflows/test.yml)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)

HCI（Human–Computer Interaction：人とコンピュータの関わりを研究する分野）の論文に、日常的に触れるきっかけを作るためのアプリケーションです。複数の国際会議から未配信の論文をランダムに選び、研究の概要と原論文へのリンクをSlackに投稿します。

論文の収集・選択・要約・配信・履歴保存をPythonのバッチ処理として実装しています。GitHub Actionsで定期実行するため、常時稼働するWebサーバーは不要です。

## 主な機能

- **8つの国際会議に対応**：CHI / UIST / DIS / CSCW / TEI / IUI / ISMAR / IEEE VR。対象は設定ファイルで変更できます。
- **最近の研究から毎日1本を紹介**：当年を含む5暦年の論文が対象。初期設定では毎朝07:00 JST頃に実行します。
- **日本語で概要を把握**：Abstract（論文の概要）から最大180文字の要約を生成し、タイトル・著者・学会・発表年・リンクとともに投稿します。
- **配信履歴を保存**：配信済みの論文を次回の候補から除外します。メタデータはSupabase PostgreSQLに保存し、再利用します。
- **投稿前にプレビュー**：SlackやDBに接続せず、ターミナルで表示を確認できる`--dry-run`を用意しています。

## 投稿イメージ

同梱の[デモデータ](examples/papers.json)による表示例です。架空の論文で、以下の本文はLLMによる生成ではなく、日本語Abstractの原文抜粋です。

```text
📄 Example: Exploring Tactile Feedback in Virtual Reality

👤 Alice Example, Bob Example, Carol Example, et al.
🏛 CHI 2025

📝 日本語要約
VR環境における触覚フィードバックの設計を検討するため、試作システムを開発した。参加者による操作課題とインタビューを通じて、提示方法ごとの使いやすさと体験の違いを比較し、設計上の課題を整理した。

🔗 https://example.org/demo-paper

🏷 Virtual Reality · Haptics · Interaction
```

## APIキーなしで試す

Python 3.12以上と[uv](https://docs.astral.sh/uv/getting-started/installation/)を用意し、リポジトリを取得して実行します。

```bash
git clone https://github.com/a-UuU-a/hci-paper-bot.git
cd hci-paper-bot
uv sync --frozen
uv run python -m src.main --dry-run --no-llm --fixture examples/papers.json --year 2025
```

外部APIへの通信・課金・Slack投稿・DB保存を行わず、上のデモをターミナルに表示します。通常の`--dry-run`では実際の論文を取得し、OpenAI APIを利用します。

実際の配信には`DATABASE_URL`・`SLACK_WEBHOOK_URL`・`OPENAI_API_KEY`を設定します。接続情報の準備、プレビュー、DB初期化、GitHub Actionsへの登録は[セットアップと運用ガイド](docs/operations.md)を参照してください。

## システム構成

```mermaid
flowchart TD
    A["GitHub Actions<br/>定期実行 / 手動実行"] --> B["論文一覧の収集<br/>DBLP Search API / SPARQL"]
    B --> C["未配信候補のランダム選択"]
    DB[("Supabase PostgreSQL<br/>論文メタデータ・配信履歴")] -.->|"配信履歴を参照"| C
    C --> D["Abstractなどの情報を補完<br/>OpenAlex / Crossref"]
    D --> E["日本語要約の生成<br/>OpenAI Responses API"]
    E --> F["Slack Incoming Webhook<br/>タイトル・要約・リンクを投稿"]
    F -->|"送信確認後に履歴を保存"| DB
```

候補を順に調べ、必要な情報と要約が揃った論文を配信します。Abstractが見つからない場合や要約に失敗した場合は、次の候補を試します。

| 技術 | 役割 |
| --- | --- |
| Python 3.12+ / uv | バッチ処理、依存関係・実行環境の管理 |
| httpx / Pydantic | HTTP通信、設定と取得データの検証 |
| DBLP / OpenAlex / Crossref | 書誌情報、Abstract、Topicなどの取得・補完 |
| OpenAI Responses API | Abstractに基づく日本語要約。初期モデルは`gpt-4.1-mini` |
| SQLAlchemy / Alembic / Supabase PostgreSQL | ORMによる永続化、DBスキーマの更新、配信履歴管理 |
| Slack Incoming Webhook / GitHub Actions | 通知、定期・手動実行、CI |
| pytest / Ruff | 自動テスト、静的解析、フォーマット |



## テストとCI

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

[CI](.github/workflows/test.yml)ではpush・Pull Requestごとに上記のチェックとオフラインデモを実行し、テスト専用のPostgreSQL 16でORM・マイグレーションも検証します。本番用のSecretsや実サービスへの投稿・課金は使いません。ローカルでは`TEST_DATABASE_URL`が未設定の場合、PostgreSQLのテストのみスキップします。

主な検証対象は、学会・年度フィルタ、論文IDと同一性、APIのページング・再試行・SPARQLへの切り替え、要約の文字数、Slackの表示・送信確認、DBのロールバック、再試行時の履歴重複、CLI全体の処理です。

## ディレクトリ構成

```text
src/
├── collectors/      # DBLP・OpenAlex・Crossrefとの連携
├── services/        # 正規化、同一論文の照合、メタデータ補完
├── recommender/     # フィルタ・選択・スコアの拡張用インターフェース
├── summarizer/      # LLM要約・原文抜粋
├── notification/    # Slackへの投稿
├── database/        # ORMモデル、永続化、接続確認
├── bot.py           # 配信処理の組み立て
└── main.py          # CLI
config/              # 対象学会・配信条件
migrations/          # Alembicによるスキーマ管理
tests/               # 単体・結合テスト
examples/            # オフラインデモ用データ
.github/workflows/   # 定期配信・CI
docs/                # セットアップと運用ガイド
```
