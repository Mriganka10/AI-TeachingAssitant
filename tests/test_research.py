"""Research assistant: manuscript parsing, review contract, revision checks and exports."""

import io
import re
import zipfile
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.agents.contracts import validate_output
from app.agents.llm import LLMService
from app.agents.manuscript import (
    Manuscript,
    location_label,
    parse_manuscript,
    revision_issues,
)
from app.agents.plain_language import polish_academic, research_style_issues
from app.agents.revision import build_revision
from app.artifacts.generator import create_artifacts
from app.artifacts.research_docs import align_paragraphs
from app.core.documents import extract_text
from app.main import app

TITLE = "Effects of Spaced Retrieval Practice on Undergraduate Statistics Performance"
SECTIONS = [
    ("Abstract", [
        (
            "Retrieval practice is widely recommended, yet its effect in large undergraduate "
            "statistics courses remains unclear. We studied 214 students across two semesters. "
            "These results show that spaced retrieval practice very clearly causes better learning."
        )
    ]),
    ("1. Introduction", [
        (
            "Students in introductory statistics often struggle to retain procedures. Prior work "
            "shows that retrieving information strengthens later recall (Roediger & Karpicke, 2006). "
            "Spacing practice over time adds a further benefit [3]."
        ),
        (
            "However, most studies were conducted in laboratory settings with short retention "
            "intervals (Dunlosky et al., 2013)."
        ),
    ]),
    ("2. Methods", [
        (
            "Participants were 214 undergraduates in two sections. The spaced group completed 12 "
            "weekly quizzes, each containing 10 questions drawn from earlier weeks."
        ),
    ]),
    ("3. Results", [
        (
            "The spaced group (n = 108) obtained a mean score of 71.2 (SD = 12.5), while the massed "
            "group (n = 106) obtained 62.8. The difference was significant, p = 0.003."
        ),
    ]),
    ("References", [
        (
            "Dunlosky, J., Rawson, K. A., & Marsh, E. J. (2013). Improving students' learning. "
            "Psychological Science in the Public Interest, 14(1), 4-58."
        ),
        (
            "Roediger, H. L., & Karpicke, J. D. (2006). Test-enhanced learning. Psychological "
            "Science, 17(3), 249-255."
        ),
    ]),
]


def _docx_paper(path: Path) -> Path:
    doc = Document()
    doc.add_heading(TITLE, 0)
    doc.add_paragraph("A. Sen and R. Das, Department of Statistics")
    for heading, paragraphs in SECTIONS:
        doc.add_heading(heading, 1)
        for text in paragraphs:
            doc.add_paragraph(text)
    doc.save(path)
    return path


def _pdf_paper(path: Path) -> Path:
    styles = getSampleStyleSheet()

    def page(canvas, document):
        canvas.setFont("Helvetica", 8)
        canvas.drawString(72, 810, "Journal of Teaching Statistics")
        canvas.drawString(290, 30, str(document.page))

    story = [Paragraph(TITLE, styles["Title"]), Paragraph("A. Sen and R. Das", styles["Normal"]),
             Spacer(1, 12)]
    for heading, paragraphs in SECTIONS:
        story.append(Paragraph(heading, styles["Heading2"]))
        story += [Paragraph(text.replace("&", "&amp;"), styles["BodyText"]) for text in paragraphs]
    SimpleDocTemplate(str(path), pagesize=A4).build(story, onFirstPage=page, onLaterPages=page)
    return path


@pytest.fixture
def paper(tmp_path: Path) -> Manuscript:
    return parse_manuscript(extract_text(_docx_paper(tmp_path / "paper.docx")))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("maker", [_docx_paper, _pdf_paper])
def test_manuscript_structure_is_recovered(tmp_path: Path, maker) -> None:
    suffix = ".docx" if maker is _docx_paper else ".pdf"
    manuscript = parse_manuscript(extract_text(maker(tmp_path / f"paper{suffix}")))
    assert manuscript.title == TITLE
    kinds = [(section.heading, section.kind) for section in manuscript.sections]
    assert ("Abstract", "abstract") in kinds
    assert ("References", "references") in kinds
    assert ("2. Methods", "body") in kinds
    references = next(s for s in manuscript.sections if s.kind == "references")
    assert len(references.paragraphs) == 2
    assert not any("Journal of Teaching Statistics" in p for s in manuscript.sections for p in s.paragraphs)
    front = manuscript.sections[0]
    assert front.kind == "front" and front.id == "S0"


def test_locations_read_naturally(paper: Manuscript) -> None:
    introduction = next(s for s in paper.sections if s.heading == "1. Introduction")
    assert location_label(paper, f"{introduction.id}.P2") == "1. Introduction, para. 2"
    assert location_label(paper, "Whole manuscript") == "Whole manuscript"
    payload = paper.to_payload()
    assert Manuscript.from_payload(payload).to_payload() == payload


def test_unstructured_text_is_split_into_parts() -> None:
    text = "\n\n".join(f"Paragraph {i} " + "word " * 200 + "end." for i in range(12))
    manuscript = parse_manuscript("A study of things\n\n" + text)
    body = [s for s in manuscript.sections if s.kind == "body"]
    assert len(body) >= 2
    assert all(len(" ".join(s.paragraphs).split()) <= 1300 for s in body)


