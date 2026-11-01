"""Short motion clips for picture scenes in the explainer video (Phase 3).

When `VIDEO_CINEMATIC_CLIPS` is on and a `GEMINI_API_KEY` is set, a few picture scenes (the
opening hook first) are brought to life with Google's Veo model: the scene's illustration is used
as the first frame and gently animated (a slow camera move, steam rising, water flowing). The clip
plays inside the picture's frame while the narrator speaks; all labels and text still come from
the renderer.

Clips are requested as soon as the illustrations exist, so they generate while narration is
recorded. Every failure is non-fatal: a scene without a clip keeps its still picture.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.artifacts.illustrations import ILLUSTRATED_TYPES
from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover
    from app.artifacts.video import VideoScene

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
ALLOWED_SECONDS = (4, 6, 8)
NEGATIVE_PROMPT = (
    "text, letters, words, numbers, captions, subtitles, labels, logos, watermarks, signatures, "
    "talking people, fast cuts, flashing, shaky camera, distorted shapes"
)
MOTION_STYLE = (
    "Animate this educational illustration with calm, slow, natural motion and a gentle camera "
    "move. Keep the same flat vector style, colours and composition as the first frame. No text "
    "appears at any time. Single continuous shot, no cuts."
)


def cinematic_available() -> bool:
    return bool(settings.video_cinematic_clips and settings.gemini_api_key)


def clip_seconds() -> int:
    """The configured clip length, snapped to a length Veo supports."""
    wanted = settings.video_cinematic_seconds
    return min(ALLOWED_SECONDS, key=lambda allowed: (abs(allowed - wanted), -allowed))


def build_prompt(motion: str, topic: str) -> str:
    return (
        f"Short opening shot for a lesson about {topic.strip() or 'this topic'}. "
        f"Motion: {motion.strip()} {MOTION_STYLE}"
    )


def eligible_scenes(scenes: list[VideoScene]) -> list[int]:
    """Picture scenes with a motion description, the opening scene first, capped by settings."""
    candidates = [
        index
        for index, scene in enumerate(scenes)
        if scene.visual_type in ILLUSTRATED_TYPES and scene.image_path and scene.cinematic.strip()
    ]
    candidates.sort(key=lambda index: (scenes[index].visual_type != "title", index))
    return sorted(candidates[: max(0, settings.video_max_cinematic_clips)])


# ---------------------------------------------------------------------------
# Veo REST calls
# ---------------------------------------------------------------------------


class _SameHostKeyRedirect(urllib.request.HTTPRedirectHandler):
    """Follow download redirects, but send the API key only to the API host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old_host = urllib.parse.urlsplit(req.full_url).hostname
            if urllib.parse.urlsplit(newurl).hostname != old_host:
                for name in list(new.headers):
                    if name.lower() == "x-goog-api-key":
                        del new.headers[name]
        return new


_opener = urllib.request.build_opener(_SameHostKeyRedirect)


def _call(url: str, *, body: dict | None = None, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key or ""},
    )
    with _opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def start_generation(prompt: str, image_path: str | None) -> str:
    """Submit a clip request and return the operation name.

    The scene's illustration is sent as the first frame. If the model rejects the image, the
    request is retried as text only, so a model without image input still produces a clip.
    """
    instance: dict[str, Any] = {"prompt": prompt}
    if image_path:
        instance["image"] = {
            "bytesBase64Encoded": base64.b64encode(Path(image_path).read_bytes()).decode("ascii"),
            "mimeType": "image/png",
        }
    parameters = {
        "aspectRatio": "16:9",
        "resolution": settings.veo_resolution,
        "durationSeconds": clip_seconds(),
        "negativePrompt": NEGATIVE_PROMPT,
    }
    url = f"{API_ROOT}/models/{settings.veo_model}:predictLongRunning"
    try:
        operation = _call(url, body={"instances": [instance], "parameters": parameters})
    except urllib.error.HTTPError as exc:
        if exc.code != 400 or "image" not in instance:
            raise
        logger.info("Veo rejected the first frame (%s); retrying as text only.", exc.code)
        instance.pop("image")
        operation = _call(url, body={"instances": [instance], "parameters": parameters})
    name = operation.get("name")
    if not name:
        raise RuntimeError("Veo did not return an operation name.")
    return str(name)


