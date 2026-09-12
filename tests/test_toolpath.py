"""Toolpath model: conversion, hints, reversal, invariants, protocol conformance."""

import copy
import math
import pickle
from itertools import pairwise

import geom2d
import pytest
from geom2d import Arc, CubicBezier, Line, P

from tcnc.corners import cuts_for_toolpath
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


def test_heading_at_takes_the_shortest_rotation_across_pi() -> None:
    seg = Segment(Line(P(0, 0), P(1, 0)), Hints(math.radians(170), math.radians(-170)))
    assert geom2d.angle_eq(seg.heading_at(0.25), math.radians(175), 1e-9)
    assert seg.rotation == pytest.approx(math.radians(20))
    unhinted = Segment(Arc.from_sweep(P(1, 0), P(0, 1), 1.0, math.pi / 2))
    assert unhinted.heading_at(0.5) == pytest.approx(math.radians(135))


def test_subdivide_interpolates_hints_and_turn() -> None:
    seg = Segment(Line(P(0, 0), P(2, 0)), Hints(start_heading=0.0, end_heading=1.0, turn=1.0))
    first, second = seg.subdivide(0.25)
    assert first.hints == Hints(0.0, 0.25, 0.25)
    assert second.hints == Hints(0.25, 1.0, 0.75)
    assert first.p2 == P(0.5, 0)
    plain_first, plain_second = Segment(Line(P(0, 0), P(2, 0))).subdivide(0.5)
    assert plain_first.hints.is_empty
    assert plain_second.hints.is_empty


def test_hints_are_validated() -> None:
    with pytest.raises(PlanError, match="finite"):
        Hints(start_heading=math.nan)
    with pytest.raises(PlanError, match="finite"):
        Hints(joint_turn=math.inf)
    with pytest.raises(PlanError, match="does not lead"):
        Hints(0.1, 0.2, turn=0.3)
    assert Hints(0.1, 0.4, turn=0.3).turn == 0.3
    assert Hints(math.radians(170), math.radians(-170), turn=math.radians(20)).turn == pytest.approx(math.radians(20))
    assert Hints(turn=1.0).is_empty is False
    assert Hints(joint_turn=1.0).reversed() == Hints(joint_turn=-1.0)


def test_subdivide_keeps_the_source_joint_whole() -> None:
    seg = Segment(Line(P(0, 0), P(2, 0)), Hints(0.0, 1.0, turn=1.0, joint_turn=2.5))
    first, second = seg.subdivide(0.5)
    assert first.hints.joint_turn == 2.5
    assert second.hints.joint_turn == 2.5
    assert first.hints.turn == 0.5
    assert first.is_connector
    assert seg.reversed().hints.joint_turn == -2.5


def test_with_hints_and_with_geom() -> None:
    seg = Segment(Line(P(0, 0), P(1, 0)))
    hinted = seg.with_hints(Hints(end_heading=0.5))
    assert hinted.hints == Hints(end_heading=0.5)
    cleared = hinted.with_hints(Hints())
    assert cleared.hints.is_empty
    moved = hinted.with_geom(Line(P(0, 1), P(1, 1)))
    assert moved.hints == hinted.hints
    assert moved.p1 == P(0, 1)


def test_from_geometry_converts_and_filters() -> None:
    curve = CubicBezier(P(0, 0), P(1, 2), P(3, 2), P(4, 0))
    tp = Toolpath.from_geometry([curve, Line(P(4, 0), P(4, 0)), Line(P(4, 0), P(5, 0))], source_id="p1")
    assert tp is not None
    assert tp.source_id == "p1"
    assert all(abs(s.geom.angle) <= math.pi / 2 + 1e-9 for s in tp if isinstance(s.geom, Arc))
    assert all(not s.is_degenerate for s in tp)
    assert tp.end == P(5, 0)


def test_from_geometry_splits_big_arcs_equally() -> None:
    big_arc = Arc.from_sweep(
        P(1, 0), P(math.cos(math.radians(359)), math.sin(math.radians(359))), 1.0, math.radians(359)
    )
    tp = Toolpath.from_geometry([big_arc])
    assert tp is not None
    assert len(tp) == 4
    assert all(isinstance(s.geom, Arc) and abs(s.geom.angle) == pytest.approx(math.radians(359) / 4) for s in tp)


def test_from_geometry_empty_returns_none_and_respects_tolerance() -> None:
    assert Toolpath.from_geometry([Line(P(1, 1), P(1, 1))]) is None
    assert Toolpath.from_geometry([]) is None
    tiny = Line(P(0, 0), P(5e-8, 0))
    kept = Toolpath.from_geometry([tiny])
    assert kept is not None
    assert kept.tolerance is None  # geom2d's EPSILON
    assert Toolpath.from_geometry([tiny], tolerance=1e-7) is None


