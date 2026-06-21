import json
import re

from app.core.config import settings


class LLMService:
    def generate(self, *, system: str, payload: dict, use_web_search: bool) -> tuple[dict, str]:
        if settings.llm_service_mode.lower() == "mock":
            return self._mock(payload), "mock"
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required. Configure it as an environment variable.")
        from openai import OpenAI

        kwargs = {
            "model": settings.openai_model,
            "instructions": system,
            "input": json.dumps(payload, ensure_ascii=False),
        }
        if use_web_search:
            kwargs["tools"] = [{"type": "web_search"}]
        if settings.openai_model.startswith("gpt-5"):
            kwargs["reasoning"] = {"effort": settings.openai_reasoning_effort}
        response = OpenAI(api_key=settings.openai_api_key).responses.create(**kwargs)
        return self._parse_json(response.output_text), settings.openai_model

    @staticmethod
    def _parse_json(text: str) -> dict:
        cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The model returned an invalid structured response.") from exc

    @staticmethod
    def _mock(payload: dict) -> dict:
        topic = payload.get("topic") or payload.get("research_topic") or "Sample topic"
        if "duration_minutes" in payload:
            return {
                "title": topic,
                "overview": f"Teaching package for {topic}.",
                "learning_objectives": ["Explain core concepts", "Apply concepts", "Evaluate evidence"],
                "lecture_sections": [
                    {"title": "Foundations", "minutes": 20, "content": ["Key definitions", "Context"]},
                    {"title": "Application", "minutes": 25, "content": ["Worked example", "Case discussion"]},
                    {"title": "Synthesis", "minutes": 15, "content": ["Review", "Reflection"]},
                ],
                "mcqs": [
                    {"question": "Which option best reflects the core concept?", "options": ["A", "B", "C", "D"], "answer": "A", "explanation": "Sample explanation"}
                ],
                "assignments": [{"title": "Applied analysis", "prompt": f"Analyze {topic}.", "rubric": ["Accuracy", "Evidence", "Clarity"]}],
                "numerical_problems": [],
                "bloom_mapping": [{"objective": "Explain core concepts", "level": "Understand"}],
                "discussion_questions": [f"What are the practical implications of {topic}?"],
                "viva_questions": [f"Explain {topic} in your own words."],
                "case_study": {"title": "Classroom case", "scenario": f"A practical scenario involving {topic}.", "questions": ["What should be done next?"]},
                "sources": [],
            }
        return {
            "title": f"Research synthesis: {topic}",
            "executive_summary": f"A structured literature synthesis for {topic}.",
            "themes": [{"theme": "Dominant theme", "synthesis": "Summary of converging evidence.", "papers": []}],
            "methodology_comparison": [{"method": "Mixed methods", "strengths": ["Triangulation"], "limitations": ["Resource intensive"], "papers": []}],
            "research_gaps": ["Limited longitudinal evidence"],
            "research_questions": [f"How does {topic} vary across contexts?"],
            "future_scope": ["Longitudinal and cross-cultural validation"],
            "methodology_suggestions": ["Use a mixed-method longitudinal design"],
            "apa_references": [],
            "sources": [],
        }


llm = LLMService()
