import math
import struct
import wave
from pathlib import Path

import pytest

from app.artifacts.video import (
    VideoScene,
    _constrain_plan_narration,
    _create_elevenlabs_narration,
    _ffmpeg_executable,
    _render_scene_frame,
    _render_segment,
    _video_plan_issues,
    build_teaching_video_scenes,
    create_teaching_video,
    estimated_video_seconds,
    generate_teaching_video_plan,
)
from app.core.config import settings


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

    issues = _video_plan_issues(plan, minimum_words=660, maximum_words=2200)

    assert any("fuller explanation" in issue for issue in issues)
    assert any("at least 660 words" in issue for issue in issues)


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
        ["This explanation defines the idea, connects it to the mechanism, and applies it."] * 10
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
    assert sum(len(scene.narration.split()) for scene in scenes) >= 660
    text_format = captured["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True


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
