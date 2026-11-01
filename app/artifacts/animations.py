"""Manim animations for STEM scenes in the explainer video (Phase 2).

For scenes the storyboard marks as `animation` (mathematics, graphs, physics, algorithms, moving
processes), a model writes a short Manim Community scene timed to the narration. The code is
checked against a strict allow-list, rendered in a separate, resource-limited process with no
secrets in its environment, and verified before use. A render that fails gets one repair attempt;
if it still fails, the scene falls back to its drawn diagram, so animations can only improve a
video, never stop it.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover
    from app.artifacts.video import VideoScene

logger = logging.getLogger(__name__)

# Where the animation sits inside the 1280x720 lesson frame: below the title, above the takeaway.
ANIMATION_BOX = (84, 200, 1196, 556)
CLIP_WIDTH = ANIMATION_BOX[2] - ANIMATION_BOX[0]  # 1112
CLIP_HEIGHT = ANIMATION_BOX[3] - ANIMATION_BOX[1]  # 356
FRAME_HEIGHT_UNITS = 6.0
FRAME_WIDTH_UNITS = round(FRAME_HEIGHT_UNITS * CLIP_WIDTH / CLIP_HEIGHT, 3)
BACKGROUND = "#F9FBFF"  # the lesson card colour, so the clip blends into the frame
MAX_CODE_CHARS = 15_000

# Trusted header prepended to every generated scene. Generated code may not touch `config`.
SCENE_HEADER = f'''from manim import *
import math
import numpy as np

config.frame_height = {FRAME_HEIGHT_UNITS}
config.frame_width = {FRAME_WIDTH_UNITS}
config.background_color = "{BACKGROUND}"

INK = "#10233F"
PRIMARY = "#1F8A84"
ACCENT = "#E3A33B"
ALERT = "#C8553D"
MUTED = "#5B6870"
SOFT = "#EAF4F2"

VMobject.set_default(color=INK)
for _text_type in (Text, MarkupText, Paragraph):
    _text_type.set_default(color=INK, font="DejaVu Sans")
for _tex_type in (MathTex, Tex):
    _tex_type.set_default(color=INK)
Dot.set_default(color=PRIMARY)
'''

ALLOWED_SCENE_BASES = {"Scene", "MovingCameraScene"}
BANNED_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "input", "breakpoint", "exit", "quit", "help", "memoryview",
    "os", "sys", "subprocess", "shutil", "pathlib", "socket", "importlib", "builtins",
    "config", "ImageMobject", "SVGMobject", "Code", "ThreeDScene", "random",
}
BANNED_ATTRIBUTES = {
    "system", "popen", "load", "save", "savez", "savez_compressed", "loadtxt", "savetxt",
    "fromfile", "tofile", "genfromtxt", "memmap", "ctypeslib", "f2py", "remove", "unlink",
    "rmtree", "write_text", "write_bytes", "read_text", "read_bytes",
}
LATEX_ONLY = {
    "MathTex", "Tex", "SingleStringMathTex", "Matrix", "IntegerMatrix", "DecimalMatrix",
    "MobjectMatrix", "DecimalNumber", "Integer", "Variable", "BarChart", "MathTable",
    "IntegerTable", "DecimalTable",
}


def animations_available() -> bool:
    return bool(
        settings.video_animations_enabled
        and settings.openai_api_key
        and importlib.util.find_spec("manim") is not None
    )


def latex_available() -> bool:
    return bool(shutil.which("latex") and shutil.which("dvisvgm"))


# ---------------------------------------------------------------------------
# Safety checks on generated code
# ---------------------------------------------------------------------------


def validate_scene_code(code: str, *, allow_latex: bool) -> list[str]:
    """Return problems that stop generated code from being rendered."""
    if len(code) > MAX_CODE_CHARS:
        return [f"The code is longer than {MAX_CODE_CHARS} characters."]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"Syntax error on line {exc.lineno}: {exc.msg}"]

    issues: list[str] = []
    scene_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in {"math", "numpy"}:
                    issues.append(f"Import of '{alias.name}' is not allowed.")
        elif isinstance(node, ast.ImportFrom):
            if node.module not in {"manim", "math"}:
                issues.append(f"Import from '{node.module}' is not allowed.")
        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES or node.id.startswith("__"):
                issues.append(f"'{node.id}' is not allowed.")
            if not allow_latex and node.id in LATEX_ONLY:
                issues.append(f"'{node.id}' needs LaTeX, which is not installed. Use Text.")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in BANNED_ATTRIBUTES:
                issues.append(f"Attribute '.{node.attr}' is not allowed.")
            if not allow_latex and node.attr == "add_coordinates":
                issues.append("'.add_coordinates' needs LaTeX, which is not installed.")
        elif isinstance(node, ast.keyword):
            if not allow_latex and node.arg == "include_numbers":
                issues.append("'include_numbers' needs LaTeX, which is not installed.")
        elif isinstance(node, (ast.While, ast.AsyncFunctionDef, ast.Await, ast.Global)):
            issues.append(f"'{type(node).__name__}' statements are not allowed.")
        elif isinstance(node, ast.ClassDef) and node.name == "LessonScene":
            bases = {base.id for base in node.bases if isinstance(base, ast.Name)}
            if bases & ALLOWED_SCENE_BASES:
                scene_found = True
            else:
                issues.append("LessonScene must inherit from Scene or MovingCameraScene.")
    if not scene_found:
        issues.append("Define `class LessonScene(Scene):` with a construct method.")
    return sorted(set(issues))


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

CODE_SCHEMA = {
    "type": "object",
    "properties": {"code": {"type": "string"}},
    "required": ["code"],
    "additionalProperties": False,
}


def coder_instructions(*, duration: float, allow_latex: bool) -> str:
    half_width = FRAME_WIDTH_UNITS / 2 - 0.4
    half_height = FRAME_HEIGHT_UNITS / 2 - 0.3
    latex_rule = (
        "For formulas use MathTex(r'...') with font sizes 36 to 48."
        if allow_latex
        else "LaTeX is NOT installed: never use MathTex, Tex, DecimalNumber, Integer, Matrix, "
        "BarChart, add_coordinates or include_numbers. Write formulas with Text and Unicode "
        "symbols such as ŷ, ², Σ, √ and ×."
    )
    return f"""
