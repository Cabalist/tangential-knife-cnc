"""Blade trail-offset compensation."""

import math

import pytest
from geom2d import Arc, Line, P

from tcnc.offset import offset_toolpath
from tcnc.toolpath import Segment, Toolpath
from tests.test_corners import square, zigzag


def test_zero_offset_returns_same_object() -> None:
    tp = square()
    assert offset_toolpath(tp, 0.0, min_arc_chord=0.01) is tp


def test_lines_are_shifted_forward_and_corners_get_marked_arcs() -> None:
    off = offset_toolpath(square(), 0.1, min_arc_chord=0.01)
    assert off.closed
    lines = [s for s in off if not s.is_arc]
    arcs = [s for s in off if s.is_arc]
    assert len(lines) == 4
    assert len(arcs) == 4  # one connector per corner, including the closure
    first = lines[0]
    assert first.p1.almost_equal(P(0.1, 0))
    assert first.p2.almost_equal(P(2.1, 0))
    assert first.start_heading == pytest.approx(0.0)
    assert not first.is_connector
    connector = arcs[0]
    assert connector.is_connector
    assert connector.hints.turn == pytest.approx(math.pi / 2)
    assert isinstance(connector.geom, Arc)
    assert connector.geom.center.almost_equal(P(2, 0))
    assert connector.geom.radius == pytest.approx(0.1)
    assert connector.geom.angle == pytest.approx(math.pi / 2)
    assert connector.start_heading == pytest.approx(0.0)
    assert connector.end_heading == pytest.approx(math.pi / 2)
    assert connector.p1.almost_equal(first.p2)
    assert connector.p2.almost_equal(lines[1].p1)


def test_wide_connectors_are_split_to_quarter_turns() -> None:
    hairpin = Toolpath.from_geometry([Line(P(0, 0), P(2, 0)), Line(P(2, 0), P(0, 0.3))])
    assert hairpin is not None
    off = offset_toolpath(hairpin, 0.1, min_arc_chord=0.001)
    connectors = [s for s in off if s.is_connector]
    assert len(connectors) == 2
    turns = [s.hints.turn for s in connectors]
    total = hairpin[1].start_heading - hairpin[0].end_heading
    assert sum(t for t in turns if t is not None) == pytest.approx(total)
    assert all(s.hints.joint_turn == pytest.approx(total) for s in connectors)  # the source joint, kept whole
    assert all(abs(s.geom.angle) <= math.pi / 2 + 1e-9 for s in connectors if isinstance(s.geom, Arc))
    assert connectors[0].end_heading == pytest.approx(connectors[1].start_heading)


def test_tiny_gaps_use_lines() -> None:
    off = offset_toolpath(zigzag(), 0.001, min_arc_chord=0.01)
    connectors = [s for s in off if s.is_connector]
    assert connectors
    assert all(not s.is_arc for s in connectors)


def test_arc_offset_keeps_center_and_grows_radius() -> None:
    arc = Arc.from_sweep(P(1, 0), P(0, 1), 1.0, math.pi / 2)
    tp = Toolpath.from_geometry([arc])
    assert tp is not None
    off = offset_toolpath(tp, 0.3, min_arc_chord=0.01)
    (seg,) = off.segments
    assert isinstance(seg.geom, Arc)
    assert seg.geom.center.almost_equal(P(0, 0))
    assert seg.geom.radius == pytest.approx(math.hypot(1.0, 0.3))
    assert seg.geom.angle == pytest.approx(math.pi / 2)
    assert seg.start_heading == pytest.approx(math.pi / 2)


def test_joint_gaps_within_tolerance_pivot_about_the_vertex() -> None:
    tp = Toolpath((Segment(Line(P(0, 0), P(2, 0))), Segment(Line(P(2, 0.004), P(2, 2)))), tolerance=0.01)
    off = offset_toolpath(tp, 0.1, min_arc_chord=0.001)
    assert off.tolerance == 0.01
    (connector,) = [s for s in off if s.is_connector]
    assert isinstance(connector.geom, Arc)
    assert connector.geom.center == P(2, 0)
    assert connector.geom.radius == pytest.approx(0.1)
    assert connector.geom.angle == pytest.approx(math.pi / 2)
    assert connector.p2.almost_equal(P(2, 0.1))
    assert connector.p2.almost_equal(off[2].p1, 0.01)
    assert not connector.p2.almost_equal(off[2].p1)


def test_g1_joint_gets_line_connector() -> None:
    tp = Toolpath.from_geometry([Line(P(0, 0), P(1, 0)), Arc.from_sweep(P(1, 0), P(2, 1), 1.0, math.pi / 2)])
    assert tp is not None
    off = offset_toolpath(tp, 0.2, min_arc_chord=0.0)
    kinds = [s.is_arc for s in off]
    assert kinds in ([False, False, True], [False, True])
