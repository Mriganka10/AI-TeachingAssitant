"""On-screen captions and a subtitle track for the explainer video.

Captions are drawn as small transparent images and overlaid by FFmpeg in the strip below the
lesson card, so they never cover a diagram and need no subtitle library in the FFmpeg build. The
same lines are written to an SRT track that players can switch on or off.

Timing is proportional to the length of each line within the scene's spoken audio. That is close
enough for reading along; it does not need word-level timestamps from the speech provider.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

FRAME_WIDTH = 1280
CAPTION_Y = 664  # below the lesson card, which ends at y=662
CAPTION_HEIGHT = 52
MAX_WORDS = 11
MIN_SECONDS = 0.9


def caption_chunks(text: str, *, max_words: int = MAX_WORDS) -> list[str]:
    """Split narration into short, readable caption lines at natural breaks."""
    chunks: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(str(text or "").split())):
        words = sentence.split()
        while words:
            if len(words) <= max_words:
                chunks.append(" ".join(words))
                break
            # Prefer to break after a comma or semicolon inside the allowed length.
            cut = max_words
            for position in range(max_words, max(2, max_words // 2) - 1, -1):
                if words[position - 1].endswith((",", ";", ":")):
                    cut = position
                    break
            chunks.append(" ".join(words[:cut]))
            words = words[cut:]
    return [chunk for chunk in chunks if chunk]


def timed_captions(text: str, speech_seconds: float) -> list[tuple[float, float, str]]:
    """Spread caption lines across the spoken part of a scene, in proportion to their length."""
    chunks = caption_chunks(text)
    if not chunks or speech_seconds <= 0:
        return []
    weights = [len(chunk) + 6 for chunk in chunks]
    total = sum(weights)
    entries: list[tuple[float, float, str]] = []
    start = 0.0
    for chunk, weight in zip(chunks, weights, strict=True):
        length = max(MIN_SECONDS, speech_seconds * weight / total)
        end = min(speech_seconds, start + length)
        if end - start < 0.3:
            break
        entries.append((round(start, 3), round(end, 3), chunk))
        start = end
    return entries


def render_caption_image(text: str, path: Path) -> Path:
    """Draw one caption line: white text on a soft dark pill, transparent elsewhere."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (FRAME_WIDTH, CAPTION_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    size = 24
    font = _font(size)
    line = textwrap.shorten(text, width=96, placeholder="…")
    while size > 16 and draw.textlength(line, font=font) > FRAME_WIDTH - 140:
        size -= 1
        font = _font(size)
    width = int(draw.textlength(line, font=font))
    left = (FRAME_WIDTH - width) // 2
    draw.rounded_rectangle(
        (left - 18, 6, left + width + 18, CAPTION_HEIGHT - 6), radius=14, fill=(8, 22, 26, 205)
    )
    draw.text((left, (CAPTION_HEIGHT - size) // 2 - 2), line, font=font, fill=(255, 255, 255, 255))
    image.save(path, format="PNG")
    return path


def srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(entries: list[tuple[float, float, str]], path: Path) -> Path:
    blocks = [
        f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(entries, start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()
