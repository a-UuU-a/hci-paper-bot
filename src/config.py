import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from src.errors import BotError

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Venue(ConfigModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    publisher: Literal["acm", "ieee"]
    enabled: bool = True
    weight: float = Field(default=1, ge=0)
    dblp_stream: str
    dblp_venues: list[str]
    journal_stream: str | None = None
    journal_issues: dict[int, list[str]] = Field(default_factory=dict)


class RecommendationSettings(ConfigModel):
    papers_per_day: int = Field(default=1, ge=1)
    selection_method: Literal["random"] = "random"
    exclude_sent: bool = True


class PublicationSettings(ConfigModel):
    years_back: int = Field(default=5, ge=1)
    require_abstract: bool = True


class SummarySettings(ConfigModel):
    language: Literal["ja"] = "ja"
    max_chars: int = Field(default=180, ge=20, le=1000)
    use_llm: bool = True
    fallback: Literal["skip", "extractive"] = "skip"


class SlackSettings(ConfigModel):
    show_topics: bool = True
    max_authors: int = Field(default=3, ge=1)
    channel: str = Field(default="default", min_length=1)


class CollectionSettings(ConfigModel):
    dblp_api_url: str = "https://dblp.org/search/publ/api"
    dblp_sparql_url: str = "https://sparql.dblp.org/sparql"
    page_size: int = Field(default=1000, ge=1, le=1000)
    request_interval: float = Field(default=1, ge=0)
    max_enrichment_attempts: int = Field(default=50, ge=1)
    use_crossref: bool = True


class HTTPSettings(ConfigModel):
    timeout_seconds: float = Field(default=30, gt=0)
    max_retries: int = Field(default=3, ge=0, le=3)


class Settings(ConfigModel):
    recommendation: RecommendationSettings = Field(default_factory=RecommendationSettings)
    publication: PublicationSettings = Field(default_factory=PublicationSettings)
    summary: SummarySettings = Field(default_factory=SummarySettings)
    slack: SlackSettings = Field(default_factory=SlackSettings)
    collection: CollectionSettings = Field(default_factory=CollectionSettings)
    http: HTTPSettings = Field(default_factory=HTTPSettings)

    @model_validator(mode="after")
    def validate_attempt_budget(self):
        if self.collection.max_enrichment_attempts < self.recommendation.papers_per_day:
            raise ValueError("max_enrichment_attempts must be at least papers_per_day")
        return self


class Credentials(BaseModel):
    slack_webhook_url: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr("")
    supabase_url: str = ""
    supabase_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    openalex_api_key: SecretStr = SecretStr("")
    metadata_email: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            **{
                name: os.getenv(name.upper(), "")
                for name in cls.model_fields
                if name != "openai_model"
            },
            openai_model=os.getenv("OPENAI_MODEL") or "gpt-4.1-mini",
        )

    def validate_for_run(self, settings: Settings, *, dry_run: bool):
        required = []
        if not dry_run:
            required.append("slack_webhook_url")
            if not self.database_url.get_secret_value():
                required.extend(["supabase_url", "supabase_key"])
        if settings.summary.use_llm:
            required.append("openai_api_key")
        missing = [
            name.upper()
            for name in required
            if not (
                getattr(self, name).get_secret_value()
                if isinstance(getattr(self, name), SecretStr)
                else getattr(self, name)
            )
        ]
        if missing:
            raise BotError("Missing environment variables: " + ", ".join(missing))


def load_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> tuple[Settings, list[Venue]]:
    try:
        settings_data = yaml.safe_load((config_dir / "settings.yaml").read_text()) or {}
        venue_data = yaml.safe_load((config_dir / "venues.yaml").read_text())
        settings = Settings.model_validate(settings_data)
        venues = [Venue.model_validate(item) for item in venue_data["venues"]]
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        # Pydantic/YAML errors can include input values: keep them out of logs.
        raise BotError("Invalid configuration; check settings.yaml and venues.yaml") from exc
    if len({venue.id for venue in venues}) != len(venues):
        raise BotError("Venue IDs must be unique")
    if not any(venue.enabled for venue in venues):
        raise BotError("Enable at least one venue")
    return settings, venues


def apply_runtime_overrides(
    settings: Settings, venues: list[Venue], *, year: int | None = None
) -> tuple[Settings, list[Venue], int | None]:
    """Apply optional deployment filters without changing the shared YAML defaults."""
    venue_ids = os.getenv("PAPER_VENUES", "").strip()
    if venue_ids:
        selected = {item.strip().lower() for item in venue_ids.split(",")}
        if not selected <= {venue.id for venue in venues}:
            raise BotError("PAPER_VENUES must contain comma-separated venue IDs from venues.yaml")
        venues = [venue.model_copy(update={"enabled": venue.id in selected}) for venue in venues]

    publication_year = os.getenv("PAPER_YEAR", "").strip()
    if publication_year:
        try:
            override_year = int(publication_year)
        except ValueError:
            raise BotError("PAPER_YEAR must be an integer between 1900 and 2200") from None
        if not 1900 <= override_year <= 2200:
            raise BotError("PAPER_YEAR must be an integer between 1900 and 2200")
        settings = settings.model_copy(deep=True)
        settings.publication.years_back = 1
        if year is None:
            year = override_year

    return settings, venues, year
