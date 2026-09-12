"""Jobs with several tools: the model, the job file, the program layout and the preview."""

import copy
import math
import pickle
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, cast

import pytest
from geom2d import Line, P

from tcnc.cli import EXIT_OK, EXIT_SVG, EXIT_USAGE, main, run
from tcnc.corners import JobPlan, plan_cuts
from tcnc.errors import OptionError, PlanError
from tcnc.gcode import GCodeWriter, write_program
from tcnc.jobfile import load_job_file, parse_job
from tcnc.options import Job, KnifeOptions, Operation, OscillationMode, Tool, ToolKind, pass_schedule
from tcnc.plan import load_document, plan_job, plan_toolpaths
from tcnc.preview import preview_svg
from tcnc.toolpath import Toolpath
from tests.test_corners import circle, square

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

KNIFE = Tool(name="knife", kind="knife", number=1, spindle_speed=800)
CREASER = Tool(name="creaser", kind="creaser", number=2, a_offset=math.pi / 2, xy_feed=400.0)
PEN = Tool(name="pen", kind="pen", number=3, a_offset=math.radians(45))
MOUNTED = Tool(name="mounted", kind="knife")


def three_operations(*, xy_feed: float = 250.0, z_feed: float = 250.0) -> Job:
    return Job(
        tools=(KNIFE, CREASER, PEN),
        operations=(
            Operation(name="crease", tool="creaser", layers=("Crease",), z_depth=-0.4),
            Operation(name="cut", tool="knife", layers=("Cut",), z_depth=-1.5, overcut=1.0, z_step=0.75),
            Operation(name="marks", tool="pen", layers=("Marks",), z_depth=-0.5, z_safe=3.0),
        ),
        xy_feed=xy_feed,
        z_feed=z_feed,
    )


def body(text: str) -> list[str]:
    stripped = (line.split("  ;", 1)[0].rstrip() for line in text.splitlines())
    return [line for line in stripped if line and not line.startswith(";")]


# ----- the model ----------------------------------------------------------------


def test_settings_resolve_operation_then_tool_then_job() -> None:
    job = three_operations(xy_feed=250.0, z_feed=300.0)
    crease, cut, marks = job.settings
    assert crease.xy_feed == 400.0  # the tool's
    assert crease.z_feed == 300.0  # the job's
    assert crease.corner_angle == pytest.approx(math.radians(10))  # the creaser's kind default
    assert cut.corner_angle == pytest.approx(math.radians(15))
    assert cut.pass_depths == (-0.75, -1.5)
    assert marks.corner_angle is None
    assert marks.z_safe == 3.0
    assert crease.oscillation_mode == "off"  # a creaser never oscillates
    assert cut.oscillation_mode == "operation"
    assert cut.z_safe == 10.0
    assert not marks.tangential
    assert crease.a_offset == pytest.approx(math.pi / 2)
    assert job.resolve(job.operations[1]) == cut
    assert Tool(name="c", kind="creaser", corner_angle=0.1).default_corner_angle == 0.1
    assert pass_schedule(-1.0, 0.0, 3) == (-1.0,)


