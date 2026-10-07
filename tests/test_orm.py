import logging
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from src.bot import run_bot
from src.config import Settings
from src.database.migrate import upgrade_database
from src.database.models import Base, PaperRecord, RecommendationRecord
from src.database.orm import SQLAlchemyRepository, create_database_engine
from src.errors import BotError
from src.summarizer.extractive import ExtractiveSummarizer


@pytest.fixture
def repository(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'test.sqlite'}")
    upgrade_database(engine)
    yield SQLAlchemyRepository(engine, max_retries=0)
    engine.dispose()


def test_migration_creates_expected_schema_and_is_repeatable(repository):
    engine = repository.engine
    assert {"papers", "recommendations", "alembic_version"} <= set(
        inspect(engine).get_table_names()
    )
    upgrade_database(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "0001"
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_paper_round_trip_and_idempotent_upsert(repository, paper_factory):
    paper = paper_factory(topics=["VR", "Haptics"], citation_count=5)
    repository.upsert_papers([paper, paper])
    assert repository.get_papers(2025, 2025) == [paper]
    assert repository.get_papers(2022, 2024) == []
    updated = paper.model_copy(update={"citation_count": 6, "abstract": "Updated abstract."})
    repository.upsert_papers([updated])
    assert repository.get_papers(2025, 2025) == [updated]


def test_orm_history_excludes_failed_and_relationship_is_available(repository, paper_factory):
    sent = paper_factory()
    failed = paper_factory(doi="10.1145/failed")
    repository.upsert_papers([sent, failed])
    repository.record_recommendation(sent.id, channel="hci", status="sent")
    repository.record_recommendation(failed.id, channel="hci", status="failed")
    assert repository.get_sent_ids() == {sent.id}
    with Session(repository.engine) as session:
        row = session.get(PaperRecord, sent.id)
        assert row.recommendations[0].status == "sent"
        assert row.recommendations[0].sent_at is not None
        failed_history = session.scalars(
            select(RecommendationRecord).where(RecommendationRecord.paper_id == failed.id)
        ).one()
        assert failed_history.sent_at is None


def test_large_result_is_not_limited_to_rest_page_size(repository, paper_factory):
    papers = [paper_factory(doi=f"10.1145/{i}") for i in range(1001)]
    repository.upsert_papers(papers)
    assert len(repository.get_papers(2025, 2025)) == 1001


def test_metadata_batches_roll_back_together(repository, paper_factory):
    repository.UPSERT_BATCH_SIZE = 1
    calls = []

    def fail_second_batch(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO papers"):
            calls.append(statement)
            if len(calls) == 2:
                raise IntegrityError(statement, parameters, Exception("private details"))

    event.listen(repository.engine, "before_cursor_execute", fail_second_batch)
    try:
        with pytest.raises(BotError, match="Database operation failed"):
            repository.upsert_papers([paper_factory(), paper_factory(doi="10.1145/other")])
    finally:
        event.remove(repository.engine, "before_cursor_execute", fail_second_batch)
    assert repository.get_papers(2025, 2025) == []


def test_invalid_history_is_rejected_and_rolled_back(repository, paper_factory):
    repository.upsert_papers([paper_factory()])
    with pytest.raises(BotError):
        repository.record_recommendation("missing-paper", channel="hci", status="sent")
    assert repository.get_sent_ids() == set()


def test_transient_connection_failure_retries_without_exposing_secrets(repository, caplog):
    sleeps = []
    retrying = SQLAlchemyRepository(repository.engine, max_retries=3, sleep=sleeps.append)
    with patch.object(
        retrying.sessions,
        "begin",
        side_effect=OperationalError("private password", None, Exception("private hostname")),
    ):
        with caplog.at_level(logging.WARNING), pytest.raises(BotError) as exc:
            retrying.get_sent_ids()
    assert sleeps == [1, 2, 4]
    assert "private" not in caplog.text + str(exc.value)


def test_postgresql_url_selects_psycopg_and_preserves_encoded_password():
    engine = create_database_engine("postgresql://postgres:pass%40word@localhost/postgres")
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.password == "pass@word"
        assert engine.url.query["sslmode"] == "require"
    finally:
        engine.dispose()


def test_invalid_database_url_returns_safe_error():
    with pytest.raises(BotError) as exc:
        create_database_engine("invalid-url-containing-secret")
    assert "containing-secret" not in str(exc.value)


def test_existing_initial_schema_and_data_are_adopted_without_recreation(tmp_path, paper_factory):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'old.sqlite'}")
    try:
        Base.metadata.create_all(engine)
        repository = SQLAlchemyRepository(engine, max_retries=0)
        repository.upsert_papers([paper_factory()])
        repository.record_recommendation(paper_factory().id, channel="hci", status="sent")
        upgrade_database(engine)
        assert repository.get_sent_ids() == {paper_factory().id}
        assert repository.get_papers(2025, 2025) == [paper_factory()]
    finally:
        engine.dispose()


def test_incomplete_existing_schema_is_not_stamped(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'incomplete.sqlite'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("create table papers (id text primary key)"))
        with pytest.raises(BotError, match="Incomplete existing database"):
            upgrade_database(engine)
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_real_orm_bot_pipeline_does_not_resend(repository, paper_factory, venues):
    paper = paper_factory()
    delivered = []

    class Service:
        def collect(self, venues, years):
            return [paper]

        def enrich(self, candidate):
            return candidate

    class Notifier:
        def send(self, candidate, summary):
            assert repository.get_sent_ids() == set()
            delivered.append(candidate.id)

    kwargs = dict(
        settings=Settings(),
        venues=venues,
        service=Service(),
        repository=repository,
        summarizer=ExtractiveSummarizer(),
        notifier=Notifier(),
        year=2026,
    )
    assert run_bot(**kwargs).delivered == 1
    assert repository.get_sent_ids() == {paper.id}
    with pytest.raises(BotError, match="Only 0/1"):
        run_bot(**kwargs)
    assert delivered == [paper.id]


def test_retry_after_uncertain_commit_does_not_duplicate_history(repository, paper_factory):
    paper = paper_factory()
    repository.upsert_papers([paper])

    # Simulate the retry wrapper invoking the same write after an already successful commit.
    def execute_twice(operation):
        for _ in range(2):
            with repository.sessions.begin() as session:
                operation(session)

    with patch.object(repository, "_transaction", execute_twice):
        repository.record_recommendation(paper.id, channel="hci", status="sent")
    with Session(repository.engine) as session:
        assert len(session.scalars(select(RecommendationRecord)).all()) == 1


def test_history_check_constraint_requires_sent_timestamp(repository, paper_factory):
    repository.upsert_papers([paper_factory()])
    with Session(repository.engine) as session:
        session.add(
            RecommendationRecord(
                paper_id=paper_factory().id,
                channel="hci",
                status="failed",
                sent_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