def test_from_geometry_merges_runs_shorter_than_the_tolerance() -> None:
    steps = [Line(P(1 + 0.004 * i, 0), P(1 + 0.004 * (i + 1), 0)) for i in range(3)]
    path = [Line(P(0, 0), P(1, 0)), *steps, Line(P(1.012, 0), P(2, 0))]
    tp = Toolpath.from_geometry(path, tolerance=0.01)
    assert tp is not None
    assert tp.tolerance == 0.01
    assert [s.geom for s in tp] == [Line(P(0, 0), P(1, 0)), Line(P(1, 0), P(1.012, 0)), Line(P(1.012, 0), P(2, 0))]
    # A run whose chord is itself below the tolerance disappears; the gap it leaves is within tolerance.
    short = Toolpath.from_geometry([Line(P(0, 0), P(1, 0)), steps[0], Line(P(1.004, 0), P(2, 0))], tolerance=0.01)
    assert short is not None
    assert len(short) == 2
    trailing = Toolpath.from_geometry([Line(P(0, 0), P(1, 0)), steps[0]], tolerance=0.01)
    assert trailing is not None
    assert trailing.end == P(1, 0)
    # Without a job tolerance nothing above EPSILON is touched.
    assert len(Toolpath.from_geometry(path) or ()) == 5


def dense_arc(radius: float, sweep: float, count: int, *, center: P | None = None) -> list[Line]:
    """A circular arc sampled as ``count`` straight edges."""
    origin = P(0, 0) if center is None else center
    points = [origin + P.from_polar(radius, sweep * i / count) for i in range(count + 1)]
    return [Line(a, b) for a, b in pairwise(points)]


def deviation(tp: Toolpath, points: list[P]) -> float:
    """The largest distance from any of ``points`` to the toolpath."""
    return max(min(s.geom.distance_to_point(p, segment=True) for s in tp) for p in points)


def test_dense_circle_survives_simplification_as_one_smooth_loop() -> None:
    edges = dense_arc(1.0, math.tau, 1000)
    assert max(e.length for e in edges) < 0.01
    tp = Toolpath.from_geometry(edges, tolerance=0.01)
    assert tp is not None
    assert tp.closed
    assert tp.length == pytest.approx(math.tau, rel=0.01)
    assert 10 < len(tp) < 60  # chords of about 0.28 mm, not 1000 edges and not one line
    assert deviation(tp, [e.p1 for e in edges]) <= 0.01
    # The blade follows the circle, so the chords carry no corners for the planner.
    for a, b in pairwise([*tp, tp[0]]):
        assert abs(heading_change(a, b)) < math.radians(1.0)
    assert all(geom2d.angle_eq(s.start_heading, (s.p1 - P(0, 0)).angle + math.pi / 2, 0.01) for s in tp)


def test_dense_open_curve_keeps_its_crown() -> None:
    edges = dense_arc(1.0, math.pi, 500)
    tp = Toolpath.from_geometry(edges, tolerance=0.01)
    assert tp is not None
    assert not tp.closed
    assert deviation(tp, [P(0, 1), P.from_polar(1.0, 0.3), P.from_polar(1.0, 2.5)]) <= 0.01
    assert tp.length == pytest.approx(math.pi, rel=0.01)


def test_simplification_keeps_real_corners_and_blobs_vanish() -> None:
    # Two dense straight runs meeting at a right angle: two chords and one 90° corner.
    up = [Line(P(0, 0.005 * i), P(0, 0.005 * (i + 1))) for i in range(200)]
    right = [Line(P(0.005 * i, 1), P(0.005 * (i + 1), 1)) for i in range(200)]
    tp = Toolpath.from_geometry([*up, *right], tolerance=0.01)
    assert tp is not None
    assert len(tp) == 2
    assert tp.start == P(0, 0)
    assert tp.end == P(1, 1)
    assert tp[0].p2.distance(P(0, 1)) <= 0.01  # the corner may be cut short by the resolution, no more
    assert heading_change(tp[0], tp[1]) == pytest.approx(-math.pi / 2, abs=math.radians(1))
    # A zigzag that never leaves a 10 µm neighbourhood is nothing; the path around it stays connected.
    blob = [Line(P(1 + 0.002 * i, 0.003 * (i % 2)), P(1 + 0.002 * (i + 1), 0.003 * ((i + 1) % 2))) for i in range(4)]
    tp = Toolpath.from_geometry([Line(P(0, 0), P(1, 0)), *blob, Line(P(1.008, 0), P(2, 0))], tolerance=0.01)
    assert tp is not None
    assert len(tp) == 2
    # A dense hairpin doubles back: the chords follow it out and back instead of cancelling.
    out = dense_arc(0.5, math.pi, 400, center=P(0, 0.5))  # from (0.5, 0.5) over (0, 1) to (-0.5, 0.5)
    tp = Toolpath.from_geometry(
        [Line(P(0.5, -0.5), P(0.5, 0.5)), *out, Line(P(-0.5, 0.5), P(-0.5, -0.5))], tolerance=0.01
    )
    assert tp is not None
    assert tp.length == pytest.approx(2 + math.pi * 0.5, rel=0.01)


