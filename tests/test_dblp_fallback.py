import logging
import re

import httpx
import pytest

from src.collectors.dblp import DBLPCollector
from src.config import CollectionSettings, Venue
from src.errors import APIError, BotError


def binding(key="first", *, author="Alice Smith 0001", ordinal=1, **changes):
    values = {
        "publ": f"https://dblp.org/rec/conf/chi/{key}",
        "kind": "https://dblp.org/rdf/schema#Inproceedings",
        "title": f"{key.title()} Paper.",
        "year": "2025",
        "venue": "CHI",
        "doi": f"https://doi.org/10.1145/{key}",
        "author": author,
        "ordinal": str(ordinal),
        **changes,
    }
    return {name: {"value": value} for name, value in values.items() if value is not None}


def response(rows, **extra):
    return httpx.Response(200, json={"results": {"bindings": rows}, **extra})


def test_html_challenge_switches_to_sparql_once_for_the_entire_run(http_factory, venues, caplog):
    search_calls = []
    sparql_calls = []

    def handler(request):
        if request.url.host == "dblp.org":
            search_calls.append(request)
            return httpx.Response(200, text="<html>Anubis challenge private-value</html>")
        sparql_calls.append(request)
        assert request.url.host == "sparql.dblp.org"
        assert request.headers["Accept"] == "application/sparql-results+json"
        query = request.url.params["query"]
        assert "<https://dblp.org/streams/conf/chi>" in query
        assert "SELECT DISTINCT ?publ" in query
        assert "dblp:AuthorSignature" in query
        year = "2024" if '"2024"^^xsd:gYear' in query else "2025"
        return response([binding(year, year=year)])

    collector = DBLPCollector(http_factory(handler), CollectionSettings(request_interval=0))
    with caplog.at_level(logging.WARNING):
        assert len(collector.collect(venues[0], 2025)) == 1
        assert len(collector.collect(venues[0], 2024)) == 1
    assert len(search_calls) == 1
    assert len(sparql_calls) == 2
    assert "switching to the official SPARQL API" in caplog.text
    assert "private-value" not in caplog.text


def test_fallback_pages_publications_and_preserves_author_order(http_factory, venues):
    offsets = []

    def handler(request):
        if request.url.host == "dblp.org":
            return httpx.Response(403)
        query = request.url.params["query"]
        offset = int(re.search(r"OFFSET (\d+)", query)[1])
        offsets.append(offset)
        assert "LIMIT 2" in query
        if offset == 0:
            return response(
                [
                    binding(author="Bob Jones", ordinal=2, doi=None),
                    binding(doi=None),
                    binding(doi=None),  # Multiple document URLs can duplicate a signature.
                    binding("second"),
                    binding("second", author="Carol Davis", ordinal=2),
                ]
            )
        return response([binding("third")])

    collector = DBLPCollector(
        http_factory(handler), CollectionSettings(page_size=2, request_interval=0)
    )
    papers = collector.collect(venues[0], 2025)
    assert offsets == [0, 2]
    assert [paper.title for paper in papers] == ["First Paper", "Second Paper", "Third Paper"]
    assert papers[0].authors == ["Alice Smith", "Bob Jones"]
    assert papers[0].id.startswith("sha256:")
    assert papers[0].url == "https://dblp.org/rec/conf/chi/first"
    assert papers[1].doi == "10.1145/second"


def test_fallback_still_excludes_companion_volumes_and_editorials(http_factory, venues):
    def handler(request):
        if request.url.host == "dblp.org":
            return httpx.Response(200, text="<html>challenge</html>")
        return response(
            [
                binding(),
                binding("adjunct", venue="CHI Extended Abstracts"),
                binding("workshop", venue="CHI Workshops"),
                binding("editorial", title="Editorial Introduction"),
                binding("old", year="2024"),
            ]
        )

    collector = DBLPCollector(http_factory(handler), CollectionSettings(request_interval=0))
    assert [paper.title for paper in collector.collect(venues[0], 2025)] == ["First Paper"]


