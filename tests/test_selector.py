import random

import pytest

from src.recommender.selector import select_papers


def test_empty_candidates():
    assert select_papers([], count=1) == []


def test_single_candidate(paper_factory):
    paper = paper_factory()
    assert select_papers([paper], count=10) == [paper]


def test_multiple_candidates_are_unique_and_bounded(paper_factory):
    papers = [paper_factory(doi=f"10.1145/{i}") for i in range(20)]
    result = select_papers(papers + papers, count=4, rng=random.Random(7))
    assert len(result) == len({paper.id for paper in result}) == 4
    assert all(paper in papers for paper in result)
    assert result == select_papers(papers, count=4, rng=random.Random(7))
    assert select_papers(papers, count=0) == []


def test_rejects_unknown_method_and_negative_count():
    with pytest.raises(ValueError):
        select_papers([], method="embedding")
    with pytest.raises(ValueError):
        select_papers([], count=-1)
