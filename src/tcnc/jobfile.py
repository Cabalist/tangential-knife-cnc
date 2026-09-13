"""Job files: a TOML description of tools and operations.

::

    [job]                      # job-wide settings and, optionally, the files
    input = "box.svg"
    output = "box.ngc"
    z_safe = 10

    [tools.knife]              # one table per tool, named by its key
    kind = "knife"
    number = 1

    [[operations]]             # in the order they are cut
    tool = "knife"
    layers = ["Cut"]
    z_depth = -1.5

Angles (``a_offset``, ``corner_angle``) are degrees in the file. Unknown
keys are errors, and every value must have the type its key expects. File
paths in ``[job]`` are relative to the job file's directory. A ``[meta]``
table is accepted and ignored: producers may record provenance and their
own numbers there.

Several files can be layered (``load_job_files``): later ``[job]`` keys
override earlier ones, tools merge by name and key, operations are
appended in order. That is how an operator's machine file (tools, feeds,
depths) combines with a layout's operations-only file. The full reference
is ``docs/job-file.md``.
"""

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tcnc.errors import OptionError
from tcnc.options import Job, Operation, Tool

if TYPE_CHECKING:
    from collections.abc import Sequence

type Value = str | int | float | bool | list[str]

_FLOAT = "float"
_DEGREES = "degrees"
_INT = "int"
_BOOL = "bool"
_STR = "str"
_NAMES = "names"

FILE_KEYS = {"input": _STR, "output": _STR, "preview": _STR}
JOB_KEYS = {
    "flip_y": _BOOL,
    "tolerance": _FLOAT,
    "biarc_tolerance": _FLOAT,
    "biarc_max_depth": _INT,
    "output_precision": _INT,
    "z_safe": _FLOAT,
    "blend_mode": _STR,
    "blend_tolerance": _FLOAT,
    "gcode_comments": _BOOL,
    "gcode_line_numbers": _BOOL,
    "write_settings": _BOOL,
    "xy_feed": _FLOAT,
    "z_feed": _FLOAT,
    "a_feed": _FLOAT,
    "tool_wait": _FLOAT,
}
TOOL_KEYS = {
    "kind": _STR,
    "number": _INT,
    "a_offset": _DEGREES,
    "corner_angle": _DEGREES,
    "blade_offset": _FLOAT,
    "blade_width": _FLOAT,
    "oscillation": _BOOL,
    "spindle_speed": _INT,
    "spindle_wait_on": _FLOAT,
    "z_depth": _FLOAT,
    "z_step": _FLOAT,
    "xy_feed": _FLOAT,
    "z_feed": _FLOAT,
    "a_feed": _FLOAT,
    "tool_wait": _FLOAT,
}
OPERATION_KEYS = {
    "name": _STR,
    "tool": _STR,
    "ids": _NAMES,
    "layers": _NAMES,
    "z_depth": _FLOAT,
    "z_step": _FLOAT,
    "z_safe": _FLOAT,
    "overcut": _FLOAT,
    "corner_angle": _DEGREES,
    "sort_method": _STR,
    "oscillation_mode": _STR,
    "xy_feed": _FLOAT,
    "z_feed": _FLOAT,
    "a_feed": _FLOAT,
    "tool_wait": _FLOAT,
}


@dataclass(frozen=True, slots=True)
class JobFile:
    """A loaded job file: the job and the files it names (``None`` when it names none)."""

    job: Job
    input: Path | None
    output: Path | None
    preview: Path | None


def load_job_file(path: Path | str) -> JobFile:
    """Read and validate one TOML job file (see ``load_job_files``)."""
    return load_job_files([path])


def load_job_files(paths: Sequence[Path | str]) -> JobFile:
    """Read several job files and validate them as one job, layered in order.

    Later files win: their ``[job]`` keys override, their tools merge into
    earlier tools of the same name key by key, and their operations are
    appended. Paths in each file's ``[job]`` are relative to that file.

    Raises:
        OptionError: For a missing or malformed file, an unknown key, a
            value of the wrong type, or any rule the job itself enforces.
    """
    merged: dict[str, object] = {}
    for path in paths:
        source = Path(path)
        _layer(merged, _read(source), source)
    return parse_job(merged)


def _read(source: Path) -> dict[str, object]:
    try:
        return tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        msg = f"job file not found: {source}"
        raise OptionError(msg) from exc
    except OSError as exc:
        msg = f"cannot read job file {source}: {exc}"
        raise OptionError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f"job file {source} is not valid TOML: {exc}"
        raise OptionError(msg) from exc
    except UnicodeDecodeError as exc:
        msg = f"job file {source} is not UTF-8 text: {exc}"
        raise OptionError(msg) from exc


