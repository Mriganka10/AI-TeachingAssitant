import re
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from pptx import Presentation
from pptx.util import Inches
from pypdf import PdfReader

from app.agents.llm import LLMService
from app.artifacts.generator import create_artifacts


def test_teaching_artifacts_accept_nested_model_values(tmp_path: Path) -> None:
    result = {
        "title": {"text": "Time Value of Money"},
        "overview": {"summary": "An MBA Finance teaching package."},
        "learning_objectives": [{"text": "Calculate present value."}],
        "lecture_sections": [
            {
                "title": {"value": "Foundations"},
                "content": [{"text": "Compounding and discounting"}],
            }
        ],
        "discussion_questions": {"text": "Why does timing affect value?"},
        "viva_questions": [{"value": "Define discount rate."}],
    }

    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)

    assert {path.suffix for path in paths} == {".json", ".docx", ".pdf", ".pptx"}
    assert all(path.exists() and path.stat().st_size > 0 for path in paths)


def test_teaching_deck_is_compact_and_cleans_model_markdown(tmp_path: Path) -> None:
    long_text = (
        "Supervised learning uses labelled examples while unsupervised learning discovers "
        "structure in unlabelled data. " * 18
    ) + "[scikit-learn](https://scikit-learn.org/stable/user_guide?utm_source=openai)"
    result = {
        "title": "Supervised and Unsupervised Learning in Machine Learning",
        "overview": {
            "course": "Artificial Intelligence",
            "audience": "B.Tech students",
            "duration_minutes": 60,
            "difficulty": "intermediate",
            "lesson_purpose": long_text,
            "prerequisite_knowledge": [long_text] * 4,
            "evidence_base": [long_text] * 4,
            "core_formulas": [long_text] * 5,
        },
        "learning_objectives": [
            {"id": f"LO{i}", "objective": long_text, "bloom_level": "Apply"} for i in range(1, 6)
        ],
        "lecture_sections": [
            {
                "title": f"Section {index}: A deliberately descriptive lecture title",
                "minutes": 12,
                "content": {
                    "explanation": long_text,
                    "key_points": [long_text] * 6,
                    "worked_example": {"problem": long_text, "solution": long_text},
                    "teaching_tip": long_text,
                    "source_refs": ["S1"],
                },
            }
            for index in range(1, 6)
        ],
        "mcqs": [
            {
                "question": long_text,
                "options": [long_text] * 4,
                "answer": "A",
                "explanation": long_text,
            }
            for _ in range(5)
        ],
        "discussion_questions": [long_text] * 5,
        "sources": [
            {
                "title": "User Guide",
                "year": "2026",
                "url": "https://scikit-learn.org/stable/user_guide?utm_source=openai",
            }
        ],
    }

    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    presentation = Presentation(next(path for path in paths if path.suffix == ".pptx"))
    slide_text = "\n".join(
        shape.text
        for slide in presentation.slides
        for shape in slide.shapes
        if hasattr(shape, "text")
    )

    # Five long sections and five MCQs: two slides per section and an answer slide per MCQ.
    assert len(presentation.slides) <= 34
    assert "utm_source=openai" not in slide_text
    assert "[scikit-learn](" not in slide_text


def test_pdf_and_pptx_keep_the_requested_subject(tmp_path: Path) -> None:
    for topic in ("Time Value of Money", "Photosynthesis"):
        result = LLMService._mock({"topic": topic, "duration_minutes": 60})
        paths = create_artifacts(result, output_dir=tmp_path / topic.replace(" ", "-"))
        pdf = PdfReader(next(path for path in paths if path.suffix == ".pdf"))
        pdf_text = " ".join(page.extract_text() or "" for page in pdf.pages)
        deck = Presentation(next(path for path in paths if path.suffix == ".pptx"))
        slide_text = " ".join(
            shape.text
            for slide in deck.slides
            for shape in slide.shapes
            if hasattr(shape, "text")
        )

        assert topic.casefold() in pdf_text.casefold()
        assert topic.casefold() in slide_text.casefold()


