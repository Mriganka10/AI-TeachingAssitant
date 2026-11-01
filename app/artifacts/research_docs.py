"""Exports for the research assistant.

From one review result this module writes:

- the review report (DOCX and PDF): a reviewer-style report for the author;
- the revised manuscript (DOCX and PDF): a clean, submission-style copy of the improved paper;
- the revised manuscript with tracked changes (DOCX): every edit shown as a Word revision the
  author can accept or reject.

Each document looks like ordinary academic work: neutral metadata (the author's name only if
given), no tool names, labels or watermarks, standard fonts and layout. Placeholders the author
must complete ("[Author: ...]") are highlighted so they cannot be missed.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.agents.manuscript import Manuscript, location_label
from app.artifacts.generator import (
    _add_page_number,
    _set_cell_margins,
    _set_table_borders,
)

ACCENT = "1F4E79"
INK = "222222"
MUTED = "5F6B76"
RULE = "C9D1D9"
SOFT = "F2F5F8"
PLACEHOLDER = re.compile(r"(\[Author:[^\]]*\])")

DECISIONS = {
    "accept": ("Accept", "2E7D32", "E8F3E9"),
    "minor_revision": ("Minor revision", "1F6FB2", "E8F1FA"),
    "major_revision": ("Major revision", "B26A00", "FCF1E3"),
    "reject_and_resubmit": ("Reject and resubmit", "B23B2E", "FBE9E7"),
}
SCORE_WORDS = {1: "Poor", 2: "Weak", 3: "Fair", 4: "Good", 5: "Excellent"}
SCORE_FILLS = {1: "F6D5D1", 2: "FBE3CC", 3: "FFF1C2", 4: "DDEFD9", 5: "C8E6C9"}
STATUS_LABELS = {
    "supported": "Supported", "partly_supported": "Partly supported",
    "not_supported": "Not supported", "adequate": "Adequate", "needs_work": "Needs work",
    "missing": "Missing", "not_applicable": "Not applicable", "present": "Present",
    "partial": "Partial",
}
STATUS_FILLS = {
    "supported": "DDEFD9", "adequate": "DDEFD9", "present": "DDEFD9",
    "partly_supported": "FFF1C2", "needs_work": "FFF1C2", "partial": "FFF1C2",
    "not_supported": "F6D5D1", "missing": "F6D5D1",
}
CRITERIA = {
    "originality": "Originality", "significance": "Significance",
    "methodological_rigor": "Methodological rigour", "evidence_and_analysis": "Evidence and analysis",
    "clarity_and_structure": "Clarity and structure", "literature_coverage": "Literature coverage",
    "reproducibility": "Reproducibility", "ethics_and_reporting": "Ethics and reporting",
}
PAPER_TYPES = {
    "auto": "", "empirical": "Empirical study", "review": "Review", "theoretical": "Theoretical",
    "methods": "Methods paper", "case_study": "Case study", "other": "",
}


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _items(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict]:
    return [item for item in _items(value) if isinstance(item, dict)]


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


_TRAILING_WORDS = {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}


def _file_base(title: Any) -> str:
    """A short, readable file name from the manuscript title, cut at a word boundary."""
    words = re.findall(r"[A-Za-z0-9]+", _text(title))
    base = ""
    for word in words:
        if len(base) + len(word) + 1 > 48:
            break
        base = f"{base}-{word}" if base else word
    trimmed = base.split("-")
    while len(trimmed) > 2 and trimmed[-1].lower() in _TRAILING_WORDS:
        trimmed.pop()
    return "-".join(trimmed) or "manuscript"


def _today() -> str:
    return datetime.now(UTC).strftime("%d %B %Y").lstrip("0")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def create_research_artifacts(result: dict, *, output_dir: Path, author: str = "") -> list[Path]:
    """Write JSON, the review report (DOCX, PDF) and, if present, the revised manuscript."""
    output_dir.mkdir(parents=True, exist_ok=True)
    base = _file_base(result.get("title"))
    json_path = output_dir / f"{base}-review.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    manuscript = Manuscript.from_payload(result.get("manuscript") or {})
    blocks = review_blocks(result, manuscript)
    title = _text(result.get("title"), "Untitled manuscript")
    revision = result.get("revision") if isinstance(result.get("revision"), dict) else None
    author = author or _text((result.get("request") or {}).get("author_name"))

    builders: list[tuple[Any, Path, tuple]] = [
        (write_review_docx, output_dir / f"{base}-review.docx", (blocks, title, author)),
        (write_review_pdf, output_dir / f"{base}-review.pdf", (blocks, title, author)),
    ]
    if revision and revision.get("sections"):
        builders += [
            (write_revised_docx, output_dir / f"{base}-revised.docx", (revision, author)),
            (write_revised_pdf, output_dir / f"{base}-revised.pdf", (revision, author)),
            (write_tracked_docx, output_dir / f"{base}-revised-tracked-changes.docx",
             (revision, author)),
        ]
    with ThreadPoolExecutor(max_workers=len(builders), thread_name_prefix="research-doc") as pool:
        futures = [pool.submit(builder, path, *arguments) for builder, path, arguments in builders]
        written = [future.result() for future in futures]
    return [json_path, *written]


# ---------------------------------------------------------------------------
# Review content, shared by the DOCX and PDF writers
# ---------------------------------------------------------------------------


@dataclass
class Block:
    kind: str  # cover | h1 | h2 | p | label | bullets | numbered | table | callout | note
    text: str = ""
    label: str = ""
    items: tuple = ()
    headers: tuple = ()
    rows: tuple = ()
    widths: tuple = ()
    fills: tuple = ()  # per row: {column index: fill colour}
    tone: tuple = ()  # callout: (title colour, background colour)
    meta: tuple = ()


def review_blocks(result: dict, manuscript: Manuscript) -> list[Block]:
    def where(value: Any) -> str:
        return location_label(manuscript, _text(value))

    sources = {s.get("id"): _text(s.get("title"), s.get("id")) for s in _dicts(result.get("sources"))}
    request = result.get("request") if isinstance(result.get("request"), dict) else {}
    profile = result.get("manuscript_profile") or {}
    recommendation = result.get("recommendation") or {}
    blocks: list[Block] = []

    meta = [
        ("Manuscript", _text(result.get("title"), "Untitled manuscript")),
        ("Discipline", _text(profile.get("discipline")) or _text(request.get("discipline"))),
        ("Article type", _sentence_case(_text(profile.get("paper_type"))
                                        or PAPER_TYPES.get(request.get("paper_type", ""), ""))),
        ("Target venue", _text(request.get("target_venue"))),
        ("Prepared for", _text(request.get("author_name"))),
        ("Date", _today()),
    ]
    decision, colour, background = DECISIONS.get(
        recommendation.get("decision"), ("Revision", ACCENT, SOFT)
    )
    blocks.append(Block("cover", meta=tuple((k, v) for k, v in meta if v)))
    confidence = _text(recommendation.get("confidence"))
    blocks.append(Block(
        "callout",
        label=f"Recommendation: {decision}" + (f" (reviewer confidence: {confidence})" if confidence else ""),
        text=_text(recommendation.get("rationale")),
        tone=(colour, background),
    ))

    # 1. Summary
    blocks.append(Block("h1", "1. Summary of the manuscript"))
    blocks.append(Block("p", _text(result.get("summary"))))
    for label, key in (("Research question", "research_question"), ("Design and methods", "methods_summary"),
                       ("Data", "data_summary")):
        if _text(profile.get(key)):
            blocks.append(Block("label", label=label, text=_text(profile.get(key))))
    if _items(profile.get("claimed_contributions")):
        blocks.append(Block("h2", "Stated contributions"))
        blocks.append(Block("bullets", items=tuple(_text(i) for i in profile["claimed_contributions"])))
    if _items(profile.get("key_findings")):
        blocks.append(Block("h2", "Main findings"))
        blocks.append(Block("bullets", items=tuple(_text(i) for i in profile["key_findings"])))

    # 2. Assessment
    blocks.append(Block("h1", "2. Overall assessment"))
    rows, fills = [], []
    for item in _dicts(result.get("scorecard")):
        score = max(1, min(5, int(item.get("score") or 3)))
        rows.append((CRITERIA.get(item.get("criterion"), _text(item.get("criterion")).replace("_", " ").capitalize()),
                     f"{score}/5  {SCORE_WORDS[score]}", _text(item.get("justification"))))
        fills.append({1: SCORE_FILLS[score]})
    if rows:
        scores = [int(r[1][0]) for r in rows]
        blocks.append(Block("p", f"Average score across {len(rows)} criteria: "
                                 f"{sum(scores) / len(scores):.1f} out of 5."))
        blocks.append(Block("table", headers=("Criterion", "Score", "Comment"), rows=tuple(rows),
                            widths=(1.55, 0.95, 4.0), fills=tuple(fills)))

    # 3. Strengths
    strengths = _dicts(result.get("strengths"))
    if strengths:
        blocks.append(Block("h1", "3. Strengths"))
        blocks.append(Block("numbered", items=tuple(
            (_text(s.get("point")), (("Evidence", _text(s.get("evidence"))), ("Where", where(s.get("location")))))
            for s in strengths
        )))

    # 4. Major concerns
    concerns = _dicts(result.get("major_concerns"))
    if concerns:
        blocks.append(Block("h1", "4. Major concerns"))
        blocks.append(Block("p", "These points affect the validity of the conclusions or the strength "
                                 "of the contribution, and should be addressed before resubmission."))
        blocks.append(Block("numbered", items=tuple(
            (f"{_text(c.get('issue'))} ({_text(c.get('priority'), 'medium')} priority)",
             (("Where", where(c.get("location"))), ("Why it matters", _text(c.get("why_it_matters"))),
              ("Suggested revision", _text(c.get("suggested_fix")))))
            for c in concerns
        )))

    # 5. Minor issues
    minor = _dicts(result.get("minor_issues"))
    if minor:
        blocks.append(Block("h1", "5. Minor issues"))
        blocks.append(Block("table", headers=("Where", "Issue", "Suggestion"), widths=(1.5, 2.5, 2.5),
                            rows=tuple((where(m.get("location")), _text(m.get("issue")), _text(m.get("suggestion")))
                                       for m in minor)))

    # 6. Claims
    claims = _dicts(result.get("claims_check"))
    if claims:
        blocks.append(Block("h1", "6. Claims and supporting evidence"))
        blocks.append(Block("table", headers=("Claim", "Where", "Support", "Comment"), widths=(2.1, 1.25, 1.0, 2.15),
                            rows=tuple((_text(c.get("claim")), where(c.get("location")),
                                        STATUS_LABELS.get(c.get("support"), _text(c.get("support"))),
                                        _text(c.get("comment"))) for c in claims),
                            fills=tuple({2: STATUS_FILLS.get(c.get("support"), "")} for c in claims)))

    # 7. Methods
    methods = _dicts(result.get("methodology_review"))
    stats = _dicts(result.get("statistics_and_reproducibility"))
    if methods or stats:
        blocks.append(Block("h1", "7. Methodology, statistics and reproducibility"))
        for item in methods:
            blocks.append(Block("h2", _text(item.get("aspect"))))
            blocks.append(Block("p", _text(item.get("assessment"))))
            if _text(item.get("recommendation")):
                blocks.append(Block("label", label="Recommendation", text=_text(item.get("recommendation"))))
        if stats:
            blocks.append(Block("h2", "Statistics and reproducibility checklist"))
            blocks.append(Block("table", headers=("Item", "Status", "Comment"), widths=(2.0, 1.1, 3.4),
                                rows=tuple((_text(s.get("item")), STATUS_LABELS.get(s.get("status"), _text(s.get("status"))),
                                            _text(s.get("comment"))) for s in stats),
                                fills=tuple({1: STATUS_FILLS.get(s.get("status"), "")} for s in stats)))

    # 8. Reporting
    checklist = result.get("reporting_checklist") or {}
    checklist_items = _dicts(checklist.get("items"))
    if checklist_items:
        blocks.append(Block("h1", "8. Reporting standards"))
        guideline = _text(checklist.get("guideline"))
        if guideline:
            blocks.append(Block("p", f"Checked against: {guideline}."))
        blocks.append(Block("table", headers=("Item", "Status", "Comment"), widths=(2.0, 1.1, 3.4),
                            rows=tuple((_text(i.get("item")), STATUS_LABELS.get(i.get("status"), _text(i.get("status"))),
                                        _text(i.get("comment"))) for i in checklist_items),
                            fills=tuple({1: STATUS_FILLS.get(i.get("status"), "")} for i in checklist_items)))

    # 9. Gaps and literature
    gaps = _dicts(result.get("research_gaps"))
    positioning = _dicts(result.get("literature_positioning"))
    if gaps or positioning:
        blocks.append(Block("h1", "9. Research gaps and position in the literature"))
        if gaps:
            blocks.append(Block("numbered", items=tuple(
                (_text(g.get("gap")),
                 (("Evidence", _text(g.get("evidence"))), ("Interpretation", _text(g.get("inference"))),
                  ("Confidence", _text(g.get("confidence")).capitalize()),
                  ("Related work", "; ".join(sources.get(ref, ref) for ref in _items(g.get("source_refs"))))))
                for g in gaps
            )))
        if positioning:
            blocks.append(Block("h2", "Engagement with the literature"))
            blocks.append(Block("table", headers=("Theme", "Assessment", "What is missing"), widths=(1.5, 2.6, 2.4),
                                rows=tuple((_text(p.get("theme")), _text(p.get("assessment")),
                                            _text(p.get("missing_work"))) for p in positioning)))

    # 10. Writing
    blocks.append(Block("h1", "10. Writing, presentation and references"))
    writing = _dicts(result.get("writing_and_structure"))
    if writing:
        blocks.append(Block("table", headers=("Aspect", "Comment", "Suggestion"), widths=(1.4, 2.6, 2.5),
                            rows=tuple((_text(w.get("aspect")), _text(w.get("comment")), _text(w.get("suggestion")))
                                       for w in writing)))
    if _text(result.get("abstract_feedback")):
        blocks.append(Block("label", label="Abstract", text=_text(result.get("abstract_feedback"))))
    titles = [_text(t) for t in _items(result.get("title_suggestions")) if _text(t)]
    if titles:
        blocks.append(Block("h2", "Possible titles"))
        blocks.append(Block("bullets", items=tuple(titles)))
    references = _dicts(result.get("references_review"))
    if references:
        blocks.append(Block("h2", "References"))
        blocks.append(Block("bullets", items=tuple(
            f"{_text(r.get('issue'))}: {_text(r.get('detail'))}".strip(": ") for r in references)))

    # 11. Venue
    venue = result.get("venue_fit") or {}
    if _text(venue.get("assessment")):
        target = _text(venue.get("target")) or _text(request.get("target_venue"))
        blocks.append(Block("h1", "11. Fit with the target venue" if target else "11. Suitable venues"))
        blocks.append(Block("p", (f"{target}: " if target else "") + _text(venue.get("assessment"))))
        if _items(venue.get("suggestions")):
            blocks.append(Block("bullets", items=tuple(_text(s) for s in venue["suggestions"])))

    # 12. Future work
    questions = _dicts(result.get("research_questions"))
    scope = _dicts(result.get("future_scope"))
    if questions or scope:
        blocks.append(Block("h1", "12. Directions for further research"))
        if questions:
            blocks.append(Block("numbered", items=tuple(
                (_text(q.get("question")),
                 (("Rationale", _text(q.get("rationale"))),
                  ("Key variables", ", ".join(_text(v) for v in _items(q.get("key_variables_or_constructs"))))))
                for q in questions
            )))
        if scope:
            blocks.append(Block("bullets", items=tuple(f"{_text(s.get('area'))}: {_text(s.get('scope'))}" for s in scope)))

    # 13. Plan
    plan = sorted(_dicts(result.get("revision_plan")), key=lambda p: int(p.get("priority") or 99))
    if plan:
        blocks.append(Block("h1", "13. Suggested revision plan"))
        blocks.append(Block("p", "Ordered by expected impact on the paper. Effort is a rough guide."))
        blocks.append(Block("table", headers=("#", "Action", "Addresses", "Effort"), widths=(0.35, 3.4, 1.9, 0.85),
                            rows=tuple((str(i), _text(p.get("action")), _text(p.get("addresses")),
                                        _text(p.get("effort")).capitalize()) for i, p in enumerate(plan, start=1))))

    questions_for_authors = [_text(q) for q in _items(result.get("author_questions")) if _text(q)]
    if questions_for_authors:
        blocks.append(Block("h1", "14. Questions for the authors"))
        blocks.append(Block("bullets", items=tuple(questions_for_authors), label="numbers"))

    revision = result.get("revision") if isinstance(result.get("revision"), dict) else None
    if revision and revision.get("sections"):
        blocks.extend(_revision_summary_blocks(revision))

    apa = [_text(a) for a in _items(result.get("apa_references")) if _text(a)]
    if apa:
        blocks.append(Block("h1", "Works consulted"))
        blocks.append(Block("bullets", items=tuple(apa), label="plain"))

    notes = result.get("quality_notes") or {}
    scope_notes = [_text(n) for n in _items(notes.get("limitations")) if _text(n)]
    if manuscript.truncated:
        scope_notes.insert(0, "The manuscript was longer than the review limit; the final part was not reviewed.")
    unverified = [_text(n) for n in _items(notes.get("unverified_claims")) if _text(n)]
    if scope_notes or unverified:
        blocks.append(Block("h2", "Scope of this review"))
        blocks.append(Block("bullets", items=tuple(scope_notes + [f"Not verified: {u}" for u in unverified])))
    return blocks


ACTION_LABELS = {"keep": "Unchanged", "revise": "Revised", "expand": "Expanded", "add": "New section"}
STATUS_NOTES = {
    "kept_original": "left as written",
    "partly_revised": "partly revised",
    "unchanged": "",
    "revised": "",
    "added": "",
}

DISCLOSURE = (
    "During the preparation of this work the author(s) used an AI-assisted writing and review "
    "tool to improve the language, structure and presentation of the manuscript. The author(s) "
    "reviewed and edited the content as needed and take full responsibility for the content of "
    "the publication."
)


def _revision_summary_blocks(revision: dict) -> list[Block]:
    sections = [s for s in _dicts(revision.get("sections")) if s.get("kind") in {"abstract", "body", "references"}]
    blocks = [
        Block("h1", "15. The revised manuscript"),
        Block("p", "The revised manuscript accompanies this report as a clean copy (Word and PDF) and as a Word "
                   "copy with tracked changes. The study's data, results and existing citations are unchanged. "
                   "The revision strengthens the argument, structure and presentation, adds material the review "
                   "found missing, and cites only references confirmed in Crossref or arXiv."),
    ]
    rows = []
    for section in sections:
        heading = _text(section.get("heading")) or ("Abstract" if section.get("kind") == "abstract" else "Untitled")
        action = "Revised" if section.get("kind") in {"abstract", "references"} and section.get("status") == "revised" \
            else ACTION_LABELS.get(section.get("action"), "Revised")
        if section.get("kind") == "references" and section.get("status") != "revised":
            action = "Unchanged"
        note = STATUS_NOTES.get(section.get("status"), "")
        what = "; ".join(_text(c.get("change")) for c in _dicts(section.get("changes"))[:3])
        rows.append((heading, action + (f" ({note})" if note else ""), what or "-"))
    if rows:
        blocks.append(Block("table", headers=("Section", "Change", "What was done"), widths=(1.9, 1.3, 3.3),
                            rows=tuple(rows)))
    dropped = [_text(d) for d in _items(revision.get("dropped_sections")) if _text(d)]
    if dropped:
        blocks.append(Block("p", "Planned but not included, because they could not be written from the "
                                 "manuscript and verified sources alone: " + ", ".join(dropped) + "."))

    references = _dicts(revision.get("new_references"))
    if references:
        blocks.append(Block("h2", "References added"))
        blocks.append(Block("p", "Each reference below was confirmed in Crossref or arXiv, and its details come "
                                 "from that registry. Please read each one and check that the statements "
                                 "attributed to it match the source."))
        blocks.append(Block("table", headers=("Reference", "Used to support"), widths=(4.2, 2.3), rows=tuple(
            (_text(r.get("text")), "; ".join(_text(k) for k in _items(r.get("key_points"))[:2]) or _text(r.get("relevance")))
            for r in references)))

    notes = [(_text(s.get("heading")) or "Abstract", _text(n))
             for s in sections for n in _items(s.get("author_notes")) if _text(n)]
    if notes:
        blocks.append(Block("h2", "Information only the authors can supply"))
        blocks.append(Block("p", "The revised paper marks these places with highlighted [Author: ...] notes. "
                                 "Replace each note with the requested detail before submission."))
        blocks.append(Block("table", headers=("Section", "What to add"), widths=(1.8, 4.7), rows=tuple(notes)))

    blocks.append(Block("h2", "Before you submit"))
    blocks.append(Block("bullets", label="numbers", items=(
        "Read the tracked-changes copy and accept or reject each edit; the paper must say what you mean.",
        "Replace every highlighted [Author: ...] note.",
        "Check each added reference and the statement it supports.",
        ("Restore figures, equations and special formatting from your original file, and apply "
         "the journal's template."),
        ("Check the journal's policy on AI-assisted writing. Most publishers ask authors to "
         "declare it in the manuscript; a statement you can adapt is given below."),
    )))
    blocks.append(Block("label", label="Suggested declaration", text=DISCLOSURE))
    return blocks


# ---------------------------------------------------------------------------
# DOCX helpers
# ---------------------------------------------------------------------------


def _font(run, name: str, size: float, *, bold: bool = False, italic: bool = False,
          color: str = INK) -> None:
    run.font.name = name
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attribute), name)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor.from_string(color)


def _shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)


def _neutral_properties(doc, title: str, author: str) -> None:
    zoom = doc.settings.element.find(qn("w:zoom"))
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")  # the default template omits a required attribute
    props = doc.core_properties
    props.title = title
    props.author = author
    props.last_modified_by = author
    props.comments = ""
    props.keywords = ""
    props.category = ""
    props.subject = ""
    props.revision = 1
    now = datetime.now(UTC).replace(tzinfo=None)
    props.created = now
    props.modified = now


def _base_styles(doc, body_font: str, size: float, *, heading_font: str | None = None) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = body_font
    normal.font.size = Pt(size)
    rpr = normal.element.get_or_add_rPr()
    fonts = rpr.get_or_add_rFonts()
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attribute), body_font)
    for name in ("Heading 1", "Heading 2", "Heading 3", "Title"):
        style = doc.styles[name]
        style.font.name = heading_font or body_font
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:ascii"), heading_font or body_font)
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:hAnsi"), heading_font or body_font)
        style.font.color.rgb = RGBColor.from_string(INK)


# Schema order of property children. Helpers append properties in whatever order they run; a
# final pass puts them in the order Word's schema requires.
_ORDER = {
    "pPr": ("pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl",
            "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku",
            "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi",
            "adjustRightInd", "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents",
            "suppressOverlap", "jc", "textDirection", "textAlignment", "textboxTightWrap",
            "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr", "pPrChange"),
    "tcPr": ("cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap",
             "tcMar", "textDirection", "tcFitText", "vAlign", "hideMark", "headers", "cellIns",
             "cellDel", "cellMerge", "tcPrChange"),
    "tblPr": ("tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
              "tblStyleColBandSize", "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd",
              "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription", "tblPrChange"),
}


def _normalize_order(doc) -> None:
    for tag, order in _ORDER.items():
        rank = {name: index for index, name in enumerate(order)}
        for element in doc.element.body.iter(qn(f"w:{tag}")):
            children = list(element)
            ordered = sorted(children, key=lambda child: rank.get(child.tag.split("}")[-1], len(rank)))
            if ordered != children:
                for child in children:
                    element.remove(child)
                element.extend(ordered)


def _save(doc, path: Path) -> Path:
    _normalize_order(doc)
    doc.save(path)
    return path


def _add_runs_with_placeholders(paragraph, text: str, font: str, size: float) -> None:
    for part in PLACEHOLDER.split(text):
        if not part:
            continue
        run = paragraph.add_run(part)
        _font(run, font, size)
        if PLACEHOLDER.fullmatch(part):
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW


# ---------------------------------------------------------------------------
# Review report: DOCX
# ---------------------------------------------------------------------------

R_BODY, R_HEAD = "Calibri", "Cambria"


def write_review_docx(path: Path, blocks: list[Block], title: str, author: str) -> Path:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    for side in ("left_margin", "right_margin"):
        setattr(section, side, Mm(22))
    section.top_margin, section.bottom_margin = Mm(20), Mm(18)
    _base_styles(doc, R_BODY, 10.5, heading_font=R_HEAD)
    doc.styles["Normal"].paragraph_format.space_after = Pt(6)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.12
    _neutral_properties(doc, f"Review: {title}", author)

    header = section.header.paragraphs[0]
    run = header.add_run(f"Manuscript review | {_short(title, 70)}")
    _font(run, R_BODY, 8, color=MUTED)
    footer = section.footer.paragraphs[0]
    _add_page_number(footer)
    for run in footer.runs:
        _font(run, R_BODY, 8, color=MUTED)

    for block in blocks:
        _docx_block(doc, block)
    return _save(doc, path)


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "..."


def _docx_heading(doc, text: str, level: int) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(16 if level == 1 else 10)
    paragraph.paragraph_format.space_after = Pt(6 if level == 1 else 4)
    paragraph.paragraph_format.keep_with_next = True
    run = paragraph.add_run(text)
    _font(run, R_HEAD, 14 if level == 1 else 11.5, bold=True, color=ACCENT if level == 1 else INK)
    if level == 1:
        p_pr = paragraph._p.get_or_add_pPr()
        border = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for key, value in (("w:val", "single"), ("w:sz", "4"), ("w:space", "2"), ("w:color", RULE)):
            bottom.set(qn(key), value)
        border.append(bottom)
        p_pr.append(border)


def _docx_table(doc, headers: Iterable[str], rows: Iterable[Iterable[str]], widths: Iterable[float],
                fills: Iterable[dict] = ()) -> None:
    headers, rows, widths, fills = list(headers), [list(r) for r in rows], list(widths), list(fills)
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    _set_table_borders(table)
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.width = Inches(widths[index])
        _shade_cell(cell, SOFT)
        _set_cell_margins(cell, top=70, bottom=70)
        paragraph = cell.paragraphs[0]
        paragraph.paragraph_format.space_after = Pt(0)
        _font(paragraph.add_run(header), R_BODY, 9, bold=True)
    for row_index, values in enumerate(rows):
        cells = table.add_row().cells
        row_fills = fills[row_index] if row_index < len(fills) else {}
        for index, value in enumerate(values):
            cell = cells[index]
            cell.width = Inches(widths[index])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            _set_cell_margins(cell, top=60, bottom=60)
            if row_fills.get(index):
                _shade_cell(cell, row_fills[index])
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.05
            _font(paragraph.add_run(str(value)), R_BODY, 9)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _docx_block(doc, block: Block) -> None:
    if block.kind == "cover":
        kicker = doc.add_paragraph()
        _font(kicker.add_run("MANUSCRIPT REVIEW"), R_BODY, 9, bold=True, color=ACCENT)
        kicker.paragraph_format.space_after = Pt(4)
        meta = dict(block.meta)
        title = doc.add_paragraph()
        title.paragraph_format.space_after = Pt(10)
        _font(title.add_run(meta.pop("Manuscript", "")), R_HEAD, 18, bold=True)
        rows = [(key, value) for key, value in block.meta if key != "Manuscript"]
        table = doc.add_table(rows=0, cols=2)
        table.autofit = False
        table.columns[0].width, table.columns[1].width = Inches(1.3), Inches(5.2)
        for key, value in rows:
            cells = table.add_row().cells
            cells[0].width, cells[1].width = Inches(1.3), Inches(5.2)
            for cell in cells:
                _set_cell_margins(cell, top=20, bottom=20, start=0)
                cell.paragraphs[0].paragraph_format.space_after = Pt(0)
            _font(cells[0].paragraphs[0].add_run(key), R_BODY, 9.5, bold=True, color=MUTED)
            _font(cells[1].paragraphs[0].add_run(value), R_BODY, 9.5)
        doc.add_paragraph().paragraph_format.space_after = Pt(2)
    elif block.kind == "callout":
        colour, background = block.tone
        table = doc.add_table(rows=1, cols=1)
        table.autofit = False
        cell = table.rows[0].cells[0]
        cell.width = Inches(6.5)
        _shade_cell(cell, background)
        _set_cell_margins(cell, top=120, bottom=120, start=180, end=180)
        tc_pr = cell._tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        left = OxmlElement("w:left")
        for key, value in (("w:val", "single"), ("w:sz", "24"), ("w:color", colour)):
            left.set(qn(key), value)
        borders.append(left)
        tc_pr.append(borders)
        first = cell.paragraphs[0]
        first.paragraph_format.space_after = Pt(3)
        _font(first.add_run(block.label), R_HEAD, 12, bold=True, color=colour)
        if block.text:
            body = cell.add_paragraph()
            body.paragraph_format.space_after = Pt(0)
            _font(body.add_run(block.text), R_BODY, 10)
        doc.add_paragraph().paragraph_format.space_after = Pt(2)
    elif block.kind in {"h1", "h2"}:
        _docx_heading(doc, block.text, 1 if block.kind == "h1" else 2)
    elif block.kind == "p":
        if block.text:
            _font(doc.add_paragraph().add_run(block.text), R_BODY, 10.5)
    elif block.kind == "label":
        paragraph = doc.add_paragraph()
        _font(paragraph.add_run(f"{block.label}. "), R_BODY, 10.5, bold=True)
        _font(paragraph.add_run(block.text), R_BODY, 10.5)
    elif block.kind == "bullets":
        style = {"numbers": "List Number", "plain": None}.get(block.label, "List Bullet")
        for item in block.items:
            paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(3)
            if style is None:
                paragraph.paragraph_format.left_indent = Inches(0.4)
                paragraph.paragraph_format.first_line_indent = Inches(-0.4)
            _font(paragraph.add_run(item), R_BODY, 10.5)
    elif block.kind == "numbered":
        for number, (heading, details) in enumerate(block.items, start=1):
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.keep_with_next = True
            _font(paragraph.add_run(f"{number}. {heading}"), R_BODY, 10.5, bold=True)
            for label, value in details:
                if not value:
                    continue
                line = doc.add_paragraph()
                line.paragraph_format.left_indent = Inches(0.25)
                line.paragraph_format.space_after = Pt(2)
                _font(line.add_run(f"{label}: "), R_BODY, 10, italic=True, color=MUTED)
                _font(line.add_run(value), R_BODY, 10)
    elif block.kind == "table":
        _docx_table(doc, block.headers, block.rows, block.widths, block.fills)


# ---------------------------------------------------------------------------
# PDF fonts and review report
# ---------------------------------------------------------------------------

_FONT_DIRS = (Path("/usr/share/fonts/truetype"), Path("/usr/share/fonts"), Path("C:/Windows/Fonts"),
              Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"))
_FAMILIES = {
    "RSans": (("Carlito-Regular.ttf", "Carlito-Bold.ttf", "Carlito-Italic.ttf"),
              ("calibri.ttf", "calibrib.ttf", "calibrii.ttf"),
              ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf"),
              ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf")),
    "RSerif": (("Caladea-Regular.ttf", "Caladea-Bold.ttf", "Caladea-Italic.ttf"),
               ("cambria.ttc", "cambriab.ttf", "cambriai.ttf"),
               ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf"),
               ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf")),
    "RTimes": (("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf"),
               ("times.ttf", "timesbd.ttf", "timesi.ttf"),
               ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf")),
}
_FALLBACK = {"RSans": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique"),
             "RSerif": ("Times-Roman", "Times-Bold", "Times-Italic"),
             "RTimes": ("Times-Roman", "Times-Bold", "Times-Italic")}
_REGISTERED: dict[str, tuple[str, str, str]] = {}


def _find_font(name: str) -> Path | None:
    for directory in _FONT_DIRS:
        if not directory.exists():
            continue
        direct = directory / name
        if direct.exists():
            return direct
        found = next(directory.rglob(name), None)
        if found:
            return found
    return None


def pdf_family(family: str) -> tuple[str, str, str]:
    """Register (once) and return the regular, bold and italic font names for a family."""
    if family in _REGISTERED:
        return _REGISTERED[family]
    for regular, bold, italic in _FAMILIES[family]:
        paths = [_find_font(regular), _find_font(bold)]
        if not all(paths):
            continue
        italic_path = _find_font(italic) or paths[0]
        try:
            names = (family, f"{family}-Bold", f"{family}-Italic")
            for name, font_path in zip(names, [*paths, italic_path], strict=True):
                pdfmetrics.registerFont(TTFont(name, str(font_path)))
            pdfmetrics.registerFontFamily(family, normal=names[0], bold=names[1], italic=names[2],
                                          boldItalic=names[1])
        except (TTFError, OSError):
            continue
        _REGISTERED[family] = names
        return names
    _REGISTERED[family] = _FALLBACK[family]
    return _FALLBACK[family]


def _markup(text: str) -> str:
    return escape(text).replace("\n", "<br/>")


def _review_styles() -> dict[str, ParagraphStyle]:
    sans, sans_bold, sans_italic = pdf_family("RSans")
    _, serif_bold, _ = pdf_family("RSerif")
    ink, muted, accent = (colors.HexColor(f"#{c}") for c in (INK, MUTED, ACCENT))
    return {
        "kicker": ParagraphStyle("kicker", fontName=sans_bold, fontSize=8.5, leading=11, textColor=accent),
        "title": ParagraphStyle("title", fontName=serif_bold, fontSize=17, leading=21, textColor=ink, spaceAfter=8),
        "meta_key": ParagraphStyle("meta_key", fontName=sans_bold, fontSize=8.8, leading=11.5, textColor=muted),
        "meta": ParagraphStyle("meta", fontName=sans, fontSize=8.8, leading=11.5, textColor=ink),
        "h1": ParagraphStyle("h1", fontName=serif_bold, fontSize=13, leading=16, textColor=accent,
                             spaceBefore=12, spaceAfter=5, keepWithNext=1),
        "h2": ParagraphStyle("h2", fontName=serif_bold, fontSize=10.8, leading=13.5, textColor=ink,
                             spaceBefore=8, spaceAfter=3, keepWithNext=1),
        "body": ParagraphStyle("body", fontName=sans, fontSize=9.8, leading=13.4, textColor=ink, spaceAfter=5),
        "item": ParagraphStyle("item", fontName=sans_bold, fontSize=9.8, leading=13, textColor=ink,
                               spaceBefore=5, spaceAfter=2),
        "detail": ParagraphStyle("detail", fontName=sans, fontSize=9.3, leading=12.4, textColor=ink,
                                 leftIndent=12, spaceAfter=2),
        "bullet": ParagraphStyle("bullet", fontName=sans, fontSize=9.8, leading=13, textColor=ink,
                                 leftIndent=14, bulletIndent=3, spaceAfter=2.5),
        "hanging": ParagraphStyle("hanging", fontName=sans, fontSize=9.3, leading=12.4, textColor=ink,
                                  leftIndent=18, firstLineIndent=-18, spaceAfter=3),
        "cell": ParagraphStyle("cell", fontName=sans, fontSize=8.6, leading=11, textColor=ink),
        "cell_head": ParagraphStyle("cell_head", fontName=sans_bold, fontSize=8.6, leading=11, textColor=ink),
        "callout_title": ParagraphStyle("callout_title", fontName=serif_bold, fontSize=11.5, leading=14),
        "header": ParagraphStyle("header", fontName=sans, fontSize=7.5, textColor=muted),
        "_italic": ParagraphStyle("_italic", fontName=sans_italic),
    }


def _pdf_table(headers, rows, widths, fills, styles, total_width: float) -> Table:
    scale = total_width / sum(widths)
    data = [[Paragraph(_markup(h), styles["cell_head"]) for h in headers]]
    data += [[Paragraph(_markup(str(v)), styles["cell"]) for v in row] for row in rows]
    table = Table(data, colWidths=[w * scale for w in widths], repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{SOFT}")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_index, row_fills in enumerate(fills, start=1):
        for column, fill in (row_fills or {}).items():
            if fill:
                commands.append(("BACKGROUND", (column, row_index), (column, row_index),
                                 colors.HexColor(f"#{fill}")))
    table.setStyle(TableStyle(commands))
    return table


def write_review_pdf(path: Path, blocks: list[Block], title: str, author: str) -> Path:
    styles = _review_styles()
    sans = styles["body"].fontName
    width = A4[0] - 44 * mm
    story: list = []
    italic = styles["_italic"].fontName
    for block in blocks:
        if block.kind == "cover":
            meta = dict(block.meta)
            story.append(Paragraph("MANUSCRIPT REVIEW", styles["kicker"]))
            story.append(Spacer(1, 3))
            story.append(Paragraph(_markup(meta.get("Manuscript", "")), styles["title"]))
            rows = [[Paragraph(_markup(k), styles["meta_key"]), Paragraph(_markup(v), styles["meta"])]
                    for k, v in block.meta if k != "Manuscript"]
            if rows:
                table = Table(rows, colWidths=[32 * mm, width - 32 * mm])
                table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                           ("TOPPADDING", (0, 0), (-1, -1), 1),
                                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
                story.append(table)
            story.append(Spacer(1, 8))
        elif block.kind == "callout":
            colour, background = (colors.HexColor(f"#{c}") for c in block.tone)
            title_style = ParagraphStyle("ct", parent=styles["callout_title"], textColor=colour)
            content = [Paragraph(_markup(block.label), title_style)]
            if block.text:
                content.append(Spacer(1, 3))
                content.append(Paragraph(_markup(block.text), styles["body"]))
            box = Table([[content]], colWidths=[width])
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("LINEBEFORE", (0, 0), (0, -1), 3.5, colour),
                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story += [box, Spacer(1, 6)]
        elif block.kind == "h1":
            story.append(Paragraph(_markup(block.text), styles["h1"]))
        elif block.kind == "h2":
            story.append(Paragraph(_markup(block.text), styles["h2"]))
        elif block.kind == "p" and block.text:
            story.append(Paragraph(_markup(block.text), styles["body"]))
        elif block.kind == "label":
            story.append(Paragraph(f"<b>{_markup(block.label)}.</b> {_markup(block.text)}", styles["body"]))
        elif block.kind == "bullets":
            for number, item in enumerate(block.items, start=1):
                if block.label == "plain":
                    story.append(Paragraph(_markup(item), styles["hanging"]))
                else:
                    bullet = f"{number}." if block.label == "numbers" else "\u2022"
                    story.append(Paragraph(_markup(item), styles["bullet"], bulletText=bullet))
        elif block.kind == "numbered":
            for number, (heading, details) in enumerate(block.items, start=1):
                group = [Paragraph(f"{number}. {_markup(heading)}", styles["item"])]
                group += [
                    Paragraph(f'<font name="{italic}" color="#{MUTED}">{_markup(label)}:</font> {_markup(value)}',
                              styles["detail"])
                    for label, value in details if value
                ]
                story.append(KeepTogether(group))
        elif block.kind == "table":
            story += [_pdf_table(block.headers, block.rows, block.widths, block.fills, styles, width),
                      Spacer(1, 6)]

    header_text = f"Manuscript review | {_short(title, 80)}"

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(sans, 7.5)
        canvas.setFillColor(colors.HexColor(f"#{MUTED}"))
        canvas.drawString(22 * mm, A4[1] - 12 * mm, header_text)
        canvas.drawRightString(A4[0] - 22 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm,
        bottomMargin=18 * mm, title=f"Review: {title}", author=author, subject="", creator=author,
        producer=author, keywords="",
    )
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path


# ---------------------------------------------------------------------------
# Revised manuscript: shared layout
# ---------------------------------------------------------------------------

M_FONT = "Times New Roman"
M_SIZE = 12


def _revised_sections(revision: dict) -> list[dict]:
    return _dicts(revision.get("sections"))


def _manuscript_docx(author: str, title: str) -> Document:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Inches(1))
    _base_styles(doc, M_FONT, M_SIZE)
    normal = doc.styles["Normal"].paragraph_format
    normal.line_spacing = 1.5
    normal.space_after = Pt(0)
    _neutral_properties(doc, title, author)
    footer = section.footer.paragraphs[0]
    _add_page_number(footer)
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in footer.runs:
        _font(run, M_FONT, 10)
    # Only the page number: drop the "Page " label for a plain manuscript footer.
    if footer.runs and footer.runs[0].text.startswith("Page"):
        footer.runs[0].text = ""
    return doc


def _manuscript_title(doc, title: str, front: list[str]) -> None:
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(12)
    _font(paragraph.add_run(title), M_FONT, 14, bold=True)
    for line in front:
        extra = doc.add_paragraph()
        extra.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _font(extra.add_run(line), M_FONT, M_SIZE)
    if front:
        doc.add_paragraph()


def _manuscript_heading(doc, heading: str, kind: str, level: int = 1):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    paragraph.paragraph_format.space_after = Pt(6 if level == 1 else 4)
    paragraph.paragraph_format.keep_with_next = True
    if kind == "abstract":
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if heading:
        _font(paragraph.add_run(heading), M_FONT, M_SIZE, bold=True, italic=level == 2)
    return paragraph


def _body_paragraph(doc, kind: str, first: bool):
    paragraph = doc.add_paragraph()
    if kind == "references":
        paragraph.paragraph_format.left_indent = Inches(0.5)
        paragraph.paragraph_format.first_line_indent = Inches(-0.5)
    elif kind == "abstract":
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    elif not first:
        paragraph.paragraph_format.first_line_indent = Inches(0.5)
    return paragraph


def _keywords_text(revision: dict) -> str:
    keywords = [_text(k) for k in _items(revision.get("keywords")) if _text(k)]
    return "; ".join(keywords)


def _keywords_paragraph(doc):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(6)
    return paragraph


def _tables_after(section: dict) -> dict[int, list[dict]]:
    """Tables keyed by the number of paragraphs that precede them."""
    placed: dict[int, list[dict]] = {}
    count = len(_items(section.get("revised")))
    for table in _dicts(section.get("tables")):
        placed.setdefault(min(max(int(table.get("after") or 0), 0), count), []).append(table)
    return placed


def _three_line_borders(table) -> None:
    """Journal-style table: rules above and below the header and at the bottom only."""
    properties = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge, size in (("top", "12"), ("left", None), ("bottom", "12"), ("right", None),
                       ("insideH", None), ("insideV", None)):  # schema order
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single" if size else "nil")
        if size:
            element.set(qn("w:sz"), size)
            element.set(qn("w:color"), "000000")
        borders.append(element)
    properties.append(borders)
    for cell in table.rows[0].cells:
        tc_borders = OxmlElement("w:tcBorders")
        bottom = OxmlElement("w:bottom")
        for key, value in (("w:val", "single"), ("w:sz", "6"), ("w:color", "000000")):
            bottom.set(qn(key), value)
        tc_borders.append(bottom)
        cell._tc.get_or_add_tcPr().append(tc_borders)


def _docx_manuscript_table(doc, table: dict, revisions: _Revisions | None = None) -> None:
    caption = doc.add_paragraph()
    caption.paragraph_format.space_before = Pt(10)
    caption.paragraph_format.space_after = Pt(4)
    caption.paragraph_format.keep_with_next = True
    label = f"Table {table.get('number', '')}. "
    text = _text(table.get("caption"))
    if revisions:
        revisions.inserted(caption, label + text, bold_prefix=len(label))
        revisions.mark_paragraph(caption, "w:ins")
    else:
        _font(caption.add_run(label), M_FONT, 10.5, bold=True)
        _font(caption.add_run(text), M_FONT, 10.5)
    columns = [_text(c) for c in _items(table.get("columns"))]
    grid = doc.add_table(rows=1, cols=len(columns))
    grid.alignment = WD_TABLE_ALIGNMENT.CENTER
    _three_line_borders(grid)
    rows = [columns] + [[_text(cell) for cell in row] for row in _items(table.get("rows"))]
    for index, values in enumerate(rows):
        cells = grid.rows[0].cells if index == 0 else grid.add_row().cells
        if revisions:
            grid.rows[index]._tr.get_or_add_trPr().append(revisions._mark("w:ins"))
        for column, value in enumerate(values):
            paragraph = cells[column].paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.0
            if revisions:
                revisions.inserted(paragraph, value, size=10, bold_prefix=len(value) if index == 0 else 0)
            else:
                _font(paragraph.add_run(value), M_FONT, 10, bold=index == 0)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(4)
    if revisions:
        revisions.mark_paragraph(spacer, "w:ins")


def write_revised_docx(path: Path, revision: dict, author: str) -> Path:
    title = _text(revision.get("title"), "Untitled manuscript")
    doc = _manuscript_docx(author, title)
    sections = _revised_sections(revision)
    front = next((s for s in sections if s.get("kind") == "front"), None)
    _manuscript_title(doc, title, [_text(p) for p in _items((front or {}).get("revised"))])
    keywords = _keywords_text(revision)
    for section in sections:
        if section is front:
            continue
        kind = section.get("kind", "body")
        if _text(section.get("heading")):
            _manuscript_heading(doc, _text(section["heading"]), kind, int(section.get("level") or 1))
        tables = _tables_after(section)
        for table in tables.get(0, []):
            _docx_manuscript_table(doc, table)
        for index, text in enumerate(_items(section.get("revised")), start=1):
            paragraph = _body_paragraph(doc, kind, index == 1)
            _add_runs_with_placeholders(paragraph, _text(text), M_FONT, M_SIZE)
            for table in tables.get(index, []):
                _docx_manuscript_table(doc, table)
        if kind == "abstract" and keywords:
            paragraph = _keywords_paragraph(doc)
            _font(paragraph.add_run("Keywords: "), M_FONT, M_SIZE, italic=True, bold=True)
            _font(paragraph.add_run(keywords), M_FONT, M_SIZE)
    return _save(doc, path)


def write_revised_pdf(path: Path, revision: dict, author: str) -> Path:
    serif, serif_bold, _ = pdf_family("RTimes")
    margin = 25.4 * mm
    title = _text(revision.get("title"), "Untitled manuscript")
    base = ParagraphStyle("m_body", fontName=serif, fontSize=M_SIZE, leading=M_SIZE * 1.5 * 1.15,
                          alignment=TA_LEFT)
    styles = {
        "title": ParagraphStyle("m_title", parent=base, fontName=serif_bold, fontSize=14, leading=19,
                                alignment=TA_CENTER, spaceAfter=10),
        "front": ParagraphStyle("m_front", parent=base, alignment=TA_CENTER),
        "heading": ParagraphStyle("m_heading", parent=base, fontName=serif_bold, spaceBefore=12,
                                  spaceAfter=4, keepWithNext=1),
        "abstract_heading": ParagraphStyle("m_abs_h", parent=base, fontName=serif_bold, alignment=TA_CENTER,
                                           spaceBefore=12, spaceAfter=4, keepWithNext=1),
        "first": base,
        "body": ParagraphStyle("m_indent", parent=base, firstLineIndent=12.7 * mm),
        "abstract": ParagraphStyle("m_abs", parent=base, alignment=TA_JUSTIFY),
        "references": ParagraphStyle("m_ref", parent=base, leftIndent=12.7 * mm, firstLineIndent=-12.7 * mm),
        "subheading": ParagraphStyle("m_sub", parent=base, fontName=pdf_family("RTimes")[2], spaceBefore=8,
                                     spaceAfter=3, keepWithNext=1),
        "keywords": ParagraphStyle("m_kw", parent=base, spaceBefore=6),
        "caption": ParagraphStyle("m_cap", parent=base, fontSize=10.5, leading=13, spaceAfter=3),
        "cell": ParagraphStyle("m_cell", parent=base, fontSize=10, leading=12.5),
    }

    def markup(text: str) -> str:
        return "".join(
            f'<font backColor="#FFF176">{escape(part)}</font>' if PLACEHOLDER.fullmatch(part) else escape(part)
            for part in PLACEHOLDER.split(text) if part
        )

    sections = _revised_sections(revision)
    front = next((s for s in sections if s.get("kind") == "front"), None)
    story: list = [Paragraph(escape(title), styles["title"])]
    story += [Paragraph(markup(_text(p)), styles["front"]) for p in _items((front or {}).get("revised"))]
    story.append(Spacer(1, 10))
    keywords = _keywords_text(revision)
    width = A4[0] - 2 * margin

    def table_flowables(table: dict) -> list:
        caption = Paragraph(f"<b>Table {table.get('number', '')}.</b> {escape(_text(table.get('caption')))}",
                            styles["caption"])
        columns = [_text(c) for c in _items(table.get("columns"))]
        data = [[Paragraph(f"<b>{escape(c)}</b>", styles["cell"]) for c in columns]]
        data += [[Paragraph(markup(_text(cell)), styles["cell"]) for cell in row]
                 for row in _items(table.get("rows"))]
        grid = Table(data, colWidths=[width / len(columns)] * len(columns), repeatRows=1)
        grid.setStyle(TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
            ("LINEBELOW", (0, -1), (-1, -1), 1.0, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        return [Spacer(1, 6), KeepTogether([caption, grid]), Spacer(1, 8)]

    for section in sections:
        if section is front:
            continue
        kind = section.get("kind", "body")
        level = int(section.get("level") or 1)
        if _text(section.get("heading")):
            style = styles["abstract_heading"] if kind == "abstract" else (
                styles["heading"] if level == 1 else styles["subheading"])
            story.append(Paragraph(escape(_text(section["heading"])), style))
        tables = _tables_after(section)
        for table in tables.get(0, []):
            story += table_flowables(table)
        for index, text in enumerate(_items(section.get("revised")), start=1):
            style = styles.get(kind) if kind in {"abstract", "references"} else (
                styles["first"] if index == 1 else styles["body"])
            story.append(Paragraph(markup(_text(text)), style))
            for table in tables.get(index, []):
                story += table_flowables(table)
        if kind == "abstract" and keywords:
            story.append(Paragraph(f"<b><i>Keywords:</i></b> {escape(keywords)}", styles["keywords"]))

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(serif, 10)
        canvas.drawCentredString(A4[0] / 2, 12 * mm, str(document.page))
        canvas.restoreState()

    SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin,
        bottomMargin=margin, title=title, author=author, subject="", creator=author,
        producer=author, keywords="",
    ).build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path


# ---------------------------------------------------------------------------
# Revised manuscript with tracked changes (DOCX)
# ---------------------------------------------------------------------------


class _Revisions:
    """Builds w:ins / w:del runs with unique IDs, one author and one timestamp."""

    def __init__(self, author: str) -> None:
        self.author = author or "Editor"
        self.date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.next_id = 1

    def _mark(self, tag: str):
        element = OxmlElement(tag)
        element.set(qn("w:id"), str(self.next_id))
        element.set(qn("w:author"), self.author)
        element.set(qn("w:date"), self.date)
        self.next_id += 1
        return element

    @staticmethod
    def _run(text: str, *, deleted: bool, highlight: bool = False, bold: bool = False,
             italic: bool = False, size: float = M_SIZE):
        run = OxmlElement("w:r")
        properties = OxmlElement("w:rPr")
        fonts = OxmlElement("w:rFonts")
        for attribute in ("w:ascii", "w:hAnsi", "w:cs"):
            fonts.set(qn(attribute), M_FONT)
        properties.append(fonts)
        # Schema order inside w:rPr: rFonts, b, i, sz, highlight.
        if bold:
            properties.append(OxmlElement("w:b"))
        if italic:
            properties.append(OxmlElement("w:i"))
        size_element = OxmlElement("w:sz")
        size_element.set(qn("w:val"), str(int(size * 2)))
        properties.append(size_element)
        if highlight:
            mark = OxmlElement("w:highlight")
            mark.set(qn("w:val"), "yellow")
            properties.append(mark)
        run.append(properties)
        node = OxmlElement("w:delText" if deleted else "w:t")
        node.set(qn("xml:space"), "preserve")
        node.text = text
        run.append(node)
        return run

    def _runs(self, text: str, *, bold: bool, italic: bool, size: float, bold_prefix: int = 0):
        pieces = [(text[:bold_prefix], True), (text[bold_prefix:], bold)] if bold_prefix else [(text, bold)]
        for piece, piece_bold in pieces:
            for part in PLACEHOLDER.split(piece):
                if part:
                    yield self._run(part, deleted=False, highlight=bool(PLACEHOLDER.fullmatch(part)),
                                    bold=piece_bold, italic=italic, size=size)

    def plain(self, paragraph, text: str, *, bold: bool = False, italic: bool = False,
              size: float = M_SIZE) -> None:
        for run in self._runs(text, bold=bold, italic=italic, size=size):
            paragraph._p.append(run)

    def inserted(self, paragraph, text: str, *, bold_prefix: int = 0, bold: bool = False,
                 italic: bool = False, size: float = M_SIZE) -> None:
        if not text:
            return
        wrapper = self._mark("w:ins")
        for run in self._runs(text, bold=bold, italic=italic, size=size, bold_prefix=bold_prefix):
            wrapper.append(run)
        paragraph._p.append(wrapper)

    def deleted(self, paragraph, text: str, *, bold: bool = False, italic: bool = False) -> None:
        if not text:
            return
        wrapper = self._mark("w:del")
        wrapper.append(self._run(text, deleted=True, bold=bold, italic=italic))
        paragraph._p.append(wrapper)

    def mark_paragraph(self, paragraph, tag: str) -> None:
        """Mark the paragraph mark itself as inserted or deleted, so accept/reject is clean."""
        p_pr = paragraph._p.get_or_add_pPr()
        r_pr = p_pr.find(qn("w:rPr"))
        if r_pr is None:
            r_pr = OxmlElement("w:rPr")
            p_pr.append(r_pr)
        r_pr.append(self._mark(tag))


_TOKEN = re.compile(r"\s+|[^\s]+")


def _diff_into(paragraph, before: str, after: str, revisions: _Revisions, *, bold: bool = False,
               italic: bool = False) -> None:
    old, new = _TOKEN.findall(before), _TOKEN.findall(after)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for operation, a1, a2, b1, b2 in matcher.get_opcodes():
        if operation == "equal":
            revisions.plain(paragraph, "".join(old[a1:a2]), bold=bold, italic=italic)
        else:
            revisions.deleted(paragraph, "".join(old[a1:a2]), bold=bold, italic=italic)
            revisions.inserted(paragraph, "".join(new[b1:b2]), bold=bold, italic=italic)


def align_paragraphs(original: list[str], revised: list[str]) -> list[tuple[str | None, str | None]]:
    """Pair original and revised paragraphs in order; unmatched ones are pure deletions/insertions."""
    pairs: list[tuple[str | None, str | None]] = []
    matcher = difflib.SequenceMatcher(a=original, b=revised, autojunk=False)
    for operation, a1, a2, b1, b2 in matcher.get_opcodes():
        if operation == "equal":
            pairs += [(text, text) for text in original[a1:a2]]
            continue
        old, new = original[a1:a2], revised[b1:b2]
        # Pair each revised paragraph with the most similar remaining original, keeping order.
        used = -1
        for text in new:
            best, best_score = None, 0.35
            for index in range(used + 1, len(old)):
                score = difflib.SequenceMatcher(a=old[index].split(), b=text.split(), autojunk=False).ratio()
                if score > best_score:
                    best, best_score = index, score
            if best is None:
                pairs.append((None, text))
                continue
            pairs += [(old[index], None) for index in range(used + 1, best)]
            pairs.append((old[best], text))
            used = best
        pairs += [(old[index], None) for index in range(used + 1, len(old))]
    return pairs


def write_tracked_docx(path: Path, revision: dict, author: str) -> Path:
    title = _text(revision.get("title"), "Untitled manuscript")
    doc = _manuscript_docx(author, title)
    revisions = _Revisions(author)
    sections = _revised_sections(revision)
    front = next((s for s in sections if s.get("kind") == "front"), None)
    _manuscript_title(doc, title, [_text(p) for p in _items((front or {}).get("original"))])
    keywords = _keywords_text(revision)
    for section in sections:
        if section is front:
            continue
        kind = section.get("kind", "body")
        level = int(section.get("level") or 1)
        new_heading = _text(section.get("heading"))
        old_heading = _text(section.get("original_heading"))
        added = not _items(section.get("original")) and section.get("action") == "add"
        if new_heading or old_heading:
            paragraph = _manuscript_heading(doc, "", kind, level)
            if added or not old_heading:
                revisions.inserted(paragraph, new_heading, bold_prefix=len(new_heading), italic=level == 2)
                revisions.mark_paragraph(paragraph, "w:ins")
            elif new_heading == old_heading:
                revisions.plain(paragraph, new_heading, bold=True, italic=level == 2)
            else:
                _diff_into(paragraph, old_heading, new_heading, revisions, bold=True, italic=level == 2)
        original = [_text(p) for p in _items(section.get("original"))]
        revised = [_text(p) for p in _items(section.get("revised"))]
        tables = _tables_after(section)
        for table in tables.get(0, []):
            _docx_manuscript_table(doc, table, revisions)
        position = 0  # paragraphs of the revised text written so far, for table placement
        for index, (before, after) in enumerate(align_paragraphs(original, revised)):
            paragraph = _body_paragraph(doc, kind, index == 0)
            if before is not None and after is not None:
                _diff_into(paragraph, before, after, revisions)
            elif after is not None:
                revisions.inserted(paragraph, after)
                revisions.mark_paragraph(paragraph, "w:ins")
            elif before is not None:
                revisions.deleted(paragraph, before)
                revisions.mark_paragraph(paragraph, "w:del")
            if after is not None:
                position += 1
                for table in tables.get(position, []):
                    _docx_manuscript_table(doc, table, revisions)
        if kind == "abstract" and keywords:
            paragraph = _keywords_paragraph(doc)
            revisions.inserted(paragraph, f"Keywords: {keywords}", bold_prefix=len("Keywords: "))
            revisions.mark_paragraph(paragraph, "w:ins")
    # Further edits by the author are tracked too.
    _enable_track_changes(doc)
    return _save(doc, path)


# Settings children that must come after w:trackRevisions in the schema.
_AFTER_TRACK_REVISIONS = (
    "doNotTrackMoves", "doNotTrackFormatting", "documentProtection", "autoFormatOverride",
    "styleLockTheme", "styleLockQFSet", "defaultTabStop",
)


def _enable_track_changes(doc) -> None:
    element = doc.settings.element
    if element.find(qn("w:trackRevisions")) is not None:
        return
    track = OxmlElement("w:trackRevisions")
    for child in element:
        if child.tag.split("}")[-1] in _AFTER_TRACK_REVISIONS or child.tag == qn("w:defaultTabStop"):
            child.addprevious(track)
            return
    element.append(track)


