import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from src.database.migrate import upgrade_database
from src.database.models import PaperRecord, RecommendationRecord
from src.database.orm import SQLAlchemyRepository, create_database_engine
from src.errors import BotError


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL integration runs in CI")
def test_postgresql_migration_upsert_and_history(paper_factory):
    engine = create_database_engine(os.environ["TEST_DATABASE_URL"])
    # This test uses only the explicitly named test database, never DATABASE_URL.
    assert engine.url.database == "hci_paper_bot_test"
    token = uuid4().hex
    paper = paper_factory(doi=f"10.1145/test-{token}", topics=["VR", "触覚"], citation_count=7)
    try:
        upgrade_database(engine)
        upgrade_database(engine)
        repository = SQLAlchemyRepository(engine, max_retries=0)
        repository.upsert_papers([paper])
        with Session(engine) as session:
            original_created_at = session.get(PaperRecord, paper.id).created_at
        updated = paper.model_copy(update={"citation_count": 8})
        repository.upsert_papers([updated])
        assert updated in repository.get_papers(2025, 2025)
        repository.record_recommendation(paper.id, channel="ci", status="failed")
        assert paper.id not in repository.get_sent_ids()
        repository.record_recommendation(paper.id, channel="ci", status="sent")
        assert paper.id in repository.get_sent_ids()
        with Session(engine) as session:
            row = session.get(PaperRecord, paper.id)
            assert row.created_at == original_created_at
            assert row.updated_at >= row.created_at
            assert len(row.recommendations) == 2
            assert (
                session.scalar(
                    text(
                        "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.papers'::regclass"
                    )
                )
                is True
            )
            assert (
                session.scalar(
                    select(RecommendationRecord.sent_at).where(
                        RecommendationRecord.paper_id == paper.id,
                        RecommendationRecord.status == "sent",
                    )
                )
                is not None
            )
        with pytest.raises(BotError):
            repository.record_recommendation(f"missing-{token}", channel="ci", status="sent")
    finally:
        # Delete only rows created by this test, in foreign-key order.
        with engine.begin() as connection:
            connection.execute(
                delete(RecommendationRecord).where(RecommendationRecord.paper_id == paper.id)
            )
            connection.execute(delete(PaperRecord).where(PaperRecord.id == paper.id))
        engine.dispose()