def test_teacher_reference_details_stay_out_of_student_presentation(tmp_path: Path) -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    result["lecture_sections"][0]["content"]["teaching_tip"] = "PRIVATE FACILITATION TIP"
    result["case_study"]["teaching_notes"] = "PRIVATE CASE GUIDANCE"
    result["quality_notes"]["review_priorities"] = ["PRIVATE REVIEW PRIORITY"]
    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    by_suffix = {path.suffix: path for path in paths}
    docx_text = "\n".join(p.text for p in Document(by_suffix[".docx"]).paragraphs)
    pdf_text = "\n".join(p.extract_text() or "" for p in PdfReader(by_suffix[".pdf"]).pages)
    deck = Presentation(by_suffix[".pptx"])
    slide_text = "\n".join(
        shape.text for slide in deck.slides for shape in slide.shapes if hasattr(shape, "text")
    )
    for teacher_text in (docx_text, pdf_text):
        assert "PRIVATE FACILITATION TIP" in teacher_text
        assert "PRIVATE CASE GUIDANCE" in teacher_text
        assert "PRIVATE REVIEW PRIORITY" in teacher_text
        assert "Teacher's guide" in teacher_text
        assert "keep it for your own use" in teacher_text
    for private_text in (
        "PRIVATE FACILITATION TIP", "PRIVATE CASE GUIDANCE", "PRIVATE REVIEW PRIORITY",
        "Professor Review Checklist", "Bloom's Taxonomy Mapping",
    ):
        assert private_text not in slide_text
    assert "Cell Structure" in slide_text
    question_slide = next(
        slide for slide in deck.slides
        if any("Knowledge Check 1" == shape.text for shape in slide.shapes if hasattr(shape, "text"))
    )
    question_text = " ".join(shape.text for shape in question_slide.shapes if hasattr(shape, "text"))
    assert "Answer and explanation" not in question_text
    assert any(
        "Why this is right" == shape.text
        for slide in deck.slides for shape in slide.shapes if hasattr(shape, "text")
    )


def test_exporters_preserve_math_and_normalize_mcq_labels(tmp_path: Path) -> None:
    result = {
        "title": "Equation and Assessment QA",
        "overview": {
            "course": "Machine Learning",
            "audience": "B.Tech students",
            "duration_minutes": 30,
            "difficulty": "intermediate",
            "lesson_purpose": "Use mathematical notation accurately.",
            "core_formulas": [
                "Prediction: ŷ = f(x)",
                "MSE = (1/n) Σ_i (y_i - ŷ_i)^2",
                "Centroid μ_k ≈ 3.2",
                "Distance = √((a_1 - b_1)^2 + (a_2 - b_2)^2)",
            ],
        },
        "mcqs": [
            {
                "question": "Which statement is correct?",
                "options": ["A. First", "B) Second", "Option C: Third", "D - Fourth"],
                "answer": "A",
                "explanation": "The first option is correct.",
            }
        ],
        "numerical_problems": [
            {
                "problem": "Compute the mean squared error.",
                "given": ["Predictions ŷ = [2, 4]", "True values y = [1, 5]"],
                "solution_steps": [
                    "Errors y - ŷ = [-1, 1].",
                    "MSE = (1/2) Σ_i (y_i - ŷ_i)^2 = 1.",
                    "Distance = √((a_1 - b_1)^2 + (a_2 - b_2)^2).",
                ],
                "final_answer": "MSE = 1.",
            }
        ],
    }

    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    by_suffix = {path.suffix: path for path in paths}

    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(by_suffix[".pdf"]).pages)
    docx_text = "\n".join(paragraph.text for paragraph in Document(by_suffix[".docx"]).paragraphs)
    presentation = Presentation(by_suffix[".pptx"])
    pptx_text = "\n".join(
        shape.text
        for slide in presentation.slides
        for shape in slide.shapes
        if hasattr(shape, "text")
    )

    for exported_text in (pdf_text, docx_text):
        assert "A. First" in exported_text
    for exported_text in (pdf_text, docx_text, pptx_text):
        assert "A. A. First" not in exported_text
        assert "B. B) Second" not in exported_text
        assert "B) Second" not in exported_text
        assert "ŷ" in exported_text
        assert "Σ" in exported_text
        assert "₁" in exported_text
        assert "₂" in exported_text
        assert "■" not in exported_text
        assert "□" not in exported_text

    with ZipFile(by_suffix[".docx"]) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Cambria Math" in document_xml


