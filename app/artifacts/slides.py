"""Student-facing lesson decks built from native, editable PowerPoint shapes.

Every visual on a slide is a real PowerPoint object: diagrams, icons and decorative drawings are
composed from autoshapes and freeforms, and slides carry native transitions and entrance
animations. Nothing is a flattened picture, so a presenter can edit any label, colour or step.

Animations play automatically ("after previous") so a learner who opens the deck alone still sees
each idea build up. In edit view and in PDF export every element is visible.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from app.artifacts.generator import (
    _as_list,
    _clean_url,
    _contains_math,
    _mcq_option_text,
    _plain_text,
)

SLIDE_WIDTH = 13.333
SLIDE_HEIGHT = 7.5
MARGIN = 0.6
HEAD_FONT = "Cambria"
BODY_FONT = "Calibri"
MATH_FONT = "Cambria Math"

DIAGRAM_TYPES = ("process", "cycle", "comparison", "concept_map", "hierarchy", "timeline")


@dataclass(frozen=True)
class Palette:
    dark: str  # dominant colour for cover, recap and emphasis
    primary: str  # diagram nodes and icon circles
    accent: str  # one sharp highlight
    alert: str  # misconceptions and limits
    tint: str  # soft card background
    warm_tint: str  # soft highlight background
    alert_tint: str
    ink: str = "1F2A30"
    muted: str = "5B6870"
    line: str = "D6DEE2"
    canvas: str = "FFFFFF"
    on_dark_muted: str = "B9CCCF"


TEACHING = Palette(
    dark="0E4249",
    primary="1F8A84",
    accent="E3A33B",
    alert="C8553D",
    tint="EAF4F2",
    warm_tint="FCF1DE",
    alert_tint="FBE9E4",
)
# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def create_presentation(result: dict, path: Path, *, author: str = "") -> Path:
    deck = Presentation()
    deck.slide_width = Inches(SLIDE_WIDTH)
    deck.slide_height = Inches(SLIDE_HEIGHT)
    _build_teaching_deck(DeckBuilder(deck, TEACHING), result)
    _set_metadata(deck, result, author=author)
    deck.save(path)
    return path


def _set_metadata(deck, result: dict, *, author: str) -> None:
    """Neutral document properties; replaces the library template's defaults."""
    now = datetime.now(UTC).replace(tzinfo=None)
    props = deck.core_properties
    props.title = _plain_text(result.get("title"), "Presentation")
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    subject = _plain_text(overview.get("course"))
    props.subject = "" if subject.lower() == "general" else subject
    props.author = author
    props.last_modified_by = author
    props.comments = ""
    props.keywords = ""
    props.category = ""
    props.revision = 1
    props.created = now
    props.modified = now


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def clean(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(
            f"{key.replace('_', ' ').capitalize()}: {_plain_text(item)}"
            for key, item in value.items()
            if item not in (None, "", [])
        )
    return _plain_text(value).strip()


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def estimate_lines(text: str, width: float, size: float, *, bold: bool = False) -> int:
    usable = max(0.4, width - 0.05)
    char_width = size * (0.53 if bold else 0.49) / 72
    per_line = max(6, int(usable / char_width))
    total = 0
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        if not words:
            total += 1
            continue
        line_length = 0
        lines = 1
        for word in words:
            needed = len(word) + (1 if line_length else 0)
            if line_length + needed > per_line:
                lines += 1
                line_length = len(word)
            else:
                line_length += needed
        total += lines
    return total


def text_height(text: str, width: float, size: float, *, bold: bool = False, spacing=1.18) -> float:
    return estimate_lines(text, width, size, bold=bold) * size * spacing / 72


def fit_text(
    text: Any,
    width: float,
    height: float,
    *,
    preferred: float,
    minimum: float,
    bold: bool = False,
) -> tuple[str, float]:
    """Choose a font size that fits; if needed, keep whole sentences rather than cut words."""
    value = clean(text)
    if not value:
        return "", preferred
    size = preferred
    while size >= minimum:
        if text_height(value, width, size, bold=bold) <= height:
            return value, size
        size -= 1
    parts = sentences(value)
    kept: list[str] = []
    for part in parts:
        candidate = " ".join([*kept, part])
        if kept and text_height(candidate, width, minimum, bold=bold) > height:
            break
        kept.append(part)
    return " ".join(kept) or value, minimum


def short(value: Any, words: int) -> str:
    """Shorten to a word budget at a natural boundary, for diagram labels."""
    text = clean(value)
    tokens = text.split()
    if len(tokens) <= words:
        return text.rstrip(".")
    clause = re.split(r"[,;:.(]", text)[0].split()
    if 2 <= len(clause) <= words:
        return " ".join(clause)
    return " ".join(tokens[:words]).rstrip(",;:")


def rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value)


# ---------------------------------------------------------------------------
# Slide builder: primitives, icons, diagrams, animation
# ---------------------------------------------------------------------------


class DeckBuilder:
    def __init__(self, deck, palette: Palette):
        self.deck = deck
        self.palette = palette
        self.number = 0

    # -- slides -----------------------------------------------------------------------------
    def new_slide(self, *, dark: bool = False, transition: str = "fade") -> SlideCanvas:
        slide = self.deck.slides.add_slide(self.deck.slide_layouts[6])
        self.number += 1
        canvas = SlideCanvas(slide, self.palette, dark=dark, number=self.number)
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = rgb(self.palette.dark if dark else self.palette.canvas)
        canvas.transition = transition
        return canvas

    def content_slide(self, title: str, kicker: str = "") -> SlideCanvas:
        canvas = self.new_slide()
        canvas.header(title, kicker)
        canvas.page_number()
        return canvas


