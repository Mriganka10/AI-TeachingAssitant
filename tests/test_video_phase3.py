"""Phase 3 video quality: short Veo motion clips for picture scenes, with a safe fallback."""

import base64
import io
import json
import math
import struct
import time
import urllib.error
import urllib.request
import wave
from concurrent.futures import Future
from pathlib import Path

import pytest
from PIL import Image

from app.artifacts import animations, cinematic
from app.artifacts import video as video_module
from app.artifacts.video import (
    ILLUSTRATION_TITLE_BOX,
    VIDEO_PLAN_SCHEMA,
    VideoScene,
    _ffmpeg_executable,
    _media_duration,
    _render_segment,
    clip_placement,
    illustration_picture_box,
    render_scene_stages,
)
from app.core.config import settings

VIDEO_URI = "https://generativelanguage.googleapis.com/v1beta/files/abc:download?alt=media"


def _scene(**changes) -> VideoScene:
    values = {
        "section": "LESSON",
        "title": "Why lines help",
        "bullets": ("Prices", "Growth"),
        "narration": "Every day you meet trends without noticing them.",
        "visual_type": "illustration",
        "visual_elements": ("Prices", "Growth"),
        "takeaway": "Trends are everywhere.",
        "illustration": "a market stall",
        "image_path": "picture.png",
        "cinematic": "The camera slowly pushes in.",
    }
    values.update(changes)
    return VideoScene(**values)


class _Response:
    def __init__(self, payload) -> None:
        self._data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self._data