def test_pptx_callouts_fit_content_without_overlapping_sections(tmp_path: Path) -> None:
    result = {
        "title": "Machine Learning Assessment",
        "overview": {
            "course": "Artificial Intelligence",
            "audience": "B.Tech students",
            "duration_minutes": 30,
            "difficulty": "intermediate",
            "lesson_purpose": "Distinguish learning paradigms and evaluate predictions.",
        },
        "lecture_sections": [
            {
                "title": "Supervised and Unsupervised Learning",
                "minutes": 15,
                "content": {
                    "explanation": "Labels determine whether a model learns a prediction target.",
                    "key_points": [
                        "Supervised learning uses labelled examples.",
                        "Unsupervised learning discovers structure without target labels.",
                    ],
                    "worked_example": {
                        "problem": (
                            "Classify these tasks: predicting an exam score, detecting spam, "
                            "grouping customers without predefined types, and compressing image features."
                        ),
                        "solution": (
                            "The first two tasks are supervised. Customer grouping and feature "
                            "compression are unsupervised."
                        ),
                    },
                    "teaching_tip": (
                        "Draw one table with columns x and y and another with only x. Ask students "
                        "which information the algorithm can use during training."
                    ),
                    "common_errors": [
                        "Do not assume an unlabeled dataset contains a known prediction target."
                    ],
                },
            }
        ],
        "mcqs": [
            {
                "question": "Why does a supervised model need a separate test set?",
                "options": [
                    "Estimate generalization",
                    "Fit parameters",
                    "Create labels",
                    "Scale features",
                ],
                "answer": "A",
                "explanation": (
                    "The test set is not used to fit the model. It estimates performance on data "
                    "that the model did not see during training."
                ),
            }
        ],
        "numerical_problems": [
            {
                "problem": (
                    "A classifier is tested on 12 examples. Compare the true and predicted labels "
                    "and compute classification accuracy."
                ),
                "given": [
                    "Total examples n = 12",
                    "A correct prediction means the predicted label equals the true label",
                    "Accuracy = correct predictions / total predictions",
                ],
                "solution_steps": [
                    "Compare each pair of true and predicted labels.",
                    "Count nine correct predictions.",
                    "Divide 9 by 12 to obtain 0.75.",
                    "Convert the result to 75 percent.",
                ],
                "final_answer": "Accuracy = 0.75 = 75%.",
            }
        ],
    }

    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    presentation = Presentation(next(path for path in paths if path.suffix == ".pptx"))

    in_action = next(
        slide
        for slide in presentation.slides
        if any(
            getattr(shape, "text", "") == "Supervised and Unsupervised Learning: in action"
            for shape in slide.shapes
        )
    )
    example = next(shape for shape in in_action.shapes if getattr(shape, "text", "") == "Example")
    watch_out = next(
        shape for shape in in_action.shapes if getattr(shape, "text", "") == "Watch out"
    )
    # The example card sits above or beside the misconception card, never on top of it.
    assert example.top < watch_out.top or example.left < watch_out.left
    assert not any(
        "Draw one table with columns x and y" in getattr(shape, "text", "")
        for slide in presentation.slides for shape in slide.shapes
    )
    assert not any(
        "…" in getattr(shape, "text", "")
        for slide in presentation.slides for shape in slide.shapes
    )
    # Nothing is placed outside the slide.
    for slide in presentation.slides:
        for shape in slide.shapes:
            assert shape.left >= 0 and shape.top >= 0
            assert shape.left + shape.width <= presentation.slide_width + Inches(0.01)
            assert shape.top + shape.height <= presentation.slide_height + Inches(0.01)

    def slide_titled(title: str):
        return next(
            slide
            for slide in presentation.slides
            if any(getattr(shape, "text", "") == title for shape in slide.shapes)
        )

    # Learners see the problem first; the worked solution is revealed on the next slide.
    problem_slide = slide_titled("Try It: Problem 1")
    problem_texts = [shape.text for shape in problem_slide.shapes if hasattr(shape, "text")]
    assert "Problem" in problem_texts
    assert "Final answer" not in problem_texts
    assert not any("75%" in text for text in problem_texts)

    solution_slide = slide_titled("Problem 1: Worked Solution")
    solution_texts = [shape.text for shape in solution_slide.shapes if hasattr(shape, "text")]
    assert "Final answer" in solution_texts
    assert any("75%" in text for text in solution_texts)


