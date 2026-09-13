"""G-code writer: modal words, feeds, arcs, layout."""

import math
from datetime import UTC, datetime
from itertools import pairwise

import pytest
from geom2d import Arc, Line, P

from tcnc.errors import PlanError
from tcnc.gcode import GCodeWriter, write_program
from tcnc.options import KnifeOptions
from tcnc.plan import plan_toolpaths
from tcnc.toolpath import Segment, Toolpath
from tests.test_corners import circle, square

FIXED_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def writer(options: KnifeOptions | None = None) -> GCodeWriter:
    return GCodeWriter(options or KnifeOptions(), now=lambda: FIXED_NOW)


QUIET = KnifeOptions(gcode_comments=False)


def body(text: str) -> list[str]:
    """Command lines only: no blank lines, no comment lines, no inline comments."""
    stripped = (line.split("  ;", 1)[0].rstrip() for line in text.splitlines())
    return [line for line in stripped if line and not line.startswith(";")]


def test_header_and_footer_words() -> None:
    w = writer(KnifeOptions(blend_mode="blend", blend_tolerance=0.01))
    w.header()
    w.footer()
    lines = body(w.text())
    assert lines[0] == "%"
    assert any(line.startswith("G17") for line in lines)
    assert any(line.startswith("G21") for line in lines)
    assert any(line.startswith("G90") for line in lines)
    assert any(line.startswith("G40") for line in lines)
    assert not any(line.startswith("G49") for line in lines)  # tool length compensation is the controller's
    assert {"G94", "G91.1", "G97"}.issubset(lines)
    assert any(line.startswith("G64 P0.010") for line in lines)
    assert any(line.startswith("F250.000") for line in lines)
    assert lines[-2].startswith("M2")
    assert lines[-1] == "%"
    assert "Created 2026-09-12T12:00:00+00:00" in w.text()


def test_modal_suppression_and_feed_recording() -> None:
    w = writer(QUIET)
    w.feed(x=1.0, y=0.0)
    w.feed(x=1.0, y=0.0)  # nothing changes: no line
    w.feed(x=2.0)  # same feed: no F word
    w.feed(z=-0.1)  # z feed differs: F emitted
    assert w.lines == ["G1 X1.000 Y0.000 F250.000", "G1 X2.000", "G1 Z-0.100"]
    w2 = writer(KnifeOptions(gcode_comments=False, z_feed=5.0))
    w2.feed(x=1.0)
    w2.feed(z=-0.1)
    w2.feed(z=-0.1, feed=7.0)  # no motion: the changed feed must not be recorded
    w2.feed(x=2.0, feed=7.0)  # ... so it is written with the next real move
    assert w2.lines == ["G1 X1.000 F250.000", "G1 Z-0.100 F5.000", "G1 X2.000 F7.000"]


def test_angles_are_degrees_and_negative_zero_is_normalised() -> None:
    w = writer(QUIET)
    w.rapid(x=0.0, y=-0.00001, a=math.pi / 2)
    assert w.lines == ["G0 X0.000 Y0.000 A90.000"]


def test_arc_words_and_validation() -> None:
    w = writer(QUIET)
    w.rapid(x=1.0, y=0.0)
    w.arc(end=P(0, 1), center=P(0, 0), sweep=math.pi / 2)
    assert w.lines[-1] == "G3 X0.000 Y1.000 I-1.000 J0.000 F250.000"
    w.arc(end=P(1, 0), center=P(0, 0), sweep=-math.pi / 2)
    assert w.lines[-1] == "G2 X1.000 Y0.000 I0.000 J-1.000"
    with pytest.raises(PlanError, match="off the circle"):
        w.arc(end=P(5, 5), center=P(0, 0), sweep=-math.pi / 2)
    fresh = writer()
    with pytest.raises(PlanError, match="start position"):
        fresh.arc(end=P(1, 0), center=P(0, 0), sweep=-math.pi / 2)


def test_quantized_partial_arc_does_not_become_a_full_circle() -> None:
    w = writer(QUIET)
    w.rapid(x=1, y=0)
    w.arc(end=P(math.cos(2e-5), math.sin(2e-5)), center=P(0, 0), sweep=2e-5, a=2e-5)
    assert w.lines[-1] == "G1 A0.001 F60.000"  # only A moves, so the A feed applies
    tiny = writer(QUIET)
    tiny.rapid(x=0, y=0)
    tiny.arc(end=P(0.00001, 0.00001), center=P(0.00001, 0), sweep=-math.pi / 2)
    assert tiny.lines == ["G0 X0.000 Y0.000"]  # nothing representable moved, nothing written


