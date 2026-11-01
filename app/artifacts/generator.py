import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

NAVY = "12243F"
CYAN = "22A6B6"
INK = "253247"
MUTED = "667386"
LIGHT = "F3F6FA"
LINE = "DDE4ED"
WHITE = "FFFFFF"
_OPTION_PREFIX = re.compile(r"^\s*(?:option\s+)?[A-Ha-h]\s*[.):\-]\s*", re.IGNORECASE)
_MATH_CHARACTERS = re.compile(
    r"[\u0370-\u03ff\u2070-\u209f\u2200-\u22ff\u00b5\u0176\u0177\u0232\u0233]"
)
_PDF_FONT_REGULAR = "Helvetica"
_PDF_FONT_BOLD = "Helvetica-Bold"
_SUBSCRIPT_TRANSLATION = str.maketrans("0123456789ijn", "₀₁₂₃₄₅₆₇₈₉ᵢⱼₙ")
_SUPERSCRIPT_TRANSLATION = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")


def _plain_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return _clean_generated_text(value.strip())
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        for preferred_key in (
            "title",
            "text",
            "value",
            "name",
            "objective",
            "question",
            "summary",
            "description",
            "evidence",
        ):
            if preferred_key in value and value[preferred_key] is not None:
                return _plain_text(value[preferred_key], fallback)
        return "; ".join(f"{_humanize(key)}: {_plain_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return "\n".join(_plain_text(item) for item in value if item is not None)
    return str(value)


def _clean_generated_text(value: str) -> str:
    """Remove model-facing Markdown and tracking parameters from exported prose."""
    value = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        lambda match: f"{match.group(1)} ({_clean_url(match.group(2))})",
        value,
    )
    value = re.sub(r"(?<!\()https?://[^\s)]+", lambda match: _clean_url(match.group(0)), value)
    value = value.replace("utm_source=openai&", "").replace("?utm_source=openai", "")
    return _format_math_notation(value)


def _format_math_notation(value: str) -> str:
    """Convert common model notation into portable, readable Unicode mathematics."""
    replacements = {
        r"\hat{y}": "ŷ",
        r"\mu": "μ",
        r"\Sigma": "Σ",
        r"\sum": "Σ",
        r"\approx": "≈",
        r"\times": "×",
        r"\leq": "≤",
        r"\geq": "≥",
    }
    for source, replacement in replacements.items():
        value = value.replace(source, replacement)
    value = value.replace("$", "")
    value = re.sub(
        r"_\{([0-9ijn]+)\}|_([0-9ijn]+)",
        lambda match: (match.group(1) or match.group(2)).translate(_SUBSCRIPT_TRANSLATION),
        value,
    )
    value = re.sub(
        r"\^\{([0-9+\-]+)\}|\^([0-9+\-]+)",
        lambda match: (match.group(1) or match.group(2)).translate(_SUPERSCRIPT_TRANSLATION),
        value,
    )
    return value


def _objective_reference(item: Any) -> str:
    """Teacher-facing objective line with its ID and Bloom level, e.g. `LO1 (Understand): ...`."""
    if not isinstance(item, dict):
        return _plain_text(item)
    objective = _plain_text(item.get("objective") or item.get("text"))
    label = _plain_text(item.get("id"))
    if item.get("bloom_level"):
        label = f"{label} ({_plain_text(item.get('bloom_level'))})".strip()
    return f"{label}: {objective}" if label else objective


def _mcq_answer_key(item: dict) -> str:
    """Teacher answer-key text: correct option, rationale, and what the item assesses."""
    meta = " - ".join(
        value
        for value in (
            f"Objective {_plain_text(item.get('objective_id'))}" if item.get("objective_id") else "",
            _plain_text(item.get("difficulty")).capitalize(),
        )
        if value
    )
    return "\n".join(
        value
        for value in (
            _plain_text(item.get("answer")),
            _plain_text(item.get("explanation")),
            f"Assesses: {meta}" if meta else "",
        )
        if value
    )


def _mcq_option_text(value: Any) -> str:
    """Return an option without a model-supplied A./B)/Option C prefix."""
    return _OPTION_PREFIX.sub("", _plain_text(value), count=1).strip()


def _contains_math(text: Any) -> bool:
    return bool(_MATH_CHARACTERS.search(_plain_text(text)))


