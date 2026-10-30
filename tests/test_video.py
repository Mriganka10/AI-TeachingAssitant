import math
import struct
import wave
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from app.agents.llm import LLMService
from app.artifacts.video import (
    VideoScene,
    _audience_video_package,
    _concatenate_segments,
    _concept_connector_points,
    _constrain_plan_narration,
    _create_elevenlabs_narration,
    _display_text,
    _ffmpeg_executable,
    _math_tokens,
    _media_duration,
    _render_scene_frame,
    _render_segment,
    _resolve_video_duration_range,
    _run_ffmpeg,
    _trim_narration_silence,
    _verify_scene_progression,
    _video_plan_issues,
    build_teaching_video_scenes,
    create_teaching_video,
    estimated_video_seconds,
    generate_teaching_video_plan,
)
from app.core.config import settings


def test_video_planner_receives_only_audience_facing_material() -> None:
    result = LLMService._mock({"topic": "Cell Structure", "duration_minutes": 60})
    result["lecture_sections"][0]["content"]["teaching_tip"] = "PRIVATE FACILITATION TIP"
    result["case_study"]["teaching_notes"] = "PRIVATE CASE GUIDANCE"
    result["quality_notes"]["review_priorities"] = ["PRIVATE REVIEW PRIORITY"]

    audience_package = _audience_video_package(result)
    text = str(audience_package)
    assert "Cell Structure" in text
    assert "PRIVATE FACILITATION TIP" not in text
    assert "PRIVATE CASE GUIDANCE" not in text
    assert "PRIVATE REVIEW PRIORITY" not in text
    assert "common_errors" in text


def test_request_video_duration_overrides_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings, "video_min_minutes", 4)
    monkeypatch.setattr(settings, "video_max_minutes", 15)

    assert _resolve_video_duration_range(min_minutes=1, max_minutes=30) == (1, 30)
    assert _resolve_video_duration_range(min_minutes=None, max_minutes=None) == (4, 15)

    with pytest.raises(ValueError, match="cannot exceed"):
        _resolve_video_duration_range(min_minutes=11, max_minutes=7)
    with pytest.raises(ValueError, match="positive"):
        _resolve_video_duration_range(min_minutes=0, max_minutes=7)


def test_video_plan_uses_teaching_content_and_respects_fifteen_minute_budget() -> None:
    repeated_explanation = " ".join(["Explain the concept with evidence and an example."] * 500)
    result = {
        "title": "Responsible Machine Learning",
        "overview": {
            "course": "Artificial Intelligence",
            "audience": "Postgraduate students",
            "difficulty": "advanced",
            "lesson_purpose": "Evaluate machine-learning decisions responsibly.",
        },
        "learning_objectives": [
            {"objective": "Explain the central concepts."},
            {"objective": "Apply the concepts to a realistic case."},
        ],
        "lecture_sections": [
            {
                "title": f"Concept {index}",
                "minutes": 20,
                "content": {
                    "explanation": repeated_explanation,
                    "key_points": ["Evidence matters", "Assumptions must be tested"],
                    "classroom_activity": "Compare two model decisions.",
                },
            }
            for index in range(1, 9)
        ],
        "discussion_questions": ["Which assumption is most important?"],
    }

    scenes = build_teaching_video_scenes(result, max_minutes=15)

    assert scenes[0].title == "Responsible Machine Learning"
    assert any(scene.title == "Concept 1" for scene in scenes)
    assert estimated_video_seconds(scenes) < 15 * 60
    assert all(len(scene.narration) <= 3900 for scene in scenes)


def test_shorter_video_limit_reduces_narration_budget() -> None:
    result = {
        "title": "Short lesson",
        "overview": {"lesson_purpose": "Introduce the topic."},
        "lecture_sections": [
            {
                "title": "Detailed section",
                "content": {"explanation": "word " * 2000, "key_points": ["A", "B"]},
            }
        ],
    }

    one_minute = build_teaching_video_scenes(result, max_minutes=1)
    fifteen_minutes = build_teaching_video_scenes(result, max_minutes=15)

    assert sum(len(scene.narration.split()) for scene in one_minute) < sum(
        len(scene.narration.split()) for scene in fifteen_minutes
    )


def test_video_plan_rejects_brief_bullet_only_narration() -> None:
    plan = {
        "title": "Too brief",
        "target_duration_seconds": 240,
        "scenes": [
            {
                "section": "CONCEPT",
                "title": f"Scene {index}",
                "visual_type": "title" if index == 0 else "recap" if index == 7 else "process",
                "visual_elements": ["Point one", "Point two"],
                "bullets": ["Point one", "Point two"],
                "takeaway": "A concise takeaway.",
                "narration": "This is only a very short summary without a complete explanation.",
            }
            for index in range(8)
        ],
    }

    issues = _video_plan_issues(plan, minimum_words=760, maximum_words=2200)

    assert any("fuller explanation" in issue for issue in issues)
    assert any("at least 760 words" in issue for issue in issues)