@pytest.mark.parametrize(
    "xs",
    [
        [0.0, 0.009, 0.018, 0.009],
        [0.0, 0.009, 0.018, 0.009, 0.0, -0.009, -0.018, -0.027],
        [0.0, 0.009, 0.018, 0.027, 0.018, 0.009, 0.0],
    ],
)
def test_retraced_short_runs_keep_every_vertex_within_tolerance(xs: list[float]) -> None:
    points = [P(x, 0.0) for x in xs]
    tp = Toolpath.from_geometry([Line(a, b) for a, b in pairwise(points)], tolerance=0.01)
    assert tp is not None, "the run reaches beyond the resolution and cannot vanish"
    assert deviation(tp, points) <= 0.01


def test_disconnected_source_pieces_are_rejected_before_simplification() -> None:
    for gap in (0.1, 100.0):
        with pytest.raises(PlanError, match="do not connect"):
            Toolpath.from_geometry([Line(P(0, 0), P(0.005, 0)), Line(P(gap, 0), P(gap + 0.005, 0))], tolerance=0.01)


def test_simplifying_a_long_dense_straight_line_is_cheap() -> None:
    import time  # noqa: PLC0415 - timing only here

    edges = [Line(P(i * 0.005, 0), P((i + 1) * 0.005, 0)) for i in range(10_000)]
    started = time.perf_counter()
    tp = Toolpath.from_geometry(edges, tolerance=0.01)
    elapsed = time.perf_counter() - started
    assert tp is not None
    assert [s.geom for s in tp] == [Line(P(0, 0), P(50, 0))]
    assert elapsed < 2.0, elapsed
    # A slight wobble within the resolution is still one chord; a wobble beyond it is not.
    wobbly = [Line(P(i * 0.005, 0.004 * (i % 2)), P((i + 1) * 0.005, 0.004 * ((i + 1) % 2))) for i in range(2000)]
    tp = Toolpath.from_geometry(wobbly, tolerance=0.01)
    assert tp is not None
    assert len(tp) <= 3
    assert deviation(tp, [e.p1 for e in wobbly]) <= 0.01


def test_flat_cubic_keeps_resolvable_blade_headings() -> None:
    radius = 0.02
    kappa = 4 * (math.sqrt(2) - 1) / 3
    quarter = CubicBezier(P(radius, 0), P(radius, kappa * radius), P(kappa * radius, radius), P(0, radius))
    assert quarter.flatness <= 0.01  # within the resolution of its chord, yet it turns the blade by 90°
    tp = Toolpath.from_geometry([quarter], tolerance=0.01)
    assert tp is not None
    assert geom2d.angle_eq(tp[0].start_heading, math.pi / 2, math.radians(1))
    assert geom2d.angle_eq(tp[-1].end_heading, math.pi, math.radians(1))
    # A 10 mm curve whose 5 µm hooks at the ends turn 90° within the resolution: the hooks are corners.
    hooked = CubicBezier(P(0, 0), P(0, 0.005), P(10, 0.005), P(10, 0))
    up, down = Line(P(0, -5), P(0, 0)), Line(P(10, 0), P(10, -5))
    tp = Toolpath.from_geometry([up, hooked, down], tolerance=0.01)
    assert tp is not None
    assert deviation(tp, [hooked.point_at(i / 20) for i in range(21)]) <= 0.01
    assert len(cuts_for_toolpath(tp, corner_angle=math.radians(15), overcut=0.0)) == 3


