"""Toolpath model: conversion, hints, reversal, protocol conformance."""

import copy
import math
import pickle
from itertools import pairwise

import geom2d
import pytest
from geom2d import Arc, CubicBezier, Line, P

from tcnc.errors import PlanError
from tcnc.toolpath import Hints, Segment, Toolpath, heading_change, segments_are_g1


def quarter_arc(p1: tuple[float, float], p2: tuple[float, float], *, ccw: bool = True) -> Arc:
    return Arc.from_sweep(p1, p2, 1.0, math.pi / 2 if ccw else -math.pi / 2)


def rounded_square() -> list[Line | Arc]:
    """A CCW rounded rectangle whose top edge runs leftward (the ±π joint)."""
    return [
        Line(P(1, 0), P(3, 0)),
        quarter_arc((3, 0), (4, 1)),
        Line(P(4, 1), P(4, 3)),
        quarter_arc((4, 3), (3, 4)),
        Line(P(3, 4), P(1, 4)),
        quarter_arc((1, 4), (0, 3)),
        Line(P(0, 3), P(0, 1)),
        quarter_arc((0, 1), (1, 0)),
    ]


def test_segment_delegates_and_satisfies_protocol() -> None:
    seg = Segment(Line(P(0, 0), P(2, 0)))
    assert seg.p1 == P(0, 0)
    assert seg.length == 2.0
    assert seg.start_heading == 0.0
    assert seg.point_at(0.5) == P(1, 0)
    path = [seg, Segment(Line(P(2, 0), P(2, 2)))]
    assert geom2d.path_length(path) == pytest.approx(4.0)
    assert not geom2d.path_is_closed(path)


def test_hints_override_tangents_and_reverse_correctly() -> None:
    seg = Segment(Line(P(0, 0), P(1, 0)), Hints(start_heading=0.1, end_heading=0.2))
    assert seg.start_heading == 0.1
    rev = seg.reversed()
    assert rev.p1 == P(1, 0)
    assert rev.start_heading == pytest.approx(geom2d.normalize_angle(0.2 + math.pi, center=0.0))
    assert rev.end_heading == pytest.approx(geom2d.normalize_angle(0.1 + math.pi, center=0.0))
    back = rev.reversed()
    assert back.geom == seg.geom
    assert back.hints.start_heading == pytest.approx(0.1)
    assert back.hints.end_heading == pytest.approx(0.2)


def test_with_hints_and_with_geom() -> None:
    seg = Segment(Line(P(0, 0), P(1, 0)))
    hinted = seg.with_hints(end_heading=0.5)
    assert hinted.hints == Hints(end_heading=0.5)
    moved = hinted.with_geom(Line(P(0, 1), P(1, 1)))
    assert moved.hints == hinted.hints
    assert moved.p1 == P(0, 1)


def test_from_geometry_converts_and_filters() -> None:
    curve = CubicBezier(P(0, 0), P(1, 2), P(3, 2), P(4, 0))
    big_arc = Arc.from_sweep(
        P(1, 0), P(math.cos(math.radians(359)), math.sin(math.radians(359))), 1.0, math.radians(359)
    )
    path = [Line(P(5, 5), P(5, 5)), curve, big_arc]
    tp = Toolpath.from_geometry(path, source_id="p1")
    assert tp is not None
    assert tp.source_id == "p1"
    arcs = [s for s in tp if s.is_arc]
    assert all(abs(s.geom.angle) <= math.pi / 2 + 1e-9 for s in arcs if isinstance(s.geom, Arc))
    big_pieces = [
        s
        for s in tp
        if isinstance(s.geom, Arc) and abs(s.geom.radius - 1.0) < 1e-9 and s.geom.center.almost_equal(P(0, 0))
    ]
    assert len(big_pieces) == 4
    assert all(not s.is_degenerate for s in tp)


def test_from_geometry_empty_returns_none() -> None:
    assert Toolpath.from_geometry([Line(P(1, 1), P(1, 1))]) is None
    assert Toolpath.from_geometry([]) is None


def test_closed_detection_and_rotation() -> None:
    tp = Toolpath.from_geometry(rounded_square())
    assert tp is not None
    assert tp.closed
    assert len(tp) == 8
    rotated = tp.rotated_to(2)
    assert rotated.start == P(4, 1)
    assert rotated.closed
    open_tp = Toolpath.from_geometry([Line(P(0, 0), P(1, 0))])
    assert open_tp is not None
    assert not open_tp.closed
    with pytest.raises(PlanError):
        open_tp.rotated_to(1)


def test_pi_joints_are_g1_and_heading_change_is_zero() -> None:
    tp = Toolpath.from_geometry(rounded_square())
    assert tp is not None
    segs = [*tp, tp[0]]
    for a, b in pairwise(segs):
        assert segments_are_g1(a, b), (a, b)
        assert heading_change(a, b) == pytest.approx(0.0, abs=1e-9)


def test_sharp_corner_heading_change_sign() -> None:
    right = Segment(Line(P(0, 0), P(1, 0)))
    up = Segment(Line(P(1, 0), P(1, 1)))
    down = Segment(Line(P(1, 0), P(1, -1)))
    assert heading_change(right, up) == pytest.approx(math.pi / 2)
    assert heading_change(right, down) == pytest.approx(-math.pi / 2)
    assert not segments_are_g1(right, up)


def test_reversed_toolpath_round_trips() -> None:
    tp = Toolpath.from_geometry(rounded_square())
    assert tp is not None
    rev = tp.reversed()
    assert rev.start == tp.end
    assert rev.reversed() == tp
    assert rev.length == pytest.approx(tp.length)


def test_toolpath_requires_segments_and_is_picklable() -> None:
    with pytest.raises(PlanError):
        Toolpath(())
    tp = Toolpath.from_geometry(rounded_square())
    assert tp is not None
    assert pickle.loads(pickle.dumps(tp)) == tp
    assert copy.deepcopy(tp) == tp
    assert hash(tp) == hash(copy.deepcopy(tp))
