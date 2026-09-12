"""SVG loading: millimetre scale, flip, transforms, filters, conversion, error paths."""

import math
from typing import TYPE_CHECKING

import pytest
from geom2d import Arc, CubicBezier, Line, P

from tcnc.errors import SvgError
from tcnc.svg import MM_PER_PX, SvgDocument, load_svg
from tcnc.toolpath import Toolpath

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

INCH = 25.4


def mm(*inches: float) -> P:
    """A point given in inches, in millimetres."""
    x, y = inches
    return P(x * INCH, y * INCH)


def load(fixture: Path, *, flip_y: bool = True, ids: tuple[str, ...] = (), layers: tuple[str, ...] = ()) -> SvgDocument:
    return load_svg(fixture, flip_y=flip_y, ids=ids, layers=layers)


def drawing(tmp_path: Path, body: str, *, size: str = 'width="96" height="96"') -> Path:
    path = tmp_path / "drawing.svg"
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" {size}>{body}</svg>'
    )
    return path


def test_inch_page_is_scaled_flipped_and_closed(fixture: Callable[[str], Path]) -> None:
    doc = load(fixture("square.svg"))
    assert doc.width == pytest.approx(4 * INCH)
    assert doc.height == pytest.approx(4 * INCH)
    (path,) = doc.paths
    assert path.source_id == "square"
    assert path.closed
    assert len(path.geometry) == 4
    assert all(isinstance(g, Line) for g in path.geometry)
    first = path.geometry[0]
    assert isinstance(first, Line)
    assert first.p1.almost_equal(mm(1, 3))  # y flipped: 4 - 1
    assert first.p2.almost_equal(mm(3, 3))


def test_no_flip_keeps_svg_frame(fixture: Callable[[str], Path]) -> None:
    doc = load(fixture("square.svg"), flip_y=False)
    first = doc.paths[0].geometry[0]
    assert isinstance(first, Line)
    assert first.p1.almost_equal(mm(1, 1))


def test_circle_becomes_four_arcs(fixture: Callable[[str], Path]) -> None:
    (path,) = load(fixture("circle.svg")).paths
    arcs = [g for g in path.geometry if isinstance(g, Arc)]
    assert len(arcs) == 4
    assert len(path.geometry) == 4  # zero-length Close dropped
    for arc in arcs:
        assert arc.radius == pytest.approx(INCH)
        assert arc.center.almost_equal(mm(2, 2))
        assert abs(arc.angle) == pytest.approx(math.pi / 2)


def test_rounded_rect_lines_and_arcs(fixture: Callable[[str], Path]) -> None:
    (path,) = load(fixture("rounded-rect.svg")).paths
    assert sum(isinstance(g, Line) for g in path.geometry) == 4
    arcs = [g for g in path.geometry if isinstance(g, Arc)]
    assert len(arcs) == 4
    assert all(a.radius == pytest.approx(0.5 * INCH) for a in arcs)
    box_ys = [p.y for g in path.geometry for p in (g.p1, g.p2)]
    assert min(box_ys) == pytest.approx(1.5 * INCH)
    assert max(box_ys) == pytest.approx(3.5 * INCH)


def test_mm_document_is_exact(fixture: Callable[[str], Path]) -> None:
    doc = load_svg(fixture("mm-document.svg"))
    assert doc.width == pytest.approx(100.0, abs=1e-9)
    line = next(p for p in doc.paths if p.source_id == "line80mm").geometry[0]
    assert isinstance(line, Line)
    assert line.length == pytest.approx(80.0, abs=1e-9)
    assert line.p1.almost_equal(P(10, 90), 1e-9)
    circle = next(p for p in doc.paths if p.source_id == "circle-r20mm")
    assert all(isinstance(g, Arc) and g.radius == pytest.approx(20.0, abs=1e-9) for g in circle.geometry)


def test_px_document_uses_96_px_per_inch(fixture: Callable[[str], Path]) -> None:
    doc = load(fixture("px-document.svg"))
    assert doc.width == pytest.approx(384 * MM_PER_PX)
    (path,) = doc.paths
    line = path.geometry[0]
    assert isinstance(line, Line)
    assert line.length == pytest.approx(2 * INCH)
    assert line.p1.almost_equal(mm(1, 3))


def test_quadratic_and_elliptical_conversion(fixture: Callable[[str], Path]) -> None:
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
    assert arc.radius == pytest.approx(INCH)
    assert arc.center.almost_equal(mm(1.5, 0.5))
    # Drawn clockwise on screen: after the Y flip it bulges up from a chord at y=0.5 in.
    assert arc.is_clockwise
    assert arc.point_at(0.5).almost_equal(mm(1.5, 1.5))