You write one Manim Community (v0.19+) scene that animates a single idea from a lesson video
while a narrator speaks. Return JSON {{"code": "..."}} containing only the Python code.

Rules:
- The environment has already run `from manim import *`, `import math`, `import numpy as np`,
  set up the canvas and defined colours. Do not change `config`. Do not import anything else.
- Define exactly `class LessonScene(Scene):` with `def construct(self):`.
- The canvas is wide and short: {FRAME_WIDTH_UNITS} units wide, {FRAME_HEIGHT_UNITS} units tall.
  Keep every object inside x from -{half_width:.1f} to {half_width:.1f} and y from
  -{half_height:.1f} to {half_height:.1f}. Arrange content left to right; use
  VGroup(...).arrange(RIGHT, buff=0.6), .next_to, .scale_to_fit_width and .scale_to_fit_height.
- The background is light. Colours: INK for text and lines, PRIMARY for main shapes, ACCENT for
  highlights, ALERT for mistakes, MUTED for secondary items, SOFT for light fills. Never use WHITE
  or very light colours for lines or text.
- Text: Text("...", font_size=32 to 44). Labels have five words or fewer. {latex_rule}
- Show at most six labelled objects at once; fade out or transform old ones before adding more.
- Timing: the whole scene must last {duration:.1f} seconds in total (sum of every run_time and
  self.wait). Follow the narration cues so each visual appears when it is spoken. Finish with
  self.wait(...) to fill the remaining time.
