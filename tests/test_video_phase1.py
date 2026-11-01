"""Phase 1 video quality: reviewer pass, illustrations and captions."""

import base64
import io
import json
import math
import struct
import wave
from copy import deepcopy
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageChops

from app.artifacts import captions, illustrations
from app.artifacts import video as video_module
from app.artifacts.video import (
    VIDEO_PLAN_SCHEMA,
    VideoScene,
    _ffmpeg_executable,
    _media_duration,
    _render_segment,
    _review_video_plan,
    _scene_captions,
    render_scene_stages,
    reveal_count,
)
from app.core.config import settings


def _png_bytes(color: str = "#1F8A84") -> bytes:
    image = Image.new("RGB", (640, 480), "#F4F1EA")
    image.paste(Image.new("RGB", (200, 200), color), (220, 140))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return self._data


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------


def test_caption_lines_are_short_and_cover_the_narration() -> None:
    text = (
        "Photosynthesis lets plants make food from light, water and carbon dioxide, which is "
        "why leaves face the sun. The oxygen we breathe is a by-product."
    )
    chunks = captions.caption_chunks(text)
    assert all(len(chunk.split()) <= captions.MAX_WORDS for chunk in chunks)
    assert " ".join(chunks).split() == text.split()

    timed = captions.timed_captions(text, 12.0)
    assert timed[0][0] == 0.0
    assert all(start < end <= 12.0 for start, end, _ in timed)
    assert all(previous[1] <= following[0] for previous, following in pairwise(timed))


def test_srt_track_uses_standard_timestamps(tmp_path: Path) -> None:
    path = captions.write_srt([(0.0, 1.5, "Hello"), (61.25, 63.0, "World")], tmp_path / "a.srt")
    content = path.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello" in content
    assert "2\n00:01:01,250 --> 00:01:03,000\nWorld" in content


def test_captions_can_be_switched_off(tmp_path: Path, monkeypatch) -> None:
    scene = VideoScene("S", "Title", ("a", "b"), "One two three four five.", "key_points")
    monkeypatch.setattr(settings, "video_captions_enabled", False)
    assert _scene_captions(scene, tmp_path, index=1, speech_seconds=5.0) == []
    monkeypatch.setattr(settings, "video_captions_enabled", True)
    entries = _scene_captions(scene, tmp_path, index=1, speech_seconds=5.0)
    assert entries and entries[0][3].exists()


# ---------------------------------------------------------------------------
# Illustrations
# ---------------------------------------------------------------------------