def test_knife_options_are_a_one_operation_job() -> None:
    opts = KnifeOptions(z_depth=-1.5, overcut=1.0, oscillation_mode="program", spindle_speed=1000)
    job = opts.to_job()
    assert [tool.name for tool in job.tools] == ["knife"]
    assert job.tools[0].number is None
    (operation,) = job.operations
    assert operation.oscillation_mode == "operation"
    assert opts.settings.pass_depths == (-1.5,)
    assert KnifeOptions(oscillation_mode="operation").to_job().operations[0].oscillation_mode == "operation"
    assert pickle.loads(pickle.dumps(job)) == job
    assert hash(copy.deepcopy(job)) == hash(job)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Tool(name="", kind="knife"),
        lambda: Tool(name="x", kind=cast("ToolKind", "drill")),
        lambda: Tool(name="x", kind="knife", number=0),
        lambda: Tool(name="x", kind="pen", blade_offset=0.2),
        lambda: Tool(name="x", kind="pen", corner_angle=0.3),
        lambda: Tool(name="x", kind="pen", oscillation=True),
        lambda: Tool(name="x", kind="creaser", oscillation=True),
        lambda: Tool(name="x", kind="knife", xy_feed=0.0),
        lambda: Tool(name="x", kind="knife", a_offset=math.nan),
        lambda: Operation(tool="knife", z_depth=0.0),
        lambda: Operation(tool="knife", z_depth=-1.0, z_safe=0.0),
        lambda: Operation(tool="knife", z_depth=-1.0, oscillation_mode=cast("OscillationMode", "program")),
        lambda: Operation(tool="", z_depth=-1.0),
        lambda: Job(tools=(), operations=()),
        lambda: Job(tools=(KNIFE,), operations=()),
        lambda: Job(tools=(KNIFE,), operations=(Operation(tool="pen", z_depth=-1.0),)),
        lambda: Job(
            tools=(KNIFE, Tool(name="knife2", kind="knife", number=1)),
            operations=(Operation(tool="knife", z_depth=-1.0),),
        ),
        lambda: Job(tools=(KNIFE, KNIFE), operations=(Operation(tool="knife", z_depth=-1.0),)),
        lambda: Job(
            tools=(MOUNTED, PEN),
            operations=(
                Operation(tool="pen", z_depth=-1.0, oscillation_mode="off"),
                Operation(tool="mounted", z_depth=-1.0),
            ),
        ),
        lambda: Job(
            tools=(MOUNTED, PEN),
            operations=(
                Operation(tool="mounted", z_depth=-1.0),
                Operation(tool="pen", z_depth=-1.0, oscillation_mode="off"),
                Operation(tool="mounted", z_depth=-1.0),
            ),
        ),
        lambda: Job(
            tools=(MOUNTED, Tool(name="also", kind="knife")), operations=(Operation(tool="mounted", z_depth=-1.0),)
        ),
        lambda: Job(tools=(PEN,), operations=(Operation(tool="pen", z_depth=-1.0, oscillation_mode="cut"),)),
        lambda: Job(
            tools=(PEN,), operations=(Operation(tool="pen", z_depth=-1.0, overcut=1.0, oscillation_mode="off"),)
        ),
        lambda: Job(
            tools=(PEN,), operations=(Operation(tool="pen", z_depth=-1.0, z_step=0.5, oscillation_mode="off"),)
        ),
        lambda: Job(
            tools=(PEN,), operations=(Operation(tool="pen", z_depth=-1.0, corner_angle=0.3, oscillation_mode="off"),)
        ),
        lambda: Job(tools=(KNIFE,), operations=(Operation(tool="knife", z_depth=-1.0, z_safe=0.0001),)),
        lambda: Job(tools=(KNIFE,), operations=(Operation(tool="knife", z_depth=-1.0, z_step=0.0001),)),
        lambda: Job(tools=(KNIFE,), operations=(Operation(tool="knife", z_depth=-1.0),), tolerance=1e-12),
    ],
)
def test_invalid_jobs_raise_option_error(factory: Callable[[], object]) -> None:
    with pytest.raises(OptionError):
        factory()


def test_leading_operations_may_use_the_mounted_tool() -> None:
    job = Job(
        tools=(MOUNTED, PEN),
        operations=(
            Operation(tool="mounted", z_depth=-1.0),
            Operation(tool="mounted", z_depth=-2.0),
            Operation(tool="pen", z_depth=-0.5, oscillation_mode="off"),
        ),
    )
    assert [settings.tool.name for settings in job.settings] == ["mounted", "mounted", "pen"]


# ----- the job file --------------------------------------------------------------