def _register_pdf_fonts() -> tuple[str, str]:
    """Register an embedded font that actually contains our mathematical glyphs."""
    global _PDF_FONT_REGULAR, _PDF_FONT_BOLD
    if _PDF_FONT_REGULAR != "Helvetica":
        return _PDF_FONT_REGULAR, _PDF_FONT_BOLD

    candidates = [
        (Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/segoeuib.ttf")),
        (Path("C:/Windows/Fonts/calibri.ttf"), Path("C:/Windows/Fonts/calibrib.ttf")),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (
            Path(pdfmetrics.__file__).resolve().parents[1] / "fonts" / "Vera.ttf",
            Path(pdfmetrics.__file__).resolve().parents[1] / "fonts" / "VeraBd.ttf",
        ),
    ]
    for regular_path, bold_path in candidates:
        if not (regular_path.exists() and bold_path.exists()):
            continue
        try:
            regular = TTFont("DocSans", str(regular_path))
            bold = TTFont("DocSans-Bold", str(bold_path))
            required = "₀₁₂₃₄₅₆₇₈₉ᵢⱼₙ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻Σŷμ√−"
            if any(
                ord(character) not in font.face.charToGlyph
                for font in (regular, bold)
                for character in required
            ):
                continue
            pdfmetrics.registerFont(regular)
            pdfmetrics.registerFont(bold)
            pdfmetrics.registerFontFamily(
                "DocSans",
                normal="DocSans",
                bold="DocSans-Bold",
                italic="DocSans",
                boldItalic="DocSans-Bold",
            )
        except TTFError:
            _PDF_FONT_REGULAR = "Helvetica"
            _PDF_FONT_BOLD = "Helvetica-Bold"
        else:
            _PDF_FONT_REGULAR = "DocSans"
            _PDF_FONT_BOLD = "DocSans-Bold"
            break
    return _PDF_FONT_REGULAR, _PDF_FONT_BOLD


def _clean_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        query = urlencode(
            [
                (key, item)
                for key, item in parse_qsl(parts.query)
                if not key.lower().startswith("utm_")
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    except ValueError:
        return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _safe_title(value: Any) -> str:
    text = _plain_text(value, "output")
    return "".join(c if c.isalnum() else "-" for c in text).strip("-")[:60] or "output"


# Running header on every DOCX/PDF page: these are the teacher's private copy.
REFERENCE_LABEL = "TEACHER'S GUIDE - CONTAINS ANSWER KEYS"
REFERENCE_NOTE = (
    "This is the teacher's copy. It has the answer keys, model answers, timings and teaching "
    "notes, so keep it for your own use. Share the slides and the video with students."
)
SUPPORTED_AGENT_TYPES = ("teaching", "research")


def _document_subject(result: dict) -> str:
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    subject = _plain_text(overview.get("course"))
    return "" if subject.lower() == "general" else subject


def _color(value: str) -> RGBColor:
    return RGBColor.from_string(value)




def create_artifacts(
    result: dict, *, agent_type: str = "teaching", output_dir: Path, author: str = ""
) -> list[Path]:
    """Write the teaching package as JSON, DOCX, PDF and PPTX, or a manuscript review.

    For research results this writes the review report (DOCX, PDF) and the revised manuscript
    (DOCX, PDF, and DOCX with tracked changes); see `research_docs`.

    DOCX and PDF are the teacher's guide; PPTX is the student presentation. `author` fills the
    document properties (for example the teacher's name). It defaults to blank so files never
    carry a tool or product name in their metadata.
    """
    if agent_type not in SUPPORTED_AGENT_TYPES:
        raise ValueError(f"Unsupported agent type: {agent_type}.")
    if agent_type == "research":
        # The manuscript review and the revised paper have their own exporters.
        from app.artifacts.research_docs import create_research_artifacts

        return create_research_artifacts(result, output_dir=output_dir, author=author)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_title(result.get("title", "lesson"))
    json_path = output_dir / f"{base}.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    builders = (
        (_create_docx, output_dir / f"{base}.docx"),
        (_create_pdf, output_dir / f"{base}.pdf"),
        (_create_pptx, output_dir / f"{base}.pptx"),
    )
    # Each exporter writes a different file and has no shared mutable document state. Running
    # them together shortens the post-generation wait without changing any output format.
    with ThreadPoolExecutor(max_workers=len(builders), thread_name_prefix="artifact") as executor:
        futures = [
            executor.submit(builder, result, destination, author=author)
            for builder, destination in builders
        ]
        generated = [future.result() for future in futures]
    return [json_path, *generated]


# ---------------------------------------------------------------------------
# DOCX - standard_business_brief preset with academic-report named overrides
# ---------------------------------------------------------------------------


def _set_docx_font(run, *, size: float, color: str = INK, bold: bool = False) -> None:
    font_name = "Cambria Math" if _contains_math(run.text) else "Arial"
    run.font.name = font_name
    fonts = run._element.get_or_add_rPr().rFonts
    for attribute in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attribute), font_name)
    run.font.size = Pt(size)
    run.font.color.rgb = _color(color)
    run.bold = bold


def _shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def _set_cell_margins(cell, top: int = 90, start: int = 120, bottom: int = 90, end: int = 120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _set_table_borders(table) -> None:
    table_properties = table._tbl.tblPr
    borders = table_properties.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        table_properties.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = borders.find(qn(f"w:{edge}"))
        if element is None:
            element = OxmlElement(f"w:{edge}")
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:color"), "D9D9D9")


def _add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Page ")
    _set_docx_font(run, size=9, color=MUTED)
    fld_char_1 = OxmlElement("w:fldChar")
    fld_char_1.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = "PAGE"
    fld_char_2 = OxmlElement("w:fldChar")
    fld_char_2.set(qn("w:fldCharType"), "end")
    run._r.extend([fld_char_1, instr_text, fld_char_2])


def _configure_docx(doc: Document, title: str, *, author: str = "") -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.78)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.82)
    section.right_margin = Inches(0.82)
    section.header_distance = Inches(0.35)
    section.footer_distance = Inches(0.35)

    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = _color(INK)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15

    for style_name, size, color, before, after in (
        ("Title", 26, "000000", 0, 8),
        ("Subtitle", 12, "000000", 0, 18),
        ("Heading 1", 17, "000000", 18, 8),
        ("Heading 2", 13.5, "000000", 13, 6),
        ("Heading 3", 11.5, "000000", 9, 4),
    ):
        style = doc.styles[style_name]
        style.font.name = "Arial"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
        style.font.size = Pt(size)
        style.font.color.rgb = _color(color)
        style.font.bold = style_name != "Subtitle"
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    header = section.header.paragraphs[0]
    header.text = REFERENCE_LABEL
    _set_docx_font(header.runs[0], size=8.5, color=MUTED, bold=True)
    footer = section.footer.paragraphs[0]
    _add_page_number(footer)

    # Neutral properties: replaces the python-docx template defaults.
    props = doc.core_properties
    props.title = title
    props.author = author
    props.last_modified_by = author
    props.comments = ""
    props.keywords = ""
    props.category = ""
    props.revision = 1


