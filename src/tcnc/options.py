"""Knife job options: one frozen record with every value in internal units.

Internal units are millimetres for lengths (the G-code is metric, ``G21``),
seconds for time and radians for angles. The command line converts degrees
to radians in ``tcnc.cli``; nothing downstream converts anything.

``tolerance`` is the job's distance resolution: two points closer than it
are the same point, pieces shorter than it carry no geometry, and every
geom2d call that takes a tolerance receives it. It must not be finer than
geom2d's own numerical floor (``const.EPSILON``).

Machine contract encoded here: the material surface is Z = 0, cutting
depths are below it, and ``z_safe`` clears the surface and every pass. The
machine reads words rounded to ``output_precision`` decimals, so the
heights, steps and feeds are validated on their rounded values, passes are
planned on the grid of representable depths so that no written increment
exceeds ``z_step``, and the number of passes is bounded (``MAX_PASSES``).
"""

import math
from dataclasses import dataclass, fields
from typing import Literal

from geom2d import const

from tcnc.errors import OptionError

type BlendMode = Literal["default", "blend", "exact"]
type OscillationMode = Literal["program", "cut", "off"]
type SortMethod = Literal["none", "nearest"]

ANGLE_FIELDS = frozenset({"corner_angle", "a_offset"})
MAX_PASSES = 1000
MAX_OUTPUT_PRECISION = 9
_PASS_SLACK = 1e-9
# Grid cells beyond which a float no longer counts them exactly: a step that coarse never subdivides a
# schedulable depth, and a depth that deep cannot be scheduled.
_MAX_GRID_UNITS = 2**53


def _require(*, condition: bool, message: str) -> None:
    if not condition:
        raise OptionError(message)


