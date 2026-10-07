# HCI Paper Recommendation Bot — 実装仕様書

## 1. プロジェクト概要

### 1.1 目的

HCI 系学会・国際会議に採択された論文の中から、毎日 1 本以上の論文を自動選択し、Slack に日本語の短い要約付きで投稿する Bot を構築する。

対象論文について、最低限以下を Slack に投稿する。

- 論文タイトル
- 著者
- 学会名
- 発表年
- 日本語の短い要約
- DOI または論文ページへのリンク

初期版では「未配信論文からランダムに選択」する。
将来的には、ユーザーの興味や Slack 上のフィードバックに基づく個人化推薦へ拡張する。

---

## 2. 対象学会

初期対象は以下とする。

### ACM 系

- CHI
- UIST
- DIS
- CSCW
- TEI
- IUI

### IEEE 系

- ISMAR
- IEEE VR

設定ファイルから対象学会の有効・無効を切り替えられるようにする。

---

## 3. システム全体構成

```text
                    GitHub Repository
                           |
                           v
                   GitHub Actions
                    毎朝定期実行
                           |
                           v
                    Python Application
                           |
              +------------+-------------+
              |                          |
              v                          v
            DBLP                      OpenAlex
      学会論文リスト取得          Abstract・Topic等補完
              |                          |
              +------------+-------------+
                           |
                           v
                    Paper Normalizer
                           |
                           v
                      Supabase
               論文・配信履歴を保存
                           |
                           v
                    Paper Selector
                           |
                           v
                 Japanese Summarizer
                           |
                           v
                 Slack Incoming Webhook
                           |
                           v
                      Slack Channel
```

MVP では常時稼働する Web サーバーを持たない。

GitHub Actions の cron により Python プログラムを定期実行することで、クラウド費用を可能な限り抑える。

---

## 4. 使用技術

| 項目 | 採用技術 |
|---|---|
| Language | Python 3.12 |
| Package / Environment Manager | uv |
| Scheduler | GitHub Actions |
| Paper Metadata | DBLP |
| Paper Metadata / Abstract | OpenAlex |
| Metadata補完 | Crossref |
| Database | Supabase PostgreSQL |
| Notification | Slack Incoming Webhook |
| Configuration | YAML |
| Testing | pytest |
| Lint | Ruff |
| Type Check | optional: mypy / pyright |
| LLM Summary | OpenAI API 等を後から接続可能な設計 |

---

# 5. Python 環境管理

## 5.1 uv を使用する

Python のパッケージ管理・仮想環境管理には `uv` を使用する。

`requirements.txt` は原則として使用せず、以下で管理する。

```text
pyproject.toml
uv.lock
```

初期化例：

```bash
uv init
```

依存関係追加例：

```bash
uv add httpx
uv add pydantic
uv add pyyaml
uv add supabase
```

開発用依存関係：

```bash
uv add --dev pytest
uv add --dev ruff
```

実行：

```bash
uv run python -m src.main
```

テスト：

```bash
uv run pytest
```

Lint：

```bash
uv run ruff check .
```

GitHub Actions 上でも `uv sync` を使用する。

---

# 6. Repository 構成

```text
hci-paper-bot/
|
├── README.md
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
|
├── config/
│   ├── venues.yaml
│   └── settings.yaml
|
├── src/
│   ├── __init__.py
│   ├── main.py
│   |
│   ├── models/
│   │   └── paper.py
│   |
│   ├── collectors/
│   │   ├── dblp.py
│   │   ├── openalex.py
│   │   └── crossref.py
│   |
│   ├── services/
│   │   ├── paper_service.py
│   │   └── normalization.py
│   |
│   ├── recommender/
│   │   ├── filter.py
│   │   ├── scorer.py
│   │   └── selector.py
│   |
│   ├── summarizer/
│   │   ├── base.py
│   │   ├── extractive.py
│   │   └── llm.py
│   |
│   ├── database/
│   │   └── supabase.py
│   |
│   └── notification/
│       └── slack.py
|
├── tests/
│   ├── test_filter.py
│   ├── test_selector.py
│   ├── test_normalization.py
│   └── test_summary.py
|
└── .github/
    └── workflows/
        ├── daily-paper.yml
        └── test.yml
```

---

# 7. 設定ファイル

## 7.1 `config/venues.yaml`

対象学会を管理する。

