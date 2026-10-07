import hashlib
import html
import json
import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit

from src.models.paper import Paper


def normalize_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unicodedata.normalize("NFKC", html.unescape(text)).split())


def normalize_title(value: str) -> str:
    return normalize_text(value).rstrip(".")


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip()
    if re.match(r"^https?://(?:dx\.)?doi\.org/", doi, flags=re.I):
        doi = urlsplit(doi).path.lstrip("/")
    doi = unquote(doi)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I).strip().lower()
    return doi if re.fullmatch(r"10\.\d{4,9}/\S+", doi) else None


def normalize_authors(authors: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            normalize_text(re.sub(r"\s+\d{4}$", "", author))
            for author in authors
            if author and author.strip()
        )
    )


def fingerprint(title: str, year: int, authors: list[str]) -> str:
    first = normalize_authors(authors[:1])
    data = [normalize_title(title).casefold(), year, first[0].casefold() if first else ""]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def generate_paper_id(title: str, year: int, authors: list[str], doi: str | None = None) -> str:
    normalized = normalize_doi(doi)
    return f"doi:{normalized}" if normalized else f"sha256:{fingerprint(title, year, authors)}"


def valid_url(value: str | None) -> str | None:
    if value and urlsplit(value).scheme in {"http", "https"} and urlsplit(value).netloc:
        return value
    return None


def normalize_paper(**data: Any) -> Paper:
    data["title"] = normalize_title(data["title"])
    data["authors"] = normalize_authors(data.get("authors") or [])
    data["doi"] = normalize_doi(data.get("doi"))
    data["venue"] = {"VR": "IEEE VR", "ieee-vr": "IEEE VR"}.get(
        data["venue"], normalize_text(data["venue"])
    )
    data["year"] = int(data["year"])
    data["abstract"] = normalize_text(data["abstract"]) if data.get("abstract") else None
    data["url"] = f"https://doi.org/{data['doi']}" if data["doi"] else valid_url(data.get("url"))
    data["id"] = generate_paper_id(data["title"], data["year"], data["authors"], data["doi"])
    return Paper.model_validate(data)


def reconstruct_abstract(index: dict[str, list[int]] | None) -> str | None:
    if not index:
        return None
    words = [(position, word) for word, positions in index.items() for position in positions]
    return normalize_text(" ".join(word for _, word in sorted(words))) or None


def matches_metadata(paper: Paper, title: str, year: int | None, authors: list[str]) -> bool:
    """Title searches must verify identity; never attach an unrelated abstract/DOI."""
    if normalize_title(paper.title).casefold() != normalize_title(title).casefold():
        return False
    if year is None or abs(paper.year - year) > 1:
        return False
    if paper.authors:
        normalized = normalize_authors(authors)
        last = paper.authors[0].split()[-1].casefold()
        return any(author.split()[-1].casefold() == last for author in normalized)
    return True
