"""Toolpath ordering."""

from geom2d import Line, P

from tcnc.ordering import order_toolpaths, oriented_toward
from tcnc.toolpath import Toolpath
from tests.test_corners import square


def segment_path(x1: float, y1: float, x2: float, y2: float) -> Toolpath:
    tp = Toolpath.from_geometry([Line(P(x1, y1), P(x2, y2))])
    assert tp is not None
    return tp


def test_none_keeps_order() -> None:
    paths = [segment_path(5, 5, 6, 5), segment_path(0, 0, 1, 0)]
    assert order_toolpaths(paths, "none") == paths


def test_nearest_walks_rows_and_reverses_when_end_is_nearer() -> None:
    rows = [segment_path(0, 10, 4, 10), segment_path(0, 0, 4, 0), segment_path(0, 20, 4, 20)]
    ordered = order_toolpaths(rows, "nearest")
    assert [tp.start for tp in ordered] == [P(0, 0), P(4, 10), P(0, 20)]
    assert ordered[1].end == P(0, 10)


def test_closed_path_starts_at_nearest_vertex() -> None:
    rotated, distance = oriented_toward(square(), P(2.1, 2.1))
    assert rotated.start == P(2, 2)
    assert distance < 0.2
    assert rotated.closed


def test_open_path_keeps_direction_on_tie() -> None:
    tp = segment_path(-1, 0, 1, 0)
    oriented, _ = oriented_toward(tp, P(0, 5))
    assert oriented is tp
