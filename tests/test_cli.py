"""Command line end to end."""

from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from geom2d import P

from tcnc.cli import (
    EXIT_OK,
    EXIT_PLAN,
    EXIT_SVG,
    EXIT_USAGE,
    KNIFE_OPTIONS,
    build_parser,
    main,
    options_from_namespace,
    run,
)
from tcnc.errors import OptionError
from tcnc.options import KnifeOptions

if TYPE_CHECKING:
    from collections.abc import Callable


def test_parser_defaults_match_options() -> None:
    ns = build_parser().parse_args(["in.svg"])
    assert options_from_namespace(ns) == KnifeOptions()
    ns = build_parser().parse_args(["in.svg", "--corner-angle", "30", "--id", "a", "--id", "b"])
    opts = options_from_namespace(ns)
    assert opts.corner_angle == pytest.approx(0.5235987755982988)
    assert opts.ids == ("a", "b")
    # Every option field is set by the parser and nothing else pretends to be one.
    dests = {action.dest for action in build_parser()._actions}
    assert {field.name for field in fields(KnifeOptions)} <= dests


def test_every_knife_option_is_listed_for_the_job_file_check() -> None:
    parser = build_parser()
    listed = {
        option
        for action in parser._actions
        for option in action.option_strings
        if action.dest not in ("help", "version", "debug", "output", "preview", "jobs", "only", "skip")
    }
    assert listed == set(KNIFE_OPTIONS)


def test_square_end_to_end(fixture: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "square.ngc"
    preview = tmp_path / "square-preview.svg"
    code = main(
        [
            str(fixture("square.svg")),
            "-o",
            str(out),
            "--preview",
            str(preview),
            "--corner-angle",
            "15",
            "--overcut",
            "0.05",
        ]
    )
    assert code == EXIT_OK
    text = out.read_text()
    assert text.startswith("%\n")
    assert "M2" in text
    assert text.count("G1 Z-1.000") == 4
    assert preview.read_text().startswith("<svg ")
    captured = capsys.readouterr()
    assert "4 cuts" in captured.out
    assert not list(tmp_path.glob(".*.tmp"))


def test_default_output_name(fixture: Callable[[str], Path], tmp_path: Path) -> None:
    src = tmp_path / "copy.svg"
    src.write_bytes(fixture("circle.svg").read_bytes())
    assert main([str(src)]) == EXIT_OK
    assert (tmp_path / "copy.ngc").exists()


def test_error_exit_codes(fixture: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = str(tmp_path / "x.ngc")
    assert main([str(tmp_path / "missing.svg"), "-o", out]) == EXIT_SVG
    assert "not found" in capsys.readouterr().err
    assert main([str(fixture("layers.svg")), "-o", out, "--layer", "Nothing"]) == EXIT_SVG
    assert "no cuttable geometry" in capsys.readouterr().err
    assert main([str(fixture("square.svg")), "-o", out, "--tolerance", "0"]) == EXIT_USAGE
    assert "tolerance" in capsys.readouterr().err
    with pytest.raises(SystemExit) as info:
        main([str(fixture("square.svg")), "--blend-mode", "furlongs"])
    assert info.value.code == EXIT_USAGE
    assert not Path(out).exists()


def test_output_write_error_uses_documented_exit_code(
    fixture: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(fixture("square.svg")), "-o", str(tmp_path / "absent" / "out.ngc")]) == EXIT_PLAN
    assert "cannot write output" in capsys.readouterr().err
    assert main([str(fixture("square.svg")), "-o", str(tmp_path / "x.ngc"), "--z-safe", "-1"]) == EXIT_USAGE


def test_failed_preview_leaves_the_previous_program_in_place(
    fixture: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out.ngc"
    output.write_text("OLD PROGRAM")
    preview = tmp_path / "preview-dir"
    preview.mkdir()
    assert main([str(fixture("square.svg")), "-o", str(output), "--preview", str(preview)]) == EXIT_PLAN
    assert "is a directory" in capsys.readouterr().err
    assert output.read_text() == "OLD PROGRAM"
    assert not list(tmp_path.glob(".*"))


def test_runs_of_short_links_are_cut_not_rejected(tmp_path: Path) -> None:
    for data in (
        "M0 0 L96 0 L96.00001 0 L96.00002 0 L96.00003 0 L192 0",
        "M0 0 L96 0 L96 96 L0 0.00001 L0 0.00002 Z",
    ):
        path = tmp_path / "links.svg"
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="384" height="384"><path d="{data}"/></svg>')
        assert main([str(path), "-o", str(tmp_path / "links.ngc")]) == EXIT_OK


def test_awkward_geometry_reaches_the_documented_exit_codes(tmp_path: Path) -> None:
    # An arc whose end rounds onto its centre at three decimals (allowed by a 1e-7 tolerance).
    radius = 0.00051001
    p1, p2 = P.from_polar(radius, 0.1), P.from_polar(radius, 0.4)
    arc = tmp_path / "arc.svg"
    arc.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1mm" height="1mm" viewBox="0 0 1 1">'
        f'<path d="M{p1.x!r} {p1.y!r} A{radius!r} {radius!r} 0 0 1 {p2.x!r} {p2.y!r}"/></svg>'
    )
    assert main([str(arc), "-o", str(tmp_path / "arc.ngc"), "--tolerance", "1e-7"]) in (EXIT_OK, EXIT_PLAN)
    # A thin ellipse is cut, not rejected during curve fitting.
    thin = tmp_path / "thin.svg"
    thin.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="384" height="384">'
        '<path d="M96 96 A100 0.000001 0 1 1 106 96"/></svg>'
    )
    assert main([str(thin), "-o", str(tmp_path / "thin.ngc")]) == EXIT_OK
    assert (
        main([str(thin), "-o", str(tmp_path / "thin-offset.ngc"), "--blade-offset", "1", "--overcut", "1"]) == EXIT_OK
    )


def test_output_and_preview_must_be_distinct(fixture: Callable[[str], Path], tmp_path: Path) -> None:
    output = tmp_path / "out.ngc"
    with pytest.raises(OptionError):
        run(KnifeOptions(), fixture("square.svg"), output, output)
    with pytest.raises(OptionError):
        run(KnifeOptions(), fixture("square.svg"), fixture("square.svg"))
    assert not output.exists()


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.startswith("tcnc ")
