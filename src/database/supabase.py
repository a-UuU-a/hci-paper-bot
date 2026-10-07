from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from src.errors import APIError
from src.http import RetryingHTTPClient
from src.models.paper import Paper


class PaperRepository(Protocol):
    def get_sent_ids(self) -> set[str]: ...

    def get_papers(self, start_year: int, end_year: int) -> list[Paper]: ...

    def upsert_papers(self, papers: list[Paper]) -> None: ...

    def record_recommendation(
        self, paper_id: str, *, channel: str, status: Literal["sent", "failed"], score: float = 0
    ) -> None: ...


class SupabaseRepository:
    """Supabase's PostgREST API, sharing the bot's timeout and retry policy."""

    PAGE_SIZE = 1000
    UPSERT_BATCH_SIZE = 500

    def __init__(self, http: RetryingHTTPClient, url: str, key: str):
        self.http = http
        self.base_url = url.rstrip("/") + "/rest/v1"
        self.headers = {"apikey": key}
        # New sb_secret keys are opaque, rather than JWT bearer tokens.
        if not key.startswith("sb_secret_"):
            self.headers["Authorization"] = f"Bearer {key}"

    def _pages(self, table: str, params: dict) -> list[dict]:
        rows = []
        offset = 0
        while True:
            page = self.http.json(
                "GET",
                f"{self.base_url}/{table}",
                service="Supabase",
                headers=self.headers,
                params={**params, "limit": self.PAGE_SIZE, "offset": offset},
            )
            if not isinstance(page, list):
                raise APIError("Supabase")
            rows.extend(page)
            if len(page) < self.PAGE_SIZE:
                return rows
            offset += len(page)

    def get_sent_ids(self) -> set[str]:
        return {
            row["paper_id"]
            for row in self._pages(
                "recommendations",
                {
                    "select": "paper_id,id",
                    "status": "eq.sent",
                    "order": "id.asc",
                },
            )
        }

    def get_papers(self, start_year: int, end_year: int) -> list[Paper]:
        rows = self._pages(
            "papers",
            {
                "select": "*",
                "order": "id.asc",
                "and": f"(year.gte.{start_year},year.lte.{end_year})",
            },
        )
        return [Paper.model_validate(row) for row in rows]

    def upsert_papers(self, papers: list[Paper]) -> None:
        for offset in range(0, len(papers), self.UPSERT_BATCH_SIZE):
            self.http.request(
                "POST",
                f"{self.base_url}/papers",
                service="Supabase",
                params={"on_conflict": "id"},
                headers={**self.headers, "Prefer": "resolution=merge-duplicates"},
                json=[
                    paper.model_dump(mode="json")
                    for paper in papers[offset : offset + self.UPSERT_BATCH_SIZE]
                ],
            )

    def record_recommendation(
        self, paper_id: str, *, channel: str, status: Literal["sent", "failed"], score: float = 0
    ) -> None:
        # Stable within this request, so HTTP retries cannot create multiple history rows.
        self.http.request(
            "POST",
            f"{self.base_url}/recommendations",
            service="Supabase",
            params={"on_conflict": "id"},
            headers={**self.headers, "Prefer": "resolution=merge-duplicates"},
            json={
                "id": str(uuid4()),
                "paper_id": paper_id,
                "channel": channel,
                "status": status,
                "score": score,
                "sent_at": datetime.now(UTC).isoformat() if status == "sent" else None,
            },
        )


class MemoryRepository:
    """Ephemeral repository for previews; never touches Supabase."""

    def __init__(self):
        self.papers: dict[str, Paper] = {}
        self.recommendations: list[dict] = []

    def get_sent_ids(self) -> set[str]:
        return {row["paper_id"] for row in self.recommendations if row["status"] == "sent"}

    def get_papers(self, start_year: int, end_year: int) -> list[Paper]:
        return [paper for paper in self.papers.values() if start_year <= paper.year <= end_year]

    def upsert_papers(self, papers: list[Paper]) -> None:
        self.papers.update({paper.id: paper for paper in papers})

    def record_recommendation(
        self, paper_id: str, *, channel: str, status: Literal["sent", "failed"], score: float = 0
    ) -> None:
        self.recommendations.append(
            {"paper_id": paper_id, "channel": channel, "status": status, "score": score}
        )
