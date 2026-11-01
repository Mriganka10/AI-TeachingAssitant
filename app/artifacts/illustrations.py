"""Scene illustrations for the explainer video, generated with Google's Gemini image model.

Each storyboard scene may carry a short picture description. When illustrations are enabled and a
`GEMINI_API_KEY` is configured, those descriptions are turned into images in one consistent house
style. Labels, numbers and formulas are never drawn by the image model: the video renderer adds
all text itself, so what students read is always exact.

Every failure is non-fatal. A scene whose image cannot be generated keeps its drawn diagram, so a
missing key, a quota error or a network problem never stops a video.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover
    from app.artifacts.video import VideoScene

logger = logging.getLogger(__name__)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ILLUSTRATED_TYPES = ("illustration", "title")

# One house style keeps every scene of a video, and every video, looking like a set.
HOUSE_STYLE = (
    "Flat vector educational illustration in a clean, modern textbook style. Simple friendly "
    "shapes, soft deep-teal and warm amber accents, light neutral background, gentle shading, "
    "generous empty space, clear focus on one idea. Absolutely no text, letters, words, numbers, "
    "labels, captions, logos, signatures or watermarks anywhere in the image."
)


def illustrations_available() -> bool:
    return bool(settings.video_illustrations_enabled and settings.gemini_api_key)


def build_prompt(description: str, topic: str) -> str:
    """Combine the scene's picture description with the house style."""
    return (
        f"Illustration for a short lesson about {topic.strip() or 'this topic'}. "
        f"Show: {description.strip()} "
        f"{HOUSE_STYLE}"
    )


def generate_image(prompt: str, path: Path, *, attempts: int = 2) -> Path:
    """Call the Gemini image model and save the first returned image to `path`."""
    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "4:3"},
            },
        }
    ).encode("utf-8")
    url = GEMINI_ENDPOINT.format(model=settings.gemini_image_model)
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": settings.gemini_api_key or "",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=settings.gemini_image_timeout_seconds
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            image_bytes = _first_inline_image(payload)
            _save_verified_image(image_bytes, path)
            return path
        except urllib.error.HTTPError as exc:
            last_error = exc
            # Rate limits and transient server errors are worth one more try.
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                break
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Illustration could not be generated: {last_error}")


def _first_inline_image(payload: dict) -> bytes:
    for candidate in payload.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if part.get("thought"):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    reason = (payload.get("promptFeedback") or {}).get("blockReason") or "no image returned"
    raise ValueError(f"Gemini returned no image ({reason}).")


def _save_verified_image(data: bytes, path: Path) -> None:
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        image.load()
        if image.width < 64 or image.height < 64:
            raise ValueError("Gemini returned an unusably small image.")
        image.convert("RGB").save(path, format="PNG")


def attach_illustrations(
    scenes: list[VideoScene], workspace: Path, *, topic: str
) -> list[VideoScene]:
    """Generate images for illustrated scenes; scenes that fail keep their drawn layout."""
    if not illustrations_available():
        return scenes
    wanted = [
        index
        for index, scene in enumerate(scenes)
        if scene.visual_type in ILLUSTRATED_TYPES and scene.illustration.strip()
    ][: max(0, settings.video_max_illustrations)]
    if not wanted:
        return scenes

    def work(index: int) -> tuple[int, str]:
        path = workspace / f"illustration-{index + 1:02d}.png"
        try:
            generate_image(build_prompt(scenes[index].illustration, topic), path)
            return index, str(path)
        except Exception as exc:  # noqa: BLE001 - one failed image must not stop the video
            logger.warning("Scene %s illustration skipped: %s", index + 1, exc)
            return index, ""

    updated = list(scenes)
    with ThreadPoolExecutor(max_workers=min(4, len(wanted)), thread_name_prefix="illustration") as pool:
        for index, image_path in pool.map(work, wanted):
            if image_path:
                updated[index] = replace(updated[index], image_path=image_path)
    return updated


def paste_cover(image, source_path: str, box: tuple[int, int, int, int], *, radius: int = 22) -> bool:
    """Paste an illustration into `box`, cropped to fill it, with rounded corners."""
    from PIL import Image, ImageDraw, ImageOps

    try:
        with Image.open(source_path) as picture:
            picture = picture.convert("RGB")
            width, height = box[2] - box[0], box[3] - box[1]
            fitted = ImageOps.fit(picture, (width, height), method=Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return False
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    image.paste(fitted, (box[0], box[1]), mask)
    return True