def wait_for_video(operation_name: str, *, deadline: float) -> str:
    """Poll until the operation finishes; return the generated video's download URI."""
    url = f"{API_ROOT}/{operation_name}"
    while True:
        status = _call(url)
        if status.get("done"):
            if status.get("error"):
                raise RuntimeError(f"Veo failed: {status['error'].get('message', 'unknown error')}")
            result = (status.get("response") or {}).get("generateVideoResponse") or {}
            samples = result.get("generatedSamples") or []
            uri = ((samples[0] if samples else {}).get("video") or {}).get("uri")
            if not uri:
                reasons = result.get("raiMediaFilteredReasons") or ["no video returned"]
                raise RuntimeError(f"Veo returned no clip: {'; '.join(map(str, reasons))}")
            return str(uri)
        if time.monotonic() > deadline:
            raise TimeoutError("Veo did not finish in time.")
        time.sleep(max(1, settings.veo_poll_interval_seconds))


def download_video(uri: str, path: Path) -> Path:
    if urllib.parse.urlsplit(uri).scheme != "https":
        raise RuntimeError("Veo returned an unexpected download address.")
    request = urllib.request.Request(uri, headers={"x-goog-api-key": settings.gemini_api_key or ""})
    with _opener.open(request, timeout=120) as response:
        data = response.read(200 * 1024 * 1024 + 1)
    if len(data) < 1024 or len(data) > 200 * 1024 * 1024:
        raise RuntimeError("Veo returned an unusable file.")
    path.write_bytes(data)
    return path


def generate_clip(scene: VideoScene, path: Path, *, topic: str, deadline: float) -> Path:
    operation = start_generation(build_prompt(scene.cinematic, topic), scene.image_path or None)
    return download_video(wait_for_video(operation, deadline=deadline), path)


# ---------------------------------------------------------------------------
# Running clips in the background while narration is recorded
# ---------------------------------------------------------------------------


class PendingClips:
    """Clip requests started early and collected just before scenes are encoded."""

    def __init__(self, futures: dict[int, Future], pool: ThreadPoolExecutor | None, deadline: float):
        self._futures = futures
        self._pool = pool
        self._deadline = deadline

    @property
    def scene_indexes(self) -> list[int]:
        return sorted(self._futures)

    def collect(self, *, ffmpeg: str, media_duration) -> dict[int, Path]:
        """Wait (up to the deadline) and return verified clips by scene index."""
        if not self._futures:
            return {}
        from app.artifacts.animations import verify_clip

        wait(self._futures.values(), timeout=max(0.0, self._deadline - time.monotonic()))
        clips: dict[int, Path] = {}
        for index, future in sorted(self._futures.items()):
            if not future.done():
                logger.warning("Scene %s motion clip skipped: not ready in time.", index + 1)
                continue
            try:
                clip = future.result()
                verify_clip(ffmpeg, clip, media_duration=media_duration)
                clips[index] = clip
            except Exception as exc:  # noqa: BLE001 - a failed clip keeps the still picture
                logger.warning("Scene %s motion clip skipped: %s", index + 1, exc)
        self.close()
        return clips

    def close(self) -> None:
        """Stop waiting for clips that have not started; safe to call more than once."""
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None


def start_clips(scenes: list[VideoScene], workspace: Path, *, topic: str) -> PendingClips:
    """Start Veo requests for eligible scenes and return immediately."""
    deadline = time.monotonic() + max(30, settings.veo_timeout_seconds)
    if not cinematic_available():
        return PendingClips({}, None, deadline)
    wanted = eligible_scenes(scenes)
    if not wanted:
        return PendingClips({}, None, deadline)
    pool = ThreadPoolExecutor(max_workers=min(2, len(wanted)), thread_name_prefix="veo")
    futures = {
        index: pool.submit(
            generate_clip,
            scenes[index],
            workspace / f"motion-{index + 1:02d}.mp4",
            topic=topic,
            deadline=deadline,
        )
        for index in wanted
    }
    return PendingClips(futures, pool, deadline)
