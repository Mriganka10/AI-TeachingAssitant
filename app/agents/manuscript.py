"""Manuscript handling for the research assistant: structure, locations and section revision.

1. `parse_manuscript` turns extracted text (from PDF, DOCX or TXT) into a title, front matter and
   numbered sections of paragraphs. IDs such as "S3" and "S3.P2" let the review point at exact
   places, and let the revision be compared paragraph by paragraph with the original.
2. `revision_issues` checks a revised block of text: original citations and numbers kept, no
   invented citations or numbers, a sensible length and no mention of AI. The revision pipeline
   itself lives in `app/agents/revision.py`.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from app.agents.plain_language import _SELF_REFERENCE
from app.core.config import settings

_KNOWN_HEADINGS = re.compile(
    r"^(?:abstract|summary|introduction|background|related work|literature review|"
    r"theoretical (?:framework|background)|conceptual framework|hypothes[ie]s|"
    r"(?:materials and |research )?methods?|methodology|research design|study design|"
    r"data(?: and methods?)?|participants|measures|procedure|experimental (?:setup|design)|"
    r"experiments?|results?(?: and discussion)?|findings|analysis|evaluation|discussion|"
    r"general discussion|conclusions?(?: and future work)?|limitations|future (?:work|research)|"
    r"implications|recommendations|acknowledge?ments?|funding|conflicts? of interest|"
    r"declarations?|ethics(?: statement)?|data availability(?: statement)?|author contributions|"
    r"references|bibliography|works cited|literature cited|appendix(?: [a-z0-9]+)?|"
    r"supplementary (?:material|materials|information))$",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"^(?:\d+(?:\.\d+){0,3}\.?|[IVXLC]+\.|[A-H]\.)\s+(?=[A-Z])")
_INLINE_ABSTRACT = re.compile(r"^(abstract|summary)\s*[:.—–-]\s*(\S.*)$", re.IGNORECASE)
_REFERENCES = re.compile(r"^(references|bibliography|works cited|literature cited)$", re.IGNORECASE)
_BACK_MATTER = re.compile(
    r"^(acknowledge?ments?|funding|conflicts? of interest|declarations?|author contributions|"
    r"data availability(?: statement)?|ethics(?: statement)?)$",
    re.IGNORECASE,
)
_APPENDIX = re.compile(r"^(appendix(?: [a-z0-9]+)?|supplementary .*)$", re.IGNORECASE)
_REFERENCE_START = re.compile(r"^(?:\[\d+\]|\d+\.\s|[A-Z][A-Za-z'\-]+,\s+(?:[A-Z]\.|[A-Z][a-z]+))")
_SENTENCE_END = re.compile(r"[.!?:;\"'”)\]]$")
_PLACEHOLDER = re.compile(r"\[Author:[^\]]*\]")


@dataclass
class Section:
    id: str
    heading: str
    kind: str  # front | abstract | body | back | references | appendix
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class Manuscript:
    title: str
    sections: list[Section]
    truncated: bool = False

    def section(self, section_id: str) -> Section | None:
        return next((item for item in self.sections if item.id == section_id), None)

    def to_payload(self) -> dict:
        """The manuscript as the reviewer sees it, with section and paragraph IDs."""
        return {
            "title": self.title,
            "truncated": self.truncated,
            "sections": [
                {
                    "id": section.id,
                    "heading": section.heading,
                    "kind": section.kind,
                    "paragraphs": [
                        {"id": f"{section.id}.P{index}", "text": text}
                        for index, text in enumerate(section.paragraphs, start=1)
                    ],
                }
                for section in self.sections
            ],
        }

    @classmethod
    def from_payload(cls, payload: dict) -> Manuscript:
        return cls(
            title=str(payload.get("title") or ""),
            truncated=bool(payload.get("truncated")),
            sections=[
                Section(
                    id=str(item.get("id")),
                    heading=str(item.get("heading") or ""),
                    kind=str(item.get("kind") or "body"),
                    paragraphs=[str(p.get("text") or "") for p in item.get("paragraphs") or []],
                )
                for item in payload.get("sections") or []
            ],
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _clean_lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in lines]
    lines = ["" if re.fullmatch(r"\[Page \d+\]|\d{1,4}|Page \d+( of \d+)?", line) else line
             for line in lines]
    # Running headers and footers repeat on many pages.
    counts = Counter(line for line in lines if line and len(line.split()) <= 10)
    repeated = {
        line
        for line, count in counts.items()
        if not _KNOWN_HEADINGS.match(_heading_text(line))
        and (count >= 3 or (count >= 2 and len(line.split()) <= 6 and not _SENTENCE_END.search(line)))
    }
    return ["" if line in repeated else line for line in lines]


def _heading_text(line: str) -> str:
    return _NUMBERED.sub("", line).strip().rstrip(":").strip()


def _is_heading(line: str) -> bool:
    if not line or len(line) > 110:
        return False
    words = line.split()
    if len(words) > 12 or line.endswith((".", ",", ";")) and not _NUMBERED.match(line):
        return False
    bare = _heading_text(line)
    if not bare or not re.search(r"[A-Za-z]", bare):
        return False
    if _KNOWN_HEADINGS.match(bare):
        return True
    if "," in bare or "@" in bare:  # author lines and affiliations, not headings
        return False
    if _NUMBERED.match(line) and len(words) <= 9 and not line.endswith("."):
        significant = [word for word in bare.split() if len(word) > 3]
        capitalised = [word for word in significant if word[0].isupper()]
        return bool(significant) and len(capitalised) / len(significant) >= 0.5
    letters = [character for character in bare if character.isalpha()]
    return len(letters) >= 4 and bare.isupper() and len(words) <= 8


def _join(previous: str, line: str) -> str:
    if previous.endswith("-") and len(previous) > 1 and previous[-2].isalpha() and line[:1].islower():
        return previous[:-1] + line
    return f"{previous} {line}" if previous else line


def _paragraphs(lines: list[str], *, references: bool = False) -> list[str]:
    """Rebuild paragraphs from wrapped lines (PDF) or one-line paragraphs (DOCX, TXT)."""
    non_empty = [line for line in lines if line]
    if not non_empty:
        return []
    blank_ratio = (len(lines) - len(non_empty)) / max(1, len(non_empty))
    lengths = sorted(len(line) for line in non_empty)
    typical = lengths[int(len(lengths) * 0.8)] if lengths else 0
    one_line_paragraphs = statistics.median(lengths) > 160
    paragraphs: list[str] = []
    current = ""
    for index, line in enumerate(lines):
        if not line:
            if current and (blank_ratio >= 0.15 or one_line_paragraphs):
                paragraphs.append(current)
                current = ""
            continue
        if references and current and _REFERENCE_START.match(line):
            paragraphs.append(current)
            current = ""
        if one_line_paragraphs and not references:
            if current:
                paragraphs.append(current)
            current = line
            continue
        current = _join(current, line)
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        short = len(line) < typical * 0.8
        ends_paragraph = not references and blank_ratio < 0.15 and short
        if ends_paragraph and _SENTENCE_END.search(line) and (
            not next_line or next_line[:1].isupper() or next_line[:1].isdigit()
        ):
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    return [re.sub(r"\s+", " ", paragraph).strip() for paragraph in paragraphs if paragraph.strip()]


def _kind_for(heading: str, current_kind: str) -> str:
    bare = _heading_text(heading)
    if re.match(r"^(abstract|summary)$", bare, re.IGNORECASE):
        return "abstract"
    if _REFERENCES.match(bare):
        return "references"
    if _APPENDIX.match(bare):
        return "appendix"
    if _BACK_MATTER.match(bare):
        return "back"
    if current_kind in {"references", "appendix"} and not _KNOWN_HEADINGS.match(bare):
        return current_kind  # sub-headings inside an appendix stay there
    return "body"


# Journal mastheads, licences and article metadata found above the title on first pages.
_MASTHEAD = re.compile(
    r"^(?:journal of|proceedings of|vol(?:ume)?\.?\s*\d|issn|e-issn|doi\b|https?://|www\.|"
    r"received:?\s|accepted:?\s|published:?\s|copyright|\u00a9|available online|"
    r"original (?:article|research)|research article|review article)",
    re.IGNORECASE,
)


# Author and affiliation lines: initials ("A. Sen"), institutions, or "Name and Name,".
_AUTHOR_LINE = re.compile(
    r"\b[A-Z]\.\s?[A-Z]?[a-z]|\b(?:department|university|institute|school|college|faculty|"
    r"laboratory|centre|center|hospital)\b|\*|\u2020",
    re.IGNORECASE,
)


def _take_title(front: list[str]) -> tuple[str, list[str]]:
    """Pick the title among the first lines: the longest plausible one, plus a wrapped line."""
    candidates = [
        index for index, line in enumerate(front[:4])
        if 3 <= len(line.split()) <= 30 and not re.search(r"[@]|\d{4}", line)
        and not line.endswith(".") and not _AUTHOR_LINE.search(line)
    ]
    if not candidates:
        return "", front
    index = max(candidates, key=lambda i: len(front[i].split()))
    title = front[index]
    rest = front[:index] + front[index + 1:]
    following = front[index + 1] if index + 1 < len(front) else ""
    # A long title often wraps onto a second line that has no commas, digits or e-mail.
    if following and len(following.split()) <= 12 and not re.search(r"[@\d,]", following) \
            and len(title.split()) >= 4:
        title = f"{title} {following}"
        rest = front[:index] + front[index + 2:]
    return title, rest


def parse_manuscript(text: str) -> Manuscript:
    lines = _clean_lines(text)
    blocks: list[tuple[str, str, list[str]]] = []  # heading, kind, lines
    heading, kind, buffer = "", "front", []
    for line in lines:
        inline = _INLINE_ABSTRACT.match(line)
        if inline and kind == "front":
            blocks.append((heading, kind, buffer))
            heading, kind, buffer = inline.group(1).title(), "abstract", [inline.group(2)]
            continue
        if _is_heading(line):
            blocks.append((heading, kind, buffer))
            kind = _kind_for(line, kind)
            heading, buffer = line.rstrip(":").strip(), []
            continue
        buffer.append(line)
    blocks.append((heading, kind, buffer))

    title = ""
    sections: list[Section] = []
    for block_heading, block_kind, block_lines in blocks:
        if block_kind == "front":
            front = [line for line in block_lines if line and not _MASTHEAD.search(line)]
            if not front:
                continue
            if not title:
                title, front = _take_title(front)
            paragraphs = front
            if len(" ".join(front).split()) > 250:  # no headings at all: the "front" is the body
                paragraphs = _paragraphs(block_lines)
                block_kind = "body"
            if paragraphs:
                sections.append(Section("", block_heading, block_kind, paragraphs))
            continue
        paragraphs = _paragraphs(block_lines, references=block_kind == "references")
        if paragraphs or block_heading:
            sections.append(Section("", block_heading, block_kind, paragraphs))

    sections = _split_unstructured(sections)
    number = 1
    for position, section in enumerate(sections):
        if position == 0 and section.kind == "front":
            section.id = "S0"
        else:
            section.id = f"S{number}"
            number += 1
    manuscript = Manuscript(title=title or "Untitled manuscript", sections=sections)
    return _truncate(manuscript)


def _split_unstructured(sections: list[Section]) -> list[Section]:
    """A paper without recognisable headings is split into parts of a manageable size."""
    if any(section.heading for section in sections):
        return sections
    output: list[Section] = []
    for section in sections:
        if section.kind == "front" or len(" ".join(section.paragraphs).split()) <= 1200:
            output.append(section)
            continue
        part: list[str] = []
        for paragraph in section.paragraphs:
            part.append(paragraph)
            if len(" ".join(part).split()) >= 900:
                output.append(Section("", "", "body", part))
                part = []
        if part:
            output.append(Section("", "", "body", part))
    return output


def _truncate(manuscript: Manuscript) -> Manuscript:
    remaining = settings.research_max_manuscript_chars
    for section in manuscript.sections:
        kept: list[str] = []
        for paragraph in section.paragraphs:
            if remaining <= 0:
                manuscript.truncated = True
                break
            kept.append(paragraph[:remaining])
            remaining -= len(paragraph)
        section.paragraphs = kept
    manuscript.sections = [s for s in manuscript.sections if s.paragraphs or s.heading]
    return manuscript


def location_label(manuscript: Manuscript, location: str) -> str:
    """Turn "S3.P2, S4" into "Methods, para. 2; Results" for readers."""
    text = str(location or "").strip()
    if not text or text.lower() in {"whole manuscript", "global"}:
        return "Whole manuscript"
    labels = []
    for match in re.finditer(r"\bS(\d+)(?:\.P(\d+))?\b", text):
        section = manuscript.section(f"S{match.group(1)}")
        if section is None:
            continue
        name = section.heading or ("Front matter" if section.kind == "front" else f"Part {match.group(1)}")
        labels.append(f"{name}, para. {match.group(2)}" if match.group(2) else name)
    return "; ".join(dict.fromkeys(labels)) or text


# ---------------------------------------------------------------------------
# Integrity checks for a revised section
# ---------------------------------------------------------------------------

_CITATION = re.compile(
    r"\[\d+(?:\s*[,–—-]\s*\d+)*\]"
    r"|\((?:[A-Z][A-Za-z'\-]+(?:\s+(?:et al\.|and|&)\s*[A-Z]?[A-Za-z'\-]*)?,?\s+\d{4}[a-z]?"
    r"(?:;\s*)?)+\)"
    r"|[A-Z][A-Za-z'\-]+(?:\s+et al\.|\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+)?\s+\(\d{4}[a-z]?\)"
)
_NUMBER = re.compile(r"(?<![\w.])[-−]?\d+(?:[.,]\d+)*%?")


def _tokens(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(re.sub(r"\s+", " ", match.group(0)) for match in pattern.finditer(text))


_MARKER = re.compile(r"\[\[(?:N\d+|TABLE\d)\]\]")
_STRUCTURAL = re.compile(r"\b(?:Table|Tables|Section|Sections|Fig\.|Figure|Figures|Eq\.|Equation)s?\s+\d+(?:\.\d+)*",
                         re.IGNORECASE)


def revision_issues(
    original: list[str],
    revised: list[str],
    *,
    allowed_citations: set[str] | None = None,
    allowed_numbers: set[str] | None = None,
    length: tuple[float, float] = (0.6, 1.6),
    keep_original: bool = True,
) -> list[str]:
    """Integrity problems in a revised block of text.

    `allowed_citations` and `allowed_numbers` widen what may appear beyond the original block
    (for example citations used elsewhere in the paper, or numbers stated by a verified source).
    With `keep_original`, every citation and number of the original must survive.
    """
    before = " ".join(original)
    after = _PLACEHOLDER.sub(" ", " ".join(revised))
    after = _STRUCTURAL.sub(" ", _MARKER.sub(" ", after))
    issues: list[str] = []
    cited_before, cited_after = _tokens(_CITATION, before), _tokens(_CITATION, after)
    if keep_original:
        lost = cited_before - cited_after
        if lost:
            issues.append("Keep these citations exactly as written: " + ", ".join(sorted(lost)[:12]))
    added = {item for item in cited_after - cited_before if item not in (allowed_citations or set())}
    if added:
        issues.append(
            "Remove citations that are not in the manuscript (cite new sources only with "
            "[[N1]]-style markers): " + ", ".join(sorted(added)[:12])
        )
    numbers_before, numbers_after = set(_tokens(_NUMBER, before)), set(_tokens(_NUMBER, after))
    if keep_original:
        missing = numbers_before - numbers_after
        if missing:
            issues.append("Keep these numbers exactly as written: " + ", ".join(sorted(missing)[:15]))
    new = numbers_after - numbers_before - (allowed_numbers or set())
    if new:
        issues.append(
            "Do not add numbers that are not in the manuscript or a cited source (use an "
            "[Author: ...] placeholder instead): " + ", ".join(sorted(new)[:15])
        )
    words_before, words_after = len(before.split()), len(after.split())
    low, high = length
    if words_before >= 40 and not low <= words_after / max(1, words_before) <= high:
        issues.append(
            f"The revision has {words_after} words against {words_before} in the original; keep "
            f"it between {low:.0%} and {high:.0%} of the original length."
        )
    if _SELF_REFERENCE.search(after):
        issues.append("Remove any mention of AI, a model or an assistant from the manuscript text.")
    return issues


def citation_tokens(text: str) -> set[str]:
    return set(_tokens(_CITATION, text))


def number_tokens(text: str) -> set[str]:
    return set(_tokens(_NUMBER, text))