- Use standard objects (Axes, NumberPlane, NumberLine, Line, DashedLine, Arrow, Dot, Circle,
  Square, Rectangle, RoundedRectangle, Polygon, Triangle, VGroup, Text, Brace, BraceLabel,
  SurroundingRectangle, ValueTracker, always_redraw, TracedPath) and animations (Create, Write,
  FadeIn, FadeOut, Transform, ReplacementTransform, GrowArrow, GrowFromCenter, Indicate,
  Circumscribe, MoveAlongPath, Rotate, .animate).
- No images, SVG files, sounds, file or network access, randomness, while loops or 3D scenes.
- Show only what the scene description and narration say. Do not add new facts or numbers.
"""


def _request_code(client, *, instructions: str, payload: dict) -> str:
    model = settings.video_script_model or settings.openai_model
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False),
        max_output_tokens=6000,
        store=False,
        text={
            "format": {
                "type": "json_schema",
                "name": "manim_scene",
                "strict": True,
                "schema": CODE_SCHEMA,
            }
        },
        **({"reasoning": {"effort": "low"}} if model.startswith("gpt-5") else {}),
    )
    if getattr(response, "status", None) == "incomplete":
        raise RuntimeError("The animation code response was incomplete.")
    try:
        return str(json.loads(response.output_text)["code"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("The animation code response was not valid JSON.") from exc


# ---------------------------------------------------------------------------
# Rendering in a separate, limited process
# ---------------------------------------------------------------------------


# Runs in the child process: apply limits, then start Manim. Doing this in the child (rather than
# with preexec_fn) is safe when several renders start from worker threads at once.
_LAUNCHER = """
import resource, runpy, sys
cpu, memory, files = (int(value) for value in sys.argv[1:4])
resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
resource.setrlimit(resource.RLIMIT_FSIZE, (files, files))
sys.argv = ["manim", *sys.argv[4:]]
runpy.run_module("manim", run_name="__main__")
"""


def _render_command(timeout: int, manim_arguments: list[str]) -> list[str]:
    if os.name != "posix":  # pragma: no cover - resource limits are POSIX only
        return [sys.executable, "-m", "manim", *manim_arguments]
    limits = [str(timeout + 30), str(3 * 1024**3), str(512 * 1024**2)]
    return [sys.executable, "-c", _LAUNCHER, *limits, *manim_arguments]


def render_scene_code(code: str, workdir: Path) -> Path:
    """Render generated code to an MP4 clip of CLIP_WIDTH x CLIP_HEIGHT pixels."""
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "lesson_scene.py"
    script.write_text(SCENE_HEADER + "\n\n" + code, encoding="utf-8")
    media = workdir / "media"
    timeout = settings.video_animation_render_timeout_seconds
    # No secrets reach the renderer: only what Manim needs to run.
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "MPLCONFIGDIR": str(workdir),
        "PYTHONNOUSERSITE": "1",
        "LANG": "C.UTF-8",
    }
    completed = subprocess.run(
        _render_command(
            timeout,
            [
                "render", str(script), "LessonScene",
                "-o", "clip", "--format", "mp4", "-r", f"{CLIP_WIDTH},{CLIP_HEIGHT}",
                "--fps", "30", "--disable_caching", "--media_dir", str(media),
                "--progress_bar", "none", "-v", "WARNING",
            ],
        ),
        cwd=workdir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    clips = sorted(media.rglob("clip.mp4"))
    if completed.returncode != 0 or not clips:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise RuntimeError(detail[-2000:])
    return clips[0]


def verify_clip(ffmpeg: str, clip: Path, *, media_duration) -> None:
    """Reject a clip that is too short, blank or never changes."""
    from io import BytesIO

    from PIL import Image, ImageChops, ImageStat

    seconds = media_duration(ffmpeg, clip)
    if seconds < 1.0:
        raise RuntimeError("The animation is shorter than one second.")
    frames = []
    for fraction in (0.15, 0.55, 0.95):
        grabbed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{seconds * fraction:.2f}",
             "-i", str(clip), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True, check=False,
        )
        if grabbed.returncode or not grabbed.stdout:
            raise RuntimeError("The animation could not be read back.")
        with Image.open(BytesIO(grabbed.stdout)) as frame:
            frames.append(frame.convert("RGB"))
    if max(sum(ImageStat.Stat(frame).stddev) / 3 for frame in frames) < 2.0:
        raise RuntimeError("The animation is blank.")
    changed = any(
        ImageChops.difference(first, second).getbbox() for first, second in pairwise(frames)
    )
    if not changed:
        raise RuntimeError("The animation never changes.")


# ---------------------------------------------------------------------------
# One scene, and all scenes of a video
# ---------------------------------------------------------------------------


def build_animation(
    client,
    scene: VideoScene,
    *,
    workdir: Path,
    duration: float,
    cues: list[tuple[float, float, str]],
    ffmpeg: str,
    media_duration,
) -> Path | None:
    """Write, check, render and verify one animation; return None to use the drawn fallback."""
    allow_latex = latex_available()
    instructions = coder_instructions(duration=duration, allow_latex=allow_latex)
    payload: dict[str, Any] = {
        "scene_title": scene.title,
        "what_to_animate": scene.animation,
        "on_screen_points": list(scene.bullets),
        "labels": list(scene.visual_elements),
        "narration": scene.narration,
        "narration_cues": [
            {"from_second": start, "to_second": end, "spoken": text} for start, end, text in cues
        ],
        "total_seconds": round(duration, 1),
    }
    code = ""
    error = ""
    for attempt in range(1 + max(0, settings.video_animation_repair_attempts)):
        try:
            if attempt == 0:
                code = _request_code(client, instructions=instructions, payload=payload)
            else:
                code = _request_code(
                    client,
                    instructions=instructions,
                    payload={
                        **payload,
                        "previous_code": code,
                        "error": error,
                        "task": "Fix the code so it renders. Keep the same idea and timing.",
                    },
                )
            issues = validate_scene_code(code, allow_latex=allow_latex)
            if issues:
                error = "The code was rejected before rendering: " + " ".join(issues)
                continue
            clip = render_scene_code(code, workdir / f"attempt-{attempt + 1}")
            verify_clip(ffmpeg, clip, media_duration=media_duration)
            return clip
        except subprocess.TimeoutExpired:
            error = "Rendering took too long. Use fewer objects and simpler animations."
        except Exception as exc:  # noqa: BLE001 - any failure falls back to the drawn diagram
            error = (str(exc) or type(exc).__name__)[-2000:]
    logger.warning("Animation for '%s' fell back to a drawn diagram: %s", scene.title, error[:300])
    return None


def build_clips(
    scenes: list[VideoScene],
    durations: list[float],
    cues: list[list[tuple[float, float, str]]],
    workspace: Path,
    *,
    client,
    ffmpeg: str,
    media_duration,
) -> dict[int, Path]:
    """Render animations for the animation scenes, at most VIDEO_MAX_ANIMATIONS per video.

    `durations` is how long each clip should run; `cues` are the narration lines with times
    measured from the moment the clip starts.
    """
    if not animations_available():
        return {}
    wanted = [
        index
        for index, scene in enumerate(scenes)
        if scene.visual_type == "animation" and scene.animation.strip()
    ][: max(0, settings.video_max_animations)]
    if not wanted:
        return {}

    def work(index: int) -> tuple[int, Path | None]:
        return index, build_animation(
            client,
            scenes[index],
            workdir=workspace / f"animation-{index + 1:02d}",
            duration=max(2.0, durations[index]),
            cues=cues[index],
            ffmpeg=ffmpeg,
            media_duration=media_duration,
        )

    clips: dict[int, Path] = {}
    # Rendering is CPU-heavy; two at a time keeps the worker responsive.
    with ThreadPoolExecutor(max_workers=min(2, len(wanted)), thread_name_prefix="manim") as pool:
        for index, clip in pool.map(work, wanted):
            if clip is not None:
                clips[index] = clip
    return clips
