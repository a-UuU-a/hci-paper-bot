import argparse
import json
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from src.bot import run_bot
from src.collectors.crossref import CrossrefCollector
from src.collectors.dblp import DBLPCollector
from src.collectors.openalex import OpenAlexCollector
from src.config import DEFAULT_CONFIG_DIR, Credentials, apply_runtime_overrides, load_config
from src.database.migrate import check_database_connection, migrate_database
from src.database.orm import SQLAlchemyRepository, create_database_engine
from src.database.supabase import MemoryRepository, SupabaseRepository
from src.errors import BotError
from src.http import RetryingHTTPClient
from src.models.paper import Paper
from src.notification.slack import SlackNotifier
from src.services.paper_service import PaperService
from src.summarizer.base import FallbackSummarizer
from src.summarizer.extractive import ExtractiveSummarizer
from src.summarizer.llm import LLMSummarizer

logger = logging.getLogger(__name__)


class FixtureService:
    def __init__(self, path: Path):
        try:
            self.papers = [Paper.model_validate(row) for row in json.loads(path.read_text())]
        except (OSError, ValueError, TypeError):
            raise BotError("Invalid paper fixture JSON") from None

    def collect(self, venues, years):
        enabled = {venue.name for venue in venues if venue.enabled}
        return [paper for paper in self.papers if paper.venue in enabled and paper.year in years]

    def enrich(self, paper: Paper) -> Paper:
        return paper


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily HCI paper recommendation bot")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview; no Slack or Supabase writes"
    )
    parser.add_argument("--fixture", type=Path, help="Use local paper JSON (requires --dry-run)")
    parser.add_argument("--no-llm", action="store_true", help="Show original abstract excerpts")
    parser.add_argument("--year", type=int, help="End year for the publication window")
    database_commands = parser.add_mutually_exclusive_group()
    database_commands.add_argument(
        "--init-db",
        action="store_true",
        help="Apply database migrations using DATABASE_URL, then exit",
    )
    database_commands.add_argument(
        "--check-db",
        action="store_true",
        help="Check DATABASE_URL connectivity without migrations or data writes, then exit",
    )
    args = parser.parse_args(argv)
    if args.fixture and not args.dry_run:
        parser.error("--fixture requires --dry-run")
    if args.init_db and (args.dry_run or args.fixture):
        parser.error("--init-db cannot be combined with --dry-run or --fixture")
    if args.check_db and (args.dry_run or args.fixture):
        parser.error("--check-db cannot be combined with --dry-run or --fixture")
    if args.year is not None and not 1900 <= args.year <= 2200:
        parser.error("--year must be between 1900 and 2200")
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    # httpx's INFO output includes request URLs; Slack URLs are secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    load_dotenv()
    engine = None
    try:
        credentials = Credentials.from_env()
        if args.check_db:
            check_database_connection(credentials.database_url.get_secret_value())
            return 0
        if args.init_db:
            migrate_database(credentials.database_url.get_secret_value())
            return 0
        settings, venues = load_config(args.config_dir)
        settings, venues, year = apply_runtime_overrides(settings, venues, year=args.year)
        if args.no_llm:
            settings.summary.use_llm = False
        credentials.validate_for_run(settings, dry_run=args.dry_run)
        with httpx.Client(
            timeout=settings.http.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "hci-paper-bot/0.1"},
        ) as client:
            http = RetryingHTTPClient(client, max_retries=settings.http.max_retries)
            service = (
                FixtureService(args.fixture)
                if args.fixture
                else PaperService(
                    DBLPCollector(http, settings.collection),
                    OpenAlexCollector(
                        http,
                        credentials.openalex_api_key.get_secret_value(),
                        credentials.metadata_email,
                    ),
                    CrossrefCollector(http, credentials.metadata_email)
                    if settings.collection.use_crossref
                    else None,
                )
            )
            if args.dry_run:
                repository = MemoryRepository()
            elif credentials.database_url.get_secret_value():
                engine = create_database_engine(credentials.database_url.get_secret_value())
                repository = SQLAlchemyRepository(engine, max_retries=settings.http.max_retries)
                logger.info("Database: SQLAlchemy ORM")
            else:
                repository = SupabaseRepository(
                    http, credentials.supabase_url, credentials.supabase_key.get_secret_value()
                )
                logger.info("Database: Supabase REST")
            extractive = ExtractiveSummarizer(settings.summary.max_chars)
            summarizer = (
                FallbackSummarizer(
                    LLMSummarizer(
                        http,
                        credentials.openai_api_key.get_secret_value(),
                        credentials.openai_model,
                        settings.summary.max_chars,
                    ),
                    extractive if settings.summary.fallback == "extractive" else None,
                )
                if settings.summary.use_llm
                else extractive
            )
            notifier = (
                None
                if args.dry_run
                else SlackNotifier(
                    http, credentials.slack_webhook_url.get_secret_value(), settings.slack
                )
            )
            run_bot(
                settings=settings,
                venues=venues,
                service=service,
                repository=repository,
                summarizer=summarizer,
                notifier=notifier,
                dry_run=args.dry_run,
                year=year,
            )
    except BotError as exc:
        logger.error("%s", exc)
        return 1
    except (ValidationError, httpx.HTTPError, ValueError, TypeError, KeyError):
        logger.error("Invalid data or connection settings; check configuration and API responses")
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