```yaml
venues:
  - id: chi
    name: CHI
    publisher: acm
    enabled: true
    weight: 1.0

  - id: uist
    name: UIST
    publisher: acm
    enabled: true
    weight: 1.0

  - id: dis
    name: DIS
    publisher: acm
    enabled: true
    weight: 1.0

  - id: cscw
    name: CSCW
    publisher: acm
    enabled: true
    weight: 1.0

  - id: tei
    name: TEI
    publisher: acm
    enabled: true
    weight: 1.0

  - id: iui
    name: IUI
    publisher: acm
    enabled: true
    weight: 1.0

  - id: ismar
    name: ISMAR
    publisher: ieee
    enabled: true
    weight: 1.0

  - id: ieee-vr
    name: IEEE VR
    publisher: ieee
    enabled: true
    weight: 1.0
```

将来的には DB または Web UI から変更できるようにする。

---

## 7.2 `config/settings.yaml`

```yaml
recommendation:
  papers_per_day: 1
  selection_method: random
  exclude_sent: true

publication:
  years_back: 5
  require_abstract: true

summary:
  language: ja
  max_chars: 180
  use_llm: true

slack:
  show_topics: true
  max_authors: 3
```

---

# 8. 論文データ取得方針

## 8.1 基本方針

ACM Digital Library や IEEE Xplore の HTML を直接スクレイピングすることは基本的に行わない。

論文探索には、学術メタデータ API を利用する。

---

## 8.2 DBLP

主に以下の情報を取得する。

- タイトル
- 著者
- Venue
- 発表年
- DOI
- 論文 URL

特に ACM 系学会について、

```text
CHI
UIST
DIS
CSCW
TEI
IUI
```

の「その年に発表された論文一覧」を取得する用途で利用する。

DBLP は書誌情報の取得を主目的とする。

---

## 8.3 OpenAlex

DBLP で取得した DOI またはタイトルを使って OpenAlex の Work と照合する。

主に以下を取得する。

- Abstract
- Topics
- Citation count
- Open Access URL
- DOI
- Publication year
- Authorship

特に要約生成用の Abstract 取得に使用する。

---

## 8.4 Crossref

OpenAlex または DBLP で情報が不足している場合の補完用途とする。

利用例：

- DOI の補完
- Publication metadata の確認
- 正式タイトル確認
- 著者情報補完

---

## 8.5 IEEE VR / ISMAR

MVP では OpenAlex および DBLP から取得可能な情報を優先して使用する。

必要になった場合、将来的に IEEE Xplore Metadata API を追加する。

MVP では IEEE API キーを必須にしない。

---

# 9. Paper データモデル

`src/models/paper.py`

```python
class Paper:
    id: str

    title: str
    authors: list[str]

    venue: str
    year: int

    abstract: str | None

    doi: str | None
    url: str | None

    citation_count: int | None
    topics: list[str]

    source: str
```

実際には Pydantic の `BaseModel` を利用する。

---

# 10. Paper ID

論文の一意識別子は以下の優先順位で生成する。

### 1. DOI

```text
doi:10.1145/xxxxxxx.xxxxxxx
```

### 2. DOI がない場合

以下から hash を生成する。

```text
title + year + first_author
```

例：

```text
sha256(normalized_title + year + first_author)
```

目的は重複登録・重複配信の防止である。

---

# 11. Paper 正規化

DBLP / OpenAlex / Crossref はそれぞれデータ形式が異なるため、取得後に内部 `Paper` モデルへ統一する。

```text
DBLP
    \
     \
OpenAlex ---> Normalizer ---> Paper
     /
    /
Crossref
```

正規化項目：

- タイトルの余分な空白除去
- DOI の小文字化
- DOI URL から DOI 部分だけ抽出
- 著者表記統一
- Venue 名統一
- URL 優先順位整理

---

# 12. Database

Supabase PostgreSQL を使用する。

## 12.1 `papers`

```sql
papers
---------------------------------
id
title
authors
abstract
venue
year
doi
url
citation_count
topics
created_at
updated_at
```

### 型イメージ

```text
id               text primary key
title            text not null
authors          jsonb
abstract         text
venue            text not null
year             integer not null
doi              text
url              text
citation_count   integer
topics           jsonb
created_at       timestamptz
updated_at       timestamptz
```

---

## 12.2 `recommendations`

```text
recommendations
---------------------------------
id
paper_id
sent_at
score
channel
status
```

例：

```text
paper_id          text references papers(id)
sent_at           timestamptz
score             double precision
channel           text
status            text
```

`status` は最低限以下を想定する。

```text
sent
failed
```

