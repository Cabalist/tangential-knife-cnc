"""SVG to LinuxCNC G-code for an oscillating tangential knife.

Rewritten in 2026 from utlco/utl-tcnc by Claude Zervas (LGPL-3.0).
"""

from importlib.metadata import version

__version__ = version("tangential-knife-cnc")

from tcnc.corners import Cut, CutPlan, plan_cuts
from tcnc.errors import OptionError, OutputError, PlanError, SvgError, TcncError
from tcnc.gcode import write_program
from tcnc.options import KnifeOptions
from tcnc.ordering import order_toolpaths
from tcnc.plan import load_document, plan_job, toolpaths_from_document
from tcnc.preview import preview_svg, write_preview
from tcnc.svg import SvgDocument, SvgPath, load_svg
from tcnc.toolpath import Hints, Segment, Toolpath

__all__ = [
    "Cut",
    "CutPlan",
    "Hints",
    "KnifeOptions",
    "OptionError",
    "OutputError",
    "PlanError",
    "Segment",
    "SvgDocument",
    "SvgError",
    "SvgPath",
    "TcncError",
    "Toolpath",
    "__version__",
    "load_document",
    "load_svg",
    "order_toolpaths",
    "plan_cuts",
    "plan_job",
    "preview_svg",
    "toolpaths_from_document",
    "write_preview",
    "write_program",
]