class _FakeOpener:
    """Answers Veo calls in order and records each request."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[urllib.request.Request] = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Response(answer)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "error", {}, io.BytesIO(b"{}"))


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


@pytest.fixture
def veo_on(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")
    monkeypatch.setattr(settings, "video_cinematic_clips", True)
    monkeypatch.setattr(settings, "veo_poll_interval_seconds", 1)
    monkeypatch.setattr(cinematic.time, "sleep", lambda *_: None)


# ---------------------------------------------------------------------------
# Settings and scene choice
# ---------------------------------------------------------------------------


def test_clips_are_off_by_default_and_need_a_key(monkeypatch) -> None:
    assert type(settings).model_fields["video_cinematic_clips"].default is False
    monkeypatch.setattr(settings, "video_cinematic_clips", True)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    assert cinematic.cinematic_available() is False


@pytest.mark.parametrize(("wanted", "expected"), [(1, 4), (5, 6), (7, 8), (8, 8), (30, 8)])
def test_clip_length_snaps_to_a_supported_value(monkeypatch, wanted, expected) -> None:
    monkeypatch.setattr(settings, "video_cinematic_seconds", wanted)
    assert cinematic.clip_seconds() == expected


def test_title_scene_is_chosen_first_and_the_count_is_capped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "video_max_cinematic_clips", 2)
    scenes = [
        _scene(visual_type="key_points"),  # not a picture scene
        _scene(),
        _scene(image_path=""),  # no picture was generated
        _scene(cinematic=""),  # no motion asked for
        _scene(),
        _scene(visual_type="title"),
    ]
    assert cinematic.eligible_scenes(scenes) == [1, 5]


# ---------------------------------------------------------------------------
# Veo requests
# ---------------------------------------------------------------------------


def test_request_sends_first_frame_and_parameters(tmp_path: Path, veo_on, monkeypatch) -> None:
    picture = tmp_path / "picture.png"
    Image.new("RGB", (40, 30), "#1F8A84").save(picture)
    opener = _FakeOpener({"name": "models/veo/operations/1"})
    monkeypatch.setattr(cinematic, "_opener", opener)
    monkeypatch.setattr(settings, "video_cinematic_seconds", 6)

    assert cinematic.start_generation("Leaves sway.", str(picture)) == "models/veo/operations/1"
    request = opener.requests[0]
    assert request.full_url.endswith(f"/models/{settings.veo_model}:predictLongRunning")
    assert request.get_header("X-goog-api-key") == "test-gemini-key"
    body = json.loads(request.data)
    image = body["instances"][0]["image"]
    assert base64.b64decode(image["bytesBase64Encoded"]) == picture.read_bytes()
    assert body["parameters"]["durationSeconds"] == 6
    assert body["parameters"]["aspectRatio"] == "16:9"
    assert "text" in body["parameters"]["negativePrompt"]


def test_rejected_first_frame_retries_as_text_only(tmp_path: Path, veo_on, monkeypatch) -> None:
    picture = tmp_path / "picture.png"
    Image.new("RGB", (40, 30)).save(picture)
    opener = _FakeOpener(_http_error(400), {"name": "operations/2"})
    monkeypatch.setattr(cinematic, "_opener", opener)
    assert cinematic.start_generation("Prompt", str(picture)) == "operations/2"
    assert "image" not in json.loads(opener.requests[1].data)["instances"][0]


def test_other_request_errors_are_not_retried(veo_on, monkeypatch) -> None:
    opener = _FakeOpener(_http_error(429))
    monkeypatch.setattr(cinematic, "_opener", opener)
    with pytest.raises(urllib.error.HTTPError):
        cinematic.start_generation("Prompt", None)
    assert len(opener.requests) == 1


def test_polling_returns_the_video_address(veo_on, monkeypatch) -> None:
    done = {"done": True, "response": {"generateVideoResponse": {
        "generatedSamples": [{"video": {"uri": VIDEO_URI}}]}}}
    opener = _FakeOpener({"done": False}, {"done": False}, done)
    monkeypatch.setattr(cinematic, "_opener", opener)
    uri = cinematic.wait_for_video("operations/3", deadline=time.monotonic() + 60)
    assert uri == VIDEO_URI
    assert opener.requests[0].full_url == f"{cinematic.API_ROOT}/operations/3"


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({"done": True, "error": {"message": "quota"}}, "quota"),
        ({"done": True, "response": {"generateVideoResponse": {
            "raiMediaFilteredReasons": ["blocked by safety filter"]}}}, "safety filter"),
    ],
)
def test_failed_operations_raise(veo_on, monkeypatch, answer, message) -> None:
    monkeypatch.setattr(cinematic, "_opener", _FakeOpener(answer))
    with pytest.raises(RuntimeError, match=message):
        cinematic.wait_for_video("operations/4", deadline=time.monotonic() + 60)


def test_polling_stops_at_the_deadline(veo_on, monkeypatch) -> None:
    monkeypatch.setattr(cinematic, "_opener", _FakeOpener({"done": False}))
    with pytest.raises(TimeoutError):
        cinematic.wait_for_video("operations/5", deadline=time.monotonic() - 1)


def test_download_refuses_non_https_addresses(tmp_path: Path, veo_on) -> None:
    with pytest.raises(RuntimeError, match="unexpected"):
        cinematic.download_video("http://example.com/video.mp4", tmp_path / "clip.mp4")


def test_api_key_is_not_sent_to_another_host_on_redirect() -> None:
    handler = cinematic._SameHostKeyRedirect()
    original = urllib.request.Request(VIDEO_URI, headers={"x-goog-api-key": "secret"})
    same = handler.redirect_request(
        original, None, 302, "Found", {}, "https://generativelanguage.googleapis.com/other"
    )
    other = handler.redirect_request(
        original, None, 302, "Found", {}, "https://storage.example.com/video.mp4"
    )
    assert same.get_header("X-goog-api-key") == "secret"
    assert other.get_header("X-goog-api-key") is None


# ---------------------------------------------------------------------------
# Background collection and fallback
# ---------------------------------------------------------------------------


def test_nothing_starts_when_clips_are_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "video_cinematic_clips", False)
    pending = cinematic.start_clips([_scene()], tmp_path, topic="Trends")
    assert pending.scene_indexes == []
    assert pending.collect(ffmpeg="", media_duration=None) == {}


def test_failed_or_bad_clips_are_skipped(tmp_path: Path, monkeypatch) -> None:
    good, bad, failed = Future(), Future(), Future()
    good.set_result(tmp_path / "good.mp4")
    bad.set_result(tmp_path / "bad.mp4")
    failed.set_exception(RuntimeError("quota"))

    def fake_verify(ffmpeg, clip, *, media_duration):
        if clip.name == "bad.mp4":
            raise RuntimeError("The animation is blank.")

    monkeypatch.setattr(animations, "verify_clip", fake_verify)
    pending = cinematic.PendingClips({0: good, 2: bad, 4: failed}, None, time.monotonic() + 5)
    assert pending.collect(ffmpeg="", media_duration=None) == {0: tmp_path / "good.mp4"}


def test_unfinished_clips_are_skipped_at_the_deadline(tmp_path: Path) -> None:
    pending = cinematic.PendingClips({1: Future()}, None, time.monotonic() - 1)
    assert pending.collect(ffmpeg="", media_duration=None) == {}


# ---------------------------------------------------------------------------
# Storyboard, placement and encoding
# ---------------------------------------------------------------------------


def test_storyboard_schema_carries_motion_field() -> None:
    scene_schema = VIDEO_PLAN_SCHEMA["properties"]["scenes"]["items"]
    assert "cinematic" in scene_schema["required"]
    assert "motion_clips_available" in video_module._video_script_instructions()


def test_clip_placement_matches_the_picture_frame() -> None:
    assert clip_placement(_scene(visual_type="title"))[0] == ILLUSTRATION_TITLE_BOX
    assert clip_placement(_scene(visual_type="animation")) == (animations.ANIMATION_BOX, 0, 1.0)
    one_line = illustration_picture_box(_scene(title="Short"))
    two_lines = illustration_picture_box(_scene(title="A much longer title " * 4))
    assert clip_placement(_scene(title="Short"))[0] == one_line
    assert two_lines[1] > one_line[1]  # the picture moves down below a two-line title


def test_motion_clip_fills_the_picture_with_rounded_corners(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg_executable()
    picture = tmp_path / "picture.png"
    Image.new("RGB", (800, 600), "#1F8A84").save(picture)
    clip = tmp_path / "veo.mp4"
    video_module._run_ffmpeg(
        ffmpeg, "-f", "lavfi", "-i", "color=c=0xC8553D:s=1280x720:d=2:r=24",
        "-pix_fmt", "yuv420p", str(clip),
    )
    scene = _scene(image_path=str(picture), clip_path=str(clip))
    stages = render_scene_stages(scene, tmp_path, index=1, total=1)
    audio = tmp_path / "audio.wav"
    _tone(audio, 3.5)
    output = tmp_path / "segment.mp4"
    box, radius, stretch = clip_placement(scene)
    _render_segment(ffmpeg, stages[-1], audio, output, 4.0, stages=stages,
                    visual_type="illustration", clip=clip, clip_box=box,
                    clip_radius=radius, clip_max_stretch=stretch)
    assert abs(_media_duration(ffmpeg, output) - 4.0) < 0.15

    frame_path = tmp_path / "frame.png"
    video_module._run_ffmpeg(ffmpeg, "-ss", "3", "-i", str(output), "-frames:v", "1",
                             str(frame_path))
    frame = Image.open(frame_path).convert("RGB")
    x1, y1, x2, y2 = box
    red, green, _ = frame.getpixel(((x1 + x2) // 2, (y1 + y2) // 2))
    assert red > 170 and green < 120  # the clip covers the picture
    corner = frame.getpixel((x1 + 1, y1 + 1))
    assert corner[0] > 220 and corner[2] > 220  # the rounded corner shows the light card
