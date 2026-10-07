import json

import httpx
import pytest

from src.errors import SummaryError
from src.summarizer.base import FallbackSummarizer, limit_summary
from src.summarizer.extractive import ExtractiveSummarizer
from src.summarizer.llm import LLMSummarizer


def response(text, *, status="completed"):
    return {
        "status": status,
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
    }


def test_summary_is_bounded_without_adding_facts():
    text = "操作方法を提案し、参加者実験で評価した。" * 20
    assert len(limit_summary(text, 180)) <= 180
    assert text.startswith(limit_summary(text, 180))


def test_llm_responses_api_returns_japanese(paper_factory, http_factory):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=response("VRの操作方法を提案し、参加者実験で評価した。"))

    summary = LLMSummarizer(http_factory(handler), "test-key").summarize(paper_factory())
    assert summary == "VRの操作方法を提案し、参加者実験で評価した。"
    assert requests[0]["store"] is False
    assert json.loads(requests[0]["input"])["Abstract"] == paper_factory().abstract
    assert "推測しない" in requests[0]["instructions"]


def test_llm_output_is_limited(paper_factory, http_factory):
    http = http_factory(lambda req: httpx.Response(200, json=response("日本語の要約です。" * 50)))
    assert len(LLMSummarizer(http, "test-key").summarize(paper_factory())) <= 180


@pytest.mark.parametrize("summarizer_type", ["extractive", "llm"])
def test_empty_abstract(paper_factory, http_factory, summarizer_type):
    summarizer = (
        ExtractiveSummarizer()
        if summarizer_type == "extractive"
        else LLMSummarizer(http_factory(lambda req: pytest.fail("No request expected")), "test-key")
    )
    with pytest.raises(SummaryError, match="No abstract"):
        summarizer.summarize(paper_factory(abstract=None))


def test_llm_error_skips_or_falls_back(paper_factory, http_factory):
    primary = LLMSummarizer(http_factory(lambda req: httpx.Response(500)), "test-key")
    paper = paper_factory()
    with pytest.raises(SummaryError):
        FallbackSummarizer(primary).summarize(paper)
    assert FallbackSummarizer(primary, ExtractiveSummarizer()).summarize(paper) == paper.abstract


@pytest.mark.parametrize(
    "payload",
    [response("English only."), response(""), response("日本語です。", status="incomplete"), {}],
)
def test_invalid_llm_results_are_rejected(paper_factory, http_factory, payload):
    http = http_factory(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(SummaryError):
        LLMSummarizer(http, "test-key").summarize(paper_factory())
