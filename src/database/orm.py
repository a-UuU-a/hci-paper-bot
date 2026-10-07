import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, TypeVar
from uuid import uuid4

from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.database.models import PaperRecord, RecommendationRecord
from src.errors import BotError
from src.models.paper import Paper

logger = logging.getLogger(__name__)
T = TypeVar("T")


def create_database_engine(database_url: str) -> Engine:
    if not database_url:
        raise BotError("DATABASE_URL is required; copy the Supabase Session pooler connection URI")
    try:
        url = make_url(database_url)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+psycopg")
        if url.drivername not in {"postgresql+psycopg", "sqlite", "sqlite+pysqlite"}:
            raise BotError("DATABASE_URL must use PostgreSQL or SQLite")
        kwargs = {"echo": False, "hide_parameters": True, "pool_pre_ping": True}
        if url.get_backend_name() == "postgresql":
            kwargs["connect_args"] = {"connect_timeout": 30}
            if "sslmode" not in url.query:
                url = url.update_query_dict({"sslmode": "require"})
        engine = create_engine(url, **kwargs)
        if url.get_backend_name() == "sqlite":

            @event.listens_for(engine, "connect")
            def enable_foreign_keys(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        return engine
    except (SQLAlchemyError, ValueError, TypeError):
        raise BotError(
            "Invalid DATABASE_URL; check the connection URI and password encoding"
        ) from None


class SQLAlchemyRepository:
    """SQLAlchemy ORM repository, supporting Supabase PostgreSQL and local SQLite."""

    UPSERT_BATCH_SIZE = 500

    def __init__(
        self, engine: Engine, *, max_retries: int = 3, sleep: Callable[[float], None] = time.sleep
    ):
        self.engine = engine
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        self.max_retries = max_retries
        self.sleep = sleep

    def _transaction(self, operation: Callable[[Session], T]) -> T:
        for attempt in range(self.max_retries + 1):
            try:
                with self.sessions.begin() as session:
                    return operation(session)
            except OperationalError:
                if attempt == self.max_retries:
                    raise BotError(
                        "Database connection failed; check DATABASE_URL and network"
                    ) from None
                logger.warning(
                    "Database temporarily unavailable; retry %d/%d", attempt + 1, self.max_retries
                )
                self.sleep(2**attempt)
            except SQLAlchemyError:
                raise BotError(
                    "Database operation failed; run --init-db and check the schema"
                ) from None
        raise AssertionError("unreachable")

    def _insert(self, model):
        return (
            postgres_insert(model)
            if self.engine.dialect.name == "postgresql"
            else sqlite_insert(model)
        )

    def get_sent_ids(self) -> set[str]:
        return self._transaction(
            lambda session: set(
                session.scalars(
                    select(RecommendationRecord.paper_id).where(
                        RecommendationRecord.status == "sent"
                    )
                )
            )
        )

    def get_papers(self, start_year: int, end_year: int) -> list[Paper]:
        def read(session):
            rows = session.scalars(
                select(PaperRecord)
                .where(PaperRecord.year.between(start_year, end_year))
                .order_by(PaperRecord.id)
            )
            return [
                Paper.model_validate({field: getattr(row, field) for field in Paper.model_fields})
                for row in rows
            ]

        return self._transaction(read)

    def upsert_papers(self, papers: list[Paper]) -> None:
        # One transaction covers all batches, avoiding partially written metadata.
        def write(session):
            unique = list({paper.id: paper for paper in papers}.values())
            for offset in range(0, len(unique), self.UPSERT_BATCH_SIZE):
                statement = self._insert(PaperRecord).values(
                    [
                        paper.model_dump(mode="json")
                        for paper in unique[offset : offset + self.UPSERT_BATCH_SIZE]
                    ]
                )
                values = {
                    field: getattr(statement.excluded, field)
                    for field in Paper.model_fields
                    if field != "id"
                }
                values["updated_at"] = func.now()
                session.execute(statement.on_conflict_do_update(index_elements=["id"], set_=values))

        if papers:
            self._transaction(write)

    def record_recommendation(
        self, paper_id: str, *, channel: str, status: Literal["sent", "failed"], score: float = 0
    ) -> None:
        row = {
            "id": uuid4(),
            "paper_id": paper_id,
            "channel": channel,
            "status": status,
            "score": score,
            "sent_at": datetime.now(UTC) if status == "sent" else None,
        }

        # The same UUID survives connection retries, including an uncertain commit.
        def write(session):
            statement = self._insert(RecommendationRecord).values(**row)
            session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))

        self._transaction(write)
