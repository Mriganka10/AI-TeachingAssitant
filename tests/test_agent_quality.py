import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agents.contracts import output_schema, validate_output
from app.agents.llm import LLMService
from app.agents.retrieval import _chunk_text, _semantic_scores
from app.core.config import settings


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


def test_teaching_quality_rejects_an_unrelated_but_well_formed_package() -> None:
    request = {"topic": "Time Value of Money", "duration_minutes": 60}
    relevant = LLMService._mock(request)
    assert validate_output("teaching", relevant, request) == []

    unrelated = deepcopy(relevant)
    unrelated["title"] = "Coastal Erosion"
    unrelated["overview"]["lesson_purpose"] = "Explain coastal erosion."
    for section in unrelated["lecture_sections"]:
        section["content"]["explanation"] = "Waves remove sand from the coast."
        section["content"]["worked_example"]["problem"] = "Describe a beach."

    issues = validate_output("teaching", unrelated, request)
    assert any("requested topic" in issue for issue in issues)
    assert any("substantive sections" in issue for issue in issues)


def test_research_quality_rejects_topic_drift_in_the_evidence_body() -> None:
    request = {"research_topic": "Responsible AI in Higher Education"}
    relevant = LLMService._mock(request)
    assert validate_output("research", relevant, request) == []

    unrelated = deepcopy(relevant)
    unrelated["executive_summary"]["evidence"] = "Beaches change over time."
    issues = validate_output("research", unrelated, request)
    assert any("substantive sections" in issue for issue in issues)


def test_llm_repairs_an_unrelated_draft_before_returning_it(monkeypatch) -> None:
    request = {"topic": "Time Value of Money", "duration_minutes": 60}
    relevant = LLMService._mock(request)
    unrelated = deepcopy(relevant)
    unrelated["title"] = "Coastal Erosion"
    unrelated["overview"]["lesson_purpose"] = "Describe a coastline."
    for section in unrelated["lecture_sections"]:
        section["content"]["explanation"] = "Waves move sand along the beach."
        section["content"]["worked_example"]["problem"] = "Describe a coastline."

    responses = iter((unrelated, relevant))
    calls: list[dict] = []

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text=json.dumps(next(responses)), status="completed")

    monkeypatch.setattr("app.agents.llm.create_openai_client", lambda **_: SimpleNamespace(
        responses=FakeResponses()
    ))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_service_mode", "openai")
    monkeypatch.setattr(settings, "llm_repair_attempts", 1)

    result, _ = LLMService().generate(
        agent_type="teaching", system="Test", payload=request, use_web_search=False
    )
    assert result["title"] == "Time Value of Money"
    assert len(calls) == 2
    assert "requested topic" in calls[1]["input"]


def test_llm_refuses_to_export_persistent_topic_drift(monkeypatch) -> None:
    request = {"topic": "Time Value of Money", "duration_minutes": 60}
    unrelated = LLMService._mock(request)
    unrelated["title"] = "Coastal Erosion"
    unrelated["overview"]["lesson_purpose"] = "Describe a coastline."
    for section in unrelated["lecture_sections"]:
        section["content"]["explanation"] = "Waves move sand along the beach."
        section["content"]["worked_example"]["problem"] = "Describe a coastline."

    class FakeResponses:
        def create(self, **_):
            return SimpleNamespace(output_text=json.dumps(unrelated), status="completed")

    monkeypatch.setattr("app.agents.llm.create_openai_client", lambda **_: SimpleNamespace(
        responses=FakeResponses()
    ))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_service_mode", "openai")
    monkeypatch.setattr(settings, "llm_repair_attempts", 1)

    with pytest.raises(RuntimeError, match="requested topic"):
        LLMService().generate(
            agent_type="teaching", system="Test", payload=request, use_web_search=False
        )
