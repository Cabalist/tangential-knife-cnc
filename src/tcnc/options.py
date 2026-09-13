"""Job settings: tools, operations and the job that ties them together.

Internal units are millimetres for lengths (the G-code is metric, ``G21``),
seconds for time and radians for angles. The command line and the job file
convert degrees to radians; nothing downstream converts anything.

A ``Job`` is the canonical model: job-wide settings, the ``Tool`` records
of the machine's tool table, and the ordered ``Operation`` records, each of
which cuts one selection with one tool. Settings that exist at several
levels resolve operation, then tool, then job. ``Job.resolve`` turns an
operation into an ``OperationSettings`` record with every value final,
validated where it is used, including the pass schedule. ``KnifeOptions``
is the flat single-tool record of the command line; it builds a
one-operation job and validates through it.

``tolerance`` is the job's distance resolution: two points closer than it
are the same point, pieces shorter than it carry no geometry, and every
geom2d call that takes a tolerance receives it. It must not be finer than
geom2d's own numerical floor (``geom2d.EPSILON``).

Machine contract encoded here: the material surface is Z = 0, cutting
depths are below it, and every safe height clears the surface and every
pass. The machine reads words rounded to ``output_precision`` decimals, so
the heights, steps and feeds are validated on their rounded values, passes
are planned on the grid of representable depths so that no written
increment exceeds ``z_step``, and the number of passes is bounded
(``MAX_PASSES``). Tool offsets are the controller's (``G43``); nothing here
shifts geometry for a tool. ``Job.tool_change_z`` is the one value in
machine coordinates.
"""

import dataclasses
import math
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Literal

from geom2d import const

from tcnc.errors import OptionError

if TYPE_CHECKING:
    from collections.abc import Sequence

type ToolKind = Literal["knife", "creaser", "pen"]
type BlendMode = Literal["default", "blend", "exact"]
type OscillationMode = Literal["operation", "cut", "off"]
type KnifeOscillationMode = Literal["program", "operation", "cut", "off"]
type SortMethod = Literal["none", "nearest"]

ANGLE_FIELDS = frozenset({"corner_angle", "a_offset"})
MAX_PASSES = 1000
MAX_OUTPUT_PRECISION = 9
KIND_CORNER_ANGLE: dict[str, float] = {"knife": math.radians(15.0), "creaser": math.radians(10.0)}
"""Default lift threshold per tool kind; the pen never lifts at corners."""
_PASS_SLACK = 1e-9
# Grid cells beyond which a float no longer counts them exactly: a step that coarse never subdivides a
# schedulable depth, and a depth that deep cannot be scheduled.
_MAX_GRID_UNITS = 2**53


def _require(*, condition: bool, message: str) -> None:
    if not condition:
        raise OptionError(message)


def _finite(record: Tool | Operation | Job | KnifeOptions, label: str) -> None:
    """Every float field of ``record`` is a finite number."""
    for field in fields(record):
        value = getattr(record, field.name)
        if isinstance(value, float):
            _require(
                condition=math.isfinite(value), message=f"{label}: {field.name} must be a finite number, got {value!r}"
            )


def _angle_text(name: str, value: object) -> str:
    if name in ANGLE_FIELDS and isinstance(value, float):
        return f"{math.degrees(value):g} deg"
    return str(value)


