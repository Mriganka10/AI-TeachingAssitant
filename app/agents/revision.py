"""Publication-grade revision of a reviewed manuscript.

The pipeline turns the peer review into a revised paper in four steps:

1. Sources: with web search on, find recent and seminal work the revision needs, then keep only
   works confirmed by Crossref or arXiv (`app/agents/sources.py`).
2. Plan: decide, for every body section, whether to keep, revise or expand it, which sections to
   add (for example limitations or a comparison of approaches), where tables help, the keywords,
   and which verified sources each section cites.
3. Write: each planned section is written in parallel by a strong academic writer, then checked:
   original citations and numbers kept, no citation or number that is not in the manuscript or a
   verified source, sensible length, no mention of AI. One repair is allowed; after that a revised
   section falls back to the original text and an added section is dropped.
4. Assemble: final heading numbering, citations converted to the paper's own style (numeric or
   author-year), new references merged into the reference list, tables numbered.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.agents.contracts import LITERATURE_SCHEMA, REVISION_PLAN_SCHEMA, REVISION_SCHEMA
from app.agents.manuscript import (
    _NUMBERED,
    Manuscript,
    Section,
    citation_tokens,
    number_tokens,
    revision_issues,
)
from app.agents.plain_language import polish_academic
from app.agents.prompts import LITERATURE_SYSTEM, REVISION_PLAN_SYSTEM, REVISION_SYSTEM
from app.agents.sources import (
    Source,
    format_apa,
    format_ieee,
    in_text_author_year,
    verify_all,
)
from app.core.config import settings
from app.core.openai_client import create_openai_client

logger = logging.getLogger(__name__)

_SOURCE_MARKER = re.compile(r"\[\[(N\d+)\]\]")
_SOURCE_RUN = re.compile(r"(?:\[\[N\d+\]\][\s,;]*)*\[\[N\d+\]\]")
_TABLE_MARKER = re.compile(r"\[\[TABLE(\d)\]\]")
_KEYWORDS_LINE = re.compile(r"^\s*(?:key\s*words|keywords|index terms)\s*[:—–-]", re.IGNORECASE)
_NUMBER_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)(\.?)\s+")
LENGTH = {"revise": (0.7, 1.9), "expand": (0.9, 3.2), "abstract": (0.6, 1.7)}


@dataclass
class Planned:
    source_id: str  # "" for an added section
    heading: str  # without numbering
    level: int
    action: str  # keep | revise | expand | add
    goals: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    source_keys: list[str] = field(default_factory=list)
    table_brief: str = ""
    label: str = ""  # final heading as shown, with numbering if the paper uses it


# ---------------------------------------------------------------------------
# Model calls
# ---------------------------------------------------------------------------


def _mock_mode() -> bool:
    return settings.llm_service_mode.lower() == "mock"


def _structured(client, *, instructions: str, payload: dict, name: str, schema: dict,
                web_search: bool = False, max_tokens: int = 16_000) -> dict:
    model = settings.openai_model
    kwargs: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False),
        "max_output_tokens": max_tokens,
        "store": False,
        "prompt_cache_key": f"professor-ai:research:{name}-v2",
        "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
    }
    if web_search:
        kwargs["tools"] = [{"type": "web_search"}]
    if model.startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": settings.research_reasoning_effort}
    response = client.responses.create(**kwargs)
    if getattr(response, "status", None) == "incomplete":
        raise RuntimeError(f"The {name} response was incomplete.")
    value = json.loads(response.output_text)
    if not isinstance(value, dict):
        raise TypeError(f"The {name} response was not a JSON object.")
    return value


# ---------------------------------------------------------------------------
# 1. Sources
# ---------------------------------------------------------------------------


def find_sources(client, manuscript: Manuscript, review: dict, request: dict) -> list[Source]:
    if _mock_mode() or not request.get("use_web_search"):
        return []
    references = next((s for s in manuscript.sections if s.kind == "references"), None)
    payload = {
        "manuscript_title": manuscript.title,
        "discipline": (review.get("manuscript_profile") or {}).get("discipline"),
        "paper_type": (review.get("manuscript_profile") or {}).get("paper_type"),
        "research_gaps": review.get("research_gaps"),
        "literature_positioning": review.get("literature_positioning"),
        "major_concerns": [c.get("issue") for c in review.get("major_concerns") or []],
        "existing_references": (references.paragraphs if references else [])[:150],
        "maximum_sources": settings.research_max_new_sources,
    }
    try:
        found = _structured(client, instructions=LITERATURE_SYSTEM, payload=payload,
                            name="literature", schema=LITERATURE_SCHEMA, web_search=True)
    except Exception as exc:  # noqa: BLE001 - the revision continues without new sources
        logger.warning("Literature search failed: %s", exc)
        return []
    existing = references.paragraphs if references else []
    return verify_all(found.get("sources") or [], existing_titles=existing)


# ---------------------------------------------------------------------------
# 2. Plan
# ---------------------------------------------------------------------------


def _bare(heading: str) -> str:
    return _NUMBER_PREFIX.sub("", heading).strip() or heading


def _original_level(heading: str) -> int:
    match = _NUMBER_PREFIX.match(heading)
    return 2 if match and "." in match.group(1) else 1


def _uses_numbering(sections: list[Section]) -> tuple[bool, str]:
    body = [s for s in sections if s.kind == "body" and s.heading]
    numbered = [s for s in body if _NUMBERED.match(s.heading)]
    if not body or len(numbered) < max(1, len(body) // 2):
        return False, ""
    match = _NUMBER_PREFIX.match(numbered[0].heading)
    return True, (match.group(2) if match else ".")


def _mock_plan(manuscript: Manuscript) -> dict:
    body = [s for s in manuscript.sections if s.kind == "body"]
    sections = [
        {"source_id": s.id, "heading": _bare(s.heading), "level": _original_level(s.heading),
         "action": "keep" if index == len(body) - 1 and len(body) > 2 else "revise",
         "goals": ["Clarify the argument."], "addresses": ["Writing"], "source_keys": [],
         "table": {"wanted": False, "brief": ""}}
        for index, s in enumerate(body)
    ]
    sections.append({
        "source_id": "", "heading": "Limitations and Future Work", "level": 1, "action": "add",
        "goals": ["State the limits of the design."], "addresses": ["Major concern 1"],
        "source_keys": [], "table": {"wanted": True, "brief": "Limitations and remedies"},
    })
    return {"keywords": ["retrieval practice", "spacing", "statistics education"],
            "abstract_goals": ["State design and main result."], "sections": sections}


def normalize_plan(raw: dict, manuscript: Manuscript, sources: list[Source]) -> tuple[list[Planned], dict]:
    """Make the plan safe: every body section exactly once, limits on added sections and tables."""
    body = {s.id: s for s in manuscript.sections if s.kind == "body"}
    known_keys = {source.key for source in sources}
    planned: list[Planned] = []
    seen: set[str] = set()
    added = tables = 0
    for entry in raw.get("sections") or []:
        if not isinstance(entry, dict):
            continue
        source_id = str(entry.get("source_id") or "").strip()
        action = str(entry.get("action") or "revise")
        if source_id:
            if source_id not in body or source_id in seen:
                continue
            seen.add(source_id)
            action = "expand" if action == "add" else action
            heading = str(entry.get("heading") or "").strip() or _bare(body[source_id].heading)
        else:
            if added >= settings.research_max_new_sections or not str(entry.get("heading") or "").strip():
                continue
            added += 1
            action = "add"
            heading = str(entry["heading"]).strip()
        table = entry.get("table") if isinstance(entry.get("table"), dict) else {}
        wanted = bool(table.get("wanted")) and action != "keep" and tables < 3
        tables += int(wanted)
        planned.append(Planned(
            source_id=source_id,
            heading=_bare(heading),
            level=2 if int(entry.get("level") or 1) == 2 else 1,
            action=action if action in {"keep", "revise", "expand", "add"} else "revise",
            goals=[str(g) for g in entry.get("goals") or []][:6],
            addresses=[str(a) for a in entry.get("addresses") or []][:6],
            source_keys=[str(k) for k in entry.get("source_keys") or [] if str(k) in known_keys],
            table_brief=str(table.get("brief") or "") if wanted else "",
        ))
    # Body sections the plan forgot keep their place after their original predecessor.
    order = list(body)
    for index, section_id in enumerate(order):
        if section_id in seen:
            continue
        original = body[section_id]
        item = Planned(section_id, _bare(original.heading) if original.heading else "",
                       _original_level(original.heading), "revise")
        previous = next((p for p in reversed(order[:index]) if p in seen), None)
        position = next((i + 1 for i, p in enumerate(planned) if p.source_id == previous), 0)
        planned.insert(position, item)
        seen.add(section_id)
    if planned and planned[0].level == 2:
        planned[0].level = 1
    extras = {
        "keywords": [str(k).strip() for k in raw.get("keywords") or [] if str(k).strip()][:8],
        "abstract_goals": [str(g) for g in raw.get("abstract_goals") or []][:6],
    }
    return planned, extras


def label_headings(planned: list[Planned], manuscript: Manuscript) -> bool:
    numbered, dot = _uses_numbering(manuscript.sections)
    first = second = 0
    for item in planned:
        if not item.heading:
            item.label = ""
            continue
        if not numbered:
            item.label = item.heading
        elif item.level == 1:
            first, second = first + 1, 0
            item.label = f"{first}{dot} {item.heading}"
        else:
            second += 1
            item.label = f"{max(first, 1)}.{second} {item.heading}"
    return numbered


def plan_revision(client, manuscript: Manuscript, review: dict, sources: list[Source],
                  request: dict) -> tuple[list[Planned], dict]:
    if _mock_mode():
        return normalize_plan(_mock_plan(manuscript), manuscript, sources)
    payload = {
        "manuscript": {
            "title": manuscript.title,
            "sections": [
                {"id": s.id, "heading": s.heading, "kind": s.kind,
                 "words": len(" ".join(s.paragraphs).split()),
                 "opening": " ".join(s.paragraphs)[:600]}
                for s in manuscript.sections
            ],
        },
        "review": {key: review.get(key) for key in (
            "manuscript_profile", "summary", "recommendation", "major_concerns", "minor_issues",
            "claims_check", "methodology_review", "reporting_checklist", "research_gaps",
            "literature_positioning", "writing_and_structure", "revision_plan", "abstract_feedback",
            "venue_fit")},
        "new_sources": [_source_brief(source) for source in sources],
        "target_venue": request.get("target_venue", ""),
        "author_instructions": request.get("instructions", ""),
        "maximum_new_sections": settings.research_max_new_sections,
    }
    raw = _structured(client, instructions=REVISION_PLAN_SYSTEM, payload=payload,
                      name="revision_plan", schema=REVISION_PLAN_SCHEMA, max_tokens=8_000)
    return normalize_plan(raw, manuscript, sources)


def _source_brief(source: Source) -> dict:
    return {
        "key": source.key,
        "cite_as": f"[[{source.key}]]",
        "authors": source.authors[:3] + (["et al."] if len(source.authors) > 3 else []),
        "year": source.year,
        "title": source.title,
        "venue": source.venue,
        "key_points": source.key_points,
        "relevance": source.relevance,
    }


# ---------------------------------------------------------------------------
# 3. Write
# ---------------------------------------------------------------------------


def _review_items_for(review: dict, section_id: str) -> dict:
    pattern = re.compile(rf"\b{re.escape(section_id)}(?:\.P\d+)?\b") if section_id else None

    def mine(items: Any) -> list:
        return [
            item for item in items or []
            if isinstance(item, dict) and pattern is not None
            and pattern.search(str(item.get("location", "")))
        ]

    return {
        "major_concerns": mine(review.get("major_concerns")),
        "minor_issues": mine(review.get("minor_issues")),
        "claims_check": mine(review.get("claims_check")),
    }


def _chunks(paragraphs: list[str], limit: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    words = 0
    for paragraph in paragraphs:
        size = len(paragraph.split())
        if current and words + size > limit:
            chunks.append(current)
            current, words = [], 0
        current.append(paragraph)
        words += size
    if current:
        chunks.append(current)
    return chunks


def _table_text(tables: list[dict]) -> list[str]:
    return [" ".join([t.get("caption", ""), *t.get("columns", []), *(" ".join(r) for r in t.get("rows", []))])
            for t in tables]


def _clean_tables(raw: Any) -> list[dict]:
    tables = []
    for table in raw or []:
        if not isinstance(table, dict):
            continue
        columns = [str(c).strip() for c in table.get("columns") or [] if str(c).strip()][:7]
        if len(columns) < 2:
            continue
        rows = []
        for row in table.get("rows") or []:
            cells = [str(cell).strip() for cell in row][: len(columns)]
            cells += [""] * (len(columns) - len(cells))
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append({
                "caption": str(table.get("caption") or "").strip().rstrip("."),
                "columns": columns,
                "rows": rows[:20],
                "after": max(0, int(table.get("after_paragraph") or 0)),
            })
    return tables[:2]


def _mock_write(item: Planned, original: list[str]) -> dict:
    if item.action == "add":
        paragraphs = [
            (
                "The design compares two cohorts taught in different semesters, so differences "
                "between cohorts may account for part of the observed effect [Author: describe "
                "any baseline comparison of the two cohorts]. The findings should therefore be "
                "read as an association rather than as a causal estimate. Students also chose how "
                "many quizzes to complete, so the link between completion and examination scores "
                "may partly reflect motivation rather than practice alone."
            ),
            (
                "Further work should assign students to conditions within the same semester and "
                "report effect sizes with confidence intervals. [[TABLE1]] summarises the main "
                "limitations and how future studies can address them. Replication at other "
                "institutions, with different instructors and examination formats, would show "
                "whether the benefit holds beyond the setting studied here."
            ),
        ]
        tables = [{"caption": "Limitations of the present study and possible remedies",
                   "columns": ["Limitation", "Consequence", "Remedy"],
                   "rows": [["Cohorts not randomised", "Possible confounding", "Randomise within a semester"],
                            ["Single institution", "Limited generality", "Replicate at other institutions"]],
                   "after_paragraph": 2}]
        return {"paragraphs": paragraphs, "tables": tables, "changes": [
            {"change": "Added a section on limitations and future work.", "reason": "Requested by the review.",
             "addresses": "Major concern 1"}], "author_notes": ["Describe any baseline comparison of the cohorts."]}
    revised = [polish_academic(re.sub(r"\bvery\s+", "", re.sub(r"\bshows\b", "indicates", p))) for p in original]
    return {"paragraphs": revised, "tables": [], "changes": [], "author_notes": []}


def write_block(client, item: Planned, original: list[str], *, context: dict, allowed: dict,
                part: tuple[int, int], with_table: bool) -> dict:
    """Write one block; one repair; on failure return None so the caller can fall back."""
    if _mock_mode():
        result = _mock_write(item, original)
    else:
        payload = {
            **context,
            "section": {
                "final_heading": item.label or item.heading,
                "action": item.action,
                "part": f"{part[0]} of {part[1]}" if part[1] > 1 else "whole section",
                "goals": item.goals,
                "addresses": item.addresses,
                "review_items_for_this_section": _review_items_for(context["_review"], item.source_id),
                "sources_to_cite": [s for s in context["new_sources"] if s["key"] in item.source_keys],
                "table_requested": item.table_brief if with_table else "",
                "original_paragraphs": original,
            },
        }
        payload.pop("_review", None)
        result = None
    for attempt in range(2):
        if result is None:
            result = _structured(client, instructions=REVISION_SYSTEM, payload=payload,
                                 name="revised_section", schema=REVISION_SCHEMA)
        originals = set(original)
        paragraphs = [
            text if text in originals else polish_academic(text)
            for text in (str(p).strip() for p in result.get("paragraphs") or []) if text
        ]
        tables = _clean_tables(result.get("tables")) if with_table or item.action == "add" else []
        tables = [{**t, "caption": polish_academic(t["caption"])} for t in tables]
        issues = _block_issues(item, original, paragraphs, tables, allowed)
        if not issues:
            return {
                "paragraphs": paragraphs,
                "tables": tables,
                "changes": [c for c in result.get("changes") or [] if isinstance(c, dict)],
                "author_notes": [str(n) for n in result.get("author_notes") or [] if str(n).strip()],
            }
        logger.info("Section '%s' attempt %s failed checks: %s", item.heading, attempt + 1, issues)
        if _mock_mode() or attempt:
            break
        payload = {**payload, "previous_draft": {"paragraphs": paragraphs, "tables": tables},
                   "problems_to_fix": issues,
                   "task": "Return a corrected version of the same section that fixes every problem."}
        result = None
    return {}


def _block_issues(item: Planned, original: list[str], paragraphs: list[str], tables: list[dict],
                  allowed: dict) -> list[str]:
    if not paragraphs:
        return ["The section is empty."]
    text = paragraphs + _table_text(tables)
    joined = " ".join(text)
    issues = []
    unknown = sorted(set(_SOURCE_MARKER.findall(joined)) - allowed["keys"])
    if unknown:
        issues.append("Only these source markers exist: " + ", ".join(sorted(allowed["keys"])) +
                      ". Remove " + ", ".join(unknown) + ".")
    referenced = {int(n) for n in _TABLE_MARKER.findall(joined)}
    if any(n > len(tables) for n in referenced):
        issues.append("The text refers to a table that was not returned.")
    cited_numbers: set[str] = set()
    for key in set(_SOURCE_MARKER.findall(joined)):
        cited_numbers |= allowed["numbers_by_key"].get(key, set())
    issues += _repetition_issues(original, paragraphs)
    if item.action == "add":
        words = len(" ".join(paragraphs).split())
        if not 100 <= words <= 2200:
            issues.append(f"An added section should have 150 to 1,500 words; this one has {words}.")
        issues += revision_issues([], text, allowed_citations=allowed["citations"],
                                  allowed_numbers=allowed["numbers"] | cited_numbers,
                                  length=(0, 99), keep_original=False)
    else:
        issues += revision_issues(original, text, allowed_citations=allowed["citations"],
                                  allowed_numbers=allowed["numbers"] | cited_numbers,
                                  length=LENGTH.get(item.action, LENGTH["revise"]))
    return issues


def _sentences(text: str) -> list[str]:
    return [re.sub(r"\W+", " ", part).strip().lower()
            for part in re.split(r"(?<=[.!?])\s+", text) if len(part.split()) >= 8]


def _repetition_issues(original: list[str], paragraphs: list[str]) -> list[str]:
    """Repeated sentences or near-identical paragraphs are padding, not revision."""
    seen_before = set(_sentences(" ".join(original)))
    counts: dict[str, int] = {}
    for sentence in _sentences(" ".join(paragraphs)):
        counts[sentence] = counts.get(sentence, 0) + 1
    repeated = [s for s, n in counts.items() if n > 1 and s not in seen_before]
    if repeated:
        return [f"Remove repeated sentences ({len(repeated)} sentence(s) appear more than once)."]
    return []


# ---------------------------------------------------------------------------
# 4. Assemble
# ---------------------------------------------------------------------------


def _citation_style(manuscript: Manuscript) -> str:
    text = " ".join(" ".join(s.paragraphs) for s in manuscript.sections if s.kind != "references")
    numeric = len(re.findall(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", text))
    author_year = len(re.findall(r"\(\s*[A-Z][A-Za-z'\-]+[^()]{0,60}?\d{4}[a-z]?\s*\)", text))
    return "numeric" if numeric >= author_year else "author_year"


def _max_reference_number(manuscript: Manuscript) -> int:
    numbers = [0]
    for section in manuscript.sections:
        for paragraph in section.paragraphs:
            if section.kind == "references":
                match = re.match(r"^\[?(\d{1,4})[\].]", paragraph)
                if match:
                    numbers.append(int(match.group(1)))
            for group in re.findall(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]", paragraph):
                numbers += [int(n) for n in re.findall(r"\d+", group)]
    return max(numbers)


def _reference_prefix(manuscript: Manuscript) -> str:
    references = next((s for s in manuscript.sections if s.kind == "references"), None)
    first = references.paragraphs[0] if references and references.paragraphs else ""
    if re.match(r"^\[\d+\]", first):
        return "brackets"
    if re.match(r"^\d+\.\s", first):
        return "dot"
    return "none"


def _reference_format(manuscript: Manuscript) -> str:
    """IEEE or APA for new references, following the paper's own reference list."""
    references = next((s for s in manuscript.sections if s.kind == "references"), None)
    entries = references.paragraphs if references else []
    if entries and _reference_prefix(manuscript) != "none":
        return "ieee"
    apa_like = sum(bool(re.search(r"\(\d{4}[a-z]?\)\.", entry)) for entry in entries)
    if entries and apa_like >= len(entries) / 2:
        return "apa"
    return "ieee" if _citation_style(manuscript) == "numeric" else "apa"


