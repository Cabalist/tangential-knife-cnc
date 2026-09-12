"""The job pipeline shared by the command line and the library API."""

from typing import TYPE_CHECKING

from tcnc.corners import CutPlan, plan_cuts
from tcnc.offset import offset_toolpath
from tcnc.ordering import order_toolpaths
from tcnc.svg import SvgDocument, load_svg
from tcnc.toolpath import Toolpath

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tcnc.options import KnifeOptions


def load_document(path: Path | str, options: KnifeOptions) -> SvgDocument:
    """Load an SVG file with every loader setting taken from ``options``."""
    return load_svg(
        path,
        flip_y=options.flip_y,
        ids=options.ids,
        layers=options.layers,
        tolerance=options.tolerance,
        curve_tolerance=options.biarc_tolerance,
    )


def toolpaths_from_document(document: SvgDocument, options: KnifeOptions) -> list[Toolpath]:
    """Convert every path of a loaded document into a toolpath, dropping empty ones."""
    toolpaths: list[Toolpath] = []
    for svg_path in document.paths:
        toolpath = Toolpath.from_geometry(
            svg_path.geometry,
            biarc_tolerance=options.biarc_tolerance,
            biarc_max_depth=options.biarc_max_depth,
            tolerance=options.tolerance,
            source_id=svg_path.source_id,
        )
        if toolpath is not None:
            toolpaths.append(toolpath)
    return toolpaths


def plan_job(toolpaths: Sequence[Toolpath], options: KnifeOptions) -> CutPlan:
    """Order, compensate and split toolpaths into the cut plan the writer consumes."""
    ordered = order_toolpaths(toolpaths, options.sort_method, corner_angle=options.corner_angle)
    if options.blade_offset > 0.0:
        ordered = [
            offset_toolpath(toolpath, options.blade_offset, min_arc_chord=options.output_resolution)
            for toolpath in ordered
        ]
    return plan_cuts(ordered, options)
