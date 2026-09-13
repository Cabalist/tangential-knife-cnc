"""LinuxCNC G-code writer and the program layout for a cut plan.

Machine contract:

- Modes the program relies on are set in the header: XY plane (G17), feed
  in units per minute (G94), incremental arc centres (G91.1), spindle speed
  in RPM (G97), absolute positioning (G90), no cutter compensation (G40).
  The active work coordinate system and the tool length compensation are
  left as the controller has them: a program without a tool change runs
  in the compensation state it starts in.
- The A axis is an unwrapped rotary axis: values accumulate across closed
  shapes and every move takes the shortest rotation from the current value.
  The program assumes A = 0 at its start and ends by unwinding to A0 after
  the last lift, with the head off, so one sheet leaves the axis where the
  next one expects it. A pen operation parks the A axis once at the pen's
  mounting angle and writes no other A word.
- Tools: a numbered tool is selected with ``T n M6`` followed by ``G43``,
  which applies the loaded tool's offsets from the controller's tool table
  (LinuxCNC does not apply them on ``M6`` by itself). The oscillation is
  off at every change. With ``tool_change_z`` set, the program first goes
  to that height in machine coordinates (``G53 G0 Z``), which no work
  offset or tool length can shift; without it, no retract is written and
  the change happens where the tool is (the previous cut's lift, or the
  start position before the first change), so the controller's
  ``TOOL_CHANGE_QUILL_UP`` is expected to lift the head. Nothing else is
  written around the change: no dwell, the controller blocks until it is
  confirmed.
  ``M6`` may move the axes and ``G43`` changes the compensated coordinates,
  so the writer forgets every cached axis value at a change and positions
  Z, X, Y and A explicitly afterwards. A tool without a number is the one
  already mounted.
- Every word is written at ``output_precision`` decimals, and the writer
  tracks the *rounded* values it wrote, so modal suppression, arc
  validation and the choice of feed see what the controller sees. An arc
  is checked the way LinuxCNC reads it: the radii at both rounded ends must
  agree, and the directed sweep the rounded words describe must be the
  nominal sweep. An arc too small to express at that precision (or whose
  rounded endpoints would make the controller take the long way round)
  becomes a straight move when the chord is within the output resolution
  of the arc, and an error otherwise.
- The default feed is chosen from the axes that actually move after
  rounding: XY, else Z, else A. A caller-supplied feed is written as given.

Angles are radians on the way in and degrees on the way out; lengths are
millimetres (the header sets ``G21``). Toolpath segments may meet within the
job tolerance rather than exactly; before an arc the writer feeds to the
arc's own start point (a no-op when the rounded words do not change), so
every G2/G3 starts on its circle.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import geom2d
from geom2d import Arc, Line, P

from tcnc import __version__
from tcnc.errors import PlanError
from tcnc.options import Job, KnifeOptions, OperationSettings, Tool, as_job

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from tcnc.corners import Cut, JobPlan, OperationPlan

# LinuxCNC dialect ------------------------------------------------------------
PROGRAM_DELIMITER = "%"
PLANE_XY = "G17"
UNITS_MM = "G21"
ABSOLUTE = "G90"
FEED_PER_MINUTE = "G94"
ARC_CENTRE_INCREMENTAL = "G91.1"
SPINDLE_RPM_MODE = "G97"
CANCEL_CUTTER_COMP = "G40"
MACHINE_COORDINATES = "G53"
BLEND = "G64"
EXACT_PATH = "G61"
RAPID = "G0"
FEED = "G1"
ARC_CW = "G2"
ARC_CCW = "G3"
DWELL = "G4"
SPINDLE_ON_CW = "M3"
SPINDLE_OFF = "M5"
TOOL_CHANGE = "M6"
TOOL_OFFSETS = "G43"
END_PROGRAM = "M2"
COMMENT_PREFIX = ";"
# LinuxCNC rejects an arc whose start and end radii differ by more than this (mm).
RADIUS_TOLERANCE = 0.005
# Rounding moves each of the start, end and centre by at most half a resolution per axis; this many
# resolutions of angular slack at the arc's radius covers the sweep the controller reconstructs.
_SWEEP_BUDGET_RESOLUTIONS = 4.0

_CONTROL_CHARS = {chr(code) for code in range(32)} | {chr(127)}


def format_word(value: float, precision: int) -> str:
    """Format a rounded value at ``precision`` decimals, never as negative zero."""
    rounded = round(value, precision)
    if rounded == 0.0:
        rounded = 0.0
    return f"{rounded:.{precision}f}"


def comment_lines(text: str) -> list[str]:
    """Split a comment into lines with control characters removed, so none can become a command."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in cleaned.split("\n"):
        stripped = "".join(char for char in line if char not in _CONTROL_CHARS or char == "\t").replace("\t", " ")
        lines.append(stripped)
    return lines


