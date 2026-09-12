"""Command line: ``tcnc INPUT.svg [-o OUT.ngc] [--preview OUT.svg] [options]``."""

import argparse
import math
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, override

from geom2d import GeometryError

from tcnc import __version__
from tcnc.corners import CutPlan, plan_cuts
from tcnc.errors import OptionError, PlanError, SvgError
from tcnc.gcode import write_program
from tcnc.offset import offset_toolpath
from tcnc.options import KnifeOptions
from tcnc.ordering import order_toolpaths
from tcnc.preview import write_preview
from tcnc.svg import load_svg
from tcnc.toolpath import Toolpath

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_SVG = 2
EXIT_PLAN = 3


class _Parser(argparse.ArgumentParser):
    """argparse with the usage exit code this tool documents."""

    @override
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


@dataclass(frozen=True, slots=True)
class RunResult:
    """What a run produced."""

    output: Path
    preview: Path | None
    plan: CutPlan


def build_parser() -> _Parser:
    """The argument parser; option names match ``KnifeOptions`` field names."""
    defaults = KnifeOptions()
    parser = _Parser(
        prog="tcnc", description="Convert an SVG drawing to LinuxCNC G-code for an oscillating tangential knife."
    )
    parser.add_argument("input", type=Path, help="SVG file to cut")
    parser.add_argument("-o", "--output", type=Path, help="G-code file to write (default: INPUT with .ngc)")
    parser.add_argument("--preview", type=Path, metavar="SVG", help="also write a preview of the cut plan")
    parser.add_argument("--debug", action="store_true", help="show tracebacks on errors")
    parser.add_argument("--version", action="version", version=f"tcnc {__version__}")

    inp = parser.add_argument_group("input selection")
    inp.add_argument(
        "--id",
        dest="ids",
        action="append",
        default=[],
        metavar="ID",
        help="cut only elements with this id (repeatable)",
    )
    inp.add_argument(
        "--layer",
        dest="layers",
        action="append",
        default=[],
        metavar="NAME",
        help="cut only elements in this Inkscape layer label or group id (repeatable)",
    )

    units = parser.add_argument_group("units and geometry")
    units.add_argument(
        "--gcode-units",
        choices=("in", "mm"),
        default=defaults.gcode_units,
        help="G-code and option length units (default: %(default)s)",
    )
    units.add_argument(
        "--flip-y",
        action=argparse.BooleanOptionalAction,
        default=defaults.flip_y,
        help="put the machine origin at the bottom left (default: on)",
    )
    units.add_argument(
        "--tolerance", type=float, default=defaults.tolerance, help="geometry tolerance (default: %(default)s)"
    )
    units.add_argument(
        "--biarc-tolerance",
        type=float,
        default=defaults.biarc_tolerance,
        help="curve to biarc fit tolerance (default: %(default)s)",
    )
    units.add_argument(
        "--biarc-max-depth",
        type=int,
        default=defaults.biarc_max_depth,
        help="curve subdivision limit (default: %(default)s)",
    )
    units.add_argument(
        "--output-precision",
        type=int,
        default=defaults.output_precision,
        help="decimal places in G-code words (default: %(default)s)",
    )

    machine = parser.add_argument_group("machine")
    machine.add_argument(
        "--xy-feed", type=float, default=defaults.xy_feed, help="XY feed rate, units/min (default: %(default)s)"
    )
    machine.add_argument(
        "--z-feed", type=float, default=defaults.z_feed, help="Z plunge feed rate, units/min (default: %(default)s)"
    )
    machine.add_argument(
        "--a-feed", type=float, default=defaults.a_feed, help="A axis feed rate, deg/min (default: %(default)s)"
    )
    machine.add_argument(
        "--z-safe", type=float, default=defaults.z_safe, help="Z height for rapid moves (default: %(default)s)"
    )
    machine.add_argument(
        "--z-depth", type=float, default=defaults.z_depth, help="final cutting depth (default: %(default)s)"
    )
    machine.add_argument(
        "--z-step",
        type=float,
        default=defaults.z_step,
        help="depth per pass, 0 for a single pass (default: %(default)s)",
    )
    machine.add_argument(
        "--tool-wait",
        type=float,
        default=defaults.tool_wait,
        help="seconds to dwell after plunge and lift (default: %(default)s)",
    )
    machine.add_argument(
        "--blend-mode",
        choices=("", "blend", "exact"),
        default=defaults.blend_mode,
        help="trajectory blending: '' (controller default), blend (G64) or exact (G61)",
    )
    machine.add_argument(
        "--blend-tolerance",
        type=float,
        default=defaults.blend_tolerance,
        help="G64 P tolerance when blending (default: %(default)s)",
    )

    knife = parser.add_argument_group("knife")
    knife.add_argument(
        "--corner-angle",
        type=float,
        default=math.degrees(defaults.corner_angle),
        metavar="DEG",
        help="lift and re-plunge at heading changes above this (default: %(default)s)",
    )
    knife.add_argument(
        "--overcut",
        type=float,
        default=defaults.overcut,
        help="extend each cut this far past its ends (default: %(default)s)",
    )
    knife.add_argument(
        "--blade-offset",
        type=float,
        default=defaults.blade_offset,
        help="blade trail offset behind the axis, 0 to disable (default: %(default)s)",
    )
    knife.add_argument(
        "--blade-width",
        type=float,
        default=defaults.blade_width,
        help="blade width, used for preview heading ticks (default: %(default)s)",
    )
    knife.add_argument(
        "--a-offset",
        type=float,
        default=math.degrees(defaults.a_offset),
        metavar="DEG",
        help="blade mounting angle added to every A value (default: %(default)s)",
    )
    knife.add_argument(
        "--oscillation-mode",
        choices=("program", "cut", "off"),
        default=defaults.oscillation_mode,
        help="when to switch the oscillating head (default: %(default)s)",
    )
    knife.add_argument(
        "--spindle-speed",
        type=int,
        default=defaults.spindle_speed,
        help="S word for the head, 0 to omit (default: %(default)s)",
    )
    knife.add_argument(
        "--spindle-wait-on",
        type=float,
        default=defaults.spindle_wait_on,
        help="seconds to wait after switching the head on (default: %(default)s)",
    )

    paths = parser.add_argument_group("paths and output")
    paths.add_argument(
        "--path-sort-method",
        dest="sort_method",
        choices=("none", "nearest"),
        default=defaults.sort_method,
        help="cut order: file order or nearest neighbour (default: %(default)s)",
    )
    paths.add_argument(
        "--gcode-comments",
        action=argparse.BooleanOptionalAction,
        default=defaults.gcode_comments,
        help="write comments into the G-code (default: on)",
    )
    paths.add_argument(
        "--gcode-line-numbers",
        action=argparse.BooleanOptionalAction,
        default=defaults.gcode_line_numbers,
        help="number the G-code lines (default: off)",
    )
    paths.add_argument(
        "--write-settings",
        action=argparse.BooleanOptionalAction,
        default=defaults.write_settings,
        help="list every option in the G-code header (default: off)",
    )
    return parser


