"""G-code writer: modal words, feeds, arcs, layout."""

import math
from datetime import UTC, datetime
from itertools import pairwise

import pytest
from geom2d import Line, P

from tcnc.corners import plan_cuts
from tcnc.errors import PlanError
from tcnc.gcode import GCodeWriter, write_program
from tcnc.options import KnifeOptions
from tcnc.toolpath import Toolpath
from tests.test_corners import circle, square

FIXED_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def writer(**changes: object) -> GCodeWriter:
    return GCodeWriter(KnifeOptions().with_(**changes), now=lambda: FIXED_NOW)


def body(text: str) -> list[str]:
    """Command lines only: no blank lines, no comment lines, no inline comments."""
    stripped = (line.split("  ;", 1)[0].rstrip() for line in text.splitlines())
    return [line for line in stripped if line and not line.startswith(";")]


def test_header_and_footer_words() -> None:
    w = writer(gcode_units="mm", blend_mode="blend", blend_tolerance=0.01)
    w.header()
    w.footer()
    lines = body(w.text())
    assert lines[0] == "%"
    assert any(line.startswith("G17") for line in lines)
    assert any(line.startswith("G21") for line in lines)
    assert any(line.startswith("G90") for line in lines)
    assert any(line.startswith("G40") for line in lines)
    assert any(line.startswith("G49") for line in lines)
    assert any(line.startswith("G64 P0.0100") for line in lines)
    assert any(line.startswith("F10.0000") for line in lines)
    assert lines[-2].startswith("M2")
    assert lines[-1] == "%"
    assert "Created 2026-09-12T12:00:00+00:00" in w.text()


def test_modal_suppression_and_feed_recording() -> None:
    w = writer(gcode_comments=False)
    w.feed(x=1.0, y=0.0)
    w.feed(x=1.0, y=0.0)  # nothing changes: no line
    w.feed(x=2.0)  # same feed: no F word
    w.feed(z=-0.1)  # z feed differs: F emitted
    assert w.lines == ["G1 X1.0000 Y0.0000 F10.0000", "G1 X2.0000", "G1 Z-0.1000"]
    w2 = writer(gcode_comments=False, z_feed=5.0)
    w2.feed(x=1.0)
    w2.feed(z=-0.1)
    w2.feed(z=-0.1, feed=5.0)  # no motion: feed change must not be recorded
    w2.feed(x=2.0)
    assert w2.lines == ["G1 X1.0000 F10.0000", "G1 Z-0.1000 F5.0000", "G1 X2.0000 F10.0000"]


def test_angles_are_degrees_and_negative_zero_is_normalised() -> None:
    w = writer(gcode_comments=False)
    w.rapid(x=0.0, y=-0.00001, a=math.pi / 2)
    assert w.lines == ["G0 X0.0000 Y0.0000 A90.0000"]


def test_arc_words_and_validation() -> None:
    w = writer(gcode_comments=False)
    w.rapid(x=1.0, y=0.0)
    w.arc(clockwise=False, end=P(0, 1), center=P(0, 0))
    assert w.lines[-1] == "G3 X0.0000 Y1.0000 I-1.0000 J0.0000 F10.0000"
    with pytest.raises(PlanError):
        w.arc(clockwise=True, end=P(5, 5), center=P(0, 0))
    fresh = writer()
    with pytest.raises(PlanError):
        fresh.arc(clockwise=True, end=P(1, 0), center=P(0, 0))


def test_line_numbers_and_comment_toggle() -> None:
    w = writer(gcode_line_numbers=True)
    w.command("G17", comment="plane")
    w.comment("note")
    assert w.lines == ["N1 G17  ; plane", "; note"]
    quiet = writer(gcode_comments=False)
    quiet.command("G17", comment="plane")
    quiet.comment("note")
    assert quiet.lines == ["G17"]


def test_write_program_square_layout() -> None:
    opts = KnifeOptions(
        corner_angle=math.radians(15), overcut=0.05, z_depth=-0.06, spindle_speed=1000, spindle_wait_on=1.5
    )
    text = write_program(plan_cuts([square()], opts), now=lambda: FIXED_NOW)
    lines = body(text)
    assert lines.count("M3 S1000") == 1
    assert lines.count("M5") == 1
    assert sum(line.startswith("G4 P1.500") for line in lines) == 1
    plunges = [line for line in lines if line.startswith("G1 Z-0.0600")]
    lifts = [line for line in lines if line.startswith("G0 Z1.0000")]
    assert len(plunges) == 4
    assert len(lifts) == 4 + 1  # initial safe height plus one per cut
    assert lines.index("M3 S1000") < lines.index(plunges[0])
    assert lines.index("M5") > lines.index(lifts[-1])
    # The first rapid goes to the start of the lead-in (0.05 before the corner), heading up the right edge.
    first_rapid = next(line for line in lines if line.startswith("G0 X"))
    assert first_rapid == "G0 X2.0000 Y-0.0500 A90.0000"


def test_write_program_passes_and_cut_mode() -> None:
    opts = KnifeOptions(z_depth=-0.2, z_step=0.1, oscillation_mode="cut", overcut=0.0)
    text = write_program(plan_cuts([circle()], opts), now=lambda: FIXED_NOW)
    lines = body(text)
    assert lines.count("M3") == 2
    assert lines.count("M5") == 2
    assert [line for line in lines if line.startswith("G1 Z")] == ["G1 Z-0.1000", "G1 Z-0.2000"]
    assert sum(line.startswith("G3 ") for line in lines) == 8


def test_shortest_rotation_across_pi() -> None:
    opts = KnifeOptions(overcut=0.0, corner_angle=math.pi)
    left_up = Toolpath.from_geometry([Line(P(0, 0), P(-1, 0.05)), Line(P(-1, 0.05), P(-2, 0))])
    assert left_up is not None
    text = write_program(plan_cuts([left_up], opts), now=lambda: FIXED_NOW)
    a_words = [word for line in body(text) for word in line.split() if word.startswith("A")]
    values = [float(word[1:]) for word in a_words]
    assert values[0] == pytest.approx(177.1376, abs=1e-3)
    assert all(abs(b - a) < 180.0 for a, b in pairwise(values))
