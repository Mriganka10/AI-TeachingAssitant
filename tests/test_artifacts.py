from pathlib import Path
from zipfile import ZipFile

from docx import Document
from pptx import Presentation
from pptx.util import Inches
from pypdf import PdfReader

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
            {"id": f"LO{i}", "objective": long_text, "bloom_level": "Apply"}
            for i in range(1, 6)
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
        "sources": [{"title": "User Guide", "year": "2026", "url":
                     "https://scikit-learn.org/stable/user_guide?utm_source=openai"}],
    }

    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    presentation = Presentation(next(path for path in paths if path.suffix == ".pptx"))
    slide_text = "\n".join(
        shape.text for slide in presentation.slides for shape in slide.shapes if hasattr(shape, "text")
    )

    assert len(presentation.slides) <= 24
    assert "utm_source=openai" not in slide_text
    assert "[scikit-learn](" not in slide_text


def test_research_artifacts_are_all_professor_formats(tmp_path: Path) -> None:
    result = {
        "title": "Responsible AI in Higher Education",
        "executive_summary": {
            "evidence": "Institutions are adopting AI at different rates.",
            "inference": "Governance maturity may explain part of the variation.",
        },
        "themes": [
            {
                "theme": "Governance",
                "synthesis": {
                    "evidence": "Policies emphasize review and transparency.",
                    "inference": "Implementation quality remains uneven.",
                },
                "papers": ["Example et al., 2026"],
            }
        ],
        "research_gaps": [
            {
                "gap": "Limited longitudinal evidence",
                "evidence": "Most studies are cross-sectional.",
                "inference": "Long-term faculty effects remain uncertain.",
            }
        ],
        "research_questions": ["How does governance maturity affect faculty adoption?"],
        "methodology_suggestions": [
            {"suggestion": "Longitudinal mixed methods", "rationale": "Captures change over time."}
        ],
        "sources": [],
    }

    paths = create_artifacts(result, agent_type="research", output_dir=tmp_path)

    assert {path.suffix for path in paths} == {".json", ".docx", ".pdf", ".pptx"}
    assert all(path.exists() and path.stat().st_size > 0 for path in paths)


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
            ],
        },
        "mcqs": [{
            "question": "Which statement is correct?",
            "options": ["A. First", "B) Second", "Option C: Third", "D - Fourth"],
            "answer": "A",
            "explanation": "The first option is correct.",
        }],
        "numerical_problems": [{
            "problem": "Compute the mean squared error.",
            "given": ["Predictions ŷ = [2, 4]", "True values y = [1, 5]"],
            "solution_steps": [
                "Errors y - ŷ = [-1, 1].",
                "MSE = (1/2) Σ_i (y_i - ŷ_i)^2 = 1.",
            ],
            "final_answer": "MSE = 1.",
        }],
    }

    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    by_suffix = {path.suffix: path for path in paths}

    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(by_suffix[".pdf"]).pages)
    docx_text = "\n".join(paragraph.text for paragraph in Document(by_suffix[".docx"]).paragraphs)
    presentation = Presentation(by_suffix[".pptx"])
    pptx_text = "\n".join(
        shape.text for slide in presentation.slides for shape in slide.shapes if hasattr(shape, "text")
    )

    for exported_text in (pdf_text, docx_text, pptx_text):
        assert "A. First" in exported_text
        assert "A. A. First" not in exported_text
        assert "B. B) Second" not in exported_text
        assert "ŷ" in exported_text
        assert "Σ" in exported_text
        assert "■" not in exported_text

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
        "lecture_sections": [{
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
            },
        }],
        "mcqs": [{
            "question": "Why does a supervised model need a separate test set?",
            "options": ["Estimate generalization", "Fit parameters", "Create labels", "Scale features"],
            "answer": "A",
            "explanation": (
                "The test set is not used to fit the model. It estimates performance on data "
                "that the model did not see during training."
            ),
        }],
        "numerical_problems": [{
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
        }],
    }

    paths = create_artifacts(result, agent_type="teaching", output_dir=tmp_path)
    presentation = Presentation(next(path for path in paths if path.suffix == ".pptx"))

    section_slide = next(
        slide for slide in presentation.slides
        if any(getattr(shape, "text", "") == "Supervised and Unsupervised Learning"
               for shape in slide.shapes)
    )
    application = next(shape for shape in section_slide.shapes if shape.text.startswith("APPLICATION"))
    professor_note = next(
        shape for shape in section_slide.shapes if shape.text.startswith("PROFESSOR NOTE")
    )
    assert application.top + application.height <= professor_note.top
    assert "…" not in application.text

    numerical_slide = next(
        slide for slide in presentation.slides
        if any(getattr(shape, "text", "") == "Numerical Problem 1" for shape in slide.shapes)
    )
    problem = next(shape for shape in numerical_slide.shapes if shape.text.startswith("PROBLEM"))
    solution = next(shape for shape in numerical_slide.shapes if shape.text == "Solution")
    final_answer = next(
        shape for shape in numerical_slide.shapes if shape.text.startswith("FINAL ANSWER")
    )
    assert problem.top + problem.height <= solution.top
    assert final_answer.height < Inches(1.5)
