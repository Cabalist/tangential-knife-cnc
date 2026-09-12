"""Cut planning: corner splitting, lead-in and overcut."""

import copy
import math
import pickle

import geom2d
import pytest
from geom2d import Arc, Line, P

from tcnc.corners import Cut, OperationPlan, cuts_for_toolpath, entry_indices, plan_cuts
from tcnc.errors import PlanError
from tcnc.options import KnifeOptions
from tcnc.toolpath import Hints, Segment, Toolpath
from tests.test_toolpath import rounded_square


def square() -> Toolpath:
    tp = Toolpath.from_geometry(
        [Line(P(0, 0), P(2, 0)), Line(P(2, 0), P(2, 2)), Line(P(2, 2), P(0, 2)), Line(P(0, 2), P(0, 0))]
    )
    assert tp is not None
    return tp


def circle() -> Toolpath:
    arcs = [
        Arc.from_sweep(P(1, 0), P(0, 1), 1.0, math.pi / 2),
        Arc.from_sweep(P(0, 1), P(-1, 0), 1.0, math.pi / 2),
        Arc.from_sweep(P(-1, 0), P(0, -1), 1.0, math.pi / 2),
        Arc.from_sweep(P(0, -1), P(1, 0), 1.0, math.pi / 2),
    ]
    tp = Toolpath.from_geometry(arcs)
    assert tp is not None
    return tp


def zigzag() -> Toolpath:
    tp = Toolpath.from_geometry([Line(P(0, 0), P(1, 1)), Line(P(1, 1), P(2, 0)), Line(P(2, 0), P(3, 1))])
    assert tp is not None
    return tp


def test_square_splits_into_four_extended_cuts_from_its_own_start() -> None:
    cuts = cuts_for_toolpath(square(), corner_angle=math.radians(15), overcut=0.05)
    assert len(cuts) == 4
    for cut in cuts:
        assert cut.lead_in is not None
        assert cut.overcut is not None
        assert len(cut.core) == 1
        assert not cut.loop
        edge = cut.core[0]
        assert cut.lead_in.p2 == edge.p1
        assert cut.lead_in.length == pytest.approx(0.05)
        assert cut.overcut.p1 == edge.p2
        assert cut.overcut.length == pytest.approx(0.05)
        assert cut.start_heading == pytest.approx(edge.start_heading)
        assert cut.length == pytest.approx(2.1)
        assert len(cut.segments) == 3
    # The closure joint is itself a corner, so the path keeps its own start.
    assert cuts[0].core[0].p1 == P(0, 0)
    assert entry_indices(square(), math.radians(15)) == [0, 1, 2, 3]


def test_closed_path_rotates_only_when_its_start_is_not_a_corner() -> None:
    # A square whose first vertex is smoothed away: only three corners remain.
    tp = Toolpath.from_geometry(
        [
            Line(P(0.5, 0), P(2, 0)),
            Line(P(2, 0), P(2, 2)),
            Line(P(2, 2), P(0, 2)),
            Line(P(0, 2), P(0, 0.5)),
            Arc.from_sweep(P(0, 0.5), P(0.5, 0), 0.5, math.pi / 2),
        ]
    )
    assert tp is not None
    assert entry_indices(tp, math.radians(15)) == [1, 2, 3]
    cuts = cuts_for_toolpath(tp, corner_angle=math.radians(15), overcut=0.0)
    assert len(cuts) == 3
    assert cuts[0].start == P(2, 0)
    assert cuts[-1].core[-1].p2 == P(2, 0)


def test_smooth_closed_path_is_one_loop_with_overrun() -> None:
    (cut,) = cuts_for_toolpath(circle(), corner_angle=math.radians(15), overcut=0.3)
    assert cut.loop
    assert cut.lead_in is None
    assert cut.overcut is not None
    assert cut.overcut.p1 == P(1, 0)
    assert cut.overcut.length == pytest.approx(0.3)
    assert len(cut.core) == 4
    assert cut.end.almost_equal(cut.overcut.p2)


def test_rounded_square_is_one_loop() -> None:
    tp = Toolpath.from_geometry(rounded_square())
    assert tp is not None
    cuts = cuts_for_toolpath(tp, corner_angle=math.radians(15), overcut=0.1)
    assert len(cuts) == 1
    assert cuts[0].loop


def test_loop_overrun_is_clamped_to_first_segment() -> None:
    (cut,) = cuts_for_toolpath(circle(), corner_angle=math.radians(15), overcut=100.0)
    assert cut.overcut is not None
    assert cut.overcut.geom == cut.core[0].geom