def finalize_citations(sections: list[dict], sources: list[Source], manuscript: Manuscript) -> list[dict]:
    """Replace [[Nk]] markers with the paper's citation style; return the cited new references."""
    by_key = {source.key: source for source in sources}
    style = _citation_style(manuscript)
    order: list[str] = []

    def texts(section: dict):
        yield from section["revised"]
        for table in section.get("tables") or []:
            yield table["caption"]
            for row in table["rows"]:
                yield from row

    for section in sections:
        for text in texts(section):
            for key in _SOURCE_MARKER.findall(text):
                if key in by_key and key not in order:
                    order.append(key)
    start = _max_reference_number(manuscript)
    numbers = {key: start + index for index, key in enumerate(order, start=1)}

    def replace(match: re.Match) -> str:
        keys = [k for k in _SOURCE_MARKER.findall(match.group(0)) if k in by_key]
        if not keys:
            return ""
        if style == "numeric":
            return "[" + ", ".join(str(numbers[k]) for k in sorted(set(keys), key=numbers.get)) + "]"
        return "(" + "; ".join(in_text_author_year(by_key[k]) for k in dict.fromkeys(keys)) + ")"

    def convert(text: str) -> str:
        return re.sub(r"\s+([.,;:])", r"\1", _SOURCE_RUN.sub(replace, text))

    for section in sections:
        section["revised"] = [convert(p) for p in section["revised"]]
        for table in section.get("tables") or []:
            table["caption"] = convert(table["caption"])
            table["rows"] = [[convert(cell) for cell in row] for row in table["rows"]]

    prefix = _reference_prefix(manuscript)
    reference_format = _reference_format(manuscript)
    cited = []
    for key in order:
        source = by_key[key]
        text = format_ieee(source) if reference_format == "ieee" else format_apa(source)
        if style == "numeric":
            label = {"brackets": f"[{numbers[key]}] ", "dot": f"{numbers[key]}. "}.get(prefix, "")
            text = label + text
        cited.append({**source.to_dict(), "number": numbers[key], "text": text, "style": style,
                      "format": reference_format})
    return cited


