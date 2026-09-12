"""Toolpath model: geometry segments carrying blade-heading hints.

A ``Segment`` wraps one geom2d ``Line`` or ``Arc`` plus the headings the A
axis holds at its ends. Without hints the heading is the geometric tangent;
after blade-offset compensation the geometry moves but the blade must keep
following the artwork, so the hints record the original tangents. A
connector inserted by compensation additionally records the signed
``joint_turn`` of the source joint it spans, which is how a sharp corner
survives compensation as a lift boundary; that value is the whole joint's
turn and is kept intact when the connector is split, while ``turn`` is the
blade rotation across one piece. A ``Toolpath`` is an ordered, connected,
non-empty sequence of segments whose arcs sweep at most 90°, and it carries
the distance ``tolerance`` at which "connected" was judged: every later
stage (blade compensation, corner planning, the writer) reads that
tolerance from the toolpath instead of geom2d's numerical floor.
"""

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import geom2d
from geom2d import Arc, Box, CubicBezier, GeometryError, Line, P, const

from tcnc.errors import PlanError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

type Geometry = Line | Arc
type SourceGeometry = Line | Arc | CubicBezier

MAX_ARC_ANGLE = math.pi / 2.0
_SWEEP_SLACK = 1e-9
_HEADING_SLACK = 1e-9


def _opposite(angle: float | None) -> float | None:
    return None if angle is None else geom2d.normalize_angle(angle + math.pi, center=0.0)


