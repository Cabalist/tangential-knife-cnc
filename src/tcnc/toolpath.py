"""Toolpath model: geometry segments carrying heading hints.

A ``Segment`` wraps one geom2d ``Line`` or ``Arc`` and the optional headings
the A axis should hold at its ends (used when a segment was moved by the
blade trail offset and its own tangent no longer matches the cut). A
``Toolpath`` is an ordered, immutable sequence of segments. Both are frozen
dataclasses; ``Segment`` also satisfies the ``geom2d.Segment`` protocol so
the geom2d path helpers accept toolpaths directly.
"""

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import geom2d
from geom2d import Arc, Box, CubicBezier, Line, P

from tcnc.errors import PlanError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

type Geometry = Line | Arc
type SourceGeometry = Line | Arc | CubicBezier

DEFAULT_MAX_ARC_ANGLE = math.pi / 2.0


def _opposite(angle: float | None) -> float | None:
    return None if angle is None else geom2d.normalize_angle(angle + math.pi, center=0.0)


@dataclass(frozen=True, slots=True)
class Hints:
    """Headings the A axis should hold at a segment's ends, if not its tangents."""

    start_heading: float | None = None
    end_heading: float | None = None

    def reversed(self) -> Hints:
        """Hints for the reversed segment: ends swapped, headings turned by pi."""
        return Hints(start_heading=_opposite(self.end_heading), end_heading=_opposite(self.start_heading))


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
        """Split at parameter ``t``; the pieces carry no hints."""
        first, second = self.geom.subdivide(t)
        return Segment(first), Segment(second)

    def reversed(self) -> Segment:
        """The same geometry traversed the other way, hints swapped and turned."""
        return Segment(self.geom.reversed(), self.hints.reversed())

    # headings -----------------------------------------------------------------

    @property
    def start_heading(self) -> float:
        """A-axis heading at the start: the hint if set, else the tangent."""
        return self.geom.start_tangent_angle if self.hints.start_heading is None else self.hints.start_heading

    @property
    def end_heading(self) -> float:
        """A-axis heading at the end: the hint if set, else the tangent."""
        return self.geom.end_tangent_angle if self.hints.end_heading is None else self.hints.end_heading

    @property
    def is_arc(self) -> bool:
        """True when the geometry is a circular arc."""
        return isinstance(self.geom, Arc)

    def with_hints(self, *, start_heading: float | None = None, end_heading: float | None = None) -> Segment:
        """Copy with the given headings set (``None`` leaves a heading untouched)."""
        hints = self.hints
        if start_heading is not None:
            hints = replace(hints, start_heading=start_heading)
        if end_heading is not None:
            hints = replace(hints, end_heading=end_heading)
        return replace(self, hints=hints)

    def with_geom(self, geom: Geometry) -> Segment:
        """Copy with new geometry and the same hints."""
        return replace(self, geom=geom)


def heading_change(first: Segment, second: Segment) -> float:
    """Signed, wrap-safe turn of the A axis from ``first``'s end to ``second``'s start."""
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
    """An ordered, non-empty sequence of segments; ``closed`` when it returns to its start."""

    segments: tuple[Segment, ...]
    closed: bool = False
    source_id: str | None = None

    def __post_init__(self) -> None:
        """A toolpath must contain at least one segment."""
        if not self.segments:
            msg = "a Toolpath needs at least one segment"
            raise PlanError(msg)

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
        biarc_tolerance: float = 0.001,
        biarc_max_depth: int = 4,
        max_arc_angle: float = DEFAULT_MAX_ARC_ANGLE,
        source_id: str | None = None,
    ) -> Toolpath | None:
        """Convert lines, arcs and cubic Béziers into a toolpath.

        Béziers become biarcs, arcs are split to at most ``max_arc_angle``,
        and degenerate pieces are dropped. Returns ``None`` when nothing
        usable remains.
        """
        segments: list[Segment] = []
        for geom in path:
            pieces: list[Line | Arc]
            match geom:
                case CubicBezier():
                    pieces = geom.biarc_approximation(
                        biarc_tolerance, max_depth=biarc_max_depth, max_arc_angle=max_arc_angle
                    )
                case Arc():
                    pieces = list(geom.split_max_sweep(max_arc_angle))
                case Line():
                    pieces = [geom]
            segments.extend(Segment(piece) for piece in pieces if not piece.is_degenerate)
        if not segments:
            return None
        closed = segments[0].p1.almost_equal(segments[-1].p2) and (len(segments) > 1 or segments[0].is_arc)
        return cls(tuple(segments), closed=closed, source_id=source_id)

    def with_segments(self, segments: Sequence[Segment]) -> Toolpath:
        """Copy with different segments and the same closure flag and id."""
        return replace(self, segments=tuple(segments))

    def reversed(self) -> Toolpath:
        """Traverse the same path the other way."""
        return self.with_segments([seg.reversed() for seg in reversed(self.segments)])

    def rotated_to(self, index: int) -> Toolpath:
        """A closed path re-started at the vertex before segment ``index``."""
        if not self.closed:
            msg = "only a closed toolpath can be rotated to a different start"
            raise PlanError(msg)
        index %= len(self.segments)
        return self.with_segments(self.segments[index:] + self.segments[:index])
