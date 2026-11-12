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

from app.agents.plain_language import polish_text
from app.agents.relevance import storyboard_relevance_issues
from app.artifacts import animations, cinematic, illustrations
from app.artifacts import captions as caption_tools
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
    "cycle",
    "illustration",
    "animation",
    "worked_example",
    "formula",
    "case_study",
    "key_points",
    "recap",
)

# Phrases that only belong in the teacher reference. The audience-facing video must not contain them.
_TEACHER_ONLY_PATTERN = re.compile(
    r"\bLO\d+\b|bloom'?s?\s+(?:taxonomy|level)|teaching\s+tip|facilitation\s+note|"
    r"professor\s+(?:note|guidance|review)|expected\s+answer|answer\s+key",
    re.IGNORECASE,
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
                    "illustration": {"type": "string"},
                    "animation": {"type": "string"},
                    "cinematic": {"type": "string"},
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
                    "illustration",
                    "animation",
                    "cinematic",
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
    illustration: str = ""  # picture description for the image model ("" for diagram scenes)
    image_path: str = ""  # generated illustration, when available
    animation: str = ""  # what moves on screen, for the Manim coder ("" for other scenes)
    cinematic: str = ""  # gentle motion for the scene's picture, for Veo ("" for most scenes)
    clip_path: str = ""  # rendered animation or motion clip, when available


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
        # Illustrations are optional: any scene without an image keeps its drawn diagram.
        topic = _text(result.get("title"), "this topic")
        scenes = illustrations.attach_illustrations(scenes, workspace, topic=topic)
        # Motion clips take minutes, so they generate while the narration is recorded.
        pending_motion = cinematic.start_clips(scenes, workspace, topic=topic)
        try:
            prepared: list[tuple[VideoScene, Path, Path, float]] = []
            scene_stages: list[list[Path]] = []
            narration_seconds = 0.0
            for index, scene in enumerate(scenes, start=1):
                audio = workspace / f"scene-{index:02d}.wav"
                stages = render_scene_stages(scene, workspace, index=index, total=len(scenes))
                frame = stages[-1]
                scene_stages.append(stages)
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
            end_frames = _attach_animations(
                prepared, scene_stages, scene_durations, workspace, tempo=tempo, ffmpeg=ffmpeg
            )
            _attach_motion_clips(pending_motion, prepared, end_frames, workspace, ffmpeg=ffmpeg)
            segments: list[Path] = []
            previous_frame: Path | None = None
            subtitle_entries: list[tuple[float, float, str]] = []
            elapsed = 0.0
            for index, ((scene, frame, audio, audio_seconds), duration, stages) in enumerate(
                zip(prepared, scene_durations, scene_stages, strict=True), start=1
            ):
                segment = workspace / f"scene-{index:02d}.mp4"
                placement = clip_placement(scene)
                scene_captions = _scene_captions(
                    scene, workspace, index=index, speech_seconds=min(duration, audio_seconds / tempo)
                )
                subtitle_entries.extend(
                    (elapsed + start, elapsed + end, text) for start, end, text, _ in scene_captions
                )
                elapsed += duration
                _render_segment(
                    ffmpeg,
                    frame,
                    audio,
                    segment,
                    duration,
                    scene_index=index,
                    tempo=tempo,
                    stages=stages,
                    previous_frame=previous_frame,
                    is_last=index == len(prepared),
                    visual_type=scene.visual_type,
                    captions=[(start, end, image) for start, end, _, image in scene_captions],
                    clip=Path(scene.clip_path) if scene.clip_path else None,
                    clip_box=placement[0],
                    clip_radius=placement[1],
                    clip_max_stretch=placement[2],
                )
                segments.append(segment)
                previous_frame = end_frames.get(index - 1, frame)

            if len(segments) != len(scenes):
                raise RuntimeError("Video rendering did not include every storyboard scene.")
            rendered = workspace / "verified-explainer.mp4"
            subtitles = (
                caption_tools.write_srt(subtitle_entries, workspace / "captions.srt")
                if subtitle_entries
                else None
            )
            _concatenate_segments(ffmpeg, segments, rendered, maximum_seconds, subtitles=subtitles)
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
        finally:
            pending_motion.close()

    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("Video rendering did not produce a playable MP4 file.")
    return path


def _attach_animations(
    prepared: list[tuple[VideoScene, Path, Path, float]],
    scene_stages: list[list[Path]],
    scene_durations: list[float],
    workspace: Path,
    *,
    tempo: float,
    ffmpeg: str,
) -> dict[int, Path]:
    """Render Manim clips for animation scenes, now that narration timings are known.

    Scenes that get a clip are redrawn with an empty space for it. Scenes whose animation fails
    keep the drawn fallback they already have. Returns, per scene index, a still of the scene's
    final moment (frame plus the clip's last image) for the next scene's entry transition.
    """
    scenes = [scene for scene, _, _, _ in prepared]
    if not any(scene.visual_type == "animation" for scene in scenes):
        return {}
    if not animations.animations_available():
        return {}
    durations: list[float] = []
    cues: list[list[tuple[float, float, str]]] = []
    for index, ((scene, _, _, audio_seconds), duration) in enumerate(
        zip(prepared, scene_durations, strict=True)
    ):
        offset = intro_seconds(duration, intro=index > 0)
        speech = min(duration, audio_seconds / tempo)
        durations.append(max(2.0, speech - offset - 0.2))
        cues.append(
            [
                (round(max(0.0, start - offset), 2), round(max(0.0, end - offset), 2), text)
                for start, end, text in caption_tools.timed_captions(scene.narration, speech)
            ]
        )
    clips = animations.build_clips(
        scenes,
        durations,
        cues,
        workspace,
        client=create_openai_client(api_key=settings.openai_api_key),
        ffmpeg=ffmpeg,
        media_duration=_media_duration,
    )
    end_frames: dict[int, Path] = {}
    for index, clip in clips.items():
        scene, _, audio, audio_seconds = prepared[index]
        scene = replace(scene, clip_path=str(clip))
        stages = render_scene_stages(scene, workspace, index=index + 1, total=len(prepared))
        prepared[index] = (scene, stages[-1], audio, audio_seconds)
        scene_stages[index] = stages
        try:
            end_frames[index] = _compose_end_frame(ffmpeg, stages[-1], scene, workspace, index + 1)
        except (RuntimeError, OSError):
            pass  # the next scene then enters from the frame without the clip's last image
    return end_frames


def _compose_end_frame(
    ffmpeg: str, frame: Path, scene: VideoScene, workspace: Path, index: int
) -> Path:
    """The scene's final still with the clip's last image in place."""
    from PIL import Image

    last = workspace / f"scene-{index:02d}-clip-last.png"
    _run_ffmpeg(ffmpeg, "-sseof", "-0.2", "-i", scene.clip_path, "-frames:v", "1", "-update", "1",
                str(last))
    output = workspace / f"scene-{index:02d}-end.png"
    box, radius, _ = clip_placement(scene)
    with Image.open(frame) as base:
        composed = base.convert("RGB")
    if not illustrations.paste_cover(composed, str(last), box, radius=radius):
        raise RuntimeError("The clip's last image could not be read.")
    composed.save(output, format="PNG")
    return output


def _attach_motion_clips(
    pending: cinematic.PendingClips,
    prepared: list[tuple[VideoScene, Path, Path, float]],
    end_frames: dict[int, Path],
    workspace: Path,
    *,
    ffmpeg: str,
) -> None:
    """Add finished Veo clips to their picture scenes; scenes without one keep the still."""
    for index, clip in pending.collect(ffmpeg=ffmpeg, media_duration=_media_duration).items():
        scene, frame, audio, audio_seconds = prepared[index]
        scene = replace(scene, clip_path=str(clip))
        prepared[index] = (scene, frame, audio, audio_seconds)
        try:
            end_frames[index] = _compose_end_frame(ffmpeg, frame, scene, workspace, index + 1)
        except (RuntimeError, OSError):
            pass


def _scene_captions(
    scene: VideoScene, workspace: Path, *, index: int, speech_seconds: float
) -> list[tuple[float, float, str, Path]]:
    """Timed caption lines for one scene, each rendered to its own overlay image."""
    if not settings.video_captions_enabled:
        return []
    entries = []
    for line, (start, end, text) in enumerate(
        caption_tools.timed_captions(scene.narration, speech_seconds)
    ):
        image = caption_tools.render_caption_image(
            text, workspace / f"caption-{index:02d}-{line:03d}.png"
        )
        entries.append((start, end, text, image))
    return entries


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
    audience_package = _audience_video_package(result)
    source_word_count = len(_text(audience_package).split())
    target_minutes = min(maximum, max(minimum, round(2 + source_word_count / 500)))
    minimum_words = max(75, round(minimum * SCRIPT_WORDS_PER_MINUTE * 0.95))
    target_words = target_minutes * SCRIPT_WORDS_PER_MINUTE
    maximum_words = max(minimum_words, round(maximum * SCRIPT_WORDS_PER_MINUTE * 1.05))
    suggested_scene_count = max(3, round(target_words / 110))

    client = create_openai_client(api_key=settings.openai_api_key)
    request_payload: dict[str, Any] = {
        "teaching_package": audience_package,
        "video_requirements": {
            "minimum_minutes": minimum,
            "target_minutes": target_minutes,
            "maximum_minutes": maximum,
            "minimum_narration_words": minimum_words,
            "target_narration_words": target_words,
            "maximum_narration_words": maximum_words,
            "suggested_scene_count": suggested_scene_count,
            "illustrations_available": illustrations.illustrations_available(),
            "animations_available": animations.animations_available(),
            "maximum_animation_scenes": max(0, settings.video_max_animations),
            "motion_clips_available": cinematic.cinematic_available()
            and illustrations.illustrations_available(),
            "maximum_motion_clips": max(0, settings.video_max_cinematic_clips),
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
        plan = _request_video_plan(
            client,
            instructions=_video_script_instructions(),
            payload=request_payload,
            maximum_words=maximum_words,
        )
        plan = _constrain_plan_narration(plan, maximum_words=maximum_words)
        plan = scrub_teacher_terms(plan, audience_package)
        last_issues = _video_plan_issues(
            plan,
            minimum_words=minimum_words,
            maximum_words=maximum_words,
            teaching_package=audience_package,
        )
        if not last_issues:
            if settings.video_script_review_enabled:
                plan = _review_video_plan(
                    client,
                    plan,
                    request_payload=request_payload,
                    minimum_words=minimum_words,
                    maximum_words=maximum_words,
                    teaching_package=audience_package,
                )
            return _plan_to_scenes(plan)
    raise RuntimeError("The teaching-video script failed quality checks: " + " ".join(last_issues))


def _plan_to_scenes(plan: dict) -> list[VideoScene]:
    def clean(value: Any, fallback: str = "") -> str:
        return polish_text(_text(value, fallback))

    return [
        VideoScene(
            section=clean(scene.get("section"), "LESSON"),
            title=clean(scene.get("title"), "Key concept"),
            bullets=tuple(polish_text(item) for item in _items(scene.get("bullets"))[:5]),
            narration=clean(scene.get("narration"))[:MAX_TTS_CHARACTERS],
            visual_type=_text(scene.get("visual_type"), "key_points"),
            visual_elements=tuple(
                polish_text(item) for item in _items(scene.get("visual_elements"))[:6]
            ),
            takeaway=clean(scene.get("takeaway")),
            illustration=_text(scene.get("illustration")),
            animation=_text(scene.get("animation")),
            cinematic=_text(scene.get("cinematic")),
        )
        for scene in plan["scenes"]
    ]


def _request_video_plan(client, *, instructions: str, payload: dict, maximum_words: int) -> dict:
    """One schema-constrained storyboard call."""
    response = client.responses.create(
        model=settings.video_script_model or settings.openai_model,
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False),
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
        return json.loads(response.output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The teaching-video script was not valid JSON.") from exc


def _review_video_plan(
    client,
    plan: dict,
    *,
    request_payload: dict,
    minimum_words: int,
    maximum_words: int,
    teaching_package: dict,
) -> dict:
    """Second pass: a reviewer rewrites the script for a complete beginner.

    The reviewed plan replaces the draft only if it still passes every storyboard check, so this
    pass can improve a video but never break one.
    """
    try:
        reviewed = _request_video_plan(
            client,
            instructions=_video_review_instructions(),
            payload={
                "teaching_package": request_payload["teaching_package"],
                "video_requirements": request_payload["video_requirements"],
                "draft_plan": plan,
            },
            maximum_words=maximum_words,
        )
        reviewed = _constrain_plan_narration(reviewed, maximum_words=maximum_words)
        reviewed = scrub_teacher_terms(reviewed, teaching_package)
    except Exception:  # noqa: BLE001 - the draft is already valid; keep it
        return plan
    issues = _video_plan_issues(
        reviewed,
        minimum_words=minimum_words,
        maximum_words=maximum_words,
        teaching_package=teaching_package,
    )
    return plan if issues else reviewed


def _audience_video_package(result: dict) -> dict:
    """Provide the planner with learner-facing material, not private facilitation notes."""
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    sections = []
    for section in result.get("lecture_sections") or []:
        if not isinstance(section, dict):
            continue
        content = section.get("content") if isinstance(section.get("content"), dict) else {}
        sections.append({
            "title": section.get("title"),
            "content": {key: content[key] for key in (
                "explanation", "key_points", "worked_example", "worked_examples",
                "common_errors", "concept_check", "source_refs", "diagram",
            ) if key in content},
        })
    case_study = result.get("case_study")
    if isinstance(case_study, dict):
        case_study = {key: case_study[key] for key in (
            "title", "scenario", "questions", "source_refs",
        ) if key in case_study}
    # Objective IDs and Bloom levels are curriculum metadata for the teacher reference only.
    objectives = [
        item.get("objective") if isinstance(item, dict) else item
        for item in result.get("learning_objectives") or []
    ]
    numerical = [
        {key: item[key] for key in ("problem", "given", "solution_steps", "final_answer") if key in item}
        for item in result.get("numerical_problems") or []
        if isinstance(item, dict)
    ]
    return {
        "title": result.get("title"),
        # The audience label tailors tone and depth; the planner must not display it on screen.
        "overview": {key: overview[key] for key in (
            "course", "audience", "difficulty", "lesson_purpose",
            "prerequisite_knowledge", "core_formulas", "evidence_base",
        ) if key in overview},
        "learning_objectives": [value for value in objectives if value],
        "lecture_sections": sections,
        "case_study": case_study or {},
        "numerical_problems": numerical,
        "discussion_questions": result.get("discussion_questions", []),
        "sources": result.get("sources", []),
    }


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


def _video_review_instructions() -> str:
    return """
You are a senior teacher reviewing an educational video script before it is recorded. The viewer
is a student who has never met this topic. Return the complete revised plan in the same schema.

Check every scene and fix what fails:
- Every technical term is defined in plain words before or as it is used.
- Every abstract idea has a concrete example or an everyday analogy.
- Each scene connects to the previous one; the whole video tells one story.
- Sentences are short and spoken naturally; remove filler, repetition and stock phrases.
- The worked example is explained step by step, saying why each step happens.
- On-screen bullets and labels are short and match what the narration says.
- Illustration descriptions are concrete pictures with no text, labels or numbers in them.
- Animation descriptions say, step by step and in narration order, what appears and moves.
- Motion descriptions (the cinematic field) describe calm movement of the same picture only.

Keep these unchanged: the facts (use only the supplied teaching package, add nothing new), the
number of scenes, each scene's visual_type, the first scene's single short sentence saying the
voice is AI-generated, and a total narration length inside the requested word range. Do not
mention the review, AI or how the video was made anywhere else.
"""


def _video_script_instructions() -> str:
    return """
You are an educational video scriptwriter. Convert the validated teaching package into a compact,
genuinely explanatory lesson for the requested students and a broader audience. Assume the viewer
has never heard of this topic. Address viewers directly as "you" in plain, everyday language:
short sentences with one idea each, familiar comparisons, and no jargon left undefined. Define
unfamiliar terms before using them; explain why each idea matters.
Do not narrate teacher instructions, facilitation notes, internal quality checks, rubric details,
or references to a professor handbook. Do not show or speak lesson-planning metadata such as
session or section timings, the audience label, Bloom's taxonomy levels, learning-objective IDs,
or raw source IDs; use overview.audience only to pitch tone and depth. Keep the academic substance
and source qualifications. Never use these words anywhere in the script: "teaching tip",
"facilitation", "professor note", "Bloom's", "learning objective ID", "LO1", "expected answer" or
"answer key"; when you pose a question, simply give the answer.

Use only information supported by the supplied teaching package. Do not introduce new statistics,
citations, formulas, claims, or examples that are not supported by it. You may reorganize and
explain supported material in clearer language.

The video must teach, not merely read headings or bullet points. Follow this teaching arc:
1. Hook: open with a familiar, everyday situation or question that makes the topic matter.
2. Intuition: give one simple analogy the viewer already understands before any formal term.
3. Concept: define the central ideas in plain words and separate easily confused ideas.
4. Mechanism: explain how or why each idea works, including assumptions and limits.
5. Worked example: walk through a supplied example step by step, saying why each step happens.
6. Misconception: name a common mistake from the package and show why it is wrong.
7. Recap: close with the few ideas to remember and one question to think about.
Link every scene to the one before it ("Now that you know X, ...") so the video feels like one
explanation, not a list of facts.

Design this as a multimedia video overview rather than a recorded slide deck. Select the visual type
that best explains each scene. Use concept maps for relationships, comparisons for contrasts,
processes or timelines for sequences, cycles for repeating loops, worked examples for applications,
formula scenes for mathematics, and recap scenes for synthesis. When a lecture section supplies a
diagram, reuse its type and node labels for that scene. Diagram elements appear on screen one at a
time, so order visual_elements in the order the narration explains them. Use at least three
distinct visual types across the video. visual_elements must contain concise labels of 1 to 4
words that can be rendered as a diagram; bullets must reinforce the explanation rather than
duplicate those labels. The first scene must use title and the last scene must use recap.

Illustrations: when video_requirements.illustrations_available is true, use the illustration
visual type for the hook, analogies and real-world context, roughly one scene in three, and
describe in the illustration field one simple picture that helps a beginner understand the scene
(concrete objects, a setting, what is happening). Never ask for text, labels, numbers, symbols or
formulas in a picture; on-screen text comes from bullets and visual_elements. Also describe a
picture for the title scene. For every other scene, and whenever illustrations_available is false,
set illustration to an empty string and do not use the illustration visual type.

Animations: when video_requirements.animations_available is true, use the animation visual type
for ideas that are clearer in motion: a graph being drawn, a formula changing step by step, a
quantity growing or shrinking, a physical process, or an algorithm moving through its steps. Use
it in at most maximum_animation_scenes scenes and never for the first or last scene. In the
animation field, describe what appears and moves on screen, step by step, in the same order as
the narration, using only facts, numbers and formulas from the teaching package (for example:
"Draw x and y axes. Plot five data points. Draw a straight line through them. Highlight the
vertical gap between one point and the line, labelled error."). The animation sits between the
title and the takeaway, so keep it to one idea. For every other scene, and whenever
animations_available is false, set animation to an empty string and do not use the animation
visual type.

Motion clips: when video_requirements.motion_clips_available is true, the picture of the title
scene, and of up to maximum_motion_clips scenes in total that use the illustration type, can be
brought to life for a few seconds. For those scenes, write in the cinematic field one sentence of
calm, natural motion for that same picture (for example: "The camera slowly pushes in while steam
rises from the cup and the leaves sway."). Describe only movement of what is already in the
picture; never ask for text, people talking, new objects or scene changes. Prefer the hook and
real-world scenes. For every other scene, and whenever motion_clips_available is false, set
cinematic to an empty string.

Each scene needs two to five short on-screen bullets, a one-sentence takeaway, and natural spoken
narration. Narration must contain complete explanations with transitions between ideas; never use
filler, slogans, repeated sentences, or classroom-management instructions as a substitute for
teaching. Speak mathematical notation in an understandable way. Respect the requested narration
word range because it controls the final duration. Use the supplied suggested_scene_count as the
target storyboard length and normally keep each scene between 25 and 180 narration words so short
and long user-selected videos both progress through visible scene changes. The speech provider's
terms require telling listeners that the voice is synthetic, so the first scene's narration must
include one short, natural sentence saying the voice is AI-generated. Mention it only once, and do
not otherwise refer to AI or to how the video was made.
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
        if visual_type == "animation" and not _text(scene.get("animation")):
            issues.append(f"Scene {index} uses the animation type but does not describe it.")
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
    leaked = _teacher_only_terms(scenes, teaching_package)
    if leaked:
        found = ", ".join(f'"{term}"' for term in dict.fromkeys(leaked))
        issues.append(
            "The storyboard contains teacher-only material (facilitation tips, Bloom levels, "
            f"objective IDs, or professor notes): {found}. Rewrite those parts for students "
            "and a broader audience without these words."
        )
    if teaching_package is not None:
        issues.extend(storyboard_relevance_issues(plan, teaching_package))
    return issues


# Teacher-facing wording that has a natural student-facing equivalent.
_TEACHER_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in (
        (r"\s*\(\s*LO\d+\s*\)", ""),
        (r"\bLO\d+\s*[:.\-\u2013]\s*", ""),
        (r"\bLO\d+\b", ""),
        (r"\bexpected\s+answer(s?)\b", r"answer\1"),
        (r"\banswer\s+key(s?)\b", "answers"),
        (r"\bteaching\s+tip(s?)\s*:\s*", ""),
        (r"\bteaching\s+tip(s?)\b", r"tip\1"),
    )
)
# Wording that only makes sense to a teacher: the whole sentence or list item is removed.
_TEACHER_SENTENCE = re.compile(
    r"bloom'?s?\s+(?:taxonomy|level)|facilitation\s+note|professor\s+(?:note|guidance|review)|"
    r"teaching\s+tip",
    re.IGNORECASE,
)


def _scrub_text(text: str, allowed: str, *, drop_sentences: bool) -> str:
    if not text:
        return text
    found = [match.group(0) for pattern in (_TEACHER_SENTENCE, *(p for p, _ in _TEACHER_REPLACEMENTS))
             for match in pattern.finditer(text)]
    if not any(term.casefold().strip() not in allowed for term in found):
        return text  # nothing to change: clean text is left exactly as written
    if drop_sentences:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        sentences = [
            sentence for sentence in sentences
            if not any(match.group(0).casefold() not in allowed
                       for match in _TEACHER_SENTENCE.finditer(sentence))
        ]
        text = " ".join(sentences)
    for pattern, replacement in _TEACHER_REPLACEMENTS:
        text = pattern.sub(
            lambda match, replacement=replacement: match.group(0)
            if match.group(0).casefold() in allowed else match.expand(replacement),
            text,
        )
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text).strip()
    return text[:1].upper() + text[1:] if text else text


def scrub_teacher_terms(plan: Any, teaching_package: dict | None) -> Any:
    """Rewrite or remove teacher-only wording so one stray phrase does not cost the video.

    Phrases that occur in the learner material itself (for example in a lesson about Bloom's
    taxonomy) are part of the subject and are left alone.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("scenes"), list):
        return plan
    allowed = _text(teaching_package).casefold() if teaching_package else ""
    scenes = []
    for scene in plan["scenes"]:
        if not isinstance(scene, dict):
            scenes.append(scene)
            continue
        scene = dict(scene)
        for key in ("section", "title", "takeaway"):
            if isinstance(scene.get(key), str):
                scene[key] = _scrub_text(scene[key], allowed, drop_sentences=False) or scene[key]
        if isinstance(scene.get("narration"), str):
            scene["narration"] = _scrub_text(scene["narration"], allowed, drop_sentences=True) \
                or scene["narration"]
        for key in ("bullets", "visual_elements"):
            items = scene.get(key)
            if not isinstance(items, list):
                continue
            kept = [
                _scrub_text(str(item), allowed, drop_sentences=False)
                for item in items
                if not any(match.group(0).casefold() not in allowed
                           for match in _TEACHER_SENTENCE.finditer(str(item)))
            ]
            scene[key] = [item for item in kept if item]
        # Storyboard rules need at least two entries per list: top a short list up from the other.
        for key, other in (("bullets", "visual_elements"), ("visual_elements", "bullets")):
            items = scene.get(key)
            if isinstance(items, list) and len(items) < 2:
                spare = [item for item in scene.get(other) or [] if item not in items]
                scene[key] = items + spare[: 2 - len(items)]
        scenes.append(scene)
    return {**plan, "scenes": scenes}


def _teacher_only_terms(scenes: list, teaching_package: dict | None) -> list[str]:
    """Return teacher-only phrases in the storyboard that the learner material does not contain.

    Phrases already present in the audience package are part of the subject itself (for example,
    a lesson about Bloom's taxonomy) and are therefore allowed.
    """
    storyboard = " ".join(
        _text(scene.get(key))
        for scene in scenes
        if isinstance(scene, dict)
        for key in ("section", "title", "bullets", "visual_elements", "takeaway", "narration")
    )
    allowed = _text(teaching_package).casefold() if teaching_package else ""
    return [
        match.group(0)
        for match in _TEACHER_ONLY_PATTERN.finditer(storyboard)
        if match.group(0).casefold() not in allowed
    ]


def build_teaching_video_scenes(result: dict, *, max_minutes: int = 15) -> list[VideoScene]:
    """Convert the teaching package into concise scenes and a bounded narration script."""
    title = _text(result.get("title"), "Teaching explainer")
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    purpose = _text(overview.get("lesson_purpose"), f"An academic overview of {title}.")
    scenes = [
        VideoScene(
            section="LESSON",
            title=title,
            bullets=tuple(
                value
                for value in (
                    _labelled("Course", overview.get("course")),
                    _labelled("Level", overview.get("difficulty")),
                )
                if value
            ),
            narration=(
                f"Welcome. In this short lesson you will learn about {title}. {purpose} "
                "The voice you hear is AI-generated."
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
        check = _text(content.get("concept_check"))
        worked = content.get("worked_example") or content.get("worked_examples")
        worked_text = _text(worked)
        bullets = tuple((points + [value for value in (worked_text, check) if value])[:5])
        narration_parts = [section_title + ".", explanation]
        if points:
            narration_parts.append("The main points are " + _spoken_list(points[:5]) + ".")
        if worked_text:
            narration_parts.append("Consider this example. " + worked_text)
        if check:
            narration_parts.append("Check your understanding: " + check)
        narration = " ".join(part for part in narration_parts if part)
        if narration:
            scenes.append(
                VideoScene(
                    section="KEY CONCEPT",
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
                + "Use the source list to explore the ideas further."
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


def _render_scene_frame(
    scene: VideoScene,
    path: Path,
    *,
    index: int,
    total: int,
    reveal: int | None = None,
    show_takeaway: bool = True,
) -> None:
    """Draw one frame. `reveal` limits how many diagram elements are drawn (None draws all)."""
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
    draw.text((84, 72), scene.section.upper(), font=_font(18, bold=True), fill=accent)
    draw.text((1120, 74), f"{index:02d}/{total:02d}", font=_font(17, bold=True), fill="#667386")

    if scene.clip_path and scene.visual_type == "animation":
        _draw_scene_title(draw, scene)  # the animation clip fills the space below the title
    elif scene.image_path and scene.visual_type == "illustration":
        _draw_illustration_scene(image, draw, scene, limit=reveal)
    elif scene.image_path and scene.visual_type == "title":
        illustrations.paste_cover(image, scene.image_path, ILLUSTRATION_TITLE_BOX)
        _draw_title_visual(draw, scene, limit=reveal, right=ILLUSTRATION_TITLE_BOX[0] - 34)
    else:
        _visual_drawer(scene)(draw, scene, limit=reveal)
    if show_takeaway:
        _draw_takeaway(draw, scene.takeaway or _summary_from_narration(scene.narration))
    progress_width = int(1110 * index / max(1, total))
    draw.rounded_rectangle((84, 638, 1194, 644), radius=3, fill="#DDE5F0")
    draw.rounded_rectangle((84, 638, 84 + progress_width, 644), radius=3, fill=accent)
    image.save(path, format="PNG")


def _visual_drawer(scene: VideoScene):
    return {
        "title": _draw_title_visual,
        "comparison": _draw_comparison_visual,
        "process": _draw_sequence_visual,
        "timeline": _draw_timeline_visual,
        "cycle": _draw_cycle_visual,
        "concept_map": _draw_concept_map_visual,
        "worked_example": _draw_example_visual,
        "case_study": _draw_example_visual,
        "formula": _draw_formula_visual,
        "illustration": _draw_illustration_fallback,  # used when no image could be generated
        "animation": _draw_illustration_fallback,  # used when no animation could be rendered
    }.get(scene.visual_type, _draw_key_points_visual)


def _scene_elements(scene: VideoScene) -> list[str]:
    return list(scene.visual_elements or scene.bullets)


def reveal_count(scene: VideoScene) -> int:
    """How many diagram elements the scene builds up one at a time."""
    elements = _scene_elements(scene)
    kind = scene.visual_type
    if kind == "title":
        return len(elements[:3])
    if kind == "comparison":
        midpoint = max(1, (len(elements) + 1) // 2)
        return len(elements[:midpoint][:3]) + len(elements[midpoint:][:3])
    if kind in {"process", "timeline"}:
        return len(elements[:5])
    if kind == "cycle":
        return len(elements[:6])
    if kind == "concept_map":
        return len(elements[:4])
    if kind in {"worked_example", "case_study"}:
        return 2
    if kind == "formula":
        return 1 + len(elements[1:5] or list(scene.bullets)[:4])
    if kind == "animation":
        # With a clip: title, a hold while the animation plays, then the takeaway.
        return 1 if scene.clip_path else len(_illustration_points(scene))
    if kind == "illustration":
        points = len(_illustration_points(scene))
        return 1 + points if scene.image_path else points
    return len(elements[:5])


ILLUSTRATION_TITLE_BOX = (716, 104, 1196, 548)


def _illustration_points(scene: VideoScene) -> list[str]:
    return list(scene.bullets or scene.visual_elements)[:4]


def _draw_illustration_fallback(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    """No picture available: show the scene's explanatory points as a numbered list."""
    _draw_key_points_visual(
        draw, replace(scene, visual_elements=tuple(_illustration_points(scene))), limit=limit
    )


def illustration_picture_box(scene: VideoScene) -> tuple[int, int, int, int]:
    """Where an illustration scene's picture sits; it starts below the (one- or two-line) title."""
    from PIL import Image, ImageDraw

    title_bottom = _draw_scene_title(ImageDraw.Draw(Image.new("RGB", (8, 8))), scene)
    return (84, title_bottom + 16, 690, 548)


PICTURE_RADIUS = 22
MOTION_MAX_STRETCH = 1.6  # a motion clip may be slowed down this much to cover more narration


def clip_placement(scene: VideoScene) -> tuple[tuple[int, int, int, int], int, float]:
    """Box, corner radius and maximum slow-down for the scene's clip."""
    if scene.visual_type == "animation":
        return animations.ANIMATION_BOX, 0, 1.0
    if scene.visual_type == "title":
        return ILLUSTRATION_TITLE_BOX, PICTURE_RADIUS, MOTION_MAX_STRETCH
    return illustration_picture_box(scene), PICTURE_RADIUS, MOTION_MAX_STRETCH


def _draw_illustration_scene(image, draw, scene: VideoScene, *, limit: int | None = None) -> None:
    """Picture on the left, numbered points appearing one at a time on the right."""
    title_bottom = _draw_scene_title(draw, scene)
    if _shown(0, limit):
        illustrations.paste_cover(image, scene.image_path, illustration_picture_box(scene))
    y = title_bottom + 26
    for position, item in enumerate(_illustration_points(scene), start=1):
        if not _shown(position, limit):
            continue
        draw.ellipse((720, y + 2, 756, y + 38), fill="#2B6DE8")
        draw.text((732, y + 9), str(position), font=_font(18, bold=True), fill="#FFFFFF")
        y = _draw_wrapped(
            draw, item, (772, y + 6, 1188, y + 110), size=21, color="#253247", bold=True,
            max_lines=3,
        ) + 22


def render_scene_stages(
    scene: VideoScene, workspace: Path, *, index: int, total: int
) -> list[Path]:
    """Frames that build the scene up: title, each diagram element in turn, then the takeaway."""
    count = reveal_count(scene)
    stages: list[Path] = []
    for reveal in range(count + 1):
        stage = workspace / f"scene-{index:02d}-stage-{reveal:02d}.png"
        _render_scene_frame(
            scene, stage, index=index, total=total, reveal=reveal, show_takeaway=False
        )
        stages.append(stage)
    final = workspace / f"scene-{index:02d}.png"
    _render_scene_frame(scene, final, index=index, total=total)
    stages.append(final)
    return stages


def _shown(position: int, limit: int | None) -> bool:
    return limit is None or position < limit


def _arrow_head(draw, tip: tuple[float, float], angle: float, *, size: int, fill: str) -> None:
    import math

    left = (tip[0] - size * math.cos(angle - 0.45), tip[1] - size * math.sin(angle - 0.45))
    right = (tip[0] - size * math.cos(angle + 0.45), tip[1] - size * math.sin(angle + 0.45))
    draw.polygon([tip, left, right], fill=fill)


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


def _draw_title_visual(
    draw, scene: VideoScene, *, limit: int | None = None, right: int = 1130
) -> None:
    title_bottom = _draw_wrapped(
        draw,
        scene.title,
        (90, 128, right, 315),
        size=50,
        color="#10233F",
        bold=True,
        max_lines=3,
        spacing=10,
    )
    elements = _scene_elements(scene)[:3]
    x = 92
    for position, element in enumerate(elements):
        width = min(330, max(190, len(_text(element)) * 11 + 44))
        if x + width > right:
            break
        if _shown(position, limit):
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


def _draw_comparison_visual(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = _scene_elements(scene)
    midpoint = max(1, (len(elements) + 1) // 2)
    columns = [elements[:midpoint][:3], elements[midpoint:][:3]]
    labels = list(scene.bullets)[:2] or ["First view", "Second view"]
    position = 0
    for column_index, (column, left) in enumerate(zip(columns, (84, 650), strict=False)):
        right = left + 546
        draw.rounded_rectangle(
            (left, title_bottom + 22, right, 548),
            radius=22,
            fill="#EEF4FF" if column_index == 0 else "#FFF5E6",
            outline="#B9CFF5" if column_index == 0 else "#F2D3A2",
            width=2,
        )
        label = labels[0 if column_index == 0 else min(1, len(labels) - 1)]
        _draw_wrapped(
            draw,
            label,
            (left + 24, title_bottom + 44, right - 20, title_bottom + 92),
            size=21,
            color="#2459B8" if column_index == 0 else "#A4630F",
            bold=True,
            max_lines=1,
        )
        y = title_bottom + 105
        for item in column:
            if _shown(position, limit):
                draw.ellipse((left + 26, y + 7, left + 38, y + 19),
                             fill="#2B6DE8" if column_index == 0 else "#D98A1E")
                y = (
                    _draw_wrapped(
                        draw, item, (left + 52, y, right - 22, y + 75), size=20,
                        color="#253247", max_lines=2,
                    )
                    + 14
                )
            position += 1
    draw.ellipse((610, 300, 670, 360), fill="#10233F")
    draw.text((624, 316), "vs", font=_font(20, bold=True), fill="#FFFFFF")


def _draw_sequence_visual(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = _scene_elements(scene)[:5]
    count = max(1, len(elements))
    gap = 34
    card_width = min(200, (1090 - (count - 1) * gap) // count)
    total_width = count * card_width + (count - 1) * gap
    start_x = 90 + (1090 - total_width) // 2
    y = title_bottom + 55
    for position, element in enumerate(elements):
        if not _shown(position, limit):
            continue
        x = start_x + position * (card_width + gap)
        if position:
            draw.line((x - gap + 4, y + 90, x - 8, y + 90), fill="#7EA7EB", width=5)
            _arrow_head(draw, (x - 2, y + 90), 0.0, size=14, fill="#7EA7EB")
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


def _draw_timeline_visual(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = _scene_elements(scene)[:5]
    count = max(1, len(elements))
    axis_y = title_bottom + 175
    draw.line((100, axis_y, 1170, axis_y), fill="#B9CFF5", width=6)
    _arrow_head(draw, (1182, axis_y), 0.0, size=18, fill="#B9CFF5")
    slot = 1060 / count
    for position, element in enumerate(elements):
        if not _shown(position, limit):
            continue
        center_x = int(110 + slot * position + slot / 2)
        draw.ellipse((center_x - 14, axis_y - 14, center_x + 14, axis_y + 14), fill="#2B6DE8")
        above = position % 2 == 0
        top = axis_y - 150 if above else axis_y + 40
        draw.line((center_x, axis_y - 14 if above else axis_y + 14,
                   center_x, top + 110 if above else top), fill="#7EA7EB", width=3)
        draw.rounded_rectangle((center_x - slot / 2 + 8, top, center_x + slot / 2 - 8, top + 110),
                               radius=16, fill="#EEF4FF")
        _draw_centered_wrapped(
            draw, element, (int(center_x - slot / 2 + 18), top + 14,
                            int(center_x + slot / 2 - 18), top + 100),
            size=18, color="#253247", bold=True, max_lines=3, spacing=4,
        )


def _draw_cycle_visual(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    import math

    title_bottom = _draw_scene_title(draw, scene)
    elements = _scene_elements(scene)[:6]
    count = max(2, len(elements))
    center = (640, title_bottom + 200)
    radius_x, radius_y = 330, 150
    draw.ellipse((center[0] - radius_x, center[1] - radius_y,
                  center[0] + radius_x, center[1] + radius_y), outline="#C9D9F5", width=5)
    for position, element in enumerate(elements):
        if not _shown(position, limit):
            continue
        angle = -math.pi / 2 + 2 * math.pi * position / count
        arrow_angle = angle + math.pi / count
        tip = (center[0] + radius_x * math.cos(arrow_angle),
               center[1] + radius_y * math.sin(arrow_angle))
        tangent = math.atan2(radius_y * math.cos(arrow_angle), -radius_x * math.sin(arrow_angle))
        _arrow_head(draw, tip, tangent, size=20, fill="#D98A1E")
        node_x = center[0] + radius_x * math.cos(angle)
        node_y = center[1] + radius_y * math.sin(angle)
        box = (int(node_x - 120), int(node_y - 42), int(node_x + 120), int(node_y + 42))
        draw.rounded_rectangle(box, radius=20, fill="#EEF4FF", outline="#2B6DE8", width=3)
        _draw_centered_wrapped(draw, element, (box[0] + 12, box[1] + 10, box[2] - 12, box[3] - 8),
                               size=17, color="#253247", bold=True, max_lines=2, spacing=4)
    _draw_centered_wrapped(draw, scene.title, (center[0] - 150, center[1] - 40,
                                               center[0] + 150, center[1] + 40),
                           size=20, color="#2459B8", bold=True, max_lines=2, spacing=4)


def _draw_concept_map_visual(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    center = (640, title_bottom + 190)
    positions = [
        (105, title_bottom + 55),
        (865, title_bottom + 55),
        (105, title_bottom + 275),
        (865, title_bottom + 275),
    ]
    items_and_positions = list(zip(_scene_elements(scene)[:4], positions, strict=False))

    # Connect only the shape boundaries. Drawing centre-to-centre lines made the
    # connectors cut through both the central label and the surrounding cards.
    for position, (_, (x, y)) in enumerate(items_and_positions):
        if _shown(position, limit):
            start, end = _concept_connector_points(center, (120, 64), (x, y, x + 260, y + 116))
            draw.line((*start, *end), fill="#9DB9E8", width=4)

    for position, (item, (x, y)) in enumerate(items_and_positions):
        if not _shown(position, limit):
            continue
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


def _draw_example_visual(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = _scene_elements(scene)
    left_text = elements[0] if elements else "Problem or situation"
    right_text = (
        "\n".join(elements[1:]) if len(elements) > 1 else _summary_from_narration(scene.narration)
    )
    panels = (
        (84, "SITUATION", left_text, "#FFF5E6"),
        (650, "EXPLANATION", right_text, "#EAF7F2"),
    )
    for position, (left, label, body, fill) in enumerate(panels):
        if not _shown(position, limit):
            continue
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
    if _shown(1, limit):
        draw.line((634, 330, 648, 330), fill="#7EA7EB", width=5)
        _arrow_head(draw, (650, 330), 0.0, size=12, fill="#7EA7EB")


def _draw_formula_visual(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = _scene_elements(scene)
    formula = elements[0] if elements else scene.title
    if _shown(0, limit):
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
    for position, item in enumerate(elements[1:5] or list(scene.bullets)[:4], start=1):
        if not _shown(position, limit):
            continue
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


def _draw_key_points_visual(draw, scene: VideoScene, *, limit: int | None = None) -> None:
    title_bottom = _draw_scene_title(draw, scene)
    elements = _scene_elements(scene)[:5]
    y = title_bottom + 22
    for position, item in enumerate(elements):
        if _shown(position, limit):
            draw.rounded_rectangle(
                (90, y, 1188, y + 68), radius=16,
                fill="#EEF4FF" if position % 2 == 0 else "#F1F7F4",
            )
            draw.text(
                (112, y + 20), f"{position + 1:02d}", font=_font(18, bold=True), fill="#2B6DE8"
            )
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


SCENE_TRANSITIONS = ("smoothleft", "circleopen", "slideup", "smoothup", "radial", "wipeleft")
_REVEAL_TRANSITIONS = {
    "process": "wipeleft",
    "timeline": "wipeleft",
    "title": "wipeleft",
    "cycle": "circleopen",
    "concept_map": "circleopen",
    "comparison": "wipedown",
    "key_points": "wipedown",
    "formula": "wipedown",
    "recap": "wipedown",
    "illustration": "circleopen",
}


def intro_seconds(duration: float, *, intro: bool) -> float:
    """Length of the transition that brings a scene in from the previous one."""
    return min(0.7, max(0.3, duration * 0.12)) if intro else 0.0


def stage_schedule(
    stage_count: int, duration: float, *, intro: bool
) -> tuple[list[int], list[tuple[float, float]]]:
    """Choose which stage frames to show and when each transition starts.

    Returns the stage indices to use and one (offset, length) pair per transition. With `intro`,
    the first transition brings the scene in from the previous scene's last frame. Reveals are
    spread over the first two-thirds of the narration so the diagram builds while it is
    explained; very short scenes keep fewer intermediate steps.
    """
    intro_length = intro_seconds(duration, intro=intro)
    steps = max(0, stage_count - 1)
    start = intro_length + 0.35
    latest = duration - 0.6
    if steps == 0 or latest <= start:
        selected = [stage_count - 1]
        transitions = [(0.0, intro_length)] if intro else []
        return selected, transitions
    window_end = min(latest, max(start + steps * 0.9, duration * 0.68))
    room = max(1, int((window_end - start) / 0.6) + 1)
    if steps > room:
        # Keep the first and last stage and an even spread in between.
        picks = sorted({round(index * steps / (room - 1)) for index in range(room)}) if room > 1 \
            else [steps]
        selected = [0] + [pick for pick in picks if pick > 0]
    else:
        selected = list(range(stage_count))
    reveal_steps = len(selected) - 1
    spacing = (window_end - start) / max(1, reveal_steps - 1) if reveal_steps > 1 else 0.0
    fade = min(0.5, max(0.25, spacing * 0.7)) if reveal_steps > 1 else 0.5
    transitions = [(0.0, intro_length)] if intro else []
    for step in range(reveal_steps):
        transitions.append((start + step * spacing, fade))
    return selected, transitions


def _render_segment(
    ffmpeg: str,
    frame: Path,
    audio: Path,
    output: Path,
    duration: float,
    *,
    scene_index: int = 1,
    tempo: float = 1.0,
    stages: list[Path] | None = None,
    previous_frame: Path | None = None,
    is_last: bool = False,
    visual_type: str = "key_points",
    captions: list[tuple[float, float, Path]] | None = None,
    clip: Path | None = None,
    clip_box: tuple[int, int, int, int] | None = None,
    clip_radius: int = 0,
    clip_max_stretch: float = 1.0,
) -> None:
    """Encode one scene: animated entry, step-by-step diagram build-up, captions, narration.

    `clip` is an animation or motion clip placed in `clip_box` (ANIMATION_BOX by default) from the
    end of the entry transition, cropped to fill the box with `clip_radius` rounded corners. It may
    be slowed down by up to `clip_max_stretch` to cover more of the scene; its last frame is then
    held until the scene ends.
    """
    stage_paths = list(stages) if stages else [frame]
    intro = previous_frame is not None
    selected, transitions = stage_schedule(len(stage_paths), duration, intro=intro)
    images = ([previous_frame] if intro else []) + [stage_paths[index] for index in selected]
    if len(transitions) != len(images) - 1:
        transitions = transitions[: len(images) - 1]

    # Each still must last until the transition out of it has finished.
    lengths: list[float] = []
    for position in range(len(images)):
        begin = transitions[position - 1][0] if position else 0.0
        if position < len(transitions):
            offset, length = transitions[position]
            lengths.append(max(0.1, offset - begin + length + 0.05))
        else:
            lengths.append(max(0.1, duration - begin + 0.1))

    arguments: list[str] = []
    for image, length in zip(images, lengths, strict=True):
        arguments += ["-loop", "1", "-framerate", "30", "-t", f"{length:.3f}", "-i", str(image)]
    caption_items = list(captions or [])
    for _, _, caption_image in caption_items:
        arguments += [
            "-loop", "1", "-framerate", "30", "-t", f"{duration + 0.2:.3f}",
            "-i", str(caption_image),
        ]
    clip_stream = mask_stream = None
    box = clip_box or animations.ANIMATION_BOX
    if clip is not None:
        clip_stream = len(images) + len(caption_items)
        arguments += ["-i", str(clip)]
        if clip_radius > 0:
            mask_stream = clip_stream + 1
            mask = _rounded_mask(box, clip_radius, output.parent / f"{output.stem}-mask.png")
            arguments += ["-loop", "1", "-framerate", "30", "-t", f"{duration + 1:.3f}",
                          "-i", str(mask)]
    arguments += ["-i", str(audio)]

    chains = [
        f"[{position}:v]format=yuv420p,fps=30,settb=AVTB,setsar=1[s{position}]"
        for position in range(len(images))
    ]
    current = "s0"
    reveal = _REVEAL_TRANSITIONS.get(visual_type, "fade")
    for position, (offset, length) in enumerate(transitions, start=1):
        if intro and position == 1:
            effect = SCENE_TRANSITIONS[(scene_index - 2) % len(SCENE_TRANSITIONS)]
        elif position == len(transitions):
            effect = "fade"  # the takeaway banner fades in
        else:
            effect = reveal
        label = f"x{position}"
        chains.append(
            f"[{current}][s{position}]xfade=transition={effect}:duration={length:.3f}:"
            f"offset={offset:.3f}[{label}]"
        )
        current = label
    if clip_stream is not None:
        clip_start = intro_seconds(duration, intro=intro)
        x, y, right, bottom = box
        width, height = right - x, bottom - y
        stretch = 1.0
        if clip_max_stretch > 1.0:
            playable = max(0.1, duration - clip_start - 0.3)
            clip_length = max(0.1, _media_duration(ffmpeg, clip))
            stretch = min(clip_max_stretch, max(1.0, playable / clip_length))
        pixel_format = "yuva420p" if mask_stream is not None else "yuv420p"
        chains.append(
            f"[{clip_stream}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setpts={stretch:.4f}*(PTS-STARTPTS),fps=30,"
            f"format={pixel_format},setsar=1,"
            f"tpad=stop_mode=clone:stop_duration={duration + 1:.3f},"
            f"setpts=PTS-STARTPTS+{clip_start:.3f}/TB[clipraw]"
        )
        if mask_stream is not None:
            chains.append(
                f"[{mask_stream}:v]format=gray,setpts=PTS-STARTPTS+{clip_start:.3f}/TB[mask];"
                "[clipraw][mask]alphamerge[clip]"
            )
        else:
            chains.append("[clipraw]null[clip]")
        chains.append(
            f"[{current}][clip]overlay={x}:{y}:eof_action=repeat:"
            f"enable='gte(t,{clip_start:.3f})'[anim]"
        )
        current = "anim"
    # Captions sit in the strip below the lesson card, each visible only while it is spoken.
    for position, (start, end, _) in enumerate(caption_items):
        stream = len(images) + position
        label = f"c{position}"
        chains.append(
            f"[{current}][{stream}:v]overlay=0:{caption_tools.CAPTION_Y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'[{label}]"
        )
        current = label
    finishing = [f"trim=duration={duration:.3f}", "setpts=PTS-STARTPTS"]
    if not intro:
        finishing.append("fade=t=in:st=0:d=0.40")
    if is_last:
        finishing.append(f"fade=t=out:st={max(0.0, duration - 0.6):.3f}:d=0.6")
    chains.append(f"[{current}]{','.join(finishing)}[video]")

    audio_seconds = _wav_duration(audio)
    audio_padding = max(0.0, duration - audio_seconds / tempo)
    audio_index = (
        len(images)
        + len(caption_items)
        + (1 if clip_stream is not None else 0)
        + (1 if mask_stream is not None else 0)
    )
    chains.append(
        f"[{audio_index}:a]atempo={tempo:.4f},apad=pad_dur={audio_padding:.3f}[audio]"
    )
    _run_ffmpeg(
        ffmpeg,
        *arguments,
        "-filter_complex",
        ";".join(chains),
        "-map",
        "[video]",
        "-map",
        "[audio]",
        "-t",
        f"{duration:.3f}",
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
        str(output),
    )


def _rounded_mask(box: tuple[int, int, int, int], radius: int, path: Path) -> Path:
    from PIL import Image, ImageDraw

    width, height = box[2] - box[0], box[3] - box[1]
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    mask.save(path, format="PNG")
    return path


def _concatenate_segments(
    ffmpeg: str,
    segments: list[Path],
    output: Path,
    maximum_seconds: int,
    *,
    subtitles: Path | None = None,
) -> None:
    inputs = [argument for segment in segments for argument in ("-i", str(segment))]
    subtitle_arguments: list[str] = []
    if subtitles is not None:
        # A switchable subtitle track in addition to the captions drawn on the picture.
        inputs += ["-i", str(subtitles)]
        subtitle_arguments = [
            "-map", f"{len(segments)}:s", "-c:s", "mov_text",
            "-metadata:s:s:0", "language=eng",
        ]
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
        *subtitle_arguments,
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
