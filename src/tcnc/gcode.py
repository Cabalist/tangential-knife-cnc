"""LinuxCNC G-code writer and the program layout for a cut plan.

Machine contract:

- Modes the program relies on are set in the header: XY plane (G17), feed
  in units per minute (G94), incremental arc centres (G91.1), spindle speed
  in RPM (G97), absolute positioning (G90), no cutter or tool-length
  compensation. The active work coordinate system is left as the
  controller has it.
- The A axis is an unwrapped rotary axis: values accumulate across closed
  shapes and every move takes the shortest rotation from the current value.
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

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from tcnc.corners import Cut, CutPlan
    from tcnc.options import KnifeOptions

# LinuxCNC dialect ------------------------------------------------------------
PROGRAM_DELIMITER = "%"
PLANE_XY = "G17"
UNITS_MM = "G21"
ABSOLUTE = "G90"
FEED_PER_MINUTE = "G94"
ARC_CENTRE_INCREMENTAL = "G91.1"
SPINDLE_RPM_MODE = "G97"
CANCEL_CUTTER_COMP = "G40"
CANCEL_TOOL_LENGTH_COMP = "G49"
BLEND = "G64"
EXACT_PATH = "G61"
RAPID = "G0"
FEED = "G1"
ARC_CW = "G2"
ARC_CCW = "G3"
DWELL = "G4"
SPINDLE_ON_CW = "M3"
SPINDLE_OFF = "M5"
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
    """Accumulates G-code lines for one program."""

    def __init__(self, options: KnifeOptions, *, now: Callable[[], datetime] | None = None) -> None:
        self.options = options
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
        if self.options.gcode_comments:
            self.lines.extend(f"{COMMENT_PREFIX} {line}" for line in comment_lines(text))

    def blank(self) -> None:
        """Write an empty line, if comments are enabled (they carry the layout)."""
        if self.options.gcode_comments:
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
        arc that cannot be expressed at this precision (its end or centre
        rounds onto its start, or the rounded endpoints would send the
        controller the long way round) is written as a straight move when
        its chord is within the output resolution of the arc; otherwise it
        is an error.
        """
        if self.state.x is None or self.state.y is None:
            msg = "an arc needs a known XY start position"
            raise PlanError(msg)
        precision = self.options.output_precision
        resolution = self.options.output_resolution
        start = P(self.state.x, self.state.y)
        end_rounded = P(round(end.x, precision), round(end.y, precision))
        offset = center - start
        i, j = round(offset.x, precision), round(offset.y, precision)
        if sweep == 0.0 or end_rounded == start or (i == 0.0 and j == 0.0):
            self._motion(Move(FEED, x=end.x, y=end.y, a=a, feed=feed, comment=comment))
            return
        centre_rounded = P(start.x + i, start.y + j)
        start_radius = start.distance(centre_rounded)
        end_radius = end_rounded.distance(centre_rounded)
        if abs(start_radius - end_radius) > RADIUS_TOLERANCE:
            msg = (
                f"arc end point is off the circle at {precision} decimals: "
                f"start radius {start_radius:g}, end radius {end_radius:g}"
            )
            raise PlanError(msg)
        written = _written_sweep(start, end_rounded, centre_rounded, clockwise=sweep < 0.0)
        budget = _SWEEP_BUDGET_RESOLUTIONS * resolution / min(start_radius, end_radius)
        if abs(written - abs(sweep)) > budget:
            sagitta = end.distance(center) * (1.0 - math.cos(sweep / 2.0))
            if sagitta > resolution:
                msg = (
                    f"arc sweep changes after rounding to {precision} decimals: "
                    f"{math.degrees(abs(sweep)):.4f}° nominal, {math.degrees(written):.4f}° as written"
                )
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
        speed = self.options.spindle_speed
        self._write(f"{SPINDLE_ON_CW} S{speed}" if speed > 0 else SPINDLE_ON_CW, "oscillation on")
        self.dwell(self.options.spindle_wait_on)

    def spindle_off(self) -> None:
        """Stop the oscillating head."""
        self._write(SPINDLE_OFF, "oscillation off")

    # program layout -------------------------------------------------------

    def header(self, extra_comments: Iterable[str] = ()) -> None:
        """Program delimiter, provenance comments and modal setup."""
        opts = self.options
        self.raw(PROGRAM_DELIMITER)
        self.comment(f"Generated by tcnc {__version__}")
        self.comment(f"Created {self._now().isoformat(timespec='seconds')}")
        self.comment("Units: mm; A axis unwrapped; material surface at Z0")
        for text in extra_comments:
            self.comment(text)
        if opts.write_settings:
            self.comment("Settings:")
            for line in opts.as_settings_lines():
                self.comment(f"  {line}")
        self.blank()
        self.command(PLANE_XY, comment="XY plane")
        self.command(UNITS_MM, comment="millimetres")
        self.command(ABSOLUTE, comment="absolute positioning")
        self.command(FEED_PER_MINUTE, comment="feed in mm per minute")
        self.command(ARC_CENTRE_INCREMENTAL, comment="arc centres relative to the start point")
        self.command(SPINDLE_RPM_MODE, comment="spindle speed in RPM")
        self.command(CANCEL_CUTTER_COMP, comment="no cutter compensation")
        self.command(CANCEL_TOOL_LENGTH_COMP, comment="no tool length compensation")
        if opts.blend_mode == "blend":
            if opts.blend_tolerance > 0.0:
                self.command(f"{BLEND} P{self._fmt(opts.blend_tolerance)}", comment="blend with tolerance")
            else:
                self.command(BLEND, comment="blend at best speed")
        elif opts.blend_mode == "exact":
            self.command(EXACT_PATH, comment="exact path mode")
        self._motion(Move(None, feed=opts.xy_feed, comment="default feed"))
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
            return self.options.xy_feed
        if z is not None:
            return self.options.z_feed
        if a is not None:
            return self.options.a_feed
        return self.options.xy_feed

    def _axis_word(self, letter: str, value: float | None, last: float | None) -> tuple[str | None, float | None]:
        """The word to write for an axis, and the rounded value to record (``None`` = unchanged)."""
        if value is None:
            return None, None
        rounded = round(value, self.options.output_precision)
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
            feed = self.options.xy_feed if move.code != FEED else self._default_feed([word for word, _ in axes])
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
        if self.options.gcode_line_numbers:
            line = f"N{self._line_number} {line}"
            self._line_number += 1
        if comment and self.options.gcode_comments:
            inline = " | ".join(part for part in comment_lines(comment) if part)
            line = f"{line}  {COMMENT_PREFIX} {inline}"
        self.lines.append(line)

    def _fmt(self, value: float) -> str:
        return format_word(value, self.options.output_precision)


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


