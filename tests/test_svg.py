"""SVG loading through svgelements: units, flip, filters, conversion."""

import math
from typing import TYPE_CHECKING

import pytest
from geom2d import Arc, CubicBezier, Line, P

from tcnc.errors import SvgError
from tcnc.svg import PX_PER_MM, SvgDocument, load_svg

if TYPE_CHECKING:
    from pathlib import Path

INCH = 1.0 / 96.0
MM = 1.0 / PX_PER_MM


def load(fixture: Path, *, flip_y: bool = True, ids: tuple[str, ...] = (), layers: tuple[str, ...] = ()) -> SvgDocument:
    return load_svg(fixture, unit_scale=INCH, flip_y=flip_y, ids=ids, layers=layers)


def test_square_in_inches_is_flipped_and_closed(fixture) -> None:
    doc = load(fixture("square.svg"))
    assert doc.width == pytest.approx(4.0)
    assert doc.height == pytest.approx(4.0)
    (path,) = doc.paths
    assert path.source_id == "square"
    assert path.closed
    assert len(path.geometry) == 4
    assert all(isinstance(g, Line) for g in path.geometry)
    first = path.geometry[0]
    assert isinstance(first, Line)
    assert first.p1.almost_equal(P(1, 3))  # y flipped: 4 - 1
    assert first.p2.almost_equal(P(3, 3))


def test_no_flip_keeps_svg_frame(fixture) -> None:
    doc = load(fixture("square.svg"), flip_y=False)
    first = doc.paths[0].geometry[0]
    assert isinstance(first, Line)
    assert first.p1.almost_equal(P(1, 1))


def test_circle_becomes_four_arcs(fixture) -> None:
    (path,) = load(fixture("circle.svg")).paths
    arcs = [g for g in path.geometry if isinstance(g, Arc)]
    assert len(arcs) == 4
    assert len(path.geometry) == 4  # zero-length Close dropped
    for arc in arcs:
        assert arc.radius == pytest.approx(1.0)
        assert arc.center.almost_equal(P(2, 2))
        assert abs(arc.angle) == pytest.approx(math.pi / 2)


def test_rounded_rect_lines_and_arcs(fixture) -> None:
    (path,) = load(fixture("rounded-rect.svg")).paths
    assert sum(isinstance(g, Line) for g in path.geometry) == 4
    arcs = [g for g in path.geometry if isinstance(g, Arc)]
    assert len(arcs) == 4
    assert all(a.radius == pytest.approx(0.5) for a in arcs)
    box_ys = [p.y for g in path.geometry for p in (g.p1, g.p2)]
    assert min(box_ys) == pytest.approx(1.5)
    assert max(box_ys) == pytest.approx(3.5)


def test_mm_document(fixture) -> None:
    doc = load_svg(fixture("mm-document.svg"), unit_scale=MM)
    assert doc.width == pytest.approx(100.0)
    line = next(p for p in doc.paths if p.source_id == "line80mm").geometry[0]
    assert isinstance(line, Line)
    assert line.length == pytest.approx(80.0)
    assert line.p1.almost_equal(P(10, 90))
    circle = next(p for p in doc.paths if p.source_id == "circle-r20mm")
    assert all(isinstance(g, Arc) and g.radius == pytest.approx(20.0) for g in circle.geometry)


def test_px_document(fixture) -> None:
    doc = load(fixture("px-document.svg"))
    (path,) = doc.paths
    line = path.geometry[0]
    assert isinstance(line, Line)
    assert line.length == pytest.approx(2.0)
    assert line.p1.almost_equal(P(1, 3))


def test_quadratic_and_elliptical_conversion(fixture) -> None:
    (quad,) = load(fixture("quadratic.svg")).paths
    assert len(quad.geometry) == 1
    assert isinstance(quad.geometry[0], CubicBezier)
    doc = load(fixture("elliptical-arc.svg"))
    earc = next(p for p in doc.paths if p.source_id == "earc")
    assert all(isinstance(g, CubicBezier) for g in earc.geometry)
    assert len(earc.geometry) > 1
    carc = next(p for p in doc.paths if p.source_id == "carc")
    (arc,) = carc.geometry
    assert isinstance(arc, Arc)
    assert arc.radius == pytest.approx(1.0)
    assert arc.center.almost_equal(P(1.5, 0.5))
    # Drawn clockwise on screen: after the Y flip it bulges up from a chord at y=0.5.
    assert arc.is_clockwise
    assert arc.point_at(0.5).almost_equal(P(1.5, 1.5))


def test_scaled_and_mirrored_groups(fixture) -> None:
    (circle,) = load(fixture("scaled-group.svg")).paths
    assert all(isinstance(g, Arc) and g.radius == pytest.approx(1.0) for g in circle.geometry)
    assert all(isinstance(g, Arc) and g.center.almost_equal(P(2, 2)) for g in circle.geometry)
    (mirrored,) = load(fixture("mirrored-group.svg")).paths
    (arc,) = mirrored.geometry
    assert isinstance(arc, Arc)
    assert arc.p1.almost_equal(P(1, 1))
    assert arc.p2.almost_equal(P(3, 1))
    assert not arc.is_clockwise
    assert arc.point_at(0.5).almost_equal(P(2, 0))


def test_visibility_layer_and_id_filters(fixture) -> None:
    doc = load(fixture("layers.svg"))
    assert sorted(p.source_id or "" for p in doc.paths) == ["mark-line", "visible-line"]
    cuts = load(fixture("layers.svg"), layers=("Cuts",))
    assert [p.source_id for p in cuts.paths] == ["visible-line"]
    by_id = load(fixture("layers.svg"), layers=("layer3",))
    assert [p.source_id for p in by_id.paths] == ["mark-line"]
    one = load(fixture("layers.svg"), ids=("mark-line",))
    assert [p.source_id for p in one.paths] == ["mark-line"]
    none = load(fixture("layers.svg"), ids=("guide-line",))
    assert none.paths == ()


def test_degenerate_paths_are_dropped(fixture) -> None:
    doc = load(fixture("degenerate.svg"))
    assert [p.source_id for p in doc.paths] == ["real"]


def test_polyline_and_line_shapes(fixture) -> None:
    doc = load(fixture("polyline-open.svg"))
    by_id = {p.source_id: p for p in doc.paths}
    assert len(by_id["zigzag"].geometry) == 3
    assert not by_id["zigzag"].closed
    assert len(by_id["diagonal"].geometry) == 1


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SvgError):
        load_svg(tmp_path / "nope.svg", unit_scale=INCH)
