"""Load cut geometry from an SVG file.

svgelements parses the file, applies the viewBox and composes every
transform into a per-shape matrix; this module does the rest explicitly so
no geometry passes through the parser's own transformed-arc representation
(which is only exact for similarity transforms):

- every point goes through the shape's affine matrix exactly;
- a circular arc under a similarity transform stays a circular arc, with
  its sense of rotation taken from the matrix determinant and the Y flip,
  built from its exact endpoints and validated at the job tolerance;
- an elliptical arc, or any arc under a shear or non-uniform scale, becomes
  cubic Béziers, refined until a sampled error estimate meets the curve
  tolerance or an explicit error is raised;
- an arc with a zero radius, or with identical endpoints, is what SVG says
  it is (a straight line; nothing);
- malformed path data raises ``SvgError`` instead of being skipped.

The loader converts faithfully; only exactly empty pieces are left out. The
job's resolution (dropping or merging pieces too short to matter, closure)
is applied later by ``Toolpath.from_geometry``.

Units: the output is millimetres. The root element's physical ``width`` and
``height`` are converted to CSS px exactly (96 per inch) before the parser
sees them, so a page declared in mm, cm, in, pt or pc has the exact size it
declares and px content keeps its exact px scale. Visibility policy:
``display:none`` and ``visibility="hidden"`` elements (their descendants
included) are skipped; opacity, clipping and masks are not considered.
Everything skipped, hidden content as well as text, images and foreign
objects, which tcnc cannot cut, is listed in ``SvgDocument.skipped`` so a
run can say what the drawing contains that the program does not.

The file is read once; ``SvgDocument.source`` carries the SHA-256 of those
bytes exactly as they are on disk, before the root size is normalised.
"""

import dataclasses
import io
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from geom2d import Arc, CubicBezier, GeometryError, Line, P
from svgelements import svgelements as se

from tcnc.errors import SvgError
from tcnc.provenance import FileDigest

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from tcnc.toolpath import SourceGeometry

PX_PER_INCH = 96.0
MM_PER_INCH = 25.4
MM_PER_PX = MM_PER_INCH / PX_PER_INCH
SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "{http://www.inkscape.org/namespaces/inkscape}"
LABEL_KEY = f"{INKSCAPE_NS}label"
# CSS absolute units, in px per unit (the inch factor is exact by construction).
PX_PER_UNIT = {
    "in": PX_PER_INCH,
    "mm": PX_PER_INCH / MM_PER_INCH,
    "cm": 10.0 * PX_PER_INCH / MM_PER_INCH,
    "pt": PX_PER_INCH / 72.0,
    "pc": PX_PER_INCH / 6.0,
}
# Peak radial error of one cubic Bézier fitted to a quarter circle, relative to the radius;
# used only as the starting guess before the measured refinement in ``_arc_pieces``.
_CUBIC_QUARTER_ERROR = 2.7e-4
# Work limit for one elliptical arc: beyond this many cubics the tolerance is reported as unmet.
MAX_ARC_PIECES = 4096
_ERROR_SAMPLES = (0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875)
_LENGTH_RE = re.compile(r"^\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)\s*$")


@dataclass(frozen=True, slots=True)
class SvgPath:
    """One subpath from the drawing, already in millimetres, with what it can be selected by.

    ``groups`` are the Inkscape labels and ids of the groups above the
    element, ``clones`` the ids of the ``<use>`` elements it came through.
    """

    geometry: tuple[SourceGeometry, ...]
    source_id: str | None
    closed: bool
    groups: tuple[str, ...] = ()
    clones: tuple[str, ...] = ()

    def selected_by(self, *, ids: Sequence[str] = (), layers: Sequence[str] = ()) -> bool:
        """True when the path matches the selection (an empty selection matches everything)."""
        return _selected(self.source_id, self.groups, self.clones, ids=ids, layers=layers)


@dataclass(frozen=True, slots=True)
class SvgProblem:
    """An element that could not be converted, reported only when a selection asks for it."""

    source_id: str | None
    groups: tuple[str, ...]
    clones: tuple[str, ...]
    message: str

    def selected_by(self, *, ids: Sequence[str] = (), layers: Sequence[str] = ()) -> bool:
        """True when the selection would include this element."""
        return _selected(self.source_id, self.groups, self.clones, ids=ids, layers=layers)


