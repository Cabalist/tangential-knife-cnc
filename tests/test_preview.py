"""Preview SVG rendering."""

import math

from svgelements import svgelements as se

from tcnc.corners import plan_cuts
from tcnc.options import KnifeOptions
from tcnc.preview import preview_svg
from tests.test_corners import circle, square


def test_preview_parses_and_has_one_element_per_feature(tmp_path) -> None:
    opts = KnifeOptions(corner_angle=math.radians(15), overcut=0.05, blade_width=0.1)
    plan = plan_cuts([square(), circle()], opts)
    text = preview_svg(plan, page=(4.0, 4.0))
    path = tmp_path / "p.svg"
    path.write_text(text)
    svg = se.SVG.parse(str(path))
    elements = list(svg.elements())
    paths = [e for e in elements if isinstance(e, se.Path)]
    lines = [e for e in elements if isinstance(e, se.SimpleLine)]
    circles = [e for e in elements if isinstance(e, se.Circle)]
    # 4 runs x (lead-in, core, overcut) + loop x (core, overcut)
    assert len(paths) == 4 * 3 + 2
    assert len(circles) == 5  # one lift marker per cut
    rapids = [e for e in lines if e.values.get("stroke-dasharray")]
    assert len(rapids) == 4
    ticks = [e for e in lines if not e.values.get("stroke-dasharray")]
    assert ticks
    # The circle at the origin extends the 4 in page to 5 in.
    assert 'width="5in"' in text
    assert 'viewBox="-1 -1 5 5"' in text
    inside = preview_svg(plan_cuts([square()], KnifeOptions(overcut=0.0)), page=(4.0, 4.0))
    assert 'width="4in" height="4in" viewBox="0 0 4 4"' in inside


def test_empty_plan_renders() -> None:
    text = preview_svg(plan_cuts([], KnifeOptions()))
    assert text.startswith("<svg ")