---

# 13. 推薦候補の生成

MVP では以下の順番でフィルタする。

```text
取得済み論文
    |
    v
enabled=true の学会のみ
    |
    v
対象年度内
    |
    v
Abstract あり
    |
    v
配信済み論文を除外
    |
    v
Candidate Papers
```

---

# 14. MVP の推薦アルゴリズム

初期版：

```text
未配信 Candidate Papers
          |
          v
       Random
          |
          v
      1 Paper
```

Python 側では推薦方法を差し替え可能にする。

```python
select_papers(
    papers,
    method="random",
    count=1,
)
```

`selector.py` と `scorer.py` を分離する。

---

# 15. 将来の推薦機能

MVP 完成後、以下の順で発展させる。

```text
Random
  |
  v
Keyword Matching
  |
  v
Embedding Similarity
  |
  v
Slack Feedback
  |
  v
Personalized Recommendation
```

将来的なスコア例：

```text
score =
    0.60 * semantic_similarity
  + 0.15 * topic_similarity
  + 0.10 * venue_weight
  + 0.10 * recency
  + 0.05 * feedback_score
```

初期実装ではこの処理は行わない。

---

# 16. 日本語要約

## 16.1 要件

Slack に表示する要約は日本語とする。

長さの目安：

```text
100〜180文字程度
```

可能な限り以下を含める。

1. 研究目的
2. 提案手法・システム
3. 評価方法または主要な結果

Abstract に存在しない情報を追加してはならない。

---

## 16.2 Summarizer Interface

要約部分は交換可能にする。

```python
class Summarizer:
    def summarize(self, paper: Paper) -> str: ...
```

実装：

```text
ExtractiveSummarizer
LLMSummarizer
```

---

## 16.3 MVP の要約方針

日本語で読みたいという要件を優先し、MVP から LLM 要約を利用する。

ただし API 障害・利用上限に備えて fallback を持つ。

```text
LLM Summary
     |
  success
     |
     v
Japanese Summary

     OR

LLM Error
     |
     v
Fallback
     |
     v
Abstract の簡易表示または投稿スキップ
```

MVP では「要約生成失敗時はその論文を Skip」でもよい。

---

# 17. LLM Prompt

基本プロンプト例：

```text
以下は HCI 分野の学術論文の Abstract です。

この論文を、日本語で簡潔に紹介してください。

要件:
- 100〜180文字程度
- 研究目的を含める
- 提案手法またはシステムを含める
- Abstract に評価・主要結果が書かれている場合は含める
- Abstract に書かれていない内容を推測しない
- 専門用語は必要に応じて英語表記を残す
- 「本論文では」から始める必要はない
- 宣伝調ではなく、中立的に記述する

Title:
{title}

Abstract:
{abstract}
```

---

# 18. Slack 投稿フォーマット

基本フォーマット：

```text
📄 *Paper Title*

👤 Alice Smith, Bob Jones, Carol Lee, et al.
🏛 *CHI 2025*

📝 *日本語要約*
VR 環境における〇〇を提案し、△△を用いたユーザ実験によって
その有効性を評価した。結果として〇〇への改善が示された。

🔗 https://doi.org/10.xxxx/xxxx

🏷 VR · Haptics · Interaction
```

著者数が `max_authors` より多い場合：

```text
Alice Smith, Bob Jones, Carol Lee, et al.
```

とする。

---

# 19. Slack 通知

MVP では Slack Incoming Webhook を利用する。

必要な Secret：

```text
SLACK_WEBHOOK_URL
```

Webhook URL は repository にコミットしない。

---

# 20. GitHub Actions

## 20.1 Daily Workflow

`.github/workflows/daily-paper.yml`

目的：

- 毎日指定時刻に Bot を実行
- 手動実行にも対応

例：

```yaml
name: Daily HCI Paper

on:
  schedule:
    - cron: "0 22 * * *"

  workflow_dispatch:

jobs:
  recommend:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v6

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync --frozen

      - name: Run HCI Paper Bot
        run: uv run python -m src.main
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

---

# 21. 実行時刻

GitHub Actions の cron は UTC 基準。

日本時間 07:00 に実行する場合：

```text
07:00 JST
=
22:00 UTC（前日）
```

そのため：

```yaml
cron: "0 22 * * *"
```

とする。

GitHub Actions の scheduled workflow は、必ずしも秒単位・分単位で厳密な時刻に開始されるとは限らない。

本システムでは数分程度の遅延は許容する。

---

# 22. GitHub Secrets

最低限以下を登録する。

```text
SLACK_WEBHOOK_URL
SUPABASE_URL
SUPABASE_KEY
OPENAI_API_KEY
```

将来的に追加する可能性があるもの：

```text
IEEE_API_KEY
```

Secrets はコード・ログに出力しない。

---

# 23. main.py の処理フロー

```text
START
 |
 v