def _add_docx_cover(doc: Document, result: dict) -> None:
    title = _plain_text(result.get("title"), "Untitled")
    kicker = doc.add_paragraph()
    kicker.paragraph_format.space_before = Pt(22)
    kicker.paragraph_format.space_after = Pt(10)
    run = kicker.add_run("TEACHER'S GUIDE")
    _set_docx_font(run, size=9, color=CYAN, bold=True)

    title_p = doc.add_paragraph(style="Title")
    title_p.add_run(title)
    subtitle = doc.add_paragraph(style="Subtitle")
    subtitle.add_run(_cover_subtitle(result))

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(12)
    label = p.add_run("Teacher's copy: ")
    _set_docx_font(label, size=9.5, color="000000", bold=True)
    body = p.add_run(REFERENCE_NOTE)
    _set_docx_font(body, size=9.5, color=INK)


def _cover_subtitle(result: dict) -> str:
    parts = [
        _document_subject(result),
        "Teacher's guide",
        datetime.now(UTC).strftime("%d %B %Y"),
    ]
    return " - ".join(part for part in parts if part)


def _add_docx_bullet(doc: Document, text: Any, *, level: int = 0) -> None:
    paragraph = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    paragraph.paragraph_format.space_after = Pt(3)
    run = paragraph.add_run(_plain_text(text))
    _set_docx_font(run, size=10)


def _add_docx_number(doc: Document, text: Any) -> None:
    paragraph = doc.add_paragraph(style="List Number")
    paragraph.paragraph_format.space_after = Pt(4)
    run = paragraph.add_run(_plain_text(text))
    _set_docx_font(run, size=10)


def _add_docx_labelled(doc: Document, label: str, value: Any) -> None:
    text = _plain_text(value)
    if not text:
        return
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    run = p.add_run(f"{label}: ")
    _set_docx_font(run, size=10.5, color=NAVY, bold=True)
    body = p.add_run(text)
    _set_docx_font(body, size=10.5)


def _add_docx_callout(doc: Document, label: str, value: Any, fill: str = LIGHT) -> None:
    _add_docx_labelled(doc, label, value)


def _add_docx_table(doc: Document, headers: list[str], rows: list[list[Any]], widths: list[float]):
    if not rows:
        return
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    _set_table_borders(table)
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    for index, width in enumerate(widths):
        table.columns[index].width = Inches(width)
    header_cells = table.rows[0].cells
    for index, header in enumerate(headers):
        header_cells[index].width = Inches(widths[index])
        header_cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        _shade_cell(header_cells[index], NAVY)
        _set_cell_margins(header_cells[index], top=100, bottom=100)
        p = header_cells[index].paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(header)
        _set_docx_font(run, size=9, color=WHITE, bold=True)
    for row_index, values in enumerate(rows):
        cells = table.add_row().cells
        for index, value in enumerate(values):
            cells[index].width = Inches(widths[index])
            cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_margins(cells[index], top=90, bottom=90)
            if row_index % 2:
                _shade_cell(cells[index], "F7F9FC")
            p = cells[index].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(_plain_text(value))
            _set_docx_font(run, size=9.2, color=INK)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)


def _create_docx(result: dict, path: Path, *, author: str = "") -> Path:
    doc = Document()
    title = _plain_text(result.get("title"), "Untitled")
    _configure_docx(doc, title, author=author)
    doc.core_properties.subject = _document_subject(result)
    _add_docx_cover(doc, result)
    _render_teaching_docx(doc, result)
    doc.save(path)
    return path


