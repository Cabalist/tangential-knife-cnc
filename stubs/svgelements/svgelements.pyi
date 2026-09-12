# Minimal typing surface of svgelements 1.9 for tcnc (see __init__.pyi).
from collections.abc import Iterator
from typing import Any

DEFAULT_PPI: float

class Point:
    x: float
    y: float
    def __init__(self, x: float = ..., y: float = ...) -> None: ...

class SVGElement:
    values: dict[str, Any]
    @property
    def id(self) -> str | None: ...

class Shape(SVGElement):
    def segments(self, transformed: bool = ...) -> list[PathSegment]: ...

class Path(Shape):
    def __init__(self, *args: object, **kwargs: object) -> None: ...

class Rect(Shape): ...
class Circle(Shape): ...
class Ellipse(Shape): ...
class Polyline(Shape): ...
class Polygon(Shape): ...
class SimpleLine(Shape): ...

class Group(SVGElement):
    def __iter__(self) -> Iterator[SVGElement]: ...
    def __len__(self) -> int: ...

class SVG(Group):
    width: float | None
    height: float | None
    viewbox: Any
    @classmethod
    def parse(
        cls,
        source: str,
        reify: bool = ...,
        ppi: float = ...,
        width: float | None = ...,
        height: float | None = ...,
        color: str = ...,
        transform: str | None = ...,
        context: Any = ...,
        parse_display_none: bool = ...,
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
    def as_cubic_curves(self, arc_required: int | None = ...) -> Iterator[CubicBezier]: ...
