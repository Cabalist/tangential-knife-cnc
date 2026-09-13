"""Command line: ``tcnc INPUT.svg [-o OUT.ngc] [--preview OUT.svg] [options]`` or ``tcnc --job JOB.toml``.

The flat options describe one knife operation. A job file describes any
number of tools and operations; with ``--job`` only the input, output,
preview and debugging options may accompany it.
"""

import argparse
import math
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, override

from geom2d import GeometryError

from tcnc import __version__
from tcnc.errors import OptionError, OutputError, PlanError, SvgError
from tcnc.gcode import write_program
from tcnc.jobfile import load_job_files
from tcnc.options import Job, KnifeOptions, as_job
from tcnc.output import publish
from tcnc.plan import load_document, plan_job
from tcnc.preview import preview_svg

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from tcnc.corners import JobPlan

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
    plan: JobPlan


# Options that describe the single-knife job; they cannot accompany a job file.
KNIFE_OPTIONS = frozenset(
    {
        "--id",
        "--layer",
        "--flip-y",
        "--no-flip-y",
        "--tolerance",
        "--biarc-tolerance",
        "--biarc-max-depth",
        "--output-precision",
        "--xy-feed",
        "--z-feed",
        "--a-feed",
        "--z-safe",
        "--z-depth",
        "--z-step",
        "--tool-wait",
        "--blend-mode",
        "--blend-tolerance",
        "--corner-angle",
        "--overcut",
        "--blade-offset",
        "--blade-width",
        "--a-offset",
        "--oscillation-mode",
        "--spindle-speed",
        "--spindle-wait-on",
        "--path-sort-method",
        "--gcode-comments",
        "--no-gcode-comments",
        "--gcode-line-numbers",
        "--no-gcode-line-numbers",
        "--write-settings",
        "--no-write-settings",
    }
)


def build_parser() -> _Parser:
    """The argument parser; every ``KnifeOptions`` field has an option with the same ``dest``."""
    defaults = KnifeOptions()
    parser = _Parser(
        prog="tcnc",
        description=(
            "Convert an SVG drawing to LinuxCNC G-code for an oscillating tangential knife, "
            "or run a multi-tool job file."
        ),
        allow_abbrev=False,  # an abbreviated knife option must not slip past the --job check
    )
    parser.add_argument("input", type=Path, nargs="?", help="SVG file to cut (a job file may name it instead)")
    parser.add_argument("-o", "--output", type=Path, help="G-code file to write (default: INPUT with .ngc)")
    parser.add_argument("--preview", type=Path, metavar="SVG", help="also write a preview of the cut plan")
    parser.add_argument(
        "--job",
        type=Path,
        metavar="TOML",
        action="append",
        dest="jobs",
        help=(
            "job file with tools and operations (excludes the knife options); repeatable, later files "
            "override earlier ones, e.g. the machine's tools then a layout's operations"
        ),
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        default=[],
        help="run only the operation with this name (repeatable)",
    )
    parser.add_argument(
        "--skip",
        metavar="NAME",
        action="append",
        default=[],
        help="leave out the operation with this name, e.g. mark on a machine without a pen (repeatable)",
    )
    parser.add_argument("--debug", action="store_true", help="show tracebacks on errors")
    parser.add_argument("--version", action="version", version=f"tcnc {__version__}")
    _knife_arguments(parser, defaults)
    return parser


