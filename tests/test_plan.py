"""The job pipeline: ordering, compensation and corner planning together."""

import math

import geom2d
import pytest
from geom2d import Arc, Line, P

from tcnc.corners import plan_cuts
from tcnc.errors import PlanError
from tcnc.gcode import write_program
from tcnc.offset import offset_toolpath
from tcnc.options import KnifeOptions
from tcnc.ordering import order_toolpaths
from tcnc.plan import plan_toolpaths
from tcnc.toolpath import Toolpath
from tests.test_corners import circle, square


def test_offset_preserves_sharp_corner_lifts() -> None:
    options = KnifeOptions(blade_offset=0.1)
    shifted = offset_toolpath(square(), 0.1, min_arc_chord=0.0001)
    assert len(plan_cuts([shifted], options).cuts) == 4
    assert len(plan_toolpaths([square()], options).cuts) == 4


def test_offset_loop_overcut_preserves_blade_heading() -> None:
    options = KnifeOptions(blade_offset=0.3, overcut=0.3)
    shifted = offset_toolpath(circle(), 0.3, min_arc_chord=0.0001)
    cut = plan_cuts([shifted], options).cuts[0]
    assert cut.overcut is not None
    assert isinstance(shifted[0].geom, Arc)
    expected = shifted[0].start_heading + options.overcut / shifted[0].geom.radius
    assert geom2d.angle_eq(cut.overcut.end_heading, expected, 1e-9)


def test_offset_open_arc_extension_follows_blade_heading() -> None:
    path = Toolpath((circle()[0],))
    shifted = offset_toolpath(path, 0.3, min_arc_chord=0.0001)
    cut = plan_cuts([shifted], KnifeOptions(overcut=0.3)).cuts[0]
    assert cut.lead_in is not None
    assert geom2d.angle_eq(cut.lead_in.start_tangent_angle, cut.lead_in.start_heading, 1e-9)
    assert cut.lead_in.start_heading == pytest.approx(math.pi / 2)


@pytest.mark.parametrize(("turn", "threshold"), [(120, 100), (100, 75), (170, 120), (60, 100)])
def test_source_corner_survives_connector_subdivision(turn: float, threshold: float) -> None:
    vertex = P(1, 0)
    end = vertex + P.from_polar(1, math.radians(turn))
    path = Toolpath.from_geometry([Line(P(0, 0), vertex), Line(vertex, end)])
    assert path is not None
    raw = plan_toolpaths([path], KnifeOptions(corner_angle=math.radians(threshold)))
    offset = plan_toolpaths([path], KnifeOptions(corner_angle=math.radians(threshold), blade_offset=0.1))
    expected = 2 if turn > threshold else 1
    assert len(raw.cuts) == expected
    assert len(offset.cuts) == expected


def test_corner_split_keeps_nearest_entry() -> None:
    ordered = order_toolpaths([square()], "nearest", corner_angle=math.radians(15))
    assert plan_cuts(ordered, KnifeOptions()).cuts[0].start == P(0, 0)
    assert plan_toolpaths([square()], KnifeOptions(sort_method="nearest")).cuts[0].start == P(0, 0)


def test_discontinuous_toolpath_is_rejected() -> None:
    with pytest.raises(PlanError):
        Toolpath.from_geometry([Line(P(0, 0), P(1, 0)), Line(P(10, 0), P(11, 0))])


def test_plan_job_applies_every_option() -> None:
    far = Toolpath.from_geometry([Line(P(10, 10), P(11, 10))])
    near = Toolpath.from_geometry([Line(P(1, 0), P(0, 0))])
    assert far is not None
    assert near is not None
    plan = plan_toolpaths([far, near], KnifeOptions(sort_method="nearest", blade_offset=0.1, overcut=0.05))
    assert plan.cuts[0].core[0].p1.almost_equal(P(0.1, 0))  # nearest first, reversed, shifted
    assert plan.cuts[0].lead_in is not None
    text = write_program(plan, now=None)
    assert "M2" in text
