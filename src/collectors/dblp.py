import logging
import time
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from src.config import CollectionSettings, Venue
from src.errors import APIError, BotError
from src.http import RetryingHTTPClient
from src.models.paper import Paper
from src.services.normalization import normalize_doi, normalize_paper, normalize_text

logger = logging.getLogger(__name__)


def _list(value: Any) -> list:
    return value if isinstance(value, list) else [value] if value else []


class DBLPCollector:
    def __init__(self, http: RetryingHTTPClient, settings: CollectionSettings):
        self.http = http
        self.settings = settings

    def _search(self, stream: str, year: int) -> Iterable[dict]:
        offset = 0
        while True:
            time.sleep(self.settings.request_interval)
            data = self.http.json(
                "GET",
                self.settings.dblp_api_url,
                service="DBLP",
                params={
                    "q": f"stream:{stream}: year:{year}:",
                    "format": "json",
                    "h": self.settings.page_size,
                    "f": offset,
                },
            )
            try:
                result = data["result"]["hits"]
                total = int(result["@total"])
                hits = _list(result.get("hit"))
                infos = [hit["info"] for hit in hits]
            except (KeyError, TypeError, ValueError):
                raise APIError("DBLP") from None
            if not hits:
                if offset < total:
                    raise BotError("DBLP returned an incomplete page")
                return
            yield from infos
            offset += len(hits)
            if offset >= total:
                return

    @staticmethod
    def _is_paper(info: dict) -> bool:
        return info.get("type") in {"Conference and Workshop Papers", "Journal Articles"}

    @staticmethod
    def _normalize(info: dict, venue: Venue) -> Paper:
        raw_authors = _list((info.get("authors") or {}).get("author"))
        authors = [
            author.get("text", "") if isinstance(author, dict) else author for author in raw_authors
        ]
        editions = _list(info.get("ee"))
        editions = [
            edition.get("text", "") if isinstance(edition, dict) else edition
            for edition in editions
        ]
        doi = normalize_doi(info.get("doi")) or next(
            (normalize_doi(url) for url in editions if normalize_doi(url)), None
        )
        return normalize_paper(
            title=info["title"],
            authors=authors,
            venue=venue.name,
            year=info["year"],
            doi=doi,
            url=next(iter(editions), None) or info.get("url"),
            source="dblp",
        )

    def collect(self, venue: Venue, year: int) -> list[Paper]:
        papers = {}
        streams = [venue.dblp_stream]
        if venue.journal_stream:
            if year >= 2025 and year not in venue.journal_issues:
                raise BotError(f"Configure CSCW journal_issues for {year} in venues.yaml")
            streams.append(venue.journal_stream)
        for stream in streams:
            for info in self._search(stream, year):
                if not self._is_paper(info) or int(info.get("year", 0)) != year:
                    continue
                if stream == venue.journal_stream:
                    issue = str(info.get("number", ""))
                    if not (
                        issue.upper().startswith("CSCW")
                        or issue in venue.journal_issues.get(year, [])
                    ):
                        continue
                elif not set(_list(info.get("venue"))) & set(venue.dblp_venues):
                    # Excludes extended abstracts, companion volumes and workshops.
                    continue
                if "editorial" in normalize_text(info.get("title", "")).casefold():
                    continue
                try:
                    paper = self._normalize(info, venue)
                except (KeyError, TypeError, ValueError, ValidationError):
                    logger.warning("Skipping malformed DBLP record for %s %d", venue.name, year)
                    continue
                if paper.doi or paper.url:
                    papers[paper.id] = paper
        logger.info("DBLP %s %d: %d papers", venue.name, year, len(papers))
        return list(papers.values())
