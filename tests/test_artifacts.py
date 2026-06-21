from pathlib import Path

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