class SlideCanvas:
    def __init__(self, slide, palette: Palette, *, dark: bool, number: int):
        self.slide = slide
        self.palette = palette
        self.dark = dark
        self.number = number
        self.transition = "fade"
        self.steps: list[list[tuple[Any, str]]] = []

    # -- animation bookkeeping --------------------------------------------------------------
    def step(self, *shapes, effect: str = "fade") -> None:
        """Queue shapes that appear together; steps play one after another."""
        items = [(shape, effect) for shape in shapes if shape is not None]
        if items:
            self.steps.append(items)

    def also(self, *shapes, effect: str = "fade") -> None:
        """Add shapes to the most recent step so they appear with it."""
        if not self.steps:
            self.step(*shapes, effect=effect)
            return
        self.steps[-1].extend((shape, effect) for shape in shapes if shape is not None)

    def finish(self) -> None:
        apply_transition(self.slide, self.transition)
        apply_animation(self.slide, self.steps)

    # -- primitives -------------------------------------------------------------------------
    def text(
        self,
        value: Any,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        size: float = 16,
        color: str | None = None,
        bold: bool = False,
        italic: bool = False,
        font: str = BODY_FONT,
        align=PP_ALIGN.LEFT,
        anchor=MSO_ANCHOR.TOP,
        minimum: float | None = None,
        spacing: float = 1.1,
    ):
        content = clean(value)
        if minimum is not None:
            content, size = fit_text(content, w, h, preferred=size, minimum=minimum, bold=bold)
        box = self.slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        frame = box.text_frame
        _plain_frame(frame, anchor=anchor)
        for index, paragraph_text in enumerate(content.split("\n") or [""]):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.alignment = align
            paragraph.line_spacing = spacing
            run = paragraph.add_run()
            run.text = paragraph_text
            _style_run(
                run,
                size=size,
                color=color or (self.palette.canvas if self.dark else self.palette.ink),
                bold=bold,
                italic=italic,
                font=MATH_FONT if _contains_math(paragraph_text) else font,
            )
        return box

    def bullets(
        self,
        items: list[str],
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        size: float = 18,
        minimum: float = 14,
        color: str | None = None,
        marker_color: str | None = None,
        numbered: bool = False,
    ):
        values = [clean(item) for item in items if clean(item)]
        joined = "\n".join(values)
        _, size = fit_text(joined, w - 0.35, h - 0.1 * len(values), preferred=size, minimum=minimum)
        while len(values) > 1 and text_height("\n".join(values), w - 0.35, size) + 0.1 * len(
            values
        ) > h:
            values.pop()
        box = self.slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        frame = box.text_frame
        _plain_frame(frame)
        for index, value in enumerate(values):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.line_spacing = 1.08
            paragraph.space_after = Pt(size * 0.55)
            _bullet(
                paragraph,
                marker_color or self.palette.primary,
                size,
                number=index + 1 if numbered else None,
            )
            run = paragraph.add_run()
            run.text = value
            _style_run(
                run,
                size=size,
                color=color or (self.palette.canvas if self.dark else self.palette.ink),
                font=MATH_FONT if _contains_math(value) else BODY_FONT,
            )
        return box

    def shape(
        self,
        kind,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str | None = None,
        line: str | None = None,
        line_width: float = 1.25,
        shadow: bool = False,
        alpha: int | None = None,
        rotation: float = 0,
        radius: float | None = None,
    ):
        item = self.slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
        if fill:
            item.fill.solid()
            item.fill.fore_color.rgb = rgb(fill)
            if alpha is not None:
                _set_alpha(item, alpha)
        else:
            item.fill.background()
        if line:
            item.line.color.rgb = rgb(line)
            item.line.width = Pt(line_width)
        else:
            item.line.fill.background()
        if radius is not None and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
            item.adjustments[0] = radius
        if rotation:
            item.rotation = rotation
        _set_shadow(item, shadow)
        if item.has_text_frame:
            _plain_frame(item.text_frame, anchor=MSO_ANCHOR.MIDDLE)
        return item

    def card(self, x, y, w, h, *, fill: str | None = None, shadow: bool = True):
        return self.shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            x,
            y,
            w,
            h,
            fill=fill or self.palette.tint,
            shadow=shadow,
            radius=min(0.12, 0.18 / max(0.4, min(w, h))),
        )

    def label_in(self, shape, value: str, *, size: float, color: str, bold=True, font=BODY_FONT):
        frame = shape.text_frame
        _plain_frame(frame, anchor=MSO_ANCHOR.MIDDLE, margin=0.06)
        paragraph = frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        run = paragraph.add_run()
        run.text = value
        _style_run(run, size=size, color=color, bold=bold, font=font)
        return shape

    def line(self, x1, y1, x2, y2, *, color: str, width: float = 2.0, arrow: bool = False):
        connector = self.slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2)
        )
        connector.line.color.rgb = rgb(color)
        connector.line.width = Pt(width)
        if arrow:
            ln = connector.line._get_or_add_ln()
            tail = etree.SubElement(ln, qn("a:tailEnd"))
            tail.set("type", "triangle")
            tail.set("w", "med")
            tail.set("len", "med")
        return connector

    def polyline(self, points: list[tuple[float, float]], *, color: str, width: float = 2.5):
        builder = self.slide.shapes.build_freeform(
            Inches(points[0][0]), Inches(points[0][1]), scale=1.0
        )
        builder.add_line_segments([(Inches(px), Inches(py)) for px, py in points[1:]], close=False)
        shape = builder.convert_to_shape()
        shape.fill.background()
        shape.line.color.rgb = rgb(color)
        shape.line.width = Pt(width)
        _set_shadow(shape, False)
        ln = shape.line._get_or_add_ln()
        ln.set("cap", "rnd")
        etree.SubElement(ln, qn("a:round"))
        return shape

    def header(self, title: str, kicker: str = "") -> None:
        title_text, size = fit_text(title, 11.6, 0.95, preferred=34, minimum=24, bold=True)
        if kicker:
            self.text(
                kicker.upper(), MARGIN, 0.38, 9.5, 0.3, size=12, color=self.palette.primary, bold=True
            )
        self.text(
            title_text,
            MARGIN,
            0.66,
            11.9,
            1.0,
            size=size,
            bold=True,
            font=HEAD_FONT,
            color=self.palette.ink,
            anchor=MSO_ANCHOR.TOP,
        )

    def page_number(self) -> None:
        self.text(
            str(self.number),
            SLIDE_WIDTH - 1.1,
            SLIDE_HEIGHT - 0.5,
            0.6,
            0.3,
            size=10,
            color=self.palette.muted,
            align=PP_ALIGN.RIGHT,
        )

    # -- icons ------------------------------------------------------------------------------
    def icon(self, kind: str, x: float, y: float, d: float = 0.62, *, fill=None, fg=None):
        """Draw a simple icon inside a filled circle; returns every shape for animation."""
        fill = fill or self.palette.primary
        fg = fg or "FFFFFF"
        shapes = [self.shape(MSO_SHAPE.OVAL, x, y, d, d, fill=fill)]
        cx, cy, s = x + d / 2, y + d / 2, d / 2
        draw = _ICON_DRAWERS.get(kind, _icon_dot)
        shapes.extend(draw(self, cx, cy, s, fg, fill))
        return shapes

    # -- diagrams ---------------------------------------------------------------------------
    def diagram(self, spec: dict, x: float, y: float, w: float, h: float) -> None:
        kind = spec.get("type") if spec.get("type") in DIAGRAM_TYPES else "concept_map"
        nodes = [
            {"label": short(node.get("label"), 8), "detail": short(node.get("detail"), 16)}
            for node in _as_list(spec.get("nodes"))
            if isinstance(node, dict) and clean(node.get("label"))
        ]
        if len(nodes) < 2:
            return
        title = short(spec.get("title"), 6)
        builders = {
            "process": self._diagram_process,
            "timeline": self._diagram_timeline,
            "cycle": self._diagram_cycle,
            "comparison": self._diagram_comparison,
            "hierarchy": self._diagram_hierarchy,
            "concept_map": self._diagram_concept_map,
        }
        builders[kind](title, nodes, x, y, w, h)

    def _node_text(self, node, x, y, w, h, *, label_size=15, color=None, center=True):
        align = PP_ALIGN.CENTER if center else PP_ALIGN.LEFT
        label_h = min(h, text_height(node["label"], w, label_size, bold=True) + 0.08)
        label = self.text(
            node["label"],
            x,
            y,
            w,
            label_h,
            size=label_size,
            bold=True,
            color=color or self.palette.ink,
            align=align,
            minimum=12,
        )
        detail = None
        if node["detail"] and h - label_h > 0.3:
            detail = self.text(
                node["detail"],
                x,
                y + label_h + 0.02,
                w,
                h - label_h - 0.02,
                size=13,
                color=self.palette.muted,
                align=align,
                minimum=11,
            )
        return [label, detail]

    def _node_height(self, node, width, *, label_size=15) -> float:
        height = text_height(node["label"], width, label_size, bold=True) + 0.08
        if node["detail"]:
            height += text_height(node["detail"], width, 13) + 0.06
        return height

    def _diagram_process(self, title, nodes, x, y, w, h):
        nodes = nodes[:6]
        count = len(nodes)
        if w / count < 2.2:
            self._diagram_steps(nodes, x, y, w, h)
            return
        gap = 0.32
        node_w = (w - gap * (count - 1)) / count
        circle = 0.56
        card_h = max(self._node_height(node, node_w - 0.3) for node in nodes) + 0.35
        card_h = min(h - 1.05, max(1.0, card_h))
        y += max(0.0, (h - 1.05 - card_h) / 2)  # centre a short diagram in its area
        line_y = y + 0.28 + circle / 2
        track = self.line(
            x + node_w / 2, line_y, x + w - node_w / 2, line_y, color=self.palette.line, width=3
        )
        self.step(track, effect="wipe_left")
        for index, node in enumerate(nodes):
            nx = x + index * (node_w + gap)
            badge = self.shape(
                MSO_SHAPE.OVAL, nx + node_w / 2 - circle / 2, y + 0.28, circle, circle,
                fill=self.palette.primary if index % 2 == 0 else self.palette.dark,
            )
            self.label_in(badge, str(index + 1), size=16, color="FFFFFF")
            body = self.card(nx, y + 1.05, node_w, card_h, fill=self.palette.tint, shadow=False)
            texts = self._node_text(node, nx + 0.15, y + 1.2, node_w - 0.3, card_h - 0.3)
            self.step(badge, body, *texts)

    def _diagram_steps(self, nodes, x, y, w, h):
        """Vertical numbered steps on a rail, used when columns would be too narrow."""
        count = len(nodes)
        row_h = min(1.25, h / count)
        y += max(0.0, (h - row_h * count) / 2)
        circle = 0.5
        rail = self.line(x + circle / 2, y + circle / 2, x + circle / 2,
                         y + row_h * (count - 1) + circle / 2, color=self.palette.line, width=3)
        self.step(rail, effect="wipe_down")
        for index, node in enumerate(nodes):
            ny = y + index * row_h
            badge = self.shape(MSO_SHAPE.OVAL, x, ny, circle, circle,
                               fill=self.palette.primary if index % 2 == 0 else self.palette.dark)
            self.label_in(badge, str(index + 1), size=15, color="FFFFFF")
            texts = self._node_text(node, x + circle + 0.3, ny + 0.02, w - circle - 0.3,
                                    row_h - 0.1, label_size=16, center=False)
            self.step(badge, *texts)

    def _diagram_timeline(self, title, nodes, x, y, w, h):
        nodes = nodes[:6]
        count = len(nodes)
        mid = y + h / 2
        axis = self.line(x, mid, x + w, mid, color=self.palette.primary, width=3, arrow=True)
        self.step(axis, effect="wipe_left")
        slot = w / count
        for index, node in enumerate(nodes):
            cx = x + slot * index + slot / 2
            dot = self.shape(MSO_SHAPE.OVAL, cx - 0.13, mid - 0.13, 0.26, 0.26,
                             fill=self.palette.accent)
            above = index % 2 == 0
            box_h = h / 2 - 0.35
            box_y = mid - 0.3 - box_h if above else mid + 0.3
            stem = self.line(cx, mid - 0.13 if above else mid + 0.13,
                             cx, box_y + box_h if above else box_y,
                             color=self.palette.line, width=1.5)
            texts = self._node_text(node, cx - slot / 2 + 0.05, box_y, slot - 0.1, box_h,
                                    label_size=14)
            self.step(dot, stem, *texts)

    def _diagram_cycle(self, title, nodes, x, y, w, h):
        nodes = nodes[:6]
        count = len(nodes)
        cx, cy = x + w / 2, y + h / 2
        rx, ry = w / 2 - 1.05, h / 2 - 0.62
        ring = self.shape(MSO_SHAPE.OVAL, cx - rx, cy - ry, 2 * rx, 2 * ry,
                          line=self.palette.line, line_width=3)
        center = self.text(title, cx - rx + 0.5, cy - 0.45, 2 * rx - 1.0, 0.9, size=16,
                           bold=True, color=self.palette.dark, align=PP_ALIGN.CENTER,
                           anchor=MSO_ANCHOR.MIDDLE, minimum=12, font=HEAD_FONT)
        self.step(ring, center)
        node_w, node_h = 2.0, 1.02
        for index, node in enumerate(nodes):
            angle = -math.pi / 2 + 2 * math.pi * index / count
            nx, ny = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
            arrow_angle = angle + math.pi / count
            ax, ay = cx + rx * math.cos(arrow_angle), cy + ry * math.sin(arrow_angle)
            pointer = self.shape(
                MSO_SHAPE.ISOSCELES_TRIANGLE, ax - 0.13, ay - 0.11, 0.26, 0.22,
                fill=self.palette.accent,
                rotation=math.degrees(arrow_angle) + 180,
            )
            body = self.card(nx - node_w / 2, ny - node_h / 2, node_w, node_h,
                             fill=self.palette.tint)
            texts = self._node_text(node, nx - node_w / 2 + 0.08, ny - node_h / 2 + 0.08,
                                    node_w - 0.16, node_h - 0.14, label_size=14)
            self.step(body, *texts, pointer)

    def _diagram_comparison(self, title, nodes, x, y, w, h):
        nodes = nodes[:3]
        count = len(nodes)
        gap = 0.55
        col_w = (w - gap * (count - 1)) / count
        needed = max(text_height(node["detail"], col_w - 0.5, 16) for node in nodes) + 1.5
        y += max(0.0, (h - max(2.4, needed)) / 2)
        h = min(h, max(2.4, needed))
        fills = (self.palette.tint, self.palette.warm_tint, self.palette.alert_tint)
        heads = (self.palette.primary, self.palette.accent, self.palette.alert)
        for index, node in enumerate(nodes):
            cx = x + index * (col_w + gap)
            body = self.card(cx, y, col_w, h, fill=fills[index])
            head = self.shape(MSO_SHAPE.ROUNDED_RECTANGLE, cx + 0.2, y + 0.22, col_w - 0.4, 0.62,
                              fill=heads[index], radius=0.3)
            self.label_in(head, node["label"], size=16, color="FFFFFF")
            detail = self.text(node["detail"], cx + 0.25, y + 1.05, col_w - 0.5, h - 1.25,
                               size=16, color=self.palette.ink, minimum=12)
            items = [body, head, detail]
            if index:
                badge = self.shape(MSO_SHAPE.OVAL, cx - gap / 2 - 0.3, y + h / 2 - 0.3, 0.6, 0.6,
                                   fill=self.palette.dark)
                self.label_in(badge, "vs", size=13, color="FFFFFF")
                items.append(badge)
            self.step(*items)

    def _diagram_hierarchy(self, title, nodes, x, y, w, h):
        root, children = nodes[0], nodes[1:6]
        top_w = min(4.0, w * 0.5)
        top = self.shape(MSO_SHAPE.ROUNDED_RECTANGLE, x + w / 2 - top_w / 2, y, top_w, 0.85,
                         fill=self.palette.dark, radius=0.25, shadow=True)
        self.label_in(top, root["label"], size=17, color="FFFFFF")
        self.step(top)
        count = max(1, len(children))
        gap = 0.25
        child_w = (w - gap * (count - 1)) / count
        bus_y = y + 1.25
        lines = [self.line(x + w / 2, y + 0.85, x + w / 2, bus_y, color=self.palette.line)]
        if count > 1:
            lines.append(self.line(x + child_w / 2, bus_y, x + w - child_w / 2, bus_y,
                                   color=self.palette.line))
        self.step(*lines, effect="wipe_down")
        for index, node in enumerate(children):
            cx = x + index * (child_w + gap)
            drop = self.line(cx + child_w / 2, bus_y, cx + child_w / 2, bus_y + 0.3,
                             color=self.palette.line)
            body = self.card(cx, bus_y + 0.3, child_w, h - 1.55, fill=self.palette.tint)
            texts = self._node_text(node, cx + 0.12, bus_y + 0.45, child_w - 0.24, h - 1.8)
            self.step(drop, body, *texts)

    def _diagram_concept_map(self, title, nodes, x, y, w, h):
        nodes = nodes[:6]
        cx, cy = x + w / 2, y + h / 2
        core_w, core_h = 2.3, 1.15
        node_w, node_h = min(2.9, w * 0.36), 1.25
        slots = _concept_slots(len(nodes), x, y, w, h, node_w, node_h)
        core = self.shape(MSO_SHAPE.OVAL, cx - core_w / 2, cy - core_h / 2, core_w, core_h,
                          fill=self.palette.dark, shadow=True)
        self.label_in(core, title or "Key idea", size=15, color="FFFFFF", font=HEAD_FONT)
        self.step(core)
        for node, (nx, ny) in zip(nodes, slots, strict=False):
            start, end = _edge_points((cx, cy), (core_w / 2, core_h / 2),
                                      (nx, ny, nx + node_w, ny + node_h))
            link = self.line(*start, *end, color=self.palette.primary, width=2)
            body = self.card(nx, ny, node_w, node_h, fill=self.palette.tint)
            texts = self._node_text(node, nx + 0.12, ny + 0.1, node_w - 0.24, node_h - 0.16,
                                    label_size=15)
            self.step(link, body, *texts)