def _number_tables(sections: list[dict]) -> None:
    count = 0
    for section in sections:
        local = {}
        for index, table in enumerate(section.get("tables") or [], start=1):
            count += 1
            table["number"] = count
            local[index] = count
        section["revised"] = [
            _TABLE_MARKER.sub(lambda m, local=local: f"Table {local.get(int(m.group(1)), int(m.group(1)))}", p)
            for p in section["revised"]
        ]


def build_revision(manuscript: Manuscript, review: dict, request: dict) -> dict:
    client = None if _mock_mode() else create_openai_client(api_key=settings.openai_api_key)
    sources = find_sources(client, manuscript, review, request)
    try:
        planned, extras = plan_revision(client, manuscript, review, sources, request)
    except Exception as exc:  # noqa: BLE001 - fall back to revising every section in place
        logger.warning("Revision plan failed, revising sections in place: %s", exc)
        planned, extras = normalize_plan({"sections": []}, manuscript, sources)
    numbered = label_headings(planned, manuscript)

    full_text = " ".join(" ".join(s.paragraphs) for s in manuscript.sections)
    briefs = [_source_brief(source) for source in sources]
    numbers_by_key = {
        source.key: number_tokens(" ".join([source.year, source.title, source.venue, *source.key_points]))
        for source in sources
    }
    allowed = {
        "keys": {source.key for source in sources},
        "citations": citation_tokens(full_text),
        "numbers": number_tokens(full_text),
        "numbers_by_key": numbers_by_key,
    }
    abstract = next((s for s in manuscript.sections if s.kind == "abstract"), None)
    references = next((s for s in manuscript.sections if s.kind == "references"), None)
    context = {
        "manuscript_title": manuscript.title,
        "abstract": " ".join(abstract.paragraphs) if abstract else "",
        "paper_profile": review.get("manuscript_profile"),
        "review_summary": review.get("summary"),
        "final_outline": [{"heading": p.label or p.heading, "action": p.action} for p in planned],
        "citation_style": _citation_style(manuscript),
        "existing_references": (references.paragraphs if references else [])[:150],
        "new_sources": briefs,
        "target_venue": request.get("target_venue", ""),
        "author_instructions": request.get("instructions", ""),
        "_review": review,
    }
    originals = {s.id: s for s in manuscript.sections}

    jobs: list[tuple[int, int, int, list[str], bool]] = []
    for position, item in enumerate(planned):
        if item.action == "keep":
            continue
        source = originals.get(item.source_id)
        paragraphs = list(source.paragraphs) if source else []
        if item.action == "add" or not paragraphs:
            jobs.append((position, 1, 1, paragraphs, True))
            continue
        chunks = _chunks(paragraphs, settings.research_revision_max_words)
        for number, chunk in enumerate(chunks, start=1):
            jobs.append((position, number, len(chunks), chunk, number == 1))

    def work(job):
        position, number, total, chunk, with_table = job
        item = planned[position]
        try:
            result = write_block(client, item, chunk, context=context, allowed=allowed,
                                 part=(number, total), with_table=with_table)
        except Exception as exc:  # noqa: BLE001 - one failed section must not stop the paper
            logger.warning("Writing '%s' failed: %s", item.heading, exc)
            result = {}
        return position, number, chunk, result

    results: dict[int, list] = {}
    workers = max(1, min(settings.research_revision_workers, len(jobs) or 1))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="revise") as pool:
        for position, number, chunk, result in pool.map(work, jobs):
            results.setdefault(position, []).append((number, chunk, result))

    body_out: list[dict] = []
    dropped: list[str] = []
    for position, item in enumerate(planned):
        source = originals.get(item.source_id)
        original = list(source.paragraphs) if source else []
        parts = sorted(results.get(position, []), key=lambda entry: entry[0])
        if item.action == "keep" or not parts:
            revised, tables, changes, notes, status = original, [], [], [], "unchanged"
        else:
            revised, tables, changes, notes, statuses = [], [], [], [], set()
            for _, chunk, result in parts:
                if result:
                    offset = len(revised)
                    revised += result["paragraphs"]
                    tables += [{**t, "after": offset + min(t["after"], len(result["paragraphs"]))}
                               for t in result["tables"]]
                    changes += result["changes"]
                    notes += result["author_notes"]
                    statuses.add("ok")
                else:
                    revised += chunk
                    statuses.add("fallback")
            if item.action == "add" and statuses != {"ok"}:
                dropped.append(item.heading)
                continue
            if statuses == {"ok"}:
                status = "added" if item.action == "add" else "revised"
            elif statuses == {"fallback"}:
                status = "kept_original"
            else:
                status = "partly_revised"
        body_out.append({
            "id": item.source_id or f"A{position + 1}",
            "source_id": item.source_id,
            "heading": item.label if item.heading else (source.heading if source else ""),
            "original_heading": source.heading if source else "",
            "level": item.level,
            "kind": "body",
            "action": item.action,
            "status": status,
            "original": original,
            "revised": revised,
            "tables": tables,
            "changes": changes,
            "author_notes": notes,
            "addresses": item.addresses,
        })

    if dropped:  # renumber headings after a dropped section
        kept = [p for p in planned if not (p.action == "add" and p.heading in dropped)]
        label_headings(kept, manuscript)
        labels = iter(p.label for p in kept)
        for section in body_out:
            label = next(labels)
            if section["heading"]:
                section["heading"] = label or section["heading"]

    abstract_out = _revise_abstract(client, abstract, extras, body_out, context, allowed) if abstract else None
    _number_tables(body_out)
    cited = finalize_citations(body_out + ([abstract_out] if abstract_out else []), sources, manuscript)

    sections_out: list[dict] = []
    body_inserted = False
    for section in manuscript.sections:
        if section.kind == "body":
            if not body_inserted:
                sections_out += body_out
                body_inserted = True
            continue
        if section.kind == "abstract" and abstract_out:
            sections_out.append(abstract_out)
            continue
        entry = _unchanged(section)
        if section.kind == "references":
            entry = _merge_references(entry, cited)
        sections_out.append(entry)
    if cited and not references:
        refs = _merge_references({**_unchanged(Section("R1", "References", "references", [])),
                                  "original_heading": ""}, cited)
        appendix_at = next((i for i, s in enumerate(sections_out) if s["kind"] == "appendix"), len(sections_out))
        sections_out.insert(appendix_at, refs)
    if not body_inserted:
        sections_out += body_out

    return {
        "title": manuscript.title,
        "keywords": extras.get("keywords") or [],
        "numbered_headings": numbered,
        "citation_style": _citation_style(manuscript),
        "sections": sections_out,
        "new_references": cited,
        "dropped_sections": dropped,
    }


