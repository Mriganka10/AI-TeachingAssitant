from app.agents.contracts import output_schema, validate_output
from app.agents.llm import LLMService
from app.agents.retrieval import _chunk_text, _semantic_scores


def test_strict_contracts_require_every_declared_property() -> None:
    for agent_type in ("teaching", "research"):
        schema = output_schema(agent_type)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_mock_teaching_package_passes_semantic_quality_checks() -> None:
    request = {
        "topic": "Responsible AI",
        "course": "Information Systems",
        "audience": "Postgraduate students",
        "duration_minutes": 75,
        "difficulty": "advanced",
    }
    result = LLMService._mock(request)

    assert validate_output("teaching", result, request) == []
    assert sum(section["minutes"] for section in result["lecture_sections"]) == 75
    assert len(result["mcqs"]) >= 5


def test_retrieval_chunks_long_sources_and_ranks_matching_chunk() -> None:
    text = ("Unrelated introductory material. " * 180) + "\n\n" + (
        "Longitudinal governance evidence and faculty adoption outcomes. " * 80
    )
    chunks = _chunk_text(text)
    scores = _semantic_scores("longitudinal governance faculty adoption", chunks)

    assert len(chunks) >= 2
    best_chunk = chunks[max(range(len(scores)), key=scores.__getitem__)]
    assert "Longitudinal governance" in best_chunk
