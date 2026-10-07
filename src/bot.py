import logging
import random
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import Settings, Venue
from src.database.supabase import PaperRepository
from src.errors import BotError, SummaryError
from src.models.paper import Paper
from src.notification.slack import Notifier, format_slack_message
from src.recommender.filter import filter_papers
from src.recommender.scorer import NeutralScorer
from src.recommender.selector import select_papers
from src.services.normalization import fingerprint
from src.services.paper_service import PaperService, merge_metadata
from src.summarizer.base import Summarizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    collected: int
    candidates: int
    skipped: int
    delivered: int


def paper_fingerprint(paper: Paper) -> str:
    return fingerprint(paper.title, paper.year, paper.authors)


def run_bot(
    *,
    settings: Settings,
    venues: list[Venue],
    service: PaperService,
    repository: PaperRepository,
    summarizer: Summarizer,
    notifier: Notifier | None = None,
    dry_run: bool = False,
    year: int | None = None,
    rng: random.Random | None = None,
) -> RunResult:
    end_year = year or datetime.now(ZoneInfo("Asia/Tokyo")).year
    start_year = end_year - settings.publication.years_back + 1
    logger.info("HCI Paper Bot started%s", " (dry run)" if dry_run else "")
    logger.info("Enabled venues: %s", ", ".join(venue.name for venue in venues if venue.enabled))
    logger.info("Publication range: %d-%d", start_year, end_year)
    sent_ids = repository.get_sent_ids()
    cached = repository.get_papers(start_year, end_year)
    cached_by_id = {paper.id: paper for paper in cached}
    cached_by_fingerprint = {paper_fingerprint(paper): paper for paper in cached}
    sent_fingerprints = {paper_fingerprint(paper) for paper in cached if paper.id in sent_ids}
    collected = service.collect(venues, range(start_year, end_year + 1))
    papers = dict(cached_by_id)
    for paper in collected:
        previous = cached_by_id.get(paper.id) or cached_by_fingerprint.get(paper_fingerprint(paper))
        if previous:
            paper = merge_metadata(paper, previous.model_dump(), previous.source)
        papers[paper.id] = paper
    repository.upsert_papers(list(papers.values()))
    candidates = filter_papers(
        papers.values(),
        venues=venues,
        start_year=start_year,
        end_year=end_year,
        require_abstract=False,
        sent_ids=sent_ids,
        exclude_sent=settings.recommendation.exclude_sent,
    )
    # A hash-only cache entry and its later DOI entry represent the same paper.
    # Remove the old alias before shuffling so it gets only one chance in the draw.
    fingerprints_with_doi = {paper_fingerprint(paper) for paper in candidates if paper.doi}
    candidates = [
        paper
        for paper in candidates
        if paper.doi or paper_fingerprint(paper) not in fingerprints_with_doi
    ]
    if settings.recommendation.exclude_sent:
        candidates = [
            paper for paper in candidates if paper_fingerprint(paper) not in sent_fingerprints
        ]
    logger.info(
        "Collected: %d; unsent candidates before abstract enrichment: %d",
        len(collected),
        len(candidates),
    )
    # A uniform random permutation permits skipping missing abstracts/failed summaries.
    ordered = select_papers(
        candidates, method=settings.recommendation.selection_method, count=len(candidates), rng=rng
    )
    delivered = skipped = 0
    attempted_ids: set[str] = set()
    attempted_fingerprints: set[str] = set()
    for raw in ordered[: settings.collection.max_enrichment_attempts]:
        paper = service.enrich(raw)
        identity = paper_fingerprint(paper)
        if (
            paper.id in attempted_ids
            or identity in attempted_fingerprints
            or (settings.recommendation.exclude_sent and paper.id in sent_ids)
        ):
            skipped += 1
            continue
        attempted_ids.add(paper.id)
        attempted_fingerprints.add(identity)
        repository.upsert_papers([paper])
        if not filter_papers(
            [paper],
            venues=venues,
            start_year=start_year,
            end_year=end_year,
            require_abstract=settings.publication.require_abstract,
            sent_ids=sent_ids,
            exclude_sent=settings.recommendation.exclude_sent,
        ):
            logger.info("Skipping paper without required metadata: %s", paper.id)
            skipped += 1
            continue
        try:
            summary = summarizer.summarize(paper)
        except SummaryError as exc:
            logger.warning("Skipping paper %s: %s", paper.id, exc)
            skipped += 1
            continue
        logger.info("Selected paper: %s", paper.title)
        if dry_run:
            print(format_slack_message(paper, summary, settings.slack))
        else:
            if notifier is None:
                raise BotError("Slack notifier is required")
            try:
                notifier.send(paper, summary)
            except BotError:
                try:
                    repository.record_recommendation(
                        paper.id, channel=settings.slack.channel, status="failed"
                    )
                except BotError:
                    logger.error("Failed to save Slack failure history")
                raise
            logger.info("Slack message sent")
            try:
                repository.record_recommendation(
                    paper.id,
                    channel=settings.slack.channel,
                    status="sent",
                    score=NeutralScorer().score(paper),
                )
            except BotError:
                raise BotError(
                    "Slack message sent, but history save failed; check the channel "
                    "before rerunning to avoid duplicate delivery"
                ) from None
            logger.info("Recommendation saved")
        sent_ids.add(paper.id)
        delivered += 1
        if delivered >= settings.recommendation.papers_per_day:
            break
    logger.info("Finished: %d delivered, %d skipped", delivered, skipped)
    if delivered < settings.recommendation.papers_per_day:
        raise BotError(
            f"Only {delivered}/{settings.recommendation.papers_per_day} papers available "
            "or summarized; check metadata, summary settings and candidate budget"
        )
    return RunResult(len(collected), len(candidates), skipped, delivered)