@pytest.mark.parametrize("year,issue", [(2024, "CSCW2"), (2025, "7")])
def test_fallback_keeps_only_cscw_journal_issues(http_factory, year, issue):
    venue = Venue(
        id="cscw",
        name="CSCW",
        publisher="acm",
        dblp_stream="conf/cscw",
        dblp_venues=["CSCW"],
        journal_stream="journals/pacmhci",
        journal_issues={2025: ["2", "7"]},
    )

    def handler(request):
        if request.url.host == "dblp.org":
            return httpx.Response(503)
        if "streams/conf/cscw>" in request.url.params["query"]:
            return response([])
        return response(
            [
                binding(
                    "cscw",
                    year=str(year),
                    kind="https://dblp.org/rdf/schema#Article",
                    venue="Proc. ACM Hum. Comput. Interact.",
                    number=issue,
                ),
                binding(
                    "chi-play",
                    year=str(year),
                    kind="https://dblp.org/rdf/schema#Article",
                    venue="Proc. ACM Hum. Comput. Interact.",
                    number="4",
                ),
            ]
        )

    collector = DBLPCollector(http_factory(handler), CollectionSettings(request_interval=0))
    papers = collector.collect(venue, year)
    assert len(papers) == 1
    assert papers[0].title == "Cscw Paper"
    assert papers[0].venue == "CSCW"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": {"bindings": "invalid"}},
        {"results": {"bindings": [None]}},
        {"results": {"bindings": [binding(kind="unknown")]}},
        {"results": {"bindings": [binding(title=None)]}},
        {"results": {"bindings": [binding(ordinal="invalid")]}},
        {"results": {"bindings": [binding()]}, "meta": {"result-size-total": 2}},
    ],
)
def test_malformed_or_truncated_sparql_response_fails_without_exposing_data(
    http_factory, venues, payload
):
    def handler(request):
        if request.url.host == "dblp.org":
            return httpx.Response(403)
        return httpx.Response(200, json=payload)

    collector = DBLPCollector(http_factory(handler), CollectionSettings(request_interval=0))
    with pytest.raises(APIError, match="DBLP SPARQL") as exc:
        collector.collect(venues[0], 2025)
    assert "unknown" not in str(exc.value)
    assert "invalid" not in str(exc.value)


def test_repeated_sparql_page_fails_instead_of_looping(http_factory, venues):
    def handler(request):
        if request.url.host == "dblp.org":
            return httpx.Response(403)
        return response([binding()])

    collector = DBLPCollector(
        http_factory(handler), CollectionSettings(page_size=1, request_interval=0)
    )
    with pytest.raises(BotError, match="repeated page"):
        collector.collect(venues[0], 2025)


def test_partial_search_failure_recovers_without_duplicate_papers(http_factory, venues):
    def handler(request):
        if request.url.host == "dblp.org":
            if request.url.params["f"] == "0":
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "hits": {
                                "@total": "2",
                                "hit": [
                                    {
                                        "info": {
                                            "title": "First Paper.",
                                            "year": "2025",
                                            "type": "Conference and Workshop Papers",
                                            "venue": "CHI",
                                            "doi": "10.1145/first",
                                        }
                                    }
                                ],
                            }
                        }
                    },
                )
            return httpx.Response(200, text="<html>challenge</html>")
        return response([binding(), binding("second")])

    collector = DBLPCollector(
        http_factory(handler), CollectionSettings(page_size=3, request_interval=0)
    )
    papers = collector.collect(venues[0], 2025)
    assert [paper.id for paper in papers] == ["doi:10.1145/first", "doi:10.1145/second"]


def test_both_dblp_endpoints_unavailable_remains_fatal(http_factory, venues):
    collector = DBLPCollector(
        http_factory(lambda req: httpx.Response(503)), CollectionSettings(request_interval=0)
    )
    with pytest.raises(APIError, match="DBLP SPARQL: HTTP 503"):
        collector.collect(venues[0], 2025)
