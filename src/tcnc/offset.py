"""Blade trail-offset compensation.

A trailing blade edge sits ``distance`` behind the rotation axis along the
direction of travel. Shifting every segment forward by that distance puts
the edge on the artwork; where two shifted segments no longer meet, a
connector (an arc pivoting about the original vertex, or a short line) is
inserted. Heading hints keep the A axis on the original tangents.
"""

import math
from dataclasses import replace
from typing import TYPE_CHECKING

import geom2d
from geom2d import Arc, Line

from tcnc.toolpath import Hints, Segment, Toolpath, segments_are_g1

if TYPE_CHECKING:
    from tcnc.toolpath import Geometry


def offset_toolpath(toolpath: Toolpath, distance: float, *, min_arc_chord: float) -> Toolpath:
    """Shift ``toolpath`` forward by ``distance`` and bridge the gaps.

    Gaps shorter than ``min_arc_chord``, and gaps at tangent-continuous
    joints, are bridged with a line; others with an arc about the vertex.
    """
    if geom2d.is_zero(distance):
        return toolpath
    shifted = [_offset_segment(segment, distance) for segment in toolpath]
    out: list[Segment] = []
    for index, current in enumerate(shifted):
        if index > 0:
            out.extend(
                _connector(toolpath[index - 1], shifted[index - 1], toolpath[index], current, distance, min_arc_chord)
            )
        out.append(current)
    if toolpath.closed:
        out.extend(_connector(toolpath[-1], shifted[-1], toolpath[0], shifted[0], distance, min_arc_chord))
    return replace(toolpath, segments=tuple(out))


def _offset_segment(segment: Segment, distance: float) -> Segment:
    hints = Hints(start_heading=segment.start_heading, end_heading=segment.end_heading)
    geom: Geometry
    match segment.geom:
        case Line() as line:
            geom = line.shift(distance)
        case Arc() as arc:
            geom = Arc(
                arc.p1 + arc.start_tangent * distance,
                arc.p2 + arc.end_tangent * distance,
                math.hypot(distance, arc.radius),
                arc.angle,
                arc.center,
            )
    return Segment(geom, hints)


def _connector(
    before: Segment,
    before_shifted: Segment,
    after: Segment,
    after_shifted: Segment,
    distance: float,
    min_arc_chord: float,
) -> list[Segment]:
    start, end = before_shifted.p2, after_shifted.p1
    if start.almost_equal(end):
        return []
    hints = Hints(start_heading=before.end_heading, end_heading=after.start_heading)
    if start.distance(end) < min_arc_chord or segments_are_g1(before, after):
        return [Segment(Line(start, end), hints)]
    pivot = before.p2
    angle = pivot.angle2(start, end)
    return [Segment(Arc(start, end, distance, angle, pivot), hints)]