def test_teacher_reference_is_labelled_and_keeps_full_answer_keys(tmp_path: Path) -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    result["viva_questions"][0]["expected_answer"] = "PRIVATE VIVA ANSWER"
    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    by_suffix = {path.suffix: path for path in paths}
    document = Document(by_suffix[".docx"])
    docx_text = "\n".join(p.text for p in document.paragraphs) + "\n".join(
        cell.text for table in document.tables for row in table.rows for cell in row.cells
    )
    pdf_text = "\n".join(p.extract_text() or "" for p in PdfReader(by_suffix[".pdf"]).pages)

    assert "TEACHER'S GUIDE" in document.sections[0].header.paragraphs[0].text
    assert "TEACHER'S GUIDE" in pdf_text
    for teacher_text in (docx_text, pdf_text):
        assert "Answer key and rationale" in teacher_text
        assert "Assesses: Objective LO2" in teacher_text
        assert "PRIVATE VIVA ANSWER" in teacher_text
        assert "Bloom's Taxonomy Mapping" in teacher_text
        assert "20 minutes" in teacher_text  # pacing plan stays with the teacher


def test_student_presentation_omits_teacher_metadata(tmp_path: Path) -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    result["numerical_problems"] = [
        {
            "problem": "A cell is 20 micrometres wide. Express its width in millimetres.",
            "given": ["Width = 20 micrometres", "1 mm = 1000 micrometres"],
            "solution_steps": ["Divide 20 by 1000."],
            "final_answer": "0.02 mm",
        }
    ]
    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    deck = Presentation(next(path for path in paths if path.suffix == ".pptx"))
    slides = [
        [shape.text for shape in slide.shapes if hasattr(shape, "text")] for slide in deck.slides
    ]
    slide_text = "\n".join("\n".join(texts) for texts in slides)

    for teacher_only in (
        "minutes", "Audience:", "University students", "Duration", "LO1", "Bloom",
        "Expected", "Teaching tip", "Professor", "Review sources and important claims",
    ):
        assert teacher_only not in slide_text
    assert "PART 1 OF 3" in slide_text
    assert "Our path through the topic" in slide_text
    assert deck.core_properties.subject == ""  # "General" course is left blank

    problem_slide = next(texts for texts in slides if "Try It: Problem 1" in texts)
    assert not any("0.02 mm" in text for text in problem_slide)
    solution_slide = next(texts for texts in slides if "Problem 1: Worked Solution" in texts)
    assert any("0.02 mm" in text for text in solution_slide)
    assert all(not slide.has_notes_slide for slide in deck.slides)


_TOOL_MARKERS = re.compile(
    r"\bAI\b|AI-assisted|AI-generated|Professor AI|python-pptx|python-docx|Steve Canny|"
    r"Unverified Claims|Professor Review",
    re.IGNORECASE,
)


def _all_text(paths: dict) -> dict[str, str]:
    document = Document(paths[".docx"])
    deck = Presentation(paths[".pptx"])
    return {
        "docx": "\n".join(p.text for p in document.paragraphs)
        + "\n".join(cell.text for table in document.tables for row in table.rows
                    for cell in row.cells)
        + document.sections[0].header.paragraphs[0].text,
        "pdf": "\n".join(page.extract_text() or "" for page in PdfReader(paths[".pdf"]).pages),
        "pptx": "\n".join(shape.text for slide in deck.slides for shape in slide.shapes
                          if hasattr(shape, "text")),
    }