# ----- tools ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Tool:
    """A physical tool in the machine's tool table.

    ``number`` is the ``T`` number; ``None`` means the tool is mounted
    already and no tool change is written. ``corner_angle`` and
    ``oscillation`` default per kind; the feeds and ``tool_wait`` default
    to the job's values. ``z_depth`` and ``z_step`` are this tool's
    defaults for its operations (the material's cut and score depths live
    naturally with the tool). The kind rules: a pen has no blade offset,
    no corner threshold and no passes, and does not oscillate; a creaser
    does not oscillate.
    """

    name: str
    kind: ToolKind
    number: int | None = None
    a_offset: float = 0.0
    corner_angle: float | None = None
    blade_offset: float = 0.0
    blade_width: float = 0.0
    oscillation: bool | None = None
    spindle_speed: int = 0
    spindle_wait_on: float = 0.0
    z_depth: float | None = None
    z_step: float | None = None
    xy_feed: float | None = None
    z_feed: float | None = None
    a_feed: float | None = None
    tool_wait: float | None = None

    def __post_init__(self) -> None:
        """Validate the tool's own values and the rules of its kind."""
        label = f"tool {self.name!r}"
        _require(condition=bool(self.name), message="a tool needs a name")
        _require(
            condition=self.kind in ("knife", "creaser", "pen"),
            message=f"{label}: kind must be 'knife', 'creaser' or 'pen', got {self.kind!r}",
        )
        _finite(self, label)
        _require(
            condition=self.number is None or self.number >= 1,
            message=f"{label}: number must be a positive T number or absent, got {self.number!r}",
        )
        _require(
            condition=self.corner_angle is None or 0.0 < self.corner_angle <= math.pi,
            message=f"{label}: corner_angle must be in (0, 180] degrees",
        )
        for name in ("blade_offset", "blade_width", "spindle_wait_on"):
            _require(condition=getattr(self, name) >= 0.0, message=f"{label}: {name} must be >= 0")
        _require(condition=self.spindle_speed >= 0, message=f"{label}: spindle_speed must be >= 0")
        for name in ("xy_feed", "z_feed", "a_feed"):
            value = getattr(self, name)
            _require(condition=value is None or value > 0.0, message=f"{label}: {name} must be > 0")
        _require(condition=self.tool_wait is None or self.tool_wait >= 0.0, message=f"{label}: tool_wait must be >= 0")
        _require(condition=self.z_depth is None or self.z_depth < 0.0, message=f"{label}: z_depth must be < 0")
        _require(condition=self.z_step is None or self.z_step >= 0.0, message=f"{label}: z_step must be >= 0")
        if self.kind == "pen":
            _require(condition=self.blade_offset == 0.0, message=f"{label}: a pen has no blade_offset")
            _require(condition=not self.z_step, message=f"{label}: a pen draws in one pass; z_step must be 0")
            _require(
                condition=self.corner_angle is None,
                message=f"{label}: a pen never lifts at corners; corner_angle does not apply",
            )
            _require(condition=self.oscillation is not True, message=f"{label}: a pen does not oscillate")
        if self.kind == "creaser":
            _require(condition=self.oscillation is not True, message=f"{label}: a creaser does not oscillate")

    @property
    def tangential(self) -> bool:
        """True when the A axis follows the path heading with this tool."""
        return self.kind != "pen"

    @property
    def oscillates(self) -> bool:
        """True when the tool is switched with ``M3``/``M5`` (the kind default unless set)."""
        return self.kind == "knife" if self.oscillation is None else self.oscillation

    @property
    def default_corner_angle(self) -> float | None:
        """The lift threshold this tool brings, or ``None`` for a tool that never lifts."""
        if self.kind == "pen":
            return None
        return KIND_CORNER_ANGLE[self.kind] if self.corner_angle is None else self.corner_angle


