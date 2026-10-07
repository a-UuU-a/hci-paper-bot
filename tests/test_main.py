from pathlib import Path

import pytest

from src.config import Credentials, Settings, load_config
from src.database.orm import SQLAlchemyRepository, create_database_engine
from src.errors import BotError
from src.main import main


def test_default_configuration_has_eight_venues():
    settings, venues = load_config()
    assert len(venues) == 8
    assert {venue.name for venue in venues} == {
        "CHI",
        "UIST",
        "DIS",
        "CSCW",
        "TEI",
        "IUI",
        "ISMAR",
        "IEEE VR",
    }
    assert all(venue.enabled for venue in venues)
    assert settings.publication.years_back == 5


def test_missing_credentials_are_detected_without_showing_values():
    with pytest.raises(BotError, match="SLACK_WEBHOOK_URL"):
        Credentials().validate_for_run(Settings(), dry_run=False)
    credentials = Credentials(openai_api_key="secret-value")
    assert "secret-value" not in repr(credentials)


def test_invalid_config_does_not_leak_input(tmp_path):
    (tmp_path / "settings.yaml").write_text("unexpected: secret-value\n")
    (tmp_path / "venues.yaml").write_text("venues: []\n")
    with pytest.raises(BotError) as exc:
        load_config(tmp_path)
    assert "secret-value" not in str(exc.value)


def test_offline_preview_without_keys_or_writes(monkeypatch, capsys):
    def forbidden_request(*args, **kwargs):
        pytest.fail("Offline preview must not make HTTP requests")

    monkeypatch.setattr("httpx.Client.request", forbidden_request)
    fixture = Path(__file__).resolve().parent.parent / "examples" / "papers.json"
    assert main(["--dry-run", "--no-llm", "--fixture", str(fixture), "--year", "2025"]) == 0
    assert "触覚フィードバック" in capsys.readouterr().out


def test_fixture_cannot_be_sent_to_slack():
    with pytest.raises(SystemExit) as exc:
        main(["--fixture", "examples/papers.json"])
    assert exc.value.code == 2


def test_orm_credentials_only_need_database_url_slack_and_openai():
    Credentials(
        database_url="postgresql://user:password@host/db",
        slack_webhook_url="https://example.org/webhook",
        openai_api_key="test",
    ).validate_for_run(Settings(), dry_run=False)
    assert "password" not in repr(Credentials(database_url="postgresql://user:password@host/db"))


def test_init_db_does_not_require_slack_or_openai(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bot.sqlite'}")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert main(["--init-db"]) == 0
    assert (tmp_path / "bot.sqlite").exists()


def test_init_db_cannot_be_combined_with_dry_run():
    with pytest.raises(SystemExit) as exc:
        main(["--init-db", "--dry-run"])
    assert exc.value.code == 2


def test_cli_selects_orm_without_rest_credentials(monkeypatch, tmp_path, paper_factory):
    database_url = f"sqlite:///{tmp_path / 'cli.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.org/fake-webhook")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert main(["--init-db"]) == 0
    paper = paper_factory()
    delivered = []

    class Service:
        def collect(self, venues, years):
            return [paper]

        def enrich(self, candidate):
            return candidate

    class Notifier:
        def send(self, candidate, summary):
            delivered.append(candidate.id)

    monkeypatch.setattr("src.main.PaperService", lambda *args: Service())
    monkeypatch.setattr("src.main.SlackNotifier", lambda *args: Notifier())
    monkeypatch.setattr(
        "httpx.Client.request",
        lambda *args, **kwargs: pytest.fail("No external HTTP request is expected"),
    )
    assert main(["--no-llm", "--year", "2026"]) == 0
    assert main(["--no-llm", "--year", "2026"]) == 1
    assert delivered == [paper.id]
    engine = create_database_engine(database_url)
    try:
        assert SQLAlchemyRepository(engine).get_sent_ids() == {paper.id}
    finally:
        engine.dispose()


def test_runtime_filters_limit_collection_and_slack_including_cached_papers(
    monkeypatch, tmp_path, paper_factory
):
    database_url = f"sqlite:///{tmp_path / 'filtered.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.org/fake-webhook")
    monkeypatch.setenv("PAPER_VENUES", "chi,uist")
    monkeypatch.setenv("PAPER_YEAR", "2026")
    assert main(["--init-db"]) == 0
    cached = [
        paper_factory(title="Cached DIS", venue="DIS", year=2026, doi="10.1145/dis"),
        paper_factory(title="Cached older CHI", year=2025, doi="10.1145/old"),
    ]
    engine = create_database_engine(database_url)
    try:
        SQLAlchemyRepository(engine).upsert_papers(cached)
    finally:
        engine.dispose()
    papers = [
        paper_factory(title="CHI 2026", year=2026, doi="10.1145/chi26"),
        paper_factory(title="UIST 2026", venue="UIST", year=2026, doi="10.1145/uist26"),
        *cached,
    ]
    collected = []
    delivered = []

    class Service:
        def collect(self, venues, years):
            collected.append(({venue.id for venue in venues if venue.enabled}, list(years)))
            return papers

        def enrich(self, candidate):
            return candidate

    class Notifier:
        def send(self, candidate, summary):
            delivered.append(candidate)

    monkeypatch.setattr("src.main.PaperService", lambda *args: Service())
    monkeypatch.setattr("src.main.SlackNotifier", lambda *args: Notifier())
    monkeypatch.setattr(
        "httpx.Client.request",
        lambda *args, **kwargs: pytest.fail("No external HTTP request is expected"),
    )
    assert main(["--no-llm"]) == 0
    assert main(["--no-llm"]) == 0
    assert main(["--no-llm"]) == 1
    assert collected == [({"chi", "uist"}, [2026])] * 3
    assert {paper.id for paper in delivered} == {papers[0].id, papers[1].id}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PAPER_VENUES", "chi,unknown-private-value"),
        ("PAPER_VENUES", "chi,"),
        ("PAPER_YEAR", "private-value"),
        ("PAPER_YEAR", "1899"),
        ("PAPER_YEAR", "2201"),
    ],
)
def test_invalid_runtime_filter_fails_before_network_calls(monkeypatch, caplog, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "httpx.Client.request",
        lambda *args, **kwargs: pytest.fail("Invalid filters must not make HTTP requests"),
    )
    assert main(["--dry-run", "--no-llm"]) == 1
    assert name in caplog.text
    assert value not in caplog.text


def test_empty_runtime_filters_keep_default_window_and_cli_year(monkeypatch, capsys):
    monkeypatch.setenv("PAPER_VENUES", "  ")
    monkeypatch.setenv("PAPER_YEAR", "")
    fixture = Path(__file__).resolve().parent.parent / "examples" / "papers.json"
    assert main(["--dry-run", "--no-llm", "--fixture", str(fixture), "--year", "2026"]) == 0
    assert "CHI 2025" in capsys.readouterr().out


def test_cli_year_overrides_runtime_year_while_keeping_single_year(monkeypatch, capsys):
    monkeypatch.setenv("PAPER_VENUES", "chi,uist")
    monkeypatch.setenv("PAPER_YEAR", "2026")
    fixture = Path(__file__).resolve().parent.parent / "examples" / "papers.json"
    assert main(["--dry-run", "--no-llm", "--fixture", str(fixture), "--year", "2025"]) == 0
    assert "CHI 2025" in capsys.readouterr().out
