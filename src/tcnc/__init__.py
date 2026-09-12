"""SVG to LinuxCNC G-code for an oscillating tangential knife.

Rewritten in 2026 from utlco/utl-tcnc by Claude Zervas (LGPL-3.0).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("utl-tcnc")
except PackageNotFoundError:  # pragma: no cover - source checkout without metadata
    __version__ = "0.0.0"

from tcnc.corners import Cut, CutPlan, plan_cuts
from tcnc.errors import OptionError, PlanError, SvgError, TcncError
from tcnc.gcode import write_program
from tcnc.options import KnifeOptions
from tcnc.ordering import order_toolpaths
from tcnc.preview import preview_svg, write_preview
from tcnc.svg import SvgDocument, SvgPath, load_svg
from tcnc.toolpath import Hints, Segment, Toolpath

__all__ = [
    "Cut",
    "CutPlan",
    "Hints",
    "KnifeOptions",
    "OptionError",
    "PlanError",
    "Segment",
    "SvgDocument",
    "SvgError",
    "SvgPath",
    "TcncError",
    "Toolpath",
    "__version__",
    "load_svg",
    "order_toolpaths",
    "plan_cuts",
    "preview_svg",
    "write_preview",
    "write_program",
]