# ---------------------------------------------------------------------------
# Review contract
# ---------------------------------------------------------------------------


def test_mock_review_passes_the_contract(paper: Manuscript) -> None:
    request = {"manuscript": paper.to_payload()}
    review = LLMService._mock_research(request)
    assert validate_output("research", review, request) == []


def test_review_rejects_unknown_locations_and_inconsistent_decisions(paper: Manuscript) -> None:
    request = {"manuscript": paper.to_payload()}
    review = LLMService._mock_research(request)
    review["major_concerns"][0]["location"] = "S99.P4"
    review["recommendation"]["decision"] = "accept"
    issues = " ".join(validate_output("research", review, request))
    assert "S99.P4" in issues
    assert "'accept'" in issues


def test_reviewer_voice_checks() -> None:
    review = {"summary": "This paper delves into a rich tapestry of ideas.",
              "strengths": [{"evidence": "We delve into the data", "point": "Clear", "location": "S1"}]}
    issues = " ".join(research_style_issues(review))
    assert "delve" in issues and "tapestry" in issues
    # Quotes from the manuscript (evidence) are not held against the reviewer.
    assert research_style_issues({"strengths": [{"evidence": "We delve into"}]}) == []
    assert "Moreover," in polish_academic("Moreover, the data — as shown — hold.")
    assert "—" not in polish_academic("Moreover, the data — as shown — hold.")


# ---------------------------------------------------------------------------
# Revision integrity
# ---------------------------------------------------------------------------


ORIGINAL = [(
             "Prior work shows recall improves (Roediger & Karpicke, 2006). Spacing helps [3]. "
             "We had 214 students and p = 0.003."
            )]


def test_faithful_revision_passes() -> None:
    revised = [(
                "Earlier studies indicate that retrieval improves recall (Roediger & Karpicke, "
                "2006). Spacing adds a further benefit [3]. The sample included 214 students "
                "(p = 0.003)."
               )]
    assert revision_issues(ORIGINAL, revised) == []


def test_lost_or_invented_citations_and_numbers_are_caught() -> None:
    issues = " ".join(revision_issues(
        ORIGINAL, [(
                    "Earlier work shows recall improves. Spacing helps [3][4]. We had 214 students, "
                    "95% passed, p = 0.003."
                   )]))
    assert "(Roediger & Karpicke, 2006)" in issues
    assert "[4]" in issues
    assert "95%" in issues


def test_author_placeholders_are_allowed() -> None:
    revised = [ORIGINAL[0].replace("students", "students [Author: add the 2019 cohort size]")]
    assert revision_issues(ORIGINAL, revised) == []


