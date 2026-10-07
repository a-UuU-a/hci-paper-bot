import httpx
import pytest

from src.collectors.crossref import CrossrefCollector
from src.collectors.dblp import DBLPCollector
from src.collectors.openalex import OpenAlexCollector
from src.config import CollectionSettings, Venue
from src.errors import APIError, BotError
from src.services.paper_service import PaperService


def dblp_info(title="A Paper.", *, venue="CHI", year="2025", key="conf/chi/Example25", **extra):
    return {
        "title": title,
        "authors": {"author": {"text": "Alice Smith 0001"}},
        "venue": venue,
        "year": year,
        "key": key,
        "type": "Conference and Workshop Papers",
        "doi": "10.1145/123.456",
        "ee": "https://doi.org/10.1145/123.456",
        **extra,
    }


def dblp_response(infos, total=None):
    return {
        "result": {
            "hits": {
                "@total": str(total if total is not None else len(infos)),
                "hit": [{"info": info} for info in infos],
            }
        }
    }


def test_dblp_pagination_excludes_adjunct_and_proceedings(http_factory, venues):
    infos = [
        dblp_info(),
        dblp_info(venue="CHI Extended Abstracts", doi="10.1145/adjunct"),
        dblp_info(title="Proceedings of CHI", type="Editorship"),
        dblp_info(title="Another Paper", doi="10.1145/another"),
    ]
    offsets = []

    def handler(request):
        assert request.url.params["q"] == "stream:conf/chi: year:2025:"
        offset = int(request.url.params["f"])
        offsets.append(offset)
        return httpx.Response(200, json=dblp_response(infos[offset : offset + 2], total=4))

    settings = CollectionSettings(page_size=2, request_interval=0)
    papers = DBLPCollector(http_factory(handler), settings).collect(venues[0], 2025)
    assert offsets == [0, 2]
    assert [paper.title for paper in papers] == ["A Paper", "Another Paper"]
    assert papers[0].authors == ["Alice Smith"]


def test_cscw_does_not_include_other_pacmhci_issues(http_factory):
    venue = Venue(
        id="cscw",
        name="CSCW",
        publisher="acm",
        dblp_stream="conf/cscw",
        dblp_venues=["CSCW"],
        journal_stream="journals/pacmhci",
        journal_issues={2025: ["2", "7"]},
    )
    infos = [
        dblp_info(
            title="CSCW study",
            venue="Proc. ACM Hum. Comput. Interact.",
            type="Journal Articles",
            number="2",
        ),
        dblp_info(
            title="CHI PLAY study",
            venue="Proc. ACM Hum. Comput. Interact.",
            type="Journal Articles",
            number="4",
            doi="10.1145/play",
        ),
    ]

    def handler(request):
        data = infos if "journals/pacmhci" in request.url.params["q"] else []
        return httpx.Response(200, json=dblp_response(data))

    collector = DBLPCollector(http_factory(handler), CollectionSettings(request_interval=0))
    assert [paper.title for paper in collector.collect(venue, 2025)] == ["CSCW study"]
    with pytest.raises(BotError, match="journal_issues for 2027"):
        collector.collect(venue, 2027)


def test_old_cscw_named_issues_are_supported(http_factory):
    venue = Venue(
        id="cscw",
        name="CSCW",
        publisher="acm",
        dblp_stream="conf/cscw",
        dblp_venues=["CSCW"],
        journal_stream="journals/pacmhci",
    )
    info = dblp_info(year="2024", number="CSCW2", type="Journal Articles")
    collector = DBLPCollector(
        http_factory(
            lambda req: httpx.Response(
                200, json=dblp_response([info] if "journals/pacmhci" in req.url.params["q"] else [])
            )
        ),
        CollectionSettings(request_interval=0),
    )
    assert len(collector.collect(venue, 2024)) == 1


def test_dblp_api_failure_is_fatal(http_factory, venues):
    collector = DBLPCollector(
        http_factory(lambda req: httpx.Response(503)), CollectionSettings(request_interval=0)
    )
    with pytest.raises(APIError):
        collector.collect(venues[0], 2025)


def test_openalex_doi_lookup_and_abstract_enrichment(paper_factory, http_factory):
    paper = paper_factory(abstract=None)
    work = {
        "doi": "https://doi.org/10.1145/123.456",
        "cited_by_count": 4,
        "abstract_inverted_index": {"VR.": [2], "We": [0], "study": [1]},
        "topics": [{"display_name": "Human-Computer Interaction"}],
    }

    def handler(request):
        assert request.url.path == "/works/doi:10.1145/123.456"
        assert request.headers["Authorization"] == "Bearer test-openalex-key"
        return httpx.Response(200, json=work)

    http = http_factory(handler)
    service = PaperService(None, OpenAlexCollector(http, "test-openalex-key"))
    enriched = service.enrich(paper)
    assert enriched.abstract == "We study VR."
    assert enriched.citation_count == 4
    assert enriched.topics == ["Human-Computer Interaction"]
    assert enriched.venue == paper.venue and enriched.year == paper.year


def test_openalex_search_does_not_accept_first_unrelated_result(paper_factory, http_factory):
    paper = paper_factory(doi=None)
    works = [
        {"title": "Unrelated", "publication_year": 2025},
        {
            "title": paper.title,
            "publication_year": 2025,
            "authorships": [{"author": {"display_name": "Alice Smith"}}],
        },
    ]
    collector = OpenAlexCollector(
        http_factory(lambda req: httpx.Response(200, json={"results": works}))
    )
    assert collector.find_work(paper) == works[1]


def test_doi_not_found_does_not_fail_the_run(paper_factory, http_factory):
    collector = OpenAlexCollector(http_factory(lambda req: httpx.Response(404)))
    assert collector.find_work(paper_factory()) is None


def test_crossref_restores_doi_and_strips_jats(paper_factory, http_factory):
    paper = paper_factory(doi=None, abstract=None)
    work = {
        "DOI": "10.1145/RESTORED",
        "title": [paper.title],
        "published": {"date-parts": [[2025, 5, 1]]},
        "author": [{"given": "Alice", "family": "Smith"}],
        "abstract": "<jats:p>We study <jats:italic>VR</jats:italic>.</jats:p>",
    }
    collector = CrossrefCollector(
        http_factory(lambda req: httpx.Response(200, json={"message": {"items": [work]}}))
    )
    matched = collector.find_work(paper)
    assert collector.metadata(matched)["doi"] == "10.1145/restored"
    assert "jats" not in collector.metadata(matched)["abstract"]


def test_openalex_failure_can_use_crossref_abstract(paper_factory, http_factory):
    paper = paper_factory(abstract=None)

    def handler(request):
        if request.url.host == "api.openalex.org":
            return httpx.Response(503)
        return httpx.Response(
            200, json={"message": {"DOI": paper.doi, "abstract": "<p>Recovered abstract.</p>"}}
        )

    http = http_factory(handler)
    service = PaperService(None, OpenAlexCollector(http), CrossrefCollector(http))
    assert service.enrich(paper).abstract == "Recovered abstract."