def test_exports_carry_no_ai_or_tool_markers(tmp_path: Path) -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    paths = {p.suffix: p for p in create_artifacts(result, output_dir=tmp_path)}
    for name, text in _all_text(paths).items():
        assert not _TOOL_MARKERS.search(text), (name, _TOOL_MARKERS.search(text))

    docx_props = Document(paths[".docx"]).core_properties
    pptx_props = Presentation(paths[".pptx"]).core_properties
    pdf_meta = PdfReader(paths[".pdf"]).metadata
    metadata = " ".join(
        str(value)
        for value in (
            docx_props.author, docx_props.last_modified_by, docx_props.comments,
            pptx_props.author, pptx_props.last_modified_by, pptx_props.comments,
            pdf_meta.get("/Author"), pdf_meta.get("/Creator"), pdf_meta.get("/Producer"),
        )
    )
    assert not _TOOL_MARKERS.search(metadata)
    assert "ReportLab" not in metadata


def test_author_is_written_to_every_document(tmp_path: Path) -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    paths = {p.suffix: p for p in create_artifacts(result, agent_type="teaching",
                                                    output_dir=tmp_path, author="Dr. A. Rao")}
    assert Document(paths[".docx"]).core_properties.author == "Dr. A. Rao"
    assert Presentation(paths[".pptx"]).core_properties.author == "Dr. A. Rao"
    assert PdfReader(paths[".pdf"]).metadata.get("/Author") == "Dr. A. Rao"


def test_slides_have_transitions_and_valid_entrance_animations(tmp_path: Path) -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    deck = Presentation(next(p for p in create_artifacts(result, agent_type="teaching",
                                                         output_dir=tmp_path)
                             if p.suffix == ".pptx"))
    namespace = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
    animated_slides = 0
    for slide in deck.slides:
        element = slide._element
        assert element.find("p:transition", namespace) is not None
        timing = element.find("p:timing", namespace)
        if timing is None:
            continue
        animated_slides += 1
        shape_ids = {str(shape.shape_id) for shape in slide.shapes}
        targets = {node.get("spid") for node in timing.iter(f"{{{namespace['p']}}}spTgt")}
        assert targets and targets <= shape_ids
        # Timing must follow the transition, as PowerPoint requires.
        children = [child.tag.split("}")[1] for child in element]
        assert children.index("timing") == children.index("transition") + 1
    assert animated_slides >= len(deck.slides) - 1


def test_section_diagram_is_drawn_and_old_results_still_render(tmp_path: Path) -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    result["lecture_sections"][0]["content"]["diagram"] = {
        "type": "cycle",
        "title": "Cell life",
        "nodes": [
            {"label": "Grow", "detail": "The cell gets bigger."},
            {"label": "Copy DNA", "detail": "Genetic material is copied."},
            {"label": "Divide", "detail": "One cell becomes two."},
        ],
    }
    deck = Presentation(next(p for p in create_artifacts(result, agent_type="teaching",
                                                         output_dir=tmp_path / "new")
                             if p.suffix == ".pptx"))
    texts = {shape.text for slide in deck.slides for shape in slide.shapes
             if hasattr(shape, "text")}
    assert {"Grow", "Copy DNA", "Divide", "Cell life"} <= texts

    # Results saved before the diagram field existed fall back to an illustrated key-ideas list.
    for section in result["lecture_sections"]:
        section["content"].pop("diagram")
    deck = Presentation(next(p for p in create_artifacts(result, agent_type="teaching",
                                                         output_dir=tmp_path / "old")
                             if p.suffix == ".pptx"))
    texts = [shape.text for slide in deck.slides for shape in slide.shapes
             if hasattr(shape, "text")]
    assert "Key ideas" in texts
    assert "First foundations point" in texts


def test_unknown_agent_types_cannot_export(tmp_path: Path) -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    with pytest.raises(ValueError, match="Unsupported agent type"):
        create_artifacts(result, agent_type="synthesis", output_dir=tmp_path)