type SkipReason = Literal["hidden", "unsupported"]
UNSUPPORTED_TAGS = frozenset({"text", "image", "foreignObject"})
"""Drawing content tcnc cannot cut; reported as skipped rather than passed over silently."""


@dataclass(frozen=True, slots=True)
class SkippedElement:
    """A drawing element the loader left out: hidden, or not a shape tcnc can cut (``unsupported``)."""

    tag: str
    source_id: str | None
    groups: tuple[str, ...]
    reason: SkipReason


def _selected(
    source_id: str | None,
    groups: Sequence[str],
    clones: Sequence[str],
    *,
    ids: Sequence[str],
    layers: Sequence[str],
) -> bool:
    wanted_ids, wanted_layers = frozenset(ids), frozenset(layers)
    if wanted_ids and source_id not in wanted_ids and not wanted_ids.intersection(clones):
        return False
    return not wanted_layers or bool(wanted_layers.intersection(groups))


@dataclass(frozen=True, slots=True)
class SvgDocument:
    """The visible content of an SVG file in millimetres.

    ``problems`` are the elements that could not be converted (a degenerate
    transform, malformed geometry); they only matter when a selection
    includes them, so ``select`` raises for them then and not before.
    ``skipped`` lists what was left out on purpose: hidden elements and
    content tcnc cannot cut (text, images, foreign objects). ``source`` is
    the file's name and the SHA-256 of its bytes (``None`` for a document
    not loaded from a file).
    """

    paths: tuple[SvgPath, ...]
    width: float
    height: float
    problems: tuple[SvgProblem, ...] = ()
    skipped: tuple[SkippedElement, ...] = ()
    source: FileDigest | None = None

    def select(self, *, ids: Sequence[str] = (), layers: Sequence[str] = ()) -> tuple[SvgPath, ...]:
        """The paths matching ``ids`` (element ids or clone ids) and ``layers`` (group labels or ids).

        Raises:
            SvgError: When the selection includes an element that could not be converted.
        """
        for problem in self.problems:
            if problem.selected_by(ids=ids, layers=layers):
                raise SvgError(problem.message)
        return tuple(path for path in self.paths if path.selected_by(ids=ids, layers=layers))


@dataclass(frozen=True, slots=True)
class _Frame:
    """px → mm mapping with the Y flip, applied after the shape matrix."""

    scale: float
    height_px: float
    flip_y: bool

    def point(self, pt: se.Point) -> P:
        y = float(pt.y)
        if self.flip_y:
            y = self.height_px - y
        return P(float(pt.x) * self.scale, y * self.scale)


@dataclass(frozen=True, slots=True)
class _Mapping:
    """One shape's affine matrix followed by the page frame."""

    matrix: se.Matrix
    frame: _Frame

    def point(self, pt: se.Point) -> P:
        return self.frame.point(self.matrix.point_in_matrix_space(pt))

    @property
    def orientation(self) -> float:
        """+1 when the mapping preserves the sense of rotation, -1 when it mirrors it."""
        sign = 1.0 if self.matrix.determinant > 0 else -1.0
        return -sign if self.frame.flip_y else sign

    @property
    def max_scale(self) -> float:
        """Largest length scale factor of the mapping (operator norm)."""
        return _singular_values(self.matrix)[0] * self.frame.scale

    @property
    def is_similarity(self) -> bool:
        """True when the matrix is a rotation/scale, possibly mirrored (circles stay circles).

        Judged on the coefficients directly: the columns of a similarity
        are equal in length and perpendicular, which for ``[[a, c], [b, d]]``
        means ``a = d, b = -c`` (proper) or ``a = -d, b = c`` (mirrored),
        within a relative slack. No singular values are involved; their
        discriminant loses the difference for ordinary rotations.
        """
        a, b, c, d = self.matrix.a, self.matrix.b, self.matrix.c, self.matrix.d
        scale = math.hypot(a, b)
        slack = scale * 1e-9
        return scale > 0.0 and (
            (abs(a - d) <= slack and abs(b + c) <= slack) or (abs(a + d) <= slack and abs(b - c) <= slack)
        )

    @property
    def uniform_scale(self) -> float:
        """Length scale of a similarity mapping."""
        return math.hypot(self.matrix.a, self.matrix.b) * self.frame.scale