def _render_teaching_docx(doc: Document, result: dict) -> None:
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    doc.add_heading("Session at a Glance", level=1)
    _add_docx_table(
        doc,
        ["Course", "Audience", "Duration", "Level"],
        [
            [
                overview.get("course", "General"),
                overview.get("audience", "University students"),
                f"{overview.get('duration_minutes', '')} minutes",
                overview.get("difficulty", "Intermediate"),
            ]
        ],
        [1.7, 2.0, 1.3, 1.7],
    )
    _add_docx_callout(doc, "Lesson purpose", overview.get("lesson_purpose"))

    prerequisites = _as_list(overview.get("prerequisite_knowledge"))
    if prerequisites:
        doc.add_heading("Prerequisite Knowledge", level=2)
        for item in prerequisites:
            _add_docx_bullet(doc, item)

    formulas = _as_list(overview.get("core_formulas"))
    if formulas:
        doc.add_heading("Core Formula Sheet", level=2)
        _add_docx_table(
            doc,
            ["Concept and Formula"],
            [[item] for item in formulas],
            [6.7],
        )

    objectives = _as_list(result.get("learning_objectives"))
    if objectives:
        doc.add_heading("Learning Objectives", level=1)
        for item in objectives:
            _add_docx_number(doc, _objective_reference(item))

    sections = _as_list(result.get("lecture_sections"))
    if sections:
        doc.add_heading("Session Plan", level=1)
        plan_rows = []
        for section in sections:
            if not isinstance(section, dict):
                plan_rows.append(["", section, ""])
                continue
            content = section.get("content", {})
            focus = ""
            if isinstance(content, dict):
                focus = _plain_text(
                    content.get("key_points")
                    or content.get("classroom_activity")
                    or content.get("worked_example")
                )
            else:
                focus = _plain_text(content)
            plan_rows.append(
                [
                    f"{section.get('minutes', '')} min",
                    section.get("title", "Lecture Section"),
                    focus[:260],
                ]
            )
        _add_docx_table(doc, ["Time", "Section", "Teaching Focus"], plan_rows, [0.8, 2.35, 3.55])

        doc.add_page_break()
        doc.add_heading("Detailed Lecture Notes", level=1)
        for section in sections:
            if not isinstance(section, dict):
                doc.add_heading("Lecture Section", level=2)
                _add_docx_bullet(doc, section)
                continue
            minutes = section.get("minutes")
            suffix = f"  |  {minutes} minutes" if minutes else ""
            doc.add_heading(_plain_text(section.get("title"), "Lecture Section") + suffix, level=2)
            _render_teaching_section_docx(doc, section.get("content", {}))

    _render_teaching_assessment_docx(doc, result)

    case_study = result.get("case_study")
    if isinstance(case_study, dict) and case_study:
        doc.add_page_break()
        doc.add_heading("Case Study", level=1)
        doc.add_heading(_plain_text(case_study.get("title"), "Applied Case"), level=2)
        _add_docx_callout(doc, "Scenario", case_study.get("scenario"), "EEF3FA")
        questions = _as_list(case_study.get("questions"))
        if questions:
            doc.add_heading("Case Questions", level=3)
            for question in questions:
                _add_docx_number(doc, question)
        _add_docx_callout(doc, "Teaching notes", case_study.get("teaching_notes"), "F3F0FB")

    _render_sources_docx(doc, result)
    _render_quality_notes_docx(doc, result)


def _render_teaching_section_docx(doc: Document, content: Any) -> None:
    if not isinstance(content, dict):
        for item in _as_list(content):
            _add_docx_bullet(doc, item)
        return
    _add_docx_labelled(doc, "Explanation", content.get("explanation"))
    key_points = _as_list(content.get("key_points"))
    if key_points:
        doc.add_heading("Key Points", level=3)
        for point in key_points:
            _add_docx_bullet(doc, point)
    for label, key, fill in (
        ("Classroom activity", "classroom_activity", "EAF7F8"),
        ("Concept check", "concept_check", "EEF3FA"),
        ("Mini discussion", "mini_discussion", "F3F0FB"),
        ("Teaching tip", "teaching_tip", "FFF7E7"),
        ("Closing prompt", "closing_prompt", "EEF3FA"),
        ("Expected answer", "expected_answer", "EAF6F0"),
    ):
        _add_docx_callout(doc, label, content.get(key), fill)
    for key in ("worked_example", "worked_examples"):
        examples = _as_list(content.get(key))
        for index, example in enumerate(examples, start=1):
            if isinstance(example, dict):
                doc.add_heading(f"Worked Example {index}", level=3)
                _add_docx_labelled(doc, "Problem", example.get("problem"))
                _add_docx_callout(doc, "Solution", example.get("solution"), "EAF6F0")
            elif example:
                _add_docx_callout(doc, "Worked example", example)
    board_work = _as_list(content.get("board_work"))
    if board_work:
        doc.add_heading("Board / Slide Work", level=3)
        for item in board_work:
            _add_docx_bullet(doc, item)
    common_errors = _as_list(content.get("common_errors"))
    if common_errors:
        doc.add_heading("Common Errors to Address", level=3)
        for item in common_errors:
            _add_docx_bullet(doc, item)
    source_refs = _as_list(content.get("source_refs"))
    if source_refs:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(8)
        run = p.add_run(
            "Source references: " + "; ".join(_plain_text(item) for item in source_refs)
        )
        _set_docx_font(run, size=8.5, color=MUTED)