def _concept_slots(count, x, y, w, h, node_w, node_h):
    left, right = x, x + w - node_w
    middle_x = x + w / 2 - node_w / 2
    rows = [y, y + h / 2 - node_h / 2, y + h - node_h]
    layouts = {
        2: [(left, rows[1]), (right, rows[1])],
        3: [(middle_x, rows[0]), (left, rows[2]), (right, rows[2])],
        4: [(left, rows[0]), (right, rows[0]), (left, rows[2]), (right, rows[2])],
        5: [(middle_x, rows[0]), (left, rows[1] - 0.25), (right, rows[1] - 0.25),
            (left + 0.6, rows[2]), (right - 0.6, rows[2])],
        6: [(left, rows[0]), (right, rows[0]), (left, rows[1]), (right, rows[1]),
            (left, rows[2]), (right, rows[2])],
    }
    return layouts[max(2, min(6, count))]


def _edge_points(center, radii, card):
    """Connector endpoints on the ellipse boundary and the card boundary."""
    cx, cy = center
    rx, ry = radii
    left, top, right, bottom = card
    tx, ty = (left + right) / 2, (top + bottom) / 2
    dx, dy = tx - cx, ty - cy
    scale = 1 / math.sqrt((dx / rx) ** 2 + (dy / ry) ** 2) if (dx or dy) else 0
    start = (cx + dx * scale, cy + dy * scale)
    half_w, half_h = (right - left) / 2, (bottom - top) / 2
    card_scale = 1 / max(abs(dx) / half_w, abs(dy) / half_h, 1e-6)
    end = (tx - dx * card_scale, ty - dy * card_scale)
    return start, end


# ---------------------------------------------------------------------------
# Icons drawn with shapes (each returns the shapes it created)
# ---------------------------------------------------------------------------


