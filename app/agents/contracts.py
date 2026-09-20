"""Structured output contracts and semantic quality checks for specialist agents."""

from __future__ import annotations

from typing import Any


def _string(*, enum: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "string"}
    if enum:
        schema["enum"] = enum
    return schema


def _array(items: dict, *, minimum: int = 0, maximum: int | None = None) -> dict:
    schema: dict[str, Any] = {"type": "array", "items": items, "minItems": minimum}
    if maximum is not None:
        schema["maxItems"] = maximum
    return schema


def _object(properties: dict[str, dict]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


SOURCE = _object(
    {
        "id": _string(),
        "title": _string(),
        "source_type": _string(enum=["uploaded_document", "web", "reference"]),
        "year": _string(),
        "url": _string(),
    }
)

QUALITY_NOTES = _object(
    {
        "limitations": _array(_string()),
        "unverified_claims": _array(_string()),
        "review_priorities": _array(_string(), minimum=1),
    }
)

TEACHING_SCHEMA = _object(
    {
        "title": _string(),
        "overview": _object(
            {
                "course": _string(),
                "audience": _string(),
                "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 300},
                "difficulty": _string(enum=["introductory", "intermediate", "advanced"]),
                "lesson_purpose": _string(),
                "prerequisite_knowledge": _array(_string()),
                "evidence_base": _array(_string()),
                "core_formulas": _array(_string()),
            }
        ),
        "learning_objectives": _array(
            _object(
                {
                    "id": _string(),
                    "objective": _string(),
                    "bloom_level": _string(
                        enum=["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"]
                    ),
                }
            ),
            minimum=3,
            maximum=8,
        ),
        "lecture_sections": _array(
            _object(
                {
                    "title": _string(),
                    "minutes": {"type": "integer", "minimum": 1, "maximum": 300},
                    "learning_objective_ids": _array(_string(), minimum=1),
                    "content": _object(
                        {
                            "explanation": _string(),
                            "key_points": _array(_string(), minimum=2, maximum=8),
                            "worked_example": _object(
                                {
                                    "problem": _string(),
                                    "solution": _string(),
                                }
                            ),
                            "classroom_activity": _string(),
                            "concept_check": _string(),
                            "common_errors": _array(_string()),
                            "teaching_tip": _string(),
                            "source_refs": _array(_string()),
                        }
                    ),
                }
            ),
            minimum=3,
        ),
        "mcqs": _array(
            _object(
                {
                    "question": _string(),
                    "options": _array(_string(), minimum=4, maximum=4),
                    "answer": _string(enum=["A", "B", "C", "D"]),
                    "explanation": _string(),
                    "difficulty": _string(enum=["introductory", "intermediate", "advanced"]),
                    "objective_id": _string(),
                    "source_refs": _array(_string()),
                }
            ),
            minimum=5,
        ),
        "assignments": _array(
            _object(
                {
                    "title": _string(),
                    "prompt": _string(),
                    "deliverables": _array(_string(), minimum=1),
                    "rubric": _array(
                        _object(
                            {
                                "criterion": _string(),
                                "weight_percent": {"type": "integer", "minimum": 0, "maximum": 100},
                                "description": _string(),
                            }
                        ),
                        minimum=2,
                    ),
                }
            ),
            minimum=1,
        ),
        "numerical_problems": _array(
            _object(
                {
                    "problem": _string(),
                    "given": _array(_string()),
                    "solution_steps": _array(_string(), minimum=1),
                    "final_answer": _string(),
                }
            )
        ),
        "bloom_mapping": _array(
            _object(
                {
                    "objective": _string(),
                    "level": _string(
                        enum=["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"]
                    ),
                    "assessment": _string(),
                }
            ),
            minimum=3,
        ),
        "discussion_questions": _array(_string(), minimum=3),
        "viva_questions": _array(
            _object(
                {
                    "question": _string(),
                    "expected_answer": _string(),
                    "difficulty": _string(enum=["introductory", "intermediate", "advanced"]),
                }
            ),
            minimum=3,
        ),
        "case_study": _object(
            {
                "title": _string(),
                "scenario": _string(),
                "questions": _array(_string(), minimum=2),
                "teaching_notes": _string(),
                "source_refs": _array(_string()),
            }
        ),
        "sources": _array(SOURCE),
        "quality_notes": QUALITY_NOTES,
    }
)

RESEARCH_SCHEMA = _object(
    {
        "title": _string(),
        "scope": _object(
            {
                "discipline": _string(),
                "problem_statement": _string(),
                "inclusion_boundaries": _array(_string(), minimum=1),
                "exclusion_boundaries": _array(_string()),
            }
        ),
        "executive_summary": _object(
            {
                "evidence": _string(),
                "inference": _string(),
                "limitations": _string(),
            }
        ),
        "themes": _array(
            _object(
                {
                    "theme": _string(),
                    "evidence": _array(_string(), minimum=1),
                    "synthesis": _object({"evidence": _string(), "inference": _string()}),
                    "papers": _array(_string()),
                    "source_refs": _array(_string()),
                }
            ),
            minimum=2,
        ),
        "methodology_comparison": _array(
            _object(
                {
                    "method": _string(),
                    "studies": _array(_string()),
                    "strengths": _array(_string(), minimum=1),
                    "limitations": _array(_string(), minimum=1),
                    "suitability": _string(),
                }
            ),
            minimum=1,
        ),
        "research_gaps": _array(
            _object(
                {
                    "gap": _string(),
                    "evidence": _string(),
                    "inference": _string(),
                    "confidence": _string(enum=["low", "moderate", "high"]),
                    "source_refs": _array(_string()),
                }
            ),
            minimum=2,
        ),
        "research_questions": _array(
            _object(
                {
                    "question": _string(),
                    "rationale": _string(),
                    "key_variables_or_constructs": _array(_string()),
                }
            ),
            minimum=2,
        ),
        "future_scope": _array(
            _object({"area": _string(), "scope": _string()}),
            minimum=2,
        ),
        "methodology_suggestions": _array(
            _object(
                {
                    "suggestion": _string(),
                    "rationale": _string(),
                    "data_collection": _string(),
                    "analysis": _string(),
                }
            ),
            minimum=1,
        ),
        "apa_references": _array(_string()),
        "sources": _array(SOURCE),
        "quality_notes": QUALITY_NOTES,
    }
)


def output_schema(agent_type: str) -> dict:
    return TEACHING_SCHEMA if agent_type == "teaching" else RESEARCH_SCHEMA


def schema_name(agent_type: str) -> str:
    return "teaching_package" if agent_type == "teaching" else "research_synthesis"


def validate_output(agent_type: str, result: dict, request: dict) -> list[str]:
    """Return actionable semantic problems that JSON Schema alone cannot guarantee."""
    issues: list[str] = []
    schema = output_schema(agent_type)
    for key in schema["required"]:
        if key not in result:
            issues.append(f"Missing top-level field: {key}")

    if agent_type == "teaching":
        sections = result.get("lecture_sections") or []
        requested_duration = int(request.get("duration_minutes") or 60)
        actual_duration = sum(
            int(section.get("minutes") or 0) for section in sections if isinstance(section, dict)
        )
        if actual_duration != requested_duration:
            issues.append(
                f"Lecture-section minutes total {actual_duration}; they must total {requested_duration}."
            )
        objective_ids = {
            item.get("id") for item in result.get("learning_objectives", []) if isinstance(item, dict)
        }
        for index, item in enumerate(result.get("mcqs", []), start=1):
            if len(item.get("options", [])) != 4:
                issues.append(f"MCQ {index} must have exactly four options.")
            if item.get("objective_id") not in objective_ids:
                issues.append(f"MCQ {index} references an unknown learning objective.")
        for index, assignment in enumerate(result.get("assignments", []), start=1):
            total = sum(
                int(row.get("weight_percent") or 0)
                for row in assignment.get("rubric", [])
                if isinstance(row, dict)
            )
            if total != 100:
                issues.append(f"Assignment {index} rubric weights total {total}; they must total 100.")
    else:
        has_sources = bool(result.get("sources"))
        for index, gap in enumerate(result.get("research_gaps", []), start=1):
            if not gap.get("evidence") or (has_sources and not gap.get("source_refs")):
                issues.append(f"Research gap {index} lacks evidence or source references.")

    source_ids = {
        source.get("id") for source in result.get("sources", []) if isinstance(source, dict)
    }
    referenced_ids: set[str] = set()
    for value in _walk_source_refs(result):
        referenced_ids.update(value)
    unknown = sorted(ref for ref in referenced_ids if ref and ref not in source_ids)
    if unknown:
        issues.append("Unknown source references: " + ", ".join(unknown))
    return issues


def _walk_source_refs(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_refs" and isinstance(item, list):
                yield [str(ref) for ref in item]
            else:
                yield from _walk_source_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_source_refs(item)
