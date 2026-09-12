"""Command line end to end."""

from pathlib import Path

import pytest

from tcnc.cli import EXIT_OK, EXIT_PLAN, EXIT_SVG, EXIT_USAGE, build_parser, main
from tcnc.options import KnifeOptions


def test_parser_defaults_match_options() -> None:
    ns = build_parser().parse_args(["in.svg"])
    opts = KnifeOptions.from_namespace(ns)
    assert opts == KnifeOptions()


def test_square_end_to_end(fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
    assert text.count("G1 Z-0.2500") == 4
    assert preview.read_text().startswith("<svg ")
    captured = capsys.readouterr()
    assert "4 cuts" in captured.out
    assert not list(tmp_path.glob(".*.tmp"))


def test_default_output_name(fixture, tmp_path: Path) -> None:
    src = tmp_path / "copy.svg"
    src.write_bytes(fixture("circle.svg").read_bytes())
    assert main([str(src)]) == EXIT_OK
    assert (tmp_path / "copy.ngc").exists()


def test_error_exit_codes(fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = str(tmp_path / "x.ngc")
    assert main([str(tmp_path / "missing.svg"), "-o", out]) == EXIT_SVG
    assert "not found" in capsys.readouterr().err
    assert main([str(fixture("layers.svg")), "-o", out, "--layer", "Nothing"]) == EXIT_SVG
    assert "no cuttable geometry" in capsys.readouterr().err
    assert main([str(fixture("square.svg")), "-o", out, "--tolerance", "0"]) == EXIT_USAGE
    assert "tolerance" in capsys.readouterr().err
    with pytest.raises(SystemExit) as info:
        main([str(fixture("square.svg")), "--gcode-units", "furlongs"])
    assert info.value.code == EXIT_USAGE
    assert not Path(out).exists()


def test_plan_error_exit_code(fixture, tmp_path: Path) -> None:
    # z_safe below z_depth is an option error; a geometry failure is a plan error. Both are reported, not raised.
    out = str(tmp_path / "x.ngc")
    assert main([str(fixture("square.svg")), "-o", out, "--z-safe", "-1"]) == EXIT_USAGE
    assert EXIT_PLAN == 3


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.startswith("tcnc ")
