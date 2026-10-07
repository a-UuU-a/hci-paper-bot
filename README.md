# HCI Paper Recommendation Bot

CHI / UIST / DIS / CSCW / TEI / IUI / ISMAR / IEEE VR の論文から、毎日未配信の論文をランダムに選び、日本語要約付きでSlackに投稿するPython 3.12アプリケーションです。仕様書のMVPを実装しています。

DBLPで書誌情報を取得し、OpenAlexでAbstract・Topic・引用数を補完、Crossrefで不足情報を補います。SQLAlchemy ORMでSupabase PostgreSQLに論文と配信履歴を保存し、Alembicでテーブルを作成・更新します。GitHub Actionsが毎日07:00 JST頃に起動します。Webサーバーは不要です。

## ローカルで試す

[uv](https://docs.astral.sh/uv/getting-started/installation/)をインストールして実行します。

```bash
uv sync --frozen
uv run python -m src.main --dry-run --no-llm --fixture examples/papers.json --year 2025
```

この例は架空のデモ論文です。APIキー・通信は不要で、Slack投稿もSupabase保存も行いません。日本語Abstractの原文抜粋をターミナルに表示します。

実際の論文とLLM要約をプレビューする場合は以下を実行します。

```bash
cp .env.example .env
# .env に OPENAI_API_KEY を記入
uv run python -m src.main --dry-run
```

`--dry-run`はSlack・Supabaseに接続しません。通常のプレビューは学術APIとOpenAI APIを利用します。OpenAI APIの利用料が発生します。`--no-llm`で原文抜粋に切り替えられます。`--fixture`は`--dry-run`時のみ使用可能です。

## 利用開始までに行うこと

用意する接続情報は、DB接続URL・Slack Webhook URL・OpenAI APIキーの3つです。

1. [Supabase](https://supabase.com/dashboard)でプロジェクトを作成します。プロジェクトの **Connect → Session pooler → URI** を選び、接続URLをコピーします。`[YOUR-PASSWORD]`をプロジェクト作成時のDBパスワードに置き換えて、`DATABASE_URL`に設定します。SupabaseのAPIキーは不要です。Session poolerを使うとIPv4環境から接続できます。[接続方法](https://supabase.com/docs/guides/database/connecting-to-postgres)
2. [Slack Apps](https://api.slack.com/apps)でAppを作成し、**Incoming Webhooks → Activate Incoming Webhooks → Add New Webhook to Workspace** から投稿先チャンネルを選びます。表示されたURLを`SLACK_WEBHOOK_URL`に設定します。
3. [OpenAIのAPIキー画面](https://platform.openai.com/api-keys)でキーを作成し、`OPENAI_API_KEY`に設定します。[公式クイックスタート](https://developers.openai.com/api/docs/quickstart)
4. `cp .env.example .env`で設定ファイルを作り、上記の3つを記入します。`.env`はGit管理対象外です。

DBパスワードに`@`・`:`・`/`・`#`等が含まれる場合は、URLのパスワード部分をURLエンコードしてください。`postgresql://...`を貼り付ければ自動的にpsycopgドライバを使用します。PostgreSQLへの通信は初期設定でTLSを使用します。

| 環境変数 | 用途 |
| --- | --- |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook |
| `DATABASE_URL` | SupabaseのSession pooler接続URI |
| `OPENAI_API_KEY` | 日本語要約生成 |
| `OPENAI_MODEL` | 任意。初期値は`gpt-4.1-mini` |
| `OPENALEX_API_KEY` | 任意。OpenAlexのAPI予算を増やす無料キー |
| `METADATA_EMAIL` | 任意。学術APIに伝える連絡先 |

```bash
uv sync --frozen
uv run python -m src.main --init-db
uv run python -m src.main --dry-run
# 表示を確認してから実際にSlackへ投稿する
uv run python -m src.main
```

`--init-db`はDBの初期化・更新だけを行います。SQL Editorへの手動貼り付けは不要です。再度実行しても適用済みのマイグレーションは繰り返しません。初期テーブルのRLSを有効にし、一般クライアントにはアクセスを許可しない構成です。

通常実行はSlackに投稿し、成功した論文を配信済みとして保存します。DBパスワード・Webhook URL・APIキーはログに出力しません。DBLPの検索APIがbot対策ページを返す場合は、公式SPARQL APIに自動で切り替えます。プレビューで実行環境からAPIにアクセスできることを確認してください。

## GitHub Actions

コードをGitHubリポジトリに配置し、**Settings → Secrets and variables → Actions → New repository secret** で次の3つを登録します。

- `DATABASE_URL`
- `SLACK_WEBHOOK_URL`
- `OPENAI_API_KEY`

`OPENALEX_API_KEY`は必要に応じて追加します。`OPENAI_MODEL`と`METADATA_EMAIL`は任意のRepository variablesです。本番Workflowは投稿前にAlembicマイグレーションを適用するので、GitHubで初回実行する場合も手動でテーブルを作成する必要はありません。

- [daily-paper.yml](.github/workflows/daily-paper.yml): 毎日22:00 UTC（翌日07:00 JST）頃に本番実行。
- Actions → Daily HCI Paper → Run workflow: 手動実行。最初は`dry_run=true`で確認し、投稿する場合はチェックを外します。
- [test.yml](.github/workflows/test.yml): push / PRでRuff・pytest・オフラインプレビューを実行。テスト用PostgreSQL 16でORM・マイグレーションも検証します。

定期実行はデフォルトブランチにあるWorkflowが対象です。開始時刻には遅延があり得ます。同一リポジトリの定期・手動実行はconcurrencyで直列化しています。他のリポジトリやローカルから同時に本番実行しないでください。

## DB実装

[src/database/models.py](src/database/models.py)がSQLAlchemyのテーブルモデル、[src/database/orm.py](src/database/orm.py)が取得・保存処理です。`Paper`は引き続きPydanticの内部データモデルです。`migrations/versions/`のAlembicリビジョンでスキーマを管理します。

複数バッチの論文保存を一つのDBトランザクションで行い、DOIベースのIDでupsertします。配信履歴には同じ書き込みの再試行で同じUUIDを使い、DB保存の再試行による履歴の重複を防ぎます。DB接続エラーは最大3回再試行します。

以前のSQLを実行済みの場合、`--init-db`は既存テーブルの必要な列を確認し、データを保持してAlembic管理に移行します。スキーマが異なる場合は停止します。初期構成のSupabase REST方式も利用可能で、`DATABASE_URL`が未設定のときに`SUPABASE_URL`と`SUPABASE_KEY`を使います。ORMとRESTを同時に設定した場合はORMを優先します。

Supabaseを作る前にローカルDBだけを試す場合は、`.env`の`DATABASE_URL`を`sqlite:///./papers.sqlite`にして`--init-db`を実行できます。SQLiteファイルはGit管理対象外です。GitHub Actionsの本番実行にはSupabase PostgreSQLを使ってください。

## 設定

[config/venues.yaml](config/venues.yaml)の`enabled`で8学会を切り替えます。DBLPのstreamと会議名を指定し、Extended Abstracts / Companion / WorkshopやProceedings自体を除外します。CSCWはPACMHCIのCSCW号も対象とし、他の学会の号を混入させません。

PACMHCIは2025年から号番号が数値になったため、CSCWの号を`journal_issues`に指定しています。2025年は2・7号、2026年は確認できた2号を設定しています。新しい号や2027年以降の対象年はDBLPのCSCW目録を確認して追加してください。2025年以降の年の設定が欠ける場合は明示的に失敗します。

[config/settings.yaml](config/settings.yaml)で次を変更できます。

| 設定 | 初期値 / 意味 |
| --- | --- |
| `recommendation.papers_per_day` | 1。1回の実行で配信する件数 |
| `recommendation.selection_method` | `random` |
| `recommendation.exclude_sent` | `true`。成功履歴がある論文を除外 |
| `publication.years_back` | 5。当年を含む5暦年（2026年なら2022〜2026年） |
| `publication.require_abstract` | `true` |
| `summary.max_chars` | 180。生成結果の最大文字数 |
| `summary.use_llm` | `true`。OpenAI Responses APIで日本語要約 |
| `summary.fallback` | `skip`。`extractive`で原文抜粋に変更可能 |
| `slack.show_topics` / `max_authors` | `true` / 3 |
| `slack.channel` | 履歴上のラベル。投稿先はWebhookで決定 |
| `collection.max_enrichment_attempts` | 50。1回で調べる候補の上限 |
| `collection.request_interval` | 1秒。DBLPへのリクエスト間隔 |
| `collection.use_crossref` | `true` |
| `http.max_retries` | 3。最初のリクエストに加え最大3回再試行 |

設定は起動時に検証します。`--config-dir`で別の設定ディレクトリ、`--year`で対象期間の最終年を指定できます。

## 処理と障害時の動作

1. 配信履歴と保存済みメタデータを読み、設定した全学会・全年度の書誌情報をDBLPからページング取得します。
2. DOI優先のIDで重複を排除し、過去に補完したAbstract等を保ったままSupabaseに保存します。DOIが後から判明した場合もタイトル・年・筆頭著者による照合で配信履歴を引き継ぎます。
3. 未配信候補を均等なランダム順に並べます。必要な候補だけOpenAlex / Crossrefで順に補完し、Abstractフィルタと要約生成を通った論文を選択します。全論文のAbstractを毎日取得する方式ではありません。
4. SlackがHTTP 200と`ok`を返したことを確認した後、`recommendations.status=sent`を保存します。Slack失敗は`failed`として記録可能な場合に保存し、配信済みにはしません。

DBLPの検索APIが通信・応答エラーになった場合は、公式SPARQL APIから同じ学会・年の書誌情報を取得します。一度切り替えたら、その実行中は残りの学会・年度もSPARQLを使います。著者順・DOI・CSCW号番号を保持し、Extended Abstracts等の除外条件も共通です。両方のAPIが利用できない場合は実行失敗です。OpenAlex / Crossrefの通信失敗やAbstract欠落はその論文をスキップできます。LLM失敗は初期設定ではスキップし、次の候補を試します。原文抜粋は翻訳を行わないため、英語の場合は「Abstract（原文抜粋）」と表示します。日本語生成文は100〜180文字を目安とし、上限を超える場合は文境界を優先して短縮します。

HTTP 429 / 5xx / 通信エラーを最大3回、1・2・4秒の間隔で再試行し、`Retry-After`があればその待ち時間を尊重します。恒久的な4xxは再試行しません。設定件数を配信できなかった場合は、既に成功した分の履歴を保ったうえで終了コード1を返します。

Slack送信後にDB保存が失敗すると実行失敗になります。Slack WebhookとDBの間にはトランザクションがないため、再実行時の重複を完全には防げません。Slackの実際の投稿を確認し、必要ならその論文の成功履歴を登録してから再実行してください。Webhookの応答を受信できず再試行した場合にも同様の制約があります。

APIエンドポイントは`collection.dblp_api_url`（検索API）と`collection.dblp_sparql_url`（SPARQL API）で設定できます。SPARQLは[DBLPの公式公開API](https://blog.dblp.org/2024/09/09/introducing-our-public-sparql-query-service/)です。追加のAPIキーは必要ありません。

## 検証

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

テストは外部API通信をモックし、フィルタ、ID生成、抽選、要約、Slack表示、APIのページング・再試行、送信とDB保存の順序・失敗を検証します。ORMとAlembicはローカルSQLiteで実行し、CIでは専用のPostgreSQLでも検証します。`TEST_DATABASE_URL`がないローカル環境ではPostgreSQLテストのみスキップします。実サービスへの投稿・課金は行いません。

`src/recommender/scorer.py`、`src/summarizer/base.py`、`PaperRepository`に差し替え用のインターフェースがあります。キーワード推薦・Embedding・Slackフィードバック・Web UIは今回のMVPの対象外です。

## API資料

- [DBLP Search API](https://dblp.org/faq/How+to+use+the+dblp+search+API.html)
- [DBLP CSCW目録](https://dblp.org/db/conf/cscw/index.html)
- [OpenAlex: DOIによる取得](https://help.openalex.org/api/get-single-entities/)、[認証](https://help.openalex.org/api/authentication/)
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [Supabase Data REST API](https://supabase.com/docs/guides/api)、[API keys](https://supabase.com/docs/guides/api/api-keys)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/en/20/orm/)、[Alembic](https://alembic.sqlalchemy.org/en/latest/)
- [OpenAI Responses APIによるテキスト生成](https://developers.openai.com/api/docs/guides/text)
- [Slack Incoming Webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)
