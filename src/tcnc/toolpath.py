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
# An arc that never leaves the resolution around its chord and turns less than this is its chord: such arcs
# come from fitting nearly straight curves, have radii beyond geom2d's absolute numerical floor, and change
# no heading the job can resolve.
_FLAT_ARC_TURN = math.radians(1.0)


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
    """One geometry segment plus its heading hints.

    A hinted ``turn`` must lead from the resolved start heading to the
    resolved end heading, geometry defaults included, so ``heading_at``,
    the endpoint headings and the writer's rotation always agree.
    """

    geom: Geometry
    hints: Hints = Hints()

    def __post_init__(self) -> None:
        """Check the rotation against the headings the segment actually reports."""
        turn = self.hints.turn
        if turn is not None and not geom2d.angle_eq(self.start_heading + turn, self.end_heading, _HEADING_SLACK):
            msg = (
                f"hint turn {turn!r} does not lead from the segment's start heading {self.start_heading!r} "
                f"to its end heading {self.end_heading!r}"
            )
            raise PlanError(msg)

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

        Béziers become biarcs within ``biarc_tolerance`` (a Bézier that
        stays within the resolution of its chord is the chord) and arcs are
        split to at most 90°. ``tolerance`` (a distance, default geom2d's
        ``EPSILON``) is the resolution of the result: runs of consecutive
        pieces shorter than it are replaced by chords that stay within it of
        every vertex they replace, a run that fits inside it is left out,
        and where such chords meet each other or their neighbours on a
        curve that is smooth at this resolution the blade heading follows
        that curve rather than the chords (see ``simplify``). The path is
        closed when its ends meet within the tolerance. Returns ``None``
        when nothing usable remains; raises ``PlanError`` when the pieces do
        not connect or geom2d cannot approximate a curve.
        """
        resolution = const.EPSILON if tolerance is None else tolerance
        try:
            pieces = _flatten(path, biarc_tolerance, biarc_max_depth, resolution)
        except GeometryError as exc:
            msg = f"path {source_id or '<unnamed>'}: {exc}"
            raise PlanError(msg) from exc
        for index in range(1, len(pieces)):
            before, after = pieces[index - 1], pieces[index]
            if not before.p2.almost_equal(after.p1, tolerance):
                msg = (
                    f"path {source_id or '<unnamed>'}: pieces {index - 1} and {index} do not connect: "
                    f"{before.p2} vs {after.p1}"
                )
                raise PlanError(msg)
        simplified = _simplify(pieces, resolution)
        if not simplified:
            return None
        first, last = simplified[0].geom, simplified[-1].geom
        closed = first.p1.almost_equal(last.p2, tolerance) and (len(simplified) > 1 or isinstance(first, Arc))
        segments = _smoothed(simplified, resolution, closed=closed)
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


def _flatten(
    path: Sequence[SourceGeometry], biarc_tolerance: float, biarc_max_depth: int, resolution: float
) -> list[Geometry]:
    """Lines and arcs (at most 90° each) for the source geometry, in order."""
    pieces: list[Geometry] = []
    for geom in path:
        match geom:
            case CubicBezier():
                pieces.extend(_curve_pieces(geom, biarc_tolerance, biarc_max_depth, resolution))
            case Arc():
                pieces.extend(geom.split_max_sweep(MAX_ARC_ANGLE))
            case Line():
                pieces.append(geom)
    return [_flattened(piece, resolution) for piece in pieces]


def _flattened(piece: Geometry, resolution: float) -> Geometry:
    """``piece``, or its chord for an arc that is straight at this resolution (see ``_FLAT_ARC_TURN``)."""
    if isinstance(piece, Arc) and abs(piece.angle) <= _FLAT_ARC_TURN:
        sagitta = piece.radius * (1.0 - math.cos(piece.angle / 2.0))
        if sagitta <= resolution:
            return Line(piece.p1, piece.p2)
    return piece


def _curve_pieces(
    curve: CubicBezier, biarc_tolerance: float, biarc_max_depth: int, resolution: float
) -> list[Geometry]:
    """Biarcs for ``curve``; its chord when the curve is straight at the resolution (or within the biarc budget).

    A curve whose control polygon stays within ``resolution`` of the chord
    and whose end tangents lie along it (within ``_FLAT_ARC_TURN``) carries
    neither a feature nor a heading the job can resolve, so the chord
    stands in for it without asking geom2d for arcs of astronomical
    radius. Distance alone is not enough: a tiny quarter circle is within
    the resolution of its chord yet turns the blade by 90°. geom2d may
    also fail to form a candidate arc for a curve that is nearly straight
    at the biarc tolerance; under the same angular condition the chord is
    within that budget and is used.
    """
    if _straight_at(curve, resolution):
        return [curve.chord]
    try:
        return list(curve.biarc_approximation(biarc_tolerance, max_depth=biarc_max_depth, max_arc_angle=MAX_ARC_ANGLE))
    except GeometryError:
        if _straight_at(curve, biarc_tolerance):
            return [curve.chord]
        raise


def _straight_at(curve: CubicBezier, distance: float) -> bool:
    """True when ``curve`` stays within ``distance`` of its chord and both end tangents lie along the chord."""
    chord = curve.chord
    if chord.is_degenerate or curve.flatness > distance:
        return False
    direction = chord.angle
    return geom2d.angle_eq(curve.start_tangent_angle, direction, _FLAT_ARC_TURN) and geom2d.angle_eq(
        curve.end_tangent_angle, direction, _FLAT_ARC_TURN
    )


@dataclass(frozen=True, slots=True)
class _Piece:
    """A piece of a simplified path; ``chord`` marks a line that replaced a run of short pieces."""

    geom: Geometry
    chord: bool


@dataclass(slots=True)
class _Run:
    """A run of short pieces being replaced by the chord from ``start`` to ``end``.

    The chord must stay within the resolution of every vertex the run has
    absorbed. Two bounds make that a constant-time test per piece: ``far``,
    the distance from the start to the farthest vertex, which the end is
    required to reach (so no vertex projects beyond the chord and a run
    that doubles back is cut short), and a cone of chord directions, the
    intersection over every vertex more than the resolution from the start
    of the directions along which the line through the start passes
    within the resolution of that vertex. Directions are kept relative to
    the first constraining vertex so the cone never straddles the ±π seam.
    """

    start: P
    end: P
    far: float = 0.0
    reference: float | None = None
    low: float = -math.pi
    high: float = math.pi

    @classmethod
    def begin(cls, start: P, end: P, resolution: float) -> _Run:
        run = cls(start, start)
        run.extend(end, resolution)
        return run

    def accepts(self, point: P) -> bool:
        """True when the chord from the start to ``point`` stays within the resolution of every absorbed vertex."""
        distance = self.start.distance(point)
        if distance < self.far:
            return False
        if self.reference is None:
            return True
        relative = geom2d.normalize_angle((point - self.start).angle - self.reference, center=0.0)
        return any(self.low <= angle <= self.high for angle in (relative, relative + math.tau, relative - math.tau))

    def extend(self, point: P, resolution: float) -> None:
        """Make ``point`` the end; from now on the chord must pass within the resolution of it."""
        self.end = point
        distance = self.start.distance(point)
        self.far = max(self.far, distance)
        if distance <= resolution:
            return
        half = math.asin(min(1.0, resolution / distance))
        direction = (point - self.start).angle
        if self.reference is None:
            self.reference, self.low, self.high = direction, -half, half
            return
        relative = geom2d.normalize_angle(direction - self.reference, center=0.0)
        self.low, self.high = max(self.low, relative - half), min(self.high, relative + half)


def _simplify(pieces: Sequence[Geometry], resolution: float) -> list[_Piece]:
    """Replace runs of pieces shorter than ``resolution`` by chords within ``resolution`` of every replaced vertex.

    A run grows while the chord from its start to the candidate end stays
    within the resolution of every vertex absorbed so far (see ``_Run``;
    the work is constant per piece). When the next piece would break that,
    the run so far becomes one chord and a new run starts at its end. A
    run whose chord is shorter than the resolution fits entirely inside
    the resolution around its start (the end is always the farthest
    vertex), so it is left out and the following run continues from the
    same start; no vertex is ever further than the resolution from the
    result and no gap wider than the resolution can open between kept
    pieces. Pieces at least the resolution long are kept as they are.
    """
    out: list[_Piece] = []
    run: _Run | None = None

    def flush() -> None:
        nonlocal run
        if run is not None and run.far >= resolution:
            out.append(_Piece(Line(run.start, run.end), chord=True))
        run = None

    for piece in pieces:
        if piece.length >= resolution:
            flush()
            out.append(_Piece(piece, chord=False))
            continue
        if run is None:
            run = _Run.begin(piece.p1, piece.p2, resolution)
            continue
        if run.accepts(piece.p2):
            run.extend(piece.p2, resolution)
            continue
        if run.far >= resolution:
            out.append(_Piece(Line(run.start, run.end), chord=True))
            run = _Run.begin(run.end, piece.p2, resolution)
        else:
            run = _Run.begin(run.start, piece.p2, resolution)
    flush()
    return out


def _smoothed(simplified: Sequence[_Piece], resolution: float, *, closed: bool) -> list[Segment]:
    """Segments for the simplified pieces, with blade headings that follow a curve the chords sample.

    At a joint where at least one side is a chord and both sides are
    lines, the circle through the joint and its two neighbouring vertices
    is the curve the chords may be sampling. When both chords stay within
    the resolution of that circle, the data is a smooth curve at this
    resolution and the blade heading at the joint is the circle's tangent
    (the bisector of the two chord directions); otherwise the joint is a
    real corner and the chords keep their own headings.
    """
    count = len(simplified)
    starts: list[float | None] = [None] * count
    ends: list[float | None] = [None] * count
    joints = list(range(1, count))
    if closed and count > 1:
        joints.append(0)
    for joint in joints:
        before, after = simplified[joint - 1], simplified[joint]
        if not (before.chord or after.chord) or not isinstance(before.geom, Line) or not isinstance(after.geom, Line):
            continue
        heading = _smooth_heading(before.geom, after.geom, resolution)
        if heading is not None:
            ends[joint - 1] = heading
            starts[joint] = heading
    return [
        Segment(piece.geom, Hints(start_heading=starts[index], end_heading=ends[index]))
        for index, piece in enumerate(simplified)
    ]


def _smooth_heading(before: Line, after: Line, resolution: float) -> float | None:
    """The tangent at the joint of the circle through both lines' far ends, if both lines lie within ``resolution`` of it.

    On that circle the tangent at the joint leaves the first chord by half
    of the chord's central angle (the inscribed-angle theorem), which is
    the bisector of the two chord directions only when the chords are
    equally long.
    """
    turn = geom2d.calc_rotation(before.angle, after.angle)
    if abs(turn) > MAX_ARC_ANGLE:
        return None
    if turn == 0.0:
        return before.angle
    a, b, c = before.p1, before.p2, after.p2
    doubled_area = abs((b - a).cross(c - a))
    if doubled_area == 0.0:
        return None
    radius = before.length * after.length * a.distance(c) / (2.0 * doubled_area)
    for half_chord in (before.length / 2.0, after.length / 2.0):
        rest = math.sqrt(max(0.0, radius * radius - half_chord * half_chord))
        sagitta = half_chord * half_chord / (radius + rest)
        if sagitta > resolution:
            return None
    half_central_angle = math.asin(min(1.0, before.length / (2.0 * radius)))
    return before.angle + math.copysign(half_central_angle, turn)
