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

import geom2d
from geom2d import Arc, Line, P

from tcnc.errors import PlanError
from tcnc.toolpath import MAX_ARC_ANGLE, Hints, Segment, Toolpath, heading_change, segments_are_g1


@dataclass(frozen=True, slots=True)
class _Joint:
    """The two source segments at a joint and their shifted counterparts."""

    before: Segment
    before_shifted: Segment
    after: Segment
    after_shifted: Segment


def offset_toolpath(toolpath: Toolpath, distance: float, *, min_arc_chord: float) -> Toolpath:
    """Shift ``toolpath`` forward by ``distance`` along the blade heading and bridge the gaps.

    Every segment moves so that a blade edge trailing ``distance`` behind
    the axis along the blade heading follows the artwork (see
    ``_offset_segments``; a line whose heading turns along it is cut into
    pieces so the edge stays within the larger of ``min_arc_chord`` and
    the toolpath's tolerance). Gaps shorter than ``min_arc_chord``, and
    gaps at tangent-continuous joints, are bridged with a line; others
    with an arc about the vertex, split so no piece sweeps more than 90°.
    """
    if geom2d.is_zero(distance):
        return toolpath
    tolerance = toolpath.tolerance
    budget = max(min_arc_chord, tolerance or 0.0)
    shifted = [_offset_segments(segment, distance, budget) for segment in toolpath]
    out: list[Segment] = []
    for index, pieces in enumerate(shifted):
        if index > 0:
            joint = _Joint(toolpath[index - 1], shifted[index - 1][-1], toolpath[index], pieces[0])
            out.extend(_connector(joint, distance=distance, min_arc_chord=min_arc_chord, tolerance=tolerance))
        out.extend(pieces)
    if toolpath.closed:
        joint = _Joint(toolpath[-1], shifted[-1][-1], toolpath[0], shifted[0][0])
        out.extend(_connector(joint, distance=distance, min_arc_chord=min_arc_chord, tolerance=tolerance))
    return toolpath.with_segments(out)


def _offset_segments(segment: Segment, distance: float, budget: float) -> list[Segment]:
    """The axis path for ``segment``: the blade edge trails ``distance`` behind the axis along the blade heading.

    A line whose headings are its own direction shifts along itself, and
    an arc whose headings are its tangents shifts to the concentric arc,
    both exactly. A line whose blade heading turns along it (the chords of
    a simplified curve) has a compensated locus that is not straight; it is
    approximated by straight pieces between endpoints shifted along their
    own headings, subdivided until the blade edge stays within ``budget``
    of the artwork. Arcs with headings other than their tangents have no
    producer in the pipeline and are rejected.

    Raises:
        PlanError: For an arc whose blade headings are not its tangents.
    """
    hints = Hints(start_heading=segment.start_heading, end_heading=segment.end_heading)
    match segment.geom:
        case Line() as line:
            if _headings_are_tangents(segment):
                return [Segment(line.shift(distance), hints)]
            return _offset_turning_line(segment, distance, budget)
        case Arc() as arc:
            if not _headings_are_tangents(segment):
                msg = "blade compensation needs an arc's blade headings to be its tangents"
                raise PlanError(msg)
            geom = Arc(
                arc.p1 + arc.start_tangent * distance,
                arc.p2 + arc.end_tangent * distance,
                math.hypot(distance, arc.radius),
                arc.angle,
                arc.center,
            )
            return [Segment(geom, hints)]


def _headings_are_tangents(segment: Segment) -> bool:
    return geom2d.angle_eq(segment.start_heading, segment.start_tangent_angle) and geom2d.angle_eq(
        segment.end_heading, segment.end_tangent_angle
    )


def _offset_turning_line(segment: Segment, distance: float, budget: float) -> list[Segment]:
    """Straight pieces shifted along their end headings, enough of them that the blade edge stays within ``budget``.

    Between two endpoints shifted along their own headings the blade edge
    drifts from the artwork by at most ``distance * (1 - cos(turn / 2))``
    for the ``turn`` of heading across the piece, so the segment is cut
    into pieces whose turn keeps that below the budget.
    """
    turn = abs(segment.rotation)
    largest = 2.0 * math.acos(max(-1.0, 1.0 - budget / abs(distance))) if abs(distance) > budget else math.tau
    count = max(1, math.ceil(turn / largest)) if largest > 0.0 else 1
    out: list[Segment] = []
    for index in range(count):
        t0, t1 = index / count, (index + 1) / count
        h0, h1 = segment.heading_at(t0), segment.heading_at(t1)
        start = segment.point_at(t0) + P.from_polar(distance, h0)
        end = segment.point_at(t1) + P.from_polar(distance, h1)
        out.append(Segment(Line(start, end), Hints(start_heading=h0, end_heading=h1)))
    return out


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
