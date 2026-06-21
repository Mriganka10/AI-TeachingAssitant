import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from pptx import Presentation
from pptx.dml.color import RGBColor as PptxRGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches as PptxInches
from pptx.util import Pt as PptxPt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

NAVY = "12243F"
BLUE = "2557D6"
CYAN = "22A6B6"
PURPLE = "6654C7"
INK = "253247"
MUTED = "667386"
LIGHT = "F3F6FA"
LINE = "DDE4ED"
WHITE = "FFFFFF"
GREEN = "19845F"
GOLD = "9A6B13"


def _plain_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip()
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
        return "; ".join(
            f"{_humanize(key)}: {_plain_text(item)}" for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return "\n".join(_plain_text(item) for item in value if item is not None)
    return str(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _safe_title(value: Any) -> str:
    text = _plain_text(value, "output")
    return "".join(c if c.isalnum() else "-" for c in text).strip("-")[:60] or "output"


def _color(value: str) -> RGBColor:
    return RGBColor.from_string(value)


def _pptx_color(value: str) -> PptxRGBColor:
    return PptxRGBColor.from_string(value)


def create_artifacts(result: dict, *, agent_type: str, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_title(result.get("title", agent_type))
    json_path = output_dir / f"{base}.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    paths = [
        json_path,
        _create_docx(result, output_dir / f"{base}.docx", agent_type),
        _create_pdf(result, output_dir / f"{base}.pdf", agent_type),
        _create_pptx(result, output_dir / f"{base}.pptx", agent_type),
    ]
    return paths


# ---------------------------------------------------------------------------
# DOCX - standard_business_brief preset with academic-report named overrides
# ---------------------------------------------------------------------------


def _set_docx_font(run, *, size: float, color: str = INK, bold: bool = False) -> None:
    run.font.name = "Arial"
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")
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


def _configure_docx(doc: Document, title: str, agent_type: str) -> None:
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
        ("Title", 26, NAVY, 0, 8),
        ("Subtitle", 12, MUTED, 0, 18),
        ("Heading 1", 17, BLUE, 18, 8),
        ("Heading 2", 13.5, NAVY, 13, 6),
        ("Heading 3", 11.5, PURPLE if agent_type == "research" else CYAN, 9, 4),
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
    header.text = "PROFESSOR AI  |  " + (
        "TEACHING PACKAGE" if agent_type == "teaching" else "RESEARCH SYNTHESIS"
    )
    _set_docx_font(header.runs[0], size=8.5, color=MUTED, bold=True)
    footer = section.footer.paragraphs[0]
    _add_page_number(footer)

    props = doc.core_properties
    props.title = title
    props.subject = "AI-assisted faculty draft for professor review"
    props.author = "Professor AI Workspace"
    props.comments = "Generated material must be reviewed by a professor before use."


def _add_docx_cover(doc: Document, result: dict, agent_type: str) -> None:
    title = _plain_text(result.get("title"), "Professor AI Output")
    kicker = doc.add_paragraph()
    kicker.paragraph_format.space_before = Pt(22)
    kicker.paragraph_format.space_after = Pt(10)
    run = kicker.add_run(
        "TEACHING & CURRICULUM"
        if agent_type == "teaching"
        else "RESEARCH & LITERATURE SYNTHESIS"
    )
    _set_docx_font(run, size=9, color=CYAN if agent_type == "teaching" else PURPLE, bold=True)

    title_p = doc.add_paragraph(style="Title")
    title_p.add_run(title)
    subtitle = doc.add_paragraph(style="Subtitle")
    subtitle.add_run(
        "Professor review edition  |  Generated "
        + datetime.now(UTC).strftime("%d %B %Y")
    )

    notice = doc.add_table(rows=1, cols=1)
    notice.alignment = WD_TABLE_ALIGNMENT.LEFT
    notice.autofit = False
    notice.columns[0].width = Inches(6.7)
    cell = notice.cell(0, 0)
    _shade_cell(cell, "EAF2FB")
    _set_cell_margins(cell, top=120, bottom=120, start=160, end=160)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    label = p.add_run("FACULTY REVIEW NOTE  ")
    _set_docx_font(label, size=9, color=BLUE, bold=True)
    body = p.add_run(
        "This is AI-assisted draft material. Verify facts, citations, calculations, "
        "assessment suitability, and institutional policy before classroom or publication use."
    )
    _set_docx_font(body, size=9, color=INK)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _add_docx_bullet(doc: Document, text: Any, *, level: int = 0) -> None:
    paragraph = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    paragraph.paragraph_format.space_after = Pt(3)
    paragraph.add_run(_plain_text(text))


def _add_docx_number(doc: Document, text: Any) -> None:
    paragraph = doc.add_paragraph(style="List Number")
    paragraph.paragraph_format.space_after = Pt(4)
    paragraph.add_run(_plain_text(text))


def _add_docx_labelled(doc: Document, label: str, value: Any) -> None:
    text = _plain_text(value)
    if not text:
        return
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    run = p.add_run(f"{label}: ")
    _set_docx_font(run, size=10.5, color=NAVY, bold=True)
    p.add_run(text)


def _add_docx_callout(doc: Document, label: str, value: Any, fill: str = LIGHT) -> None:
    text = _plain_text(value)
    if not text:
        return
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    table.columns[0].width = Inches(6.7)
    cell = table.cell(0, 0)
    _shade_cell(cell, fill)
    _set_cell_margins(cell, top=120, bottom=120, start=150, end=150)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    label_run = p.add_run(f"{label.upper()}\n")
    _set_docx_font(label_run, size=8.5, color=BLUE, bold=True)
    body = p.add_run(text)
    _set_docx_font(body, size=10, color=INK)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def _add_docx_table(doc: Document, headers: list[str], rows: list[list[Any]], widths: list[float]):
    if not rows:
        return
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
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


def _create_docx(result: dict, path: Path, agent_type: str) -> Path:
    doc = Document()
    title = _plain_text(result.get("title"), "Professor AI Output")
    _configure_docx(doc, title, agent_type)
    _add_docx_cover(doc, result, agent_type)
    if agent_type == "teaching":
        _render_teaching_docx(doc, result)
    else:
        _render_research_docx(doc, result)
    doc.save(path)
    return path


def _render_teaching_docx(doc: Document, result: dict) -> None:
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    doc.add_heading("Session at a Glance", level=1)
    _add_docx_table(
        doc,
        ["Course", "Audience", "Duration", "Level"],
        [[
            overview.get("course", "General"),
            overview.get("audience", "University students"),
            f"{overview.get('duration_minutes', '')} minutes",
            overview.get("difficulty", "Intermediate"),
        ]],
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
            if isinstance(item, dict):
                objective = item.get("objective") or item.get("text") or item
                prefix = f"{item.get('id')}: " if item.get("id") else ""
                _add_docx_number(doc, prefix + _plain_text(objective))
            else:
                _add_docx_number(doc, item)

    sections = _as_list(result.get("lecture_sections"))
    if sections:
        doc.add_heading("60-Minute Session Plan", level=1)
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
            plan_rows.append([
                f"{section.get('minutes', '')} min",
                section.get("title", "Lecture Section"),
                focus[:260],
            ])
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


def _render_teaching_section_docx(doc: Document, content: Any) -> None:
    if not isinstance(content, dict):
        for item in _as_list(content):
            _add_docx_bullet(doc, item)
        return
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
        run = p.add_run("Source references: " + "; ".join(_plain_text(item) for item in source_refs))
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
                _add_docx_bullet(doc, f"{chr(65 + option_index)}. {_plain_text(option)}", level=1)
            _add_docx_callout(
                doc,
                "Answer and rationale",
                f"{_plain_text(item.get('answer'))}\n{_plain_text(item.get('explanation'))}",
                "EAF6F0",
            )

    if numerical:
        doc.add_heading("Numerical Problems", level=2)
        for index, item in enumerate(numerical, start=1):
            if isinstance(item, dict):
                doc.add_heading(f"Problem {index}", level=3)
                _add_docx_labelled(doc, "Question", item.get("problem"))
                _add_docx_callout(doc, "Solution", item.get("solution"), "EAF6F0")
            else:
                _add_docx_number(doc, item)

    if assignments:
        doc.add_heading("Assignments", level=2)
        for item in assignments:
            if isinstance(item, dict):
                doc.add_heading(_plain_text(item.get("title"), "Assignment"), level=3)
                _add_docx_callout(doc, "Brief", item.get("prompt"), "EEF3FA")
                rubric = _as_list(item.get("rubric"))
                if rubric:
                    doc.add_paragraph("Assessment criteria:", style="Heading 3")
                    for criterion in rubric:
                        _add_docx_bullet(doc, criterion)
            else:
                _add_docx_bullet(doc, item)

    if bloom:
        rows = []
        for item in bloom:
            if isinstance(item, dict):
                rows.append([item.get("objective"), item.get("level")])
            else:
                rows.append([item, ""])
        doc.add_heading("Bloom's Taxonomy Mapping", level=2)
        _add_docx_table(doc, ["Learning Objective", "Bloom Level"], rows, [5.15, 1.55])

    if discussion:
        doc.add_heading("Discussion Questions", level=2)
        for item in discussion:
            _add_docx_number(doc, item)

    if viva:
        doc.add_heading("Viva Questions and Expected Answers", level=2)
        rows = []
        for item in viva:
            if isinstance(item, dict):
                rows.append([item.get("question"), item.get("expected_answer")])
            else:
                rows.append([item, ""])
        _add_docx_table(doc, ["Question", "Expected Answer / Talking Points"], rows, [3.0, 3.7])


def _render_research_docx(doc: Document, result: dict) -> None:
    summary = result.get("executive_summary")
    doc.add_heading("Executive Summary", level=1)
    if isinstance(summary, dict):
        _add_docx_callout(doc, "Evidence", summary.get("evidence"), "EEF3FA")
        _add_docx_callout(doc, "Interpretation", summary.get("inference"), "F3F0FB")
    else:
        doc.add_paragraph(_plain_text(summary))

    themes = _as_list(result.get("themes"))
    if themes:
        doc.add_heading("Thematic Literature Synthesis", level=1)
        for index, item in enumerate(themes, start=1):
            if not isinstance(item, dict):
                doc.add_heading(f"Theme {index}", level=2)
                doc.add_paragraph(_plain_text(item))
                continue
            doc.add_heading(f"{index}. {_plain_text(item.get('theme'), 'Theme')}", level=2)
            synthesis = item.get("synthesis")
            if isinstance(synthesis, dict):
                _add_docx_labelled(doc, "Evidence", synthesis.get("evidence"))
                _add_docx_labelled(doc, "Interpretation", synthesis.get("inference"))
            else:
                doc.add_paragraph(_plain_text(synthesis))
            papers = _as_list(item.get("papers"))
            if papers:
                p = doc.add_paragraph()
                run = p.add_run("Studies represented: " + "; ".join(_plain_text(x) for x in papers))
                _set_docx_font(run, size=8.5, color=MUTED)

    methods = _as_list(result.get("methodology_comparison"))
    if methods:
        doc.add_page_break()
        doc.add_heading("Methodology Comparison", level=1)
        for index, item in enumerate(methods, start=1):
            if not isinstance(item, dict):
                _add_docx_number(doc, item)
                continue
            doc.add_heading(f"{index}. {_plain_text(item.get('method'), 'Method')}", level=2)
            _add_docx_labelled(doc, "Strengths", item.get("strengths"))
            _add_docx_labelled(doc, "Limitations", item.get("limitations"))
            papers = _as_list(item.get("papers"))
            if papers:
                _add_docx_labelled(doc, "Representative studies", "; ".join(map(_plain_text, papers)))

    gaps = _as_list(result.get("research_gaps"))
    if gaps:
        doc.add_heading("Research Gaps", level=1)
        for index, item in enumerate(gaps, start=1):
            if isinstance(item, dict):
                doc.add_heading(f"{index}. {_plain_text(item.get('gap'), 'Research gap')}", level=2)
                _add_docx_callout(doc, "Evidence for the gap", item.get("evidence"), "EEF3FA")
                _add_docx_callout(doc, "Research implication", item.get("inference"), "F3F0FB")
            else:
                _add_docx_number(doc, item)

    questions = _as_list(result.get("research_questions"))
    if questions:
        doc.add_heading("Proposed Research Questions", level=1)
        for question in questions:
            _add_docx_number(doc, question)

    future = _as_list(result.get("future_scope"))
    if future:
        doc.add_heading("Future Scope", level=1)
        rows = []
        for item in future:
            if isinstance(item, dict):
                rows.append([item.get("area"), item.get("scope")])
            else:
                rows.append([item, ""])
        _add_docx_table(doc, ["Research Area", "Opportunity"], rows, [2.0, 4.7])

    suggestions = _as_list(result.get("methodology_suggestions"))
    if suggestions:
        doc.add_heading("Methodology Recommendations", level=1)
        for index, item in enumerate(suggestions, start=1):
            if isinstance(item, dict):
                doc.add_heading(
                    f"{index}. {_plain_text(item.get('suggestion'), 'Recommendation')}", level=2
                )
                _add_docx_labelled(doc, "Rationale", item.get("rationale"))
            else:
                _add_docx_number(doc, item)

    references = _as_list(result.get("apa_references"))
    if references:
        doc.add_page_break()
        doc.add_heading("APA References", level=1)
        for reference in references:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.25)
            p.paragraph_format.first_line_indent = Inches(-0.25)
            p.paragraph_format.space_after = Pt(5)
            p.add_run(_plain_text(reference))

    _render_sources_docx(doc, result)


def _render_sources_docx(doc: Document, result: dict) -> None:
    sources = _as_list(result.get("sources"))
    evidence = result.get("overview", {}).get("evidence_base", []) if isinstance(
        result.get("overview"), dict
    ) else []
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


# ---------------------------------------------------------------------------
# PDF - academic report layout mirroring the DOCX semantic structure
# ---------------------------------------------------------------------------


def _pdf_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "AcademicTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=23,
            leading=28,
            textColor=colors.HexColor(f"#{NAVY}"),
            alignment=TA_LEFT,
            spaceAfter=12,
        ),
        "subtitle": ParagraphStyle(
            "AcademicSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor(f"#{MUTED}"),
            spaceAfter=16,
        ),
        "h1": ParagraphStyle(
            "AcademicH1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=colors.HexColor(f"#{BLUE}"),
            spaceBefore=14,
            spaceAfter=7,
        ),
        "h2": ParagraphStyle(
            "AcademicH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=14,
            textColor=colors.HexColor(f"#{NAVY}"),
            spaceBefore=10,
            spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "AcademicH3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=colors.HexColor(f"#{PURPLE}"),
            spaceBefore=7,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "AcademicBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=12.5,
            textColor=colors.HexColor(f"#{INK}"),
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "AcademicSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.8,
            leading=10,
            textColor=colors.HexColor(f"#{MUTED}"),
        ),
        "bullet": ParagraphStyle(
            "AcademicBullet",
            parent=base["BodyText"],
            fontName="Helvetica",
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
            fontName="Helvetica",
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
    content = Paragraph(
        f"<b>{escape(label.upper())}</b><br/>{escape(text).replace(chr(10), '<br/>')}",
        styles["callout"],
    )
    table = Table([[content]], colWidths=[6.7 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(f"#{fill}")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(f"#{LINE}")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend([table, Spacer(1, 7)])


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
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
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


def _pdf_page(canvas, doc, agent_type: str):
    canvas.saveState()
    width, height = LETTER
    canvas.setStrokeColor(colors.HexColor(f"#{LINE}"))
    canvas.setLineWidth(0.5)
    canvas.line(0.82 * inch, height - 0.46 * inch, width - 0.82 * inch, height - 0.46 * inch)
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.setFillColor(colors.HexColor(f"#{MUTED}"))
    label = "PROFESSOR AI  |  " + (
        "TEACHING PACKAGE" if agent_type == "teaching" else "RESEARCH SYNTHESIS"
    )
    canvas.drawString(0.82 * inch, height - 0.35 * inch, label)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(width - 0.82 * inch, 0.38 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _create_pdf(result: dict, path: Path, agent_type: str) -> Path:
    title = _plain_text(result.get("title"), "Professor AI Output")
    styles = _pdf_styles()
    story = [
        Spacer(1, 0.2 * inch),
        _pdf_paragraph(title, styles["title"]),
        _pdf_paragraph(
            "Professor review edition  |  AI-assisted draft  |  "
            + datetime.now(UTC).strftime("%d %B %Y"),
            styles["subtitle"],
        ),
    ]
    _pdf_callout(
        story,
        "Faculty review note",
        "Verify facts, citations, calculations, assessment suitability, and institutional "
        "policy before classroom or publication use.",
        styles,
        "EAF2FB",
    )
    if agent_type == "teaching":
        _render_teaching_pdf(story, result, styles)
    else:
        _render_research_pdf(story, result, styles)
    document = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        rightMargin=0.82 * inch,
        leftMargin=0.82 * inch,
        topMargin=0.64 * inch,
        bottomMargin=0.62 * inch,
        title=title,
        author="Professor AI Workspace",
        subject="AI-assisted faculty draft",
    )
    document.build(
        story,
        onFirstPage=lambda canvas, doc: _pdf_page(canvas, doc, agent_type),
        onLaterPages=lambda canvas, doc: _pdf_page(canvas, doc, agent_type),
    )
    return path


def _render_teaching_pdf(story: list, result: dict, styles) -> None:
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    story.append(_pdf_paragraph("Session at a Glance", styles["h1"]))
    _pdf_table(
        story,
        ["Course", "Audience", "Duration", "Level"],
        [[overview.get("course"), overview.get("audience"),
          f"{overview.get('duration_minutes', '')} minutes", overview.get("difficulty")]],
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
            text = item.get("objective") if isinstance(item, dict) else item
            story.append(Paragraph(f"{index}. {escape(_plain_text(text))}", styles["bullet"]))

    sections = _as_list(result.get("lecture_sections"))
    if sections:
        story.append(_pdf_paragraph("Session Plan", styles["h1"]))
        rows = []
        for section in sections:
            if isinstance(section, dict):
                content = section.get("content", {})
                focus = content.get("key_points") if isinstance(content, dict) else content
                rows.append([
                    f"{section.get('minutes', '')} min",
                    section.get("title"),
                    _plain_text(focus)[:250],
                ])
        _pdf_table(story, ["Time", "Section", "Teaching Focus"], rows, [0.75, 2.3, 3.65], styles)
        story.extend([PageBreak(), _pdf_paragraph("Detailed Lecture Notes", styles["h1"])])
        for section in sections:
            if not isinstance(section, dict):
                continue
            story.append(_pdf_paragraph(
                _plain_text(section.get("title"))
                + (f"  |  {section.get('minutes')} minutes" if section.get("minutes") else ""),
                styles["h2"],
            ))
            _render_teaching_section_pdf(story, section.get("content", {}), styles)

    _render_teaching_assessment_pdf(story, result, styles)
    case_study = result.get("case_study")
    if isinstance(case_study, dict) and case_study:
        story.extend([PageBreak(), _pdf_paragraph("Case Study", styles["h1"])])
        story.append(_pdf_paragraph(case_study.get("title"), styles["h2"]))
        _pdf_callout(story, "Scenario", case_study.get("scenario"), styles, "EEF3FA")
        if case_study.get("questions"):
            story.append(_pdf_paragraph("Case Questions", styles["h3"]))
            for index, question in enumerate(_as_list(case_study.get("questions")), start=1):
                story.append(Paragraph(f"{index}. {escape(_plain_text(question))}", styles["bullet"]))
        _pdf_callout(story, "Teaching notes", case_study.get("teaching_notes"), styles, "F3F0FB")
    _render_sources_pdf(story, result, styles)


def _render_teaching_section_pdf(story: list, content: Any, styles) -> None:
    if not isinstance(content, dict):
        _pdf_bullets(story, content, styles)
        return
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
        story.append(_pdf_paragraph(
            "Source references: " + "; ".join(map(_plain_text, _as_list(content.get("source_refs")))),
            styles["small"],
        ))


def _render_teaching_assessment_pdf(story: list, result: dict, styles) -> None:
    if any(result.get(key) for key in (
        "mcqs", "assignments", "numerical_problems", "bloom_mapping",
        "discussion_questions", "viva_questions"
    )):
        story.extend([PageBreak(), _pdf_paragraph("Assessment and Engagement Toolkit", styles["h1"])])
    mcqs = _as_list(result.get("mcqs"))
    if mcqs:
        story.append(_pdf_paragraph("Multiple-Choice Questions", styles["h2"]))
        for index, item in enumerate(mcqs, start=1):
            if not isinstance(item, dict):
                continue
            story.append(_pdf_paragraph(f"{index}. {_plain_text(item.get('question'))}", styles["h3"]))
            for option_index, option in enumerate(_as_list(item.get("options"))):
                story.append(Paragraph(
                    f"{chr(65 + option_index)}. {escape(_plain_text(option))}", styles["bullet"]
                ))
            _pdf_callout(
                story,
                "Answer and rationale",
                f"{_plain_text(item.get('answer'))}\n{_plain_text(item.get('explanation'))}",
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
                _pdf_callout(story, "Solution", item.get("solution"), styles, "EAF6F0")
    assignments = _as_list(result.get("assignments"))
    if assignments:
        story.append(_pdf_paragraph("Assignments", styles["h2"]))
        for item in assignments:
            if isinstance(item, dict):
                story.append(_pdf_paragraph(item.get("title"), styles["h3"]))
                _pdf_callout(story, "Brief", item.get("prompt"), styles, "EEF3FA")
                _pdf_bullets(story, item.get("rubric"), styles)
    bloom = _as_list(result.get("bloom_mapping"))
    if bloom:
        rows = [[item.get("objective"), item.get("level")] for item in bloom if isinstance(item, dict)]
        story.append(_pdf_paragraph("Bloom's Taxonomy Mapping", styles["h2"]))
        _pdf_table(story, ["Learning Objective", "Bloom Level"], rows, [5.1, 1.6], styles)
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
                rows.append([item.get("question"), item.get("expected_answer")])
            else:
                rows.append([item, ""])
        story.append(_pdf_paragraph("Viva Questions and Expected Answers", styles["h2"]))
        _pdf_table(story, ["Question", "Expected Answer / Talking Points"], rows, [3.0, 3.7], styles)


def _render_research_pdf(story: list, result: dict, styles) -> None:
    story.append(_pdf_paragraph("Executive Summary", styles["h1"]))
    summary = result.get("executive_summary")
    if isinstance(summary, dict):
        _pdf_callout(story, "Evidence", summary.get("evidence"), styles, "EEF3FA")
        _pdf_callout(story, "Interpretation", summary.get("inference"), styles, "F3F0FB")
    else:
        story.append(_pdf_paragraph(summary, styles["body"]))
    themes = _as_list(result.get("themes"))
    if themes:
        story.append(_pdf_paragraph("Thematic Literature Synthesis", styles["h1"]))
        for index, item in enumerate(themes, start=1):
            if not isinstance(item, dict):
                continue
            block = [_pdf_paragraph(f"{index}. {_plain_text(item.get('theme'))}", styles["h2"])]
            synthesis = item.get("synthesis")
            if isinstance(synthesis, dict):
                block.append(_pdf_paragraph(
                    f"Evidence: {_plain_text(synthesis.get('evidence'))}", styles["body"]
                ))
                block.append(_pdf_paragraph(
                    f"Interpretation: {_plain_text(synthesis.get('inference'))}",
                    styles["body"],
                ))
            if item.get("papers"):
                block.append(_pdf_paragraph(
                    "Studies represented: " + "; ".join(map(_plain_text, _as_list(item.get("papers")))),
                    styles["small"],
                ))
            story.append(KeepTogether(block))
    methods = _as_list(result.get("methodology_comparison"))
    if methods:
        story.extend([PageBreak(), _pdf_paragraph("Methodology Comparison", styles["h1"])])
        for index, item in enumerate(methods, start=1):
            if not isinstance(item, dict):
                continue
            story.append(_pdf_paragraph(f"{index}. {_plain_text(item.get('method'))}", styles["h2"]))
            story.append(_pdf_paragraph(
                f"Strengths: {_plain_text(item.get('strengths'))}", styles["body"]
            ))
            story.append(_pdf_paragraph(
                f"Limitations: {_plain_text(item.get('limitations'))}",
                styles["body"],
            ))
            if item.get("papers"):
                story.append(_pdf_paragraph(
                    "Representative studies: "
                    + "; ".join(map(_plain_text, _as_list(item.get("papers")))),
                    styles["small"],
                ))
    gaps = _as_list(result.get("research_gaps"))
    if gaps:
        story.append(_pdf_paragraph("Research Gaps", styles["h1"]))
        for index, item in enumerate(gaps, start=1):
            if not isinstance(item, dict):
                continue
            story.append(_pdf_paragraph(f"{index}. {_plain_text(item.get('gap'))}", styles["h2"]))
            _pdf_callout(story, "Evidence for the gap", item.get("evidence"), styles, "EEF3FA")
            _pdf_callout(story, "Research implication", item.get("inference"), styles, "F3F0FB")
    questions = _as_list(result.get("research_questions"))
    if questions:
        story.append(_pdf_paragraph("Proposed Research Questions", styles["h1"]))
        for index, question in enumerate(questions, start=1):
            story.append(Paragraph(f"{index}. {escape(_plain_text(question))}", styles["bullet"]))
    future = _as_list(result.get("future_scope"))
    if future:
        rows = [[item.get("area"), item.get("scope")] for item in future if isinstance(item, dict)]
        story.append(_pdf_paragraph("Future Scope", styles["h1"]))
        _pdf_table(story, ["Research Area", "Opportunity"], rows, [2.0, 4.7], styles)
    suggestions = _as_list(result.get("methodology_suggestions"))
    if suggestions:
        story.append(_pdf_paragraph("Methodology Recommendations", styles["h1"]))
        for index, item in enumerate(suggestions, start=1):
            if isinstance(item, dict):
                story.append(_pdf_paragraph(
                    f"{index}. {_plain_text(item.get('suggestion'))}", styles["h2"]
                ))
                story.append(_pdf_paragraph(
                    f"Rationale: {_plain_text(item.get('rationale'))}",
                    styles["body"],
                ))
    references = _as_list(result.get("apa_references"))
    if references:
        story.extend([PageBreak(), _pdf_paragraph("APA References", styles["h1"])])
        for reference in references:
            story.append(_pdf_paragraph(reference, styles["body"]))
    _render_sources_pdf(story, result, styles)


def _render_sources_pdf(story: list, result: dict, styles) -> None:
    sources = _as_list(result.get("sources"))
    evidence = result.get("overview", {}).get("evidence_base", []) if isinstance(
        result.get("overview"), dict
    ) else []
    if not sources and not evidence:
        return
    story.extend([PageBreak(), _pdf_paragraph("Sources and Further Reading", styles["h1"])])
    for index, item in enumerate(sources, start=1):
        if isinstance(item, dict):
            title = _plain_text(item.get("title"), "Source")
            year = _plain_text(item.get("year"))
            url = _plain_text(item.get("url"))
            story.append(Paragraph(f"{index}. {escape(title)}"
                                   + (f" ({escape(year)})" if year else "")
                                   + (f"<br/><font color='#{MUTED}'>{escape(url)}</font>" if url else ""),
                                   styles["body"]))
        else:
            story.append(Paragraph(f"{index}. {escape(_plain_text(item))}", styles["body"]))
    if evidence:
        story.append(_pdf_paragraph("Evidence Base Named in the Teaching Plan", styles["h2"]))
        _pdf_bullets(story, evidence, styles)


# ---------------------------------------------------------------------------
# PPTX - professor-ready lecture/research presentation
# ---------------------------------------------------------------------------


def _pptx_set_text(shape, text: Any, *, size: int, color: str, bold: bool = False,
                   align=PP_ALIGN.LEFT):
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.TOP
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = _plain_text(text)
    run.font.name = "Arial"
    run.font.size = PptxPt(size)
    run.font.bold = bold
    run.font.color.rgb = _pptx_color(color)
    return frame


def _pptx_add_text(slide, text: Any, x, y, w, h, *, size=18, color=INK, bold=False,
                   align=PP_ALIGN.LEFT):
    shape = slide.shapes.add_textbox(PptxInches(x), PptxInches(y), PptxInches(w), PptxInches(h))
    _pptx_set_text(shape, text, size=size, color=color, bold=bold, align=align)
    return shape


def _pptx_add_header(slide, title: str, section: str, number: int, accent: str):
    _pptx_add_text(slide, section.upper(), 0.65, 0.38, 4.6, 0.28, size=10, color=accent, bold=True)
    _pptx_add_text(slide, title, 0.65, 0.72, 11.7, 0.72, size=35, color=NAVY, bold=True)
    _pptx_add_text(slide, f"{number:02d}", 12.15, 0.4, 0.5, 0.25, size=9, color=MUTED,
                   align=PP_ALIGN.RIGHT)


def _pptx_add_footer(slide, text: str):
    _pptx_add_text(slide, text, 0.65, 7.16, 12.0, 0.18, size=8, color=MUTED)


def _pptx_add_bullets(slide, values: Any, x, y, w, h, *, size=19, color=INK, max_items=7):
    shape = slide.shapes.add_textbox(PptxInches(x), PptxInches(y), PptxInches(w), PptxInches(h))
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = PptxInches(0.08)
    frame.margin_right = PptxInches(0.04)
    for index, value in enumerate(_as_list(values)[:max_items]):
        p = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        p.text = _plain_text(value)
        p.level = 0
        p.font.name = "Arial"
        p.font.size = PptxPt(size)
        p.font.color.rgb = _pptx_color(color)
        p.space_after = PptxPt(8)
        p.line_spacing = 1.08
    return shape


def _pptx_add_callout(slide, label: str, text: Any, x, y, w, h, accent: str):
    shape = slide.shapes.add_textbox(PptxInches(x), PptxInches(y), PptxInches(w), PptxInches(h))
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = PptxInches(0.16)
    frame.margin_right = PptxInches(0.14)
    frame.margin_top = PptxInches(0.11)
    frame.margin_bottom = PptxInches(0.08)
    p = frame.paragraphs[0]
    r = p.add_run()
    r.text = label.upper()
    r.font.name = "Arial"
    r.font.size = PptxPt(10)
    r.font.bold = True
    r.font.color.rgb = _pptx_color(accent)
    p.space_after = PptxPt(5)
    p2 = frame.add_paragraph()
    p2.text = _plain_text(text)
    p2.font.name = "Arial"
    p2.font.size = PptxPt(16)
    p2.font.color.rgb = _pptx_color(INK)
    p2.line_spacing = 1.05
    shape.fill.solid()
    shape.fill.fore_color.rgb = _pptx_color(LIGHT)
    shape.line.color.rgb = _pptx_color(LINE)
    return shape


def _create_pptx(result: dict, path: Path, agent_type: str) -> Path:
    deck = Presentation()
    deck.slide_width = PptxInches(13.333)
    deck.slide_height = PptxInches(7.5)
    accent = CYAN if agent_type == "teaching" else PURPLE
    _pptx_title_slide(deck, result, agent_type, accent)
    if agent_type == "teaching":
        _render_teaching_pptx(deck, result, accent)
    else:
        _render_research_pptx(deck, result, accent)
    deck.core_properties.title = _plain_text(result.get("title"), "Professor AI Output")
    deck.core_properties.subject = "AI-assisted faculty presentation"
    deck.core_properties.author = "Professor AI Workspace"
    deck.save(path)
    return path


def _pptx_title_slide(deck: Presentation, result: dict, agent_type: str, accent: str):
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _pptx_color(NAVY)
    _pptx_add_text(
        slide,
        "PROFESSOR AI  /  "
        + ("TEACHING & CURRICULUM" if agent_type == "teaching" else "RESEARCH SYNTHESIS"),
        0.8, 0.65, 10.8, 0.32, size=12, color=accent, bold=True,
    )
    title = _plain_text(result.get("title"), "Professor AI Output")
    title_size = 42 if len(title) > 80 else 50
    _pptx_add_text(
        slide,
        title,
        0.8, 1.25, 11.5, 3.25, size=title_size, color=WHITE, bold=True,
    )
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    subtitle = (
        f"{overview.get('course', 'Faculty teaching package')}  |  "
        f"{overview.get('duration_minutes', '')} minutes  |  "
        f"{overview.get('difficulty', '')}"
        if agent_type == "teaching"
        else "Literature review, research gaps, questions, and methodology direction"
    )
    _pptx_add_text(slide, subtitle, 0.84, 5.05, 10.9, 0.58, size=20, color="C0CDDD")
    _pptx_add_text(
        slide,
        "AI-assisted faculty draft. Verify facts, citations, calculations, and academic suitability.",
        0.84, 6.72, 11.7, 0.3, size=10, color="8FA4BF",
    )


def _render_teaching_pptx(deck: Presentation, result: dict, accent: str):
    number = 2
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _pptx_add_header(slide, "Session at a Glance", "Teaching Package", number, accent)
    number += 1
    _pptx_add_callout(slide, "Lesson purpose", overview.get("lesson_purpose"), 0.7, 1.65, 7.5, 2.0, accent)
    _pptx_add_callout(
        slide,
        "Session design",
        f"Course: {overview.get('course', 'General')}\n"
        f"Audience: {overview.get('audience', 'University students')}\n"
        f"Duration: {overview.get('duration_minutes', '')} minutes\n"
        f"Level: {overview.get('difficulty', 'Intermediate')}",
        8.45, 1.65, 4.2, 2.0, accent,
    )
    _pptx_add_text(slide, "Prerequisite knowledge", 0.75, 4.05, 4.0, 0.35, size=24, color=NAVY, bold=True)
    _pptx_add_bullets(slide, overview.get("prerequisite_knowledge"), 0.75, 4.5, 5.7, 2.05, size=17)
    _pptx_add_text(slide, "Evidence base", 6.9, 4.05, 3.5, 0.35, size=24, color=NAVY, bold=True)
    _pptx_add_bullets(slide, overview.get("evidence_base"), 6.9, 4.5, 5.6, 2.05, size=16)
    _pptx_add_footer(slide, "Professor review edition")

    objectives = _as_list(result.get("learning_objectives"))
    if objectives:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Learning Objectives", "Teaching Package", number, accent)
        number += 1
        values = []
        for item in objectives:
            if isinstance(item, dict):
                prefix = f"{item.get('id')}  " if item.get("id") else ""
                values.append(prefix + _plain_text(item.get("objective")))
            else:
                values.append(item)
        _pptx_add_bullets(slide, values, 0.85, 1.65, 11.7, 4.9, size=19, max_items=8)
        _pptx_add_footer(slide, "Learning outcomes should be aligned to assessment and course policy.")

    sections = _as_list(result.get("lecture_sections"))
    if sections:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Session Roadmap", "Teaching Package", number, accent)
        number += 1
        y = 1.65
        for section in sections[:7]:
            if not isinstance(section, dict):
                continue
            _pptx_add_text(slide, f"{section.get('minutes', '')} min", 0.8, y, 1.05, 0.34,
                           size=16, color=accent, bold=True)
            _pptx_add_text(slide, section.get("title"), 1.9, y, 10.5, 0.44,
                           size=19, color=NAVY, bold=True)
            y += 0.72
        _pptx_add_footer(slide, "A structured 60-minute teaching flow")

        for section in sections:
            if not isinstance(section, dict):
                continue
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            title = _plain_text(section.get("title"), "Lecture Section")
            _pptx_add_header(slide, title, f"{section.get('minutes', '')} minutes", number, accent)
            number += 1
            content = section.get("content", {})
            if isinstance(content, dict):
                key_points = _as_list(content.get("key_points"))
                _pptx_add_text(slide, "Key teaching points", 0.75, 1.9, 5.8, 0.36,
                               size=24, color=NAVY, bold=True)
                _pptx_add_bullets(slide, key_points, 0.75, 2.33, 6.0, 4.05, size=17, max_items=7)
                activity = (
                    content.get("classroom_activity")
                    or content.get("worked_example")
                    or content.get("worked_examples")
                    or content.get("mini_discussion")
                    or content.get("concept_check")
                )
                _pptx_add_callout(slide, "In-class application", activity, 7.05, 1.92, 5.55, 2.15, accent)
                teaching_note = (
                    content.get("teaching_tip")
                    or content.get("common_errors")
                    or content.get("closing_prompt")
                    or content.get("expected_answer")
                )
                _pptx_add_callout(slide, "Professor note", teaching_note, 7.05, 4.35, 5.55, 1.85, accent)
                refs = _as_list(content.get("source_refs"))
                if refs:
                    _pptx_add_text(slide, "Sources: " + "; ".join(map(_plain_text, refs)),
                                   0.75, 6.55, 11.7, 0.25, size=9, color=MUTED)
            else:
                _pptx_add_bullets(slide, content, 0.85, 1.7, 11.5, 4.8, size=20)

    formulas = _as_list(overview.get("core_formulas"))
    if formulas:
        for start in range(0, len(formulas), 4):
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            _pptx_add_header(slide, "Core Formula Sheet", "Reference", number, accent)
            number += 1
            _pptx_add_bullets(slide, formulas[start:start + 4], 0.95, 1.7, 11.2, 4.9, size=22, max_items=4)
            _pptx_add_footer(slide, "Ensure rate and period units are consistent.")

    case_study = result.get("case_study")
    if isinstance(case_study, dict) and case_study:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, _plain_text(case_study.get("title"), "Applied Case"),
                         "Case Study", number, accent)
        number += 1
        _pptx_add_callout(slide, "Scenario", case_study.get("scenario"), 0.75, 1.62, 7.2, 3.5, accent)
        _pptx_add_text(slide, "Discussion questions", 8.25, 1.72, 4.1, 0.4,
                       size=23, color=NAVY, bold=True)
        _pptx_add_bullets(slide, case_study.get("questions"), 8.25, 2.2, 4.15, 3.5, size=17, max_items=5)
        _pptx_add_footer(slide, "Use teaching notes in the professor document for facilitation guidance.")

    discussion = _as_list(result.get("discussion_questions"))
    if discussion:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Discussion Questions", "Student Engagement", number, accent)
        number += 1
        _pptx_add_bullets(slide, discussion, 0.9, 1.65, 11.5, 4.9, size=21, max_items=7)

    mcqs = _as_list(result.get("mcqs"))
    for start in range(0, len(mcqs), 3):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Knowledge Check", "Assessment", number, accent)
        number += 1
        y = 1.55
        for q_index, item in enumerate(mcqs[start:start + 3], start=start + 1):
            if not isinstance(item, dict):
                continue
            _pptx_add_text(slide, f"{q_index}. {_plain_text(item.get('question'))}",
                           0.75, y, 11.8, 0.6, size=18, color=NAVY, bold=True)
            options = [f"{chr(65+i)}. {_plain_text(v)}" for i, v in enumerate(_as_list(item.get("options")))]
            _pptx_add_text(slide, "   ".join(options), 0.95, y + 0.58, 11.4, 0.68, size=14, color=INK)
            y += 1.65
        _pptx_add_footer(slide, "Answers and rationales are available in the professor document.")

    assignments = _as_list(result.get("assignments"))
    if assignments:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Assignment Brief", "Assessment", number, accent)
        number += 1
        item = assignments[0]
        if isinstance(item, dict):
            _pptx_add_text(slide, item.get("title"), 0.78, 1.62, 11.5, 0.5,
                           size=27, color=NAVY, bold=True)
            _pptx_add_callout(slide, "Task", item.get("prompt"), 0.78, 2.25, 7.5, 2.9, accent)
            _pptx_add_text(slide, "Assessment criteria", 8.55, 2.25, 3.8, 0.4,
                           size=22, color=NAVY, bold=True)
            _pptx_add_bullets(slide, item.get("rubric"), 8.55, 2.75, 3.8, 2.7, size=17, max_items=6)

    _pptx_sources_slide(deck, result, number, accent)


def _render_research_pptx(deck: Presentation, result: dict, accent: str):
    number = 2
    summary = result.get("executive_summary")
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _pptx_add_header(slide, "Executive Summary", "Research Synthesis", number, accent)
    number += 1
    if isinstance(summary, dict):
        _pptx_add_callout(slide, "Evidence", summary.get("evidence"), 0.72, 1.62, 7.6, 4.9, accent)
        _pptx_add_callout(slide, "Interpretation", summary.get("inference"), 8.55, 1.62, 4.05, 4.9, accent)
    else:
        _pptx_add_bullets(slide, summary, 0.85, 1.7, 11.5, 4.8, size=20)

    themes = _as_list(result.get("themes"))
    for item in themes:
        if not isinstance(item, dict):
            continue
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, _plain_text(item.get("theme"), "Research Theme"),
                         "Thematic Synthesis", number, accent)
        number += 1
        synthesis = item.get("synthesis")
        if isinstance(synthesis, dict):
            _pptx_add_callout(slide, "Evidence", synthesis.get("evidence"), 0.72, 1.62, 7.5, 4.45, accent)
            _pptx_add_callout(slide, "Interpretation", synthesis.get("inference"),
                              8.5, 1.62, 4.1, 4.45, accent)
        else:
            _pptx_add_bullets(slide, synthesis, 0.85, 1.7, 11.5, 4.8, size=20)
        papers = _as_list(item.get("papers"))
        if papers:
            _pptx_add_text(slide, "Studies: " + "; ".join(map(_plain_text, papers)),
                           0.75, 6.5, 11.7, 0.27, size=10, color=MUTED)

    gaps = _as_list(result.get("research_gaps"))
    for start in range(0, len(gaps), 2):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Research Gaps", "Research Direction", number, accent)
        number += 1
        x_positions = [0.72, 6.82]
        for offset, item in enumerate(gaps[start:start + 2]):
            if not isinstance(item, dict):
                continue
            x = x_positions[offset]
            _pptx_add_text(slide, item.get("gap"), x, 1.62, 5.75, 0.85,
                           size=23, color=NAVY, bold=True)
            _pptx_add_callout(slide, "Evidence", item.get("evidence"), x, 2.58, 5.75, 1.75, accent)
            _pptx_add_callout(slide, "Research implication", item.get("inference"),
                              x, 4.52, 5.75, 1.55, accent)

    questions = _as_list(result.get("research_questions"))
    if questions:
        for start in range(0, len(questions), 6):
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            _pptx_add_header(slide, "Proposed Research Questions", "Research Direction", number, accent)
            number += 1
            _pptx_add_bullets(slide, questions[start:start + 6], 0.85, 1.65, 11.5, 4.95,
                              size=19, max_items=6)

    methods = _as_list(result.get("methodology_suggestions"))
    if methods:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Methodology Recommendations", "Research Design", number, accent)
        number += 1
        y = 1.55
        for index, item in enumerate(methods[:4], start=1):
            if isinstance(item, dict):
                _pptx_add_text(slide, f"{index}. {_plain_text(item.get('suggestion'))}",
                               0.75, y, 5.4, 0.72, size=19, color=NAVY, bold=True)
                _pptx_add_text(slide, item.get("rationale"), 6.35, y, 6.0, 0.92,
                               size=15, color=INK)
                y += 1.25

    future = _as_list(result.get("future_scope"))
    if future:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Future Research Scope", "Next Horizon", number, accent)
        number += 1
        y = 1.55
        for item in future[:5]:
            if isinstance(item, dict):
                _pptx_add_text(slide, item.get("area"), 0.75, y, 3.3, 0.55,
                               size=19, color=accent, bold=True)
                _pptx_add_text(slide, item.get("scope"), 4.25, y, 8.0, 0.75,
                               size=16, color=INK)
                y += 1.0

    _pptx_sources_slide(deck, result, number, accent)


def _pptx_sources_slide(deck: Presentation, result: dict, number: int, accent: str):
    sources = _as_list(result.get("sources"))
    references = _as_list(result.get("apa_references"))
    values = references or [
        (
            _plain_text(item.get("title"))
            + (f" ({_plain_text(item.get('year'))})" if item.get("year") else "")
            + (f" - {_plain_text(item.get('url'))}" if item.get("url") else "")
        )
        if isinstance(item, dict)
        else _plain_text(item)
        for item in sources
    ]
    if not values:
        return
    for start in range(0, len(values), 7):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        _pptx_add_header(slide, "Sources and Further Reading", "References", number, accent)
        number += 1
        _pptx_add_bullets(slide, values[start:start + 7], 0.82, 1.55, 11.75, 5.25,
                          size=15, max_items=7)
        _pptx_add_footer(slide, "Verify citation details before academic publication or distribution.")
