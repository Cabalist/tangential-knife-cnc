"""SVG to LinuxCNC G-code for an oscillating tangential knife.

Rewritten in 2026 from utlco/utl-tcnc by Claude Zervas (LGPL-3.0).
"""

from importlib.metadata import version

__version__ = version("tangential-knife-cnc")

from tcnc.corners import Cut, JobPlan, OperationPlan, plan_cuts
from tcnc.errors import OptionError, OutputError, PlanError, SvgError, TcncError
from tcnc.gcode import write_program
from tcnc.jobfile import JobFile, load_job_file
from tcnc.options import Job, KnifeOptions, Operation, OperationSettings, Tool
from tcnc.ordering import order_toolpaths
from tcnc.plan import load_document, plan_job, plan_operation, plan_toolpaths, toolpaths_from_document
from tcnc.preview import preview_svg, write_preview
from tcnc.provenance import Provenance
from tcnc.svg import SvgDocument, SvgPath, load_svg
from tcnc.toolpath import Hints, Segment, Toolpath

__all__ = [
    "Cut",
    "Hints",
    "Job",
    "JobFile",
    "JobPlan",
    "KnifeOptions",
    "Operation",
    "OperationPlan",
    "OperationSettings",
    "OptionError",
    "OutputError",
    "PlanError",
    "Provenance",
    "Segment",
    "SvgDocument",
    "SvgError",
    "SvgPath",
    "TcncError",
    "Tool",
    "Toolpath",
    "__version__",
    "load_document",
    "load_job_file",
    "load_svg",
    "order_toolpaths",
    "plan_cuts",
    "plan_job",
    "plan_operation",
    "plan_toolpaths",
    "preview_svg",
    "toolpaths_from_document",
    "write_preview",
    "write_program",
]