def test_open_path_split_and_threshold() -> None:
    cuts = cuts_for_toolpath(zigzag(), corner_angle=math.radians(15), overcut=0.0)
    assert len(cuts) == 3
    assert all(cut.lead_in is None and cut.overcut is None for cut in cuts)
    assert cuts[0].segments == (zigzag()[0],)
    # 90 degree turns are below a 100 degree threshold: one run, both ends extended.
    (cut,) = cuts_for_toolpath(zigzag(), corner_angle=math.radians(100), overcut=0.2)
    assert len(cut.core) == 3
    assert cut.start.almost_equal(P(0, 0) - P(1, 1).unit * 0.2)
    assert cut.end.almost_equal(P(3, 1) + P(1, 1).unit * 0.2)
    # Exactly at the threshold is not sharp.
    (cut,) = cuts_for_toolpath(zigzag(), corner_angle=math.pi / 2, overcut=0.0)
    assert len(cut.core) == 3


def test_extensions_follow_the_blade_heading_not_the_axis_tangent() -> None:
    hinted = Segment(Line(P(0, 0), P(2, 0)), Hints(start_heading=math.pi / 4, end_heading=math.pi / 4))
    tp = Toolpath((hinted,))
    (cut,) = cuts_for_toolpath(tp, corner_angle=math.radians(15), overcut=1.0)
    assert cut.lead_in is not None
    assert cut.overcut is not None
    assert geom2d.angle_eq(cut.lead_in.start_tangent_angle, math.pi / 4)
    assert cut.lead_in.start_heading == pytest.approx(math.pi / 4)
    assert cut.overcut.p2.almost_equal(P(2, 0) + P.from_polar(1.0, math.pi / 4))


def test_sharp_connectors_are_lift_boundaries() -> None:
    connector = Segment(Line(P(2, 0), P(2, 0.1)), Hints(0.0, math.pi / 2, turn=math.pi / 2, joint_turn=math.pi / 2))
    tp = Toolpath(
        (Segment(Line(P(0, 0), P(2, 0))), connector, Segment(Line(P(2, 0.1), P(2, 2)), Hints(math.pi / 2, math.pi / 2)))
    )
    cuts = cuts_for_toolpath(tp, corner_angle=math.radians(15), overcut=0.0)
    assert len(cuts) == 2
    assert all(not any(s.is_connector for s in cut.core) for cut in cuts)
    (single,) = cuts_for_toolpath(tp, corner_angle=math.radians(100), overcut=0.0)
    assert len(single.core) == 3


def test_plan_cuts_collects_and_bounds() -> None:
    opts = KnifeOptions(corner_angle=math.radians(15), overcut=0.05)
    plan = plan_cuts([square(), circle()], opts)
    assert isinstance(plan, OperationPlan)
    assert len(plan.cuts) == 5
    assert plan.settings == opts.settings
    box = plan.bounding_box
    assert box is not None
    assert box.xmin == pytest.approx(-1.0)
    assert box.xmax == pytest.approx(2.05)
    assert plan.cut_length == pytest.approx(4 * 2.1 + 2 * math.pi + 0.05)
    assert plan_cuts([], opts).bounding_box is None


def test_cut_invariants_and_round_trip() -> None:
    with pytest.raises(PlanError):
        Cut(())
    edge = Segment(Line(P(0, 0), P(1, 0)))
    with pytest.raises(PlanError, match="lead-in"):
        Cut((edge,), lead_in=Segment(Line(P(5, 5), P(6, 6))))
    with pytest.raises(PlanError, match="overcut"):
        Cut((edge,), overcut=Segment(Line(P(0, 0), P(0.5, 0))))
    with pytest.raises(PlanError, match="do not connect"):
        Cut((edge, Segment(Line(P(10, 0), P(11, 0)))))
    with pytest.raises(PlanError, match="closed"):
        Cut((edge,), loop=True)
    with pytest.raises(PlanError, match="split to 90"):
        Cut((Segment(Arc.from_sweep(P(1, 0), P(0, -1), 1.0, 3 * math.pi / 2)),))
    with pytest.raises(PlanError, match="split to 90"):
        Cut((edge,), overcut=Segment(Arc.from_sweep(P(1, 0), P(-1, 0), 1.0, math.pi)))
    gapped = (edge, Segment(Line(P(1, 0.004), P(2, 0))))
    assert Cut(gapped, tolerance=0.01).tolerance == 0.01
    with pytest.raises(PlanError):
        Cut(gapped)
    loop = circle()
    overrun = loop[0]
    assert Cut(loop.segments, overcut=overrun, loop=True).overcut is overrun  # a loop's overrun restarts the loop
    cut = cuts_for_toolpath(square(), corner_angle=math.radians(15), overcut=0.05)[0]
    assert pickle.loads(pickle.dumps(cut)) == cut
    assert copy.deepcopy(cut) == cut
    plan = plan_cuts([square()], KnifeOptions(overcut=0.05))
    assert pickle.loads(pickle.dumps(plan)) == plan
    assert hash(plan) == hash(copy.deepcopy(plan))