# ----- operations ----------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Operation:
    """One pass of one tool over one selection of the artwork.

    ``tool`` names a tool of the job, or a tool kind when the job has
    exactly one tool of that kind. ``ids`` and ``layers`` select as the
    command line does; both empty means everything visible. Values left
    ``None`` resolve through the tool and the job (``z_depth`` must be set
    on one of the two); ``oscillation_mode`` defaults to ``"operation"``
    for a tool that oscillates and to ``"off"`` otherwise.
    """

    tool: str
    z_depth: float | None = None
    name: str | None = None
    ids: tuple[str, ...] = ()
    layers: tuple[str, ...] = ()
    z_step: float | None = None
    z_safe: float | None = None
    overcut: float = 0.0
    corner_angle: float | None = None
    sort_method: SortMethod = "none"
    oscillation_mode: OscillationMode | None = None
    xy_feed: float | None = None
    z_feed: float | None = None
    a_feed: float | None = None
    tool_wait: float | None = None

    def __post_init__(self) -> None:
        """Validate the operation's own values; kind rules and rounding are checked when the job resolves it."""
        label = f"operation {self.name!r}" if self.name else f"operation with tool {self.tool!r}"
        _require(condition=bool(self.tool), message=f"{label}: an operation needs a tool")
        _finite(self, label)
        _require(
            condition=self.z_depth is None or self.z_depth < 0.0,
            message=f"{label}: z_depth is measured down from the material surface at Z=0 and must be < 0",
        )
        _require(
            condition=self.z_step is None or self.z_step >= 0.0,
            message=f"{label}: z_step must be >= 0 (0 means a single pass)",
        )
        _require(condition=self.z_safe is None or self.z_safe > 0.0, message=f"{label}: z_safe must clear the surface")
        _require(condition=self.overcut >= 0.0, message=f"{label}: overcut must be >= 0")
        _require(
            condition=self.corner_angle is None or 0.0 < self.corner_angle <= math.pi,
            message=f"{label}: corner_angle must be in (0, 180] degrees",
        )
        _require(
            condition=self.sort_method in ("none", "nearest"),
            message=f"{label}: sort_method must be 'none' or 'nearest', got {self.sort_method!r}",
        )
        _require(
            condition=self.oscillation_mode in (None, "operation", "cut", "off"),
            message=f"{label}: oscillation_mode must be 'operation', 'cut' or 'off', got {self.oscillation_mode!r}",
        )
        for name in ("xy_feed", "z_feed", "a_feed"):
            value = getattr(self, name)
            _require(condition=value is None or value > 0.0, message=f"{label}: {name} must be > 0")
        _require(condition=self.tool_wait is None or self.tool_wait >= 0.0, message=f"{label}: tool_wait must be >= 0")


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationSettings:
    """An operation with every value resolved and validated, ready for planning and writing."""

    name: str
    tool: Tool
    ids: tuple[str, ...]
    layers: tuple[str, ...]
    z_depth: float
    z_step: float
    z_safe: float
    overcut: float
    corner_angle: float | None
    sort_method: SortMethod
    oscillation_mode: OscillationMode
    xy_feed: float
    z_feed: float
    a_feed: float
    tool_wait: float
    pass_depths: tuple[float, ...]

    @property
    def pass_count(self) -> int:
        """Number of passes written for every cut."""
        return len(self.pass_depths)

    @property
    def tangential(self) -> bool:
        """True when the A axis follows the path heading."""
        return self.tool.tangential

    @property
    def blade_offset(self) -> float:
        """The tool's trail offset."""
        return self.tool.blade_offset

    @property
    def a_offset(self) -> float:
        """The tool's mounting angle."""
        return self.tool.a_offset

    @property
    def oscillates(self) -> bool:
        """True when this operation switches the head with ``M3``/``M5``."""
        return self.oscillation_mode != "off"


