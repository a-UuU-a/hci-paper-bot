import pytest

from src.services.normalization import (
    generate_paper_id,
    matches_metadata,
    normalize_authors,
    normalize_doi,
    normalize_paper,
    reconstruct_abstract,
)


@pytest.mark.parametrize(
    "value",
    [
        "10.1145/ABC.123",
        "https://doi.org/10.1145/abc.123",
        "HTTP://DX.DOI.ORG/10.1145/ABC.123",
        " doi:10.1145/ABC.123 ",
    ],
)
def test_doi_forms_have_identical_ids(value):
    assert generate_paper_id("Different titles", 2025, ["Alice"], value) == "doi:10.1145/abc.123"


@pytest.mark.parametrize("value", [None, "", "garbage", "https://example.com/10.1145/test"])
def test_invalid_doi(value):
    assert normalize_doi(value) is None


def test_doi_url_parameters_are_not_part_of_the_identifier():
    assert normalize_doi("https://doi.org/10.1145/ABC.123?download=1#abstract") == "10.1145/abc.123"


def test_hash_is_stable_under_whitespace_case_and_dblp_author_suffix():
    first = generate_paper_id("A   Paper. ", 2025, ["Alice Smith 0001"])
    assert first == generate_paper_id("a paper", 2025, ["alice smith"])
    assert first != generate_paper_id("A paper", 2024, ["Alice Smith"])
    assert first != generate_paper_id("A paper", 2025, ["Bob Jones"])


def test_normalizes_html_unicode_author_and_venue():
    paper = normalize_paper(
        title="  A <i>Paper</i> &amp; ＶＲ. ",
        authors=["Alice Smith 0001"],
        venue="VR",
        year="2025",
        doi="10.1109/ABC.123",
    )
    assert paper.title == "A Paper & VR"
    assert paper.authors == ["Alice Smith"]
    assert paper.venue == "IEEE VR"
    assert paper.url == "https://doi.org/10.1109/abc.123"


def test_duplicate_authors_and_unusable_urls():
    assert normalize_authors(["Alice", " Alice ", ""]) == ["Alice"]
    assert normalize_paper(title="A", year=2025, venue="CHI", url="javascript:alert(1)").url is None


def test_abstract_reconstruction_preserves_positions_and_repeated_words():
    assert reconstruct_abstract({"VR.": [3], "We": [0, 2], "study": [1]}) == "We study We VR."
    assert reconstruct_abstract(None) is None


def test_title_matching_checks_year_and_author(paper_factory):
    paper = paper_factory()
    assert matches_metadata(paper, "An Example Paper.", 2025, ["Alice Smith"])
    assert not matches_metadata(paper, "Unrelated paper", 2025, ["Alice Smith"])
    assert not matches_metadata(paper, paper.title, 2020, ["Alice Smith"])
    assert not matches_metadata(paper, paper.title, 2025, ["Someone Else"])
