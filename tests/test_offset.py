"""Blade trail-offset compensation."""

import math

import pytest
from geom2d import Arc, Line, P

from tcnc.offset import offset_toolpath
from tcnc.toolpath import Toolpath
from tests.test_corners import square, zigzag


def test_zero_offset_returns_same_object() -> None:
    tp = square()
    assert offset_toolpath(tp, 0.0, min_arc_chord=0.01) is tp


def test_lines_are_shifted_forward_and_corners_get_arcs() -> None:
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
    connector = arcs[0]
    assert isinstance(connector.geom, Arc)
    assert connector.geom.center.almost_equal(P(2, 0))
    assert connector.geom.radius == pytest.approx(0.1)
    assert connector.geom.angle == pytest.approx(math.pi / 2)
    assert connector.start_heading == pytest.approx(0.0)
    assert connector.end_heading == pytest.approx(math.pi / 2)
    assert connector.p1.almost_equal(first.p2)
    assert connector.p2.almost_equal(lines[1].p1)


def test_tiny_gaps_use_lines() -> None:
    off = offset_toolpath(zigzag(), 0.001, min_arc_chord=0.01)
    connectors = [s for s in off if s.length < 0.01]
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


def test_g1_joint_gets_line_connector() -> None:
    tp = Toolpath.from_geometry([Line(P(0, 0), P(1, 0)), Arc.from_sweep(P(1, 0), P(2, 1), 1.0, math.pi / 2)])
    assert tp is not None
    off = offset_toolpath(tp, 0.2, min_arc_chord=0.0)
    kinds = [s.is_arc for s in off]
    assert kinds in ([False, False, True], [False, True])