# ----- the job -------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Job:
    """Job-wide settings, the tools, and the ordered operations.

    ``tool_change_z`` is a height in *machine* coordinates (``G53``), the
    one frame no work offset or tool length compensation can shift: when
    set, the program goes there before every tool change; when ``None``,
    no retract is written before a change and the controller's own change
    procedure (``TOOL_CHANGE_QUILL_UP``) is expected to lift the head.
    """

    tools: tuple[Tool, ...]
    operations: tuple[Operation, ...]
    flip_y: bool = True
    tolerance: float = 0.01
    biarc_tolerance: float = 0.01
    biarc_max_depth: int = 8
    output_precision: int = 3
    z_safe: float = 10.0
    tool_change_z: float | None = None
    blend_mode: BlendMode = "default"
    blend_tolerance: float = 0.0
    gcode_comments: bool = True
    gcode_line_numbers: bool = False
    write_settings: bool = False
    xy_feed: float = 250.0
    z_feed: float = 250.0
    a_feed: float = 60.0
    tool_wait: float = 0.0

    def __post_init__(self) -> None:
        """Validate the job-wide values, the tool and operation structure, and every resolved operation."""
        _finite(self, "job")
        floor = const.EPSILON
        _require(
            condition=self.tolerance >= floor,
            message=f"tolerance must be at least geom2d's EPSILON ({floor!r}), got {self.tolerance!r}",
        )
        _require(
            condition=self.biarc_tolerance >= floor,
            message=f"biarc_tolerance must be at least geom2d's EPSILON ({floor!r}), got {self.biarc_tolerance!r}",
        )
        _require(
            condition=self.biarc_max_depth >= 0, message=f"biarc_max_depth must be >= 0, got {self.biarc_max_depth!r}"
        )
        _require(
            condition=0 <= self.output_precision <= MAX_OUTPUT_PRECISION,
            message=f"output_precision must be 0..{MAX_OUTPUT_PRECISION}, got {self.output_precision!r}",
        )
        _require(
            condition=self.z_safe > 0.0, message=f"z_safe must clear the material surface at Z=0, got {self.z_safe!r}"
        )
        for name in ("xy_feed", "z_feed", "a_feed"):
            value = getattr(self, name)
            _require(condition=value > 0.0, message=f"{name} must be > 0, got {value!r}")
        _require(condition=self.tool_wait >= 0.0, message=f"tool_wait must be >= 0 seconds, got {self.tool_wait!r}")
        _require(
            condition=self.blend_mode in ("default", "blend", "exact"),
            message=f"blend_mode must be 'default', 'blend' or 'exact', got {self.blend_mode!r}",
        )
        _require(
            condition=self.blend_tolerance >= 0.0, message=f"blend_tolerance must be >= 0, got {self.blend_tolerance!r}"
        )
        places = self.output_precision
        _require(
            condition=self.blend_tolerance == 0.0 or round(self.blend_tolerance, places) > 0.0,
            message=f"blend_tolerance {self.blend_tolerance!r} rounds to zero at {places} decimals",
        )
        self._check_structure()
        for index, operation in enumerate(self.operations):
            self._resolve(operation, index)

    def _check_structure(self) -> None:
        _require(
            condition=bool(self.tools),
            message="a job needs at least one tool (they usually come from the operator's machine job file)",
        )
        _require(condition=bool(self.operations), message="a job needs at least one operation")
        names = [tool.name for tool in self.tools]
        _require(condition=len(set(names)) == len(names), message=f"tool names must be unique, got {names!r}")
        numbers = [tool.number for tool in self.tools if tool.number is not None]
        _require(condition=len(set(numbers)) == len(numbers), message=f"tool numbers must be unique, got {numbers!r}")
        unnumbered = [tool.name for tool in self.tools if tool.number is None]
        _require(
            condition=len(unnumbered) <= 1,
            message=f"at most one tool may be mounted without a number, got {unnumbered!r}",
        )
        names = [self.tool(operation.tool).name for operation in self.operations]
        # An unnumbered tool is the one already mounted: it can only be used before any tool change.
        first = names[0]
        changed = False
        for name in names:
            if name != first:
                changed = True
            if self.tool(name).number is None and (changed or name != first):
                msg = (
                    f"tool {name!r} has no number, so the program cannot change to it; "
                    "an unnumbered tool can only be used by the leading operations"
                )
                raise OptionError(msg)

    def select(self, *, only: Sequence[str] = (), skip: Sequence[str] = ()) -> Job:
        """The same job with only the operations named in ``only`` (all when empty), minus those in ``skip``.

        Operations are matched by their resolved names (``op N`` for an
        unnamed one) and keep the job's order.

        Raises:
            OptionError: For a name the job does not have, or when nothing is left.
        """
        names = [operation.name or f"op {index + 1}" for index, operation in enumerate(self.operations)]
        unknown = [name for name in (*only, *skip) if name not in names]
        _require(
            condition=not unknown,
            message=f"unknown operation(s) {', '.join(unknown)}; the job has {', '.join(names)}",
        )
        kept = tuple(
            operation
            for name, operation in zip(names, self.operations, strict=True)
            if (not only or name in only) and name not in skip
        )
        _require(condition=bool(kept), message=f"no operation is left to run; the job has {', '.join(names)}")
        return dataclasses.replace(self, operations=kept)

    def tool(self, name: str) -> Tool:
        """The tool called ``name``, or the job's only tool of that kind when no tool has the name.

        Raises:
            OptionError: When neither matches, or the kind is ambiguous.
        """
        for tool in self.tools:
            if tool.name == name:
                return tool
        of_kind = [tool for tool in self.tools if tool.kind == name]
        if len(of_kind) == 1:
            return of_kind[0]
        if of_kind:
            msg = f"several tools are of kind {name!r} ({', '.join(t.name for t in of_kind)}); name one of them"
            raise OptionError(msg)
        msg = f"no tool named {name!r} (and no tool of that kind)"
        raise OptionError(msg)

    @property
    def output_resolution(self) -> float:
        """The smallest length difference the G-code words can express."""
        return 10.0**-self.output_precision

    @property
    def settings(self) -> tuple[OperationSettings, ...]:
        """Every operation resolved, in order."""
        return tuple(self._resolve(operation, index) for index, operation in enumerate(self.operations))

    def resolve(self, operation: Operation) -> OperationSettings:
        """Resolve one of the job's operations."""
        return self._resolve(operation, self.operations.index(operation))

    def _resolve(self, operation: Operation, index: int) -> OperationSettings:
        tool = self.tool(operation.tool)
        name = operation.name or f"op {index + 1}"
        label = f"operation {name!r}"

        def pick(field: str) -> float:
            value = getattr(operation, field)
            if value is None:
                value = getattr(tool, field)
            if value is None:
                value = getattr(self, field)
            return float(value)

        z_safe = self.z_safe if operation.z_safe is None else operation.z_safe
        z_depth = tool.z_depth if operation.z_depth is None else operation.z_depth
        _require(
            condition=z_depth is not None,
            message=f"{label}: z_depth is set neither on the operation nor on tool {tool.name!r}",
        )
        assert z_depth is not None
        z_step = operation.z_step if operation.z_step is not None else (tool.z_step or 0.0)
        if tool.kind == "pen":
            _require(
                condition=operation.corner_angle is None,
                message=f"{label}: a pen never lifts; corner_angle does not apply",
            )
            _require(condition=operation.overcut == 0.0, message=f"{label}: a pen has no overcut")
            _require(condition=z_step == 0.0, message=f"{label}: a pen draws in one pass; z_step must be 0")
            corner_angle = None
        else:
            corner_angle = operation.corner_angle if operation.corner_angle is not None else tool.default_corner_angle
        if operation.oscillation_mode is None:
            mode: OscillationMode = "operation" if tool.oscillates else "off"
        else:
            mode = operation.oscillation_mode
            _require(
                condition=mode == "off" or tool.oscillates,
                message=f"{label}: tool {tool.name!r} does not oscillate; oscillation_mode must be 'off'",
            )
        places, resolution = self.output_precision, self.output_resolution
        _require(
            condition=round(z_safe, places) > 0.0,
            message=f"{label}: z_safe {z_safe!r} rounds to Z0 at {places} decimals; it must be at least {resolution:g}",
        )
        _require(
            condition=round(z_depth, places) < 0.0,
            message=f"{label}: z_depth {z_depth!r} rounds to Z0 at {places} decimals",
        )
        feeds = {field: pick(field) for field in ("xy_feed", "z_feed", "a_feed")}
        for field, value in feeds.items():
            _require(
                condition=round(value, places) > 0.0,
                message=f"{label}: {field} {value!r} rounds to F0 at {places} decimals",
            )
        depths = pass_schedule(z_depth, z_step, places, label=label)
        return OperationSettings(
            name=name,
            tool=tool,
            ids=operation.ids,
            layers=operation.layers,
            z_depth=z_depth,
            z_step=z_step,
            z_safe=z_safe,
            overcut=operation.overcut,
            corner_angle=corner_angle,
            sort_method=operation.sort_method,
            oscillation_mode=mode,
            xy_feed=feeds["xy_feed"],
            z_feed=feeds["z_feed"],
            a_feed=feeds["a_feed"],
            tool_wait=pick("tool_wait"),
            pass_depths=depths,
        )

    def settings_lines(self) -> tuple[str, ...]:
        """``name = value`` lines for the G-code header: the job, then every tool, then every operation."""
        lines: list[str] = [
            f"job: {field.name} = {_angle_text(field.name, getattr(self, field.name))}"
            for field in fields(self)
            if field.name not in ("tools", "operations")
        ]
        for tool in self.tools:
            lines.extend(
                f"tool {tool.name}: {field.name} = {_angle_text(field.name, getattr(tool, field.name))}"
                for field in fields(tool)
                if field.name != "name"
            )
        for index, operation in enumerate(self.operations):
            name = operation.name or f"op {index + 1}"
            lines.extend(
                f"operation {name}: {field.name} = {_angle_text(field.name, getattr(operation, field.name))}"
                for field in fields(operation)
                if field.name != "name"
            )
        return tuple(lines)


