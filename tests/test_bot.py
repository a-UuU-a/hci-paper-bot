import random

import pytest

from src.bot import run_bot
from src.config import Settings
from src.database.supabase import MemoryRepository
from src.errors import BotError, SummaryError
from src.services.normalization import normalize_paper
from src.summarizer.extractive import ExtractiveSummarizer


class FakeService:
    def __init__(self, papers):
        self.papers = papers
        self.years = []
        self.enriched = []

    def collect(self, venues, years):
        self.years = list(years)
        return self.papers

    def enrich(self, paper):
        self.enriched.append(paper.id)
        return paper


class RecordingRepository(MemoryRepository):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def record_recommendation(self, paper_id, *, status, **kwargs):
        self.events.append(status)
        super().record_recommendation(paper_id, status=status, **kwargs)


class RecordingNotifier:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail

    def send(self, paper, summary):
        self.events.append("slack")
        if self.fail:
            raise BotError("Slack delivery failed")


def test_sends_slack_before_saving_sent_history(paper_factory, venues):
    events = []
    repository = RecordingRepository(events)
    service = FakeService([paper_factory()])
    result = run_bot(
        settings=Settings(),
        venues=venues,
        service=service,
        repository=repository,
        summarizer=ExtractiveSummarizer(),
        notifier=RecordingNotifier(events),
        year=2026,
    )
    assert events == ["slack", "sent"]
    assert result.delivered == 1
    assert service.years == [2022, 2023, 2024, 2025, 2026]
    assert repository.get_sent_ids() == {paper_factory().id}


def test_slack_failure_does_not_mark_sent(paper_factory, venues):
    events = []
    repository = RecordingRepository(events)
    with pytest.raises(BotError, match="Slack delivery failed"):
        run_bot(
            settings=Settings(),
            venues=venues,
            service=FakeService([paper_factory()]),
            repository=repository,
            summarizer=ExtractiveSummarizer(),
            notifier=RecordingNotifier(events, fail=True),
            year=2026,
        )
    assert events == ["slack", "failed"]
    assert repository.get_sent_ids() == set()


def test_database_failure_after_delivery_fails_with_clear_message(paper_factory, venues):
    events = []

    class FailingRepository(RecordingRepository):
        def record_recommendation(self, *args, **kwargs):
            raise BotError("Supabase: HTTP 503")

    with pytest.raises(BotError, match="Slack message sent, but history save failed"):
        run_bot(
            settings=Settings(),
            venues=venues,
            service=FakeService([paper_factory()]),
            repository=FailingRepository(events),
            summarizer=ExtractiveSummarizer(),
            notifier=RecordingNotifier(events),
            year=2026,
        )
    assert events == ["slack"]


def test_summary_failure_tries_next_candidate(paper_factory, venues):
    calls = []

    class FailingOnceSummarizer:
        def summarize(self, paper):
            calls.append(paper.id)
            if len(calls) == 1:
                raise SummaryError("LLM unavailable")
            return "VRの操作方法を評価した。"

    papers = [paper_factory(doi=f"10.1145/{i}", title=f"Paper {i}") for i in range(3)]
    events = []
    result = run_bot(
        settings=Settings(),
        venues=venues,
        service=FakeService(papers),
        repository=RecordingRepository(events),
        summarizer=FailingOnceSummarizer(),
        notifier=RecordingNotifier(events),
        year=2026,
        rng=random.Random(0),
    )
    assert result.delivered == 1 and result.skipped == 1
    assert len(calls) == 2 and events == ["slack", "sent"]


def test_previous_abstract_is_not_erased_by_new_dblp_data(paper_factory, venues, capsys):
    cached = paper_factory(abstract="保存済みのAbstractから目的と手法を紹介する。")
    repository = MemoryRepository()
    repository.upsert_papers([cached])
    run_bot(
        settings=Settings(),
        venues=venues,
        service=FakeService([paper_factory(abstract=None)]),
        repository=repository,
        summarizer=ExtractiveSummarizer(),
        dry_run=True,
        year=2026,
    )
    assert repository.papers[cached.id].abstract == cached.abstract
    assert cached.abstract in capsys.readouterr().out
    assert repository.recommendations == []


def test_new_doi_does_not_resend_previously_hashed_paper(paper_factory, venues):
    old = paper_factory(doi=None)
    repository = MemoryRepository()
    repository.upsert_papers([old])
    repository.record_recommendation(old.id, channel="hci", status="sent")
    events = []
    with pytest.raises(BotError, match="Only 0/1"):
        run_bot(
            settings=Settings(),
            venues=venues,
            service=FakeService([paper_factory()]),
            repository=repository,
            summarizer=ExtractiveSummarizer(),
            notifier=RecordingNotifier(events),
            year=2026,
        )
    assert events == []


def test_enrichment_new_doi_is_checked_against_sent_history(paper_factory, venues):
    sent = paper_factory()
    raw = paper_factory(title="DBLP title variant", doi=None, abstract=None)
    repository = MemoryRepository()
    repository.upsert_papers([sent])
    repository.record_recommendation(sent.id, channel="hci", status="sent")

    class LateDOIService(FakeService):
        def enrich(self, paper):
            data = paper.model_dump()
            data.update(doi=sent.doi, abstract="Enriched abstract")
            return normalize_paper(**data)

    events = []
    with pytest.raises(BotError, match="Only 0/1"):
        run_bot(
            settings=Settings(),
            venues=venues,
            service=LateDOIService([raw]),
            repository=repository,
            summarizer=ExtractiveSummarizer(),
            notifier=RecordingNotifier(events),
            year=2026,
        )
    assert events == []


def test_multiple_papers_and_partial_failure_keep_success_history(paper_factory, venues):
    settings = Settings()
    settings.recommendation.papers_per_day = 2
    papers = [
        paper_factory(title="Valid paper"),
        paper_factory(title="No abstract", doi="10.1145/no-abstract", abstract=None),
    ]
    events = []
    repository = RecordingRepository(events)
    with pytest.raises(BotError, match="Only 1/2"):
        run_bot(
            settings=settings,
            venues=venues,
            service=FakeService(papers),
            repository=repository,
            summarizer=ExtractiveSummarizer(),
            notifier=RecordingNotifier(events),
            year=2026,
        )
    assert repository.get_sent_ids() == {papers[0].id}
    assert events == ["slack", "sent"]


def test_hash_and_doi_aliases_have_one_chance_in_random_draw(paper_factory, venues):
    repository = MemoryRepository()
    repository.upsert_papers([paper_factory(doi=None)])
    result = run_bot(
        settings=Settings(),
        venues=venues,
        service=FakeService([paper_factory()]),
        repository=repository,
        summarizer=ExtractiveSummarizer(),
        dry_run=True,
        year=2026,
    )
    assert result.candidates == 1
    assert result.delivered == 1