def _reject_control_chars(text: str, what: str) -> None:
    if any(char in _CONTROL_CHARS for char in text):
        msg = f"{what} contains control characters: {text!r}"
        raise PlanError(msg)


@dataclass(frozen=True, slots=True)
class Move:
    """One motion line to serialize: the command and the axis words it may carry."""

    code: str | None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    a: float | None = None
    i: float | None = None
    j: float | None = None
    feed: float | None = None
    comment: str | None = None


@dataclass(slots=True)
class AxisState:
    """Last *rounded* value written for each modal word; ``None`` until first written."""

    x: float | None = None
    y: float | None = None
    z: float | None = None
    a: float | None = None
    feed: float | None = None


class GCodeWriter:
    """Accumulates G-code lines for one program.

    ``job`` holds the program-wide settings; ``settings`` is the operation
    whose feeds and tool the motion methods use, set by ``write_program``
    as it moves from operation to operation (initially the first one).
    """

    def __init__(self, job: Job | KnifeOptions, *, now: Callable[[], datetime] | None = None) -> None:
        self.job = as_job(job)
        self.settings: OperationSettings = self.job.settings[0]
        self.state = AxisState()
        self.lines: list[str] = []
        self._now = now or (lambda: datetime.now(UTC))
        self._line_number = 1

    # output ---------------------------------------------------------------

    def text(self) -> str:
        """The program so far, one line per entry, newline-terminated."""
        return "".join(f"{line}\n" for line in self.lines)

    def raw(self, line: str) -> None:
        """Write a line verbatim (no line number, no comment)."""
        _reject_control_chars(line, "raw line")
        self.lines.append(line)

    def comment(self, text: str) -> None:
        """Write a comment (one output line per input line), if comments are enabled."""
        if self.job.gcode_comments:
            self.lines.extend(f"{COMMENT_PREFIX} {line}" for line in comment_lines(text))

    def blank(self) -> None:
        """Write an empty line, if comments are enabled (they carry the layout)."""
        if self.job.gcode_comments:
            self.lines.append("")

    def command(self, code: str, *, comment: str | None = None) -> None:
        """Write a bare command such as ``G17`` with an optional inline comment."""
        _reject_control_chars(code, "command")
        self._write(code, comment)

    # motion ---------------------------------------------------------------

    def rapid(
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        a: float | None = None,
        comment: str | None = None,
    ) -> None:
        """G0 to the given axis values; unchanged axes are omitted."""
        self._motion(Move(RAPID, x=x, y=y, z=z, a=a, comment=comment))

    def feed(  # noqa: PLR0913 - one keyword-only parameter per axis word
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        a: float | None = None,
        feed: float | None = None,
        comment: str | None = None,
    ) -> None:
        """G1 to the given axis values at ``feed`` (default: chosen from the axes that move after rounding)."""
        self._motion(Move(FEED, x=x, y=y, z=z, a=a, feed=feed, comment=comment))

    def arc(  # noqa: PLR0913 - one keyword-only parameter per axis word
        self,
        *,
        end: P,
        center: P,
        sweep: float,
        a: float | None = None,
        feed: float | None = None,
        comment: str | None = None,
    ) -> None:
        """G2/G3 from the current XY position to ``end`` about ``center``, sweeping ``sweep`` radians (CCW positive).

        Validation happens on the rounded words the controller will read:
        the radii at both ends must agree within LinuxCNC's tolerance and the
        directed sweep those words describe must match the nominal one. An
        arc that cannot be expressed at this precision (its end rounds onto
        its start, an end rounds onto the centre, or the rounded endpoints
        would send the controller the long way round) is written as a
        straight move when its chord is within the output resolution of the
        arc; otherwise it is an error.
        """
        if self.state.x is None or self.state.y is None:
            msg = "an arc needs a known XY start position"
            raise PlanError(msg)
        precision = self.job.output_precision
        resolution = self.job.output_resolution
        start = P(self.state.x, self.state.y)
        end_rounded = P(round(end.x, precision), round(end.y, precision))
        offset = center - start
        i, j = round(offset.x, precision), round(offset.y, precision)
        centre_rounded = P(start.x + i, start.y + j)
        start_radius = start.distance(centre_rounded)
        end_radius = end_rounded.distance(centre_rounded)
        problem: str | None = None
        if sweep == 0.0 or end_rounded == start:
            problem = "its end rounds onto its start"
        elif start_radius == 0.0 or end_radius == 0.0:
            problem = "an end rounds onto its centre"
        elif abs(start_radius - end_radius) > RADIUS_TOLERANCE:
            msg = (
                f"arc end point is off the circle at {precision} decimals: "
                f"start radius {start_radius:g}, end radius {end_radius:g}"
            )
            raise PlanError(msg)
        else:
            written = _written_sweep(start, end_rounded, centre_rounded, clockwise=sweep < 0.0)
            budget = _SWEEP_BUDGET_RESOLUTIONS * resolution / min(start_radius, end_radius)
            if abs(written - abs(sweep)) > budget:
                problem = f"its sweep would be {math.degrees(written):.4f}° instead of {math.degrees(abs(sweep)):.4f}°"
        if problem is not None:
            # Every unrepresentable arc takes the same exit: a straight move when the arc never leaves
            # the output resolution around its chord, otherwise an error.
            sagitta = end.distance(center) * (1.0 - math.cos(sweep / 2.0))
            if sagitta > resolution:
                msg = f"arc cannot be written at {precision} decimals: {problem}"
                raise PlanError(msg)
            self._motion(Move(FEED, x=end.x, y=end.y, a=a, feed=feed, comment=comment))
            return
        self._motion(
            Move(ARC_CW if sweep < 0.0 else ARC_CCW, x=end.x, y=end.y, a=a, i=i, j=j, feed=feed, comment=comment)
        )

    def dwell(self, seconds: float) -> None:
        """G4 pause for ``seconds`` (nothing is written for zero or less)."""
        if seconds > 0.0:
            self._write(f"{DWELL} P{seconds:.3f}", f"pause {seconds:.3f} s")

    def spindle_on(self) -> None:
        """Start the oscillating head and wait for it to come up to speed."""
        tool = self.settings.tool
        self._write(
            f"{SPINDLE_ON_CW} S{tool.spindle_speed}" if tool.spindle_speed > 0 else SPINDLE_ON_CW, "oscillation on"
        )
        self.dwell(tool.spindle_wait_on)

    def spindle_off(self) -> None:
        """Stop the oscillating head."""
        self._write(SPINDLE_OFF, "oscillation off")

    def machine_z(self, z: float, *, comment: str | None = None) -> None:
        """Rapid to ``z`` in machine coordinates (``G53 G0 Z``): no work offset or tool length applies.

        The work-coordinate Z is unknown afterwards, so the next Z move
        writes its word again.
        """
        self._write(f"{MACHINE_COORDINATES} {RAPID} Z{self._fmt(z)}", comment)
        self.state.z = None

    def tool_change(self, tool: Tool) -> None:
        """Select ``tool`` (``T n M6``) and apply its offsets from the tool table (``G43``).

        Raises:
            PlanError: For a tool without a number (it is mounted; there is nothing to change to).
        """
        if tool.number is None:
            msg = f"tool {tool.name!r} has no number to change to"
            raise PlanError(msg)
        self._write(f"T{tool.number} {TOOL_CHANGE}", f"tool change: {tool.name} ({tool.kind})")
        self.command(TOOL_OFFSETS, comment="tool offsets from the tool table")
        self.forget_position()

    def forget_position(self) -> None:
        """Drop every cached axis value: the next move writes all its words (the feed stays modal)."""
        self.state = AxisState(feed=self.state.feed)

    # program layout -------------------------------------------------------

    def header(self, extra_comments: Iterable[str] = ()) -> None:
        """Program delimiter, provenance comments and modal setup."""
        opts = self.job
        self.raw(PROGRAM_DELIMITER)
        self.comment(f"Generated by tcnc {__version__}")
        self.comment(f"Created {self._now().isoformat(timespec='seconds')}")
        self.comment("Units: mm; A axis unwrapped; material surface at Z0")
        for text in extra_comments:
            self.comment(text)
        if opts.write_settings:
            self.comment("Settings:")
            for line in opts.settings_lines():
                self.comment(f"  {line}")
        self.blank()
        self.command(PLANE_XY, comment="XY plane")
        self.command(UNITS_MM, comment="millimetres")
        self.command(ABSOLUTE, comment="absolute positioning")
        self.command(FEED_PER_MINUTE, comment="feed in mm per minute")
        self.command(ARC_CENTRE_INCREMENTAL, comment="arc centres relative to the start point")
        self.command(SPINDLE_RPM_MODE, comment="spindle speed in RPM")
        self.command(CANCEL_CUTTER_COMP, comment="no cutter compensation")
        if opts.blend_mode == "blend":
            if opts.blend_tolerance > 0.0:
                self.command(f"{BLEND} P{self._fmt(opts.blend_tolerance)}", comment="blend with tolerance")
            else:
                self.command(BLEND, comment="blend at best speed")
        elif opts.blend_mode == "exact":
            self.command(EXACT_PATH, comment="exact path mode")
        self._motion(Move(None, feed=self.settings.xy_feed, comment="default feed"))
        self.blank()

    def footer(self) -> None:
        """End of program."""
        self.blank()
        self.command(END_PROGRAM, comment="end of program")
        self.raw(PROGRAM_DELIMITER)

    # internals ------------------------------------------------------------

    def _default_feed(self, words: Sequence[str | None]) -> float:
        """The feed for the axis words actually written, in X/Y/Z/A order: XY, else Z, else A."""
        x, y, z, a = words
        if x is not None or y is not None:
            return self.settings.xy_feed
        if z is not None:
            return self.settings.z_feed
        if a is not None:
            return self.settings.a_feed
        return self.settings.xy_feed

    def _axis_word(self, letter: str, value: float | None, last: float | None) -> tuple[str | None, float | None]:
        """The word to write for an axis, and the rounded value to record (``None`` = unchanged)."""
        if value is None:
            return None, None
        rounded = round(value, self.job.output_precision)
        if rounded == 0.0:
            rounded = 0.0
        if last is not None and rounded == last:
            return None, None
        return f"{letter}{self._fmt(rounded)}", rounded

    def _motion(self, move: Move) -> None:
        state = self.state
        axes = (
            self._axis_word("X", move.x, state.x),
            self._axis_word("Y", move.y, state.y),
            self._axis_word("Z", move.z, state.z),
            self._axis_word("A", None if move.a is None else math.degrees(move.a), state.a),
        )
        words = [word for word, _ in axes if word is not None]
        if move.code in (ARC_CW, ARC_CCW):
            words.append(f"I{self._fmt(move.i or 0.0)}")
            words.append(f"J{self._fmt(move.j or 0.0)}")
        if not words and move.code is not None:
            # Nothing moves: a feed-only change waits for the next real move.
            return
        feed = move.feed
        if feed is None and move.code in (FEED, ARC_CW, ARC_CCW):
            feed = self.settings.xy_feed if move.code != FEED else self._default_feed([word for word, _ in axes])
        feed_word, feed_new = (None, None) if feed is None else self._axis_word("F", feed, state.feed)
        if feed_word is not None:
            words.append(feed_word)
        if not words:
            return
        state.x, state.y, state.z, state.a = (
            new if new is not None else old
            for (_, new), old in zip(axes, (state.x, state.y, state.z, state.a), strict=True)
        )
        if feed_new is not None:
            state.feed = feed_new
        prefix = f"{move.code} " if move.code is not None else ""
        self._write(prefix + " ".join(words), move.comment)

    def _write(self, line: str, comment: str | None) -> None:
        if self.job.gcode_line_numbers:
            line = f"N{self._line_number} {line}"
            self._line_number += 1
        if comment and self.job.gcode_comments:
            inline = " | ".join(part for part in comment_lines(comment) if part)
            line = f"{line}  {COMMENT_PREFIX} {inline}"
        self.lines.append(line)

    def _fmt(self, value: float) -> str:
        return format_word(value, self.job.output_precision)


