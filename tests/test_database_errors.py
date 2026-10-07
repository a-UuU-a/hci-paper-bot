import logging
from unittest.mock import patch

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import OperationalError

from src.database.migrate import check_database_connection, upgrade_database
from src.database.orm import create_database_engine
from src.errors import BotError
from src.main import main


class DriverFailure(Exception):
    def __init__(self, detail, sqlstate=None):
        super().__init__(detail)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    "detail,code,expected",
    [
        ("Network is unreachable", None, "Session pooler URI (port 5432)"),
        ("No route to host", None, "network unreachable"),
        ("FATAL: Tenant or user not found", None, "pooler user or host not found"),
        ("password authentication failed", None, "DB password"),
        ("unknown detail", "28P01", "authentication failed"),
        ("could not translate host name", None, "hostname could not be resolved"),
        ("connection timeout expired", None, "connection unavailable"),
        ("unknown detail", "42501", "permission denied"),
        ("unknown detail", "42P07", "schema mismatch"),
        ("unknown detail", None, "check DATABASE_URL, connection and schema"),
    ],
)
def test_migration_errors_explain_the_cause_without_exposing_credentials(detail, code, expected):
    engine = create_database_engine("sqlite://")
    failure = OperationalError(
        "private-sql-statement",
        {"password": "private-password"},
        DriverFailure(detail + " private-host private-password", code),
    )
    try:
        with patch.object(engine, "begin", side_effect=failure):
            with pytest.raises(BotError, match="Database migration failed") as exc:
                upgrade_database(engine)
        assert expected in str(exc.value)
        assert "private" not in str(exc.value)
        assert exc.value.__suppress_context__
    finally:
        engine.dispose()


def test_check_db_executes_only_select_and_does_not_migrate(tmp_path, monkeypatch, caplog):
    database_url = f"sqlite:///{tmp_path / 'connection-check.sqlite'}"
    engine = create_database_engine(database_url)
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, params, context, many: statements.append(statement),
    )
    monkeypatch.setattr("src.database.migrate.create_database_engine", lambda url: engine)
    with caplog.at_level(logging.INFO):
        check_database_connection(database_url)
    assert statements == ["SELECT 1"]
    assert "Database connection OK" in caplog.text
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_check_db_cli_requires_no_slack_or_openai_and_never_sends_http(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli-check.sqlite'}")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(
        "httpx.Client.request", lambda *args, **kwargs: pytest.fail("No HTTP request expected")
    )
    assert main(["--check-db"]) == 0


def test_check_db_failure_logs_only_safe_reason(monkeypatch, caplog):
    engine = create_database_engine("sqlite://")
    failure = OperationalError(
        None, None, DriverFailure("Network is unreachable; private-password private-host")
    )
    monkeypatch.setattr("src.database.migrate.create_database_engine", lambda url: engine)
    monkeypatch.setenv("DATABASE_URL", "private-url")
    try:
        with patch.object(engine, "connect", side_effect=failure), caplog.at_level(logging.ERROR):
            assert main(["--check-db"]) == 1
        assert "Session pooler URI (port 5432)" in caplog.text
        assert "private" not in caplog.text
    finally:
        engine.dispose()


@pytest.mark.parametrize("args", [["--init-db"], ["--dry-run"], ["--fixture", "unused.json"]])
def test_check_db_cannot_be_combined_with_delivery_or_migration_flags(args):
    with pytest.raises(SystemExit) as exc:
        main(["--check-db", *args])
    assert exc.value.code == 2