def test_ellipse_conversion_error_is_bounded(tmp_path: Path) -> None:
    rx_px, ry_px = 200.0, 100.0
    path = drawing(
        tmp_path, f'<path d="M0 0 A{rx_px} {ry_px} 0 0 1 {2 * rx_px} 0"/>', size='width="1000" height="1000"'
    )
    rx, ry = rx_px * MM_PER_PX, ry_px * MM_PER_PX
    for tol in (1e-3, 1e-5):
        doc = load_svg(path, flip_y=False, curve_tolerance=tol)
        (svg_path,) = doc.paths
        worst = 0.0
        for cubic in svg_path.geometry:
            assert isinstance(cubic, CubicBezier)
            for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                p = cubic.point_at(t)
                # Distance to the ellipse centred at (rx, 0) from the implicit equation and its gradient.
                u, v = (p.x - rx) / rx, p.y / ry
                f = u * u + v * v - 1.0
                grad = math.hypot(2 * u / rx, 2 * v / ry)
                worst = max(worst, abs(f) / grad)
        assert worst <= 2.0 * tol, (tol, worst)


def test_scaled_and_mirrored_groups(fixture: Callable[[str], Path]) -> None:
    (circle,) = load(fixture("scaled-group.svg")).paths
    assert all(isinstance(g, Arc) and g.radius == pytest.approx(INCH) for g in circle.geometry)
    assert all(isinstance(g, Arc) and g.center.almost_equal(mm(2, 2)) for g in circle.geometry)
    (mirrored,) = load(fixture("mirrored-group.svg")).paths
    (arc,) = mirrored.geometry
    assert isinstance(arc, Arc)
    assert arc.p1.almost_equal(mm(1, 1))
    assert arc.p2.almost_equal(mm(3, 1))
    assert not arc.is_clockwise
    assert arc.point_at(0.5).almost_equal(mm(2, 0))


def test_rotated_circle_stays_circular_and_skewed_arc_stays_on_the_artwork(tmp_path: Path) -> None:
    rotated = drawing(tmp_path, '<g transform="rotate(30 48 48)"><circle cx="48" cy="48" r="20"/></g>')
    (path,) = load_svg(rotated, flip_y=False).paths
    assert all(isinstance(g, Arc) and g.radius == pytest.approx(20 * MM_PER_PX) for g in path.geometry)
    skewed = drawing(tmp_path, '<path transform="skewX(45)" d="M20 0 A20 20 0 0 1 0 20"/>')
    geometries = load_svg(skewed, flip_y=False).paths[0].geometry
    assert len(geometries) > 1
    assert all(isinstance(g, CubicBezier) for g in geometries)
    for g in geometries:
        point = g.p1
        original = P(point.x - point.y, point.y)  # inverse shear
        assert original.length == pytest.approx(20 * MM_PER_PX, abs=1e-6)


@pytest.mark.parametrize("angle", [0, 6, 10, 18, 30, 45, 90])
def test_rotated_circles_stay_circular(tmp_path: Path, angle: int) -> None:
    body = f'<circle transform="rotate({angle} 192 192)" cx="192" cy="192" r="96"/>'
    (path,) = load_svg(drawing(tmp_path, body, size='width="384" height="384"')).paths
    assert all(isinstance(g, Arc) for g in path.geometry), [type(g).__name__ for g in path.geometry]
    assert all(isinstance(g, Arc) and g.radius == pytest.approx(96 * MM_PER_PX) for g in path.geometry)


def test_arcs_pass_exactly_through_their_mapped_endpoints(tmp_path: Path) -> None:
    # The chord is longer than the diameter: SVG grows the radius; the arc is built from the exact endpoints.
    path = drawing(tmp_path, '<path d="M0 0 A10 10 0 0 1 20.02 0"/>')
    (svg_path,) = load_svg(path, flip_y=False, tolerance=0.01).paths
    (arc,) = svg_path.geometry
    assert isinstance(arc, Arc)
    assert arc.p1 == P(0, 0)
    assert arc.p2 == P(20.02 * MM_PER_PX, 0)
    assert arc.radius == pytest.approx(10.01 * MM_PER_PX, abs=1e-6)
    assert abs(arc.angle) == pytest.approx(math.pi)


def test_visibility_layer_and_id_filters(fixture: Callable[[str], Path]) -> None:
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


def test_clones_are_included_and_selectable(tmp_path: Path) -> None:
    path = drawing(
        tmp_path,
        '<defs><path id="one" d="M0 0L20 20"/></defs><use id="inst" href="#one" x="10"/><path id="other" d="M10 20L30 30"/>',
    )
    doc = load_svg(path, flip_y=False)
    assert sorted(p.source_id or "" for p in doc.paths) == ["one", "other"]
    clone = next(p for p in doc.paths if p.source_id == "one").geometry[0]
    assert isinstance(clone, Line)
    assert clone.p1.almost_equal(P(10 * MM_PER_PX, 0))
    assert [p.source_id for p in load_svg(path, ids=("inst",)).paths] == ["one"]


def test_degenerate_paths_are_dropped(fixture: Callable[[str], Path]) -> None:
    doc = load(fixture("degenerate.svg"))
    assert [p.source_id for p in doc.paths] == ["real"]


def test_polyline_and_line_shapes(fixture: Callable[[str], Path]) -> None:
    doc = load(fixture("polyline-open.svg"))
    by_id = {p.source_id: p for p in doc.paths}
    assert len(by_id["zigzag"].geometry) == 3
    assert not by_id["zigzag"].closed
    assert len(by_id["diagonal"].geometry) == 1