def _render_teaching_assessment_docx(doc: Document, result: dict) -> None:
    mcqs = _as_list(result.get("mcqs"))
    assignments = _as_list(result.get("assignments"))
    numerical = _as_list(result.get("numerical_problems"))
    bloom = _as_list(result.get("bloom_mapping"))
    discussion = _as_list(result.get("discussion_questions"))
    viva = _as_list(result.get("viva_questions"))
    if any((mcqs, assignments, numerical, bloom, discussion, viva)):
        doc.add_page_break()
        doc.add_heading("Assessment and Engagement Toolkit", level=1)

    if mcqs:
        doc.add_heading("Multiple-Choice Questions", level=2)
        for index, item in enumerate(mcqs, start=1):
            if not isinstance(item, dict):
                _add_docx_number(doc, item)
                continue
            q = doc.add_paragraph()
            q.paragraph_format.space_before = Pt(7)
            q.paragraph_format.space_after = Pt(3)
            run = q.add_run(f"{index}. {_plain_text(item.get('question'))}")
            _set_docx_font(run, size=10.5, color=NAVY, bold=True)
            for option_index, option in enumerate(_as_list(item.get("options"))):
                _add_docx_bullet(
                    doc,
                    f"{chr(65 + option_index)}. {_mcq_option_text(option)}",
                    level=1,
                )
            _add_docx_callout(
                doc,
                "Answer key and rationale",
                _mcq_answer_key(item),
                "EAF6F0",
            )

    if numerical:
        doc.add_heading("Numerical Problems", level=2)
        for index, item in enumerate(numerical, start=1):
            if isinstance(item, dict):
                doc.add_heading(f"Problem {index}", level=3)
                _add_docx_labelled(doc, "Question", item.get("problem"))
                if item.get("given"):
                    _add_docx_labelled(doc, "Given", item.get("given"))
                steps = item.get("solution_steps") or item.get("solution")
                _add_docx_labelled(doc, "Solution steps", steps)
                _add_docx_labelled(doc, "Final answer", item.get("final_answer"))
            else:
                _add_docx_number(doc, item)

    if assignments:
        doc.add_heading("Assignments", level=2)
        for item in assignments:
            if isinstance(item, dict):
                doc.add_heading(_plain_text(item.get("title"), "Assignment"), level=3)
                _add_docx_callout(doc, "Brief", item.get("prompt"), "EEF3FA")
                deliverables = _as_list(item.get("deliverables"))
                if deliverables:
                    doc.add_paragraph("Deliverables", style="Heading 3")
                    for deliverable in deliverables:
                        _add_docx_bullet(doc, deliverable)
                rubric = _as_list(item.get("rubric"))
                if rubric:
                    rows = []
                    for criterion in rubric:
                        if isinstance(criterion, dict):
                            rows.append(
                                [
                                    criterion.get("criterion"),
                                    f"{criterion.get('weight_percent', '')}%",
                                    criterion.get("description"),
                                ]
                            )
                        else:
                            rows.append([criterion, "", ""])
                    doc.add_paragraph("Assessment criteria", style="Heading 3")
                    _add_docx_table(
                        doc,
                        ["Criterion", "Weight", "Performance description"],
                        rows,
                        [1.6, 0.8, 4.3],
                    )
            else:
                _add_docx_bullet(doc, item)

    if bloom:
        rows = []
        for item in bloom:
            if isinstance(item, dict):
                rows.append([item.get("objective"), item.get("level"), item.get("assessment")])
            else:
                rows.append([item, "", ""])
        doc.add_heading("Bloom's Taxonomy Mapping", level=2)
        _add_docx_table(
            doc,
            ["Learning Objective", "Bloom Level", "Assessment"],
            rows,
            [2.5, 1.2, 3.0],
        )

    if discussion:
        doc.add_heading("Discussion Questions", level=2)
        for item in discussion:
            _add_docx_number(doc, item)

    if viva:
        doc.add_heading("Viva Questions and Expected Answers", level=2)
        rows = []
        for item in viva:
            if isinstance(item, dict):
                rows.append(
                    [
                        item.get("question"),
                        item.get("expected_answer"),
                        _plain_text(item.get("difficulty")).capitalize(),
                    ]
                )
            else:
                rows.append([item, "", ""])
        _add_docx_table(
            doc,
            ["Question", "Expected Answer / Talking Points", "Level"],
            rows,
            [2.7, 3.0, 1.0],
        )


def _render_sources_docx(doc: Document, result: dict) -> None:
    sources = _as_list(result.get("sources"))
    evidence = (
        result.get("overview", {}).get("evidence_base", [])
        if isinstance(result.get("overview"), dict)
        else []
    )
    if not sources and not evidence:
        return
    doc.add_page_break()
    doc.add_heading("Sources and Further Reading", level=1)
    for item in sources:
        if isinstance(item, dict):
            title = _plain_text(item.get("title"), "Source")
            year = _plain_text(item.get("year"))
            url = _plain_text(item.get("url"))
            text = title + (f" ({year})" if year else "") + (f"\n{url}" if url else "")
        else:
            text = _plain_text(item)
        _add_docx_number(doc, text)
    if evidence:
        doc.add_heading("Evidence Base Named in the Teaching Plan", level=2)
        for item in _as_list(evidence):
            _add_docx_bullet(doc, item)


# Preparation notes keep every review item but read like an ordinary planning checklist.
_NOTE_SECTIONS = (
    ("Check these points against your sources", "unverified_claims"),
    ("Look at these first", "review_priorities"),
    ("Limits of this material", "limitations"),
)


def _notes_heading(result: dict) -> str:
    return "Before You Teach"


def _render_quality_notes_docx(doc: Document, result: dict) -> None:
    notes = result.get("quality_notes")
    if not isinstance(notes, dict):
        return
    doc.add_heading(_notes_heading(result), level=1)
    for heading, key in _NOTE_SECTIONS:
        values = _as_list(notes.get(key))
        if values:
            doc.add_heading(heading, level=2)
            for value in values:
                _add_docx_bullet(doc, value)


# ---------------------------------------------------------------------------
# PDF - academic report layout mirroring the DOCX semantic structure
# ---------------------------------------------------------------------------


