import logging
import re
from typing import Protocol

from src.errors import SummaryError
from src.models.paper import Paper

logger = logging.getLogger(__name__)


class Summarizer(Protocol):
    def summarize(self, paper: Paper) -> str: ...


def limit_summary(text: str, max_chars: int) -> str:
    text = " ".join(text.split()).strip()
    if not text:
        raise SummaryError("Summary is empty")
    if len(text) <= max_chars:
        return text
    excerpt = text[: max_chars - 1]
    boundaries = list(re.finditer(r"[。.!?！？](?:\s|$)|。", excerpt))
    if boundaries and boundaries[-1].end() >= max_chars // 2:
        return excerpt[: boundaries[-1].end()].strip()
    return excerpt.rstrip() + "…"


class FallbackSummarizer:
    def __init__(self, primary: Summarizer, fallback: Summarizer | None = None):
        self.primary = primary
        self.fallback = fallback

    def summarize(self, paper: Paper) -> str:
        try:
            return self.primary.summarize(paper)
        except SummaryError:
            if self.fallback is None:
                raise
            logger.warning("LLM summary failed; displaying an original abstract excerpt")
            return self.fallback.summarize(paper)
