import json

import httpx

from src.database.supabase import SupabaseRepository


def test_sent_history_is_paginated_and_failed_rows_are_not_selected(http_factory):
    offsets = []

    def handler(request):
        assert request.url.params["status"] == "eq.sent"
        assert request.url.params["order"] == "id.asc"
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        rows = (
            [{"paper_id": "doi:1"}, {"paper_id": "doi:2"}]
            if offset == 0
            else [{"paper_id": "doi:3"}]
        )
        return httpx.Response(200, json=rows)

    repository = SupabaseRepository(http_factory(handler), "https://project.supabase.co", "test")
    repository.PAGE_SIZE = 2
    assert repository.get_sent_ids() == {"doi:1", "doi:2", "doi:3"}
    assert offsets == [0, 2]


def test_paper_upserts_are_batched(paper_factory, http_factory):
    sizes = []

    def handler(request):
        assert request.url.params["on_conflict"] == "id"
        assert request.headers["Prefer"] == "resolution=merge-duplicates"
        sizes.append(len(json.loads(request.content)))
        return httpx.Response(201)

    repository = SupabaseRepository(http_factory(handler), "https://project.supabase.co", "test")
    repository.UPSERT_BATCH_SIZE = 2
    repository.upsert_papers([paper_factory(doi=f"10.1145/{i}") for i in range(5)])
    assert sizes == [2, 2, 1]


def test_history_retries_use_same_id_and_secret_key_auth(http_factory):
    requests = []

    def handler(request):
        assert request.headers["apikey"] == "sb_secret_test"
        assert "Authorization" not in request.headers
        requests.append(json.loads(request.content))
        return httpx.Response(503 if len(requests) == 1 else 201)

    repository = SupabaseRepository(
        http_factory(handler, max_retries=3), "https://project.supabase.co", "sb_secret_test"
    )
    repository.record_recommendation("doi:10.1145/123.456", channel="hci", status="sent")
    assert len(requests) == 2
    assert requests[0]["id"] == requests[1]["id"]
    assert requests[0]["sent_at"] is not None


def test_failed_history_has_no_sent_timestamp(http_factory):
    requests = []
    repository = SupabaseRepository(
        http_factory(lambda req: requests.append(json.loads(req.content)) or httpx.Response(201)),
        "https://project.supabase.co",
        "legacy",
    )
    repository.record_recommendation("doi:paper", channel="hci", status="failed")
    assert requests[0]["sent_at"] is None
