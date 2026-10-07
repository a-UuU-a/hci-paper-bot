import logging
from collections.abc import Iterable

from src.collectors.crossref import CrossrefCollector
from src.collectors.dblp import DBLPCollector
from src.collectors.openalex import OpenAlexCollector
from src.config import Venue
from src.errors import APIError
from src.models.paper import Paper
from src.services.normalization import normalize_paper

logger = logging.getLogger(__name__)


def merge_metadata(paper: Paper, metadata: dict, source: str) -> Paper:
    data = paper.model_dump()
    for field in ["abstract", "doi", "url", "authors", "topics", "citation_count"]:
        value = metadata.get(field)
        if value is not None and (value or value == 0):
            if field in {"topics", "citation_count"} or not data.get(field):
                data[field] = value
    sources = list(dict.fromkeys([*paper.source.split("+"), *source.split("+")]))
    data["source"] = "+".join(sources)
    return normalize_paper(**data)


class PaperService:
    def __init__(
        self,
        dblp: DBLPCollector,
        openalex: OpenAlexCollector,
        crossref: CrossrefCollector | None = None,
    ):
        self.dblp = dblp
        self.openalex = openalex
        self.crossref = crossref

    def collect(self, venues: list[Venue], years: Iterable[int]) -> list[Paper]:
        papers = {}
        years = list(years)
        for venue in venues:
            if venue.enabled:
                for year in years:
                    for paper in self.dblp.collect(venue, year):
                        papers[paper.id] = paper
        return list(papers.values())

    def _crossref(self, paper: Paper) -> Paper:
        if self.crossref is not None:
            try:
                work = self.crossref.find_work(paper)
                if work:
                    return merge_metadata(paper, self.crossref.metadata(work), "crossref")
            except APIError as exc:
                logger.warning("Crossref enrichment skipped: %s", exc)
        return paper

    def enrich(self, paper: Paper) -> Paper:
        if paper.abstract:
            return paper
        crossref_used = False
        if not paper.doi and self.crossref:
            paper = self._crossref(paper)
            crossref_used = True
        try:
            work = self.openalex.find_work(paper)
            if work:
                paper = merge_metadata(paper, self.openalex.metadata(work), "openalex")
        except APIError as exc:
            logger.warning("OpenAlex enrichment skipped: %s", exc)
        if not paper.abstract and not crossref_used:
            paper = self._crossref(paper)
        return paper
