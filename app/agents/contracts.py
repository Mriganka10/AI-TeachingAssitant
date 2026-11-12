"""Structured output contracts and semantic quality checks for the teaching and research agents."""

from __future__ import annotations

import re
from typing import Any

from app.agents.relevance import topic_relevance_issues


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

# One simple picture per lecture section, drawn as native shapes on the slides.
DIAGRAM = _object(
    {
        "type": _string(
            enum=["process", "cycle", "comparison", "concept_map", "hierarchy", "timeline"]
        ),
        "title": _string(),
        "nodes": _array(
            _object({"label": _string(), "detail": _string()}), minimum=2, maximum=6
        ),
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
                            "diagram": DIAGRAM,
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

# The research assistant reviews one uploaded manuscript. Locations use the paragraph IDs the
# manuscript is supplied with ("S3" for a section, "S3.P2" for its second paragraph).
SCORE_CRITERIA = [
    "originality",
    "significance",
    "methodological_rigor",
    "evidence_and_analysis",
    "clarity_and_structure",
    "literature_coverage",
    "reproducibility",
    "ethics_and_reporting",
]
REVIEW_DECISIONS = ["accept", "minor_revision", "major_revision", "reject_and_resubmit"]
CONFIDENCE = _string(enum=["low", "moderate", "high"])

RESEARCH_SCHEMA = _object(
    {
        "title": _string(),
        "manuscript_profile": _object(
            {
                "discipline": _string(),
                "paper_type": _string(),
                "research_question": _string(),
                "claimed_contributions": _array(_string(), minimum=1),
                "methods_summary": _string(),
                "data_summary": _string(),
                "key_findings": _array(_string(), minimum=1),
            }
        ),
        "summary": _string(),
        "recommendation": _object(
            {
                "decision": _string(enum=REVIEW_DECISIONS),
                "rationale": _string(),
                "confidence": CONFIDENCE,
            }
        ),
        "scorecard": _array(
            _object(
                {
                    "criterion": _string(enum=SCORE_CRITERIA),
                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "justification": _string(),
                }
            ),
            minimum=6,
            maximum=8,
        ),
        "strengths": _array(
            _object({"point": _string(), "evidence": _string(), "location": _string()}),
            minimum=2,
        ),
        "major_concerns": _array(
            _object(
                {
                    "issue": _string(),
                    "location": _string(),
                    "why_it_matters": _string(),
                    "suggested_fix": _string(),
                    "priority": _string(enum=["high", "medium"]),
                }
            ),
            minimum=1,
        ),
        "minor_issues": _array(
            _object({"location": _string(), "issue": _string(), "suggestion": _string()})
        ),
        "claims_check": _array(
            _object(
                {
                    "claim": _string(),
                    "location": _string(),
                    "support": _string(enum=["supported", "partly_supported", "not_supported"]),
                    "comment": _string(),
                }
            ),
            minimum=1,
        ),
        "methodology_review": _array(
            _object({"aspect": _string(), "assessment": _string(), "recommendation": _string()}),
            minimum=1,
        ),
        "statistics_and_reproducibility": _array(
            _object(
                {
                    "item": _string(),
                    "status": _string(
                        enum=["adequate", "needs_work", "missing", "not_applicable"]
                    ),
                    "comment": _string(),
                }
            ),
            minimum=1,
        ),
        "reporting_checklist": _object(
            {
                "guideline": _string(),
                "items": _array(
                    _object(
                        {
                            "item": _string(),
                            "status": _string(enum=["present", "partial", "missing"]),
                            "comment": _string(),
                        }
                    ),
                    minimum=3,
                ),
            }
        ),
        "research_gaps": _array(
            _object(
                {
                    "gap": _string(),
                    "evidence": _string(),
                    "inference": _string(),
                    "confidence": CONFIDENCE,
                    "source_refs": _array(_string()),
                }
            ),
            minimum=2,
        ),
        "literature_positioning": _array(
            _object(
                {
                    "theme": _string(),
                    "assessment": _string(),
                    "missing_work": _string(),
                    "source_refs": _array(_string()),
                }
            )
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
        "future_scope": _array(_object({"area": _string(), "scope": _string()}), minimum=2),
        "writing_and_structure": _array(
            _object({"aspect": _string(), "comment": _string(), "suggestion": _string()}),
            minimum=1,
        ),
        "references_review": _array(_object({"issue": _string(), "detail": _string()})),
        "title_suggestions": _array(_string(), minimum=1, maximum=3),
        "abstract_feedback": _string(),
        "venue_fit": _object(
            {"target": _string(), "assessment": _string(), "suggestions": _array(_string())}
        ),
        "revision_plan": _array(
            _object(
                {
                    "priority": {"type": "integer", "minimum": 1, "maximum": 30},
                    "action": _string(),
                    "addresses": _string(),
                    "effort": _string(enum=["small", "medium", "large"]),
                }
            ),
            minimum=3,
        ),
        "author_questions": _array(_string()),
        "apa_references": _array(_string()),
        "sources": _array(SOURCE),
        "quality_notes": QUALITY_NOTES,
    }
)

# Literature found by web search for a revision. Every entry is verified against Crossref or
# arXiv before it can be cited (app/agents/sources.py).
LITERATURE_SCHEMA = _object(
    {
        "sources": _array(
            _object(
                {
                    "title": _string(),
                    "authors": _array(_string()),
                    "year": _string(),
                    "venue": _string(),
                    "doi": _string(),
                    "arxiv_id": _string(),
                    "url": _string(),
                    "key_points": _array(_string(), minimum=1, maximum=4),
                    "relevance": _string(),
                }
            ),
            maximum=20,
        )
    }
)

# The revision plan: what happens to every section, which sections are added, and where tables go.
REVISION_PLAN_SCHEMA = _object(
    {
        "keywords": _array(_string(), minimum=3, maximum=8),
        "abstract_goals": _array(_string()),
        "sections": _array(
            _object(
                {
                    "source_id": _string(),
                    "heading": _string(),
                    "level": {"type": "integer", "minimum": 1, "maximum": 2},
                    "action": _string(enum=["keep", "revise", "expand", "add"]),
                    "goals": _array(_string()),
                    "addresses": _array(_string()),
                    "source_keys": _array(_string()),
                    "table": _object({"wanted": {"type": "boolean"}, "brief": _string()}),
                }
            ),
            minimum=1,
        ),
    }
)

TABLE = _object(
    {
        "caption": _string(),
        "columns": _array(_string(), minimum=2, maximum=7),
        "rows": _array(_array(_string(), minimum=2, maximum=7), minimum=1, maximum=20),
        "after_paragraph": {"type": "integer", "minimum": 0, "maximum": 200},
    }
)

# One manuscript section written or rewritten by the reviser.
REVISION_SCHEMA = _object(
    {
        "paragraphs": _array(_string(), minimum=1),
        "tables": _array(TABLE, maximum=2),
        "changes": _array(
            _object({"change": _string(), "reason": _string(), "addresses": _string()})
        ),
        "author_notes": _array(_string()),
    }
)

SUPPORTED_AGENT_TYPES = ("teaching", "research")
_SCHEMAS = {"teaching": TEACHING_SCHEMA, "research": RESEARCH_SCHEMA}
_SCHEMA_NAMES = {"teaching": "teaching_package", "research": "manuscript_review"}


def _require_supported(agent_type: str) -> None:
    if agent_type not in SUPPORTED_AGENT_TYPES:
        raise ValueError(f"Unsupported agent type: {agent_type}.")


def output_schema(agent_type: str = "teaching") -> dict:
    _require_supported(agent_type)
    return _SCHEMAS[agent_type]


def schema_name(agent_type: str = "teaching") -> str:
    _require_supported(agent_type)
    return _SCHEMA_NAMES[agent_type]


def validate_output(agent_type: str, result: dict, request: dict) -> list[str]:
    """Return actionable semantic problems that JSON Schema alone cannot guarantee."""
    issues: list[str] = []
    schema = output_schema(agent_type)
    for key in schema["required"]:
        if key not in result:
            issues.append(f"Missing top-level field: {key}")

    if agent_type == "research":
        issues.extend(_research_issues(result, request))
    else:
        issues.extend(_teaching_issues(result, request))

    source_ids = {
        source.get("id") for source in result.get("sources", []) if isinstance(source, dict)
    }
    referenced_ids: set[str] = set()
    for value in _walk_source_refs(result):
        referenced_ids.update(value)
    unknown = sorted(ref for ref in referenced_ids if ref and ref not in source_ids)
    if unknown:
        issues.append("Unknown source references: " + ", ".join(unknown))
    issues.extend(topic_relevance_issues(agent_type, result, request))
    return issues


def _teaching_issues(result: dict, request: dict) -> list[str]:
    issues: list[str] = []
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

    return issues

_LOCATION = re.compile(r"\bS\d+(?:\.P\d+)?\b")


def _manuscript_location_ids(request: dict) -> set[str]:
    manuscript = request.get("manuscript") if isinstance(request.get("manuscript"), dict) else {}
    ids: set[str] = set()
    for section in manuscript.get("sections") or []:
        if not isinstance(section, dict):
            continue
        ids.add(str(section.get("id")))
        ids.update(str(paragraph.get("id")) for paragraph in section.get("paragraphs") or []
                   if isinstance(paragraph, dict))
    return ids


def _research_issues(result: dict, request: dict) -> list[str]:
    issues: list[str] = []
    known = _manuscript_location_ids(request)

    def check_location(label: str, value: Any) -> None:
        text = str(value or "")
        found = _LOCATION.findall(text)
        if not found and text.strip().lower() not in {"whole manuscript", "global"}:
            issues.append(f"{label} needs a location such as S2 or S2.P3, or 'Whole manuscript'.")
        unknown = sorted({ref for ref in found if known and ref not in known})
        if unknown:
            issues.append(f"{label} cites locations that do not exist: {', '.join(unknown)}.")

    for index, item in enumerate(result.get("strengths") or [], start=1):
        check_location(f"Strength {index}", item.get("location") if isinstance(item, dict) else "")
    concerns = [item for item in result.get("major_concerns") or [] if isinstance(item, dict)]
    for index, item in enumerate(concerns, start=1):
        check_location(f"Major concern {index}", item.get("location"))
    for index, item in enumerate(result.get("minor_issues") or [], start=1):
        check_location(f"Minor issue {index}", item.get("location") if isinstance(item, dict) else "")
    for index, item in enumerate(result.get("claims_check") or [], start=1):
        check_location(f"Claim {index}", item.get("location") if isinstance(item, dict) else "")

    criteria = [
        item.get("criterion") for item in result.get("scorecard") or [] if isinstance(item, dict)
    ]
    if len(criteria) != len(set(criteria)):
        issues.append("Each scorecard criterion must appear only once.")
    decision = (result.get("recommendation") or {}).get("decision")
    high = sum(item.get("priority") == "high" for item in concerns)
    if decision == "accept" and high:
        issues.append("The recommendation is 'accept' but high-priority concerns are listed.")
    if decision == "reject_and_resubmit" and not high:
        issues.append("A 'reject_and_resubmit' decision needs at least one high-priority concern.")
    has_sources = bool(result.get("sources"))
    for index, gap in enumerate(result.get("research_gaps") or [], start=1):
        if not isinstance(gap, dict) or not gap.get("evidence"):
            issues.append(f"Research gap {index} lacks evidence.")
        elif has_sources and gap.get("source_refs") is None:
            issues.append(f"Research gap {index} lacks source references.")
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