def _unchanged(section: Section) -> dict:
    return {
        "id": section.id, "source_id": section.id, "heading": section.heading,
        "original_heading": section.heading, "level": 1,
        "kind": section.kind, "action": "keep", "status": "unchanged",
        "original": list(section.paragraphs), "revised": list(section.paragraphs), "tables": [],
        "changes": [], "author_notes": [], "addresses": [],
    }


def _merge_references(entry: dict, cited: list[dict]) -> dict:
    if not cited:
        return entry
    new = [item["text"] for item in cited]
    # Author-year lists are alphabetical. Numbered citations point at list positions, so new
    # entries go at the end and the existing order is never changed.
    if cited[0]["style"] == "author_year":
        revised = sorted(entry["original"] + new, key=lambda text: re.sub(r"[^a-z]", "", text.lower()))
    else:
        revised = entry["original"] + new
    return {**entry, "revised": revised, "status": "revised", "changes": [
        {"change": f"Added {len(new)} verified reference{'s' if len(new) != 1 else ''}.",
         "reason": "Supports material added in the revision.", "addresses": "Literature coverage"}]}


def _revise_abstract(client, abstract: Section, extras: dict, body_out: list[dict], context: dict,
                     allowed: dict) -> dict:
    original = [p for p in abstract.paragraphs if not _KEYWORDS_LINE.match(p)]
    item = Planned(abstract.id, abstract.heading, 1, "revise", goals=extras.get("abstract_goals") or [
        "State the problem, approach, main findings and contribution precisely."])
    outline = [{"heading": s["heading"], "action": s["action"], "changes": [c.get("change") for c in s["changes"]][:4]}
               for s in body_out]
    try:
        result = write_block(client, item, original, context={**context, "revised_outline": outline},
                             allowed=allowed, part=(1, 1), with_table=False) if original else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Abstract revision failed: %s", exc)
        result = {}
    revised = result.get("paragraphs") if result else None
    return {
        "id": abstract.id, "source_id": abstract.id, "heading": abstract.heading,
        "original_heading": abstract.heading, "level": 1,
        "kind": "abstract", "action": "revise",
        "status": "revised" if revised else "kept_original",
        "original": list(abstract.paragraphs),
        "revised": revised or original,
        "tables": [],
        "changes": result.get("changes", []) if result else [],
        "author_notes": result.get("author_notes", []) if result else [],
        "addresses": [],
    }