def test_job_file_round_trips_the_fixture(fixture: Callable[[str], Path]) -> None:
    loaded = load_job_file(fixture("box.toml"))
    assert loaded.input == fixture("box.svg")
    assert loaded.output is None
    job = loaded.job
    assert [tool.name for tool in job.tools] == ["knife", "creaser", "pen"]
    assert job.tool("creaser").corner_angle == pytest.approx(math.radians(8))
    assert job.tool("pen").a_offset == pytest.approx(math.radians(45))
    assert job.z_safe == 8.0
    crease, cut, marks = job.settings
    assert crease.xy_feed == 400.0
    assert cut.sort_method == "nearest"
    assert marks.z_safe == 3.0


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"jobs": {}}, "unknown key"),
        ({"job": {"units": "mm"}}, "unknown key"),
        ({"tools": {"k": {"kind": "knife", "colour": "red"}}}, "unknown key"),
        ({"tools": {"k": {"number": 1}}}, "needs a kind"),
        ({"tools": {"k": {"kind": "knife", "number": "one"}}}, "must be an integer"),
        ({"tools": {"k": {"kind": "knife", "a_offset": True}}}, "angle in degrees"),
        ({"tools": {"k": {"kind": "knife"}}, "operations": [{"tool": "k"}]}, "needs z_depth"),
        (
            {"tools": {"k": {"kind": "knife"}}, "operations": [{"tool": "k", "z_depth": -1, "layers": "Cut"}]},
            "list of strings",
        ),
        ({"tools": {"k": {"kind": "knife"}}, "operations": {"tool": "k"}}, "array of tables"),
        ({"tools": "knife"}, "must be a table"),
        (
            {"tools": {"k": {"kind": "knife"}}, "operations": [{"tool": "k", "z_depth": -1, "corner_angle": 0}]},
            "corner_angle",
        ),
    ],
)
def test_job_file_errors_name_the_key(data: dict[str, object], match: str) -> None:
    with pytest.raises(OptionError, match=match):
        parse_job(data)


