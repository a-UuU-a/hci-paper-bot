import random
from collections.abc import Sequence

from src.models.paper import Paper


def select_papers(
    papers: Sequence[Paper],
    method: str = "random",
    count: int = 1,
    *,
    rng: random.Random | None = None,
) -> list[Paper]:
    if method != "random":
        raise ValueError(f"Unsupported selection method: {method}")
    if count < 0:
        raise ValueError("count must be nonnegative")
    unique = list({paper.id: paper for paper in papers}.values())
    return (rng or random).sample(unique, min(count, len(unique)))