def _singular_values(matrix: se.Matrix) -> tuple[float, float]:
    """The linear part's singular values, largest first; the small one comes from the determinant, not by cancellation."""
    a, b, c, d = matrix.a, matrix.b, matrix.c, matrix.d
    total = a * a + b * b + c * c + d * d
    det = abs(matrix.determinant)
    root = math.sqrt(max(0.0, total * total - 4.0 * det * det))
    big = math.sqrt(max(0.0, (total + root) / 2.0))
    small = det / big if big > 0.0 else 0.0
    return big, small


def load_svg(
    path: Path | str,
    *,
    flip_y: bool = True,
    tolerance: float | None = None,
    curve_tolerance: float = 0.01,
) -> SvgDocument:
    """Read ``path`` and return its visible geometry in millimetres.

    Every visible path is returned with the group names and clone ids it
    can be selected by (``SvgDocument.select``).
    ``tolerance`` (mm, default geom2d's ``EPSILON``) is the distance at
    which a circular arc's radius and sweep are validated against, and if
    need be repaired to, its endpoints. ``curve_tolerance`` (mm) bounds the
    sampled error of converting elliptical or sheared arcs to cubics.
    """
    data = _read_bytes(path)
    svg = _parse(path, data)
    width_px, height_px = _number(svg.width), _number(svg.height)
    if width_px is None or height_px is None or width_px <= 0.0 or height_px <= 0.0:
        msg = f"SVG file {path} has no usable width/height (both attributes are required on the root element)"
        raise SvgError(msg)
    frame = _Frame(scale=MM_PER_PX, height_px=height_px, flip_y=flip_y)
    paths: list[SvgPath] = []
    problems: list[SvgProblem] = []
    skipped: list[SkippedElement] = []
    for shape, groups, clones in _shapes(svg, (), (), skipped):
        shape_id = shape.id if isinstance(shape.id, str) else None
        if _hidden(shape):
            skipped.append(SkippedElement(_tag(shape), shape_id, groups, "hidden"))
            continue
        mapping = _Mapping(shape.transform, frame)
        if _singular_values(mapping.matrix)[1] <= 0.0:
            message = f"element {shape_id or '<unnamed>'} has a degenerate transform"
            problems.append(SvgProblem(shape_id, groups, clones, message))
            continue
        try:
            converted = _convert_shape(shape, shape_id, mapping, tolerance, curve_tolerance)
        except SvgError as exc:
            problems.append(SvgProblem(shape_id, groups, clones, str(exc)))
            continue
        paths.extend(dataclasses.replace(path, groups=groups, clones=clones) for path in converted)
    return SvgDocument(
        tuple(paths),
        width=width_px * MM_PER_PX,
        height=height_px * MM_PER_PX,
        problems=tuple(problems),
        skipped=tuple(skipped),
        source=FileDigest.of(Path(path), data),
    )


def _read_bytes(path: Path | str) -> bytes:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError as exc:
        msg = f"SVG file not found: {path}"
        raise SvgError(msg) from exc
    except OSError as exc:
        msg = f"cannot read SVG file {path}: {exc}"
        raise SvgError(msg) from exc


