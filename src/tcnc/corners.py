"""Turn toolpaths into cuts: split at sharp corners, add lead-in and overcut.

A ``Cut`` is one continuous knife-down run. Wherever the blade would have to
turn by more than the corner angle while in the material, the toolpath is
split. That includes compensation connectors: a connector whose recorded
source ``joint_turn`` exceeds the threshold is removed (every piece of it)
and becomes a lift. Every open run is extended at both ends along the blade
heading by the overcut distance so the angled blade finishes the corner. A
closed toolpath with no sharp corner becomes one loop that simply overruns
its start. A ``Cut`` enforces the same traversal invariants as a
``Toolpath`` at the same tolerance, so the writer can trust either.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from geom2d import Box, Line, P

from tcnc.errors import PlanError
from tcnc.toolpath import Hints, Segment, Toolpath, check_traversal, heading_change

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tcnc.options import KnifeOptions


@dataclass(frozen=True, slots=True)
class Cut:
    """One knife-down run: the artwork segments plus optional lead-in and overcut.

    ``tolerance`` is the distance at which the core is judged connected (and
    a loop closed); ``None`` means geom2d's ``EPSILON``.
    """

    core: tuple[Segment, ...]
    source_id: str | None = None
    lead_in: Segment | None = None
    overcut: Segment | None = None
    loop: bool = False
    tolerance: float | None = None

    def __post_init__(self) -> None:
        """The core is a valid traversal (closed when ``loop``) and the extensions attach to it exactly.

        A run's overcut starts where its last segment ends; a loop's overcut
        is the beginning of the loop cut again, so it starts where the core
        starts. Extensions are single segments built from the core's own
        endpoints, so they attach exactly.
        """
        check_traversal(self.core, self.tolerance, closed=self.loop, what="cut")
        if self.lead_in is not None and not self.lead_in.p2.almost_equal(self.core[0].p1):
            msg = "lead-in does not end where the cut starts"
            raise PlanError(msg)
        if self.overcut is not None:
            anchor = self.core[0].p1 if self.loop else self.core[-1].p2
            if not self.overcut.p1.almost_equal(anchor):
                msg = "overcut does not start where the cut ends"
                raise PlanError(msg)
        for name in ("lead_in", "overcut"):
            extension = getattr(self, name)
            if extension is not None:
                check_traversal((extension,), None, closed=False, what=name.replace("_", "-"))

    @property
    def segments(self) -> tuple[Segment, ...]:
        """The full knife-down traversal: lead-in, core, overcut."""
        head = () if self.lead_in is None else (self.lead_in,)
        tail = () if self.overcut is None else (self.overcut,)
        return (*head, *self.core, *tail)

    @property
    def start(self) -> P:
        """Where the knife plunges."""
        return self.segments[0].p1

    @property
    def end(self) -> P:
        """Where the knife lifts."""
        return self.segments[-1].p2

    @property
    def start_heading(self) -> float:
        """Blade heading the cut begins with."""
        return self.segments[0].start_heading

    @property
    def end_heading(self) -> float:
        """Blade heading the cut ends with."""
        return self.segments[-1].end_heading

    @property
    def length(self) -> float:
        """Total knife-down travel including extensions."""
        return sum(segment.length for segment in self.segments)

    @property
    def bounding_box(self) -> Box:
        """Bounding box of the whole run."""
        segments = self.segments
        box = segments[0].bounding_box
        for segment in segments[1:]:
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


def is_sharp_connector(segment: Segment, corner_angle: float) -> bool:
    """True for a compensation connector (any piece of one) whose source joint turns more than ``corner_angle``."""
    joint = segment.hints.joint_turn
    return joint is not None and abs(joint) > corner_angle


def is_sharp_joint(before: Segment, after: Segment, corner_angle: float) -> bool:
    """True when the knife must lift between ``before`` and ``after``."""
    if is_sharp_connector(before, corner_angle) or is_sharp_connector(after, corner_angle):
        return True
    return abs(heading_change(before, after)) > corner_angle


def entry_indices(toolpath: Toolpath, corner_angle: float) -> list[int]:
    """Segment indices of a closed toolpath at which a cut may start without an extra lift.

    These are the segments that follow a lift boundary. An empty list means
    the path is one smooth loop and any vertex is as good as another.
    """
    segments = toolpath.segments
    count = len(segments)
    return [
        index
        for index in range(count)
        if not is_sharp_connector(segments[index], corner_angle)
        and is_sharp_joint(segments[index - 1], segments[index], corner_angle)
    ]


def cuts_for_toolpath(toolpath: Toolpath, *, corner_angle: float, overcut: float) -> list[Cut]:
    """Split one toolpath at sharp corners and extend the resulting runs."""
    if toolpath.closed:
        entries = entry_indices(toolpath, corner_angle)
        if not entries:
            return [_loop_cut(toolpath, overcut)]
        # Keep the chosen start when it is already a lift boundary; otherwise move to the first one.
        if 0 not in entries:
            toolpath = toolpath.rotated_to(entries[0])
    runs = _split_runs(toolpath.segments, corner_angle)
    return [_run_cut(run, toolpath, overcut) for run in runs]


def _split_runs(segments: Sequence[Segment], corner_angle: float) -> list[tuple[Segment, ...]]:
    runs: list[tuple[Segment, ...]] = []
    current: list[Segment] = []
    for segment in segments:
        if is_sharp_connector(segment, corner_angle):
            if current:
                runs.append(tuple(current))
                current = []
            continue
        if current and abs(heading_change(current[-1], segment)) > corner_angle:
            runs.append(tuple(current))
            current = []
        current.append(segment)
    if current:
        runs.append(tuple(current))
    return runs


def _extension(point: P, heading: float, length: float, *, before: bool) -> Segment:
    """A straight lead-in (``before``) or overcut line along the blade heading."""
    other = point + P.from_polar(length, heading)
    line = Line(other, point) if before else Line(point, other)
    return Segment(line, Hints(start_heading=heading, end_heading=heading))


def _run_cut(run: tuple[Segment, ...], toolpath: Toolpath, overcut: float) -> Cut:
    if overcut <= 0.0:
        return Cut(run, source_id=toolpath.source_id, tolerance=toolpath.tolerance)
    first, last = run[0], run[-1]
    lead_in = _extension(first.p1, first.start_heading, -overcut, before=True)
    overrun = _extension(last.p2, last.end_heading, overcut, before=False)
    return Cut(run, source_id=toolpath.source_id, lead_in=lead_in, overcut=overrun, tolerance=toolpath.tolerance)


def _loop_cut(toolpath: Toolpath, overcut: float) -> Cut:
    segments = toolpath.segments
    if overcut <= 0.0:
        return Cut(segments, source_id=toolpath.source_id, loop=True, tolerance=toolpath.tolerance)
    first = segments[0]
    overrun = first if overcut >= first.length else first.subdivide(overcut / first.length)[0]
    return Cut(segments, source_id=toolpath.source_id, overcut=overrun, loop=True, tolerance=toolpath.tolerance)
