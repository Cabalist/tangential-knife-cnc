"""Atomic publication of the generated files."""

from pathlib import Path

import pytest

from tcnc.errors import OutputError
from tcnc.output import publish


def test_publish_replaces_every_file_and_leaves_no_temporaries(tmp_path: Path) -> None:
    program, preview = tmp_path / "a.ngc", tmp_path / "a.svg"
    program.write_text("old a")
    publish([(program, "new a"), (preview, "new b")])
    assert program.read_text() == "new a"
    assert preview.read_text() == "new b"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.ngc", "a.svg"]


def test_publish_rejects_bad_destinations_before_touching_anything(tmp_path: Path) -> None:
    program = tmp_path / "a.ngc"
    program.write_text("old a")
    with pytest.raises(OutputError, match="is a directory"):
        publish([(program, "new a"), (tmp_path, "x")])
    with pytest.raises(OutputError, match="does not exist"):
        publish([(program, "new a"), (tmp_path / "absent" / "b.svg", "x")])
    assert program.read_text() == "old a"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.ngc"]


def test_publish_restores_the_previous_files_when_a_later_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program, preview = tmp_path / "a.ngc", tmp_path / "a.svg"
    program.write_text("old a")
    preview.write_text("old b")
    original = Path.replace
    calls: list[str] = []

    def failing_replace(self: Path, target: Path) -> Path:
        calls.append(self.name)
        if self.suffix == ".tmp" and target.name == "a.svg":  # only the move of the staged preview fails
            msg = "disk full"
            raise OSError(msg)
        return original(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    with pytest.raises(OutputError, match="disk full"):
        publish([(program, "new a"), (preview, "new b")])
    monkeypatch.undo()
    assert program.read_text() == "old a"
    assert preview.read_text() == "old b"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.ngc", "a.svg"]
    assert calls  # the first file had already been replaced and was rolled back


def test_publish_write_failure_removes_the_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os  # noqa: PLC0415 - only this test patches the low-level write

    def failing_fdopen(*args: object, **kwargs: object) -> object:
        msg = "no space"
        raise OSError(msg)

    monkeypatch.setattr(os, "fdopen", failing_fdopen)
    with pytest.raises(OutputError, match="no space"):
        publish([(tmp_path / "a.ngc", "x")])
    monkeypatch.undo()
    assert list(tmp_path.iterdir()) == []
