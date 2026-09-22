import json
import re

from app.agents.contracts import output_schema, schema_name, validate_output
from app.core.config import settings


class LLMService:
    def generate(
        self, *, agent_type: str, system: str, payload: dict, use_web_search: bool
    ) -> tuple[dict, str]:
        if settings.llm_service_mode.lower() == "mock":
            result = self._mock(payload)
            self._require_quality(agent_type, result, payload)
            return result, "mock"
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required. Configure it as an environment variable.")

        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        result = self._request(
            client,
            agent_type=agent_type,
            system=system,
            payload=payload,
            use_web_search=use_web_search,
        )
        issues = validate_output(agent_type, result, payload)
        for _ in range(max(0, settings.llm_repair_attempts)):
            if not issues:
                break
            result = self._request(
                client,
                agent_type=agent_type,
                system=system,
                payload={
                    "original_request": payload,
                    "draft": result,
                    "validation_issues": issues,
                    "task": (
                        "Return a corrected, complete response. Preserve supported content, fix "
                        "every listed issue, and do not add unsupported facts or references."
                    ),
                },
                use_web_search=False,
            )
            issues = validate_output(agent_type, result, payload)
        if issues:
            raise RuntimeError("Generated content failed quality checks: " + " ".join(issues))
        return result, settings.openai_model

    def _request(
        self,
        client,
        *,
        agent_type: str,
        system: str,
        payload: dict,
        use_web_search: bool,
    ) -> dict:
        kwargs = {
            "model": settings.openai_model,
            "instructions": system,
            "input": json.dumps(payload, ensure_ascii=False),
            "max_output_tokens": settings.openai_max_output_tokens,
            "prompt_cache_key": f"professor-ai:{agent_type}:structured-v2",
            "store": False,
            "text": {
                "verbosity": settings.openai_text_verbosity,
                "format": {
                    "type": "json_schema",
                    "name": schema_name(agent_type),
                    "schema": output_schema(agent_type),
                    "strict": True,
                },
            },
        }
        if use_web_search:
            kwargs["tools"] = [{"type": "web_search"}]
        if settings.openai_service_tier:
            kwargs["service_tier"] = settings.openai_service_tier
        if settings.openai_prompt_cache_retention:
            kwargs["prompt_cache_retention"] = settings.openai_prompt_cache_retention
        if settings.openai_model.startswith("gpt-5"):
            kwargs["reasoning"] = {"effort": settings.openai_reasoning_effort}
        response = client.responses.create(**kwargs)
        if getattr(response, "status", None) == "incomplete":
            detail = getattr(response, "incomplete_details", None)
            raise RuntimeError(f"The model response was incomplete: {detail or 'unknown reason'}")
        if not getattr(response, "output_text", "").strip():
            raise RuntimeError("The model returned no structured content.")
        return self._parse_json(response.output_text)

    @staticmethod
    def _parse_json(text: str) -> dict:
        cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The model returned an invalid structured response.") from exc
        if not isinstance(value, dict):
            raise TypeError("The model response must be a JSON object.")
        return value

    @staticmethod
    def _require_quality(agent_type: str, result: dict, payload: dict) -> None:
        issues = validate_output(agent_type, result, payload)
        if issues:
            raise RuntimeError("Mock output failed quality checks: " + " ".join(issues))

    @staticmethod
    def _mock(payload: dict) -> dict:
        topic = payload.get("topic") or payload.get("research_topic") or "Sample topic"
        if "duration_minutes" in payload:
            duration = int(payload.get("duration_minutes") or 60)
            first = max(5, duration // 3)
            second = max(5, duration // 3)
            third = duration - first - second
            return {
                "title": topic,
                "overview": {
                    "course": payload.get("course", "General"),
                    "audience": payload.get("audience", "University students"),
                    "duration_minutes": duration,
                    "difficulty": payload.get("difficulty", "intermediate"),
                    "lesson_purpose": f"Build an accurate working understanding of {topic}.",
                    "prerequisite_knowledge": ["Basic course vocabulary"],
                    "evidence_base": [],
                    "core_formulas": [],
                },
                "learning_objectives": [
                    {"id": "LO1", "objective": f"Explain the main concepts in {topic}.", "bloom_level": "Understand"},
                    {"id": "LO2", "objective": f"Apply {topic} to a worked example.", "bloom_level": "Apply"},
                    {"id": "LO3", "objective": f"Evaluate a decision involving {topic}.", "bloom_level": "Evaluate"},
                ],
                "lecture_sections": [
                    LLMService._mock_section("Foundations", first, "LO1", topic),
                    LLMService._mock_section("Application", second, "LO2", topic),
                    LLMService._mock_section("Evaluation", third, "LO3", topic),
                ],
                "mcqs": [LLMService._mock_mcq(topic, index) for index in range(1, 6)],
                "assignments": [{
                    "title": "Applied analysis",
                    "prompt": f"Analyze a realistic situation involving {topic} and justify your conclusion.",
                    "deliverables": ["Written analysis", "Evidence table"],
                    "rubric": [
                        {"criterion": "Accuracy", "weight_percent": 40, "description": "Uses concepts correctly."},
                        {"criterion": "Evidence", "weight_percent": 35, "description": "Supports claims with evidence."},
                        {"criterion": "Clarity", "weight_percent": 25, "description": "Communicates a logical conclusion."},
                    ],
                }],
                "numerical_problems": [],
                "bloom_mapping": [
                    {"objective": "LO1", "level": "Understand", "assessment": "MCQ and concept check"},
                    {"objective": "LO2", "level": "Apply", "assessment": "Worked example"},
                    {"objective": "LO3", "level": "Evaluate", "assessment": "Assignment"},
                ],
                "discussion_questions": [
                    f"Which assumptions most affect decisions about {topic}?",
                    f"Where could the concepts in {topic} be misapplied?",
                    f"What evidence would change your conclusion about {topic}?",
                ],
                "viva_questions": [
                    {"question": f"Define the central idea in {topic}.", "expected_answer": "A concise definition with one example.", "difficulty": "introductory"},
                    {"question": f"How would you apply {topic}?", "expected_answer": "A justified application process.", "difficulty": "intermediate"},
                    {"question": f"What are the limits of {topic}?", "expected_answer": "At least two limits and their implications.", "difficulty": "advanced"},
                ],
                "case_study": {
                    "title": "Applied classroom case",
                    "scenario": f"A decision maker must use {topic} with incomplete information.",
                    "questions": ["What is the core decision?", "Which evidence should guide it?"],
                    "teaching_notes": "Ask students to separate facts, assumptions, and conclusions.",
                    "source_refs": [],
                },
                "sources": [],
                "quality_notes": {
                    "limitations": ["Mock content is for workflow testing only."],
                    "unverified_claims": [],
                    "review_priorities": ["Replace mock content before classroom use."],
                },
            }
        return {
            "title": f"Research synthesis: {topic}",
            "scope": {
                "discipline": payload.get("discipline", "General"),
                "problem_statement": topic,
                "inclusion_boundaries": ["Sources supplied for the requested topic"],
                "exclusion_boundaries": ["Claims without supporting source material"],
            },
            "executive_summary": {
                "evidence": "The supplied mock corpus indicates variation across contexts.",
                "inference": "Context may influence the observed outcomes.",
                "limitations": "Mock mode does not contain publishable evidence.",
            },
            "themes": [
                {"theme": "Adoption", "evidence": ["Adoption varies by context."], "synthesis": {"evidence": "The mock studies report uneven adoption.", "inference": "Implementation conditions may explain the variation."}, "papers": [], "source_refs": []},
                {"theme": "Governance", "evidence": ["Oversight differs across settings."], "synthesis": {"evidence": "The mock corpus describes different governance practices.", "inference": "Governance maturity may shape outcomes."}, "papers": [], "source_refs": []},
            ],
            "methodology_comparison": [{"method": "Mixed methods", "studies": [], "strengths": ["Combines complementary evidence"], "limitations": ["Requires more time and expertise"], "suitability": "Useful for studying both outcomes and implementation."}],
            "research_gaps": [
                {"gap": "Limited longitudinal evidence", "evidence": "The mock corpus contains no longitudinal study.", "inference": "Change over time remains uncertain.", "confidence": "low", "source_refs": []},
                {"gap": "Limited cross-context comparison", "evidence": "The mock corpus does not compare settings directly.", "inference": "Contextual effects remain uncertain.", "confidence": "low", "source_refs": []},
            ],
            "research_questions": [
                {"question": f"How does {topic} vary over time?", "rationale": "Addresses the longitudinal gap.", "key_variables_or_constructs": ["time", "outcome"]},
                {"question": f"How does context influence {topic}?", "rationale": "Addresses the cross-context gap.", "key_variables_or_constructs": ["context", "outcome"]},
            ],
            "future_scope": [
                {"area": "Longitudinal research", "scope": "Measure change across multiple periods."},
                {"area": "Comparative research", "scope": "Compare consistent measures across settings."},
            ],
            "methodology_suggestions": [{"suggestion": "Longitudinal mixed-method design", "rationale": "Captures change and explains mechanisms.", "data_collection": "Repeated measures and semi-structured interviews.", "analysis": "Longitudinal modelling with thematic analysis."}],
            "apa_references": [],
            "sources": [],
            "quality_notes": {
                "limitations": ["Mock content is for workflow testing only."],
                "unverified_claims": [],
                "review_priorities": ["Replace mock evidence before academic use."],
            },
        }

    @staticmethod
    def _mock_section(title: str, minutes: int, objective_id: str, topic: str) -> dict:
        return {
            "title": title,
            "minutes": minutes,
            "learning_objective_ids": [objective_id],
            "content": {
                "explanation": f"A concise explanation of {title.lower()} in {topic}.",
                "key_points": [f"First {title.lower()} point", f"Second {title.lower()} point"],
                "worked_example": {"problem": f"Apply {topic} to a simple case.", "solution": "Identify the evidence, apply the concept, and justify the conclusion."},
                "classroom_activity": "Students compare two alternatives and explain their choice.",
                "concept_check": "Ask students to state the concept and apply it to a new example.",
                "common_errors": ["Using an unsupported assumption"],
                "teaching_tip": "Ask for evidence behind each answer.",
                "source_refs": [],
            },
        }

    @staticmethod
    def _mock_mcq(topic: str, index: int) -> dict:
        return {
            "question": f"Which option best demonstrates accurate use of {topic}? ({index})",
            "options": ["Apply the concept with evidence", "Ignore relevant evidence", "Use an unrelated rule", "Assume the conclusion"],
            "answer": "A",
            "explanation": "The correct option applies the relevant concept and supports the conclusion.",
            "difficulty": "intermediate",
            "objective_id": "LO2",
            "source_refs": [],
        }


llm = LLMService()
