"""KnifeOptions: validation and pass planning."""

import copy
import math
import pickle
from typing import TYPE_CHECKING, cast

import pytest

from tcnc.errors import OptionError
from tcnc.options import BlendMode, KnifeOptions, OscillationMode, SortMethod

if TYPE_CHECKING:
    from collections.abc import Callable


def test_defaults_are_valid_and_hashable() -> None:
    opts = KnifeOptions()
    assert hash(opts) == hash(KnifeOptions())
    assert copy.deepcopy(opts) == opts
    assert pickle.loads(pickle.dumps(opts)) == opts


@pytest.mark.parametrize(
    "factory",
    [
        lambda: KnifeOptions(tolerance=0.0),
        lambda: KnifeOptions(tolerance=1e-12),
        lambda: KnifeOptions(biarc_tolerance=1e-12),
        lambda: KnifeOptions(z_safe=0.0),
        lambda: KnifeOptions(z_safe=-0.15, z_depth=-0.3, z_step=0.1),
        lambda: KnifeOptions(z_depth=0.5),
        lambda: KnifeOptions(z_step=-0.1),
        lambda: KnifeOptions(corner_angle=0.0),
        lambda: KnifeOptions(corner_angle=math.pi + 0.1),
        lambda: KnifeOptions(overcut=-0.01),
        lambda: KnifeOptions(oscillation_mode=cast("OscillationMode", "always")),
        lambda: KnifeOptions(sort_method=cast("SortMethod", "optimize")),
        lambda: KnifeOptions(blend_mode=cast("BlendMode", "")),
        lambda: KnifeOptions(xy_feed=0.0),
        lambda: KnifeOptions(output_precision=-1),
        lambda: KnifeOptions(output_precision=12),
        lambda: KnifeOptions(xy_feed=math.inf),
        lambda: KnifeOptions(a_offset=math.nan),
        lambda: KnifeOptions(z_depth=-math.inf),
        lambda: KnifeOptions(z_safe=math.inf),
        lambda: KnifeOptions(z_depth=0.0),
        lambda: KnifeOptions(z_safe=0.00001),
        lambda: KnifeOptions(output_precision=0, z_safe=0.2, z_depth=-0.2),
        lambda: KnifeOptions(z_depth=-0.0001),
        lambda: KnifeOptions(xy_feed=0.00001),
        lambda: KnifeOptions(z_feed=0.00001),
        lambda: KnifeOptions(a_feed=0.00001),
        lambda: KnifeOptions(z_step=1e-320),
        lambda: KnifeOptions(z_step=1e-12),
        lambda: KnifeOptions(z_depth=-1e6, z_step=0.001),
        lambda: KnifeOptions(blend_mode="blend", blend_tolerance=1e-9),
    ],
)
def test_invalid_values_raise_option_error(factory: Callable[[], KnifeOptions]) -> None:
    with pytest.raises(OptionError):
        factory()


def test_rounded_values_are_named_in_the_message() -> None:
    with pytest.raises(OptionError, match="rounds to Z0"):
        KnifeOptions(z_safe=0.0004)
    with pytest.raises(OptionError, match="rounds to F0"):
        KnifeOptions(a_feed=0.0004)
    with pytest.raises(OptionError, match="passes"):
        KnifeOptions(z_depth=-2.0, z_step=0.001)
    assert KnifeOptions(z_safe=0.0005).z_safe == 0.0005  # rounds to 0.001: allowed


def test_pass_depths_single_and_stepped() -> None:
    assert KnifeOptions(z_depth=-0.25).pass_depths == (-0.25,)
    assert KnifeOptions(z_depth=-0.25, z_step=0.1).pass_depths == pytest.approx((-0.1, -0.2, -0.25))
    assert KnifeOptions(z_depth=-0.3, z_step=0.1).pass_depths == pytest.approx((-0.1, -0.2, -0.3))
    assert KnifeOptions(z_depth=-0.05, z_step=0.1).pass_depths == (-0.05,)
    assert KnifeOptions(z_depth=-1.0, z_step=0.001).pass_count == 1000
    assert KnifeOptions(z_depth=-0.3, z_step=0.1, tolerance=0.05).pass_count == 3  # the job tolerance plays no part
    depths = KnifeOptions(z_depth=-0.25, z_step=0.05, tolerance=0.06).pass_depths
    assert max(a - b for a, b in zip((0.0, *depths), depths, strict=False)) <= 0.05 + 1e-12
    # A penultimate pass that would be written with the final pass's word is dropped.
    assert KnifeOptions(z_depth=-0.2004, z_step=0.1).pass_depths == (-0.1, -0.2004)
    assert KnifeOptions(z_depth=-0.2004, z_step=0.1).pass_count == 2


def test_settings_lines_show_degrees() -> None:
    lines = KnifeOptions(corner_angle=math.radians(20)).as_settings_lines()
    assert "corner_angle = 20 deg" in lines
    assert "tolerance = 0.01" in lines


def test_output_resolution() -> None:
    assert KnifeOptions(output_precision=3).output_resolution == pytest.approx(0.001)
