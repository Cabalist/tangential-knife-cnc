"""Blade trail-offset compensation.

A trailing blade edge sits ``distance`` behind the rotation axis along the
direction of travel. Shifting every segment forward by that distance puts
the edge on the artwork; where two shifted segments no longer meet, a
connector (an arc pivoting about the original vertex, or a short line) is
inserted. Heading hints keep the blade on the original tangents, and each
connector records the signed ``turn`` of the joint it spans so the corner
planner can still decide to lift there.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import geom2d
from geom2d import Arc, Line

from tcnc.toolpath import MAX_ARC_ANGLE, Hints, Segment, Toolpath, heading_change, segments_are_g1

if TYPE_CHECKING:
    from tcnc.toolpath import Geometry


@dataclass(frozen=True, slots=True)
class _Joint:
    """The two source segments at a joint and their shifted counterparts."""

    before: Segment
    before_shifted: Segment
    after: Segment
    after_shifted: Segment


def offset_toolpath(toolpath: Toolpath, distance: float, *, min_arc_chord: float) -> Toolpath:
    """Shift ``toolpath`` forward by ``distance`` and bridge the gaps.

    Gaps shorter than ``min_arc_chord``, and gaps at tangent-continuous
    joints, are bridged with a line; others with an arc about the vertex,
    split so no piece sweeps more than 90°.
    """
    if geom2d.is_zero(distance):
        return toolpath
    shifted = [_offset_segment(segment, distance) for segment in toolpath]
    tolerance = toolpath.tolerance
    out: list[Segment] = []
    for index, current in enumerate(shifted):
        if index > 0:
            joint = _Joint(toolpath[index - 1], shifted[index - 1], toolpath[index], current)
            out.extend(_connector(joint, distance=distance, min_arc_chord=min_arc_chord, tolerance=tolerance))
        out.append(current)
    if toolpath.closed:
        joint = _Joint(toolpath[-1], shifted[-1], toolpath[0], shifted[0])
        out.extend(_connector(joint, distance=distance, min_arc_chord=min_arc_chord, tolerance=tolerance))
    return toolpath.with_segments(out)


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


def _connector(joint: _Joint, *, distance: float, min_arc_chord: float, tolerance: float | None) -> list[Segment]:
    """Segments bridging the gap the shift opened at a joint, judged at the toolpath's ``tolerance``.

    The arc pivots about the source vertex, so it ends on the circle of
    radius ``distance`` around it; where the source segments meet within
    the tolerance rather than exactly, that end is within the same tolerance
    of the next shifted segment, which the toolpath invariant allows.
    """
    start, end = joint.before_shifted.p2, joint.after_shifted.p1
    if start.almost_equal(end, tolerance):
        return []
    turn = heading_change(joint.before, joint.after)
    hints = Hints(
        start_heading=joint.before.end_heading, end_heading=joint.after.start_heading, turn=turn, joint_turn=turn
    )
    if start.distance(end) < min_arc_chord or segments_are_g1(joint.before, joint.after, point_tolerance=tolerance):
        return [Segment(Line(start, end), hints)]
    pivot = joint.before.p2
    angle = pivot.angle2(start, end)
    if geom2d.is_zero(angle):
        return [Segment(Line(start, end), hints)]
    arc_end = pivot + (start - pivot).rotate(angle)
    connector = Segment(Arc(start, arc_end, distance, angle, pivot), hints)
    return _split_connector(connector)


def _split_connector(connector: Segment) -> list[Segment]:
    """Split a connector arc into pieces of at most 90°.

    Headings are interpolated and the rotation divided between the pieces;
    the source joint's turn is carried whole by every piece, so the corner
    planner still sees the joint it came from.
    """
    if not isinstance(connector.geom, Arc) or abs(connector.geom.angle) <= MAX_ARC_ANGLE:
        return [connector]
    pieces = connector.geom.split_max_sweep(MAX_ARC_ANGLE)
    count = len(pieces)
    turn = connector.rotation
    joint = connector.hints.joint_turn
    out: list[Segment] = []
    for index, piece in enumerate(pieces):
        t0, t1 = index / count, (index + 1) / count
        hints = Hints(connector.heading_at(t0), connector.heading_at(t1), turn / count, joint)
        out.append(Segment(piece, hints))
    return out
