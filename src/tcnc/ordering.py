"""Order toolpaths to shorten rapid travel between cuts."""

from typing import TYPE_CHECKING

from geom2d import P

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tcnc.options import SortMethod
    from tcnc.toolpath import Toolpath

ORIGIN = P(0.0, 0.0)


def order_toolpaths(toolpaths: Sequence[Toolpath], method: SortMethod, *, start: P = ORIGIN) -> list[Toolpath]:
    """Return the toolpaths in machining order.

    ``none`` keeps file order. ``nearest`` walks greedily from ``start``,
    picking the toolpath whose nearest entry point is closest, reversing
    open paths when their end is nearer and rotating closed paths to start
    at their nearest vertex.
    """
    if method == "none":
        return list(toolpaths)
    remaining = list(toolpaths)
    ordered: list[Toolpath] = []
    current = start
    while remaining:
        best_index = 0
        best_path, best_distance = oriented_toward(remaining[0], current)
        for index in range(1, len(remaining)):
            candidate, distance = oriented_toward(remaining[index], current)
            if distance < best_distance:
                best_index, best_path, best_distance = index, candidate, distance
        ordered.append(best_path)
        current = best_path.end
        del remaining[best_index]
    return ordered


def oriented_toward(toolpath: Toolpath, point: P) -> tuple[Toolpath, float]:
    """The toolpath oriented to start as near ``point`` as possible, and that distance."""
    if toolpath.closed:
        index = min(range(len(toolpath)), key=lambda i: point.distance2(toolpath[i].p1))
        rotated = toolpath.rotated_to(index)
        return rotated, point.distance(rotated.start)
    to_start = point.distance(toolpath.start)
    to_end = point.distance(toolpath.end)
    if to_end < to_start:
        return toolpath.reversed(), to_end
    return toolpath, to_start