def write_program(plan: CutPlan, *, now: Callable[[], datetime] | None = None) -> str:
    """Lay out the whole program for ``plan`` and return its text."""
    opts = plan.options
    depths = opts.pass_depths
    writer = GCodeWriter(opts, now=now)
    writer.header([f"Cuts: {len(plan.cuts)}", f"Passes per cut: {len(depths)}"])
    if opts.oscillation_mode == "program":
        writer.spindle_on()
    writer.rapid(z=opts.z_safe, comment="safe height")
    current_a = 0.0
    for index, cut in enumerate(plan.cuts, 1):
        writer.blank()
        writer.comment(_cut_label(cut, index, len(plan.cuts)))
        for pass_index, depth in enumerate(depths, 1):
            if len(depths) > 1:
                writer.comment(f"pass {pass_index}/{len(depths)} at Z{format_word(depth, opts.output_precision)}")
            current_a = _write_cut(writer, cut, depth, current_a)
    writer.blank()
    if opts.oscillation_mode == "program":
        writer.spindle_off()
    writer.footer()
    return writer.text()


def _cut_label(cut: Cut, index: int, count: int) -> str:
    kind = "loop" if cut.loop else "run"
    source = f" from {cut.source_id}" if cut.source_id else ""
    return f"Cut {index}/{count}: {kind}{source}, {len(cut.core)} segment(s)"


def _write_cut(writer: GCodeWriter, cut: Cut, depth: float, current_a: float) -> float:
    opts = writer.options
    current_a = _turn_toward(current_a, cut.start_heading + opts.a_offset)
    writer.rapid(x=cut.start.x, y=cut.start.y, a=current_a)
    if opts.oscillation_mode == "cut":
        writer.spindle_on()
    writer.feed(z=depth, comment="plunge")
    writer.dwell(opts.tool_wait)
    for segment in cut.segments:
        # Both feeds below are suppressed by the writer when the rounded words do not change.
        start_a = _turn_toward(current_a, segment.start_heading + opts.a_offset)
        writer.feed(a=start_a, comment="rotate in place")
        current_a = start_a
        end_a = current_a + segment.rotation
        match segment.geom:
            case Line(p2=end):
                writer.feed(x=end.x, y=end.y, a=end_a)
            case Arc(p1=start, p2=end, center=center, angle=sweep):
                writer.feed(x=start.x, y=start.y)
                writer.arc(end=end, center=center, sweep=sweep, a=end_a)
        current_a = end_a
    writer.rapid(z=opts.z_safe, comment="lift")
    writer.dwell(opts.tool_wait)
    if opts.oscillation_mode == "cut":
        writer.spindle_off()
    return current_a