def _parse(path: Path | str, data: bytes) -> se.SVG:
    """Parse ``data``, read from ``path``, with the root's physical size normalised to exact px first."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        msg = f"{path} is not well-formed XML: {exc}"
        raise SvgError(msg) from exc
    if root.tag not in ("svg", f"{{{SVG_NS}}}svg"):
        msg = f"{path} does not contain an <svg> root element"
        raise SvgError(msg)
    if root.get("width") is None or root.get("height") is None:
        msg = f"SVG file {path} has no usable width/height (both attributes are required on the root element)"
        raise SvgError(msg)
    for name in ("width", "height"):
        px = _length_px(root.get(name))
        if px is not None:
            root.set(name, repr(px))
    try:
        # Hidden elements are kept so they can be reported; the loader skips them itself.
        svg = se.SVG.parse(
            io.BytesIO(ET.tostring(root)), reify=False, ppi=PX_PER_INCH, on_error="raise", parse_display_none=True
        )
    except (ValueError, TypeError, IndexError, KeyError, OSError) as exc:
        detail = str(exc) or type(exc).__name__
        msg = f"cannot parse SVG file {path}: {detail}"
        raise SvgError(msg) from exc
    if not isinstance(svg, se.SVG):
        msg = f"{path} does not contain an <svg> root element"
        raise SvgError(msg)
    return svg


def _length_px(declared: object) -> float | None:
    """A CSS length with an absolute unit in exact px; ``None`` for anything else (left to the parser)."""
    if not isinstance(declared, str):
        return None
    match = _LENGTH_RE.match(declared)
    if match is None or match.group(2) not in PX_PER_UNIT:
        return None
    return float(match.group(1)) * PX_PER_UNIT[match.group(2)]


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and math.isfinite(value):
        return float(value)
    return None


def _shapes(
    container: se.Group | se.Use, groups: tuple[str, ...], clones: tuple[str, ...], skipped: list[SkippedElement]
) -> Iterator[tuple[se.Shape, tuple[str, ...], tuple[str, ...]]]:
    """Yield every shape with the labels/ids of the groups above it and the ids of the clones it belongs to.

    Content tcnc cannot cut (``UNSUPPORTED_TAGS``) is appended to
    ``skipped``; titles, metadata and the like are passed over.
    """
    for child in container:
        if isinstance(child, se.Group):
            names = tuple(name for name in (child.values.get(LABEL_KEY), child.id) if isinstance(name, str))
            yield from _shapes(child, groups + names, clones, skipped)
        elif isinstance(child, se.Use):
            use_ids = (child.id,) if isinstance(child.id, str) else ()
            yield from _shapes(child, groups, clones + use_ids, skipped)
        elif isinstance(child, se.Shape):
            yield child, groups, clones
        elif (tag := _tag(child)) in UNSUPPORTED_TAGS:
            element_id = child.id if isinstance(child.id, str) else None
            skipped.append(SkippedElement(tag, element_id, groups, "hidden" if _hidden(child) else "unsupported"))


def _tag(element: se.SVGElement) -> str:
    """The element's tag without its namespace (``path``, ``text``, ...)."""
    tag = element.values.get("tag")
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else "element"


def _hidden(element: se.SVGElement) -> bool:
    """True for ``display:none`` or ``visibility:hidden``, own or inherited (the parser propagates both)."""
    return element.values.get("display") == "none" or element.values.get("visibility") == "hidden"


def _convert_shape(
    shape: se.Shape, source_id: str | None, mapping: _Mapping, tolerance: float | None, curve_tolerance: float
) -> list[SvgPath]:
    paths: list[SvgPath] = []
    geometry: list[SourceGeometry] = []
    closed = False
    try:
        for segment in shape.segments(transformed=False):
            if isinstance(segment, se.Move):
                if geometry:
                    paths.append(SvgPath(tuple(geometry), source_id, closed))
                geometry = []
                closed = False
                continue
            geometry.extend(_convert_segment(segment, mapping, tolerance, curve_tolerance))
            closed = closed or isinstance(segment, se.Close)
    except (GeometryError, SvgError) as exc:
        msg = f"element {source_id or '<unnamed>'}: {exc}"
        raise SvgError(msg) from exc
    if geometry:
        paths.append(SvgPath(tuple(geometry), source_id, closed))
    return paths


def _convert_segment(
    segment: se.PathSegment, mapping: _Mapping, tolerance: float | None, curve_tolerance: float
) -> list[SourceGeometry]:
    match segment:
        case se.Close() | se.Line():
            return _line(segment, mapping)
        case se.QuadraticBezier():
            return [
                CubicBezier.from_quadratic(
                    mapping.point(segment.start), mapping.point(segment.control), mapping.point(segment.end)
                )
            ]
        case se.CubicBezier():
            return [_cubic(segment, mapping)]
        case se.Arc():
            return _arc(segment, mapping, tolerance, curve_tolerance)
        case _:
            return []


def _line(segment: se.Line | se.Close, mapping: _Mapping) -> list[SourceGeometry]:
    """The line, unless it is exactly empty."""
    p1, p2 = mapping.point(segment.start), mapping.point(segment.end)
    if p1 == p2:
        return []
    return [Line(p1, p2)]