def _pdf_styles():
    regular_font, bold_font = _register_pdf_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "AcademicTitle",
            parent=base["Title"],
            fontName=bold_font,
            fontSize=23,
            leading=28,
            textColor=colors.HexColor(f"#{NAVY}"),
            alignment=TA_LEFT,
            spaceAfter=12,
        ),
        "subtitle": ParagraphStyle(
            "AcademicSubtitle",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor(f"#{MUTED}"),
            spaceAfter=16,
        ),
        "h1": ParagraphStyle(
            "AcademicH1",
            parent=base["Heading1"],
            fontName=bold_font,
            fontSize=15,
            leading=18,
            textColor=colors.black,
            spaceBefore=14,
            spaceAfter=7,
        ),
        "h2": ParagraphStyle(
            "AcademicH2",
            parent=base["Heading2"],
            fontName=bold_font,
            fontSize=11.5,
            leading=14,
            textColor=colors.black,
            spaceBefore=10,
            spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "AcademicH3",
            parent=base["Heading3"],
            fontName=bold_font,
            fontSize=10,
            leading=12,
            textColor=colors.black,
            spaceBefore=7,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "AcademicBody",
            parent=base["BodyText"],
            fontName=regular_font,
            fontSize=9.2,
            leading=12.5,
            textColor=colors.HexColor(f"#{INK}"),
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "AcademicSmall",
            parent=base["BodyText"],
            fontName=regular_font,
            fontSize=7.8,
            leading=10,
            textColor=colors.HexColor(f"#{MUTED}"),
        ),
        "bullet": ParagraphStyle(
            "AcademicBullet",
            parent=base["BodyText"],
            fontName=regular_font,
            fontSize=9.2,
            leading=12.5,
            leftIndent=14,
            firstLineIndent=-7,
            bulletIndent=3,
            spaceAfter=3,
        ),
        "callout": ParagraphStyle(
            "AcademicCallout",
            parent=base["BodyText"],
            fontName=regular_font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor(f"#{INK}"),
        ),
    }


def _pdf_paragraph(text: Any, style) -> Paragraph:
    return Paragraph(escape(_plain_text(text)).replace("\n", "<br/>"), style)


def _pdf_bullets(story: list, values: Any, styles) -> None:
    for item in _as_list(values):
        story.append(Paragraph("• " + escape(_plain_text(item)), styles["bullet"]))


def _pdf_callout(story: list, label: str, value: Any, styles, fill: str = "F3F6FA"):
    text = _plain_text(value)
    if not text:
        return
    story.append(
        Paragraph(
            f"<b>{escape(label)}:</b> {escape(text).replace(chr(10), '<br/>')}",
            styles["body"],
        )
    )


def _pdf_table(story: list, headers: list[str], rows: list[list[Any]], widths: list[float], styles):
    if not rows:
        return
    data = [[_pdf_paragraph(h, styles["small"]) for h in headers]]
    for row in rows:
        data.append([_pdf_paragraph(value, styles["small"]) for value in row])
    table = Table(data, colWidths=[width * inch for width in widths], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{NAVY}")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), _PDF_FONT_BOLD),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor(f"#{LINE}")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ]
        )
    )
    story.extend([table, Spacer(1, 7)])


def _pdf_page(canvas, doc):
    canvas.saveState()
    width, height = LETTER
    canvas.setFont(_PDF_FONT_BOLD, 7.5)
    canvas.setFillColor(colors.HexColor(f"#{MUTED}"))
    canvas.drawString(0.82 * inch, height - 0.35 * inch, REFERENCE_LABEL)
    canvas.setFont(_PDF_FONT_REGULAR, 8)
    canvas.drawRightString(width - 0.82 * inch, 0.38 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _create_pdf(result: dict, path: Path, *, author: str = "") -> Path:
    title = _plain_text(result.get("title"), "Untitled")
    styles = _pdf_styles()
    story = [
        Spacer(1, 0.2 * inch),
        _pdf_paragraph(title, styles["title"]),
        _pdf_paragraph(_cover_subtitle(result), styles["subtitle"]),
    ]
    _pdf_callout(
        story,
        "Teacher's copy",
        REFERENCE_NOTE,
        styles,
        "EAF2FB",
    )
    _render_teaching_pdf(story, result, styles)
    document = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        rightMargin=0.82 * inch,
        leftMargin=0.82 * inch,
        topMargin=0.64 * inch,
        bottomMargin=0.62 * inch,
        # Neutral properties instead of the library's default creator and producer strings.
        title=title,
        author=author,
        subject=_document_subject(result),
        creator=author,
        producer=author,
    )
    document.build(
        story,
        onFirstPage=_pdf_page,
        onLaterPages=_pdf_page,
    )
    return path


