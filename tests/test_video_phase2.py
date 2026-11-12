"""Phase 2 video quality: Manim animations for STEM scenes, with a safe fallback."""

import importlib.util
import json
import math
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.artifacts import animations
from app.artifacts import video as video_module
from app.artifacts.video import (
    VIDEO_PLAN_SCHEMA,
    VideoScene,
    _ffmpeg_executable,
    _media_duration,
    _render_segment,
    _video_plan_issues,
    render_scene_stages,
    reveal_count,
)
from app.core.config import settings

MANIM_INSTALLED = importlib.util.find_spec("manim") is not None

GOOD_CODE = """
class LessonScene(Scene):
    def construct(self):
        axes = Axes(x_range=[0, 5, 1], y_range=[0, 5, 1], x_length=5, y_length=4,
                    axis_config={"color": INK})
        self.play(Create(axes), run_time=0.8)
        line = axes.plot(lambda x: 0.8 * x + 0.5, color=ACCENT)
        label = Text("best line", font_size=32).to_edge(RIGHT)
        self.play(Create(line), FadeIn(label), run_time=0.8)
        self.wait(0.6)
"""


def _scene(**changes) -> VideoScene:
    values = {
        "section": "IDEA",
        "title": "Finding the best line",
        "bullets": ("Plot the points", "Draw a line", "Measure the gap"),
        "narration": "Now you will see how a line can follow the points as closely as possible.",
        "visual_type": "animation",
        "visual_elements": ("points", "line", "error"),
        "takeaway": "The best line keeps the errors small.",
        "animation": "Draw axes, plot points, then draw the line through them.",
    }
    values.update(changes)
    return VideoScene(**values)


class _CodeClient:
    """Returns the given code strings in turn, recording each request payload."""

    def __init__(self, *codes: str) -> None:
        self.codes = list(codes)
        self.payloads: list[dict] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.payloads.append(json.loads(request["input"]))
        code = self.codes.pop(0)
        return SimpleNamespace(status="completed", output_text=json.dumps({"code": code}))


def _tone(path: Path, seconds: float) -> None:
    rate = 22050
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(
            struct.pack("<h", int(800 * math.sin(2 * math.pi * 200 * k / rate)))
            for k in range(int(rate * seconds))
        ))


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


def test_valid_scene_code_passes() -> None:
    assert animations.validate_scene_code(GOOD_CODE, allow_latex=True) == []


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("import os", "Import of 'os'"),
        ("from subprocess import run", "Import from 'subprocess'"),
        ("open('/etc/passwd')", "'open' is not allowed"),
        ("x = eval('1')", "'eval' is not allowed"),
        ("x = ().__class__", "Attribute '.__class__'"),
        ("config.frame_rate = 1", "'config' is not allowed"),
        ("while True:\n    pass", "'While' statements"),
        ("np.save('x', [1])", "Attribute '.save'"),
        ("img = ImageMobject('a.png')", "'ImageMobject' is not allowed"),
    ],
)
def test_unsafe_code_is_rejected(snippet: str, expected: str) -> None:
    issues = animations.validate_scene_code(GOOD_CODE + "\n" + snippet, allow_latex=True)
    assert any(expected in issue for issue in issues), issues


def test_code_must_define_the_lesson_scene() -> None:
    issues = animations.validate_scene_code("x = 1", allow_latex=True)
    assert any("LessonScene" in issue for issue in issues)
    assert animations.validate_scene_code("def broken(:", allow_latex=True)[0].startswith("Syntax")


def test_latex_objects_are_rejected_when_latex_is_missing() -> None:
    code = GOOD_CODE + "\nformula = MathTex(r'y = mx + c')\n"
    assert animations.validate_scene_code(code, allow_latex=True) == []
    assert any("LaTeX" in issue for issue in animations.validate_scene_code(code, allow_latex=False))
    assert "never use MathTex" in animations.coder_instructions(duration=8, allow_latex=False)


def test_renderer_environment_carries_no_secrets(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def fake_run(command, **options):
        captured.update(options)
        return SimpleNamespace(returncode=1, stderr="boom", stdout="")

    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setattr(animations.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="boom"):
        animations.render_scene_code(GOOD_CODE, tmp_path)
    assert "OPENAI_API_KEY" not in captured["env"]
    assert all("secret-value" not in value for value in captured["env"].values())
    assert captured["timeout"] == settings.video_animation_render_timeout_seconds


# ---------------------------------------------------------------------------
# Fallback and orchestration
# ---------------------------------------------------------------------------


def test_rejected_code_is_repaired_once_then_falls_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "video_animation_repair_attempts", 1)
    client = _CodeClient("import os", "import sys")
    clip = animations.build_animation(
        client, _scene(), workdir=tmp_path, duration=4.0, cues=[(0.0, 2.0, "Now you")],
        ffmpeg="unused", media_duration=lambda *_: 0.0,
    )
    assert clip is None
    assert len(client.payloads) == 2
    assert "rejected before rendering" in client.payloads[1]["error"]
    assert client.payloads[1]["previous_code"] == "import os"
    assert client.payloads[0]["narration_cues"][0] == {
        "from_second": 0.0, "to_second": 2.0, "spoken": "Now you",
    }


