import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.database.models import Base
from src.database.orm import create_database_engine
from src.errors import BotError

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def upgrade_database(engine: Engine) -> None:
    try:
        with engine.begin() as connection:
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            if "alembic_version" not in tables and {"papers", "recommendations"} & tables:
                # Adopt the original SQL migration only when both tables have the expected columns.
                if not set(Base.metadata.tables) <= tables:
                    raise BotError("Incomplete existing database; check papers and recommendations")
                for name, table in Base.metadata.tables.items():
                    columns = {column["name"] for column in inspector.get_columns(name)}
                    if not set(table.columns.keys()) <= columns:
                        raise BotError(
                            "Existing database schema differs; check columns before migration"
                        )
                command.stamp(config, "0001")
                logger.info("Existing paper database adopted by Alembic")
            command.upgrade(config, "head")
    except (SQLAlchemyError, CommandError):
        raise BotError(
            "Database migration failed; check DATABASE_URL, connection and schema"
        ) from None


def migrate_database(database_url: str) -> None:
    engine = create_database_engine(database_url)
    try:
        upgrade_database(engine)
        logger.info("Database schema is up to date")
    finally:
        engine.dispose()
