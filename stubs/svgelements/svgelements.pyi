# Minimal typing surface of svgelements 1.9 for tcnc (see __init__.pyi).
from collections.abc import Iterator
from typing import IO, Any

DEFAULT_PPI: float

class Point:
    x: float
    y: float
    def __init__(self, x: float = ..., y: float = ...) -> None: ...

class Matrix:
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float
    def __init__(self, *components: object) -> None: ...
    @property
    def determinant(self) -> float: ...
    def point_in_matrix_space(self, point: Point) -> Point: ...

class SVGElement:
    values: dict[str, Any]
    @property
    def id(self) -> str | None: ...

class Transformable:
    transform: Matrix

class Shape(SVGElement, Transformable):
    def segments(self, transformed: bool = ...) -> list[PathSegment]: ...

class Path(Shape):
    def __init__(self, *args: object, **kwargs: object) -> None: ...
    def bbox(self) -> tuple[float, float, float, float]: ...

class Rect(Shape): ...
class Circle(Shape): ...
class Ellipse(Shape): ...
class Polyline(Shape): ...
class Polygon(Shape): ...
class SimpleLine(Shape): ...

class Group(SVGElement, Transformable):
    def __iter__(self) -> Iterator[SVGElement]: ...
    def __len__(self) -> int: ...

class Use(SVGElement, Transformable):
    def __iter__(self) -> Iterator[SVGElement]: ...
    def __len__(self) -> int: ...

class SVG(Group):
    width: float | None
    height: float | None
    viewbox: Any
    @classmethod
    def parse(
        cls,
        source: str | IO[bytes],
        reify: bool = ...,
        ppi: float = ...,
        width: float | None = ...,
        height: float | None = ...,
        color: str = ...,
        transform: str | None = ...,
        context: Any = ...,
        parse_display_none: bool = ...,
        on_error: str = ...,
    ) -> SVG: ...
    def elements(self, conditional: Any = ...) -> Iterator[SVGElement]: ...

class PathSegment:
    end: Point
    def point(self, position: float) -> Point: ...

class Move(PathSegment):
    start: Point | None

class Linear(PathSegment):
    start: Point

class Close(Linear): ...
class Line(Linear): ...

class Curve(PathSegment):
    start: Point

class QuadraticBezier(Curve):
    control: Point

class CubicBezier(Curve):
    control1: Point
    control2: Point

class Arc(Curve):
    rx: float
    ry: float
    center: Point
    sweep: float
    def get_rotation(self) -> float: ...
    def as_cubic_curves(self, arc_required: int | None = ...) -> Iterator[CubicBezier]: ...
