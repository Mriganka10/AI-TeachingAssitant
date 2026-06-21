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
