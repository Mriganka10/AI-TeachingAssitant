from copy import deepcopy
from types import SimpleNamespace

from app.agents.llm import LLMService
from app.agents.plain_language import polish, polish_text, style_issues
from app.core.config import settings


def test_polish_replaces_stock_wording_without_changing_meaning() -> None:
    assert polish_text("It is important to note that the slope matters.") == "The slope matters."
    assert polish_text("Models utilize data — lots of it — to learn.") == (
        "Models use data, lots of it, to learn."
    )
    assert polish_text("Ages 5—10. Furthermore, the line fits.") == "Ages 5–10. Also, the line fits."
    assert polish_text("We delve into residuals in order to measure fit, e.g. the mean.") == (
        "We look at residuals to measure fit, e.g. the mean."
    )


def test_polish_never_touches_urls_citations_or_formulas() -> None:
    content = {
        "explanation": "See https://example.org/a—b for more — really.",
        "sources": [{"title": "A — B", "url": "https://x.org/utilize"}],
        "apa_references": ["Smith — 2020"],
        "overview": {"core_formulas": ["a — b"]},
        "mcqs": [{"answer": "A", "source_refs": ["S1"]}],
    }
    polished = polish(deepcopy(content))
    assert polished["explanation"] == "See https://example.org/a—b for more, really."
    assert polished["sources"] == content["sources"]
    assert polished["apa_references"] == content["apa_references"]
    assert polished["overview"]["core_formulas"] == ["a — b"]


def test_style_issues_flag_long_sentences_filler_and_self_reference() -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    assert style_issues("teaching", result) == []

    wordy = deepcopy(result)
    content = wordy["lecture_sections"][0]["content"]
    content["explanation"] = (
        "As an AI, I will delve into the many ways in which the cell, which is the basic unit of "
        "life and which contains many smaller parts that each have their own jobs, works."
    )
    content["diagram"]["nodes"][0]["label"] = "The first and most important stage of all"
    issues = " ".join(style_issues("teaching", wordy))
    assert "reference to AI" in issues
    assert "longer than 30 words" in issues
    assert '"delve"' in issues
    assert "Shorten diagram labels" in issues


def test_style_rewrite_never_fails_a_valid_job(monkeypatch) -> None:
    request = {"topic": "Cell Structure", "duration_minutes": 60}
    valid_but_wordy = LLMService._mock(request)
    valid_but_wordy["lecture_sections"][0]["content"]["explanation"] = (
        "Furthermore, cells utilize energy in order to grow."
    )
    broken_rewrite = deepcopy(valid_but_wordy)
    broken_rewrite["lecture_sections"][0]["minutes"] += 5  # violates the duration rule

    drafts = iter([valid_but_wordy, broken_rewrite])
    calls = []

    def fake_request(self, client, **kwargs):
        calls.append(kwargs["payload"])
        return deepcopy(next(drafts))

    monkeypatch.setattr(LLMService, "_request", fake_request)
    monkeypatch.setattr("app.agents.llm.create_openai_client", lambda **_: SimpleNamespace())
    monkeypatch.setattr(settings, "llm_service_mode", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_repair_attempts", 1)

    result, _ = LLMService().generate(
        agent_type="teaching", system="s", payload=request, use_web_search=False
    )

    assert len(calls) == 2  # the style issue triggered one rewrite
    assert any("stock phrases" in issue for issue in calls[1]["validation_issues"])
    # The broken rewrite was discarded; the valid draft is kept and polished.
    assert result["lecture_sections"][0]["content"]["explanation"] == "Also, cells use energy to grow."