@dataclass(frozen=True, slots=True)
class Hints:
    """Blade headings at a segment's ends, the rotation across it, and the source joint it spans.

    ``turn`` is the signed blade rotation over this one segment (``None``:
    the shortest way between the headings). ``joint_turn`` marks a
    compensation connector with the signed turn of the whole source joint,
    whatever part of it this piece covers. Every value is finite; when both
    headings and ``turn`` are given they agree.
    """

    start_heading: float | None = None
    end_heading: float | None = None
    turn: float | None = None
    joint_turn: float | None = None

    def __post_init__(self) -> None:
        """Reject non-finite angles and a rotation that does not lead from the start heading to the end."""
        for name in ("start_heading", "end_heading", "turn", "joint_turn"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                msg = f"hint {name} must be a finite angle, got {value!r}"
                raise PlanError(msg)
        if (
            self.turn is not None
            and self.start_heading is not None
            and self.end_heading is not None
            and not geom2d.angle_eq(self.start_heading + self.turn, self.end_heading, _HEADING_SLACK)
        ):
            msg = f"hint turn {self.turn!r} does not lead from heading {self.start_heading!r} to {self.end_heading!r}"
            raise PlanError(msg)

    @property
    def is_empty(self) -> bool:
        """True when nothing overrides the geometry."""
        return self.start_heading is None and self.end_heading is None and self.turn is None and self.joint_turn is None

    def reversed(self) -> Hints:
        """Hints for the reversed segment: ends swapped, headings turned by π, turns negated."""
        return Hints(
            start_heading=_opposite(self.end_heading),
            end_heading=_opposite(self.start_heading),
            turn=None if self.turn is None else -self.turn,
            joint_turn=None if self.joint_turn is None else -self.joint_turn,
        )


@dataclass(frozen=True, slots=True)
class Segment:
    """One geometry segment plus its heading hints."""

    geom: Geometry
    hints: Hints = Hints()

    # geom2d.Segment protocol -------------------------------------------------

    @property
    def p1(self) -> P:
        """Start point."""
        return self.geom.p1

    @property
    def p2(self) -> P:
        """End point."""
        return self.geom.p2

    @property
    def length(self) -> float:
        """Arc length."""
        return self.geom.length

    @property
    def is_degenerate(self) -> bool:
        """True when the geometry has no length."""
        return self.geom.is_degenerate

    @property
    def start_tangent_angle(self) -> float:
        """Geometric tangent angle at the start."""
        return self.geom.start_tangent_angle

    @property
    def end_tangent_angle(self) -> float:
        """Geometric tangent angle at the end."""
        return self.geom.end_tangent_angle

    @property
    def start_tangent(self) -> P:
        """Unit tangent vector at the start."""
        return self.geom.start_tangent

    @property
    def end_tangent(self) -> P:
        """Unit tangent vector at the end."""
        return self.geom.end_tangent

    @property
    def bounding_box(self) -> Box:
        """Axis-aligned bounding box of the geometry."""
        return self.geom.bounding_box

    def point_at(self, t: float) -> P:
        """Point at parameter ``t`` in [0, 1]."""
        return self.geom.point_at(t)

    def tangent_at(self, t: float) -> P:
        """Unit tangent at parameter ``t`` in [0, 1]."""
        return self.geom.tangent_at(t)

    def subdivide(self, t: float) -> tuple[Segment, Segment]:
        """Split at ``t``; headings are interpolated, the rotation divided and the source joint kept whole."""
        first, second = self.geom.subdivide(t)
        if self.hints.is_empty:
            return Segment(first), Segment(second)
        middle = self.heading_at(t)
        turn, joint = self.hints.turn, self.hints.joint_turn
        return (
            Segment(first, Hints(self.start_heading, middle, None if turn is None else turn * t, joint)),
            Segment(second, Hints(middle, self.end_heading, None if turn is None else turn * (1.0 - t), joint)),
        )

    def reversed(self) -> Segment:
        """The same geometry traversed the other way, hints swapped and turned."""
        return Segment(self.geom.reversed(), self.hints.reversed())

    # headings -----------------------------------------------------------------

    @property
    def start_heading(self) -> float:
        """Blade heading at the start: the hint if set, else the tangent."""
        return self.geom.start_tangent_angle if self.hints.start_heading is None else self.hints.start_heading

    @property
    def end_heading(self) -> float:
        """Blade heading at the end: the hint if set, else the tangent."""
        return self.geom.end_tangent_angle if self.hints.end_heading is None else self.hints.end_heading

    @property
    def rotation(self) -> float:
        """Signed blade rotation over the segment (the hinted ``turn``, else the shortest way)."""
        if self.hints.turn is not None:
            return self.hints.turn
        return geom2d.calc_rotation(self.start_heading, self.end_heading)

    def heading_at(self, t: float) -> float:
        """Blade heading at ``t``: the tangent when unhinted, else linear in the segment's rotation."""
        if self.hints.is_empty:
            return self.geom.tangent_at(t).angle
        return self.start_heading + self.rotation * t

    @property
    def is_arc(self) -> bool:
        """True when the geometry is a circular arc."""
        return isinstance(self.geom, Arc)

    @property
    def is_connector(self) -> bool:
        """True for a compensation connector (or a piece of one) that spans a joint of the source path."""
        return self.hints.joint_turn is not None

    def with_hints(self, hints: Hints) -> Segment:
        """Copy with the given hints."""
        return replace(self, hints=hints)

    def with_geom(self, geom: Geometry) -> Segment:
        """Copy with new geometry and the same hints."""
        return replace(self, geom=geom)


def heading_change(first: Segment, second: Segment) -> float:
    """Signed, wrap-safe turn of the blade from ``first``'s end to ``second``'s start."""
    return geom2d.calc_rotation(first.end_heading, second.start_heading)


def segments_are_g1(
    first: Segment,
    second: Segment,
    *,
    angle_tolerance: float | None = None,
    point_tolerance: float | None = None,
) -> bool:
    """True when the segments meet and the heading does not change across the joint."""
    return first.p2.almost_equal(second.p1, point_tolerance) and geom2d.angle_eq(
        first.end_heading, second.start_heading, angle_tolerance
    )


@dataclass(frozen=True, slots=True)
class Toolpath:
    """An ordered, connected, non-empty sequence of segments.

    Invariants, checked on construction: at least one segment; consecutive
    segments share an endpoint within ``tolerance``; ``closed`` implies the
    last end meets the first start within ``tolerance``; every arc sweeps at
    most 90° (``MAX_ARC_ANGLE``). ``tolerance`` is a distance; ``None`` means
    geom2d's ``EPSILON``.
    """

    segments: tuple[Segment, ...]
    closed: bool = False
    source_id: str | None = None
    tolerance: float | None = None

    def __post_init__(self) -> None:
        """Enforce the invariants."""
        check_traversal(self.segments, self.tolerance, closed=self.closed, what="toolpath")

    def __iter__(self) -> Iterator[Segment]:
        return iter(self.segments)

    def __len__(self) -> int:
        return len(self.segments)

    def __getitem__(self, index: int) -> Segment:
        return self.segments[index]

    @property
    def start(self) -> P:
        """First point."""
        return self.segments[0].p1

    @property
    def end(self) -> P:
        """Last point."""
        return self.segments[-1].p2

    @property
    def length(self) -> float:
        """Total arc length."""
        return geom2d.path_length(self.segments)

    @property
    def bounding_box(self) -> Box:
        """Bounding box of all segments."""
        return geom2d.path_bounding_box(self.segments)

    @classmethod
    def from_geometry(
        cls,
        path: Sequence[SourceGeometry],
        *,
        biarc_tolerance: float = 0.01,
        biarc_max_depth: int = 8,
        tolerance: float | None = None,
        source_id: str | None = None,
    ) -> Toolpath | None:
        """Convert connected lines, arcs and cubic Béziers into a toolpath.

        Béziers become biarcs within ``biarc_tolerance`` and arcs are split
        to at most 90°. ``tolerance`` (a distance, default geom2d's
        ``EPSILON``) is the resolution of the result: a run of consecutive
        pieces each shorter than it is replaced by its chord, or dropped
        when even the chord is shorter, so no piece carries a heading the
        job cannot resolve; the path is closed when its ends meet within it.
        Returns ``None`` when nothing usable remains; raises ``PlanError``
        when the pieces do not connect or geom2d cannot approximate a curve.
        """
        resolution = const.EPSILON if tolerance is None else tolerance
        try:
            pieces = _flatten(path, biarc_tolerance, biarc_max_depth)
        except GeometryError as exc:
            msg = f"path {source_id or '<unnamed>'}: {exc}"
            raise PlanError(msg) from exc
        segments = [Segment(piece) for piece in _merge_short(pieces, resolution)]
        if not segments:
            return None
        closed = segments[0].p1.almost_equal(segments[-1].p2, tolerance) and (len(segments) > 1 or segments[0].is_arc)
        return cls(tuple(segments), closed=closed, source_id=source_id, tolerance=tolerance)

    def with_segments(self, segments: Sequence[Segment], *, closed: bool | None = None) -> Toolpath:
        """Copy with different segments (and optionally a different closure flag)."""
        return replace(self, segments=tuple(segments), closed=self.closed if closed is None else closed)

    def reversed(self) -> Toolpath:
        """Traverse the same path the other way."""
        return self.with_segments([segment.reversed() for segment in reversed(self.segments)])

    def rotated_to(self, index: int) -> Toolpath:
        """A closed path re-started at the vertex before segment ``index``."""
        if not self.closed:
            msg = "only a closed toolpath can be rotated to a different start"
            raise PlanError(msg)
        index %= len(self.segments)
        return self.with_segments(self.segments[index:] + self.segments[:index])


def check_traversal(segments: Sequence[Segment], tolerance: float | None, *, closed: bool, what: str) -> None:
    """Raise ``PlanError`` unless ``segments`` is a knife-down traversal the writer can serialize.

    At least one segment; consecutive segments meet within ``tolerance`` (a
    positive distance, ``None`` for geom2d's ``EPSILON``); with ``closed``
    the last end meets the first start; every arc sweeps at most
    ``MAX_ARC_ANGLE``. ``what`` names the container in messages.
    """
    if not segments:
        msg = f"a {what} needs at least one segment"
        raise PlanError(msg)
    if tolerance is not None and not (math.isfinite(tolerance) and tolerance > 0.0):
        msg = f"{what} tolerance must be a positive distance, got {tolerance!r}"
        raise PlanError(msg)
    for index in range(1, len(segments)):
        before, after = segments[index - 1], segments[index]
        if not before.p2.almost_equal(after.p1, tolerance):
            msg = f"{what} segments {index - 1} and {index} do not connect: {before.p2} vs {after.p1}"
            raise PlanError(msg)
    if closed and not segments[-1].p2.almost_equal(segments[0].p1, tolerance):
        msg = f"{what} is marked closed but its ends do not meet"
        raise PlanError(msg)
    for index, segment in enumerate(segments):
        if isinstance(segment.geom, Arc) and abs(segment.geom.angle) > MAX_ARC_ANGLE + _SWEEP_SLACK:
            msg = (
                f"{what} segment {index} sweeps {math.degrees(abs(segment.geom.angle)):.1f}°; arcs must be split to 90°"
            )
            raise PlanError(msg)


def _flatten(path: Sequence[SourceGeometry], biarc_tolerance: float, biarc_max_depth: int) -> list[Geometry]:
    """Lines and arcs (at most 90° each) for the source geometry, in order."""
    pieces: list[Geometry] = []
    for geom in path:
        match geom:
            case CubicBezier():
                pieces.extend(
                    geom.biarc_approximation(biarc_tolerance, max_depth=biarc_max_depth, max_arc_angle=MAX_ARC_ANGLE)
                )
            case Arc():
                pieces.extend(geom.split_max_sweep(MAX_ARC_ANGLE))
            case Line():
                pieces.append(geom)
    return pieces


def _merge_short(pieces: Sequence[Geometry], resolution: float) -> list[Geometry]:
    """Replace every run of pieces shorter than ``resolution`` by its chord, or drop it when the chord is short too."""
    out: list[Geometry] = []
    run_start: P | None = None
    run_end: P | None = None
    for piece in pieces:
        if piece.length < resolution:
            if run_start is None:
                run_start = piece.p1
            run_end = piece.p2
            continue
        if run_start is not None and run_end is not None:
            chord = Line(run_start, run_end)
            if chord.length >= resolution:
                out.append(chord)
            run_start = run_end = None
        out.append(piece)
    if run_start is not None and run_end is not None:
        chord = Line(run_start, run_end)
        if chord.length >= resolution:
            out.append(chord)
    return out