def _cubic(segment: se.CubicBezier, mapping: _Mapping) -> CubicBezier:
    return CubicBezier(
        mapping.point(segment.start),
        mapping.point(segment.control1),
        mapping.point(segment.control2),
        mapping.point(segment.end),
    )


def _arc(segment: se.Arc, mapping: _Mapping, tolerance: float | None, curve_tolerance: float) -> list[SourceGeometry]:
    rx, ry = abs(float(segment.rx)), abs(float(segment.ry))
    p1, p2 = mapping.point(segment.start), mapping.point(segment.end)
    if rx == 0.0 or ry == 0.0:
        # SVG: a zero radius makes the arc a straight line, and identical endpoints make it nothing
        # (the parser reports both with zero radii).
        return [] if p1 == p2 else [Line(p1, p2)]
    if mapping.is_similarity and abs(rx - ry) <= max(rx, ry) * 1e-9:
        radius = rx * mapping.uniform_scale
        angle = float(segment.sweep) * mapping.orientation
        return [_circular_arc(segment, mapping, p1=p1, p2=p2, radius=radius, angle=angle, tolerance=tolerance)]
    pieces = _arc_pieces(segment, rx, ry, curve_tolerance / mapping.max_scale)
    return [_cubic(cubic, mapping) for cubic in segment.as_cubic_curves(arc_required=pieces)]


def _circular_arc(  # noqa: PLR0913 - one keyword per arc datum
    segment: se.Arc, mapping: _Mapping, *, p1: P, p2: P, radius: float, angle: float, tolerance: float | None
) -> Arc:
    """A geom2d arc from the exact mapped endpoints, validated (and if need be repaired) at ``tolerance``.

    When the endpoints coincide at geom2d's floor the sweep is a full turn
    (or nothing); the centre is then the parser's, mapped, and the end is
    placed on the circle by the sweep.
    """
    if not p1.almost_equal(p2):
        return Arc.from_sweep(p1, p2, radius, angle, tolerance=tolerance)
    center = mapping.point(segment.center)
    end = center + (p1 - center).rotate(angle)
    return Arc(p1, end, radius, angle, center)


def _arc_pieces(segment: se.Arc, rx: float, ry: float, local_tolerance: float) -> int:
    """Smallest cubic count (in the arc's own frame) whose sampled error estimate stays within ``local_tolerance``.

    Raises ``SvgError`` when ``MAX_ARC_PIECES`` cubics are not enough; a
    count is never returned unverified.
    """
    sweep = abs(float(segment.sweep))
    radius = max(rx, ry)
    guess = (math.pi / 2.0) * (local_tolerance / (_CUBIC_QUARTER_ERROR * radius)) ** (1.0 / 6.0)
    pieces = min(MAX_ARC_PIECES, max(1, math.ceil(sweep / max(guess, 1e-6))))
    rotation = float(segment.get_rotation())
    center = segment.center
    while True:
        if _ellipse_error(segment.as_cubic_curves(arc_required=pieces), center, rx, ry, rotation) <= local_tolerance:
            return pieces
        if pieces >= MAX_ARC_PIECES:
            msg = (
                f"elliptical arc (radii {rx:g} x {ry:g}) cannot be approximated within the curve tolerance "
                f"with {MAX_ARC_PIECES} cubics"
            )
            raise SvgError(msg)
        pieces = min(MAX_ARC_PIECES, pieces * 2)


def _ellipse_error(cubics: Iterator[se.CubicBezier], center: se.Point, rx: float, ry: float, rotation: float) -> float:
    """Largest sampled first-order distance estimate from the cubics to the ellipse they approximate."""
    cos_r, sin_r = math.cos(-rotation), math.sin(-rotation)
    worst = 0.0
    for cubic in cubics:
        for t in _ERROR_SAMPLES:
            p = cubic.point(t)
            dx, dy = float(p.x) - float(center.x), float(p.y) - float(center.y)
            x, y = dx * cos_r - dy * sin_r, dx * sin_r + dy * cos_r
            u, v = x / rx, y / ry
            f = u * u + v * v - 1.0
            grad = math.hypot(2.0 * u / rx, 2.0 * v / ry)
            if grad > 0.0:
                worst = max(worst, abs(f) / grad)
    return worst
