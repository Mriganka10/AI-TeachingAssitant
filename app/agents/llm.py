import json
import re

from app.agents.contracts import output_schema, schema_name, validate_output
from app.agents.plain_language import polish, style_issues
from app.core.config import settings
from app.core.openai_client import create_openai_client


class LLMService:
    def generate(
        self,
        *,
        agent_type: str,
        system: str,
        payload: dict,
        use_web_search: bool,
        reasoning_effort: str | None = None,
    ) -> tuple[dict, str]:
        if settings.llm_service_mode.lower() == "mock":
            result = self._mock_research(payload) if agent_type == "research" else self._mock(payload)
            self._require_quality(agent_type, result, payload)
            return self._finish(agent_type, result), "mock"
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required. Configure it as an environment variable.")

        client = create_openai_client(api_key=settings.openai_api_key)
        result = self._request(
            client,
            agent_type=agent_type,
            system=system,
            payload=payload,
            use_web_search=use_web_search,
            reasoning_effort=reasoning_effort,
        )
        issues = validate_output(agent_type, result, payload)
        style = style_issues(agent_type, result)
        # Plain-language problems may trigger a rewrite, but never fail the job: if a style-only
        # rewrite breaks a hard rule, the last valid draft is kept.
        last_valid = result if not issues else None
        for _ in range(max(0, settings.llm_repair_attempts)):
            if not issues and not style:
                break
            result = self._request(
                client,
                agent_type=agent_type,
                system=system,
                payload={
                    "original_request": payload,
                    "draft": result,
                    "validation_issues": issues + style,
                    "task": (
                        "Return a corrected, complete response. Preserve supported content, fix "
                        "every listed issue, and do not add unsupported facts or references. "
                        + (
                            "Keep the reviewer's precise academic voice."
                            if agent_type == "research"
                            else "Keep the writing plain and simple."
                        )
                    ),
                },
                use_web_search=False,
                reasoning_effort=reasoning_effort,
            )
            issues = validate_output(agent_type, result, payload)
            style = style_issues(agent_type, result)
            if not issues:
                last_valid = result
        if issues and last_valid is not None:
            result, issues = last_valid, []
        if issues:
            raise RuntimeError("Generated content failed quality checks: " + " ".join(issues))
        return self._finish(agent_type, result), settings.openai_model

    @staticmethod
    def _finish(agent_type: str, result: dict) -> dict:
        polished = polish(result)
        if agent_type == "research":
            # The manuscript's own title is quoted, never reworded.
            polished["title"] = result.get("title", "")
        return polished

    def _request(
        self,
        client,
        *,
        agent_type: str,
        system: str,
        payload: dict,
        use_web_search: bool,
        reasoning_effort: str | None = None,
    ) -> dict:
        kwargs = {
            "model": settings.openai_model,
            "instructions": system,
            "input": json.dumps(payload, ensure_ascii=False),
            "max_output_tokens": settings.openai_max_output_tokens,
            "prompt_cache_key": f"professor-ai:{agent_type}:structured-v3",
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
            kwargs["reasoning"] = {"effort": reasoning_effort or settings.openai_reasoning_effort}
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
        topic = payload.get("topic") or "Sample topic"
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

    @staticmethod
    def _mock_research(payload: dict) -> dict:
        """A complete, schema-valid review used to test the workflow without a model."""
        manuscript = payload.get("manuscript") or {}
        sections = manuscript.get("sections") or [{"id": "S1", "paragraphs": [{"id": "S1.P1"}]}]
        first = sections[0]
        last = sections[-1]
        first_paragraph = (first.get("paragraphs") or [{"id": first.get("id")}])[0].get("id")
        title = manuscript.get("title") or "Untitled manuscript"
        criteria = (
            ("originality", 3), ("significance", 3), ("methodological_rigor", 2),
            ("evidence_and_analysis", 3), ("clarity_and_structure", 4), ("literature_coverage", 3),
        )
        return {
            "title": title,
            "manuscript_profile": {
                "discipline": payload.get("discipline") or "General",
                "paper_type": payload.get("paper_type") or "Empirical study",
                "research_question": "What the study sets out to test, as stated in the opening.",
                "claimed_contributions": ["A new dataset", "An analysis of its main pattern"],
                "methods_summary": "A single observational study with descriptive analysis.",
                "data_summary": "One sample described in the methods section.",
                "key_findings": ["The main pattern holds in the sample studied."],
            },
            "summary": f"The manuscript reports a study titled '{title}' and argues for its main finding.",
            "recommendation": {
                "decision": "major_revision",
                "rationale": "The question is useful, but the design does not yet support the causal wording.",
                "confidence": "moderate",
            },
            "scorecard": [
                {"criterion": name, "score": score, "justification": f"Mock justification for {name}."}
                for name, score in criteria
            ],
            "strengths": [
                {"point": "Clear research question", "evidence": "Stated early.", "location": first.get("id")},
                {"point": "Relevant topic", "evidence": "Motivated in the opening.", "location": first_paragraph},
            ],
            "major_concerns": [{
                "issue": "Causal language is stronger than the design allows.",
                "location": last.get("id"),
                "why_it_matters": "Readers may overstate the finding.",
                "suggested_fix": "Rephrase as association and add a limitations paragraph.",
                "priority": "high",
            }],
            "minor_issues": [{"location": first_paragraph, "issue": "Long opening sentence.", "suggestion": "Split it in two."}],
            "claims_check": [{"claim": "The main finding generalises.", "location": last.get("id"), "support": "partly_supported", "comment": "Only one sample was studied."}],
            "methodology_review": [{"aspect": "Sampling", "assessment": "Convenience sample.", "recommendation": "Describe recruitment and its limits."}],
            "statistics_and_reproducibility": [{"item": "Data availability", "status": "missing", "comment": "No data statement."}],
            "reporting_checklist": {
                "guideline": "General empirical reporting checklist",
                "items": [
                    {"item": "Research question stated", "status": "present", "comment": "In the opening."},
                    {"item": "Sample described", "status": "partial", "comment": "Size given, recruitment not."},
                    {"item": "Limitations discussed", "status": "missing", "comment": "No limitations section."},
                ],
            },
            "research_gaps": [
                {"gap": "No replication in a second sample.", "evidence": "Single sample reported.", "inference": "Generality is untested.", "confidence": "high", "source_refs": []},
                {"gap": "Mechanism not tested.", "evidence": "No mediator measured.", "inference": "The why remains open.", "confidence": "moderate", "source_refs": []},
            ],
            "literature_positioning": [],
            "research_questions": [
                {"question": "Does the pattern hold in a second sample?", "rationale": "Tests generality.", "key_variables_or_constructs": ["Sample", "Outcome"]},
                {"question": "What explains the pattern?", "rationale": "Tests mechanism.", "key_variables_or_constructs": ["Mediator"]},
            ],
            "future_scope": [
                {"area": "Replication", "scope": "Repeat the study in a new setting."},
                {"area": "Mechanism", "scope": "Measure a likely mediator."},
            ],
            "writing_and_structure": [{"aspect": "Flow", "comment": "Sections are clear.", "suggestion": "Add signposting at section starts."}],
            "references_review": [],
            "title_suggestions": [title],
            "abstract_feedback": "State the design and the main result with its size.",
            "venue_fit": {"target": payload.get("target_venue") or "", "assessment": "Suited to a field journal.", "suggestions": []},
            "revision_plan": [
                {"priority": 1, "action": "Soften causal claims.", "addresses": "Major concern 1", "effort": "small"},
                {"priority": 2, "action": "Add a limitations paragraph.", "addresses": "Reporting checklist", "effort": "small"},
                {"priority": 3, "action": "Add a data availability statement.", "addresses": "Reproducibility", "effort": "small"},
            ],
            "author_questions": ["How were participants recruited?"],
            "apa_references": [],
            "sources": [],
            "quality_notes": {
                "limitations": ["Mock review for workflow testing only."],
                "unverified_claims": [],
                "review_priorities": ["Replace mock review before use."],
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
                "diagram": {
                    "type": "process",
                    "title": f"{title} step by step",
                    "nodes": [
                        {"label": "Notice", "detail": "Spot where the idea shows up."},
                        {"label": "Explain", "detail": "Say what is happening and why."},
                        {"label": "Use it", "detail": "Apply the idea to a new case."},
                    ],
                },
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