def _render_teaching_pdf(story: list, result: dict, styles) -> None:
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    story.append(_pdf_paragraph("Session at a Glance", styles["h1"]))
    _pdf_table(
        story,
        ["Course", "Audience", "Duration", "Level"],
        [
            [
                overview.get("course"),
                overview.get("audience"),
                f"{overview.get('duration_minutes', '')} minutes",
                overview.get("difficulty"),
            ]
        ],
        [1.6, 2.0, 1.35, 1.75],
        styles,
    )
    _pdf_callout(story, "Lesson purpose", overview.get("lesson_purpose"), styles, "EEF3FA")
    if overview.get("prerequisite_knowledge"):
        story.append(_pdf_paragraph("Prerequisite Knowledge", styles["h2"]))
        _pdf_bullets(story, overview.get("prerequisite_knowledge"), styles)
    if overview.get("core_formulas"):
        story.append(_pdf_paragraph("Core Formula Sheet", styles["h2"]))
        _pdf_table(
            story,
            ["Concept and Formula"],
            [[item] for item in _as_list(overview.get("core_formulas"))],
            [6.7],
            styles,
        )

    objectives = _as_list(result.get("learning_objectives"))
    if objectives:
        story.append(_pdf_paragraph("Learning Objectives", styles["h1"]))
        for index, item in enumerate(objectives, start=1):
            story.append(
                Paragraph(f"{index}. {escape(_objective_reference(item))}", styles["bullet"])
            )

    sections = _as_list(result.get("lecture_sections"))
    if sections:
        story.append(_pdf_paragraph("Session Plan", styles["h1"]))
        rows = []
        for section in sections:
            if isinstance(section, dict):
                content = section.get("content", {})
                focus = content.get("key_points") if isinstance(content, dict) else content
                rows.append(
                    [
                        f"{section.get('minutes', '')} min",
                        section.get("title"),
                        _plain_text(focus)[:250],
                    ]
                )
        _pdf_table(story, ["Time", "Section", "Teaching Focus"], rows, [0.75, 2.3, 3.65], styles)
        story.extend([PageBreak(), _pdf_paragraph("Detailed Lecture Notes", styles["h1"])])
        for section in sections:
            if not isinstance(section, dict):
                continue
            story.append(
                _pdf_paragraph(
                    _plain_text(section.get("title"))
                    + (f" - {section.get('minutes')} minutes" if section.get("minutes") else ""),
                    styles["h2"],
                )
            )
            _render_teaching_section_pdf(story, section.get("content", {}), styles)

    _render_teaching_assessment_pdf(story, result, styles)
    case_study = result.get("case_study")
    if isinstance(case_study, dict) and case_study:
        story.extend([Spacer(1, 10), _pdf_paragraph("Case Study", styles["h1"])])
        story.append(_pdf_paragraph(case_study.get("title"), styles["h2"]))
        _pdf_callout(story, "Scenario", case_study.get("scenario"), styles, "EEF3FA")
        if case_study.get("questions"):
            story.append(_pdf_paragraph("Case Questions", styles["h3"]))
            for index, question in enumerate(_as_list(case_study.get("questions")), start=1):
                story.append(
                    Paragraph(f"{index}. {escape(_plain_text(question))}", styles["bullet"])
                )
        _pdf_callout(story, "Teaching notes", case_study.get("teaching_notes"), styles, "F3F0FB")
    _render_sources_pdf(story, result, styles)
    _render_quality_notes_pdf(story, result, styles)


def _render_teaching_section_pdf(story: list, content: Any, styles) -> None:
    if not isinstance(content, dict):
        _pdf_bullets(story, content, styles)
        return
    if content.get("explanation"):
        _pdf_callout(story, "Explanation", content.get("explanation"), styles)
    if content.get("key_points"):
        story.append(_pdf_paragraph("Key Points", styles["h3"]))
        _pdf_bullets(story, content.get("key_points"), styles)
    for label, key, fill in (
        ("Classroom activity", "classroom_activity", "EAF7F8"),
        ("Concept check", "concept_check", "EEF3FA"),
        ("Mini discussion", "mini_discussion", "F3F0FB"),
        ("Teaching tip", "teaching_tip", "FFF7E7"),
        ("Closing prompt", "closing_prompt", "EEF3FA"),
        ("Expected answer", "expected_answer", "EAF6F0"),
    ):
        _pdf_callout(story, label, content.get(key), styles, fill)
    for key in ("worked_example", "worked_examples"):
        for index, example in enumerate(_as_list(content.get(key)), start=1):
            if isinstance(example, dict):
                story.append(_pdf_paragraph(f"Worked Example {index}", styles["h3"]))
                _pdf_callout(
                    story,
                    "Problem",
                    example.get("problem"),
                    styles,
                    "EEF3FA",
                )
                _pdf_callout(
                    story,
                    "Solution",
                    example.get("solution"),
                    styles,
                    "EAF6F0",
                )
    if content.get("board_work"):
        story.append(_pdf_paragraph("Board / Slide Work", styles["h3"]))
        _pdf_bullets(story, content.get("board_work"), styles)
    if content.get("common_errors"):
        story.append(_pdf_paragraph("Common Errors to Address", styles["h3"]))
        _pdf_bullets(story, content.get("common_errors"), styles)
    if content.get("source_refs"):
        story.append(
            _pdf_paragraph(
                "Source references: "
                + "; ".join(map(_plain_text, _as_list(content.get("source_refs")))),
                styles["small"],
            )
        )