def test_nearly_straight_curves_become_their_chord() -> None:
    for length, height in ((100.0, 1e-5), (10.0, 1e-7), (100.0, 0.005)):
        curve = CubicBezier(P(0, 0), P(length / 3, height), P(2 * length / 3, height), P(length, 0))
        tp = Toolpath.from_geometry([curve], tolerance=0.01, biarc_tolerance=0.01)
        assert tp is not None
        assert [s.geom for s in tp] == [Line(P(0, 0), P(length, 0))]
        assert (
            max(min(s.geom.distance_to_point(curve.point_at(i / 50), segment=True) for s in tp) for i in range(51))
            <= 0.01
        )
    bulging = CubicBezier(P(0, 0), P(33, 1), P(66, 1), P(100, 0))
    tp = Toolpath.from_geometry([bulging], tolerance=0.01)
    assert tp is not None
    assert any(s.is_arc for s in tp)


def test_arcs_straight_at_the_resolution_become_lines() -> None:
    # 25 mm of a 2e7 mm circle: 4e-6 mm off its chord; larger radii cannot even be built at geom2d's floor.
    huge = Arc.from_sweep(P(0, 0), P(25, 0), 2e7, 25 / 2e7)
    tp = Toolpath.from_geometry([huge], tolerance=0.01)
    assert tp is not None
    assert [s.geom for s in tp] == [Line(P(0, 0), P(25, 0))]
    # A short arc that turns a lot is kept even though it stays close to its chord: its headings matter.
    tight = Arc.from_sweep(P(0, 0), P(0.05, 0), 0.05, math.radians(60))
    tp = Toolpath.from_geometry([tight], tolerance=0.01)
    assert tp is not None
    assert tp[0].is_arc


def test_partial_hints_are_checked_against_the_geometry() -> None:
    line = Line(P(0, 0), P(1, 0))
    for hints in (Hints(turn=math.pi), Hints(start_heading=0.0, turn=math.pi), Hints(end_heading=0.0, turn=math.pi)):
        with pytest.raises(PlanError, match="does not lead"):
            Segment(line, hints)
    assert Segment(line, Hints(turn=0.0)).heading_at(1.0) == 0.0
    seg = Segment(line, Hints(end_heading=0.5, turn=0.5))
    assert seg.heading_at(1.0) == pytest.approx(seg.end_heading)


def test_from_geometry_reports_geom2d_failures_as_plan_errors() -> None:
    curve = CubicBezier(P(0, 0), P(1, 2), P(3, 2), P(4, 0))
    with pytest.raises(PlanError, match="p1"):
        Toolpath.from_geometry([curve], biarc_tolerance=1e-12, source_id="p1")


def test_invariants_are_enforced() -> None:
    with pytest.raises(PlanError):
        Toolpath(())
    with pytest.raises(PlanError):
        Toolpath.from_geometry([Line(P(0, 0), P(1, 0)), Line(P(10, 0), P(11, 0))])
    with pytest.raises(PlanError):
        Toolpath((Segment(Line(P(0, 0), P(1, 0))),), closed=True)
    with pytest.raises(PlanError):
        Toolpath((Segment(Arc.from_sweep(P(1, 0), P(-1, 0), 1.0, math.pi)),))
    with pytest.raises(PlanError):
        Toolpath((Segment(Line(P(0, 0), P(1, 0))),), tolerance=0.0)


def test_joints_are_judged_at_the_toolpath_tolerance() -> None:
    gapped = (Segment(Line(P(0, 0), P(1, 0))), Segment(Line(P(1, 0.005), P(2, 0))))
    tp = Toolpath(gapped, tolerance=0.01)
    assert tp.reversed().tolerance == 0.01
    assert tp.with_segments(gapped).tolerance == 0.01
    with pytest.raises(PlanError):
        Toolpath(gapped)
    with pytest.raises(PlanError):
        Toolpath(gapped, tolerance=0.001)
    loop = (Segment(Line(P(0, 0), P(1, 0))), Segment(Line(P(1, 0), P(0, 0.005))))
    assert Toolpath(loop, closed=True, tolerance=0.01).rotated_to(1).tolerance == 0.01
    with pytest.raises(PlanError):
        Toolpath(loop, closed=True)


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


@pytest.mark.parametrize(
    "value",
    [
        Hints(0.1, 0.4, 0.3, 1.0),
        Segment(Line(P(0, 0), P(1, 0)), Hints(0.1, 0.2)),
        Toolpath((Segment(Line(P(0, 0), P(1, 0))),)),
    ],
    ids=["hints", "segment", "toolpath"],
)
def test_records_round_trip(value: object) -> None:
    assert pickle.loads(pickle.dumps(value)) == value
    assert copy.deepcopy(value) == value
    assert hash(value) == hash(copy.deepcopy(value))