def test_video_plan_rejects_unrelated_scenes_even_with_valid_duration() -> None:
    narration = " ".join(["Coastal tides move sand along a beach."] * 8)
    plan = {
        "title": "Time Value of Money",
        "scenes": [
            {
                "section": "COASTAL GEOGRAPHY",
                "title": "Coastal erosion",
                "visual_type": "title" if index == 0 else "recap" if index == 3 else "process",
                "visual_elements": ["Waves", "Sand"],
                "bullets": ["Tides", "Beaches"],
                "takeaway": "The coastline changes.",
                "narration": narration,
            }
            for index in range(4)
        ],
    }
    package = {
        "title": "Time Value of Money",
        "lecture_sections": [{"title": "Present Value and Discounting"}],
    }

    issues = _video_plan_issues(
        plan,
        minimum_words=100,
        maximum_words=500,
        teaching_package=package,
    )
    assert any("video scenes drift" in issue for issue in issues)


def test_video_plan_safely_constrains_small_narration_overflow() -> None:
    sentence = "The lecturer defines the concept, explains the mechanism, and applies it carefully."
    plan = {
        "title": "Bounded lesson",
        "target_duration_seconds": 840,
        "scenes": [
            {
                "section": "LESSON",
                "title": f"Scene {index}",
                "visual_type": "title" if index == 0 else "recap" if index == 7 else "process",
                "visual_elements": ["Definition", "Application"],
                "bullets": ["Definition", "Application"],
                "takeaway": "The concept connects to practice.",
                "narration": " ".join([sentence] * 26),
            }
            for index in range(8)
        ],
    }

    constrained = _constrain_plan_narration(plan, maximum_words=2200)
    narrations = [scene["narration"] for scene in constrained["scenes"]]

    assert sum(len(narration.split()) for narration in narrations) <= 2200
    assert all(len(narration.split()) >= 45 for narration in narrations)
    assert all(narration.endswith(".") for narration in narrations)


