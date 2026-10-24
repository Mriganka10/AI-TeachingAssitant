"""Create an optional narrated MP4 from validated teaching-agent content."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
import unicodedata
import urllib.error
import urllib.request
import wave
from array import array
from dataclasses import dataclass, replace
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.agents.relevance import storyboard_relevance_issues
from app.core.config import settings
from app.core.openai_client import create_openai_client

FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
WORDS_PER_MINUTE = 138
SCRIPT_WORDS_PER_MINUTE = WORDS_PER_MINUTE
MAX_TTS_CHARACTERS = 3900
MINIMUM_AUDIO_COVERAGE = 0.80
MAX_AUDIO_TEMPO = 1.15
SCENE_ACCENTS = ("#2B6DE8", "#0D93A9", "#7255C9", "#168664", "#B96E22")
VISUAL_TYPES = (
    "title",
    "concept_map",
    "comparison",
    "process",
    "timeline",
    "worked_example",
    "formula",
    "case_study",
    "key_points",
    "recap",
)

VIDEO_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "target_duration_seconds": {"type": "integer", "minimum": 60},
        "scenes": {
            "type": "array",
            "minItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "title": {"type": "string"},
                    "visual_type": {"type": "string", "enum": list(VISUAL_TYPES)},
                    "visual_elements": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 6,
                        "items": {"type": "string"},
                    },
                    "bullets": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 5,
                        "items": {"type": "string"},
                    },
                    "takeaway": {"type": "string"},
                    "narration": {"type": "string"},
                },
                "required": [
                    "section",
                    "title",
                    "visual_type",
                    "visual_elements",
                    "bullets",
                    "takeaway",
                    "narration",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "target_duration_seconds", "scenes"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class VideoScene:
    section: str
    title: str
    bullets: tuple[str, ...]
    narration: str
    visual_type: str = "key_points"
    visual_elements: tuple[str, ...] = ()
    takeaway: str = ""


def create_teaching_video(
    result: dict,
    path: Path,
    *,
    min_minutes: int | None = None,
    max_minutes: int | None = None,
) -> Path:
    """Render a verified 16:9 explainer that stays within the configured duration range."""
    if not settings.video_generation_enabled:
        raise RuntimeError("Video generation is disabled by VIDEO_GENERATION_ENABLED.")
    if settings.llm_service_mode.lower() == "mock":
        raise RuntimeError(
            "Narrated teaching videos are unavailable in mock mode. Set "
            "LLM_SERVICE_MODE=openai and configure OPENAI_API_KEY."
        )
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to prepare the multimedia video storyboard.")
    if settings.video_tts_provider == "elevenlabs" and not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is required when VIDEO_TTS_PROVIDER=elevenlabs.")

    minimum_minutes, maximum_minutes = _resolve_video_duration_range(
        min_minutes=min_minutes,
        max_minutes=max_minutes,
    )

    scenes = generate_teaching_video_plan(
        result,
        min_minutes=minimum_minutes,
        max_minutes=maximum_minutes,
    )
    if not scenes:
        raise RuntimeError("The teaching package does not contain enough content for a video.")

    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg_executable()
    minimum_seconds = minimum_minutes * 60
    maximum_seconds = maximum_minutes * 60

    with TemporaryDirectory(prefix="professor-video-", dir=path.parent) as workspace_name:
        workspace = Path(workspace_name)
        prepared: list[tuple[VideoScene, Path, Path, float]] = []
        narration_seconds = 0.0
        for index, scene in enumerate(scenes, start=1):
            frame = workspace / f"scene-{index:02d}.png"
            audio = workspace / f"scene-{index:02d}.wav"
            _render_scene_frame(scene, frame, index=index, total=len(scenes))
            _create_narration(scene.narration, audio)
            audio_seconds = _trim_narration_silence(audio)
            if audio_seconds <= 0.1:
                raise RuntimeError(f"Narration for scene {index} was empty or unreadable.")
            narration_words = len(scene.narration.split())
            slowest_reasonable_seconds = max(60.0, narration_words * 60 / 75)
            if audio_seconds > slowest_reasonable_seconds:
                raise RuntimeError(
                    f"Narration for scene {index} is unusually long for {narration_words} "
                    f"words ({audio_seconds:.1f}s). Retry the speech provider."
                )
            prepared.append((scene, frame, audio, audio_seconds))
            narration_seconds += audio_seconds

        # A four-minute lesson must contain substantial spoken explanation, not a minute of speech
        # followed by several minutes of silence. Small provider differences are filled with
        # deliberate visual breathing room after each scene.
        minimum_spoken_seconds = max(
            minimum_seconds * MINIMUM_AUDIO_COVERAGE,
            minimum_seconds - len(prepared) * 1.0,
        )
        if narration_seconds < minimum_spoken_seconds:
            narration_words = sum(len(scene.narration.split()) for scene in scenes)
            raise RuntimeError(
                "The narration service returned audio that is too short for a complete lesson "
                f"({narration_seconds:.1f}s for {narration_words} words; at least "
                f"{minimum_spoken_seconds:.0f}s is required). Check VIDEO_TTS_SPEED and the "
                "selected voice/provider, then generate the video again."
            )

        normal_pause_total = len(prepared) * 0.45
        tempo = max(1.0, (narration_seconds + normal_pause_total) / maximum_seconds)
        if tempo > MAX_AUDIO_TEMPO:
            raise RuntimeError(
                "The narration exceeds the selected maximum video duration even with a modest "
                "pace adjustment. Regenerate a shorter storyboard or increase the maximum."
            )
        playback_seconds = narration_seconds / tempo
        required_pause_total = max(0.0, minimum_seconds - playback_seconds)
        pause_per_scene = max(normal_pause_total, required_pause_total) / len(prepared)
        if playback_seconds + pause_per_scene * len(prepared) > maximum_seconds + 0.1:
            pause_per_scene = max(0.0, (maximum_seconds - playback_seconds) / len(prepared))
        scene_durations = [audio_seconds / tempo + pause_per_scene for _, _, _, audio_seconds in prepared]
        segments: list[Path] = []
        for index, ((_, frame, audio, _), duration) in enumerate(
            zip(prepared, scene_durations, strict=True), start=1
        ):
            segment = workspace / f"scene-{index:02d}.mp4"
            _render_segment(
                ffmpeg, frame, audio, segment, duration, scene_index=index, tempo=tempo
            )
            segments.append(segment)

        if len(segments) != len(scenes):
            raise RuntimeError("Video rendering did not include every storyboard scene.")
        rendered = workspace / "verified-explainer.mp4"
        _concatenate_segments(ffmpeg, segments, rendered, maximum_seconds)
        rendered_seconds = _media_duration(ffmpeg, rendered)
        if rendered_seconds < minimum_seconds - 1.0:
            raise RuntimeError(
                "Video verification failed: the rendered timeline is only "
                f"{rendered_seconds:.1f}s, but the configured minimum is {minimum_seconds}s."
            )
        if rendered_seconds > maximum_seconds + 1.0:
            raise RuntimeError(
                "Video verification failed: the rendered timeline exceeds the configured "
                f"{maximum_seconds}s limit."
            )
        _verify_scene_progression(ffmpeg, rendered, scene_durations)
        rendered.replace(path)

    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("Video rendering did not produce a playable MP4 file.")
    return path


def _resolve_video_duration_range(
    *, min_minutes: int | None, max_minutes: int | None
) -> tuple[int, int]:
    """Resolve request-level duration controls without changing deployment defaults."""
    minimum = settings.video_min_minutes if min_minutes is None else min_minutes
    maximum = settings.video_max_minutes if max_minutes is None else max_minutes
    if minimum < 1 or maximum < 1:
        raise ValueError("Video duration limits must be positive whole minutes.")
    if minimum > maximum:
        raise ValueError("Minimum video duration cannot exceed maximum video duration.")
    return minimum, maximum


def generate_teaching_video_plan(
    result: dict, *, min_minutes: int = 4, max_minutes: int = 15
) -> list[VideoScene]:
    """Create a complete, schema-constrained lecture script from validated teaching content."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to prepare the teaching-video script.")

    minimum = max(1, min_minutes)
    maximum = max(minimum, max_minutes)
    source_word_count = len(_text(result).split())
    target_minutes = min(maximum, max(minimum, round(2 + source_word_count / 500)))
    minimum_words = max(75, round(minimum * SCRIPT_WORDS_PER_MINUTE * 0.95))
    target_words = target_minutes * SCRIPT_WORDS_PER_MINUTE
    maximum_words = max(minimum_words, round(maximum * SCRIPT_WORDS_PER_MINUTE * 1.05))
    suggested_scene_count = max(3, round(target_words / 110))

    client = create_openai_client(api_key=settings.openai_api_key)
    request_payload: dict[str, Any] = {
        "teaching_package": result,
        "video_requirements": {
            "minimum_minutes": minimum,
            "target_minutes": target_minutes,
            "maximum_minutes": maximum,
            "minimum_narration_words": minimum_words,
            "target_narration_words": target_words,
            "maximum_narration_words": maximum_words,
            "suggested_scene_count": suggested_scene_count,
        },
    }
    last_issues: list[str] = []
    for attempt in range(2):
        if attempt:
            request_payload = {
                **request_payload,
                "validation_issues": last_issues,
                "task": (
                    "Return a corrected complete plan. Expand explanation with supported definitions, "
                    "reasoning, mechanisms, comparisons, and worked examples until the minimum word "
                    "count is met. Do not pad with repetition or introduce unsupported facts."
                ),
            }
        response = client.responses.create(
            model=settings.video_script_model or settings.openai_model,
            instructions=_video_script_instructions(),
            input=json.dumps(request_payload, ensure_ascii=False),
            max_output_tokens=max(
                settings.video_script_max_output_tokens,
                round(maximum_words * 2.4),
            ),
            store=False,
            text={
                "verbosity": "high",
                "format": {
                    "type": "json_schema",
                    "name": "teaching_video_plan",
                    "strict": True,
                    "schema": VIDEO_PLAN_SCHEMA,
                },
            },
            **(
                {"reasoning": {"effort": settings.openai_reasoning_effort}}
                if (settings.video_script_model or settings.openai_model).startswith("gpt-5")
                else {}
            ),
        )
        if getattr(response, "status", None) == "incomplete":
            raise RuntimeError("The teaching-video script response was incomplete.")
        if not getattr(response, "output_text", "").strip():
            raise RuntimeError("The teaching-video script service returned no content.")
        try:
            plan = json.loads(response.output_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The teaching-video script was not valid JSON.") from exc
        plan = _constrain_plan_narration(plan, maximum_words=maximum_words)
        last_issues = _video_plan_issues(
            plan,
            minimum_words=minimum_words,
            maximum_words=maximum_words,
            teaching_package=result,
        )
        if not last_issues:
            return [
                VideoScene(
                    section=_text(scene.get("section"), "TEACHING EXPLAINER"),
                    title=_text(scene.get("title"), "Key concept"),
                    bullets=tuple(_items(scene.get("bullets"))[:5]),
                    narration=_text(scene.get("narration"))[:MAX_TTS_CHARACTERS],
                    visual_type=_text(scene.get("visual_type"), "key_points"),
                    visual_elements=tuple(_items(scene.get("visual_elements"))[:6]),
                    takeaway=_text(scene.get("takeaway")),
                )
                for scene in plan["scenes"]
            ]
    raise RuntimeError("The teaching-video script failed quality checks: " + " ".join(last_issues))


def _constrain_plan_narration(plan: Any, *, maximum_words: int) -> Any:
    """Reduce a valid storyboard's small word-budget overflow without discarding scenes."""
    if not isinstance(plan, dict) or not isinstance(plan.get("scenes"), list):
        return plan
    scenes = plan["scenes"]
    word_counts = [len(_text(scene.get("narration")).split()) for scene in scenes]
    overflow = sum(word_counts) - maximum_words
    if overflow <= 0:
        return plan

    # Preserve at least 45 words per scene, then remove excess from the longest scenes first.
    for index in sorted(range(len(scenes)), key=lambda item: word_counts[item], reverse=True):
        if overflow <= 0:
            break
        removable = max(0, word_counts[index] - 45)
        if not removable:
            continue
        remove = min(removable, overflow)
        target_words = word_counts[index] - remove
        narration = _narration_excerpt(
            _text(scenes[index].get("narration")),
            maximum_words=target_words,
            minimum_words=45,
        )
        new_count = len(narration.split())
        scenes[index]["narration"] = narration
        overflow -= word_counts[index] - new_count
        word_counts[index] = new_count

    # Sentence-boundary trimming can remove more than requested, which is safe. If unusually long
    # sentences still leave an overflow, apply an exact final cap to the remaining longest scene.
    if overflow > 0:
        for index in sorted(range(len(scenes)), key=lambda item: word_counts[item], reverse=True):
            removable = max(0, word_counts[index] - 45)
            if not removable:
                continue
            remove = min(removable, overflow)
            words = _text(scenes[index].get("narration")).split()
            scenes[index]["narration"] = " ".join(words[: len(words) - remove]).rstrip(" ,;:") + "."
            overflow -= remove
            if overflow <= 0:
                break
    return plan


def _narration_excerpt(text: str, *, maximum_words: int, minimum_words: int) -> str:
    words = text.split()
    if len(words) <= maximum_words:
        return text
    lower_bound = min(minimum_words, maximum_words)
    candidate = words[:maximum_words]
    for position in range(len(candidate) - 1, lower_bound - 2, -1):
        if re.search(r"[.!?][\"')\]]?$", candidate[position]):
            return " ".join(candidate[: position + 1])
    return " ".join(candidate).rstrip(" ,;:") + "."


def _video_script_instructions() -> str:
    return """
You are a senior university lecturer and educational video scriptwriter. Convert the validated
teaching package into a compact but genuinely explanatory narrated lesson.

Use only information supported by the supplied teaching package. Do not introduce new statistics,
citations, formulas, claims, or examples that are not supported by it. You may reorganize and
explain supported material in clearer language.

The video must teach, not merely read headings or bullet points. Build a coherent progression:
1. establish why the topic matters and the mental model the viewer needs;
2. define the central concepts and distinguish easily confused ideas;
3. explain how or why each concept works, including assumptions and limitations;
4. walk through supplied examples or applications step by step;
5. address common errors or misconceptions from the package;
6. close with a concise synthesis and reflection questions.

Design this as a multimedia video overview rather than a recorded slide deck. Select the visual type
that best explains each scene. Use concept maps for relationships, comparisons for contrasts,
processes or timelines for sequences, worked examples for applications, formula scenes for
mathematics, and recap scenes for synthesis. Use at least three distinct visual types across the
video. visual_elements must contain concise labels that can be rendered as a diagram; bullets must
reinforce the explanation rather than duplicate those labels. The first scene must use title and the
last scene must use recap.

Each scene needs two to five short on-screen bullets, a one-sentence takeaway, and natural spoken
narration. Narration must contain complete explanations with transitions between ideas; never use
filler, slogans, repeated sentences, or classroom-management instructions as a substitute for
teaching. Speak mathematical notation in an understandable way. Respect the requested narration
word range because it controls the final duration. Use the supplied suggested_scene_count as the
target storyboard length and normally keep each scene between 25 and 180 narration words so short
and long user-selected videos both progress through visible scene changes. The first scene must
disclose that the narration is AI-generated.
"""


def _video_plan_issues(
    plan: Any,
    *,
    minimum_words: int,
    maximum_words: int,
    teaching_package: dict | None = None,
) -> list[str]:
    issues: list[str] = []
    if not isinstance(plan, dict):
        return ["The plan must be a JSON object."]
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or len(scenes) < 3:
        issues.append("The plan must contain at least three scenes.")
        return issues
    narration_words = 0
    visual_types: set[str] = set()
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            issues.append(f"Scene {index} is not an object.")
            continue
        narration = _text(scene.get("narration"))
        visual_type = _text(scene.get("visual_type"))
        visual_types.add(visual_type)
        narration_words += len(narration.split())
        if len(narration) > MAX_TTS_CHARACTERS:
            issues.append(f"Scene {index} narration exceeds the speech-input limit.")
        if len(narration.split()) < 25:
            issues.append(f"Scene {index} needs a fuller explanation.")
        if not 2 <= len(scene.get("bullets") or []) <= 5:
            issues.append(f"Scene {index} must contain two to five visual bullets.")
        if visual_type not in VISUAL_TYPES:
            issues.append(f"Scene {index} has an unsupported visual type.")
        if not 2 <= len(scene.get("visual_elements") or []) <= 6:
            issues.append(f"Scene {index} must contain two to six visual elements.")
        if not _text(scene.get("takeaway")):
            issues.append(f"Scene {index} must contain a concise takeaway.")
    if len(visual_types) < 3:
        issues.append("The storyboard must use at least three distinct visual types.")
    if scenes and scenes[0].get("visual_type") != "title":
        issues.append("The first scene must use the title visual type.")
    if scenes and scenes[-1].get("visual_type") != "recap":
        issues.append("The last scene must use the recap visual type.")
    if narration_words < minimum_words:
        issues.append(
            f"Narration has {narration_words} words; it needs at least {minimum_words} words."
        )
    if narration_words > maximum_words:
        issues.append(
            f"Narration has {narration_words} words; it must not exceed {maximum_words} words."
        )
    if teaching_package is not None:
        issues.extend(storyboard_relevance_issues(plan, teaching_package))
    return issues


def build_teaching_video_scenes(result: dict, *, max_minutes: int = 15) -> list[VideoScene]:
    """Convert the teaching package into concise scenes and a bounded narration script."""
    title = _text(result.get("title"), "Teaching explainer")
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    purpose = _text(overview.get("lesson_purpose"), f"An academic overview of {title}.")
    scenes = [
        VideoScene(
            section="PROFESSOR AI EXPLAINER",
            title=title,
            bullets=tuple(
                value
                for value in (
                    _labelled("Course", overview.get("course")),
                    _labelled("Audience", overview.get("audience")),
                    _labelled("Level", overview.get("difficulty")),
                )
                if value
            ),
            narration=(
                f"Welcome to this AI-narrated teaching explainer on {title}. {purpose} "
                "The voice in this video is generated by artificial intelligence."
            ),
        )
    ]

    objectives = _items(result.get("learning_objectives"), keys=("objective", "text"))
    if objectives:
        scenes.append(
            VideoScene(
                section="LEARNING ROADMAP",
                title="What you will understand",
                bullets=tuple(objectives[:6]),
                narration=(
                    "By the end of this explainer, you should be able to "
                    + _spoken_list(objectives[:6])
                    + "."
                ),
            )
        )

    for section in result.get("lecture_sections") or []:
        if not isinstance(section, dict):
            continue
        content = section.get("content") if isinstance(section.get("content"), dict) else {}
        section_title = _text(section.get("title"), "Key concept")
        explanation = _text(content.get("explanation"))
        points = _items(content.get("key_points"))
        activity = _text(content.get("classroom_activity"))
        check = _text(content.get("concept_check"))
        worked = content.get("worked_example") or content.get("worked_examples")
        worked_text = _text(worked)
        bullets = tuple((points + [value for value in (worked_text, activity) if value])[:5])
        narration_parts = [section_title + ".", explanation]
        if points:
            narration_parts.append("The main points are " + _spoken_list(points[:5]) + ".")
        if worked_text:
            narration_parts.append("Consider this example. " + worked_text)
        if activity:
            narration_parts.append("A useful classroom activity is: " + activity)
        if check:
            narration_parts.append("Check your understanding: " + check)
        narration = " ".join(part for part in narration_parts if part)
        if narration:
            scenes.append(
                VideoScene(
                    section=f"{section.get('minutes', '')} MINUTE LESSON SEGMENT".strip(),
                    title=section_title,
                    bullets=bullets,
                    narration=narration,
                )
            )

    case_study = result.get("case_study")
    if isinstance(case_study, dict) and case_study:
        scenario = _text(case_study.get("scenario"))
        questions = _items(case_study.get("questions"))
        scenes.append(
            VideoScene(
                section="APPLIED CASE",
                title=_text(case_study.get("title"), "Apply the concept"),
                bullets=tuple(([scenario] if scenario else []) + questions[:4]),
                narration=" ".join(
                    part
                    for part in (
                        "Now apply the topic to a practical case.",
                        scenario,
                        "Consider these questions: " + _spoken_list(questions) + "."
                        if questions
                        else "",
                        _text(case_study.get("teaching_notes")),
                    )
                    if part
                ),
            )
        )

    review = _items(result.get("discussion_questions"))[:3]
    if not review:
        review = objectives[:3]
    scenes.append(
        VideoScene(
            section="REVIEW",
            title="Key takeaways and reflection",
            bullets=tuple(review),
            narration=(
                f"This concludes the explainer on {title}. "
                + ("Reflect on these questions: " + _spoken_list(review) + ". " if review else "")
                + "Review the accompanying teaching package for full notes, assessments, and sources."
            ),
        )
    )
    return _apply_narration_budget(scenes, max_minutes=max_minutes)


def estimated_video_seconds(scenes: list[VideoScene]) -> float:
    """Return a conservative narration-based duration estimate used by tests and telemetry."""
    words = sum(len(scene.narration.split()) for scene in scenes)
    return max(len(scenes) * 2.0, words / WORDS_PER_MINUTE * 60)


def _apply_narration_budget(scenes: list[VideoScene], *, max_minutes: int) -> list[VideoScene]:
    # Keep a safety margin because natural pauses can make generated speech slower than word count.
    word_budget = max(120, int(max(1, max_minutes) * WORDS_PER_MINUTE * 0.82))
    remaining = word_budget
    bounded: list[VideoScene] = []
    for scene in scenes:
        if remaining <= 0:
            break
        words = scene.narration.split()
        allotted = min(len(words), remaining, 420)
        narration = " ".join(words[:allotted]).strip()
        if allotted < len(words):
            narration = narration.rstrip(" ,;:") + "."
        if narration:
            bounded.append(replace(scene, narration=narration[:MAX_TTS_CHARACTERS]))
            remaining -= allotted
    return bounded


def _create_narration(text: str, path: Path) -> None:
    if settings.video_tts_provider == "elevenlabs":
        _create_elevenlabs_narration(text, path)
        return

    client = create_openai_client(api_key=settings.openai_api_key)
    with client.audio.speech.with_streaming_response.create(
        model=settings.video_tts_model,
        voice=settings.video_tts_voice,
        input=text[:MAX_TTS_CHARACTERS],
        instructions=(
            "Speak as a clear, calm university lecturer. Use natural pauses around definitions, "
            "examples, and questions. Read mathematical notation carefully."
        ),
        response_format="wav",
        speed=settings.video_tts_speed,
    ) as response:
        response.stream_to_file(path)


def _create_elevenlabs_narration(text: str, path: Path) -> None:
    """Generate ElevenLabs MP3 narration and convert it to the renderer's WAV format."""
    voice_id = settings.elevenlabs_voice_id.strip()
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID must be configured for ElevenLabs narration.")
    endpoint = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        f"?output_format={settings.elevenlabs_output_format}"
    )
    payload = json.dumps(
        {
            "text": text[:MAX_TTS_CHARACTERS],
            "model_id": settings.elevenlabs_model_id,
            "voice_settings": {
                "stability": settings.elevenlabs_stability,
                "similarity_boost": settings.elevenlabs_similarity_boost,
                "style": 0.15,
                "use_speaker_boost": True,
                "speed": settings.video_tts_speed,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
            "xi-api-key": settings.elevenlabs_api_key or "",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            encoded_audio = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-600:]
        raise RuntimeError(f"ElevenLabs narration failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to ElevenLabs: {exc.reason}") from exc
    if not encoded_audio:
        raise RuntimeError("ElevenLabs returned an empty narration response.")
    encoded_path = path.with_suffix(".elevenlabs.mp3")
    encoded_path.write_bytes(encoded_audio)
    try:
        _run_ffmpeg(
            _ffmpeg_executable(),
            "-i",
            str(encoded_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "1",
            str(path),
        )
    finally:
        encoded_path.unlink(missing_ok=True)


def _render_scene_frame(scene: VideoScene, path: Path, *, index: int, total: int) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), "#07152E")
    draw = ImageDraw.Draw(image)
    accent = SCENE_ACCENTS[(index - 1) % len(SCENE_ACCENTS)]
    for y in range(FRAME_HEIGHT):
        ratio = y / FRAME_HEIGHT
        draw.line(
            (0, y, FRAME_WIDTH, y),
            fill=(7 + int(10 * ratio), 21 + int(20 * ratio), 46 + int(32 * ratio)),
        )
    draw.ellipse((930, -210, 1430, 290), fill="#123D75")
    draw.ellipse((-230, 510, 310, 1050), fill="#0E315F")
    draw.rounded_rectangle((54, 42, 1226, 662), radius=30, fill="#F9FBFF")
    draw.rectangle((54, 42, 1226, 54), fill=accent)
    draw.text((84, 72), scene.section.upper(), font=_font(18, bold=True), fill=accent)
    draw.text((1120, 74), f"{index:02d}/{total:02d}", font=_font(17, bold=True), fill="#667386")

    if scene.visual_type == "title":
        _draw_title_visual(draw, scene)
    elif scene.visual_type == "comparison":
        _draw_comparison_visual(draw, scene)
    elif scene.visual_type in {"process", "timeline"}:
        _draw_sequence_visual(draw, scene)
    elif scene.visual_type == "concept_map":
        _draw_concept_map_visual(draw, scene)
    elif scene.visual_type in {"worked_example", "case_study"}:
        _draw_example_visual(draw, scene)
    elif scene.visual_type == "formula":
        _draw_formula_visual(draw, scene)
    else:
        _draw_key_points_visual(draw, scene)

    _draw_takeaway(draw, scene.takeaway or _summary_from_narration(scene.narration))
    progress_width = int(1110 * index / max(1, total))
    draw.rounded_rectangle((84, 638, 1194, 644), radius=3, fill="#DDE5F0")
    draw.rounded_rectangle((84, 638, 84 + progress_width, 644), radius=3, fill=accent)
    image.save(path, format="PNG")


def _draw_wrapped(
    draw,
    text: str,
    box: tuple[int, int, int, int],
    *,
    size: int,
    color: str,
    bold: bool = False,
    max_lines: int = 4,
    spacing: int = 8,
) -> int:
    x1, y1, x2, _ = box
    font = _font(size, bold=bold)
    average = max(8, int((x2 - x1) / max(8, size * 0.56)))
    lines = textwrap.wrap(_display_text(text), width=average)[:max_lines]
    y = y1
    for line in lines:
        draw.text((x1, y), line, font=font, fill=color)
        y += size + spacing
    return y


def _draw_scene_title(draw, scene: VideoScene) -> int:
    return _draw_wrapped(
        draw,
        scene.title,
        (84, 106, 1190, 205),
        size=36,
        color="#10233F",
        bold=True,
        max_lines=2,
        spacing=8,
    )


def _draw_title_visual(draw, scene: VideoScene) -> None:
    title_bottom = _draw_wrapped(
        draw,
        scene.title,
        (90, 128, 1130, 315),
        size=50,
        color="#10233F",
        bold=True,
        max_lines=3,
        spacing=10,
    )
    elements = list(scene.visual_elements or scene.bullets)[:3]
    x = 92
    for element in elements:
        width = min(330, max(190, len(_text(element)) * 11 + 44))
        draw.rounded_rectangle(
            (x, title_bottom + 28, x + width, title_bottom + 82), radius=18, fill="#E9F1FF"
        )
        _draw_wrapped(
            draw,
            element,
            (x + 18, title_bottom + 44, x + width - 14, title_bottom + 78),
            size=17,
            color="#2459B8",
            bold=True,
            max_lines=1,
        )
        x += width + 16


def _draw_comparison_visual(draw, scene: VideoScene) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = list(scene.visual_elements or scene.bullets)
    midpoint = max(1, (len(elements) + 1) // 2)
    columns = [elements[:midpoint], elements[midpoint:]]
    labels = list(scene.bullets)[:2] or ["First view", "Second view"]
    for column, left in zip(columns, (84, 650), strict=False):
        right = left + 546
        draw.rounded_rectangle(
            (left, title_bottom + 22, right, 548),
            radius=22,
            fill="#EEF4FF",
            outline="#B9CFF5",
            width=2,
        )
        label = labels[0 if left == 84 else min(1, len(labels) - 1)]
        _draw_wrapped(
            draw,
            label,
            (left + 24, title_bottom + 44, right - 20, title_bottom + 92),
            size=21,
            color="#2459B8",
            bold=True,
            max_lines=1,
        )
        y = title_bottom + 105
        for item in column[:3]:
            draw.ellipse((left + 26, y + 7, left + 38, y + 19), fill="#2B6DE8")
            y = (
                _draw_wrapped(
                    draw,
                    item,
                    (left + 52, y, right - 22, y + 75),
                    size=20,
                    color="#253247",
                    max_lines=2,
                )
                + 14
            )


def _draw_sequence_visual(draw, scene: VideoScene) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = list(scene.visual_elements or scene.bullets)[:5]
    count = max(1, len(elements))
    available = 1090
    card_width = min(196, (available - (count - 1) * 18) // count)
    y = title_bottom + 55
    for position, element in enumerate(elements):
        x = 90 + position * (card_width + 18)
        if position:
            draw.line((x - 18, y + 85, x, y + 85), fill="#7EA7EB", width=5)
        draw.rounded_rectangle(
            (x, y, x + card_width, y + 180), radius=20, fill="#FFFFFF", outline="#B9CFF5", width=3
        )
        draw.ellipse((x + 18, y + 18, x + 60, y + 60), fill="#2B6DE8")
        draw.text((x + 32, y + 26), str(position + 1), font=_font(18, bold=True), fill="#FFFFFF")
        _draw_wrapped(
            draw,
            element,
            (x + 18, y + 78, x + card_width - 14, y + 164),
            size=18,
            color="#253247",
            bold=True,
            max_lines=3,
        )


def _draw_concept_map_visual(draw, scene: VideoScene) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    center = (640, title_bottom + 190)
    positions = [
        (105, title_bottom + 55),
        (865, title_bottom + 55),
        (105, title_bottom + 275),
        (865, title_bottom + 275),
    ]
    items_and_positions = list(
        zip(list(scene.visual_elements or scene.bullets)[:4], positions, strict=False)
    )

    # Connect only the shape boundaries. Drawing centre-to-centre lines made the
    # connectors cut through both the central label and the surrounding cards.
    for _, (x, y) in items_and_positions:
        start, end = _concept_connector_points(center, (120, 64), (x, y, x + 260, y + 116))
        draw.line((*start, *end), fill="#9DB9E8", width=4)

    for item, (x, y) in items_and_positions:
        draw.rounded_rectangle(
            (x, y, x + 260, y + 116), radius=18, fill="#EEF4FF", outline="#B9CFF5", width=2
        )
        _draw_wrapped(
            draw,
            item,
            (x + 18, y + 22, x + 242, y + 100),
            size=19,
            color="#253247",
            bold=True,
            max_lines=3,
        )

    draw.ellipse((520, center[1] - 64, 760, center[1] + 64), fill="#2B6DE8")
    _draw_centered_wrapped(
        draw,
        scene.title,
        (542, center[1] - 47, 738, center[1] + 47),
        size=16,
        color="#FFFFFF",
        bold=True,
        max_lines=3,
        spacing=4,
    )


def _concept_connector_points(
    center: tuple[int, int],
    radii: tuple[int, int],
    card: tuple[int, int, int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return connector endpoints on an ellipse and rectangular card boundary."""
    import math

    center_x, center_y = center
    radius_x, radius_y = radii
    left, top, right, bottom = card
    target_x = (left + right) / 2
    target_y = (top + bottom) / 2
    delta_x = target_x - center_x
    delta_y = target_y - center_y

    ellipse_scale = 1 / math.sqrt(
        (delta_x / max(1, radius_x)) ** 2 + (delta_y / max(1, radius_y)) ** 2
    )
    start = (
        round(center_x + delta_x * ellipse_scale),
        round(center_y + delta_y * ellipse_scale),
    )

    half_width = max(1.0, (right - left) / 2)
    half_height = max(1.0, (bottom - top) / 2)
    card_scale = 1 / max(abs(delta_x) / half_width, abs(delta_y) / half_height)
    end = (
        round(target_x - delta_x * card_scale),
        round(target_y - delta_y * card_scale),
    )
    return start, end


def _draw_centered_wrapped(
    draw,
    text: str,
    box: tuple[int, int, int, int],
    *,
    size: int,
    color: str,
    bold: bool = False,
    max_lines: int = 3,
    spacing: int = 4,
) -> None:
    """Fit and centre a short label without clipping it inside a diagram node."""
    x1, y1, x2, y2 = box
    font = _font(size, bold=bold)
    words = _display_text(text).split()
    lines: list[str] = []
    while words and len(lines) < max_lines:
        line_words: list[str] = []
        while words:
            candidate = " ".join((*line_words, words[0]))
            bounds = draw.textbbox((0, 0), candidate, font=font)
            if line_words and bounds[2] - bounds[0] > x2 - x1:
                break
            line_words.append(words.pop(0))
        lines.append(" ".join(line_words))
    if words and lines:
        suffix = "…"
        while lines[-1]:
            bounds = draw.textbbox((0, 0), lines[-1] + suffix, font=font)
            if bounds[2] - bounds[0] <= x2 - x1:
                break
            lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
        lines[-1] += suffix

    line_height = size + spacing
    y = y1 + max(0, ((y2 - y1) - len(lines) * line_height + spacing) // 2)
    for line in lines:
        bounds = draw.textbbox((0, 0), line, font=font)
        width = bounds[2] - bounds[0]
        draw.text((x1 + ((x2 - x1) - width) / 2, y), line, font=font, fill=color)
        y += line_height


def _draw_example_visual(draw, scene: VideoScene) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = list(scene.visual_elements or scene.bullets)
    left_text = elements[0] if elements else "Problem or situation"
    right_text = (
        "\n".join(elements[1:]) if len(elements) > 1 else _summary_from_narration(scene.narration)
    )
    for left, label, body, fill in (
        (84, "SITUATION", left_text, "#FFF5E6"),
        (650, "EXPLANATION", right_text, "#EAF7F2"),
    ):
        draw.rounded_rectangle((left, title_bottom + 30, left + 546, 550), radius=22, fill=fill)
        draw.text((left + 24, title_bottom + 55), label, font=_font(17, bold=True), fill="#2B6DE8")
        _draw_wrapped(
            draw,
            body,
            (left + 24, title_bottom + 96, left + 516, 525),
            size=22,
            color="#253247",
            bold=True,
            max_lines=7,
            spacing=10,
        )


def _draw_formula_visual(draw, scene: VideoScene) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = list(scene.visual_elements or scene.bullets)
    formula = elements[0] if elements else scene.title
    draw.rounded_rectangle(
        (110, title_bottom + 32, 1170, title_bottom + 172), radius=24, fill="#10233F"
    )
    _draw_math_formula(
        draw,
        formula,
        (150, title_bottom + 72, 1130, title_bottom + 150),
        size=34,
        color="#FFFFFF",
    )
    y = title_bottom + 220
    for item in elements[1:5] or list(scene.bullets)[:4]:
        draw.ellipse((126, y + 8, 138, y + 20), fill="#2B6DE8")
        y = (
            _draw_wrapped(draw, item, (158, y, 1130, y + 62), size=21, color="#253247", max_lines=2)
            + 10
        )


_SUBSCRIPT_CHARACTERS = str.maketrans(
    {
        "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4",
        "₅": "_5", "₆": "_6", "₇": "_7", "₈": "_8", "₉": "_9",
        "ₐ": "_a", "ₑ": "_e", "ₕ": "_h", "ᵢ": "_i", "ⱼ": "_j",
        "ₖ": "_k", "ₗ": "_l", "ₘ": "_m", "ₙ": "_n", "ₒ": "_o",
        "ₚ": "_p", "ᵣ": "_r", "ₛ": "_s", "ₜ": "_t", "ₓ": "_x",
        "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4",
        "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
        "ⁱ": "^i", "ⁿ": "^n", "⁺": "+", "⁻": "-", "₊": "+", "₋": "-",
    }
)


def _display_text(value: Any) -> str:
    """Return portable on-screen text for fonts available on Windows and Linux.

    Mathematical sub/superscript glyphs and styled Unicode letters are common
    in model output, but Arial and some server fonts render them as empty boxes.
    The ASCII notation remains mathematically readable in every generated frame.
    """
    text = _text(value).translate(_SUBSCRIPT_CHARACTERS)
    text = unicodedata.normalize("NFKC", text)
    latex_replacements = {
        r"\times": "×",
        r"\cdot": "·",
        r"\sum": "Σ",
        r"\sqrt": "√",
        r"\leq": "≤",
        r"\geq": "≥",
        r"\neq": "≠",
        r"\approx": "≈",
    }
    for source, replacement in latex_replacements.items():
        text = text.replace(source, replacement)
    text = re.sub(r"\\(?:mathrm|mathbf|text)\{([^{}]+)\}", r"\1", text)
    return text


def _math_tokens(value: Any) -> list[tuple[str, str]]:
    """Split portable formula text into normal, subscript, and superscript runs."""
    text = _display_text(value)
    tokens: list[tuple[str, str]] = []
    normal: list[str] = []

    def flush_normal() -> None:
        if normal:
            tokens.append(("".join(normal), "normal"))
            normal.clear()

    index = 0
    while index < len(text):
        marker = text[index]
        if marker not in {"_", "^"} or index + 1 >= len(text):
            normal.append(marker)
            index += 1
            continue
        flush_normal()
        level = "subscript" if marker == "_" else "superscript"
        index += 1
        if text[index] == "{":
            end = text.find("}", index + 1)
            if end != -1:
                tokens.append((text[index + 1 : end], level))
                index = end + 1
                continue
        match = re.match(r"[A-Za-z0-9]+", text[index:])
        if match:
            tokens.append((match.group(0), level))
            index += len(match.group(0))
        else:
            tokens.append((text[index], level))
            index += 1
    flush_normal()
    return tokens


def _draw_math_formula(
    draw,
    formula: Any,
    box: tuple[int, int, int, int],
    *,
    size: int,
    color: str,
) -> None:
    """Draw readable inline mathematics with real baseline offsets and no missing glyphs."""
    x1, y1, x2, y2 = box
    tokens = _math_tokens(formula)
    selected_size = size
    while selected_size > 22:
        main_font = _font(selected_size, bold=True)
        small_font = _font(max(14, round(selected_size * 0.68)), bold=True)
        width = sum(
            draw.textlength(text, font=main_font if level == "normal" else small_font)
            for text, level in tokens
        )
        if width <= x2 - x1:
            break
        selected_size -= 2
    main_font = _font(selected_size, bold=True)
    small_font = _font(max(14, round(selected_size * 0.68)), bold=True)
    width = sum(
        draw.textlength(text, font=main_font if level == "normal" else small_font)
        for text, level in tokens
    )
    x = x1 + max(0, ((x2 - x1) - width) / 2)
    y = y1 + max(0, ((y2 - y1) - selected_size) / 2) - 4
    for text, level in tokens:
        font = main_font if level == "normal" else small_font
        offset = 0
        if level == "subscript":
            offset = round(selected_size * 0.40)
        elif level == "superscript":
            offset = -round(selected_size * 0.24)
        draw.text((x, y + offset), text, font=font, fill=color)
        x += draw.textlength(text, font=font)


def _draw_key_points_visual(draw, scene: VideoScene) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = list(scene.visual_elements or scene.bullets)[:5]
    y = title_bottom + 22
    for position, item in enumerate(elements):
        draw.rounded_rectangle(
            (90, y, 1188, y + 68), radius=16, fill="#EEF4FF" if position % 2 == 0 else "#F1F7F4"
        )
        draw.text((112, y + 20), f"{position + 1:02d}", font=_font(18, bold=True), fill="#2B6DE8")
        _draw_wrapped(
            draw,
            item,
            (165, y + 18, 1150, y + 58),
            size=20,
            color="#253247",
            bold=True,
            max_lines=1,
        )
        y += 78


def _draw_takeaway(draw, takeaway: str) -> None:
    text = _text(takeaway)
    if not text:
        return
    draw.rounded_rectangle((84, 568, 1194, 624), radius=16, fill="#10233F")
    draw.text((104, 586), "KEY IDEA", font=_font(14, bold=True), fill="#78A9FF")
    _draw_wrapped(
        draw, text, (205, 581, 1168, 616), size=17, color="#FFFFFF", bold=True, max_lines=1
    )


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _render_segment(
    ffmpeg: str,
    frame: Path,
    audio: Path,
    output: Path,
    duration: float,
    *,
    scene_index: int = 1,
    tempo: float = 1.0,
) -> None:
    fade_out = max(0.0, duration - 0.35)
    if scene_index % 2:
        x_expression = "iw/2-(iw/zoom/2)"
        y_expression = "ih/2-(ih/zoom/2)"
    else:
        x_expression = "min((iw-iw/zoom)*(on/(30*12)),iw-iw/zoom)"
        y_expression = "min((ih-ih/zoom)*(on/(30*12)),ih-ih/zoom)"
    audio_seconds = _wav_duration(audio)
    audio_padding = max(0.0, duration - audio_seconds / tempo)
    _run_ffmpeg(
        ffmpeg,
        "-loop",
        "1",
        "-i",
        str(frame),
        "-i",
        str(audio),
        "-t",
        f"{duration:.3f}",
        "-r",
        "30",
        "-vf",
        (
            "zoompan=z='min(zoom+0.00018,1.055)':"
            f"x='{x_expression}':y='{y_expression}':d=1:s=1280x720:fps=30,"
            f"fade=t=in:st=0:d=0.30,fade=t=out:st={fade_out:.3f}:d=0.35"
        ),
        "-af",
        f"atempo={tempo:.4f},apad=pad_dur={audio_padding:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-shortest",
        str(output),
    )


def _concatenate_segments(
    ffmpeg: str, segments: list[Path], output: Path, maximum_seconds: int
) -> None:
    inputs = [argument for segment in segments for argument in ("-i", str(segment))]
    prepared_streams: list[str] = []
    concat_inputs: list[str] = []
    for index in range(len(segments)):
        prepared_streams.extend(
            (
                f"[{index}:v]setpts=PTS-STARTPTS[v{index}]",
                f"[{index}:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS[a{index}]",
            )
        )
        concat_inputs.extend((f"[v{index}]", f"[a{index}]"))
    filter_complex = (
        ";".join(prepared_streams)
        + ";"
        + "".join(concat_inputs)
        + f"concat=n={len(segments)}:v=1:a=1[outv][outa]"
    )
    _run_ffmpeg(
        ffmpeg,
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-t",
        str(maximum_seconds),
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output),
    )


def _media_duration(ffmpeg: str, path: Path) -> float:
    """Read a media duration with the bundled FFmpeg, avoiding a separate ffprobe dependency."""
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    details = "\n".join(part for part in (completed.stderr, completed.stdout) if part)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", details)
    if not match:
        raise RuntimeError("Video verification failed because its duration could not be read.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _verify_scene_progression(ffmpeg: str, path: Path, durations: list[float]) -> None:
    """Reject a video that encodes a long first scene but never reaches later scenes."""
    if len(durations) < 3:
        raise RuntimeError("A teaching video needs at least three distinct scenes.")
    from PIL import Image, ImageChops, ImageStat

    samples = []
    elapsed = 0.0
    selected = {0, len(durations) // 2, len(durations) - 1}
    for index, duration in enumerate(durations):
        if index in selected:
            position = elapsed + min(duration / 2, max(0.5, duration - 0.5))
            completed = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{position:.3f}",
                    "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
                ],
                check=False,
                capture_output=True,
            )
            if completed.returncode or not completed.stdout:
                raise RuntimeError(f"Video verification could not read scene {index + 1}.")
            with Image.open(BytesIO(completed.stdout)) as frame:
                samples.append(frame.convert("RGB").crop((70, 65, 1210, 555)))
        elapsed += duration
    for first, second in pairwise(samples):
        difference = ImageChops.difference(first, second)
        if sum(ImageStat.Stat(difference).mean) / 3 < 1.5:
            raise RuntimeError("Video verification found no visible change between planned scenes.")


def _run_ffmpeg(ffmpeg: str, *arguments: str) -> None:
    try:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Unknown FFmpeg error").strip()
        raise RuntimeError(f"Video encoder failed: {detail[-800:]}") from exc


def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError(
            "Video generation requires imageio-ffmpeg. Reinstall the project dependencies."
        ) from exc


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / max(1, audio.getframerate())
    except wave.Error as exc:
        raise RuntimeError("The narration service returned invalid WAV audio.") from exc


def _trim_narration_silence(path: Path) -> float:
    """Measure speech-bearing PCM and remove provider-added silence at the end of a WAV."""
    replacement: Path | None = None
    try:
        with wave.open(str(path), "rb") as source:
            if source.getsampwidth() != 2:
                raise RuntimeError("The narration service must return 16-bit PCM WAV audio.")
            sample_rate = source.getframerate()
            declared_frames = source.getnframes()
            actual_frames = 0
            window_frames = max(1, int(sample_rate * 0.2))
            peaks: list[int] = []
            while samples := source.readframes(window_frames):
                actual_frames += len(samples) // (source.getnchannels() * source.getsampwidth())
                values = array("h")
                values.frombytes(samples)
                if sys.byteorder != "little":
                    values.byteswap()
                peaks.append(max((abs(value) for value in values), default=0))
            if not peaks:
                return 0.0
            threshold = max(160, int(max(peaks) * 0.0125))
            active = [index for index, peak in enumerate(peaks) if peak >= threshold]
            if not active:
                return 0.0
            keep_frames = min(actual_frames, (active[-1] + 1) * window_frames + int(sample_rate * 0.4))
            duration = keep_frames / sample_rate
            if actual_frames - keep_frames < sample_rate and declared_frames == actual_frames:
                return actual_frames / sample_rate
            replacement = path.with_suffix(".trimmed.wav")
            try:
                source.rewind()
                with wave.open(str(replacement), "wb") as trimmed:
                    # Streaming TTS can declare an open-ended WAV data size. Copying
                    # that value into a new RIFF header overflows its 32-bit length.
                    trimmed.setparams(source.getparams()._replace(nframes=keep_frames))
                    remaining = keep_frames
                    while remaining:
                        count = min(remaining, sample_rate)
                        chunk = source.readframes(count)
                        if not chunk:
                            raise RuntimeError("Narration audio ended before its measured duration.")
                        trimmed.writeframesraw(chunk)
                        remaining -= len(chunk) // (source.getnchannels() * source.getsampwidth())
            except BaseException:
                replacement.unlink(missing_ok=True)
                raise
        if replacement is not None:
            replacement.replace(path)
        return duration
    except wave.Error as exc:
        raise RuntimeError("The narration service returned invalid WAV audio.") from exc


def _items(value: Any, *, keys: tuple[str, ...] = ()) -> list[str]:
    values = value if isinstance(value, list) else ([] if value is None else [value])
    output: list[str] = []
    for item in values:
        if isinstance(item, dict) and keys:
            selected = next((item.get(key) for key in keys if item.get(key)), item)
            text = _text(selected)
        else:
            text = _text(item)
        if text:
            output.append(text)
    return output


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        cleaned = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", value)
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip() or fallback
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = _text(item)
            if text:
                parts.append(f"{key.replace('_', ' ')}: {text}")
        return ". ".join(parts) or fallback
    if isinstance(value, (list, tuple)):
        return "; ".join(text for item in value if (text := _text(item))) or fallback
    return str(value)


def _labelled(label: str, value: Any) -> str:
    text = _text(value)
    return f"{label}: {text}" if text else ""


def _spoken_list(values: list[str]) -> str:
    cleaned = [value.rstrip(".") for value in values if value]
    if len(cleaned) < 2:
        return cleaned[0] if cleaned else ""
    return ", ".join(cleaned[:-1]) + ", and " + cleaned[-1]


def _summary_from_narration(narration: str) -> str:
    words = narration.split()
    return " ".join(words[:34]) + ("…" if len(words) > 34 else "")
