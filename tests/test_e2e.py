import json
import logging

import httpx
import yaml

from src.config import DEFAULT_CONFIG_DIR
from src.main import main


def test_full_cli_pipeline_saves_history_and_does_not_redeliver(monkeypatch, tmp_path, caplog):
    """Run the real CLI/adapters twice against fake HTTP services, never sending externally."""
    settings = yaml.safe_load((DEFAULT_CONFIG_DIR / "settings.yaml").read_text())
    settings["publication"]["years_back"] = 1
    settings["collection"]["request_interval"] = 0
    venues = yaml.safe_load((DEFAULT_CONFIG_DIR / "venues.yaml").read_text())
    for venue in venues["venues"]:
        venue["enabled"] = venue["id"] == "chi"
    (tmp_path / "settings.yaml").write_text(yaml.safe_dump(settings))
    (tmp_path / "venues.yaml").write_text(yaml.safe_dump(venues))
    secrets = {
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/fake-private-hook",
        "DATABASE_URL": "",
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_KEY": "sb_secret_fake-private-key",
        "OPENAI_API_KEY": "fake-private-openai-key",
        "OPENALEX_API_KEY": "fake-private-openalex-key",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    saved_papers = {}
    history = []
    messages = []
    order = []

    def handler(request):
        host = request.url.host
        path = request.url.path
        if host == "dblp.org":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "hits": {
                            "@total": "1",
                            "hit": [
                                {
                                    "info": {
                                        "title": "A Realistic Fixture Paper.",
                                        "venue": "CHI",
                                        "year": "2025",
                                        "authors": {
                                            "author": [
                                                {"text": "Alice Smith"},
                                                {"text": "Bob Jones"},
                                            ]
                                        },
                                        "doi": "10.1145/123.456",
                                        "ee": "https://doi.org/10.1145/123.456",
                                        "type": "Conference and Workshop Papers",
                                    }
                                }
                            ],
                        }
                    }
                },
            )
        if host == "api.openalex.org":
            return httpx.Response(
                200,
                json={
                    "doi": "https://doi.org/10.1145/123.456",
                    "cited_by_count": 12,
                    "topics": [{"display_name": "Virtual Reality"}],
                    "abstract_inverted_index": {"We": [0], "evaluate": [1], "VR.": [2]},
                },
            )
        if host == "api.openai.com":
            assert json.loads(request.content)["store"] is False
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "VRの操作方法を評価した。"}
                            ],
                        }
                    ],
                },
            )
        if host == "fake.supabase.co":
            assert request.headers["apikey"] == secrets["SUPABASE_KEY"]
            if request.method == "GET":
                return httpx.Response(
                    200, json=list(saved_papers.values()) if path.endswith("/papers") else history
                )
            payload = json.loads(request.content)
            if path.endswith("/papers"):
                saved_papers.update({paper["id"]: paper for paper in payload})
            else:
                order.append("history")
                history.append(payload)
            return httpx.Response(201)
        if host == "hooks.slack.com":
            order.append("slack")
            messages.append(json.loads(request.content))
            return httpx.Response(200, text="ok")
        raise AssertionError(f"Unexpected service: {host}")

    real_client = httpx.Client
    monkeypatch.setattr(
        "src.main.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with caplog.at_level(logging.INFO):
        assert main(["--config-dir", str(tmp_path), "--year", "2025"]) == 0
        assert main(["--config-dir", str(tmp_path), "--year", "2025"]) == 1
    assert order == ["slack", "history"]
    assert len(messages) == len(history) == 1
    assert history[0]["status"] == "sent"
    assert history[0]["paper_id"] == "doi:10.1145/123.456"
    assert saved_papers[history[0]["paper_id"]]["abstract"] == "We evaluate VR."
    assert "CHI 2025" in messages[0]["text"]
    assert "VRの操作方法を評価した。" in messages[0]["text"]
    assert "Virtual Reality" in messages[0]["text"]
    for name in ["SLACK_WEBHOOK_URL", "SUPABASE_KEY", "OPENAI_API_KEY", "OPENALEX_API_KEY"]:
        assert secrets[name] not in caplog.text