def test_mock_mode_does_not_create_a_silent_placeholder_video(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_service_mode", "mock")

    with pytest.raises(RuntimeError, match="unavailable in mock mode"):
        create_teaching_video({"title": "Sample"}, tmp_path / "sample.mp4")


def test_video_script_uses_strict_schema_and_meets_long_form_budget(monkeypatch) -> None:
    narration = " ".join(
        ["Supervised learning uses labels; unsupervised learning finds patterns without labels."] * 10
    )
    plan = {
        "title": "Supervised and Unsupervised Learning",
        "target_duration_seconds": 270,
        "scenes": [
            {
                "section": "CORE CONCEPT",
                "title": f"Concept {index}",
                "visual_type": (
                    "title" if index == 1 else "recap" if index == 8 else "concept_map"
                ),
                "visual_elements": ["Definition", "Mechanism", "Application"],
                "bullets": ["Definition", "Worked example"],
                "takeaway": "The distinction determines how the model learns.",
                "narration": narration,
            }
            for index in range(1, 9)
        ],
    }
    captured: dict[str, object] = {}

    class FakeResponse:
        output_text = __import__("json").dumps(plan)

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    scenes = generate_teaching_video_plan(
        {
            "title": "Supervised and Unsupervised Learning",
            "overview": {"lesson_purpose": "Compare the two learning paradigms."},
            "lecture_sections": [
                {
                    "title": "Supervised learning",
                    "content": {
                        "explanation": "Models learn from labelled examples.",
                        "key_points": ["Labels guide learning", "Evaluate on unseen data"],
                        "teaching_tip": "PRIVATE FACILITATION TIP",
                    },
                },
                {
                    "title": "Unsupervised learning",
                    "content": {
                        "explanation": "Models find structure in unlabelled examples.",
                        "key_points": ["Clustering", "Dimensionality reduction"],
                    },
                },
            ],
        },
        min_minutes=4,
        max_minutes=15,
    )

    assert len(scenes) == 8
    assert sum(len(scene.narration.split()) for scene in scenes) >= 760
    text_format = captured["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True
    assert "PRIVATE FACILITATION TIP" not in captured["input"]


def test_multimedia_scene_types_render_different_visual_compositions(tmp_path: Path) -> None:
    title_path = tmp_path / "title.png"
    process_path = tmp_path / "process.png"
    _render_scene_frame(
        VideoScene(
            section="INTRODUCTION",
            title="Supervised and Unsupervised Learning",
            bullets=("Labelled examples", "Patterns without labels"),
            narration="This scene introduces the topic.",
            visual_type="title",
            visual_elements=("Supervised", "Unsupervised"),
            takeaway="The data available during training determines the learning paradigm.",
        ),
        title_path,
        index=1,
        total=8,
    )
    _render_scene_frame(
        VideoScene(
            section="PROCESS",
            title="How supervised learning works",
            bullets=("Prepare labels", "Fit model", "Evaluate"),
            narration="This scene explains the process.",
            visual_type="process",
            visual_elements=("Label data", "Train", "Validate", "Test"),
            takeaway="Evaluation uses examples that did not fit the model.",
        ),
        process_path,
        index=2,
        total=8,
    )

    assert title_path.read_bytes() != process_path.read_bytes()
    assert title_path.stat().st_size > 10_000
    assert process_path.stat().st_size > 10_000


def test_equation_display_text_uses_portable_subscript_and_superscript_notation() -> None:
    assert _display_text("D = {(xᵢ, yᵢ), …, (xₙ, yₙ)}") == "D = {(x_i, y_i), ..., (x_n, y_n)}"
    assert _display_text("MSE = Σ(yᵢ - ŷᵢ)² / n") == "MSE = Σ(y_i - ŷ_i)^2 / n"
    assert _display_text(r"distance = \sqrt{(a₁-b₁)²}") == "distance = √{(a_1-b_1)^2}"
    assert _math_tokens("xᵢ²") == [
        ("x", "normal"),
        ("i", "subscript"),
        ("2", "superscript"),
    ]


def test_concept_map_connectors_stop_at_shape_boundaries() -> None:
    center = (640, 340)
    radii = (120, 64)
    card = (105, 205, 365, 321)

    start, end = _concept_connector_points(center, radii, card)

    # The connector begins on the central ellipse, not at its centre.
    ellipse_value = ((start[0] - center[0]) / radii[0]) ** 2 + (
        (start[1] - center[1]) / radii[1]
    ) ** 2
    assert ellipse_value == pytest.approx(1.0, abs=0.03)
    # It ends on the right or bottom edge of the left-side card, not in its text area.
    assert end[0] == card[2] or end[1] == card[3]
    assert start != center


def test_multimedia_frame_and_narration_encode_to_playable_mp4(tmp_path: Path) -> None:
    frame_path = tmp_path / "scene.png"
    audio_path = tmp_path / "scene.wav"
    video_path = tmp_path / "scene.mp4"
    _render_scene_frame(
        VideoScene(
            section="COMPARISON",
            title="Two learning paradigms",
            bullets=("Supervised learning", "Unsupervised learning"),
            narration="A brief narration used to verify the video encoder.",
            visual_type="comparison",
            visual_elements=("Labelled data", "Prediction", "Unlabelled data", "Structure"),
            takeaway="Training data determines the type of learning task.",
        ),
        frame_path,
        index=3,
        total=8,
    )
    sample_rate = 44_100
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        frames = b"".join(
            struct.pack("<h", int(1500 * math.sin(2 * math.pi * 220 * i / sample_rate)))
            for i in range(sample_rate)
        )
        audio.writeframes(frames)

    _render_segment(_ffmpeg_executable(), frame_path, audio_path, video_path, 1.0)

    assert video_path.exists()
    assert video_path.stat().st_size > 10_000


def test_multimedia_timeline_preserves_duration_and_changes_scenes(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg_executable()
    segments: list[Path] = []
    scenes = (
        VideoScene(
            section="OPENING",
            title="Start with the learning question",
            bullets=("Prior knowledge", "Learning goal"),
            narration="Opening narration.",
            visual_type="title",
            visual_elements=("Question", "Goal"),
            takeaway="Frame the lesson before adding details.",
        ),
        VideoScene(
            section="PROCESS",
            title="Follow the reasoning sequence",
            bullets=("Observe", "Compare", "Conclude"),
            narration="Process narration.",
            visual_type="process",
            visual_elements=("Observe", "Compare", "Conclude"),
            takeaway="Reasoning should be visible and ordered.",
        ),
        VideoScene(
            section="RECAP",
            title="Connect the ideas",
            bullets=("Question", "Evidence", "Conclusion"),
            narration="Recap narration.",
            visual_type="recap",
            visual_elements=("Question", "Evidence", "Conclusion"),
            takeaway="The conclusion follows from the evidence.",
        ),
    )
    sample_rate = 44_100
    for index, scene in enumerate(scenes, start=1):
        frame_path = tmp_path / f"frame-{index}.png"
        audio_path = tmp_path / f"audio-{index}.wav"
        segment_path = tmp_path / f"segment-{index}.mp4"
        _render_scene_frame(scene, frame_path, index=index, total=len(scenes))
        with wave.open(str(audio_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            frames = b"".join(
                struct.pack(
                    "<h",
                    int(1200 * math.sin(2 * math.pi * (180 + index * 40) * i / sample_rate)),
                )
                for i in range(sample_rate)
            )
            audio.writeframes(frames)
        _render_segment(
            ffmpeg,
            frame_path,
            audio_path,
            segment_path,
            1.2,
            scene_index=index,
        )
        segments.append(segment_path)

    video_path = tmp_path / "lesson.mp4"
    _concatenate_segments(ffmpeg, segments, video_path, 30)
    _verify_scene_progression(ffmpeg, video_path, [1.2] * len(scenes))

    assert _media_duration(ffmpeg, video_path) == pytest.approx(3.6, abs=0.15)
    extracted: list[Path] = []
    for index, timestamp in enumerate((0.6, 1.8, 3.0), start=1):
        image_path = tmp_path / f"sample-{index}.png"
        _run_ffmpeg(
            ffmpeg,
            "-ss",
            str(timestamp),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            image_path.as_posix(),
        )
        extracted.append(image_path)

    images = [Image.open(image_path).convert("RGB") for image_path in extracted]
    assert ImageChops.difference(images[0], images[1]).getbbox() is not None
    assert ImageChops.difference(images[1], images[2]).getbbox() is not None


def test_narration_trims_a_long_silent_tail_before_scene_timing(tmp_path: Path) -> None:
    audio_path = tmp_path / "narration.wav"
    sample_rate = 16_000
    voiced = b"".join(
        struct.pack("<h", int(4000 * math.sin(2 * math.pi * 220 * i / sample_rate)))
        for i in range(sample_rate)
    )
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(voiced + b"\x00\x00" * sample_rate * 12)

    duration = _trim_narration_silence(audio_path)

    assert 1.0 <= duration <= 1.8
    with wave.open(str(audio_path), "rb") as trimmed:
        assert trimmed.getnframes() / trimmed.getframerate() == pytest.approx(duration, abs=0.01)


def test_narration_handles_streaming_wav_with_open_ended_header(tmp_path: Path) -> None:
    audio_path = tmp_path / "streaming.wav"
    sample_rate = 16_000
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x20\x03" * sample_rate + b"\x00\x00" * sample_rate * 2)

    # Streaming providers commonly use 0xFFFFFFFF as an unknown data length.
    raw = bytearray(audio_path.read_bytes())
    struct.pack_into("<L", raw, 4, 0xFFFFFFFF)
    struct.pack_into("<L", raw, 40, 0xFFFFFFFF)
    audio_path.write_bytes(raw)

    duration = _trim_narration_silence(audio_path)

    assert 1.0 <= duration <= 1.8
    with wave.open(str(audio_path), "rb") as trimmed:
        assert trimmed.getnframes() / trimmed.getframerate() == pytest.approx(duration, abs=0.01)


def test_scene_progression_rejects_an_unchanging_video(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg_executable()
    frame = tmp_path / "single-frame.png"
    Image.new("RGB", (1280, 720), "#2557D6").save(frame)
    video = tmp_path / "unchanging.mp4"
    _run_ffmpeg(
        ffmpeg, "-loop", "1", "-i", str(frame), "-t", "3.6", "-r", "30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    )

    with pytest.raises(RuntimeError, match="no visible change"):
        _verify_scene_progression(ffmpeg, video, [1.2, 1.2, 1.2])


def test_elevenlabs_uses_non_pro_mp3_and_converts_to_wav(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"ID3-fake-mp3-audio"

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    def fake_run_ffmpeg(ffmpeg, *arguments):
        captured["ffmpeg"] = ffmpeg
        captured["arguments"] = arguments
        output = Path(arguments[-1])
        with wave.open(str(output), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(44_100)
            audio.writeframes(b"\x00\x00" * 441)

    monkeypatch.setattr("app.artifacts.video.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.artifacts.video._ffmpeg_executable", lambda: "test-ffmpeg")
    monkeypatch.setattr("app.artifacts.video._run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "elevenlabs_output_format", "mp3_44100_128")
    destination = tmp_path / "narration.wav"

    _create_elevenlabs_narration("A short teaching narration.", destination)

    assert "output_format=mp3_44100_128" in str(captured["url"])
    assert destination.exists()
    assert not destination.with_suffix(".elevenlabs.mp3").exists()
    assert "pcm_s16le" in captured["arguments"]