def test_rounded_endpoints_on_one_ray_do_not_become_a_full_turn() -> None:
    # Distinct rounded endpoints on the same ray from the rounded centre: LinuxCNC would take a full turn.
    radius, sweep = 1.00050001, 2e-4
    w = writer(QUIET)
    w.rapid(x=radius, y=0.0)
    assert w.lines[-1] == "G0 X1.001 Y0.000"
    w.arc(end=P.from_polar(radius, sweep), center=P(0, 0), sweep=sweep, a=sweep)
    assert w.lines[-1] == "G1 X1.000 A0.011 F250.000"
    # The same arc through the whole pipeline, kept by a tolerance finer than its length.
    arc = Arc.from_sweep(P(radius, 0), P.from_polar(radius, sweep), radius, sweep)
    tp = Toolpath.from_geometry([arc], tolerance=1e-6)
    assert tp is not None
    text = write_program(
        plan_toolpaths([tp], KnifeOptions(gcode_comments=False, tolerance=1e-6)), now=lambda: FIXED_NOW
    )
    assert not [line for line in body(text) if line.startswith(("G2 ", "G3 "))]
    assert all(sweep <= math.pi / 2 + 1e-9 for sweep in written_sweeps(text))


def test_arc_end_rounding_onto_the_centre_is_a_straight_move() -> None:
    radius = 0.00051001
    w = writer(QUIET)
    w.rapid(x=P.from_polar(radius, 0.1).x, y=P.from_polar(radius, 0.1).y)
    assert w.lines[-1] == "G0 X0.001 Y0.000"
    w.arc(end=P.from_polar(radius, 0.4), center=P(0, 0), sweep=0.3, a=0.3)
    assert w.lines[-1] == "G1 X0.000 A17.189 F250.000"
    # An arc that leaves the resolution around its chord cannot be written when its ends collapse: an error.
    big = writer(KnifeOptions(gcode_comments=False, output_precision=0))
    big.rapid(x=1.0, y=0.0)
    with pytest.raises(PlanError, match="cannot be written"):
        big.arc(end=P.from_polar(1.0, math.radians(350)), center=P(0, 0), sweep=math.radians(350))


def test_written_sweeps_match_the_plan_for_real_arcs() -> None:
    opts = KnifeOptions(overcut=0.5, blade_offset=0.3, gcode_comments=False)
    text = write_program(plan_toolpaths([circle(), square()], opts), now=lambda: FIXED_NOW)
    sweeps = written_sweeps(text)
    assert sweeps
    assert all(sweep <= math.pi / 2 + 1e-6 for sweep in sweeps)


def written_sweeps(text: str) -> list[float]:
    """The unsigned sweep LinuxCNC would derive from every G2/G3 line, as ``Interp::find_turn`` does."""
    x = y = 0.0
    sweeps: list[float] = []
    for line in body(text):
        if not line.startswith(("G0 ", "G1 ", "G2 ", "G3 ")):
            continue
        words = {word[0]: float(word[1:]) for word in line.split()[1:]}
        nx, ny = words.get("X", x), words.get("Y", y)
        if line.startswith(("G2 ", "G3 ")):
            cx, cy = x + words.get("I", 0.0), y + words.get("J", 0.0)
            start_angle = math.atan2(y - cy, x - cx)
            end_angle = math.atan2(ny - cy, nx - cx)
            sweep = (end_angle - start_angle) % math.tau
            if line.startswith("G2 "):
                sweep = (-sweep) % math.tau
            sweeps.append(math.tau if sweep == 0.0 else sweep)
        x, y = nx, ny
    return sweeps


def test_feed_is_chosen_from_the_axes_that_move_after_rounding() -> None:
    for end in (P(0, 0), P(1e-5, 1e-5)):
        w = writer(QUIET)
        w.rapid(x=0, y=0, a=0)
        w.feed(x=end.x, y=end.y, a=math.pi / 2)
        assert w.lines[-1] == "G1 A90.000 F60.000", end
    w = writer(QUIET)
    w.rapid(x=0, y=0, z=10, a=0)
    w.feed(x=1e-5, z=-1.0)
    assert w.lines[-1] == "G1 Z-1.000 F250.000"  # the plunge feed equals the XY feed by default
    w.feed(x=1e-5, y=0, z=-1.0, a=0, feed=42.0)
    assert w.lines[-1] == "G1 Z-1.000 F250.000"  # nothing moved: the explicit feed waits
    w.feed(x=2.0)
    assert w.lines[-1] == "G1 X2.000"


def test_rounded_radius_mismatch_is_rejected() -> None:
    w = writer(KnifeOptions(gcode_comments=False, output_precision=1))
    w.rapid(x=1.0, y=0.0)
    with pytest.raises(PlanError):
        w.arc(end=P(0.4, 1.0), center=P(0, 0), sweep=math.pi / 2)


def test_comments_cannot_emit_commands() -> None:
    w = writer()
    w.comment("shape\nG0 Z-10\n;")
    assert all(line.startswith(";") for line in w.text().splitlines())
    w.command("G17", comment="a\nG0 Z-10")
    assert w.lines[-1] == "G17  ; a | G0 Z-10"
    with pytest.raises(PlanError):
        w.command("G17\nG0 Z-10")


