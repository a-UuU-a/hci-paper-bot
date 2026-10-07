from collections.abc import Iterable

from src.config import Venue
from src.models.paper import Paper


def filter_papers(
    papers: Iterable[Paper],
    *,
    venues: list[Venue],
    start_year: int,
    end_year: int,
    require_abstract: bool = True,
    sent_ids: set[str] | None = None,
    exclude_sent: bool = True,
) -> list[Paper]:
    enabled = {venue.name for venue in venues if venue.enabled}
    sent_ids = sent_ids or set()
    candidates = {}
    for paper in papers:
        if (
            paper.venue not in enabled
            or not start_year <= paper.year <= end_year
            or (require_abstract and not (paper.abstract or "").strip())
            or (exclude_sent and paper.id in sent_ids)
            or not (paper.doi or paper.url)
        ):
            continue
        candidates[paper.id] = paper
    return list(candidates.values())