def test_job_file_paths_are_relative_to_the_file(tmp_path: Path) -> None:
    (tmp_path / "jobs").mkdir()
    job_file = tmp_path / "jobs" / "a.toml"
    job_file.write_text(
        '[job]\ninput = "../art.svg"\noutput = "out/a.ngc"\n[tools.k]\nkind = "knife"\n[[operations]]\ntool = "k"\nz_depth = -1\n'
    )
    loaded = load_job_file(job_file)
    assert loaded.input == tmp_path / "jobs" / ".." / "art.svg"
    assert loaded.output == tmp_path / "jobs" / "out" / "a.ngc"
    assert loaded.preview is None
    with pytest.raises(OptionError, match="not found"):
        load_job_file(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[job\n")
    with pytest.raises(OptionError, match="not valid TOML"):
        load_job_file(bad)


# ----- the program ---------------------------------------------------------------


def test_program_changes_tools_only_when_the_tool_changes(fixture: Callable[[str], Path]) -> None:
    job = three_operations()
    document = load_document(fixture("box.svg"), job)
    plan = plan_job(document, job)
    assert [len(operation.cuts) for operation in plan.operations] == [2, 4, 2]
    text = write_program(plan)
    lines = body(text)
    changes = [line for line in lines if line.endswith("M6")]
    assert changes == ["T2 M6", "T1 M6", "T3 M6"]
    for change in changes:
        assert lines[lines.index(change) + 1] == "G43"
    # The oscillation is off before every change and the knife is switched around its own operation only.
    assert lines.index("M3 S800") > lines.index("T1 M6")
    assert lines.index("M5") < lines.index("T3 M6")
    assert lines.count("M3 S800") == 1
    assert lines.count("M5") == 1
    # Every change happens at safe height with the head off: a lift, then M5 when it was on, then T n M6.
    for change in changes[1:]:
        before = lines[lines.index(change) - 1]
        if before == "M5":
            before = lines[lines.index(change) - 2]
        assert before.startswith("G0 Z"), before
    assert "; Operation 2/3: cut (knife, T1)" in text
    # The pen parks its A axis once and writes no other A word.
    pen_start = lines.index("T3 M6")
    a_words = [line for line in lines[pen_start:] if " A" in line or line.startswith("G0 A")]
    assert len(a_words) == 1
    (park,) = a_words
    assert park.startswith("G0 A")
    assert float(park[4:]) % 360.0 == pytest.approx(45.0)  # the nearest turn onto the pen's mounting angle
    # Its lifts go to its own safe height; the creaser and knife lift to the job's.
    assert "G0 Z3.000" in lines[pen_start:]
    assert "G0 Z10.000" in lines[: lines.index("T3 M6")]
    assert "G0 Z3.000" not in lines[: lines.index("T3 M6")]


def test_tool_changes_forget_the_cached_axes_and_retract_first() -> None:
    # Two operations on the same square with different tools: the second must re-position every axis.
    job = Job(
        tools=(CREASER, KNIFE),
        operations=(
            Operation(name="crease", tool="creaser", z_depth=-0.5),
            Operation(name="cut", tool="knife", z_depth=-0.5),
        ),
    )
    plans = [plan_cuts([square()], settings) for settings in job.settings]
    lines = body(write_program(JobPlan(job, tuple(plans))))
    first_change = lines.index("T2 M6")
    assert lines[first_change - 1] == "G0 Z10.000"  # retract before the first change, at an unknown start Z
    assert lines[first_change + 1] == "G43"
    assert lines[first_change + 2] == "G0 Z10.000"  # and again in the new tool's coordinates
    second_change = lines.index("T1 M6")
    after = lines[second_change + 1 :]
    assert after[0] == "G43"
    assert after[1] == "M3 S800"
    assert after[2] == "G0 Z10.000"  # same safe height as before the change, still written
    first_rapid = next(line for line in after if line.startswith("G0 X"))
    assert first_rapid.startswith("G0 X0.000 Y0.000 A")  # X, Y and A all written although unchanged on paper
    assert float(first_rapid.split("A")[1]) % 360.0 == pytest.approx(0.0)  # the knife's heading, unwrapped
    writer = GCodeWriter(job)
    writer.rapid(x=1.0, y=2.0, z=3.0, a=0.0)
    writer.tool_change(KNIFE)
    assert writer.state.x is None and writer.state.z is None  # noqa: PT018 - one fact: nothing is cached
    writer.rapid(x=1.0, y=2.0, z=3.0, a=0.0)
    assert writer.lines[-1] == "G0 X1.000 Y2.000 Z3.000 A0.000"


def test_pen_retracts_before_parking() -> None:
    mounted_pen = Tool(name="pen", kind="pen", a_offset=1.0)
    job = Job(tools=(mounted_pen,), operations=(Operation(tool="pen", z_depth=-0.5),))
    lines = body(write_program(JobPlan(job, (plan_cuts([square()], job.settings[0]),))))
    park = lines.index("G0 A57.296")
    assert lines[park - 1] == "G0 Z10.000"
    changed = Job(tools=(PEN,), operations=(Operation(tool="pen", z_depth=-0.5),))
    lines = body(write_program(JobPlan(changed, (plan_cuts([square()], changed.settings[0]),))))
    assert lines[lines.index("T3 M6") + 1 :][:3] == ["G43", "G0 Z10.000", "G0 A45.000"]


def test_same_tool_twice_and_mounted_tool_write_no_change() -> None:
    job = Job(
        tools=(MOUNTED, PEN),
        operations=(
            Operation(name="deep", tool="mounted", z_depth=-2.0),
            Operation(name="shallow", tool="mounted", z_depth=-0.5),
            Operation(name="marks", tool="pen", z_depth=-0.5, oscillation_mode="off"),
        ),
    )
    plans = [plan_cuts([square()], settings) for settings in job.settings]
    text = write_program(JobPlan(job, tuple(plans)))
    lines = body(text)
    assert [line for line in lines if line.endswith("M6")] == ["T3 M6"]
    assert lines.count("M3") == 2  # once per mounted-knife operation
    assert "; Operation 1/3: deep (knife)" in text


def test_single_knife_layout_is_unchanged() -> None:
    opts = KnifeOptions(overcut=1.0)
    text = write_program(plan_toolpaths([square()], opts), now=None)
    assert "M6" not in text
    assert "G43" not in text
    assert "Operation" not in text
    assert "; Cuts: 4" in text
    writer = GCodeWriter(opts)
    with pytest.raises(PlanError, match="no number"):
        writer.tool_change(opts.to_job().tools[0])


def test_pen_never_lifts_and_a_creaser_splits_like_a_knife() -> None:
    pen_job = Job(tools=(PEN,), operations=(Operation(tool="pen", z_depth=-0.5, oscillation_mode="off"),))
    (pen,) = pen_job.settings
    assert len(plan_cuts([square()], pen).cuts) == 1
    assert plan_cuts([square()], pen).cuts[0].loop
    zigzag = Toolpath.from_geometry([Line(P(0, 0), P(1, 1)), Line(P(1, 1), P(2, 0))])
    assert zigzag is not None
    assert len(plan_cuts([zigzag], pen).cuts) == 1
    crease_job = Job(tools=(CREASER,), operations=(Operation(tool="creaser", z_depth=-0.4, oscillation_mode="off"),))
    (crease,) = crease_job.settings
    assert len(plan_cuts([square()], crease).cuts) == 4
    assert len(plan_cuts([circle()], crease).cuts) == 1


def test_preview_colours_operations_and_shows_a_legend(fixture: Callable[[str], Path]) -> None:
    job = three_operations()
    plan = plan_job(load_document(fixture("box.svg"), job), job)
    text = preview_svg(plan)
    assert text.count("<text") == 3
    named = Job(
        tools=(KNIFE, PEN),
        operations=(
            Operation(name="cut & <score>", tool="knife", z_depth=-1.0),
            Operation(name="marks", tool="pen", z_depth=-0.5),
        ),
    )
    escaped = preview_svg(JobPlan(named, tuple(plan_cuts([square()], s) for s in named.settings)))
    assert "cut &amp; &lt;score&gt;" in escaped
    ET.fromstring(escaped)
    for colour in ("#1f77b4", "#d62728", "#000000"):
        assert f'stroke="{colour}"' in text
    assert "3. marks (pen, T3)" in text


# ----- the command line ----------------------------------------------------------


def test_cli_runs_a_job_file(
    fixture: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "box.ngc"
    assert main(["--job", str(fixture("box.toml")), "-o", str(out), "--preview", str(tmp_path / "box.svg")]) == EXIT_OK
    text = out.read_text()
    assert "T2 M6" in text
    assert "; Operation 1/3: crease (creaser, T2)" in text
    assert "crease 2 cuts" in capsys.readouterr().out
    assert main(["--job", str(fixture("box.toml")), "-o", str(out), "--z-depth", "-2"]) == EXIT_USAGE
    assert "cannot be used with --job" in capsys.readouterr().err
    assert main(["--job", str(tmp_path / "none.toml"), "-o", str(out)]) == EXIT_USAGE
    other = tmp_path / "square.svg"
    other.write_bytes(fixture("square.svg").read_bytes())
    assert main(["--job", str(fixture("box.toml")), str(other), "-o", str(out)]) == EXIT_SVG
    assert "selects no cuttable geometry" in capsys.readouterr().err
    assert main([]) == EXIT_USAGE
    # An abbreviated knife option is not silently ignored beside --job.
    with pytest.raises(SystemExit) as info:
        main(["--job", str(fixture("box.toml")), "-o", str(out), "--z-dep", "-2"])
    assert info.value.code == EXIT_USAGE
    # No output may replace the job file itself.
    selfish = tmp_path / "self.toml"
    selfish.write_text(
        fixture("box.toml")
        .read_text()
        .replace('input = "box.svg"', f'input = "{fixture("box.svg")}"\noutput = "self.toml"')
    )
    assert main(["--job", str(selfish)]) == EXIT_USAGE
    assert "job file" in capsys.readouterr().err
    assert selfish.read_text().startswith("#")
    assert (
        main(["--job", str(fixture("box.toml")), "--preview", str(fixture("box.toml")), "-o", str(out)]) == EXIT_USAGE
    )


def test_run_accepts_a_job(fixture: Callable[[str], Path], tmp_path: Path) -> None:
    job = load_job_file(fixture("box.toml")).job
    result = run(job, fixture("box.svg"), tmp_path / "box.ngc")
    assert len(result.plan.operations) == 3
