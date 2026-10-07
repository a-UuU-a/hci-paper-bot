from typing import Any
from urllib.parse import quote

from src.errors import APIError
from src.http import RetryingHTTPClient
from src.models.paper import Paper
from src.services.normalization import matches_metadata, normalize_doi, reconstruct_abstract


class OpenAlexCollector:
    BASE_URL = "https://api.openalex.org/works"

    def __init__(self, http: RetryingHTTPClient, api_key: str = "", email: str = ""):
        self.http = http
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.params = {"mailto": email} if email else {}

    @staticmethod
    def authors(work: dict) -> list[str]:
        return [
            item["author"]["display_name"]
            for item in work.get("authorships", [])
            if (item.get("author") or {}).get("display_name")
        ]

    def find_work(self, paper: Paper) -> dict | None:
        if paper.doi:
            work = self.http.json(
                "GET",
                f"{self.BASE_URL}/doi:{quote(paper.doi, safe='')}",
                service="OpenAlex",
                params=self.params,
                headers=self.headers,
                allowed_statuses=(404,),
            )
            if work is None:
                return None
            if not isinstance(work, dict) or normalize_doi(work.get("doi")) != paper.doi:
                raise APIError("OpenAlex")
            return work
        data = self.http.json(
            "GET",
            self.BASE_URL,
            service="OpenAlex",
            headers=self.headers,
            params={
                **self.params,
                "search": paper.title,
                "per_page": 5,
                "filter": f"publication_year:{paper.year - 1}-{paper.year + 1}",
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise APIError("OpenAlex")
        for work in data["results"]:
            if matches_metadata(
                paper, work.get("title") or "", work.get("publication_year"), self.authors(work)
            ):
                return work
        return None

    @staticmethod
    def metadata(work: dict) -> dict[str, Any]:
        primary = work.get("primary_location") or {}
        oa = work.get("open_access") or {}
        return {
            "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
            "doi": normalize_doi(work.get("doi")),
            "authors": OpenAlexCollector.authors(work),
            "topics": [
                topic["display_name"]
                for topic in work.get("topics", [])
                if topic.get("display_name")
            ],
            "citation_count": work.get("cited_by_count"),
            "url": oa.get("oa_url") or primary.get("landing_page_url") or primary.get("pdf_url"),
        }
