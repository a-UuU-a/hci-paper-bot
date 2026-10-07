from urllib.parse import quote

from src.errors import APIError
from src.http import RetryingHTTPClient
from src.models.paper import Paper
from src.services.normalization import matches_metadata, normalize_doi, normalize_text


class CrossrefCollector:
    BASE_URL = "https://api.crossref.org/works"

    def __init__(self, http: RetryingHTTPClient, email: str = ""):
        self.http = http
        self.params = {"mailto": email} if email else {}

    @staticmethod
    def authors(work: dict) -> list[str]:
        return [
            " ".join(filter(None, [author.get("given"), author.get("family")]))
            or author.get("name", "")
            for author in work.get("author", [])
        ]

    @staticmethod
    def year(work: dict) -> int | None:
        for field in ["published", "published-print", "published-online", "issued"]:
            parts = (work.get(field) or {}).get("date-parts")
            if parts and parts[0]:
                return parts[0][0]
        return None

    def find_work(self, paper: Paper) -> dict | None:
        if paper.doi:
            data = self.http.json(
                "GET",
                f"{self.BASE_URL}/{quote(paper.doi, safe='')}",
                service="Crossref",
                params=self.params,
                allowed_statuses=(404,),
            )
            if data is None:
                return None
            if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
                raise APIError("Crossref")
            work = data["message"]
            if normalize_doi(work.get("DOI")) != paper.doi:
                raise APIError("Crossref")
            return work
        data = self.http.json(
            "GET",
            self.BASE_URL,
            service="Crossref",
            params={
                **self.params,
                "query.title": paper.title,
                "rows": 5,
                "filter": (
                    f"from-pub-date:{paper.year - 1}-01-01,until-pub-date:{paper.year + 1}-12-31"
                ),
            },
        )
        try:
            works = data["message"]["items"]
        except (KeyError, TypeError):
            raise APIError("Crossref") from None
        for work in works:
            titles = work.get("title") or []
            if titles and matches_metadata(paper, titles[0], self.year(work), self.authors(work)):
                return work
        return None

    @staticmethod
    def metadata(work: dict) -> dict:
        return {
            "doi": normalize_doi(work.get("DOI")),
            "abstract": normalize_text(work["abstract"]) if work.get("abstract") else None,
            "authors": CrossrefCollector.authors(work),
            "url": work.get("URL"),
        }