def test_zero_radius_arc_is_a_line(tmp_path: Path) -> None:
    path = drawing(tmp_path, '<path d="M0 0A0 10 0 0 0 50 50"/>')
    (svg_path,) = load_svg(path).paths
    assert isinstance(svg_path.geometry[0], Line)


def test_loader_keeps_short_pieces_for_the_toolpath_stage(tmp_path: Path) -> None:
    path = drawing(tmp_path, '<path d="M0 0L0.0002 0"/>')  # 0.0002 px is 53 nm
    (svg_path,) = load_svg(path, tolerance=0.01).paths
    assert svg_path.geometry[0].length == pytest.approx(0.0002 * MM_PER_PX)
    assert Toolpath.from_geometry(svg_path.geometry, tolerance=1e-5) is not None
    assert Toolpath.from_geometry(svg_path.geometry, tolerance=1e-4) is None


def test_nearly_complete_arc_is_kept(tmp_path: Path) -> None:
    body = '<path id="subject" d="M96 96 A96 96 0 1 1 96.00001 96"/><path id="control" d="M0 300 L96 300"/>'
    doc = load_svg(drawing(tmp_path, body, size='width="384" height="384"'), tolerance=0.01)
    subject = next(p for p in doc.paths if p.source_id == "subject")
    assert all(isinstance(g, Arc) for g in subject.geometry)
    assert sum(g.length for g in subject.geometry) == pytest.approx(2 * math.pi * INCH, rel=1e-5)
    identical = drawing(tmp_path, '<path d="M96 96 A96 96 0 1 1 96 96"/><path d="M0 0 L10 0"/>')
    assert len(load_svg(identical).paths) == 1  # SVG: identical endpoints mean no arc at all


def test_thin_ellipse_is_not_replaced_by_its_chord(tmp_path: Path) -> None:
    path = drawing(tmp_path, '<path d="M96 96 A100 0.000001 0 1 1 106 96"/>', size='width="384" height="384"')
    (svg_path,) = load_svg(path, flip_y=False).paths
    assert all(isinstance(g, CubicBezier) for g in svg_path.geometry)
    xs = [p.x for g in svg_path.geometry for p in (g.p1, g.p2)]
    assert max(xs) == pytest.approx(201 * MM_PER_PX, abs=0.05)  # it reaches the far end of the major axis
    assert sum(g.length for g in svg_path.geometry) > 3 * INCH


def test_ellipse_work_limit_is_an_error_not_a_guess(tmp_path: Path) -> None:
    path = drawing(tmp_path, '<path id="e" d="M100 0 A100 50 0 0 1 0 50"/>', size='width="384" height="384"')
    with pytest.raises(SvgError, match=r"element e.*cannot be approximated"):
        load_svg(path, curve_tolerance=1e-30)


def test_anisotropic_transform_is_not_degenerate(tmp_path: Path) -> None:
    path = drawing(tmp_path, '<path transform="scale(1000000000 1)" d="M0 0 L0.000000096 96"/>')
    (svg_path,) = load_svg(path, flip_y=False).paths
    assert svg_path.geometry[0].p2.distance(mm(1, 1)) < 1e-9
    singular = drawing(tmp_path, '<path transform="scale(1 0)" d="M0 0 L10 10"/>')
    with pytest.raises(SvgError, match="degenerate"):
        load_svg(singular)


def test_physical_page_size_does_not_touch_px_content(tmp_path: Path) -> None:
    path = drawing(tmp_path, '<path d="M0 0 L96 0"/>', size='width="100mm" height="200px"')
    doc = load_svg(path, flip_y=False)
    assert doc.paths[0].geometry[0].length == pytest.approx(25.4, abs=1e-9, rel=0)
    assert doc.width == pytest.approx(100.0, abs=1e-9, rel=0)
    assert doc.height == pytest.approx(200 * MM_PER_PX, abs=1e-9, rel=0)
    for unit, expected in (("cm", 100.0), ("pt", 25.4 * 10 / 72), ("pc", 25.4 * 10 / 6), ("in", 254.0)):
        sized = load_svg(drawing(tmp_path, '<path d="M0 0 L1 0"/>', size=f'width="10{unit}" height="10{unit}"'))
        assert sized.width == pytest.approx(expected, abs=1e-9, rel=0)


def test_error_paths_raise_svg_error(tmp_path: Path) -> None:
    with pytest.raises(SvgError, match="not found"):
        load_svg(tmp_path / "nope.svg")
    broken = tmp_path / "broken.svg"
    broken.write_text("<svg><path></svg>")
    with pytest.raises(SvgError, match="well-formed"):
        load_svg(broken)
    malformed = drawing(tmp_path, '<path d="M0 0L30"/><path d="M0 0L20 20"/>')
    with pytest.raises(SvgError, match="cannot parse"):
        load_svg(malformed)
    not_svg = tmp_path / "root.svg"
    not_svg.write_text("<root/>")
    with pytest.raises(SvgError):
        load_svg(not_svg)
    no_size = tmp_path / "nosize.svg"
    no_size.write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1"/></svg>')
    with pytest.raises(SvgError, match="width/height"):
        load_svg(no_size)
