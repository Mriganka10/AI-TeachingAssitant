"""Verified literature for manuscript revisions.

New references may only enter a revised paper after this module confirms them against a
bibliographic registry: Crossref for DOIs, arXiv for preprints. The registry's own metadata
(authors, title, year, venue) is used, never the model's, and a source whose registered title
does not match the claimed one is dropped. This keeps invented or mis-attributed citations out of
the manuscript.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from app.core.config import settings

logger = logging.getLogger(__name__)

CROSSREF = "https://api.crossref.org/works/{doi}"
ARXIV = "https://export.arxiv.org/api/query?id_list={arxiv_id}"
_ATOM = "{http://www.w3.org/2005/Atom}"
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.IGNORECASE)


@dataclass
class Source:
    key: str  # "N1", "N2", ... used as [[N1]] markers while writing
    title: str
    authors: list[str]  # "Given Family"
    year: str
    venue: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    key_points: list[str] = field(default_factory=list)
    relevance: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Source:
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: item for key, item in value.items() if key in known})

    @property
    def first_surname(self) -> str:
        return _surname(self.authors[0]) if self.authors else (self.venue or "Anon.")


def normalize_doi(value: str) -> str:
    match = _DOI.search(urllib.parse.unquote(value or ""))
    return match.group(0).rstrip(".,;)").lower() if match else ""


def normalize_arxiv(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^arxiv:\s*", "", text, flags=re.IGNORECASE).removesuffix(".pdf")
    match = _ARXIV_ID.fullmatch(text) or _ARXIV_ID.search(text)
    return re.sub(r"v\d+$", "", match.group(0)) if match else ""


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(word) > 2}


def titles_match(claimed: str, registered: str) -> bool:
    a, b = _words(claimed), _words(registered)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.6


def _surname(name: str) -> str:
    parts = name.replace(",", " ").split()
    return parts[-1] if parts else name


def _get(url: str) -> bytes:
    agent = "ProfessorAI/1.0"
    if settings.research_contact_email:
        agent += f" (mailto:{settings.research_contact_email})"
    request = urllib.request.Request(url, headers={"User-Agent": agent, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=settings.research_source_timeout_seconds) as response:
        return response.read(2_000_000)


def lookup_doi(doi: str) -> dict | None:
    try:
        payload = json.loads(_get(CROSSREF.format(doi=urllib.parse.quote(doi, safe="/"))))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.info("Crossref lookup failed for %s: %s", doi, exc)
        return None
    message = payload.get("message") or {}
    title = " ".join(message.get("title") or []).strip()
    if not title:
        return None
    authors = [
        " ".join(part for part in (person.get("given"), person.get("family")) if part).strip()
        for person in message.get("author") or []
        if person.get("family") or person.get("name")
    ] or [person.get("name", "") for person in message.get("author") or [] if person.get("name")]
    date = (message.get("issued") or message.get("published") or {}).get("date-parts") or [[None]]
    year = str(date[0][0]) if date and date[0] and date[0][0] else ""
    return {
        "title": re.sub(r"<[^>]+>", "", title),
        "authors": [author for author in authors if author],
        "year": year,
        "venue": " ".join(message.get("container-title") or []).strip(),
        "volume": str(message.get("volume") or ""),
        "issue": str(message.get("issue") or ""),
        "pages": str(message.get("page") or ""),
        "doi": (message.get("DOI") or doi).lower(),
        "url": f"https://doi.org/{(message.get('DOI') or doi).lower()}",
    }


def lookup_arxiv(arxiv_id: str) -> dict | None:
    try:
        root = ET.fromstring(_get(ARXIV.format(arxiv_id=urllib.parse.quote(arxiv_id))))
    except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
        logger.info("arXiv lookup failed for %s: %s", arxiv_id, exc)
        return None
    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        return None
    title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
    if not title or title.lower() == "error":
        return None
    authors = [" ".join((node.findtext(f"{_ATOM}name") or "").split())
               for node in entry.findall(f"{_ATOM}author")]
    published = entry.findtext(f"{_ATOM}published") or ""
    return {
        "title": title,
        "authors": [author for author in authors if author],
        "year": published[:4],
        "venue": "arXiv preprint",
        "arxiv_id": arxiv_id,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def verify(candidate: dict) -> dict | None:
    """Return registry metadata for a claimed source, or None if it cannot be confirmed."""
    claimed_title = str(candidate.get("title") or "")
    doi = normalize_doi(str(candidate.get("doi") or "")) or normalize_doi(str(candidate.get("url") or ""))
    if doi:
        record = lookup_doi(doi)
        if record and titles_match(claimed_title, record["title"]):
            return record
    arxiv_id = normalize_arxiv(str(candidate.get("arxiv_id") or "")) or (
        normalize_arxiv(str(candidate.get("url") or "")) if "arxiv" in str(candidate.get("url") or "").lower()
        else ""
    )
    if arxiv_id:
        record = lookup_arxiv(arxiv_id)
        if record and titles_match(claimed_title, record["title"]):
            return record
    return None


def verify_all(candidates: list[dict], *, existing_titles: list[str] = ()) -> list[Source]:
    """Verify candidates in parallel, drop duplicates and works the paper already cites."""
    limited = candidates[: max(0, settings.research_max_new_sources) * 2]
    if not limited:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(limited)), thread_name_prefix="verify") as pool:
        records = list(pool.map(verify, limited))
    sources: list[Source] = []
    seen: set[str] = set()
    for candidate, record in zip(limited, records, strict=True):
        if not record:
            continue
        identity = record.get("doi") or record.get("arxiv_id") or record["title"].lower()
        if identity in seen or any(titles_match(record["title"], title) for title in existing_titles):
            continue
        seen.add(identity)
        sources.append(Source(
            key=f"N{len(sources) + 1}",
            title=record["title"],
            authors=record.get("authors") or [],
            year=record.get("year") or "",
            venue=record.get("venue") or "",
            volume=record.get("volume") or "",
            issue=record.get("issue") or "",
            pages=record.get("pages") or "",
            doi=record.get("doi") or "",
            arxiv_id=record.get("arxiv_id") or "",
            url=record.get("url") or "",
            key_points=[str(point) for point in candidate.get("key_points") or [] if str(point).strip()][:4],
            relevance=str(candidate.get("relevance") or ""),
        ))
        if len(sources) >= settings.research_max_new_sources:
            break
    return sources


# ---------------------------------------------------------------------------
# Reference formatting in the manuscript's own style
# ---------------------------------------------------------------------------


def _initials(name: str) -> tuple[str, str]:
    parts = name.replace(",", " ").split()
    if not parts:
        return "", ""
    initials = " ".join(f"{part[0]}." for part in parts[:-1] if part[0].isalpha())
    return initials, parts[-1]


def format_ieee(source: Source) -> str:
    names = []
    for author in source.authors[:6]:
        initials, family = _initials(author)
        names.append(f"{initials} {family}".strip())
    if len(source.authors) > 6:
        authors = f"{names[0]} et al."
    elif len(names) > 2:
        authors = ", ".join(names[:-1]) + ", and " + names[-1]
    else:
        authors = " and ".join(names)
    parts = [f"{authors}, “{source.title},”" if authors else f"“{source.title},”"]
    if source.arxiv_id:
        parts.append(f"arXiv:{source.arxiv_id}, {source.year}.")
        return " ".join(parts)
    venue = f"{source.venue}," if source.venue else ""
    details = ", ".join(
        item for item in (
            f"vol. {source.volume}" if source.volume else "",
            f"no. {source.issue}" if source.issue else "",
            f"pp. {source.pages}" if source.pages else "",
            source.year,
        ) if item
    )
    parts.append(f"{venue} {details}".strip() + ",")
    parts.append(f"doi: {source.doi}." if source.doi else "")
    return " ".join(part for part in parts if part).rstrip(",") + ("" if source.doi else ".")


def format_apa(source: Source) -> str:
    names = []
    for author in source.authors[:20]:
        initials, family = _initials(author)
        names.append(f"{family}, {initials}".strip(", "))
    if len(names) > 1:
        authors = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        authors = names[0] if names else source.venue
    if source.arxiv_id:
        return f"{authors} ({source.year}). {source.title}. arXiv. https://arxiv.org/abs/{source.arxiv_id}"
    venue = source.venue
    if source.volume:
        venue += f", {source.volume}" + (f"({source.issue})" if source.issue else "")
    if source.pages:
        venue += f", {source.pages}"
    tail = f" https://doi.org/{source.doi}" if source.doi else ""
    return f"{authors} ({source.year}). {source.title}. {venue}.{tail}".replace(". .", ".")


def in_text_author_year(source: Source) -> str:
    count = len(source.authors)
    if count >= 3:
        names = f"{source.first_surname} et al."
    elif count == 2:
        names = f"{_surname(source.authors[0])} & {_surname(source.authors[1])}"
    else:
        names = source.first_surname
    return f"{names}, {source.year}"
