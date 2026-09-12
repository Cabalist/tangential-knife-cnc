"""Shared pytest fixtures."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES_DIR = Path(__file__).parent / "files"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Directory holding the SVG fixture files."""
    return FIXTURES_DIR


@pytest.fixture
def fixture(fixtures_dir: Path) -> Callable[[str], Path]:
    """Return a callable that resolves a fixture file name to its path."""

    def _resolve(name: str) -> Path:
        path = fixtures_dir / name
        if not path.is_file():
            msg = f"missing fixture {name!r} in {fixtures_dir}"
            raise FileNotFoundError(msg)
        return path

    return _resolve
