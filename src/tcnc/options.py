"""Knife job options: one frozen record with every value in internal units.

Internal units are G-code lengths (inches or millimetres as selected),
seconds for time and radians for angles. ``from_namespace`` is the only
place command-line values are converted, so nothing downstream converts.
"""

import math
from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Literal

from tcnc.errors import OptionError

if TYPE_CHECKING:
    import argparse

type Units = Literal["in", "mm"]
type BlendMode = Literal["", "blend", "exact"]
type OscillationMode = Literal["program", "cut", "off"]
type SortMethod = Literal["none", "nearest"]

_DEG_TO_RAD_FIELDS = frozenset({"corner_angle", "a_offset"})
_TUPLE_FIELDS = frozenset({"ids", "layers"})
_PX_PER_INCH = 96.0
# svgelements' own millimetre factor (truncated); see tcnc.svg.PX_PER_MM.
_PX_PER_MM = 3.7795296


def _require(*, condition: bool, message: str) -> None:
    if not condition:
        raise OptionError(message)


@dataclass(frozen=True, slots=True)
class KnifeOptions:
    """Everything a knife job needs, validated and in internal units."""

    # Units and orientation
    gcode_units: Units = "in"
    flip_y: bool = True

    # Geometry conversion
    tolerance: float = 1e-6
    biarc_tolerance: float = 0.001
    biarc_max_depth: int = 4
    output_precision: int = 4

    # Machine
    xy_feed: float = 10.0
    z_feed: float = 10.0
    a_feed: float = 60.0
    z_safe: float = 1.0
    z_depth: float = -0.25
    z_step: float = 0.0
    tool_wait: float = 0.0
    blend_mode: BlendMode = ""
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
        """Validate ranges and cross-field consistency."""
        _require(
            condition=self.gcode_units in ("in", "mm"),
            message=f"gcode_units must be 'in' or 'mm', got {self.gcode_units!r}",
        )
        _require(condition=self.tolerance > 0.0, message=f"tolerance must be > 0, got {self.tolerance!r}")
        _require(
            condition=self.biarc_tolerance > 0.0, message=f"biarc_tolerance must be > 0, got {self.biarc_tolerance!r}"
        )
        _require(
            condition=self.biarc_max_depth >= 0, message=f"biarc_max_depth must be >= 0, got {self.biarc_max_depth!r}"
        )
        _require(
            condition=self.output_precision >= 0,
            message=f"output_precision must be >= 0, got {self.output_precision!r}",
        )
        for name in ("xy_feed", "z_feed", "a_feed"):
            value = getattr(self, name)
            _require(condition=value > 0.0, message=f"{name} must be > 0, got {value!r}")
        _require(
            condition=self.z_safe > self.z_depth,
            message=f"z_safe ({self.z_safe!r}) must be above z_depth ({self.z_depth!r})",
        )
        _require(
            condition=self.z_step >= 0.0, message=f"z_step must be >= 0 (0 means a single pass), got {self.z_step!r}"
        )
        _require(condition=self.tool_wait >= 0.0, message=f"tool_wait must be >= 0 seconds, got {self.tool_wait!r}")
        _require(
            condition=self.blend_mode in ("", "blend", "exact"),
            message=f"blend_mode must be '', 'blend' or 'exact', got {self.blend_mode!r}",
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

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> KnifeOptions:
        """Build options from parsed command-line arguments.

        Angles arrive in degrees and are converted to radians here; every
        other value is already in internal units. Attributes the namespace
        does not define keep their defaults; attributes it defines that are
        not options are ignored.
        """
        values: dict[str, object] = {}
        for field in fields(cls):
            if not hasattr(namespace, field.name):
                continue
            value = getattr(namespace, field.name)
            if value is None:
                continue
            if field.name in _DEG_TO_RAD_FIELDS:
                value = math.radians(float(value))
            elif field.name in _TUPLE_FIELDS:
                value = tuple(str(item) for item in value)
            values[field.name] = value
        return replace(cls(), **values)

    @property
    def unit_scale_from_px(self) -> float:
        """Multiplier that turns svgelements px (96 per inch) into G-code units."""
        return 1.0 / _PX_PER_INCH if self.gcode_units == "in" else 1.0 / _PX_PER_MM

    @property
    def pass_depths(self) -> tuple[float, ...]:
        """Z depths of the successive passes, ending exactly at ``z_depth``."""
        if self.z_step <= 0.0 or self.z_depth >= 0.0:
            return (self.z_depth,)
        depths: list[float] = []
        depth = 0.0
        while True:
            depth -= self.z_step
            if depth <= self.z_depth + self.tolerance:
                depths.append(self.z_depth)
                return tuple(depths)
            depths.append(depth)

    def with_(self, **changes: object) -> KnifeOptions:
        """Return a validated copy with the given fields replaced."""
        return replace(self, **changes)

    def as_settings_lines(self) -> tuple[str, ...]:
        """Human-readable ``name = value`` lines for the G-code header."""
        lines: list[str] = []
        for field in fields(self):
            value = getattr(self, field.name)
            text = f"{math.degrees(value):g} deg" if field.name in _DEG_TO_RAD_FIELDS else str(value)
            lines.append(f"{field.name} = {text}")
        return tuple(lines)