def test_gemini_image_request_and_response_parsing(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["key"] = request.get_header("X-goog-api-key")
        captured["body"] = json.loads(request.data)
        return _FakeHttpResponse(
            {"candidates": [{"content": {"parts": [
                {"text": "thinking", "thought": True},
                {"inlineData": {"mimeType": "image/png",
                                "data": base64.b64encode(_png_bytes()).decode()}},
            ]}}]}
        )

    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")
    monkeypatch.setattr(illustrations.urllib.request, "urlopen", fake_urlopen)
    path = illustrations.generate_image("a plant on a windowsill", tmp_path / "scene.png")

    assert path.exists() and Image.open(path).size == (640, 480)
    assert captured["url"].endswith(f"/models/{settings.gemini_image_model}:generateContent")
    assert captured["key"] == "test-gemini-key"
    assert captured["body"]["generationConfig"]["responseModalities"] == ["IMAGE"]


def test_prompts_forbid_text_in_pictures() -> None:
    prompt = illustrations.build_prompt("a leaf drawn as a kitchen", "Photosynthesis")
    assert "Photosynthesis" in prompt and "a leaf drawn as a kitchen" in prompt
    assert "no text" in prompt.lower()


def test_illustrations_are_optional_and_failures_fall_back(tmp_path: Path, monkeypatch) -> None:
    scenes = [
        VideoScene("A", "Title", ("a", "b"), "n", "title", ("x", "y"), "t", "a sunny field"),
        VideoScene("B", "Analogy", ("a", "b"), "n", "illustration", ("x", "y"), "t", "a kitchen"),
        VideoScene("C", "Steps", ("a", "b"), "n", "process", ("x", "y"), "t", ""),
    ]
    # No key: nothing is called and scenes are unchanged.
    monkeypatch.setattr(settings, "gemini_api_key", None)
    assert illustrations.attach_illustrations(scenes, tmp_path, topic="T") == scenes

    monkeypatch.setattr(settings, "gemini_api_key", "key")
    requested = []

    def fake_generate(prompt, path, **_):
        requested.append(prompt)
        if "kitchen" in prompt:
            raise RuntimeError("quota exceeded")
        path.write_bytes(_png_bytes())
        return path

    monkeypatch.setattr(illustrations, "generate_image", fake_generate)
    updated = illustrations.attach_illustrations(scenes, tmp_path, topic="T")
    assert len(requested) == 2  # diagram scenes are never sent to the image model
    assert updated[0].image_path  # succeeded
    assert updated[1].image_path == ""  # failed: keeps the drawn layout
    assert updated[2] == scenes[2]

    monkeypatch.setattr(settings, "video_max_illustrations", 1)
    requested.clear()
    illustrations.attach_illustrations(scenes, tmp_path, topic="T")
    assert len(requested) == 1


def test_illustrated_scene_builds_picture_then_points(tmp_path: Path) -> None:
    picture = tmp_path / "picture.png"
    picture.write_bytes(_png_bytes())
    scene = VideoScene(
        "THE BIG IDEA", "A leaf works like a kitchen",
        ("Leaves are the kitchen", "Water is an ingredient", "Light does the cooking"),
        "n", "illustration", ("Kitchen", "Ingredients"), "A leaf is a food factory.",
        "a leaf drawn as a kitchen", str(picture),
    )
    stages = render_scene_stages(scene, tmp_path, index=2, total=4)
    assert reveal_count(scene) == 4  # picture + three points
    assert len(stages) == 6
    frames = [Image.open(path).convert("RGB") for path in stages]
    for before, after in pairwise(frames):
        assert ImageChops.difference(before, after).getbbox() is not None

    # Without an image the same scene falls back to the key-points layout.
    fallback = replace(scene, image_path="")
    fallback_dir = tmp_path / "fallback"
    fallback_dir.mkdir()
    stages = render_scene_stages(fallback, fallback_dir, index=2, total=4)
    assert reveal_count(fallback) == 3  # key points only
    assert len(stages) == reveal_count(fallback) + 2


def test_storyboard_schema_carries_illustration_field() -> None:
    scene_schema = VIDEO_PLAN_SCHEMA["properties"]["scenes"]["items"]
    assert "illustration" in scene_schema["required"]
    assert "illustration" in scene_schema["properties"]["visual_type"]["enum"]


# ---------------------------------------------------------------------------
# Reviewer pass
# ---------------------------------------------------------------------------


def _valid_plan() -> dict:
    narration = "Cell structure explains how organelles divide work inside each cell. " * 4
    return {
        "title": "Cell Structure explained",
        "target_duration_seconds": 120,
        "scenes": [
            {
                "section": "Intro",
                "title": "Cell Structure",
                "visual_type": visual_type,
                "visual_elements": ["Cell", "Structure"],
                "bullets": ["Cell structure basics", "Organelles"],
                "takeaway": "Cell structure links form and function.",
                "illustration": "",
                "narration": narration,
            }
            for visual_type in ("title", "concept_map", "comparison", "recap")
        ],
    }


def _review(monkeypatch, reviewed: dict | Exception) -> dict:
    class FakeResponses:
        def create(self, **kwargs):
            if isinstance(reviewed, Exception):
                raise reviewed
            return SimpleNamespace(status="completed", output_text=json.dumps(reviewed))

    package = {"title": "Cell Structure", "lecture_sections": [{"title": "Cell Structure"}]}
    return _review_video_plan(
        SimpleNamespace(responses=FakeResponses()),
        _valid_plan(),
        request_payload={"teaching_package": package, "video_requirements": {}},
        minimum_words=50,
        maximum_words=5000,
        teaching_package=package,
    )


def test_reviewed_plan_is_used_when_it_passes_checks(monkeypatch) -> None:
    improved = deepcopy(_valid_plan())
    improved["scenes"][1]["narration"] = improved["scenes"][1]["narration"].replace(
        "explains", "shows"
    )
    assert _review(monkeypatch, improved) == improved


def test_review_never_makes_a_valid_plan_worse(monkeypatch) -> None:
    broken = deepcopy(_valid_plan())
    broken["scenes"] = broken["scenes"][:2]  # too few scenes
    assert _review(monkeypatch, broken) == _valid_plan()
    assert _review(monkeypatch, RuntimeError("timeout")) == _valid_plan()


# ---------------------------------------------------------------------------
# Encoder with captions and subtitle track
# ---------------------------------------------------------------------------


def _tone(path: Path, seconds: float) -> None:
    rate = 22_050
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(
            struct.pack("<h", int(800 * math.sin(2 * math.pi * 200 * k / rate)))
            for k in range(int(rate * seconds))
        ))


def test_segment_with_captions_keeps_duration_and_shows_caption(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg_executable()
    scene = VideoScene("S", "Captions", ("a", "b"), "n", "key_points", ("One", "Two"), "Done.")
    stages = render_scene_stages(scene, tmp_path, index=1, total=1)
    audio = tmp_path / "audio.wav"
    _tone(audio, 3.0)
    caption = captions.render_caption_image("A caption line", tmp_path / "caption.png")
    output = tmp_path / "segment.mp4"
    _render_segment(ffmpeg, stages[-1], audio, output, 4.0, stages=stages,
                    captions=[(0.0, 3.5, caption)])
    assert abs(_media_duration(ffmpeg, output) - 4.0) < 0.15

    frame_path = tmp_path / "frame.png"
    video_module._run_ffmpeg(ffmpeg, "-ss", "2", "-i", str(output), "-frames:v", "1",
                             str(frame_path))
    strip = Image.open(frame_path).convert("RGB").crop((500, 670, 780, 710))
    assert max(channel for channel in strip.tobytes()) > 200  # white caption text present


def test_final_video_carries_a_subtitle_track(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg_executable()
    segments = []
    for index in range(1, 4):
        scene = VideoScene("S", f"Scene {index}", ("a", "b"), "n", "key_points",
                           (f"Point {index}", "Two"), "Done.")
        stages = render_scene_stages(scene, tmp_path, index=index, total=3)
        audio = tmp_path / f"a{index}.wav"
        _tone(audio, 1.5)
        segment = tmp_path / f"s{index}.mp4"
        _render_segment(ffmpeg, stages[-1], audio, segment, 2.0, stages=stages, scene_index=index)
        segments.append(segment)
    srt = captions.write_srt([(0.0, 1.0, "First"), (2.0, 3.0, "Second")], tmp_path / "c.srt")
    output = tmp_path / "final.mp4"
    video_module._concatenate_segments(ffmpeg, segments, output, 60, subtitles=srt)
    completed = video_module.subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(output)], capture_output=True, text=True, check=False
    )
    assert "Subtitle: mov_text" in completed.stderr
