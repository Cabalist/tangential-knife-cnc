"""Golden G-code files: the CLI pipeline end to end, byte for byte.

Regenerate deliberately with ``TCNC_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py``
and review the diff before committing.
"""

import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tcnc.cli import run
from tcnc.options import KnifeOptions

if TYPE_CHECKING:
    from collections.abc import Callable

GOLDEN_DIR = Path(__file__).parent / "golden"
FIXED_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

# The fixture pages are 4 in (101.6 mm) square; the option values below are millimetres.
CASES: dict[str, tuple[str, KnifeOptions]] = {
    "square": ("square.svg", KnifeOptions(corner_angle=math.radians(15), overcut=1.0, z_depth=-1.5)),
    "circle": ("circle.svg", KnifeOptions(overcut=2.5)),
    "rounded-rect": ("rounded-rect.svg", KnifeOptions(overcut=1.0)),
    "star": ("star.svg", KnifeOptions(overcut=1.0)),
    "polyline-open": ("polyline-open.svg", KnifeOptions(corner_angle=math.radians(15), overcut=1.0)),
    "quadratic": ("quadratic.svg", KnifeOptions(overcut=1.0)),
    "mm-document": ("mm-document.svg", KnifeOptions(overcut=1.0, z_depth=-1.5, z_safe=5.0)),
    "multipass": ("square.svg", KnifeOptions(z_depth=-5.0, z_step=2.5, overcut=1.0)),
    "nearest": ("elliptical-arc.svg", KnifeOptions(sort_method="nearest", overcut=1.0)),
    "blade-offset": ("rounded-rect.svg", KnifeOptions(blade_offset=1.0, overcut=1.0)),
    "oscillation-cut": (
        "square.svg",
        KnifeOptions(oscillation_mode="cut", spindle_speed=1000, spindle_wait_on=0.5, overcut=1.0),
    ),
    "oscillation-off": ("circle.svg", KnifeOptions(oscillation_mode="off")),
    "layer-filter": ("layers.svg", KnifeOptions(layers=("Cuts",), overcut=1.0)),
    "numbered-with-settings": ("square.svg", KnifeOptions(gcode_line_numbers=True, write_settings=True, overcut=1.0)),
    "a-offset-exact": ("star.svg", KnifeOptions(a_offset=math.radians(90), blend_mode="exact", overcut=1.0)),
    "blade-offset-square": ("square.svg", KnifeOptions(blade_offset=1.0, overcut=1.0, corner_angle=math.radians(15))),
    "blade-offset-circle-overcut": ("circle.svg", KnifeOptions(blade_offset=5.0, overcut=5.0)),
    "hairpin-offset": (
        "polyline-open.svg",
        KnifeOptions(blade_offset=1.0, overcut=0.5, corner_angle=math.radians(120)),
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden(name: str, fixture: Callable[[str], Path], tmp_path: Path) -> None:
    svg_name, options = CASES[name]
    out = tmp_path / f"{name}.ngc"
    run(options, fixture(svg_name), out, now=lambda: FIXED_NOW)
    produced = out.read_text()
    golden = GOLDEN_DIR / f"{name}.ngc"
    if os.environ.get("TCNC_UPDATE_GOLDEN"):
        golden.write_text(produced)
    assert golden.is_file(), f"missing golden {golden.name}; run with TCNC_UPDATE_GOLDEN=1"
    assert produced == golden.read_text()