設定ファイル読み込み
 |
 v
Supabase 接続
 |
 v
送信済み Paper IDs 取得
 |
 v
対象学会・対象年度を決定
 |
 v
DBLP / OpenAlex から論文取得
 |
 v
Paper 形式へ正規化
 |
 v
必要なら Supabase papers に保存
 |
 v
Filter
 |
 v
未送信 Candidate Papers
 |
 v
Paper Selector
 |
 v
日本語要約生成
 |
 v
Slack 投稿
 |
 v
recommendations に sent として保存
 |
 v
END
```

---

# 24. Slack 送信と DB 保存の順番

以下の順序とする。

```text
1. Paper 選択
2. Summary 生成
3. Slack 送信
4. 成功確認
5. recommendations に保存
```

DB を先に更新しない。

理由：

```text
DB: sent
Slack: failed
```

という不整合を避けるため。

---

# 25. エラー処理

## DBLP API 失敗

```text
Workflow failed
```

論文候補そのものが取得できないため。

---

## OpenAlex API 失敗

対象論文だけ Skip、または一定回数 Retry。

---

## Abstract が存在しない

MVP：

```text
Skip
```

将来的には Crossref などから補完する。

---

## LLM API 失敗

候補：

```text
Retry
↓
失敗
↓
Paper Skip
```

---

## Slack 投稿失敗

```text
recommendations に sent を保存しない
Workflow failed
```

---

## Supabase 保存失敗

Slack 投稿後に DB 保存が失敗した場合、GitHub Actions を失敗扱いとする。

将来的には idempotency を強化する。

---

# 26. Retry

HTTP 通信については以下を基本とする。

```text
Max retry: 3
Backoff:
1 sec
2 sec
4 sec
```

対象：

- DBLP
- OpenAlex
- Crossref
- Slack
- LLM API

---

# 27. Logging

GitHub Actions で確認可能なログを出力する。

例：

```text
[INFO] HCI Paper Bot started
[INFO] Enabled venues: CHI, UIST, DIS, CSCW, TEI, IUI, ISMAR, IEEE VR
[INFO] Publication range: 2022-2026
[INFO] Fetching papers from DBLP
[INFO] Found 1248 papers
[INFO] Enriching metadata using OpenAlex
[INFO] 982 papers have abstracts
[INFO] Removed 142 previously sent papers
[INFO] Candidate papers: 840
[INFO] Selected paper: Example Paper Title
[INFO] Japanese summary generated
[INFO] Slack message sent
[INFO] Recommendation saved
[INFO] Finished
```

以下は絶対にログに出さない。

```text
SLACK_WEBHOOK_URL
SUPABASE_KEY
OPENAI_API_KEY
```

---

# 28. Tests

最低限以下をテストする。

## Unit Tests

### Paper filtering

- 対象学会のみ残る
- 年度外を除外
- Abstract なしを除外
- 送信済み論文を除外

### Paper ID

- DOI が同じなら同一 ID
- DOI がなくても安定した ID が生成される

### Selection

- Candidate 0 件
- Candidate 1 件
- Candidate 複数件
- 指定件数を超えない

### Slack formatting

- Author が多い場合の `et al.`
- DOI URL
- 日本語要約
- Topic 表示

### Summary

- 最大長
- 空 Abstract
- LLM エラー

---

# 29. CI

`.github/workflows/test.yml`

Pull Request または push 時に実行。

```text
Checkout
  |
  v
Install uv
  |
  v
uv sync
  |
  v
ruff
  |
  v
pytest
```

---

# 30. MVP の完成条件

Version 0.1 の完成条件：

- [ ] uv で Python 環境を管理できる
- [ ] CHI / UIST / DIS / CSCW / TEI / IUI / ISMAR / IEEE VR を設定できる
- [ ] 過去 5 年分の対象論文を取得できる
- [ ] Title を取得できる
- [ ] Authors を取得できる
- [ ] Venue / Year を取得できる
- [ ] DOI または URL を取得できる
- [ ] Abstract を取得できる
- [ ] 配信済み論文を除外できる
- [ ] 未配信論文から 1 本選択できる
- [ ] Abstract を日本語で短く要約できる
- [ ] Slack に投稿できる
- [ ] Supabase に送信履歴を保存できる
- [ ] GitHub Actions から毎日自動実行できる
- [ ] GitHub Actions から手動実行できる
- [ ] API Key / Webhook が GitHub Secrets に保存されている
- [ ] pytest が通る
- [ ] Ruff が通る

---

# 31. 開発フェーズ

## Phase 1 — Local Prototype

```text
OpenAlex / DBLP
      |
      v