def test_clips_are_skipped_when_animations_are_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "video_animations_enabled", False)
    assert animations.animations_available() is False
    assert animations.build_clips(
        [_scene()], [5.0], [[]], tmp_path, client=None, ffmpeg="", media_duration=None
    ) == {}


def test_clip_count_is_capped(tmp_path: Path, monkeypatch) -> None:
    built: list[str] = []

    def fake_build(client, scene, **_):
        built.append(scene.title)
        return tmp_path / f"{scene.title}.mp4"

    monkeypatch.setattr(animations, "animations_available", lambda: True)
    monkeypatch.setattr(animations, "build_animation", fake_build)
    monkeypatch.setattr(settings, "video_max_animations", 2)
    scenes = [_scene(title=f"S{index}") for index in range(4)] + [_scene(visual_type="recap")]
    clips = animations.build_clips(
        scenes, [5.0] * 5, [[]] * 5, tmp_path, client=None, ffmpeg="", media_duration=None
    )
    assert sorted(clips) == [0, 1]
    assert sorted(built) == ["S0", "S1"]


# ---------------------------------------------------------------------------
# Storyboard and frames
# ---------------------------------------------------------------------------


def test_storyboard_schema_carries_animation_field() -> None:
    scene_schema = VIDEO_PLAN_SCHEMA["properties"]["scenes"]["items"]
    assert "animation" in scene_schema["required"]
    assert "animation" in scene_schema["properties"]["visual_type"]["enum"]
    instructions = video_module._video_script_instructions()
    assert "animations_available" in instructions


def test_animation_scene_without_description_is_flagged() -> None:
    plan = {
        "scenes": [
            {"visual_type": "animation", "animation": "", "narration": "word " * 30,
             "bullets": ["a", "b"], "visual_elements": ["a", "b"], "takeaway": "t"}
        ] * 3
    }
    issues = _video_plan_issues(plan, minimum_words=10, maximum_words=1000)
    assert any("does not describe it" in issue for issue in issues)


def test_animation_scene_falls_back_to_drawn_points_without_a_clip(tmp_path: Path) -> None:
    scene = _scene()
    assert reveal_count(scene) == 3
    stages = render_scene_stages(scene, tmp_path, index=2, total=3)
    assert len(stages) == 5  # title, three points, takeaway


def test_animation_scene_with_clip_leaves_room_for_it(tmp_path: Path) -> None:
    scene = _scene(clip_path=str(tmp_path / "clip.mp4"))
    assert reveal_count(scene) == 1
    stages = render_scene_stages(scene, tmp_path, index=2, total=3)
    assert len(stages) == 3  # title, hold while the clip plays, takeaway
    frame = Image.open(stages[-1]).convert("RGB")
    box = frame.crop(animations.ANIMATION_BOX)
    assert box.getcolors(maxcolors=4) is not None  # the clip area is left empty


def test_segment_overlays_clip_and_keeps_duration(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg_executable()
    clip = tmp_path / "clip.mp4"
    video_module._run_ffmpeg(
        ffmpeg, "-f", "lavfi", "-i",
        f"color=c=0xC8553D:s={animations.CLIP_WIDTH}x{animations.CLIP_HEIGHT}:d=1.5:r=30",
        "-pix_fmt", "yuv420p", str(clip),
    )
    scene = _scene(clip_path=str(clip))
    stages = render_scene_stages(scene, tmp_path, index=1, total=1)
    audio = tmp_path / "audio.wav"
    _tone(audio, 3.0)
    output = tmp_path / "segment.mp4"
    _render_segment(ffmpeg, stages[-1], audio, output, 4.0, stages=stages,
                    visual_type="animation", clip=clip)
    assert abs(_media_duration(ffmpeg, output) - 4.0) < 0.15

    # The clip's last image is held after the clip ends.
    frame_path = tmp_path / "frame.png"
    video_module._run_ffmpeg(ffmpeg, "-ss", "3.5", "-i", str(output), "-frames:v", "1",
                             str(frame_path))
    x1, y1, x2, y2 = animations.ANIMATION_BOX
    red, green, blue = Image.open(frame_path).convert("RGB").getpixel(((x1 + x2) // 2,
                                                                       (y1 + y2) // 2))
    assert red > 170 and green < 120 and blue < 100


@pytest.mark.skipif(not MANIM_INSTALLED, reason="manim is not installed")
def test_manim_renders_and_verifies_a_clip(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg_executable()
    clip = animations.render_scene_code(GOOD_CODE, tmp_path)
    animations.verify_clip(ffmpeg, clip, media_duration=_media_duration)
    assert 2.0 < _media_duration(ffmpeg, clip) < 2.6
    still = tmp_path / "still.png"
    video_module._run_ffmpeg(ffmpeg, "-sseof", "-0.2", "-i", str(clip), "-frames:v", "1",
                             "-update", "1", str(still))
    assert Image.open(still).size == (animations.CLIP_WIDTH, animations.CLIP_HEIGHT)