def test_line_numbers_and_comment_toggle() -> None:
    w = writer(KnifeOptions(gcode_line_numbers=True))
    w.command("G17", comment="plane")
    w.comment("note")
    assert w.lines == ["N1 G17  ; plane", "; note"]
    quiet = writer(QUIET)
    quiet.command("G17", comment="plane")
    quiet.comment("note")
    assert quiet.lines == ["G17"]


def test_write_program_square_layout() -> None:
    opts = KnifeOptions(
        corner_angle=math.radians(15), overcut=0.05, z_depth=-0.06, spindle_speed=1000, spindle_wait_on=1.5
    )
    text = write_program(plan_toolpaths([square()], opts), now=lambda: FIXED_NOW)
    lines = body(text)
    assert lines.count("M3 S1000") == 1
    assert lines.count("M5") == 1
    assert sum(line.startswith("G4 P1.500") for line in lines) == 1
    plunges = [line for line in lines if line.startswith("G1 Z-0.060")]
    lifts = [line for line in lines if line.startswith("G0 Z10.000")]
    assert len(plunges) == 4
    assert len(lifts) == 4 + 1  # initial safe height plus one per cut
    assert lines.index("M3 S1000") < lines.index(plunges[0])
    assert lines.index("M5") > lines.index(lifts[-1])
    # The first rapid goes to the start of the lead-in (0.05 before the corner), heading up the right edge.
    first_rapid = next(line for line in lines if line.startswith("G0 X"))
    assert first_rapid == "G0 X-0.050 Y0.000 A0.000"
    # The program ends raised, with the head off and the A axis unwound to zero for the next program.
    assert lines[-5:] == ["G0 Z10.000", "M5", "G0 A0.000", "M2", "%"]


def test_program_writes_no_unwind_when_the_a_axis_ends_at_zero() -> None:
    along_x = Toolpath.from_geometry([Line(P(0, 0), P(10, 0))])
    assert along_x is not None
    lines = body(write_program(plan_toolpaths([along_x], KnifeOptions(overcut=0.0)), now=lambda: FIXED_NOW))
    assert lines[-3:] == ["M5", "M2", "%"]
    assert [line for line in lines if " A" in line] == ["G0 X0.000 Y0.000 A0.000"]  # A0 throughout


def test_write_program_passes_and_cut_mode() -> None:
    opts = KnifeOptions(z_depth=-0.2, z_step=0.1, oscillation_mode="cut", overcut=0.0)
    text = write_program(plan_toolpaths([circle()], opts), now=lambda: FIXED_NOW)
    lines = body(text)
    assert lines.count("M3") == 2
    assert lines.count("M5") == 2
    assert [line for line in lines if line.startswith("G1 Z")] == ["G1 Z-0.100", "G1 Z-0.200"]
    assert sum(line.startswith("G3 ") for line in lines) == 8


def test_arcs_start_on_their_circle_after_a_tolerated_gap() -> None:
    # The line ends 4 µm short of the arc's start; the toolpath allows that at a 10 µm tolerance.
    line = Segment(Line(P(0, 0), P(0.996, 0)))
    arc = Segment(Arc.from_sweep(P(1, 0), P(0, 1), 1.0, math.pi / 2))
    tp = Toolpath((line, arc), tolerance=0.01)
    opts = KnifeOptions(overcut=0.0, corner_angle=math.pi, gcode_comments=False, tolerance=0.01)
    text = write_program(plan_toolpaths([tp], opts), now=lambda: FIXED_NOW)
    lines = body(text)
    g3 = next(index for index, line in enumerate(lines) if line.startswith("G3 "))
    assert lines[g3 - 2] == "G1 A90.000 F60.000"  # rotate in place at the joint
    assert lines[g3 - 1] == "G1 X1.000 F250.000"  # then bridge to the arc's own start
    assert lines[g3] == "G3 X0.000 Y1.000 A180.000 I-1.000 J0.000"


def test_shortest_rotation_across_pi() -> None:
    opts = KnifeOptions(overcut=0.0, corner_angle=math.pi)
    left_up = Toolpath.from_geometry([Line(P(0, 0), P(-1, 0.05)), Line(P(-1, 0.05), P(-2, 0))])
    assert left_up is not None
    text = write_program(plan_toolpaths([left_up], opts), now=lambda: FIXED_NOW)
    lines = body(text)
    assert lines[-3] == "G0 A0.000"  # the final unwind is the one rotation allowed to exceed a half turn
    a_words = [word for line in lines[:-3] for word in line.split() if word.startswith("A")]
    values = [float(word[1:]) for word in a_words]
    assert values[0] == pytest.approx(177.1376, abs=1e-3)
    assert all(abs(b - a) < 180.0 for a, b in pairwise(values))
