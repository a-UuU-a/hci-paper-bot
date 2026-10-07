from src.errors import SummaryError
from src.models.paper import Paper
from src.summarizer.base import limit_summary


class ExtractiveSummarizer:
    """Original abstract excerpt. Does not translate or invent Japanese content."""

    def __init__(self, max_chars: int = 180):
        self.max_chars = max_chars

    def summarize(self, paper: Paper) -> str:
        if not (paper.abstract or "").strip():
            raise SummaryError("No abstract to summarize")
        return limit_summary(paper.abstract, self.max_chars)