def _turn_toward(current: float, heading: float) -> float:
    """The A value closest to ``current`` that points along ``heading``."""
    return current + geom2d.calc_rotation(current, heading)


def _written_sweep(start: P, end: P, centre: P, *, clockwise: bool) -> float:
    """The unsigned sweep LinuxCNC derives from rounded start, end and centre (``find_turn``).

    Equal start and end angles about the centre mean a full turn to the
    controller, never no turn.
    """
    start_angle = math.atan2(start.y - centre.y, start.x - centre.x)
    end_angle = math.atan2(end.y - centre.y, end.x - centre.x)
    turn = (start_angle - end_angle) if clockwise else (end_angle - start_angle)
    turn %= math.tau
    return math.tau if turn == 0.0 else turn


def write_program(plan: JobPlan, *, now: Callable[[], datetime] | None = None) -> str:
    """Lay out the whole program for ``plan`` and return its text.

    Operations follow each other in order; a tool change is written only
    when the tool differs from the one in use, and never for a tool without
    a number (the one mounted before the program starts). Before a change
    the program goes to ``job.tool_change_z`` in machine coordinates when
    that is set, and writes no retract otherwise. The program ends raised,
    with the head off and the A axis unwound to zero.
    """
    job = plan.job
    writer = GCodeWriter(job, now=now)
    operations = plan.operations
    count = len(operations)
    if count == 1:
        (only,) = operations
        extras = [f"Cuts: {len(only.cuts)}", f"Passes per cut: {only.settings.pass_count}"]
    else:
        extras = [f"Operations: {count}", f"Cuts: {len(plan.cuts)}"]
    writer.header(extras)
    current_a = 0.0
    current_tool: Tool | None = None
    for index, operation in enumerate(operations, 1):
        settings = operation.settings
        writer.settings = settings
        tool = settings.tool
        if count > 1 or tool.number is not None:
            writer.comment(_operation_label(settings, index, count))
        if (current_tool is None or tool.name != current_tool.name) and tool.number is not None:
            if job.tool_change_z is not None:
                writer.machine_z(job.tool_change_z, comment="tool change height, machine coordinates")
            writer.tool_change(tool)
        current_tool = tool
        current_a = _write_operation(writer, operation, current_a)
    writer.rapid(a=0.0, comment="A axis back to zero for the next program")
    writer.footer()
    return writer.text()


