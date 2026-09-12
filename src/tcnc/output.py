"""Publish generated files together, or not at all.

Every file is written to a unique temporary file in its destination
directory first. Then, one by one, each destination is moved aside and the
temporary file moved into place. If any step fails, the destinations that
were already replaced are restored from their moved-aside originals, every
temporary file is removed and ``OutputError`` is raised: a failed run leaves
the previous files exactly as they were.
"""

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from tcnc.errors import OutputError

if TYPE_CHECKING:
    from collections.abc import Sequence


def publish(outputs: Sequence[tuple[Path | str, str]]) -> None:
    """Write every ``(path, text)`` pair, replacing the destinations as one unit.

    Raises ``OutputError`` (and restores the previous files) when a
    destination is a directory, its directory does not exist, or any write,
    move or close fails.
    """
    targets = [(Path(path), text) for path, text in outputs]
    _check_destinations(targets)
    staged: list[tuple[Path, Path]] = []
    replaced: list[tuple[Path, Path | None]] = []
    try:
        for path, text in targets:
            staged.append((_stage(path, text), path))
        for temp, path in staged:
            _replace(temp, path, replaced)
    except OSError as exc:
        _roll_back(staged, replaced)
        msg = f"cannot write output: {exc}"
        raise OutputError(msg) from exc
    for _, backup in replaced:
        if backup is not None:
            with suppress(OSError):
                backup.unlink()


def _check_destinations(targets: Sequence[tuple[Path, str]]) -> None:
    for path, _ in targets:
        if path.is_dir():
            msg = f"cannot write output: {path} is a directory"
            raise OutputError(msg)
        if not path.parent.is_dir():
            msg = f"cannot write output: directory {path.parent} does not exist"
            raise OutputError(msg)


def _stage(path: Path, text: str) -> Path:
    """Write ``text`` to a fresh temporary file beside ``path``; the name is known before anything is written."""
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        with suppress(OSError):
            temp.unlink(missing_ok=True)
        raise
    return temp


def _replace(temp: Path, path: Path, replaced: list[tuple[Path, Path | None]]) -> None:
    """Move the previous file aside (recorded for rollback), then the staged file into place."""
    backup: Path | None = None
    if path.exists():
        backup = path.with_name(f".{path.name}.{uuid4().hex}.bak")
        path.replace(backup)
    replaced.append((path, backup))
    temp.replace(path)


def _roll_back(staged: Sequence[tuple[Path, Path]], replaced: Sequence[tuple[Path, Path | None]]) -> None:
    """Restore every replaced destination and remove every temporary file; nothing here raises."""
    for path, backup in reversed(replaced):
        with suppress(OSError):
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                backup.replace(path)
    for temp, _ in staged:
        with suppress(OSError):
            temp.unlink(missing_ok=True)