def _render_teaching_assessment_pdf(story: list, result: dict, styles) -> None:
    if any(
        result.get(key)
        for key in (
            "mcqs",
            "assignments",
            "numerical_problems",
            "bloom_mapping",
            "discussion_questions",
            "viva_questions",
        )
    ):
        story.extend(
            [PageBreak(), _pdf_paragraph("Assessment and Engagement Toolkit", styles["h1"])]
        )
    mcqs = _as_list(result.get("mcqs"))
    if mcqs:
        story.append(_pdf_paragraph("Multiple-Choice Questions", styles["h2"]))
        for index, item in enumerate(mcqs, start=1):
            if not isinstance(item, dict):
                continue
            story.append(
                _pdf_paragraph(f"{index}. {_plain_text(item.get('question'))}", styles["h3"])
            )
            for option_index, option in enumerate(_as_list(item.get("options"))):
                story.append(
                    Paragraph(
                        f"{chr(65 + option_index)}. {escape(_mcq_option_text(option))}",
                        styles["bullet"],
                    )
                )
            _pdf_callout(
                story,
                "Answer key and rationale",
                _mcq_answer_key(item),
                styles,
                "EAF6F0",
            )
    numerical = _as_list(result.get("numerical_problems"))
    if numerical:
        story.append(_pdf_paragraph("Numerical Problems", styles["h2"]))
        for index, item in enumerate(numerical, start=1):
            if isinstance(item, dict):
                story.append(_pdf_paragraph(f"Problem {index}", styles["h3"]))
                _pdf_callout(story, "Question", item.get("problem"), styles, "EEF3FA")
                _pdf_callout(story, "Given", item.get("given"), styles)
                _pdf_callout(
                    story,
                    "Solution steps",
                    item.get("solution_steps") or item.get("solution"),
                    styles,
                )
                _pdf_callout(story, "Final answer", item.get("final_answer"), styles)
    assignments = _as_list(result.get("assignments"))
    if assignments:
        story.append(_pdf_paragraph("Assignments", styles["h2"]))
        for item in assignments:
            if isinstance(item, dict):
                story.append(_pdf_paragraph(item.get("title"), styles["h3"]))
                _pdf_callout(story, "Brief", item.get("prompt"), styles, "EEF3FA")
                if item.get("deliverables"):
                    story.append(_pdf_paragraph("Deliverables", styles["h3"]))
                    _pdf_bullets(story, item.get("deliverables"), styles)
                rubric_rows = []
                for criterion in _as_list(item.get("rubric")):
                    if isinstance(criterion, dict):
                        rubric_rows.append(
                            [
                                criterion.get("criterion"),
                                f"{criterion.get('weight_percent', '')}%",
                                criterion.get("description"),
                            ]
                        )
                    else:
                        rubric_rows.append([criterion, "", ""])
                if rubric_rows:
                    _pdf_table(
                        story,
                        ["Criterion", "Weight", "Performance description"],
                        rubric_rows,
                        [1.6, 0.8, 4.3],
                        styles,
                    )
    bloom = _as_list(result.get("bloom_mapping"))
    if bloom:
        rows = [
            [item.get("objective"), item.get("level"), item.get("assessment")]
            for item in bloom
            if isinstance(item, dict)
        ]
        story.append(_pdf_paragraph("Bloom's Taxonomy Mapping", styles["h2"]))
        _pdf_table(
            story,
            ["Learning Objective", "Bloom Level", "Assessment"],
            rows,
            [2.5, 1.2, 3.0],
            styles,
        )
    discussion = _as_list(result.get("discussion_questions"))
    if discussion:
        story.append(_pdf_paragraph("Discussion Questions", styles["h2"]))
        for index, item in enumerate(discussion, start=1):
            story.append(Paragraph(f"{index}. {escape(_plain_text(item))}", styles["bullet"]))
    viva = _as_list(result.get("viva_questions"))
    if viva:
        rows = []
        for item in viva:
            if isinstance(item, dict):
                rows.append(
                    [
                        item.get("question"),
                        item.get("expected_answer"),
                        _plain_text(item.get("difficulty")).capitalize(),
                    ]
                )
            else:
                rows.append([item, "", ""])
        story.append(_pdf_paragraph("Viva Questions and Expected Answers", styles["h2"]))
        _pdf_table(
            story,
            ["Question", "Expected Answer / Talking Points", "Level"],
            rows,
            [2.7, 3.0, 1.0],
            styles,
        )


def _render_sources_pdf(story: list, result: dict, styles) -> None:
    sources = _as_list(result.get("sources"))
    evidence = (
        result.get("overview", {}).get("evidence_base", [])
        if isinstance(result.get("overview"), dict)
        else []
    )
    if not sources and not evidence:
        return
    story.extend([PageBreak(), _pdf_paragraph("Sources and Further Reading", styles["h1"])])
    for index, item in enumerate(sources, start=1):
        if isinstance(item, dict):
            title = _plain_text(item.get("title"), "Source")
            year = _plain_text(item.get("year"))
            url = _plain_text(item.get("url"))
            story.append(
                Paragraph(
                    f"{index}. {escape(title)}"
                    + (f" ({escape(year)})" if year else "")
                    + (f"<br/><font color='#{MUTED}'>{escape(url)}</font>" if url else ""),
                    styles["body"],
                )
            )
        else:
            story.append(Paragraph(f"{index}. {escape(_plain_text(item))}", styles["body"]))
    if evidence:
        story.append(_pdf_paragraph("Evidence Base Named in the Teaching Plan", styles["h2"]))
        _pdf_bullets(story, evidence, styles)


def _render_quality_notes_pdf(story: list, result: dict, styles) -> None:
    notes = result.get("quality_notes")
    if not isinstance(notes, dict):
        return
    story.append(_pdf_paragraph(_notes_heading(result), styles["h1"]))
    for heading, key in _NOTE_SECTIONS:
        values = _as_list(notes.get(key))
        if values:
            story.append(_pdf_paragraph(heading, styles["h2"]))
            _pdf_bullets(story, values, styles)


# ---------------------------------------------------------------------------
# PPTX - student presentation (see app/artifacts/slides.py)
# ---------------------------------------------------------------------------


def _create_pptx(result: dict, path: Path, *, author: str = "") -> Path:
    # Imported here because slides.py reuses this module's text helpers.
    from app.artifacts.slides import create_presentation

    return create_presentation(result, path, author=author)
