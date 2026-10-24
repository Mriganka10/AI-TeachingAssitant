"""Conservative topic alignment checks for generated academic content.

These checks catch obvious subject drift before exports are created. They are
deliberately lexical: a match does not prove factual accuracy or source support.
"""

from __future__ import annotations

import re
import unicodedata
from math import ceil
from typing import Any

_GENERIC_TERMS = {
    "a", "an", "analysis", "and", "application", "as", "assessment", "at", "based",
    "between", "by", "comparison", "concept", "conclusion", "core", "effect",
    "evaluation", "for", "foundation", "from", "how", "impact", "in", "into",
    "introduction", "key", "lesson", "of", "on", "or", "overview", "recap", "research",
    "review", "role", "study", "summary", "the", "to", "using", "with",
}


def _words(value: Any) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    words = re.findall(r"[^\W_]+", normalized)
    normalized_words: set[str] = set()
    for word in words:
        if len(word) <= 1:
            continue
        if word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 4:
            word = word[:-1]
        normalized_words.add(word)
    return normalized_words


def topic_terms(topic: str) -> set[str]:
    """Keep distinctive words while supporting short topics such as `AI`."""
    words = _words(topic)
    return words - _GENERIC_TERMS or words


def _minimum_topic_terms(anchors: set[str]) -> int:
    return min(3, max(1, ceil(len(anchors) * 0.6)))


def _visible_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_visible_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_visible_text(item) for item in value)
    return str(value or "")


def topic_relevance_issues(agent_type: str, result: dict, request: dict) -> list[str]:
    """Reject outputs whose framing or substantive sections omit the requested subject."""
    topic = str(request.get("topic") or request.get("research_topic") or "").strip()
    anchors = topic_terms(topic)
    if not anchors:
        return []

    framing = _visible_text(result.get("title"))
    if agent_type == "teaching":
        substantive = _visible_text(result.get("lecture_sections"))
    else:
        substantive = _visible_text(
            (result.get("executive_summary"), result.get("themes"), result.get("research_gaps"))
        )

    issues: list[str] = []
    minimum_framing_terms = _minimum_topic_terms(anchors)
    if len(anchors & _words(framing)) < minimum_framing_terms:
        issues.append(
            "The title does not reflect the requested topic. Rebuild the output around "
            f"the requested topic: {topic}."
        )
    if not anchors & _words(substantive):
        issues.append(
            "The substantive sections do not address the requested topic. Rebuild the "
            f"explanations and evidence around: {topic}."
        )
    return issues


def storyboard_relevance_issues(plan: dict, teaching_package: dict) -> list[str]:
    """Check that a video plan stays tied to the validated teaching package."""
    source_title = str(teaching_package.get("title") or "").strip()
    title_anchors = topic_terms(source_title)
    if not title_anchors or not isinstance(plan, dict):
        return []

    plan_title = _words(plan.get("title"))
    minimum_title_terms = _minimum_topic_terms(title_anchors)
    issues: list[str] = []
    if len(title_anchors & plan_title) < minimum_title_terms:
        issues.append("The video title does not reflect the teaching-package topic.")

    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or len(scenes) < 3:
        return issues
    section_titles = [
        section.get("title", "")
        for section in teaching_package.get("lecture_sections", [])
        if isinstance(section, dict)
    ]
    scene_anchors = title_anchors | topic_terms(" ".join(map(str, section_titles)))
    interior = scenes[1:-1]
    aligned = sum(
        bool(scene_anchors & _words(_visible_text(scene)))
        for scene in interior
        if isinstance(scene, dict)
    )
    minimum_aligned = max(1, (len(interior) + 2) // 3)
    if aligned < minimum_aligned:
        issues.append(
            "The video scenes drift from the teaching-package topic or lesson sections. "
            "Rebuild the scene explanations from the supplied teaching package."
        )
    return issues