def _icon_dot(c: SlideCanvas, cx, cy, s, fg, bg):
    return [c.shape(MSO_SHAPE.OVAL, cx - s * 0.22, cy - s * 0.22, s * 0.44, s * 0.44, fill=fg)]


def _icon_bulb(c: SlideCanvas, cx, cy, s, fg, bg):
    glass = c.shape(MSO_SHAPE.OVAL, cx - s * 0.36, cy - s * 0.55, s * 0.72, s * 0.72, fill=fg)
    neck = c.shape(MSO_SHAPE.RECTANGLE, cx - s * 0.18, cy + s * 0.12, s * 0.36, s * 0.2, fill=fg)
    base = c.shape(MSO_SHAPE.ROUNDED_RECTANGLE, cx - s * 0.16, cy + s * 0.36, s * 0.32, s * 0.12,
                   fill=fg, radius=0.5)
    return [glass, neck, base]


def _icon_target(c: SlideCanvas, cx, cy, s, fg, bg):
    outer = c.shape(MSO_SHAPE.OVAL, cx - s * 0.56, cy - s * 0.56, s * 1.12, s * 1.12, line=fg,
                    line_width=2)
    inner = c.shape(MSO_SHAPE.OVAL, cx - s * 0.32, cy - s * 0.32, s * 0.64, s * 0.64, line=fg,
                    line_width=2)
    eye = c.shape(MSO_SHAPE.OVAL, cx - s * 0.1, cy - s * 0.1, s * 0.2, s * 0.2, fill=fg)
    return [outer, inner, eye]


def _icon_search(c: SlideCanvas, cx, cy, s, fg, bg):
    lens = c.shape(MSO_SHAPE.OVAL, cx - s * 0.48, cy - s * 0.48, s * 0.68, s * 0.68, line=fg,
                   line_width=2.5)
    handle = c.polyline([(cx + s * 0.12, cy + s * 0.12), (cx + s * 0.46, cy + s * 0.46)],
                        color=fg, width=3.5)
    return [lens, handle]