@dataclass(frozen=True, slots=True, kw_only=True)
class KnifeOptions:
    """Everything a knife job needs, validated and in internal units."""

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
    oscillation_mode: OscillationMode = "program"
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
        """Validate finiteness, ranges and cross-field consistency."""
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, float):
                _require(condition=math.isfinite(value), message=f"{field.name} must be a finite number, got {value!r}")
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
        for name in ("xy_feed", "z_feed", "a_feed"):
            value = getattr(self, name)
            _require(condition=value > 0.0, message=f"{name} must be > 0, got {value!r}")
        _require(
            condition=self.z_depth < 0.0,
            message=f"z_depth is measured down from the material surface at Z=0 and must be < 0, got {self.z_depth!r}",
        )
        _require(
            condition=self.z_safe > 0.0, message=f"z_safe must clear the material surface at Z=0, got {self.z_safe!r}"
        )
        _require(
            condition=self.z_step >= 0.0, message=f"z_step must be >= 0 (0 means a single pass), got {self.z_step!r}"
        )
        self._check_rounded()
        _require(condition=self.tool_wait >= 0.0, message=f"tool_wait must be >= 0 seconds, got {self.tool_wait!r}")
        _require(
            condition=self.blend_mode in ("default", "blend", "exact"),
            message=f"blend_mode must be 'default', 'blend' or 'exact', got {self.blend_mode!r}",
        )
        _require(
            condition=self.blend_tolerance >= 0.0, message=f"blend_tolerance must be >= 0, got {self.blend_tolerance!r}"
        )
        _require(
            condition=0.0 < self.corner_angle <= math.pi,
            message=f"corner_angle must be in (0, 180] degrees, got {math.degrees(self.corner_angle)!r}",
        )
        for name in ("overcut", "blade_offset", "blade_width"):
            value = getattr(self, name)
            _require(condition=value >= 0.0, message=f"{name} must be >= 0, got {value!r}")
        _require(
            condition=self.oscillation_mode in ("program", "cut", "off"),
            message=f"oscillation_mode must be 'program', 'cut' or 'off', got {self.oscillation_mode!r}",
        )
        _require(condition=self.spindle_speed >= 0, message=f"spindle_speed must be >= 0, got {self.spindle_speed!r}")
        _require(
            condition=self.spindle_wait_on >= 0.0,
            message=f"spindle_wait_on must be >= 0 seconds, got {self.spindle_wait_on!r}",
        )
        _require(
            condition=self.sort_method in ("none", "nearest"),
            message=f"sort_method must be 'none' or 'nearest', got {self.sort_method!r}",
        )

    def _check_rounded(self) -> None:
        """The machine sees rounded words: heights, the step and the feeds must survive rounding."""
        places = self.output_precision
        resolution = self.output_resolution
        _require(
            condition=round(self.z_safe, places) > 0.0,
            message=f"z_safe {self.z_safe!r} rounds to Z0 at {places} decimals; it must be at least {resolution:g}",
        )
        _require(
            condition=round(self.z_depth, places) < 0.0,
            message=f"z_depth {self.z_depth!r} rounds to Z0 at {places} decimals; it must be at most -{resolution:g}",
        )
        _require(
            condition=self.z_step == 0.0 or self._step_units > 0,
            message=(
                f"z_step {self.z_step!r} is below the output resolution {resolution:g} at {places} decimals, "
                "so no pass could be written within it; use 0 for a single pass"
            ),
        )
        depth_units = -round(self.z_depth, places) / resolution
        _require(
            condition=math.isfinite(depth_units) and depth_units < _MAX_GRID_UNITS,
            message=f"z_depth {self.z_depth!r} is too deep to schedule at {places} decimals",
        )
        for name in ("xy_feed", "z_feed", "a_feed"):
            value = getattr(self, name)
            _require(
                condition=round(value, places) > 0.0,
                message=f"{name} {value!r} rounds to F0 at {places} decimals; it must be at least {resolution:g}",
            )
        _require(
            condition=self.blend_tolerance == 0.0 or round(self.blend_tolerance, places) > 0.0,
            message=f"blend_tolerance {self.blend_tolerance!r} rounds to zero at {places} decimals",
        )
        if self.z_step > 0.0:
            count = self._scheduled_passes
            _require(
                condition=count <= MAX_PASSES,
                message=f"z_depth {self.z_depth!r} at z_step {self.z_step!r} needs {count} passes; at most {MAX_PASSES}",
            )

    @property
    def _step_units(self) -> int:
        """``z_step`` in output grid cells, rounded down (0 below the resolution; capped when a float cannot count)."""
        units = self.z_step / self.output_resolution
        if not math.isfinite(units) or units >= _MAX_GRID_UNITS:
            return _MAX_GRID_UNITS
        return math.floor(units + _PASS_SLACK)

    @property
    def _depth_units(self) -> int:
        """The final depth's written word in output grid cells (positive); validated finite in ``_check_rounded``."""
        return round(-round(self.z_depth, self.output_precision) / self.output_resolution)

    @property
    def _scheduled_passes(self) -> int:
        """Passes on the grid: the step is known to be positive."""
        return max(1, -(-self._depth_units // self._step_units))

    @property
    def pass_depths(self) -> tuple[float, ...]:
        """Z depths of the successive passes, ending exactly at ``z_depth``.

        The intermediate passes lie on the grid of depths the output can
        represent, spaced by the largest representable step not above
        ``z_step`` and counted in whole grid cells, so every increment the
        machine sees is at most ``z_step``, no depth is written twice, and
        the schedule agrees exactly with the pass limit check.
        """
        if self.z_step <= 0.0:
            return (self.z_depth,)
        resolution, places = self.output_resolution, self.output_precision
        step_units = self._step_units
        depths = [round(-index * step_units * resolution, places) for index in range(1, self._scheduled_passes)]
        return (*depths, self.z_depth)

    @property
    def pass_count(self) -> int:
        """Number of passes actually written."""
        return len(self.pass_depths)

    @property
    def output_resolution(self) -> float:
        """The smallest length difference the G-code words can express."""
        return 10.0**-self.output_precision

    def as_settings_lines(self) -> tuple[str, ...]:
        """Human-readable ``name = value`` lines for the G-code header."""
        lines: list[str] = []
        for field in fields(self):
            value = getattr(self, field.name)
            text = f"{math.degrees(value):g} deg" if field.name in ANGLE_FIELDS else str(value)
            lines.append(f"{field.name} = {text}")
        return tuple(lines)
