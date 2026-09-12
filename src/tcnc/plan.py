"""The job pipeline shared by the command line and the library API."""

from typing import TYPE_CHECKING

from tcnc.corners import JobPlan, OperationPlan, as_settings, plan_cuts
from tcnc.errors import SvgError
from tcnc.offset import offset_toolpath
from tcnc.options import Job, KnifeOptions, OperationSettings, as_job
from tcnc.ordering import order_toolpaths
from tcnc.svg import SvgDocument, load_svg
from tcnc.toolpath import Toolpath

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def load_document(path: Path | str, job: Job | KnifeOptions) -> SvgDocument:
    """Load an SVG file with every loader setting taken from the job; nothing is selected yet."""
    resolved = as_job(job)
    return load_svg(
        path, flip_y=resolved.flip_y, tolerance=resolved.tolerance, curve_tolerance=resolved.biarc_tolerance
    )


def toolpaths_from_document(
    document: SvgDocument, job: Job | KnifeOptions, settings: OperationSettings | None = None
) -> list[Toolpath]:
    """Convert the paths an operation selects into toolpaths, dropping empty ones.

    ``settings`` names the operation; left out, the job must have exactly one.
    """
    resolved = as_job(job)
    operation = as_settings(resolved) if settings is None else settings
    toolpaths: list[Toolpath] = []
    for svg_path in document.select(ids=operation.ids, layers=operation.layers):
        toolpath = Toolpath.from_geometry(
            svg_path.geometry,
            biarc_tolerance=resolved.biarc_tolerance,
            biarc_max_depth=resolved.biarc_max_depth,
            tolerance=resolved.tolerance,
            source_id=svg_path.source_id,
        )
        if toolpath is not None:
            toolpaths.append(toolpath)
    return toolpaths


def plan_operation(
    toolpaths: Sequence[Toolpath], settings: OperationSettings, job: Job | KnifeOptions
) -> OperationPlan:
    """Order, compensate and split toolpaths into the cuts of one operation."""
    resolved = as_job(job)
    ordered = order_toolpaths(toolpaths, settings.sort_method, corner_angle=settings.corner_angle)
    if settings.blade_offset > 0.0:
        ordered = [
            offset_toolpath(toolpath, settings.blade_offset, min_arc_chord=resolved.output_resolution)
            for toolpath in ordered
        ]
    return plan_cuts(ordered, settings)


def plan_job(document: SvgDocument, job: Job | KnifeOptions) -> JobPlan:
    """Plan every operation of the job over a loaded document.

    Raises:
        SvgError: When an operation selects no cuttable geometry.
    """
    resolved = as_job(job)
    operations: list[OperationPlan] = []
    for settings in resolved.settings:
        toolpaths = toolpaths_from_document(document, resolved, settings)
        if not toolpaths:
            msg = f"operation {settings.name!r} selects no cuttable geometry"
            raise SvgError(msg)
        operations.append(plan_operation(toolpaths, settings, resolved))
    return JobPlan(resolved, tuple(operations))


def plan_toolpaths(toolpaths: Sequence[Toolpath], job: Job | KnifeOptions) -> JobPlan:
    """Plan pre-built toolpaths under a single-operation job (the library shortcut)."""
    resolved = as_job(job)
    settings = as_settings(resolved)
    return JobPlan(resolved, (plan_operation(toolpaths, settings, resolved),))