def test_failed_sections_keep_the_original_text(paper: Manuscript, monkeypatch) -> None:
    from app.agents import revision as revision_module
    from app.core.config import settings

    monkeypatch.setattr(settings, "llm_service_mode", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test")
    monkeypatch.setattr(revision_module, "create_openai_client", lambda **_: object())
    calls = []

    def fake_structured(client, *, name, payload, **_):
        calls.append((name, payload))
        if name == "revision_plan":
            raise RuntimeError("plan unavailable")  # falls back to revising every section
        return {"paragraphs": ["Completely new text with 99 invented results [42]."],
                "tables": [], "changes": [], "author_notes": []}

    monkeypatch.setattr(revision_module, "_structured", fake_structured)
    review = LLMService._mock_research({"manuscript": paper.to_payload()})
    revision = build_revision(paper, review, {})
    body = [s for s in revision["sections"] if s["kind"] in {"abstract", "body"}]
    assert all(s["status"] == "kept_original" and s["revised"] == [
        p for p in s["original"] if not p.lower().startswith("keywords")] for s in body)
    assert any("problems_to_fix" in payload for name, payload in calls if name == "revised_section")
    references = next(s for s in revision["sections"] if s["kind"] == "references")
    assert references["status"] == "unchanged"


def test_paragraph_alignment_handles_inserted_and_removed_paragraphs() -> None:
    original = ["Alpha beta gamma delta.", "Removed paragraph entirely here.", "Last one stays."]
    revised = ["Alpha beta gamma delta epsilon.", "A brand new paragraph about something.",
               "Last one stays."]
    pairs = align_paragraphs(original, revised)
    assert ("Alpha beta gamma delta.", "Alpha beta gamma delta epsilon.") in pairs
    assert ("Removed paragraph entirely here.", None) in pairs
    assert (None, "A brand new paragraph about something.") in pairs
    assert ("Last one stays.", "Last one stays.") in pairs


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def _review_result(paper: Manuscript) -> dict:
    request = {"author_name": "A. Sen", "target_venue": "Teaching Statistics", "paper_type": "empirical"}
    review = LLMService._mock_research({"manuscript": paper.to_payload(), **request})
    revision = build_revision(paper, review, request)
    return {**review, "request": request, "manuscript": paper.to_payload(), "revision": revision}


def test_research_exports_review_and_revised_paper(paper: Manuscript, tmp_path: Path) -> None:
    paths = create_artifacts(_review_result(paper), agent_type="research", output_dir=tmp_path,
                             author="A. Sen")
    names = sorted(path.name for path in paths)
    assert names == sorted([
        "Effects-of-Spaced-Retrieval-Practice-review.json",
        "Effects-of-Spaced-Retrieval-Practice-review.docx",
        "Effects-of-Spaced-Retrieval-Practice-review.pdf",
        "Effects-of-Spaced-Retrieval-Practice-revised.docx",
        "Effects-of-Spaced-Retrieval-Practice-revised.pdf",
        "Effects-of-Spaced-Retrieval-Practice-revised-tracked-changes.docx",
    ])
    by_suffix = {path.name.split("Practice-")[1]: path for path in paths}

    review_doc = Document(by_suffix["review.docx"])
    review_text = "\n".join(
        [p.text for p in review_doc.paragraphs]
        + [cell.text for table in review_doc.tables for row in table.rows for cell in row.cells]
    )
    assert "Recommendation: Major revision" in review_text
    assert "Major concerns" in review_text and "Suggested revision plan" in review_text
    assert "Before you submit" in review_text and "Suggested declaration" in review_text

    revised = Document(by_suffix["revised.docx"])
    highlighted = [run for p in revised.paragraphs for run in p.runs if run.font.highlight_color]
    assert any("[Author:" in run.text for run in highlighted)
    text = "\n".join(p.text for p in revised.paragraphs)
    assert "indicates" in text  # the mock edit
    assert "Keywords: retrieval practice; spacing; statistics education" in text
    assert "Limitations and Future Work" in text and "Table 1. Limitations" in text
    assert len(revised.tables) == 1 and revised.tables[0].rows[0].cells[0].text == "Limitation"

    pdf_text = "".join(page.extract_text() for page in PdfReader(by_suffix["revised.pdf"]).pages)
    assert TITLE.split()[0] in pdf_text and "References" in pdf_text


def test_tracked_changes_are_real_word_revisions(paper: Manuscript, tmp_path: Path) -> None:
    paths = create_artifacts(_review_result(paper), agent_type="research", output_dir=tmp_path,
                             author="A. Sen")
    tracked = next(path for path in paths if path.name.endswith("tracked-changes.docx"))
    with zipfile.ZipFile(tracked) as archive:
        document = archive.read("word/document.xml").decode()
        settings_xml = archive.read("word/settings.xml").decode()
    assert "<w:ins " in document and "<w:del " in document and "<w:delText" in document
    assert 'w:author="A. Sen"' in document
    ids = re.findall(r'<w:(?:ins|del) w:id="(\d+)"', document)
    assert len(ids) == len(set(ids))
    assert "<w:trackRevisions/>" in settings_xml


def test_exports_carry_no_tool_names_or_ai_labels(paper: Manuscript, tmp_path: Path) -> None:
    paths = create_artifacts(_review_result(paper), agent_type="research", output_dir=tmp_path,
                             author="A. Sen")
    # The manuscript files carry no tool or AI markers. (The private review report may advise the
    # author on journal disclosure policies, so it is checked for metadata only.)
    for path in paths:
        manuscript_file = "-revised" in path.name
        if path.suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                core = archive.read("docProps/core.xml").decode()
                body = archive.read("word/document.xml").decode()
            assert "python-docx" not in core and "A. Sen" in core
            if manuscript_file:
                assert not re.search(r"\bAI\b|generated|language model", body)
        elif path.suffix == ".pdf":
            info = PdfReader(path).metadata
            assert "ReportLab" not in str(info.get("/Producer", "")) + str(info.get("/Creator", ""))
            text = "".join(page.extract_text() for page in PdfReader(path).pages)
            if manuscript_file:
                assert not re.search(r"\bAI\b|generated by|language model", text)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_review_job_end_to_end(tmp_path: Path) -> None:
    from tests.test_api import login

    paper_path = _docx_paper(tmp_path / "paper.docx")
    with TestClient(app) as client:
        login(client)
        uploaded = client.post(
            "/api/documents",
            data={"collection": "manuscripts"},
            files={"file": ("paper.docx", io.BytesIO(paper_path.read_bytes()),
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert uploaded.status_code == 200
        response = client.post("/api/agents/research", json={
            "manuscript_document_id": uploaded.json()["id"],
            "target_venue": "Teaching Statistics",
            "author_name": "A. Sen",
        })
        assert response.status_code == 200
        job = client.get(f"/api/jobs/{response.json()['job_id']}").json()
        assert job["status"] == "completed", job
        assert job["result"]["recommendation"]["decision"] == "major_revision"
        assert job["result"]["revision"]["sections"]
        types = sorted(item["type"] for item in job["artifacts"])
        assert types == ["docx", "docx", "docx", "json", "pdf", "pdf"]
        listed = next(item for item in client.get("/api/jobs").json() if item["id"] == job["job_id"])
        assert listed["agent_type"] == "research" and listed["title"] == TITLE
