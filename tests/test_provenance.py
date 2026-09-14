"""Provenance: [meta] flattening and TOML rendering, the header lines and their limits."""

import hashlib
import math
import tomllib
from typing import TYPE_CHECKING

import pytest

from tcnc.errors import OptionError
from tcnc.provenance import (
    COMPONENTS,
    MAX_LINE_BYTES,
    ComponentVersion,
    FileDigest,
    MetaEntry,
    Provenance,
    meta_entries,
    runtime_versions,
)

if TYPE_CHECKING:
    from pathlib import Path

META = """
name = "A"
count = 3
ratio = 0.25
big = 1e22
on = true
when = 2026-09-13T18:02:11Z
day = 2026-09-13
sizes = [1, 2.5, "x"]
parts = [{ id = "a", n = 1 }]
empty = {}
"odd key" = 'say "hi" \\ bye'

[layout]
name = "B"

[layout.mesh]
revision = "af34"
"""


def test_meta_flattens_to_dotted_keys_with_toml_values() -> None:
    table = tomllib.loads(META)
    entries = meta_entries(table, where="job file layout.toml")
    assert [(entry.key, entry.value) for entry in entries] == [
        ("name", '"A"'),
        ("count", "3"),
        ("ratio", "0.25"),
        ("big", "1e+22"),
        ("on", "true"),
        ("when", "2026-09-13T18:02:11+00:00"),
        ("day", "2026-09-13"),
        ("sizes", '[1, 2.5, "x"]'),
        ("parts", '[{id = "a", n = 1}]'),
        ("empty", "{}"),
        ('"odd key"', '"say \\"hi\\" \\\\ bye"'),
        ("layout.name", '"B"'),
        ("layout.mesh.revision", '"af34"'),
    ]
    # Read back as TOML, the entries are the table they came from.
    assert tomllib.loads("\n".join(f"{entry.key} = {entry.value}" for entry in entries)) == table
    specials = meta_entries({"a": math.nan, "b": math.inf, "c": -math.inf}, where="w")
    assert [entry.value for entry in specials] == ["nan", "inf", "-inf"]


@pytest.mark.parametrize(
    "table",
    [
        {"mesh_revision": "a\nb"},
        {"rev\t": 1},
        {"sizes": ["ok", "bad\x00"]},
        {"outer": {"inner": "\x7f"}},
    ],
)
def test_meta_rejects_control_characters_naming_the_key_and_the_file(table: dict[str, object]) -> None:
    with pytest.raises(
        OptionError, match=r"^job file layout\.toml: \[meta\] '.+' contains a newline or control character"
    ):
        meta_entries(table, where="job file layout.toml")


def test_meta_rejects_a_line_longer_than_linuxcnc_reads() -> None:
    fits = "x" * (MAX_LINE_BYTES - len('; meta: k = ""'))
    (entry,) = meta_entries({"k": fits}, where="w")
    assert len(f"; meta: {entry.key} = {entry.value}".encode()) == MAX_LINE_BYTES
    with pytest.raises(OptionError, match=r"^w: \[meta\] 'k' would make a 253-byte header line"):
        meta_entries({"k": fits + "x"}, where="w")
    with pytest.raises(OptionError, match="253-byte"):  # bytes, not characters
        meta_entries({"k": fits[:-1] + "é"}, where="w")


def test_provenance_lines_are_in_the_documented_order() -> None:
    provenance = Provenance(
        source=FileDigest("a.svg", "0" * 64),
        job_files=(FileDigest("machine.toml", "1" * 64), FileDigest("layout.toml", "2" * 64)),
        meta=(MetaEntry("k", '"v"'),),
        versions=(ComponentVersion("tcnc", "1.2.3"), ComponentVersion("python", "3.14.0")),
    )
    assert provenance.lines() == (
        f"source: a.svg sha256={'0' * 64}",
        f"job-file: machine.toml sha256={'1' * 64}",
        f"job-file: layout.toml sha256={'2' * 64}",
        'meta: k = "v"',
        "versions: tcnc 1.2.3, python 3.14.0",
    )
    assert Provenance(from_options=True).lines() == ("job-file: none (command-line options)",)
    assert Provenance().lines() == ("job-file: none",)
    with pytest.raises(OptionError, match="control character"):
        Provenance(source=FileDigest("bad\nname.svg", "0" * 64)).lines()
    with pytest.raises(OptionError, match="LinuxCNC reads at most"):
        Provenance(source=FileDigest("n" * 200 + ".svg", "0" * 64)).lines()


def test_file_digest_is_the_sha256_of_the_bytes(tmp_path: Path) -> None:
    path = tmp_path / "drawing.svg"
    path.write_bytes(b"<svg/>\r\n")
    assert FileDigest.read(path) == FileDigest("drawing.svg", hashlib.sha256(b"<svg/>\r\n").hexdigest())
    with pytest.raises(OptionError, match="cannot read"):
        FileDigest.read(tmp_path / "missing.svg")


def test_runtime_versions_name_every_component() -> None:
    versions = runtime_versions()
    assert [item.name for item in versions] == ["tcnc", *COMPONENTS, "python"]
    assert all(item.version and item.version != "unknown" for item in versions)
