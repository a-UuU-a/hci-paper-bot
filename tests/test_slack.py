import httpx
import pytest

from src.config import SlackSettings
from src.errors import BotError
from src.notification.slack import SlackNotifier, format_slack_message


def test_slack_contains_required_fields_and_author_limit(paper_factory):
    paper = paper_factory(authors=["Alice", "Bob", "Carol", "Dan"], topics=["VR", "Haptics"])
    text = format_slack_message(paper, "触覚を使った操作方法を評価した。")
    assert "Alice, Bob, Carol, et al." in text
    assert "Dan" not in text
    assert "CHI 2025" in text
    assert "https://doi.org/10.1145/123.456" in text
    assert "触覚を使った操作方法を評価した。" in text
    assert "VR · Haptics" in text
    assert paper.title in text


def test_topics_can_be_hidden_and_url_used_without_doi(paper_factory):
    paper = paper_factory(doi=None, topics=["Haptics"])
    text = format_slack_message(paper, "A study of haptics.", SlackSettings(show_topics=False))
    assert "Haptics" not in text
    assert "https://example.org/paper" in text
    assert "Abstract（原文抜粋）" in text


def test_external_metadata_cannot_inject_slack_mentions(paper_factory):
    # Paper.model_copy intentionally preserves the original markup for the formatting test.
    paper = paper_factory().model_copy(update={"title": "Title <!channel> & test"})
    text = format_slack_message(paper, "結果を説明する。 <!here>")
    assert "<!channel>" not in text and "<!here>" not in text
    assert "&lt;!channel&gt; &amp;" in text


def test_webhook_requires_ok_confirmation(paper_factory, http_factory):
    http = http_factory(lambda req: httpx.Response(200, text="not-ok"))
    with pytest.raises(BotError, match="did not confirm"):
        SlackNotifier(http, "https://example.org/webhook", SlackSettings()).send(
            paper_factory(), "日本語の要約。"
        )
