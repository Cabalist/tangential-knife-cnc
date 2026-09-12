"""Load cut geometry from an SVG file.

svgelements does the parsing: it applies the viewBox and every transform
and hands back px coordinates at 96 px per inch. This module walks the
element tree, applies the visibility, ``--id`` and ``--layer`` filters,
converts each subpath into geom2d lines, circular arcs and cubic Béziers,
scales to G-code units and flips Y so the machine frame has Y up.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from geom2d import Arc, CubicBezier, Line, P
from svgelements import svgelements as se

from tcnc.errors import SvgError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

PX_PER_INCH = 96.0
# svgelements converts millimetres with this truncated factor rather than 96 / 25.4;
# inverting the same number makes mm documents come back exact.
PX_PER_MM = 3.7795296
INKSCAPE_NS = "{http://www.inkscape.org/namespaces/inkscape}"
LABEL_KEY = f"{INKSCAPE_NS}label"

type Geometry = Line | Arc | CubicBezier


@dataclass(frozen=True, slots=True)
class SvgPath:
    """One subpath from the drawing, already in G-code units."""

    geometry: tuple[Geometry, ...]
    source_id: str | None
    closed: bool


@dataclass(frozen=True, slots=True)
class SvgDocument:
    """The cuttable content of an SVG file in G-code units."""

    paths: tuple[SvgPath, ...]
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class _Frame:
    """px → G-code unit mapping, with an optional Y flip about the page height."""

    scale: float
    height_px: float
    flip_y: bool

    def point(self, pt: se.Point) -> P:
        x = float(pt.x) * self.scale
        y = float(pt.y)
        if self.flip_y:
            y = self.height_px - y
        return P(x, y * self.scale)

    def length(self, value: float) -> float:
        return value * self.scale


def load_svg(
    path: Path | str,
    *,
    unit_scale: float,
    flip_y: bool = True,
    ids: Sequence[str] = (),
    layers: Sequence[str] = (),
    tolerance: float = 1e-9,
) -> SvgDocument:
    """Read ``path`` and return its cuttable geometry.

    ``unit_scale`` multiplies px to give G-code units. ``ids`` keeps only
    elements with those ids; ``layers`` keeps only elements inside a group
    whose Inkscape label or id matches. Elements with ``display:none`` are
    removed by the parser; ``visibility="hidden"`` ones are skipped here.
    """
    try:
        svg = se.SVG.parse(str(path), reify=True, ppi=PX_PER_INCH)
    except FileNotFoundError as exc:
        msg = f"SVG file not found: {path}"
        raise SvgError(msg) from exc
    except (ValueError, OSError) as exc:
        msg = f"cannot parse SVG file {path}: {exc}"
        raise SvgError(msg) from exc
    width_px = _page_size(svg.width)
    height_px = _page_size(svg.height)
    if height_px is None or width_px is None:
        msg = f"SVG file {path} has no usable width/height"
        raise SvgError(msg)
    frame = _Frame(scale=unit_scale, height_px=height_px, flip_y=flip_y)
    wanted_ids = frozenset(ids)
    wanted_layers = frozenset(layers)
    paths: list[SvgPath] = []
    for shape, ancestors in _shapes(svg, ()):
        if shape.values.get("visibility") == "hidden":
            continue
        shape_id = shape.id if isinstance(shape.id, str) else None
        if wanted_ids and shape_id not in wanted_ids:
            continue
        if wanted_layers and not wanted_layers.intersection(ancestors):
            continue
        paths.extend(_convert_shape(shape, shape_id, frame, tolerance))
    return SvgDocument(tuple(paths), width=frame.length(width_px), height=frame.length(height_px))


def _page_size(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _shapes(group: se.Group, ancestors: tuple[str, ...]) -> Iterator[tuple[se.Shape, tuple[str, ...]]]:
    """Yield every shape with the labels and ids of the groups above it."""
    for child in group:
        if isinstance(child, se.Group):
            names = tuple(name for name in (child.values.get(LABEL_KEY), child.id) if isinstance(name, str))
            yield from _shapes(child, ancestors + names)
        elif isinstance(child, se.Shape):
            yield child, ancestors


def _convert_shape(shape: se.Shape, source_id: str | None, frame: _Frame, tolerance: float) -> list[SvgPath]:
    paths: list[SvgPath] = []
    geometry: list[Geometry] = []
    closed = False

    def flush() -> None:
        nonlocal geometry, closed
        if geometry:
            paths.append(SvgPath(tuple(geometry), source_id, closed))
        geometry = []
        closed = False

    for segment in se.Path(shape).segments():
        match segment:
            case se.Move():
                flush()
            case se.Close():
                geometry.extend(_line(segment, frame, tolerance))
                closed = True
            case se.Line():
                geometry.extend(_line(segment, frame, tolerance))
            case se.QuadraticBezier():
                geometry.append(
                    CubicBezier.from_quadratic(
                        frame.point(segment.start), frame.point(segment.control), frame.point(segment.end)
                    )
                )
            case se.CubicBezier():
                geometry.append(
                    CubicBezier(
                        frame.point(segment.start),
                        frame.point(segment.control1),
                        frame.point(segment.control2),
                        frame.point(segment.end),
                    )
                )
            case se.Arc():
                geometry.extend(_arc(segment, frame, tolerance))
    flush()
    return paths


def _line(segment: se.Line | se.Close, frame: _Frame, tolerance: float) -> list[Geometry]:
    p1, p2 = frame.point(segment.start), frame.point(segment.end)
    if p1.distance(p2) <= tolerance:
        return []
    return [Line(p1, p2)]


def _arc(segment: se.Arc, frame: _Frame, tolerance: float) -> list[Geometry]:
    rx, ry = float(segment.rx), float(segment.ry)
    if abs(rx - ry) > tolerance:
        return [
            CubicBezier(
                frame.point(cubic.start),
                frame.point(cubic.control1),
                frame.point(cubic.control2),
                frame.point(cubic.end),
            )
            for cubic in segment.as_cubic_curves()
        ]
    p1, p2 = frame.point(segment.start), frame.point(segment.end)
    if p1.distance(p2) <= tolerance:
        return []
    radius = frame.length(rx)
    sweep = float(segment.sweep)
    # A mirrored frame flips the sense of the sweep, and a semicircle has the
    # same centre either way, so choose the sign whose midpoint matches.
    midpoint = frame.point(segment.point(0.5))
    arc = Arc.from_sweep(p1, p2, radius, sweep)
    if not arc.point_at(0.5).almost_equal(midpoint, max(tolerance, radius * 1e-6)):
        arc = Arc.from_sweep(p1, p2, radius, -sweep)
    return [arc]