def _write_operation(writer: GCodeWriter, operation: OperationPlan, current_a: float) -> float:
    """Switch the head, lift to the operation's safe height, park a pen and write its cuts; returns the A value."""
    settings = operation.settings
    if settings.oscillation_mode == "operation":
        writer.spindle_on()
    writer.rapid(z=settings.z_safe, comment="safe height")
    if not settings.tangential:
        current_a = _turn_toward(current_a, settings.a_offset)
        writer.rapid(a=current_a, comment="park the pen at its mounting angle")
    depths = settings.pass_depths
    precision = writer.job.output_precision
    for cut_index, cut in enumerate(operation.cuts, 1):
        writer.blank()
        writer.comment(_cut_label(cut, cut_index, len(operation.cuts)))
        for pass_index, depth in enumerate(depths, 1):
            if len(depths) > 1:
                writer.comment(f"pass {pass_index}/{len(depths)} at Z{format_word(depth, precision)}")
            current_a = _write_cut(writer, cut, depth, current_a)
    writer.blank()
    if settings.oscillation_mode == "operation":
        writer.spindle_off()
    return current_a


def _operation_label(settings: OperationSettings, index: int, count: int) -> str:
    tool = settings.tool
    number = f", T{tool.number}" if tool.number is not None else ""
    return f"Operation {index}/{count}: {settings.name} ({tool.kind}{number})"