def _knife_arguments(parser: _Parser, defaults: KnifeOptions) -> None:
    """Add the single-knife options (every one of them is listed in ``KNIFE_OPTIONS``)."""

    inp = parser.add_argument_group("input selection")
    inp.add_argument(
        "--id",
        dest="ids",
        action="append",
        default=[],
        metavar="ID",
        help="cut only elements with this id, or clones of it (repeatable)",
    )
    inp.add_argument(
        "--layer",
        dest="layers",
        action="append",
        default=[],
        metavar="NAME",
        help="cut only elements in this Inkscape layer label or group id (repeatable)",
    )

    units = parser.add_argument_group("geometry (all lengths in mm)")
    units.add_argument(
        "--flip-y",
        action=argparse.BooleanOptionalAction,
        default=defaults.flip_y,
        help="put the machine origin at the bottom left (default: on)",
    )
    units.add_argument(
        "--tolerance",
        type=float,
        default=defaults.tolerance,
        help="job resolution: points closer than this coincide, shorter pieces are merged (default: %(default)s)",
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

    machine = parser.add_argument_group("machine (mm; material surface is Z0)")
    machine.add_argument(
        "--xy-feed", type=float, default=defaults.xy_feed, help="XY feed rate, mm/min (default: %(default)s)"
    )
    machine.add_argument(
        "--z-feed", type=float, default=defaults.z_feed, help="Z plunge feed rate, mm/min (default: %(default)s)"
    )
    machine.add_argument(
        "--a-feed", type=float, default=defaults.a_feed, help="A axis feed rate, deg/min (default: %(default)s)"
    )
    machine.add_argument(
        "--z-safe",
        type=float,
        default=defaults.z_safe,
        help="Z height for rapid moves, above the surface (default: %(default)s)",
    )
    machine.add_argument(
        "--z-depth",
        type=float,
        default=defaults.z_depth,
        help="final cutting depth, at or below the surface (default: %(default)s)",
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
        choices=("default", "blend", "exact"),
        default=defaults.blend_mode,
        help="trajectory blending: default (leave the controller's), blend (G64) or exact (G61)",
    )
    machine.add_argument(
        "--blend-tolerance",
        type=float,
        default=defaults.blend_tolerance,
        help="G64 P tolerance when blending (default: %(default)s)",
    )

    knife = parser.add_argument_group("knife (mm)")
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
        choices=("program", "operation", "cut", "off"),
        default=defaults.oscillation_mode,
        help="when to switch the oscillating head; program is the same as operation (default: %(default)s)",
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


def options_from_namespace(ns: argparse.Namespace) -> KnifeOptions:
    """Build ``KnifeOptions`` from parsed arguments, field by field.

    Angles arrive in degrees and are converted to radians here; ``ids`` and
    ``layers`` become tuples. A missing attribute is a programming error
    (the parser defines every field), not user input.
    """
    return KnifeOptions(
        flip_y=ns.flip_y,
        tolerance=ns.tolerance,
        biarc_tolerance=ns.biarc_tolerance,
        biarc_max_depth=ns.biarc_max_depth,
        output_precision=ns.output_precision,
        xy_feed=ns.xy_feed,
        z_feed=ns.z_feed,
        a_feed=ns.a_feed,
        z_safe=ns.z_safe,
        z_depth=ns.z_depth,
        z_step=ns.z_step,
        tool_wait=ns.tool_wait,
        blend_mode=ns.blend_mode,
        blend_tolerance=ns.blend_tolerance,
        corner_angle=math.radians(ns.corner_angle),
        overcut=ns.overcut,
        blade_offset=ns.blade_offset,
        blade_width=ns.blade_width,
        a_offset=math.radians(ns.a_offset),
        oscillation_mode=ns.oscillation_mode,
        spindle_speed=ns.spindle_speed,
        spindle_wait_on=ns.spindle_wait_on,
        sort_method=ns.sort_method,
        gcode_comments=ns.gcode_comments,
        gcode_line_numbers=ns.gcode_line_numbers,
        write_settings=ns.write_settings,
        ids=tuple(str(item) for item in ns.ids),
        layers=tuple(str(item) for item in ns.layers),
    )


def run(  # noqa: PLR0913 - one parameter per file involved
    job: Job | KnifeOptions,
    input_path: Path,
    output_path: Path,
    preview_path: Path | None = None,
    *,
    job_paths: Sequence[Path] = (),
    now: Callable[[], datetime] | None = None,
) -> RunResult:
    """Run ``job`` over ``input_path`` into ``output_path`` (and optionally a preview).

    Both files are generated in memory first and then published as one unit
    (see ``tcnc.output``): a failure leaves the previous files as they were.
    ``job_paths`` are the job files the job came from, if any; no output
    may replace one. ``now`` overrides the clock used for the header's
    creation date (tests).
    """
    _check_distinct_paths(input_path, output_path, preview_path, job_paths)
    resolved = as_job(job)
    document = load_document(input_path, resolved)
    try:
        plan = plan_job(document, resolved)
    except SvgError as exc:
        msg = f"{exc} in {input_path}" if len(resolved.operations) > 1 else f"no cuttable geometry in {input_path}"
        raise SvgError(msg) from exc
    outputs: list[tuple[Path, str]] = [(output_path, write_program(plan, now=now))]
    if preview_path is not None:
        outputs.append((preview_path, preview_svg(plan, page=(document.width, document.height))))
    publish(outputs)
    return RunResult(output_path, preview_path, plan)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    namespace = parser.parse_args(arguments)
    try:
        job, input_path, output_path, preview_path = _job_and_files(namespace, arguments)
        if namespace.only or namespace.skip:
            job = as_job(job).select(only=namespace.only, skip=namespace.skip)
        result = run(job, input_path, output_path, preview_path, job_paths=namespace.jobs or ())
    except OptionError as exc:
        return _fail(EXIT_USAGE, exc, debug=namespace.debug)
    except SvgError as exc:
        return _fail(EXIT_SVG, exc, debug=namespace.debug)
    except (PlanError, GeometryError, OutputError) as exc:
        return _fail(EXIT_PLAN, exc, debug=namespace.debug)
    print(_summary(result))  # noqa: T201
    if result.preview is not None:
        print(f"{result.preview}: preview")  # noqa: T201
    return EXIT_OK


def _job_and_files(
    namespace: argparse.Namespace, arguments: Sequence[str]
) -> tuple[Job | KnifeOptions, Path, Path, Path | None]:
    """The job and the input, output and preview paths, from the flat options or from a job file.

    Raises:
        OptionError: For a missing input, a knife option combined with
            ``--job``, or a job file that cannot be loaded.
    """
    given_input: Path | None = namespace.input
    given_output: Path | None = namespace.output
    given_preview: Path | None = namespace.preview
    if not namespace.jobs:
        if given_input is None:
            msg = "an SVG file is required unless --job names one"
            raise OptionError(msg)
        output = given_output or given_input.with_suffix(".ngc")
        return options_from_namespace(namespace), given_input, output, given_preview
    given = [token.split("=", 1)[0] for token in arguments if token.startswith("-")]
    clashing = sorted({token for token in given if token in KNIFE_OPTIONS})
    if clashing:
        msg = f"{', '.join(clashing)} describe the single-knife job and cannot be used with --job"
        raise OptionError(msg)
    loaded = load_job_files(namespace.jobs)
    input_path = given_input or loaded.input
    if input_path is None:
        msg = "neither the command line nor the job file names an SVG file"
        raise OptionError(msg)
    output_path = given_output or loaded.output or input_path.with_suffix(".ngc")
    return loaded.job, input_path, output_path, given_preview or loaded.preview


def _summary(result: RunResult) -> str:
    operations = result.plan.operations
    if len(operations) == 1:
        cuts = len(operations[0].cuts)
        plural = "s" if cuts != 1 else ""
        return f"{result.output}: {cuts} cut{plural}, {result.plan.cut_length:.3f} mm of cutting"
    parts = [
        f"{operation.settings.name} {len(operation.cuts)} cut{'s' if len(operation.cuts) != 1 else ''} {operation.cut_length:.3f} mm"
        for operation in operations
    ]
    return f"{result.output}: {', '.join(parts)}"


def _fail(code: int, exc: Exception, *, debug: bool) -> int:
    if debug:
        traceback.print_exc()
    print(f"tcnc: error: {exc}", file=sys.stderr)  # noqa: T201
    return code


def _check_distinct_paths(
    input_path: Path, output_path: Path, preview_path: Path | None, job_paths: Sequence[Path] = ()
) -> None:
    named = {"input": input_path.resolve(), "output": output_path.resolve()}
    if preview_path is not None:
        named["preview"] = preview_path.resolve()
    for index, job_path in enumerate(job_paths, 1):
        named[f"job file {index}" if len(job_paths) > 1 else "job file"] = job_path.resolve()
    seen: dict[Path, str] = {}
    for role, resolved in named.items():
        if resolved in seen:
            msg = f"{role} and {seen[resolved]} are the same file: {resolved}"
            raise OptionError(msg)
        seen[resolved] = role
