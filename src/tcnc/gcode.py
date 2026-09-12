"""LinuxCNC G-code writer and the program layout for a cut plan.

The machine-specific words live in the constants at the top; everything
else is layout. Axis values are tracked so modal words are emitted only
when they change, and a feed word is recorded only when it is written.
Angles are radians on the way in and degrees on the way out; lengths are
already in G-code units.
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
    from collections.abc import Callable, Iterable

    from tcnc.corners import Cut, CutPlan
    from tcnc.options import KnifeOptions

# LinuxCNC dialect ------------------------------------------------------------
PROGRAM_DELIMITER = "%"
PLANE_XY = "G17"
UNITS_INCH = "G20"
UNITS_MM = "G21"
ABSOLUTE = "G90"
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

_WORD_ORDER = ("X", "Y", "Z", "A", "I", "J", "F")


@dataclass(frozen=True, slots=True)
class Position:
    """A machine position: lengths in G-code units, ``a`` in radians."""

    x: float
    y: float
    z: float
    a: float


@dataclass(slots=True)
class AxisState:
    """Last value written for each modal word; ``None`` until first written."""

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
        self.lines.append(line)

    def comment(self, text: str) -> None:
        """Write a comment line, if comments are enabled."""
        if self.options.gcode_comments:
            self.lines.append(f"{COMMENT_PREFIX} {text}")

    def blank(self) -> None:
        """Write an empty line, if comments are enabled (they carry the layout)."""
        if self.options.gcode_comments:
            self.lines.append("")

    def command(self, code: str, *, comment: str | None = None) -> None:
        """Write a bare command such as ``G17`` with an optional inline comment."""
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
        self._motion(RAPID, x=x, y=y, z=z, a=a, comment=comment)

    def feed(
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        a: float | None = None,
        feed: float | None = None,
        comment: str | None = None,
    ) -> None:
        """G1 to the given axis values at ``feed`` (default: the feed for the axes moved)."""
        if feed is None:
            feed = self._default_feed(x=x, y=y, z=z, a=a)
        self._motion(FEED, x=x, y=y, z=z, a=a, feed=feed, comment=comment)

    def arc(
        self,
        *,
        clockwise: bool,
        end: P,
        center: P,
        a: float | None = None,
        feed: float | None = None,
        comment: str | None = None,
    ) -> None:
        """G2/G3 from the current XY position to ``end`` about ``center``."""
        if self.state.x is None or self.state.y is None:
            msg = "an arc needs a known XY start position"
            raise PlanError(msg)
        start = P(self.state.x, self.state.y)
        start_radius = start.distance(center)
        end_radius = end.distance(center)
        if not geom2d.float_eq(start_radius, end_radius, self.options.tolerance):
            msg = f"arc end point is off the circle: start radius {start_radius:g}, end radius {end_radius:g}"
            raise PlanError(msg)
        offset = center - start
        self._motion(
            ARC_CW if clockwise else ARC_CCW,
            x=end.x,
            y=end.y,
            a=a,
            i=offset.x,
            j=offset.y,
            feed=self.options.xy_feed if feed is None else feed,
            comment=comment,
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
        self.comment(f"Units: {opts.gcode_units}")
        for text in extra_comments:
            self.comment(text)
        if opts.write_settings:
            self.comment("Settings:")
            for line in opts.as_settings_lines():
                self.comment(f"  {line}")
        self.blank()
        self.command(PLANE_XY, comment="XY plane")
        self.command(UNITS_INCH if opts.gcode_units == "in" else UNITS_MM, comment=f"units: {opts.gcode_units}")
        self.command(ABSOLUTE, comment="absolute positioning")
        self.command(CANCEL_CUTTER_COMP, comment="no cutter compensation")
        self.command(CANCEL_TOOL_LENGTH_COMP, comment="no tool length compensation")
        if opts.blend_mode == "blend":
            if opts.blend_tolerance > 0.0:
                self.command(f"{BLEND} P{self._fmt(opts.blend_tolerance)}", comment="blend with tolerance")
            else:
                self.command(BLEND, comment="blend at best speed")
        elif opts.blend_mode == "exact":
            self.command(EXACT_PATH, comment="exact path mode")
        self._motion(None, feed=opts.xy_feed, comment="default feed")
        self.blank()

    def footer(self) -> None:
        """End of program."""
        self.blank()
        self.command(END_PROGRAM, comment="end of program")
        self.raw(PROGRAM_DELIMITER)

    # internals ------------------------------------------------------------

    def _default_feed(self, *, x: float | None, y: float | None, z: float | None, a: float | None) -> float:
        if x is not None or y is not None:
            return self.options.xy_feed
        if z is not None:
            return self.options.z_feed
        if a is not None:
            return self.options.a_feed
        return self.options.xy_feed

    def _motion(
        self,
        code: str | None,
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        a: float | None = None,
        i: float | None = None,
        j: float | None = None,
        feed: float | None = None,
        comment: str | None = None,
    ) -> None:
        tol = self.options.tolerance
        words: list[str] = []
        moved = False
        if x is not None and _changed(self.state.x, x, tol):
            self.state.x = x
            words.append(f"X{self._fmt(x)}")
            moved = True
        if y is not None and _changed(self.state.y, y, tol):
            self.state.y = y
            words.append(f"Y{self._fmt(y)}")
            moved = True
        if z is not None and _changed(self.state.z, z, tol):
            self.state.z = z
            words.append(f"Z{self._fmt(z)}")
            moved = True
        if a is not None and _changed(self.state.a, a, tol):
            self.state.a = a
            words.append(f"A{self._fmt(math.degrees(a))}")
            moved = True
        if i is not None:
            words.append(f"I{self._fmt(i)}")
        if j is not None:
            words.append(f"J{self._fmt(j)}")
        if code in (ARC_CW, ARC_CCW):
            moved = True
        if not moved and code is not None:
            # Nothing moves: a feed-only change waits for the next real move.
            return
        if feed is not None and _changed(self.state.feed, feed, tol):
            self.state.feed = feed
            words.append(f"F{self._fmt(feed)}")
        if not words:
            return
        prefix = f"{code} " if code is not None else ""
        self._write(prefix + " ".join(words), comment)

    def _write(self, line: str, comment: str | None) -> None:
        if self.options.gcode_line_numbers:
            line = f"N{self._line_number} {line}"
            self._line_number += 1
        if comment and self.options.gcode_comments:
            line = f"{line}  {COMMENT_PREFIX} {comment}"
        self.lines.append(line)

    def _fmt(self, value: float) -> str:
        text = f"{value:.{self.options.output_precision}f}"
        return "0" + text[2:] if text.startswith("-0") and float(text) == 0.0 else text


def _changed(last: float | None, value: float, tolerance: float) -> bool:
    return last is None or abs(value - last) > tolerance


def _turn_toward(current: float, heading: float) -> float:
    """The A value closest to ``current`` that points along ``heading``."""
    return current + geom2d.calc_rotation(current, heading)


def write_program(plan: CutPlan, *, now: Callable[[], datetime] | None = None) -> str:
    """Lay out the whole program for ``plan`` and return its text."""
    opts = plan.options
    writer = GCodeWriter(opts, now=now)
    writer.header([f"Cuts: {len(plan.cuts)}", f"Passes per cut: {len(opts.pass_depths)}"])
    if opts.oscillation_mode == "program":
        writer.spindle_on()
    writer.rapid(z=opts.z_safe, comment="safe height")
    current_a = 0.0
    for index, cut in enumerate(plan.cuts, 1):
        writer.blank()
        writer.comment(_cut_label(cut, index, len(plan.cuts)))
        for pass_index, depth in enumerate(opts.pass_depths, 1):
            if len(opts.pass_depths) > 1:
                writer.comment(f"pass {pass_index}/{len(opts.pass_depths)} at Z{writer._fmt(depth)}")
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
        start_a = _turn_toward(current_a, segment.start_heading + opts.a_offset)
        if not geom2d.angle_eq(start_a, current_a, opts.tolerance):
            writer.feed(a=start_a, comment="rotate in place")
            current_a = start_a
        end_a = _turn_toward(current_a, segment.end_heading + opts.a_offset)
        match segment.geom:
            case Line(p2=end):
                writer.feed(x=end.x, y=end.y, a=end_a)
            case Arc(p2=end, center=center) as arc:
                writer.arc(clockwise=arc.is_clockwise, end=end, center=center, a=end_a)
        current_a = end_a
    writer.rapid(z=opts.z_safe, comment="lift")
    writer.dwell(opts.tool_wait)
    if opts.oscillation_mode == "cut":
        writer.spindle_off()
    return current_a