# ----- pass schedule -------------------------------------------------------------


def pass_schedule(z_depth: float, z_step: float, precision: int, *, label: str = "job") -> tuple[float, ...]:
    """Z depths of the successive passes, ending exactly at ``z_depth``.

    The intermediate passes lie on the grid of depths the output can
    represent, spaced by the largest representable step not above
    ``z_step`` and counted in whole grid cells, so every increment the
    machine sees is at most ``z_step``, no depth is written twice, and the
    schedule agrees exactly with the pass limit.

    Raises:
        OptionError: For a step below the output resolution, a depth too
            deep for the grid, or more than ``MAX_PASSES`` passes.
    """
    resolution = 10.0**-precision
    depth_units = -round(z_depth, precision) / resolution
    _require(
        condition=math.isfinite(depth_units) and depth_units < _MAX_GRID_UNITS,
        message=f"{label}: z_depth {z_depth!r} is too deep to schedule at {precision} decimals",
    )
    if z_step <= 0.0:
        return (z_depth,)
    step_units = z_step / resolution
    if not math.isfinite(step_units) or step_units >= _MAX_GRID_UNITS:
        step_cells = _MAX_GRID_UNITS
    else:
        step_cells = math.floor(step_units + _PASS_SLACK)
    _require(
        condition=step_cells > 0,
        message=(
            f"{label}: z_step {z_step!r} is below the output resolution {resolution:g} at {precision} decimals, "
            "so no pass could be written within it; use 0 for a single pass"
        ),
    )
    depth_cells = round(depth_units)
    count = max(1, -(-depth_cells // step_cells))
    _require(
        condition=count <= MAX_PASSES,
        message=f"{label}: z_depth {z_depth!r} at z_step {z_step!r} needs {count} passes; at most {MAX_PASSES}",
    )
    depths = [round(-index * step_cells * resolution, precision) for index in range(1, count)]
    return (*depths, z_depth)


# ----- the single-tool record ----------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class KnifeOptions:
    """Everything a single-knife job needs, as the command line takes it.

    The flat record of one knife (mounted, so no tool change) cutting one
    selection; ``to_job`` builds the equivalent ``Job``, and construction
    validates through it. ``oscillation_mode`` accepts ``"program"`` as an
    alias of ``"operation"``.
    """

    # Orientation
    flip_y: bool = True

    # Geometry conversion (mm)
    tolerance: float = 0.01
    biarc_tolerance: float = 0.01
    biarc_max_depth: int = 8
    output_precision: int = 3

    # Machine: heights in mm, feeds in mm per minute and degrees per minute, waits in seconds
    xy_feed: float = 250.0
    z_feed: float = 250.0
    a_feed: float = 60.0
    z_safe: float = 10.0
    z_depth: float = -1.0
    z_step: float = 0.0
    tool_wait: float = 0.0
    blend_mode: BlendMode = "default"
    blend_tolerance: float = 0.0

    # Knife behaviour (angles in radians)
    corner_angle: float = math.radians(15.0)
    overcut: float = 0.0
    blade_offset: float = 0.0
    blade_width: float = 0.0
    a_offset: float = 0.0
    oscillation_mode: KnifeOscillationMode = "program"
    spindle_speed: int = 0
    spindle_wait_on: float = 0.0

    # Path ordering
    sort_method: SortMethod = "none"

    # G-code text
    gcode_comments: bool = True
    gcode_line_numbers: bool = False
    write_settings: bool = False

    # Input selection
    ids: tuple[str, ...] = ()
    layers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate by building the job."""
        _require(
            condition=self.oscillation_mode in ("program", "operation", "cut", "off"),
            message=f"oscillation_mode must be 'program', 'cut' or 'off', got {self.oscillation_mode!r}",
        )
        _finite(self, "options")
        self.to_job()

    def to_job(self) -> Job:
        """The one-tool, one-operation job these options describe."""
        tool = Tool(
            name="knife",
            kind="knife",
            a_offset=self.a_offset,
            corner_angle=self.corner_angle,
            blade_offset=self.blade_offset,
            blade_width=self.blade_width,
            oscillation=True,
            spindle_speed=self.spindle_speed,
            spindle_wait_on=self.spindle_wait_on,
        )
        mode: OscillationMode = "operation" if self.oscillation_mode == "program" else self.oscillation_mode
        operation = Operation(
            name="cut",
            tool="knife",
            ids=self.ids,
            layers=self.layers,
            z_depth=self.z_depth,
            z_step=self.z_step,
            overcut=self.overcut,
            sort_method=self.sort_method,
            oscillation_mode=mode,
        )
        return Job(
            tools=(tool,),
            operations=(operation,),
            flip_y=self.flip_y,
            tolerance=self.tolerance,
            biarc_tolerance=self.biarc_tolerance,
            biarc_max_depth=self.biarc_max_depth,
            output_precision=self.output_precision,
            z_safe=self.z_safe,
            blend_mode=self.blend_mode,
            blend_tolerance=self.blend_tolerance,
            gcode_comments=self.gcode_comments,
            gcode_line_numbers=self.gcode_line_numbers,
            write_settings=self.write_settings,
            xy_feed=self.xy_feed,
            z_feed=self.z_feed,
            a_feed=self.a_feed,
            tool_wait=self.tool_wait,
        )

    @property
    def settings(self) -> OperationSettings:
        """The single operation, resolved."""
        return self.to_job().settings[0]

    @property
    def pass_depths(self) -> tuple[float, ...]:
        """Z depths of the successive passes, ending exactly at ``z_depth``."""
        return self.settings.pass_depths

    @property
    def pass_count(self) -> int:
        """Number of passes actually written."""
        return len(self.pass_depths)

    @property
    def output_resolution(self) -> float:
        """The smallest length difference the G-code words can express."""
        return 10.0**-self.output_precision

    def as_settings_lines(self) -> tuple[str, ...]:
        """``name = value`` lines for the G-code header."""
        return self.to_job().settings_lines()


def as_job(job: Job | KnifeOptions) -> Job:
    """The job itself, or the job a ``KnifeOptions`` describes."""
    return job if isinstance(job, Job) else job.to_job()