def _cut_label(cut: Cut, index: int, count: int) -> str:
    kind = "loop" if cut.loop else "run"
    source = f" from {cut.source_id}" if cut.source_id else ""
    return f"Cut {index}/{count}: {kind}{source}, {len(cut.core)} segment(s)"


def _write_cut(writer: GCodeWriter, cut: Cut, depth: float, current_a: float) -> float:
    """One pass over ``cut``; returns the A value the axis ends at (unchanged for a non-tangential tool)."""
    settings = writer.settings
    tangential = settings.tangential
    if tangential:
        current_a = _turn_toward(current_a, cut.start_heading + settings.a_offset)
        writer.rapid(x=cut.start.x, y=cut.start.y, a=current_a)
    else:
        writer.rapid(x=cut.start.x, y=cut.start.y)
    if settings.oscillation_mode == "cut":
        writer.spindle_on()
    writer.feed(z=depth, comment="plunge")
    writer.dwell(settings.tool_wait)
    for segment in cut.segments:
        end_a: float | None = None
        if tangential:
            # Both feeds below are suppressed by the writer when the rounded words do not change.
            start_a = _turn_toward(current_a, segment.start_heading + settings.a_offset)
            writer.feed(a=start_a, comment="rotate in place")
            current_a = start_a
            end_a = current_a + segment.rotation
        match segment.geom:
            case Line(p2=end):
                writer.feed(x=end.x, y=end.y, a=end_a)
            case Arc(p1=start, p2=end, center=center, angle=sweep):
                writer.feed(x=start.x, y=start.y)
                writer.arc(end=end, center=center, sweep=sweep, a=end_a)
        if end_a is not None:
            current_a = end_a
    writer.rapid(z=settings.z_safe, comment="lift")
    writer.dwell(settings.tool_wait)
    if settings.oscillation_mode == "cut":
        writer.spindle_off()
    return current_a
