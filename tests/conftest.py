import httpx
import pytest

from src.config import Venue
from src.http import RetryingHTTPClient
from src.services.normalization import normalize_paper


@pytest.fixture
def paper_factory():
    def make(**overrides):
        return normalize_paper(
            **{
                "title": "An Example Paper",
                "authors": ["Alice Smith", "Bob Jones"],
                "venue": "CHI",
                "year": 2025,
                "abstract": "We study interaction in virtual reality.",
                "doi": "10.1145/123.456",
                "url": "https://example.org/paper",
                **overrides,
            }
        )

    return make


@pytest.fixture
def venues():
    return [
        Venue(id="chi", name="CHI", publisher="acm", dblp_stream="conf/chi", dblp_venues=["CHI"]),
        Venue(
            id="uist",
            name="UIST",
            publisher="acm",
            enabled=False,
            dblp_stream="conf/uist",
            dblp_venues=["UIST"],
        ),
    ]


@pytest.fixture
def http_factory():
    clients = []

    def make(handler, max_retries=0, sleeps=None):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return RetryingHTTPClient(
            client, max_retries=max_retries, sleep=(sleeps if sleeps is not None else []).append
        )

    yield make
    for client in clients:
        client.close()
