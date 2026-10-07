from src.recommender.filter import filter_papers


def test_filters_disabled_venue_year_abstract_and_sent(paper_factory, venues):
    valid = paper_factory(doi="10.1145/valid")
    papers = [
        valid,
        paper_factory(venue="UIST", doi="10.1145/disabled"),
        paper_factory(year=2020, doi="10.1145/old"),
        paper_factory(year=2027, doi="10.1145/future"),
        paper_factory(abstract=None, doi="10.1145/empty"),
        paper_factory(doi="10.1145/sent"),
    ]
    result = filter_papers(
        papers, venues=venues, start_year=2022, end_year=2026, sent_ids={"doi:10.1145/sent"}
    )
    assert result == [valid]


def test_filters_can_be_disabled(paper_factory, venues):
    paper = paper_factory(abstract=None)
    result = filter_papers(
        [paper],
        venues=venues,
        start_year=2025,
        end_year=2025,
        require_abstract=False,
        sent_ids={paper.id},
        exclude_sent=False,
    )
    assert result == [paper]


def test_deduplicates_and_requires_link(paper_factory, venues):
    linked = paper_factory()
    unlinked = paper_factory(doi=None, url=None)
    result = filter_papers(
        [linked, linked, unlinked], venues=venues, start_year=2025, end_year=2025
    )
    assert result == [linked]