Python
      |
      v
Terminal
```

実装：

- uv setup
- Paper model
- DBLP / OpenAlex access
- CHI の論文取得
- Terminal に表示

---

## Phase 2 — Slack Bot

```text
Paper
 |
 v
Japanese Summary
 |
 v
Slack
```

実装：

- LLM summary
- Slack Incoming Webhook
- Slack formatting

---

## Phase 3 — Database

```text
Paper
 |
 v
Supabase
 |
 v
Duplicate prevention
```

実装：

- papers
- recommendations
- sent filter

---

## Phase 4 — GitHub Actions

```text
GitHub Actions
      |
      v
Daily execution
      |
      v
Slack
```

実装：

- cron
- workflow_dispatch
- GitHub Secrets
- uv in GitHub Actions

ここで MVP 完成。

---

## Phase 5 — Recommendation

追加：

- Keyword preference
- Topic weighting
- Embeddings
- Cosine similarity

---

## Phase 6 — Feedback

Slack に以下を追加：

```text
👍 Interested
👎 Not interested
⭐ Save
```

この段階では Slack Incoming Webhook だけでは足りないため、Slack App と callback endpoint を追加する。

---

## Phase 7 — Web UI

Next.js などで設定画面を作る。

例：

```text
HCI Paper Recommender

Conferences

[x] CHI
[x] UIST
[x] DIS
[x] CSCW
[x] TEI
[x] IUI
[x] ISMAR
[x] IEEE VR

Interests

VR               0.9
Haptics          0.9
Robotics         0.8
Accessibility    0.4

Papers per day

1

[Save]
```

---

# 32. Version Roadmap

| Version | 内容 |
|---|---|
| v0.1 | GitHub Actions + Slack + Random Paper |
| v0.2 | 複数学会・年度フィルタ |
| v0.3 | 日本語 LLM 要約 |
| v0.4 | Supabase 履歴管理 |
| v0.5 | Keyword Recommendation |
| v0.6 | Embedding Recommendation |
| v0.7 | Slack Feedback |
| v0.8 | User Preference |
| v0.9 | Web UI |
| v1.0 | Personalized HCI Paper Recommender |

---

# 33. MVP の最終仕様

> CHI、UIST、DIS、CSCW、TEI、IUI、ISMAR、IEEE VR の過去 5 年の論文を対象とし、毎日 07:00 JST 頃に未配信論文から 1 本を選択する。
>
> DBLP を主に学会論文一覧の取得に利用し、OpenAlex を利用して Abstract、Topic 等のメタデータを補完する。不足情報については必要に応じて Crossref を利用する。
>
> 選択した論文について、タイトル、著者、学会名、発表年、100〜180文字程度の日本語要約、DOI または論文 URL を Slack に投稿する。
>
> 配信済み論文および論文メタデータは Supabase PostgreSQL で管理し、同じ論文を原則として再配信しない。
>
> Python の依存関係および仮想環境は uv で管理し、GitHub Actions 上でも uv を使用する。
>
> GitHub Actions の scheduled workflow により毎日自動実行し、workflow_dispatch により手動実行も可能とする。
>
> API キー、Slack Webhook URL、Supabase 認証情報等は GitHub Secrets で管理する。
>
> 初期推薦方式はランダム選択とし、将来的にキーワード、Embedding、Slack フィードバックを利用した個人化推薦へ拡張可能なモジュール構成とする。

---

# 34. 最初に実装する最小タスク

最初の開発目標は以下とする。

```text
1. uv でプロジェクトを作る

2. CHI 2025 の論文一覧を取得する

3. 1 本選ぶ

4. OpenAlex から Abstract を取得する

5. 日本語に要約する

6. Terminal に表示する

7. Slack に送る

8. GitHub Actions から手動実行する

9. cron で毎日実行する

10. Supabase で重複配信を防ぐ
```

この順番で進めることで、システム全体を早い段階で end-to-end に動作させる。