def _layer(merged: dict[str, object], data: dict[str, object], source: Path) -> None:
    """Merge one file's tables into ``merged``; file paths are resolved against the file first."""
    _known(data, {"job", "tools", "operations", "meta"}, where=f"job file {source}")
    _table(data.get("meta", {}), where=f"[meta] in {source}")
    job_table = dict(_table(data.get("job", {}), where=f"[job] in {source}"))
    for key in FILE_KEYS:
        if isinstance(job_table.get(key), str):
            job_table[key] = str(source.parent / str(job_table[key]))
    _table(merged.setdefault("job", {}), where="[job]").update(job_table)
    tools = _table(merged.setdefault("tools", {}), where="[tools]")
    for name, table in _table(data.get("tools", {}), where=f"[tools] in {source}").items():
        _table(tools.setdefault(name, {}), where=f"[tools.{name}]").update(
            _table(table, where=f"[tools.{name}] in {source}")
        )
    operations = data.get("operations", [])
    if not isinstance(operations, list):
        msg = f"[[operations]] in {source} must be an array of tables"
        raise OptionError(msg)
    cast("list[object]", merged.setdefault("operations", [])).extend(cast("list[object]", operations))


def parse_job(data: dict[str, object], *, base: Path | None = None) -> JobFile:
    """Build a job from the parsed TOML tables; ``base`` resolves the file paths in ``[job]``."""
    _known(data, {"job", "tools", "operations", "meta"}, where="the job file")
    _table(data.get("meta", {}), where="[meta]")
    job_table = _table(data.get("job", {}), where="[job]")
    _known(job_table, set(JOB_KEYS) | set(FILE_KEYS), where="[job]")
    files = {key: _convert(job_table[key], FILE_KEYS[key], f"job.{key}") for key in FILE_KEYS if key in job_table}
    job_values = {key: _convert(job_table[key], JOB_KEYS[key], f"job.{key}") for key in JOB_KEYS if key in job_table}
    tools_table = _table(data.get("tools", {}), where="[tools]")
    tools = tuple(_tool(name, _table(table, where=f"[tools.{name}]")) for name, table in tools_table.items())
    operations_value = data.get("operations", [])
    if not isinstance(operations_value, list):
        msg = "[[operations]] must be an array of tables"
        raise OptionError(msg)
    operations = tuple(
        _operation(_table(table, where=f"[[operations]] #{index + 1}"), index)
        for index, table in enumerate(operations_value)
    )
    try:
        job = Job(tools=tools, operations=operations, **cast("dict[str, Any]", job_values))
    except TypeError as exc:
        msg = f"job file: {exc}"
        raise OptionError(msg) from exc
    root = base if base is not None else Path()
    return JobFile(
        job=job,
        input=root / str(files["input"]) if "input" in files else None,
        output=root / str(files["output"]) if "output" in files else None,
        preview=root / str(files["preview"]) if "preview" in files else None,
    )


def _tool(name: str, table: dict[str, object]) -> Tool:
    where = f"tools.{name}"
    _known(table, set(TOOL_KEYS), where=f"[{where}]")
    if "kind" not in table:
        msg = f"[{where}] needs a kind"
        raise OptionError(msg)
    values = {key: _convert(table[key], TOOL_KEYS[key], f"{where}.{key}") for key in table}
    try:
        return Tool(name=name, **cast("dict[str, Any]", values))
    except TypeError as exc:
        msg = f"[{where}]: {exc}"
        raise OptionError(msg) from exc


def _operation(table: dict[str, object], index: int) -> Operation:
    where = f"operations[{index + 1}]"
    _known(table, set(OPERATION_KEYS), where=f"[[operations]] #{index + 1}")
    if "tool" not in table:
        msg = f"[[operations]] #{index + 1} needs a tool"
        raise OptionError(msg)
    values = {key: _convert(table[key], OPERATION_KEYS[key], f"{where}.{key}") for key in table}
    try:
        return Operation(**cast("dict[str, Any]", values))
    except TypeError as exc:
        msg = f"[[operations]] #{index + 1}: {exc}"
        raise OptionError(msg) from exc


def _table(value: object, *, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{where} must be a table"
        raise OptionError(msg)
    return cast("dict[str, object]", value)


def _known(table: dict[str, object], keys: set[str], *, where: str) -> None:
    unknown = sorted(set(table) - keys)
    if unknown:
        msg = f"{where}: unknown key(s) {', '.join(unknown)}"
        raise OptionError(msg)


def _convert(value: object, kind: str, where: str) -> Value | tuple[str, ...]:
    """The value with the type ``kind`` expects (``bool`` is never a number here)."""
    if kind == _BOOL:
        if isinstance(value, bool):
            return value
    elif kind == _INT:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif kind in (_FLOAT, _DEGREES):
        if isinstance(value, int | float) and not isinstance(value, bool):
            return math.radians(float(value)) if kind == _DEGREES else float(value)
    elif kind == _STR:
        if isinstance(value, str):
            return value
    elif kind == _NAMES and isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(cast("list[str]", value))
    expected = {
        _BOOL: "true or false",
        _INT: "an integer",
        _FLOAT: "a number",
        _DEGREES: "an angle in degrees",
        _STR: "a string",
        _NAMES: "a list of strings",
    }[kind]
    msg = f"{where} must be {expected}, got {value!r}"
    raise OptionError(msg)
