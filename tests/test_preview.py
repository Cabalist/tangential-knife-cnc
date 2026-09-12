"""Preview SVG rendering."""

import math
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

import geom2d
import pytest
from geom2d import Line, P
from svgelements import svgelements as se

from tcnc.errors import OutputError
from tcnc.options import KnifeOptions
from tcnc.plan import plan_toolpaths
from tcnc.preview import MAX_TICKS, preview_svg, write_preview
from tcnc.toolpath import Hints, Segment
from tests.test_corners import circle, square

if TYPE_CHECKING:
    from pathlib import Path


def test_preview_parses_and_has_one_element_per_feature(tmp_path: Path) -> None:
    opts = KnifeOptions(corner_angle=math.radians(15), overcut=0.05, blade_width=0.1)
    plan = plan_toolpaths([square(), circle()], opts)
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
    # The circle at the origin extends the 4 mm page to 5 mm.
    assert 'width="5mm"' in text
    assert 'viewBox="-1 -1 5 5"' in text
    inside = preview_svg(plan_toolpaths([square()], KnifeOptions(overcut=0.0)), page=(4.0, 4.0))
    assert 'width="4mm" height="4mm" viewBox="0 0 4 4"' in inside


def test_empty_plan_renders() -> None:
    text = preview_svg(plan_toolpaths([], KnifeOptions()))
    assert text.startswith("<svg ")


def test_preview_geometry_stays_inside_the_viewbox() -> None:
    text = preview_svg(plan_toolpaths([circle()], KnifeOptions()), page=(4, 4))
    xml = ET.fromstring(text)
    _, ymin, _, height = map(float, xml.attrib["viewBox"].split())
    core = next(element for element in xml if element.tag.endswith("path"))
    _, drawn_ymin, _, drawn_ymax = se.Path(core.attrib["d"]).bbox()
    assert ymin <= drawn_ymin <= drawn_ymax <= ymin + height


def test_preview_heading_matches_shortest_rotation() -> None:
    segment = Segment(Line(P(0, 0), P(1, 0)), Hints(math.radians(170), math.radians(-170)))
    assert geom2d.angle_eq(segment.heading_at(0.25), math.radians(175), 1e-9)


def test_tick_count_is_bounded_however_small_the_blade(tmp_path: Path) -> None:
    plan = plan_toolpaths([square()], KnifeOptions(blade_width=1e-300))
    text = preview_svg(plan)
    ticks = [line for line in text.splitlines() if "<line" in line and "stroke-dasharray" not in line]
    assert 0 < len(ticks) <= MAX_TICKS + len(plan.cuts)
    out = tmp_path / "p.svg"
    write_preview(plan, out)
    assert out.read_text() == text
    with pytest.raises(OutputError):
        write_preview(plan, tmp_path)
