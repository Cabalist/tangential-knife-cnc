"""Cut planning: corner splitting, lead-in and overcut."""

import math

import pytest
from geom2d import Arc, Line, P

from tcnc.corners import Cut, CutPlan, cuts_for_toolpath, plan_cuts
from tcnc.options import KnifeOptions
from tcnc.toolpath import Toolpath
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


def test_square_splits_into_four_extended_cuts() -> None:
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
    # The first cut starts after the first sharp joint, i.e. at the second edge.
    assert cuts[0].core[0].p1 == P(2, 0)


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


def test_plan_cuts_collects_and_bounds() -> None:
    opts = KnifeOptions(corner_angle=math.radians(15), overcut=0.05)
    plan = plan_cuts([square(), circle()], opts)
    assert isinstance(plan, CutPlan)
    assert len(plan.cuts) == 5
    assert plan.options is opts
    box = plan.bounding_box
    assert box is not None
    assert box.xmin == pytest.approx(-1.0)
    assert box.xmax == pytest.approx(2.05)
    assert plan.cut_length == pytest.approx(4 * 2.1 + 2 * math.pi + 0.05)
    assert plan_cuts([], opts).bounding_box is None


def test_cut_is_hashable_and_core_excludes_extensions() -> None:
    cuts = cuts_for_toolpath(square(), corner_angle=math.radians(15), overcut=0.05)
    assert len({hash(cut) for cut in cuts}) == 4
    assert all(isinstance(cut, Cut) for cut in cuts)