def run(
    options: KnifeOptions,
    input_path: Path,
    output_path: Path,
    preview_path: Path | None = None,
    *,
    now: Callable[[], datetime] | None = None,
) -> RunResult:
    """Cut ``input_path`` into ``output_path`` (and optionally a preview).

    ``now`` overrides the clock used for the header's creation date (tests).
    """
    document = load_svg(
        input_path,
        unit_scale=options.unit_scale_from_px,
        flip_y=options.flip_y,
        ids=options.ids,
        layers=options.layers,
        tolerance=options.tolerance,
    )
    toolpaths: list[Toolpath] = []
    for svg_path in document.paths:
        toolpath = Toolpath.from_geometry(
            svg_path.geometry,
            biarc_tolerance=options.biarc_tolerance,
            biarc_max_depth=options.biarc_max_depth,
            source_id=svg_path.source_id,
        )
        if toolpath is not None:
            toolpaths.append(toolpath)
    if not toolpaths:
        msg = f"no cuttable geometry in {input_path}"
        raise SvgError(msg)
    toolpaths = order_toolpaths(toolpaths, options.sort_method)
    if options.blade_offset > 0.0:
        min_chord = 10.0**-options.output_precision
        toolpaths = [offset_toolpath(tp, options.blade_offset, min_arc_chord=min_chord) for tp in toolpaths]
    plan = plan_cuts(toolpaths, options)
    text = write_program(plan, now=now)
    _write_atomically(output_path, text)
    if preview_path is not None:
        write_preview(plan, preview_path, page=(document.width, document.height))
    return RunResult(output_path, preview_path, plan)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    parser = build_parser()
    namespace = parser.parse_args(argv)
    try:
        options = KnifeOptions.from_namespace(namespace)
        output = namespace.output or namespace.input.with_suffix(".ngc")
        result = run(options, namespace.input, output, namespace.preview)
    except OptionError as exc:
        return _fail(EXIT_USAGE, exc, debug=namespace.debug)
    except SvgError as exc:
        return _fail(EXIT_SVG, exc, debug=namespace.debug)
    except (PlanError, GeometryError) as exc:
        return _fail(EXIT_PLAN, exc, debug=namespace.debug)
    cuts = len(result.plan.cuts)
    plural = "s" if cuts != 1 else ""
    summary = f"{result.output}: {cuts} cut{plural}, {result.plan.cut_length:.3f} {options.gcode_units} of cutting"
    print(summary)  # noqa: T201
    if result.preview is not None:
        print(f"{result.preview}: preview")  # noqa: T201
    return EXIT_OK


def _fail(code: int, exc: Exception, *, debug: bool) -> int:
    if debug:
        traceback.print_exc()
    print(f"tcnc: error: {exc}", file=sys.stderr)  # noqa: T201
    return code


def _write_atomically(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