def _icon_warning(c: SlideCanvas, cx, cy, s, fg, bg):
    triangle = c.shape(MSO_SHAPE.ISOSCELES_TRIANGLE, cx - s * 0.5, cy - s * 0.5, s, s * 0.88,
                       fill=fg)
    mark = c.text("!", cx - s * 0.3, cy - s * 0.32, s * 0.6, s * 0.6, size=max(9, s * 34),
                  bold=True, color=bg, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return [triangle, mark]


def _icon_question(c: SlideCanvas, cx, cy, s, fg, bg):
    return [c.text("?", cx - s * 0.5, cy - s * 0.56, s, s * 1.1, size=max(10, s * 52), bold=True,
                   color=fg, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, font=HEAD_FONT)]


def _icon_chat(c: SlideCanvas, cx, cy, s, fg, bg):
    bubble = c.shape(MSO_SHAPE.ROUNDED_RECTANGULAR_CALLOUT, cx - s * 0.5, cy - s * 0.42, s,
                     s * 0.66, fill=fg)
    dots = [
        c.shape(MSO_SHAPE.OVAL, cx + offset * s - s * 0.06, cy - s * 0.15, s * 0.12, s * 0.12,
                fill=bg)
        for offset in (-0.24, 0.0, 0.24)
    ]
    return [bubble, *dots]


def _icon_book(c: SlideCanvas, cx, cy, s, fg, bg):
    left = c.shape(MSO_SHAPE.ROUNDED_RECTANGLE, cx - s * 0.52, cy - s * 0.38, s * 0.48, s * 0.76,
                   fill=fg, radius=0.15)
    right = c.shape(MSO_SHAPE.ROUNDED_RECTANGLE, cx + s * 0.04, cy - s * 0.38, s * 0.48,
                    s * 0.76, fill=fg, radius=0.15)
    return [left, right]


def _icon_check(c: SlideCanvas, cx, cy, s, fg, bg):
    return [c.polyline([(cx - s * 0.4, cy + s * 0.02), (cx - s * 0.1, cy + s * 0.32),
                        (cx + s * 0.44, cy - s * 0.3)], color=fg, width=3.5)]


def _icon_cross(c: SlideCanvas, cx, cy, s, fg, bg):
    first = c.polyline([(cx - s * 0.3, cy - s * 0.3), (cx + s * 0.3, cy + s * 0.3)], color=fg,
                       width=3.5)
    second = c.polyline([(cx + s * 0.3, cy - s * 0.3), (cx - s * 0.3, cy + s * 0.3)], color=fg,
                        width=3.5)
    return [first, second]


def _icon_layers(c: SlideCanvas, cx, cy, s, fg, bg):
    return [
        c.shape(MSO_SHAPE.DIAMOND, cx - s * 0.55, cy - s * 0.5 + offset, s * 1.1, s * 0.5, fill=fg,
                line=bg, line_width=1.5)
        for offset in (s * 0.42, s * 0.21, 0.0)
    ]


def _icon_pencil(c: SlideCanvas, cx, cy, s, fg, bg):
    body = c.shape(MSO_SHAPE.RECTANGLE, cx - s * 0.13, cy - s * 0.55, s * 0.26, s * 0.8, fill=fg,
                   rotation=45)
    return [body]


def _icon_path(c: SlideCanvas, cx, cy, s, fg, bg):
    dots = [
        c.shape(MSO_SHAPE.OVAL, cx + dx * s - s * 0.12, cy + dy * s - s * 0.12, s * 0.24,
                s * 0.24, fill=fg)
        for dx, dy in ((-0.42, 0.3), (0.0, -0.05), (0.42, -0.36))
    ]
    trail = c.polyline([(cx - s * 0.42, cy + s * 0.3), (cx, cy - s * 0.05),
                        (cx + s * 0.42, cy - s * 0.36)], color=fg, width=2)
    return [trail, *dots]


_ICON_DRAWERS = {
    "bulb": _icon_bulb,
    "target": _icon_target,
    "search": _icon_search,
    "warning": _icon_warning,
    "question": _icon_question,
    "chat": _icon_chat,
    "book": _icon_book,
    "check": _icon_check,
    "cross": _icon_cross,
    "layers": _icon_layers,
    "pencil": _icon_pencil,
    "path": _icon_path,
}


# ---------------------------------------------------------------------------
# Low-level XML: text frames, bullets, transparency, shadows, transitions, animation
# ---------------------------------------------------------------------------


def _plain_frame(frame, *, anchor=MSO_ANCHOR.TOP, margin: float = 0.0) -> None:
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.vertical_anchor = anchor
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(frame, side, Inches(margin))


def _style_run(run, *, size, color, bold=False, italic=False, font=BODY_FONT) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = font
    run.font.color.rgb = rgb(color)


def _bullet(paragraph, color: str, size: float, *, number: int | None = None) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    indent = Emu(Inches(0.32 if number is None else 0.38))
    pPr.set("marL", str(indent))
    pPr.set("indent", str(-indent))
    color_el = etree.SubElement(pPr, qn("a:buClr"))
    etree.SubElement(color_el, qn("a:srgbClr")).set("val", color)
    etree.SubElement(pPr, qn("a:buSzPct")).set("val", "100000")
    if number is None:
        etree.SubElement(pPr, qn("a:buFont")).set("typeface", "Arial")
        etree.SubElement(pPr, qn("a:buChar")).set("char", "•")
    else:
        etree.SubElement(pPr, qn("a:buAutoNum")).set("type", "arabicPeriod")


def _set_alpha(shape, percent: int) -> None:
    color = shape.fill._xPr.find(qn("a:solidFill"))
    if color is None or not len(color):
        return
    alpha = etree.SubElement(color[0], qn("a:alpha"))
    alpha.set("val", str(int(percent * 1000)))


def _set_shadow(shape, enabled: bool) -> None:
    shape.shadow.inherit = False  # removes the template's default effect
    if not enabled:
        return
    effect_list = shape._element.spPr.find(qn("a:effectLst"))
    if effect_list is None:
        return
    shadow = etree.SubElement(effect_list, qn("a:outerShdw"))
    shadow.set("blurRad", "88900")
    shadow.set("dist", "19050")
    shadow.set("dir", "5400000")
    shadow.set("algn", "t")
    shadow.set("rotWithShape", "0")
    color = etree.SubElement(shadow, qn("a:srgbClr"))
    color.set("val", "1F2A30")
    etree.SubElement(color, qn("a:alpha")).set("val", "16000")


_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def apply_transition(slide, kind: str) -> None:
    """Native slide transition: fade for content, push for openers and dividers."""
    element = slide._element
    for existing in element.findall(qn("p:transition")):
        element.remove(existing)
    transition = etree.Element(qn("p:transition"))
    transition.set("spd", "med")
    if kind == "push":
        etree.SubElement(transition, qn("p:push")).set("dir", "u")
    elif kind == "wipe":
        etree.SubElement(transition, qn("p:wipe")).set("dir", "r")
    else:
        etree.SubElement(transition, qn("p:fade"))
    clr_map = element.find(qn("p:clrMapOvr"))
    if clr_map is not None:
        clr_map.addnext(transition)
    else:
        element.find(qn("p:cSld")).addnext(transition)


_EFFECTS = {
    # name: (presetID, presetSubtype, animEffect filter)
    "fade": ("10", "0", "fade"),
    "wipe_left": ("22", "8", "wipe(left)"),
    "wipe_down": ("22", "1", "wipe(down)"),
    "wipe_up": ("22", "4", "wipe(up)"),
}


def apply_animation(slide, steps: list[list[tuple[Any, str]]], *, duration_ms: int = 380) -> None:
    """Write an auto-playing entrance sequence: each step starts after the previous one."""
    steps = [step for step in steps if step]
    if not steps:
        return
    ids = iter(range(1, 10_000))
    root_id, sequence_id, group_id = next(ids), next(ids), next(ids)
    step_xml: list[str] = []
    animated: list[tuple[str, bool]] = []
    for index, step in enumerate(steps):
        effects = []
        for position, (shape, effect) in enumerate(step):
            preset, subtype, filter_name = _EFFECTS.get(effect, _EFFECTS["fade"])
            spid = str(shape.shape_id)
            node_type = "afterEffect" if position == 0 else "withEffect"
            effect_id, set_id, anim_id = next(ids), next(ids), next(ids)
            effects.append(
                f'<p:par><p:cTn id="{effect_id}" presetID="{preset}" presetClass="entr" '
                f'presetSubtype="{subtype}" fill="hold" grpId="0" nodeType="{node_type}">'
                '<p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
                f'<p:set><p:cBhvr><p:cTn id="{set_id}" dur="1" fill="hold"><p:stCondLst>'
                '<p:cond delay="0"/></p:stCondLst></p:cTn>'
                f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl><p:attrNameLst>'
                "<p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>"
                '<p:to><p:strVal val="visible"/></p:to></p:set>'
                f'<p:animEffect transition="in" filter="{filter_name}"><p:cBhvr>'
                f'<p:cTn id="{anim_id}" dur="{duration_ms}"/><p:tgtEl><p:spTgt spid="{spid}"/>'
                "</p:tgtEl></p:cBhvr></p:animEffect></p:childTnLst></p:cTn></p:par>"
            )
            is_text_shape = shape._element.tag == qn("p:sp")
            if (spid, is_text_shape) not in animated:
                animated.append((spid, is_text_shape))
        delay = 250 + index * duration_ms  # each step starts when the previous one ends
        step_xml.append(
            f'<p:par><p:cTn id="{next(ids)}" fill="hold"><p:stCondLst><p:cond delay="{delay}"/>'
            f'</p:stCondLst><p:childTnLst>{"".join(effects)}</p:childTnLst></p:cTn></p:par>'
        )
    chained = step_xml
    build = "".join(
        f'<p:bldP spid="{spid}" grpId="0" animBg="1"/>' for spid, is_sp in animated if is_sp
    )
    xml = (
        f'<p:timing xmlns:p="{_P}"><p:tnLst><p:par><p:cTn id="{root_id}" dur="indefinite" '
        'restart="never" nodeType="tmRoot"><p:childTnLst><p:seq concurrent="1" nextAc="seek">'
        f'<p:cTn id="{sequence_id}" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
        f'<p:par><p:cTn id="{group_id}" fill="hold"><p:stCondLst><p:cond delay="indefinite"/>'
        f'<p:cond evt="onBegin" delay="0"><p:tn val="{sequence_id}"/></p:cond></p:stCondLst>'
        f'<p:childTnLst>{"".join(chained)}</p:childTnLst></p:cTn></p:par>'
        "</p:childTnLst></p:cTn><p:prevCondLst><p:cond evt=\"onPrev\" delay=\"0\"><p:tgtEl>"
        "<p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst><p:nextCondLst><p:cond evt=\"onNext\" "
        'delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>'
        "</p:childTnLst></p:cTn></p:par></p:tnLst>"
        + (f"<p:bldLst>{build}</p:bldLst>" if build else "")
        + "</p:timing>"
    )
    element = slide._element
    for existing in element.findall(qn("p:timing")):
        element.remove(existing)
    timing = etree.fromstring(xml)
    anchor = element.find(qn("p:transition"))
    if anchor is None:
        anchor = element.find(qn("p:clrMapOvr"))
    anchor.addnext(timing)


# ---------------------------------------------------------------------------
# Shared slide patterns
# ---------------------------------------------------------------------------


def _cover(b: DeckBuilder, title: str, kicker: str, subtitle: str, motif: str) -> None:
    c = b.new_slide(dark=True, transition="fade")
    p = b.palette
    _cover_drawing(c, motif)
    kick = c.text(kicker.upper(), 0.85, 1.35, 7.4, 0.4, size=14, bold=True, color=p.accent)
    title_text, size = fit_text(title, 7.6, 2.9, preferred=46, minimum=30, bold=True)
    head = c.text(title_text, 0.85, 1.85, 7.6, 3.0, size=size, bold=True, font=HEAD_FONT,
                  color="FFFFFF", spacing=1.0)
    sub = c.text(subtitle, 0.85, 5.15, 7.4, 0.9, size=19, color=p.on_dark_muted, minimum=14)
    c.step(kick, head)
    c.step(sub)
    c.finish()


def _cover_drawing(c: SlideCanvas, motif: str) -> None:
    """Right-hand illustration: a small network of ideas that draws itself on."""
    p = c.palette
    big = c.shape(MSO_SHAPE.OVAL, 8.65, 0.85, 4.6, 4.6, fill=p.primary, alpha=35)
    small = c.shape(MSO_SHAPE.OVAL, 10.55, 4.2, 2.45, 2.45, fill=p.accent, alpha=22)
    c.step(big, small)
    points = [(9.4, 2.3), (11.0, 1.55), (12.25, 2.9), (10.55, 3.7), (9.85, 5.2), (11.9, 5.35)]
    links = [(0, 1), (1, 2), (2, 3), (0, 3), (3, 4), (3, 5), (2, 5)]
    lines = [
        c.line(*points[a], *points[b2], color="FFFFFF", width=1.75) for a, b2 in links
    ]
    c.step(*lines, effect="wipe_left")
    dots = []
    for index, (px, py) in enumerate(points):
        d = 0.42 if index == 3 else 0.28
        dots.append(c.shape(MSO_SHAPE.OVAL, px - d / 2, py - d / 2, d, d,
                            fill=p.accent if index in (3, 1) else "FFFFFF"))
    c.step(*dots)
    badge = c.icon(motif, 10.18, 3.0, 0.75, fill=p.accent, fg=p.dark)
    c.step(*badge)


def _card_with_icon(
    c: SlideCanvas,
    icon: str,
    label: str,
    body: Any,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str,
    icon_fill: str,
    size: float = 17,
    minimum: float = 13,
    fit: bool = True,
):
    """Card with an icon, a short label and wrapped body text; returns its shapes.

    With `fit`, a card holding little text shrinks to its content instead of leaving a large
    empty panel.
    """
    if fit and h >= 1.8:
        h = min(h, max(1.9, card_height(body, w, size)))
    card = c.card(x, y, w, h, fill=fill)
    icon_shapes = c.icon(icon, x + 0.28, y + 0.24, 0.56, fill=icon_fill)
    head = c.text(label, x + 1.0, y + 0.26, w - 1.25, 0.4, size=15, bold=True, color=icon_fill)
    if h < 1.8:  # short card: body sits beside the icon, under the label
        text = c.text(body, x + 1.0, y + 0.66, w - 1.3, h - 0.78, size=size, minimum=minimum)
    else:
        text = c.text(body, x + 0.3, y + 0.98, w - 0.6, h - 1.18, size=size, minimum=minimum)
    return [card, *icon_shapes, head, text]


def card_height(body: Any, w: float, size: float) -> float:
    """Height a tall icon card needs for its body text at the preferred size."""
    return 1.18 + text_height(clean(body), w - 0.6, size) + 0.35


def _icon_rows(
    c: SlideCanvas,
    icon: str,
    items: list[str],
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    icon_fill: str | None = None,
    size: float = 18,
    minimum: float = 14,
    max_items: int = 5,
) -> None:
    """Vertical list where each item has its own drawn icon; each row animates in."""
    values = [clean(item) for item in items if clean(item)][:max_items]
    if not values:
        return
    icon_d = 0.46 if size >= 15 else 0.4
    text_w = w - icon_d - 0.3
    # One shared font size keeps the rows consistent.
    row_h = h / len(values)
    shared = size
    for value in values:
        _, fitted = fit_text(value, text_w, row_h - 0.12, preferred=size, minimum=minimum)
        shared = min(shared, fitted)
    heights = [min(row_h - 0.12, text_height(value, text_w, shared) + 0.06) for value in values]
    gap = max(0.12, (h - sum(heights)) / len(values)) if len(values) > 1 else 0
    gap = min(gap, 0.45)
    for value, used in zip(values, heights, strict=True):
        text_value, _ = fit_text(value, text_w, row_h - 0.12, preferred=shared, minimum=shared)
        first_line = shared * 1.18 / 72
        icon_shapes = c.icon(icon, x, y + max(0.0, first_line / 2 - icon_d / 2), icon_d,
                             fill=icon_fill)
        text = c.text(text_value, x + icon_d + 0.3, y, text_w, used + 0.06, size=shared)
        c.step(*icon_shapes, text)
        y += used + gap


def _grid_cards(
    c: SlideCanvas,
    items: list[str],
    *,
    icon: str,
    top: float = 1.85,
    columns: int = 2,
    numbered: bool = False,
    fill: str | None = None,
    size: float = 17,
) -> None:
    values = [clean(item) for item in items if clean(item)][:6]
    if not values:
        return
    p = c.palette
    rows = math.ceil(len(values) / columns)
    gap = 0.3
    w = (SLIDE_WIDTH - 2 * MARGIN - gap * (columns - 1)) / columns
    h = min(1.6, (SLIDE_HEIGHT - top - 0.75 - gap * (rows - 1)) / rows)
    for index, value in enumerate(values):
        col, row = index % columns, index // columns
        x, y = MARGIN + col * (w + gap), top + row * (h + gap)
        card = c.card(x, y, w, h, fill=fill or p.tint)
        if numbered:
            badge = c.shape(MSO_SHAPE.OVAL, x + 0.25, y + h / 2 - 0.3, 0.6, 0.6, fill=p.accent)
            c.label_in(badge, str(index + 1), size=17, color=p.dark)
            marker = [badge]
        else:
            marker = c.icon(icon, x + 0.25, y + h / 2 - 0.3, 0.6)
        text = c.text(value, x + 1.05, y + 0.12, w - 1.3, h - 0.24, size=size, minimum=13,
                      anchor=MSO_ANCHOR.MIDDLE)
        c.step(card, *marker, text)


def _process_slide(b: DeckBuilder, title: str, kicker: str, nodes: list[dict]) -> None:
    c = b.content_slide(title, kicker)
    c.diagram({"type": "process", "title": title, "nodes": nodes}, MARGIN, 2.0,
              SLIDE_WIDTH - 2 * MARGIN, 4.4)
    c.finish()


def _sources_slide(b: DeckBuilder, result: dict, *, kicker: str) -> None:
    references: list[str] = []
    for item in _as_list(result.get("sources")):
        if isinstance(item, dict):
            text = clean(item.get("title")) or "Source"
            if item.get("year"):
                text += f" ({clean(item.get('year'))})"
            if item.get("url"):
                text += f". {_clean_url(clean(item.get('url')))}"
            references.append(text)
        elif clean(item):
            references.append(clean(item))
    if not references:
        return
    for page_start in range(0, len(references), 5):
        page = references[page_start : page_start + 5]
        c = b.content_slide("Further reading", kicker)
        _icon_rows(c, "book", page, MARGIN, 1.85, SLIDE_WIDTH - 2 * MARGIN, 4.9, size=15,
                   minimum=12, max_items=5)
        c.finish()


# ---------------------------------------------------------------------------
# Teaching deck
# ---------------------------------------------------------------------------


def _build_teaching_deck(b: DeckBuilder, result: dict) -> None:
    p = b.palette
    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    sections = [item for item in _as_list(result.get("lecture_sections")) if isinstance(item, dict)]
    level = clean(overview.get("difficulty")).capitalize()
    course = clean(overview.get("course"))
    parts = f"{len(sections)} parts" if len(sections) > 1 else ""
    subtitle = " · ".join(value for value in (f"{level} level" if level else "", parts) if value)
    kicker = course if course and course.lower() != "general" else "Lesson"
    _cover(b, clean(result.get("title")) or "Lesson", kicker, subtitle, "bulb")

    # What we will explore
    c = b.content_slide("What we will explore", "Getting started")
    purpose = clean(overview.get("lesson_purpose"))
    if purpose:
        card = c.card(MARGIN, 1.85, 7.6, 2.55, fill=p.tint)
        mark = c.text("“", MARGIN + 0.25, 1.75, 0.8, 0.9, size=60, color=p.primary, font=HEAD_FONT)
        quote = c.text(purpose, MARGIN + 0.45, 2.45, 6.9, 1.85, size=22, font=HEAD_FONT,
                       color=p.dark, minimum=15)
        c.step(card, mark, quote)
    facts = [
        ("book", "Course", course or "General"),
        ("target", "Level", level or "Intermediate"),
        ("layers", "Parts", f"{len(sections)} short parts" if sections else "One part"),
    ]
    for index, (icon, label, value) in enumerate(facts):
        y = 1.85 + index * 0.88
        shapes = c.icon(icon, 8.75, y, 0.6)
        head = c.text(label.upper(), 9.55, y - 0.02, 3.2, 0.3, size=11, bold=True, color=p.muted)
        body = c.text(value, 9.55, y + 0.24, 3.2, 0.45, size=17, bold=True, minimum=12)
        c.step(*shapes, head, body)
    known = [clean(item) for item in _as_list(overview.get("prerequisite_knowledge")) if clean(item)]
    if known:
        heading = c.text("Before we start, you should know", MARGIN, 4.75, 8, 0.4, size=17,
                         bold=True, color=p.dark)
        c.step(heading)
        _chips(c, known[:4], MARGIN, 5.3, SLIDE_WIDTH - 2 * MARGIN, icon="check")
    c.finish()

    # Objectives
    objectives = [
        clean(item.get("objective") if isinstance(item, dict) else item)
        for item in _as_list(result.get("learning_objectives"))
    ]
    if any(objectives):
        c = b.content_slide("By the end, you will be able to", "Your goals")
        _grid_cards(c, objectives, icon="target", numbered=True,
                    columns=1 if len(objectives) <= 3 else 2)
        c.finish()

    # Roadmap
    if sections:
        _process_slide(
            b,
            "Our path through the topic",
            "The plan",
            [{"label": clean(item.get("title")), "detail": ""} for item in sections],
        )

    for index, section in enumerate(sections, start=1):
        _teaching_section(b, section, index, len(sections))

    formulas = [clean(item) for item in _as_list(overview.get("core_formulas")) if clean(item)]
    if formulas:
        _formula_slides(b, formulas)

    case_study = result.get("case_study")
    if isinstance(case_study, dict) and clean(case_study.get("scenario")):
        _case_slide(b, case_study)

    discussion = [clean(item) for item in _as_list(result.get("discussion_questions")) if clean(item)]
    if discussion:
        _discussion_slide(b, discussion)

    mcqs = [item for item in _as_list(result.get("mcqs")) if isinstance(item, dict)]
    for index, item in enumerate(mcqs, start=1):
        _mcq_slides(b, item, index, len(mcqs))

    for item in _as_list(result.get("assignments")):
        if isinstance(item, dict):
            _assignment_slide(b, item)

    for index, item in enumerate(_as_list(result.get("numerical_problems")), start=1):
        if isinstance(item, dict):
            _numerical_slides(b, item, index)

    viva = [
        clean(item.get("question") if isinstance(item, dict) else item)
        for item in _as_list(result.get("viva_questions"))
    ]
    if any(viva):
        c = b.content_slide("Test yourself", "Practice")
        _grid_cards(c, viva, icon="question", columns=2 if len(viva) > 3 else 1, size=17)
        c.finish()

    if sections:
        _recap_slide(b, sections)
    _sources_slide(b, result, kicker="Learn more")


def _chips(c: SlideCanvas, values: list[str], x: float, y: float, w: float, *, icon: str) -> None:
    gap = 0.25
    per_row = 2
    chip_w = (w - gap * (per_row - 1)) / per_row
    for index, value in enumerate(values):
        cx = x + (index % per_row) * (chip_w + gap)
        cy = y + (index // per_row) * 0.72
        chip = c.shape(MSO_SHAPE.ROUNDED_RECTANGLE, cx, cy, chip_w, 0.58, fill=c.palette.tint,
                       radius=0.5)
        mark = c.icon(icon, cx + 0.1, cy + 0.07, 0.44)
        text = c.text(value, cx + 0.68, cy + 0.04, chip_w - 0.85, 0.5, size=15, minimum=11,
                      anchor=MSO_ANCHOR.MIDDLE)
        c.step(chip, *mark, text)


def _section_diagram(content: dict) -> dict | None:
    spec = content.get("diagram")
    if isinstance(spec, dict) and len(_as_list(spec.get("nodes"))) >= 2:
        return spec
    return None


def _teaching_section(b: DeckBuilder, section: dict, index: int, total: int) -> None:
    p = b.palette
    title = clean(section.get("title")) or f"Part {index}"
    content = section.get("content") if isinstance(section.get("content"), dict) else {}
    kicker = f"Part {index} of {total}"
    explanation = clean(content.get("explanation"))
    key_points = [clean(item) for item in _as_list(content.get("key_points")) if clean(item)]
    diagram = _section_diagram(content)

    # Slide A: the idea, explained and pictured.
    c = b.content_slide(title, kicker)
    c.transition = "push" if index == 1 else "fade"
    if diagram:
        text_w = 4.7
        if explanation:
            body = c.text(explanation, MARGIN, 1.95, text_w, 4.8, size=19, minimum=14,
                          spacing=1.15)
            c.step(body)
        c.diagram(diagram, MARGIN + text_w + 0.45, 1.85, SLIDE_WIDTH - 2 * MARGIN - text_w - 0.45,
                  4.85)
        points_on_slide_b = key_points
    else:
        if explanation:
            card = c.card(MARGIN, 1.85, 5.9, 4.9, fill=p.tint)
            body = c.text(explanation, MARGIN + 0.35, 2.15, 5.2, 4.3, size=19, minimum=14,
                          spacing=1.15)
            c.step(card, body)
        heading = c.text("Key ideas", 7.0, 1.9, 5.6, 0.45, size=19, bold=True, color=p.dark)
        c.step(heading)
        _icon_rows(c, "bulb", key_points, 7.0, 2.5, 5.7, 4.2, icon_fill=p.accent, max_items=4)
        points_on_slide_b = []
    c.finish()

    # Slide B: see it in action.
    worked = content.get("worked_example") or content.get("worked_examples")
    example = _example_text(worked) or clean(content.get("concept_check"))
    errors = [clean(item) for item in _as_list(content.get("common_errors")) if clean(item)]
    if not (example or errors or points_on_slide_b):
        return
    c = b.content_slide(f"{title}: in action", kicker)
    right_x = MARGIN
    right_w = SLIDE_WIDTH - 2 * MARGIN
    if points_on_slide_b:
        heading = c.text("Key ideas", MARGIN, 1.9, 5.4, 0.45, size=19, bold=True, color=p.dark)
        c.step(heading)
        _icon_rows(c, "bulb", points_on_slide_b, MARGIN, 2.5, 5.4, 4.2, icon_fill=p.accent,
                   max_items=4)
        right_x = MARGIN + 5.85
        right_w = SLIDE_WIDTH - MARGIN - right_x
    blocks = []
    if example:
        label = "Example" if worked else "Quick check"
        blocks.append(("search" if worked else "question", label, example, p.warm_tint, p.accent))
    if errors:
        blocks.append(("warning", "Watch out", " ".join(errors[:2]), p.alert_tint, p.alert))
    if not blocks:
        c.finish()
        return
    if len(blocks) == 2 and not points_on_slide_b:
        # Side by side when the slide is free.
        widths = (right_w * 0.6 - 0.15, right_w * 0.4 - 0.15)
        x = right_x
        for (icon, label, body, fill, icon_fill), width in zip(blocks, widths, strict=True):
            c.step(*_card_with_icon(c, icon, label, body, x, 1.9, width, 4.85, fill=fill,
                                    icon_fill=icon_fill))
            x += width + 0.3
    else:
        available = 4.85 - 0.25 * (len(blocks) - 1)
        needed = [card_height(block[2], right_w, 16) for block in blocks]
        if sum(needed) <= available:
            heights = [max(1.9, value) for value in needed]
        elif len(blocks) == 2:
            heights = [available * 0.62, available * 0.38]
        else:
            heights = [available]
        y = 1.9
        for (icon, label, body, fill, icon_fill), height in zip(blocks, heights, strict=True):
            c.step(*_card_with_icon(c, icon, label, body, right_x, y, right_w, height,
                                    fill=fill, icon_fill=icon_fill, size=16, fit=False))
            y += height + 0.25
    c.finish()


def _example_text(worked: Any) -> str:
    if isinstance(worked, dict):
        problem = clean(worked.get("problem"))
        solution = clean(worked.get("solution"))
        return "\n".join(value for value in (problem, f"Answer: {solution}" if solution else "")
                         if value)
    if isinstance(worked, list):
        return "\n".join(_example_text(item) for item in worked[:1])
    return clean(worked)


def _formula_slides(b: DeckBuilder, formulas: list[str]) -> None:
    p = b.palette
    for page_start in range(0, len(formulas), 4):
        c = b.content_slide("Formulas to remember", "Toolkit")
        page = formulas[page_start : page_start + 4]
        columns = 1 if len(page) == 1 else 2
        rows = math.ceil(len(page) / columns)
        gap = 0.3
        w = (SLIDE_WIDTH - 2 * MARGIN - gap * (columns - 1)) / columns
        h = min(2.2, (4.9 - gap * (rows - 1)) / rows)
        for index, formula in enumerate(page):
            x = MARGIN + (index % columns) * (w + gap)
            y = 1.9 + (index // columns) * (h + gap)
            tile = c.card(x, y, w, h, fill=p.dark)
            text = c.text(formula, x + 0.35, y + 0.2, w - 0.7, h - 0.4, size=22, color="FFFFFF",
                          minimum=13, anchor=MSO_ANCHOR.MIDDLE, font=MATH_FONT)
            c.step(tile, text)
        c.finish()


def _case_slide(b: DeckBuilder, case_study: dict) -> None:
    p = b.palette
    c = b.content_slide(clean(case_study.get("title")) or "A real situation", "Case study")
    c.step(*_card_with_icon(c, "book", "The situation", case_study.get("scenario"), MARGIN, 1.9,
                            7.1, 4.85, fill=p.tint, icon_fill=p.primary, size=18))
    heading = c.text("Think about", 8.15, 1.95, 4.5, 0.45, size=19, bold=True, color=p.dark)
    c.step(heading)
    questions = [clean(item) for item in _as_list(case_study.get("questions")) if clean(item)]
    _icon_rows(c, "question", questions, 8.15, 2.55, 4.6, 4.2, icon_fill=p.accent, size=16,
               minimum=12, max_items=4)
    c.finish()


def _discussion_slide(b: DeckBuilder, questions: list[str]) -> None:
    p = b.palette
    c = b.content_slide("Think and discuss", "Talk it over")
    values = questions[:4]
    columns = 2 if len(values) > 1 else 1
    rows = math.ceil(len(values) / columns)
    gap = 0.4
    w = (SLIDE_WIDTH - 2 * MARGIN - gap * (columns - 1)) / columns
    h = (4.75 - gap * (rows - 1)) / rows
    fills = (p.tint, p.warm_tint)
    for index, value in enumerate(values):
        x = MARGIN + (index % columns) * (w + gap)
        y = 1.9 + (index // columns) * (h + gap)
        bubble = c.shape(MSO_SHAPE.ROUNDED_RECTANGULAR_CALLOUT, x, y, w, h - 0.25,
                         fill=fills[(index + index // columns) % 2], shadow=True)
        bubble.adjustments[0] = -0.3 if index % 2 == 0 else 0.3
        bubble.adjustments[1] = 0.68
        mark = c.icon("chat", x + 0.3, y + 0.28, 0.55,
                      fill=p.primary if index % 2 == 0 else p.accent)
        text = c.text(value, x + 1.05, y + 0.22, w - 1.35, h - 0.75, size=18, minimum=13,
                      anchor=MSO_ANCHOR.MIDDLE)
        c.step(bubble, *mark, text)
    c.finish()


def _mcq_slides(b: DeckBuilder, item: dict, index: int, total: int) -> None:
    p = b.palette
    question = clean(item.get("question"))
    options = [_mcq_option_text(option) for option in _as_list(item.get("options"))[:4]]
    answer = clean(item.get("answer")).upper()[:1]
    correct = ord(answer) - 65 if answer in "ABCD" and answer else -1
    kicker = f"Quick check {index} of {total}"

    def layout(c: SlideCanvas, reveal: bool) -> float:
        question_text, size = fit_text(question, SLIDE_WIDTH - 2 * MARGIN, 1.15, preferred=22,
                                       minimum=16, bold=True)
        q = c.text(question_text, MARGIN, 1.8, SLIDE_WIDTH - 2 * MARGIN, 1.15, size=size,
                   bold=True, color=p.dark)
        if not reveal:
            c.step(q)
        gap = 0.3
        w = (SLIDE_WIDTH - 2 * MARGIN - gap) / 2
        h = 1.05 if reveal else 1.45
        top = 3.1
        for position, option in enumerate(options):
            x = MARGIN + (position % 2) * (w + gap)
            y = top + (position // 2) * (h + 0.22)
            is_answer = reveal and position == correct
            dim = reveal and not is_answer
            tile = c.card(x, y, w, h, fill=p.primary if is_answer else ("F2F4F5" if dim else p.tint),
                          shadow=not dim)
            letter = c.shape(MSO_SHAPE.OVAL, x + 0.22, y + h / 2 - 0.27, 0.54, 0.54,
                             fill="FFFFFF" if is_answer else (p.line if dim else p.primary))
            c.label_in(letter, chr(65 + position), size=16,
                       color=p.primary if is_answer else "FFFFFF")
            text_w = w - 1.2 - (0.75 if is_answer else 0.0)
            text = c.text(option, x + 0.95, y + 0.08, text_w, h - 0.16, size=17, minimum=12,
                          anchor=MSO_ANCHOR.MIDDLE,
                          color="FFFFFF" if is_answer else (p.muted if dim else p.ink))
            if is_answer:
                tick = c.icon("check", x + w - 0.75, y + h / 2 - 0.27, 0.54, fill=p.accent,
                              fg=p.dark)
                c.step(tile, letter, text, *tick)
            elif not reveal:
                c.step(tile, letter, text)
        return top + 2 * h + 0.22

    c = b.content_slide(f"Knowledge Check {index}", kicker)
    layout(c, reveal=False)
    c.finish()

    c = b.content_slide(f"Knowledge Check {index}: Answer", kicker)
    bottom = layout(c, reveal=True)
    explanation = clean(item.get("explanation"))
    if explanation:
        height = SLIDE_HEIGHT - 0.7 - bottom - 0.2
        c.step(*_card_with_icon(c, "bulb", "Why this is right", explanation, MARGIN, bottom + 0.2,
                                SLIDE_WIDTH - 2 * MARGIN, max(1.2, height), fill=p.warm_tint,
                                icon_fill=p.accent, size=16, minimum=12))
    c.finish()


def _assignment_slide(b: DeckBuilder, item: dict) -> None:
    p = b.palette
    title = clean(item.get("title")) or "Assignment"
    c = b.content_slide(f"Your task: {title}", "Assignment")
    deliverables = [clean(value) for value in _as_list(item.get("deliverables")) if clean(value)]
    task_h = 2.6 if deliverables else 4.85
    c.step(*_card_with_icon(c, "pencil", "What to do", item.get("prompt"), MARGIN, 1.9, 6.6,
                            task_h, fill=p.tint, icon_fill=p.primary, size=17))
    if deliverables:
        heading = c.text("What to hand in", MARGIN, 4.75, 6.6, 0.4, size=17, bold=True,
                         color=p.dark)
        c.step(heading)
        _icon_rows(c, "check", deliverables, MARGIN, 5.25, 6.6, 1.55, size=15, minimum=12,
                   max_items=3)
    rubric = [row for row in _as_list(item.get("rubric")) if isinstance(row, dict)]
    if rubric:
        x = 7.65
        width = SLIDE_WIDTH - MARGIN - x
        heading = c.text("How it is marked", x, 1.95, width, 0.45, size=19, bold=True,
                         color=p.dark)
        c.step(heading)
        rows = rubric[:5]
        row_h = 4.3 / len(rows)
        for position, row in enumerate(rows):
            y = 2.55 + position * row_h
            weight = max(0, min(100, int(row.get("weight_percent") or 0)))
            name = c.text(clean(row.get("criterion")), x, y, width - 0.9, 0.36, size=15, bold=True,
                          minimum=11)
            pct = c.text(f"{weight}%", x + width - 0.85, y, 0.85, 0.36, size=15, bold=True,
                         color=p.primary, align=PP_ALIGN.RIGHT)
            track = c.shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y + 0.42, width, 0.2, fill=p.tint,
                            radius=0.5)
            bar = c.shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y + 0.42, max(0.2, width * weight / 100),
                          0.2, fill=p.accent if position % 2 else p.primary, radius=0.5)
            c.step(name, pct, track)
            c.also(bar, effect="wipe_left")
    c.finish()


def _numerical_slides(b: DeckBuilder, item: dict, index: int) -> None:
    p = b.palette
    problem = clean(item.get("problem"))
    given = [clean(value) for value in _as_list(item.get("given")) if clean(value)]

    c = b.content_slide(f"Try It: Problem {index}", "Practice")
    problem_h = 2.3 if given else 3.8
    c.step(*_card_with_icon(c, "pencil", "Problem", problem, MARGIN, 1.9, SLIDE_WIDTH - 2 * MARGIN,
                            problem_h, fill=p.tint, icon_fill=p.primary, size=19))
    if given:
        heading = c.text("What we know", MARGIN, 4.45, 6, 0.4, size=17, bold=True, color=p.dark)
        c.step(heading)
        _chips(c, given[:4], MARGIN, 4.95, SLIDE_WIDTH - 2 * MARGIN, icon="check")
    hint = c.text("Work it out first. The solution is on the next slide.", MARGIN,
                  SLIDE_HEIGHT - 0.85, 9, 0.35, size=14, italic=True, color=p.muted)
    c.step(hint)
    c.finish()

    c = b.content_slide(f"Problem {index}: Worked Solution", "Practice")
    recap = c.text(problem, MARGIN, 1.8, SLIDE_WIDTH - 2 * MARGIN, 0.7, size=15,
                   color=p.muted, minimum=11)
    c.step(recap)
    steps = [clean(value) for value in _as_list(item.get("solution_steps")) if clean(value)][:6]
    x, top, width = MARGIN, 2.65, 7.6
    if steps:
        row_h = min(0.9, 4.1 / len(steps))
        rail = c.line(x + 0.27, top + 0.27, x + 0.27, top + row_h * (len(steps) - 1) + 0.27,
                      color=p.line, width=2.5)
        c.step(rail, effect="wipe_down")
        for position, value in enumerate(steps):
            y = top + position * row_h
            dot = c.shape(MSO_SHAPE.OVAL, x, y, 0.54, 0.54, fill=p.primary)
            c.label_in(dot, str(position + 1), size=14, color="FFFFFF")
            text = c.text(value, x + 0.8, y, width - 0.8, row_h - 0.05, size=16, minimum=12,
                          anchor=MSO_ANCHOR.TOP)
            c.step(dot, text)
    answer = clean(item.get("final_answer"))
    if answer:
        c.step(*_card_with_icon(c, "check", "Final answer", answer, 8.6, 2.65,
                                SLIDE_WIDTH - MARGIN - 8.6, 2.3, fill=p.warm_tint,
                                icon_fill=p.accent, size=18))
    c.finish()


def _recap_slide(b: DeckBuilder, sections: list[dict]) -> None:
    p = b.palette
    c = b.new_slide(dark=True, transition="fade")
    head = c.text("What to remember", MARGIN, 0.7, 10, 0.8, size=34, bold=True, font=HEAD_FONT,
                  color="FFFFFF")
    c.step(head)
    values = sections[:6]
    rows = len(values)
    row_h = min(1.25, 5.3 / max(1, rows))
    for index, section in enumerate(values):
        content = section.get("content") if isinstance(section.get("content"), dict) else {}
        point = next((clean(item) for item in _as_list(content.get("key_points")) if clean(item)),
                     "")
        y = 1.75 + index * row_h
        badge = c.shape(MSO_SHAPE.OVAL, MARGIN, y, 0.5, 0.5, fill=p.accent)
        c.label_in(badge, str(index + 1), size=15, color=p.dark)
        name = c.text(clean(section.get("title")), MARGIN + 0.75, y + 0.02, 4.1, row_h - 0.1,
                      size=20, bold=True, color="FFFFFF", minimum=13, font=HEAD_FONT)
        detail = c.text(point, MARGIN + 5.05, y + 0.04, 7.0, row_h - 0.1, size=18,
                        color=p.on_dark_muted, minimum=12)
        c.step(badge, name, detail)
    c.finish()
