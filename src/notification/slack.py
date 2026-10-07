import re
from typing import Protocol
from urllib.parse import quote

from src.config import SlackSettings
from src.errors import BotError
from src.http import RetryingHTTPClient
from src.models.paper import Paper


class Notifier(Protocol):
    def send(self, paper: Paper, summary: str) -> None: ...


def escape_mrkdwn(value: str) -> str:
    # Prevent external metadata from injecting mentions or Slack link markup.
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_slack_message(paper: Paper, summary: str, settings: SlackSettings | None = None) -> str:
    settings = settings or SlackSettings()
    authors = ", ".join(paper.authors[: settings.max_authors]) or "著者情報なし"
    if len(paper.authors) > settings.max_authors:
        authors += ", et al."
    link = f"https://doi.org/{paper.doi}" if paper.doi else paper.url
    if not link:
        raise BotError("Paper has no DOI or URL")
    # Encode Slack's markup delimiters while keeping an ordinary URL readable.
    link = quote(link, safe=":/?#[]@!$&'()*+,;=%")
    label = "日本語要約" if re.search(r"[ぁ-ゖァ-ヺ]", summary) else "Abstract（原文抜粋）"
    lines = [
        f"📄 *{escape_mrkdwn(paper.title)}*",
        "",
        f"👤 {escape_mrkdwn(authors)}",
        f"🏛 *{escape_mrkdwn(paper.venue)} {paper.year}*",
        "",
        f"📝 *{label}*",
        escape_mrkdwn(summary),
        "",
        f"🔗 <{link}>",
    ]
    if settings.show_topics and paper.topics:
        lines.extend(["", "🏷 " + escape_mrkdwn(" · ".join(paper.topics[:5]))])
    return "\n".join(lines)


class SlackNotifier:
    def __init__(self, http: RetryingHTTPClient, webhook_url: str, settings: SlackSettings):
        self.http = http
        self.webhook_url = webhook_url
        self.settings = settings

    def send(self, paper: Paper, summary: str) -> None:
        response = self.http.request(
            "POST",
            self.webhook_url,
            service="Slack",
            json={
                "text": format_slack_message(paper, summary, self.settings),
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )
        if response.status_code != 200 or response.text.strip() != "ok":
            raise BotError("Slack did not confirm message delivery")
