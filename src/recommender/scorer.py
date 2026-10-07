from typing import Protocol

from src.models.paper import Paper


class Scorer(Protocol):
    """Extension point for future keyword or embedding recommendation."""

    def score(self, paper: Paper) -> float: ...


class NeutralScorer:
    def score(self, paper: Paper) -> float:
        return 0.0
