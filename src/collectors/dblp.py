import logging
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

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
        self._use_sparql = False

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

    def _sparql_query(self, stream: str, year: int, offset: int) -> str:
        stream_url = "https://dblp.org/streams/" + quote(stream, safe="/")
        # Page publications before joining authors: an author must never consume
        # a pagination slot or get split across pages.
        return f"""PREFIX dblp: <https://dblp.org/rdf/schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?publ ?kind ?title ?year ?venue ?doi ?url ?number ?author ?ordinal WHERE {{
  {{ SELECT DISTINCT ?publ WHERE {{
      ?publ dblp:publishedInStream <{stream_url}> ;
            dblp:yearOfPublication "{int(year)}"^^xsd:gYear .
      VALUES ?kind {{ dblp:Inproceedings dblp:Article }}
      ?publ a ?kind .
    }} ORDER BY ?publ LIMIT {self.settings.page_size} OFFSET {offset} }}
  VALUES ?kind {{ dblp:Inproceedings dblp:Article }}
  ?publ a ?kind ; dblp:title ?title ;
        dblp:yearOfPublication ?year ; dblp:publishedIn ?venue .
  OPTIONAL {{ ?publ dblp:doi ?doi }}
  OPTIONAL {{ ?publ dblp:primaryDocumentPage ?url }}
  OPTIONAL {{ ?publ dblp:publishedInJournalVolumeIssue ?number }}
  OPTIONAL {{
    ?publ dblp:hasSignature ?signature .
    ?signature a dblp:AuthorSignature ; dblp:signatureDblpName ?author ;
               dblp:signatureOrdinal ?ordinal .
  }}
}} ORDER BY ?publ ?ordinal ?doi ?url ?venue ?kind ?number"""

    @staticmethod
    def _sparql_value(row: dict, field: str, *, required: bool = False) -> str:
        cell = row.get(field)
        if cell is None and not required:
            return ""
        if not isinstance(cell, dict) or not isinstance(cell.get("value"), str):
            raise ValueError("Invalid SPARQL binding")
        return cell["value"]

    @classmethod
    def _sparql_infos(cls, data: Any) -> list[dict]:
        try:
            rows = data["results"]["bindings"]
            if not isinstance(rows, list):
                raise ValueError
            # QLever can report a truncated result. Do not silently lose authors
            # or stop pagination early if fewer rows were actually transmitted.
            total = data.get("meta", {}).get("result-size-total", len(rows))
            if int(total) != len(rows):
                raise ValueError
            papers = {}
            authors: dict[str, set[tuple[int, str]]] = {}
            kinds = {
                "https://dblp.org/rdf/schema#Inproceedings": "Conference and Workshop Papers",
                "https://dblp.org/rdf/schema#Article": "Journal Articles",
            }
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                key = cls._sparql_value(row, "publ", required=True)
                kind = cls._sparql_value(row, "kind", required=True)
                if key not in papers:
                    papers[key] = {
                        "key": key,
                        "title": cls._sparql_value(row, "title", required=True),
                        "year": cls._sparql_value(row, "year", required=True),
                        "type": kinds[kind],
                        "venue": [],
                        "ee": [],
                        "number": cls._sparql_value(row, "number"),
                        "doi": cls._sparql_value(row, "doi"),
                        "url": cls._sparql_value(row, "url") or key,
                    }
                    authors[key] = set()
                info = papers[key]
                venue = cls._sparql_value(row, "venue", required=True)
                if venue not in info["venue"]:
                    info["venue"].append(venue)
                for field in ["doi", "url"]:
                    url = cls._sparql_value(row, field)
                    if url and url not in info["ee"]:
                        info["ee"].append(url)
                if author := cls._sparql_value(row, "author"):
                    ordinal = int(cls._sparql_value(row, "ordinal", required=True))
                    authors[key].add((ordinal, author))
            for key, info in papers.items():
                info["authors"] = {"author": [name for _, name in sorted(authors[key])]}
            return list(papers.values())
        except (KeyError, TypeError, ValueError, AttributeError):
            raise APIError("DBLP SPARQL") from None

    def _search_sparql(self, stream: str, year: int) -> Iterable[dict]:
        offset = 0
        seen = set()
        while True:
            time.sleep(self.settings.request_interval)
            data = self.http.json(
                "GET",
                self.settings.dblp_sparql_url,
                service="DBLP SPARQL",
                headers={"Accept": "application/sparql-results+json"},
                params={"query": self._sparql_query(stream, year, offset)},
            )
            infos = self._sparql_infos(data)
            keys = {info["key"] for info in infos}
            if len(infos) > self.settings.page_size or keys & seen:
                raise BotError("DBLP SPARQL returned an invalid or repeated page")
            yield from infos
            seen.update(keys)
            if len(infos) < self.settings.page_size:
                return
            offset += len(infos)

    def _records(self, stream: str, year: int) -> Iterable[dict]:
        if not self._use_sparql:
            try:
                yield from self._search(stream, year)
                return
            except APIError as exc:
                logger.warning(
                    "DBLP search API unavailable (%s); switching to the official SPARQL API",
                    exc,
                )
                # Keep using SPARQL for all remaining venues and years this run,
                # instead of retrying the blocked search endpoint for each one.
                self._use_sparql = True
        yield from self._search_sparql(stream, year)

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
            for info in self._records(stream, year):
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
