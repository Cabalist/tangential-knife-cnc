"""Turn toolpaths into cuts: split at sharp corners, add lead-in and overcut.

A ``Cut`` is one continuous knife-down run. Wherever the blade would have to
turn by more than the corner angle while in the material, the toolpath is
split; every open run is extended along its tangents by the overcut distance
at both ends so the angled blade finishes the corner. A closed toolpath with
no sharp corner becomes one loop that simply overruns its start.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from geom2d import Box, Line, P

from tcnc.toolpath import Hints, Segment, Toolpath, heading_change

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tcnc.options import KnifeOptions


@dataclass(frozen=True, slots=True)
class Cut:
    """One knife-down run, including any lead-in and overcut segments."""

    segments: tuple[Segment, ...]
    source_id: str | None = None
    lead_in: Segment | None = None
    overcut: Segment | None = None
    loop: bool = False

    @property
    def start(self) -> P:
        """Where the knife plunges (start of the lead-in when there is one)."""
        return self.segments[0].p1

    @property
    def end(self) -> P:
        """Where the knife lifts (end of the overcut when there is one)."""
        return self.segments[-1].p2

    @property
    def start_heading(self) -> float:
        """A-axis heading the cut begins with."""
        return self.segments[0].start_heading

    @property
    def end_heading(self) -> float:
        """A-axis heading the cut ends with."""
        return self.segments[-1].end_heading

    @property
    def length(self) -> float:
        """Total knife-down travel including extensions."""
        return sum(segment.length for segment in self.segments)

    @property
    def core(self) -> tuple[Segment, ...]:
        """The segments that come from the artwork (no lead-in, no overcut)."""
        first = 1 if self.lead_in is not None else 0
        last = len(self.segments) - (1 if self.overcut is not None else 0)
        return self.segments[first:last]

    @property
    def bounding_box(self) -> Box:
        """Bounding box of the whole run."""
        box = self.segments[0].bounding_box
        for segment in self.segments[1:]:
            box = box.union(segment.bounding_box)
        return box


@dataclass(frozen=True, slots=True)
class CutPlan:
    """Every cut of a job, in machining order, with the options that produced it."""

    cuts: tuple[Cut, ...]
    options: KnifeOptions

    @property
    def bounding_box(self) -> Box | None:
        """Bounding box of all cuts, or ``None`` for an empty plan."""
        if not self.cuts:
            return None
        box = self.cuts[0].bounding_box
        for cut in self.cuts[1:]:
            box = box.union(cut.bounding_box)
        return box

    @property
    def cut_length(self) -> float:
        """Total knife-down travel."""
        return sum(cut.length for cut in self.cuts)


def plan_cuts(toolpaths: Sequence[Toolpath], options: KnifeOptions) -> CutPlan:
    """Build the cut plan for the given toolpaths in the given order."""
    cuts: list[Cut] = []
    for toolpath in toolpaths:
        cuts.extend(cuts_for_toolpath(toolpath, corner_angle=options.corner_angle, overcut=options.overcut))
    return CutPlan(tuple(cuts), options)


def cuts_for_toolpath(toolpath: Toolpath, *, corner_angle: float, overcut: float) -> list[Cut]:
    """Split one toolpath at sharp corners and extend the resulting runs."""
    if toolpath.closed:
        sharp = _sharp_joints(toolpath.segments, corner_angle, closed=True)
        if not sharp:
            return [_loop_cut(toolpath, overcut)]
        # Start right after the first sharp joint so the closure joint is a lift, not a turn.
        toolpath = toolpath.rotated_to((sharp[0] + 1) % len(toolpath))
    pieces = _split_open(toolpath.segments, _sharp_joints(toolpath.segments, corner_angle, closed=False))
    return [_run_cut(piece, toolpath.source_id, overcut) for piece in pieces]


def _sharp_joints(segments: Sequence[Segment], corner_angle: float, *, closed: bool) -> list[int]:
    """Indices ``i`` where the joint between ``segments[i]`` and the next one turns too much."""
    count = len(segments)
    last = count if closed else count - 1
    return [i for i in range(last) if abs(heading_change(segments[i], segments[(i + 1) % count])) > corner_angle]


def _split_open(segments: Sequence[Segment], sharp: Sequence[int]) -> list[tuple[Segment, ...]]:
    pieces: list[tuple[Segment, ...]] = []
    start = 0
    for index in sharp:
        pieces.append(tuple(segments[start : index + 1]))
        start = index + 1
    pieces.append(tuple(segments[start:]))
    return [piece for piece in pieces if piece]


def _run_cut(piece: tuple[Segment, ...], source_id: str | None, overcut: float) -> Cut:
    if overcut <= 0.0:
        return Cut(piece, source_id=source_id)
    first, last = piece[0], piece[-1]
    lead_in = Segment(
        Line(first.p1 - first.start_tangent * overcut, first.p1),
        Hints(start_heading=first.start_heading, end_heading=first.start_heading),
    )
    overrun = Segment(
        Line(last.p2, last.p2 + last.end_tangent * overcut),
        Hints(start_heading=last.end_heading, end_heading=last.end_heading),
    )
    return Cut((lead_in, *piece, overrun), source_id=source_id, lead_in=lead_in, overcut=overrun)


def _loop_cut(toolpath: Toolpath, overcut: float) -> Cut:
    segments = toolpath.segments
    if overcut <= 0.0:
        return Cut(segments, source_id=toolpath.source_id, loop=True)
    first = segments[0]
    overrun = first if overcut >= first.length else first.subdivide(overcut / first.length)[0]
    overrun = Segment(overrun.geom, Hints(start_heading=first.start_heading))
    return Cut((*segments, overrun), source_id=toolpath.source_id, overcut=overrun, loop=True)
