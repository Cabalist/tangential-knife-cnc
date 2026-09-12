# Local stubs for the parts of svgelements that tcnc uses.
# svgelements.py is ISO-8859-1 encoded, which ty and pyrefly cannot read, so
# they would otherwise see an empty module. Keep these in step with
# src/tcnc/svg.py and tests.
from .svgelements import (
    SVG as SVG,
    Arc as Arc,
    Circle as Circle,
    Close as Close,
    CubicBezier as CubicBezier,
    Curve as Curve,
    Ellipse as Ellipse,
    Group as Group,
    Line as Line,
    Linear as Linear,
    Matrix as Matrix,
    Move as Move,
    Path as Path,
    PathSegment as PathSegment,
    Point as Point,
    Polygon as Polygon,
    Polyline as Polyline,
    QuadraticBezier as QuadraticBezier,
    Rect as Rect,
    Shape as Shape,
    SimpleLine as SimpleLine,
    SVGElement as SVGElement,
    Transformable as Transformable,
    Use as Use,
)

name: str
