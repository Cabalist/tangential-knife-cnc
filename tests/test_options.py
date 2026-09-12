"""KnifeOptions: validation, conversion and pass planning."""

import argparse
import copy
import math
import pickle

import pytest

from tcnc.errors import OptionError
from tcnc.options import KnifeOptions


def test_defaults_are_valid_and_hashable() -> None:
    opts = KnifeOptions()
    assert hash(opts) == hash(KnifeOptions())
    assert copy.deepcopy(opts) == opts
    assert pickle.loads(pickle.dumps(opts)) == opts


def test_from_namespace_converts_degrees_and_tuples() -> None:
    ns = argparse.Namespace(corner_angle=30.0, a_offset=-90.0, ids=["a", "b"], layers=[], unrelated="x", overcut=None)
    opts = KnifeOptions.from_namespace(ns)
    assert opts.corner_angle == pytest.approx(math.radians(30.0))
    assert opts.a_offset == pytest.approx(-math.pi / 2)
    assert opts.ids == ("a", "b")
    assert opts.layers == ()
    assert opts.overcut == 0.0


@pytest.mark.parametrize(
    "changes",
    [
        {"tolerance": 0.0},
        {"gcode_units": "px"},
        {"z_safe": -1.0},
        {"z_step": -0.1},
        {"corner_angle": 0.0},
        {"corner_angle": math.pi + 0.1},
        {"overcut": -0.01},
        {"oscillation_mode": "always"},
        {"sort_method": "optimize"},
        {"xy_feed": 0.0},
        {"output_precision": -1},
    ],
)
def test_invalid_values_raise_option_error(changes: dict[str, object]) -> None:
    with pytest.raises(OptionError):
        KnifeOptions().with_(**changes)


def test_unit_scale_from_px() -> None:
    assert KnifeOptions(gcode_units="in").unit_scale_from_px == pytest.approx(1 / 96)
    assert KnifeOptions(gcode_units="mm").unit_scale_from_px == pytest.approx(25.4 / 96, rel=1e-6)


def test_pass_depths_single_and_stepped() -> None:
    assert KnifeOptions(z_depth=-0.25).pass_depths == (-0.25,)
    assert KnifeOptions(z_depth=-0.25, z_step=0.1).pass_depths == pytest.approx((-0.1, -0.2, -0.25))
    assert KnifeOptions(z_depth=-0.3, z_step=0.1).pass_depths == pytest.approx((-0.1, -0.2, -0.3))
    assert KnifeOptions(z_depth=-0.05, z_step=0.1).pass_depths == (-0.05,)


def test_settings_lines_show_degrees() -> None:
    lines = KnifeOptions(corner_angle=math.radians(20)).as_settings_lines()
    assert "corner_angle = 20 deg" in lines
    assert any(line.startswith("gcode_units = in") for line in lines)
