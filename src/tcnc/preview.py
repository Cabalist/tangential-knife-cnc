"""Standalone SVG preview of a cut plan.

The picture is drawn in G-code units with Y up, so it matches the part as
seen on the machine: cuts in red, lead-in and overcut in a lighter red,
rapid moves as dashed green lines, blade heading ticks along the cuts and
a marker wherever the knife lifts.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from geom2d import Arc, Box, Line, P

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tcnc.corners import Cut, CutPlan
    from tcnc.toolpath import Segment

CUT_COLOR = "#d62728"
EXTENSION_COLOR = "#f4a6a6"
RAPID_COLOR = "#2ca02c"
TICK_COLOR = "#1f77b4"
LIFT_COLOR = "#ff7f0e"
DEFAULT_TICKS_PER_PAGE = 60


@dataclass(frozen=True, slots=True)
class _Canvas:
    """Page geometry in G-code units and the derived stroke sizes."""

    xmin: float
    ymin: float
    width: float
    height: float
    units: str

    @property
    def ymax(self) -> float:
        return self.ymin + self.height

    @property
    def stroke(self) -> float:
        return max(self.width, self.height) * 0.004

    def y(self, value: float) -> float:
        """Flip a Y-up value into the SVG's Y-down frame."""
        return self.ymax - value

    def fmt(self, value: float) -> str:
        return f"{value:.5g}"

    def pt(self, p: P) -> str:
        return f"{self.fmt(p.x)},{self.fmt(self.y(p.y))}"


def preview_svg(plan: CutPlan, *, page: tuple[float, float] | None = None) -> str:
    """Return the preview as SVG text."""
    canvas = _canvas(plan, page)
    size = f'width="{canvas.fmt(canvas.width)}{canvas.units}" height="{canvas.fmt(canvas.height)}{canvas.units}"'
    view = f'viewBox="{canvas.fmt(canvas.xmin)} {canvas.fmt(canvas.ymin)} {canvas.fmt(canvas.width)} {canvas.fmt(canvas.height)}"'
    background = (
        f'<rect x="{canvas.fmt(canvas.xmin)}" y="{canvas.fmt(canvas.ymin)}" '
        f'width="{canvas.fmt(canvas.width)}" height="{canvas.fmt(canvas.height)}" fill="white"/>'
    )
    parts: list[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" {size} {view}>', background]
    tick_length = plan.options.blade_width or max(canvas.width, canvas.height) / DEFAULT_TICKS_PER_PAGE
    previous_end: P | None = None
    for cut in plan.cuts:
        if previous_end is not None:
            parts.append(_rapid(canvas, previous_end, cut.start))
        parts.extend(_cut(canvas, cut, tick_length))
        previous_end = cut.end
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def write_preview(plan: CutPlan, path: Path | str, *, page: tuple[float, float] | None = None) -> None:
    """Write the preview SVG to ``path``."""
    Path(path).write_text(preview_svg(plan, page=page), encoding="utf-8")


def _canvas(plan: CutPlan, page: tuple[float, float] | None) -> _Canvas:
    units = plan.options.gcode_units
    box = plan.bounding_box
    if page is not None:
        width, height = page
        page_box = Box(P(0.0, 0.0), P(width, height))
        if box is not None:
            page_box = page_box.union(box)
        return _Canvas(page_box.xmin, page_box.ymin, page_box.width, page_box.height, units)
    if box is None:
        return _Canvas(0.0, 0.0, 1.0, 1.0, units)
    pad = max(box.width, box.height, 1e-6) * 0.1
    return _Canvas(box.xmin - pad, box.ymin - pad, box.width + 2 * pad, box.height + 2 * pad, units)


def _rapid(canvas: _Canvas, start: P, end: P) -> str:
    dash = canvas.fmt(canvas.stroke * 3)
    return (
        f'<line x1="{canvas.fmt(start.x)}" y1="{canvas.fmt(canvas.y(start.y))}" '
        f'x2="{canvas.fmt(end.x)}" y2="{canvas.fmt(canvas.y(end.y))}" '
        f'stroke="{RAPID_COLOR}" stroke-width="{canvas.fmt(canvas.stroke * 0.6)}" stroke-dasharray="{dash} {dash}"/>'
    )


def _cut(canvas: _Canvas, cut: Cut, tick_length: float) -> list[str]:
    parts: list[str] = []
    if cut.lead_in is not None:
        parts.append(_path(canvas, [cut.lead_in], EXTENSION_COLOR, canvas.stroke))
    parts.append(_path(canvas, cut.core, CUT_COLOR, canvas.stroke))
    if cut.overcut is not None:
        parts.append(_path(canvas, [cut.overcut], EXTENSION_COLOR, canvas.stroke))
    parts.extend(_ticks(canvas, cut.core, tick_length))
    r = canvas.fmt(canvas.stroke * 2)
    parts.append(
        f'<circle cx="{canvas.fmt(cut.end.x)}" cy="{canvas.fmt(canvas.y(cut.end.y))}" r="{r}" fill="{LIFT_COLOR}"/>'
    )
    return parts


def _path(canvas: _Canvas, segments: Iterable[Segment], color: str, stroke: float) -> str:
    data: list[str] = []
    for index, segment in enumerate(segments):
        if index == 0:
            data.append(f"M {canvas.pt(segment.p1)}")
        match segment.geom:
            case Line(p2=end):
                data.append(f"L {canvas.pt(end)}")
            case Arc(p2=end, radius=radius, angle=angle):
                large = 1 if abs(angle) > math.pi else 0
                # Y is flipped for display, which mirrors the sense of rotation.
                sweep = 0 if angle > 0 else 1
                data.append(f"A {canvas.fmt(radius)} {canvas.fmt(radius)} 0 {large} {sweep} {canvas.pt(end)}")
    return (
        f'<path d="{" ".join(data)}" fill="none" stroke="{color}" stroke-width="{canvas.fmt(stroke)}" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _ticks(canvas: _Canvas, segments: Iterable[Segment], tick_length: float) -> list[str]:
    """Short lines across the path showing the blade heading at regular intervals."""
    parts: list[str] = []
    spacing = tick_length * 2.0
    carry = 0.0
    half = tick_length / 2.0
    for segment in segments:
        length = segment.length
        if length <= 0.0:
            continue
        distance = spacing - carry
        while distance <= length:
            t = distance / length
            centre = segment.point_at(t)
            heading = _heading_at(segment, t)
            normal = P.from_polar(half, heading + math.pi / 2)
            a, b = centre + normal, centre - normal
            parts.append(
                f'<line x1="{canvas.fmt(a.x)}" y1="{canvas.fmt(canvas.y(a.y))}" x2="{canvas.fmt(b.x)}" '
                f'y2="{canvas.fmt(canvas.y(b.y))}" stroke="{TICK_COLOR}" stroke-width="{canvas.fmt(canvas.stroke * 0.5)}"/>'
            )
            distance += spacing
        carry = length - (distance - spacing)
    return parts


def _heading_at(segment: Segment, t: float) -> float:
    """Blade heading at ``t``: hinted headings interpolate linearly, else the tangent."""
    start, end = segment.hints.start_heading, segment.hints.end_heading
    if start is None and end is None:
        return segment.tangent_at(t).angle
    return segment.start_heading + (segment.end_heading - segment.start_heading) * t
