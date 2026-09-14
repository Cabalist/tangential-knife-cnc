# tangential-knife-cnc

SVG in, LinuxCNC G-code out, for a 3.5-axis machine with an **oscillating
tangential knife** (the package and command are `tcnc`): X/Y position, Z depth, an A axis that keeps the blade
tangent to the cut, and the oscillating head switched like a spindle.

```sh
tcnc drawing.svg -o drawing.ngc --preview drawing-preview.svg \
    --z-depth -1.5 --corner-angle 15 --overcut 1
tcnc --job box.toml                     # several tools: crease, cut, draw
```

Everything is metric: option lengths are millimetres and the G-code is
written under `G21`. A job file (below) runs several tools, a knife, a
creasing wheel and a pen, in one program with `T n M6` tool changes.

## Requirements

- Python 3.14 or newer.
- A LinuxCNC-compatible controller with X, Y, Z and a rotary A axis about Z.
  The head is switched with `M3`/`M5` (with an optional `S` word). What
  the controller must provide, the operator's machine file, the values to
  confirm on the machine and how to measure the blade are in
  `docs/machine-deployment.md`.
- Dependencies: [`tangential-knife-cnc-geometry`](https://github.com/Cabalist/tangential-knife-cnc-geometry)
  (2D geometry kernel, import name `geom2d`, LGPL) and
  [`svgelements`](https://pypi.org/project/svgelements/) (SVG parsing, MIT).

## Install

From PyPI, once released: `uv tool install tangential-knife-cnc` (or
`pipx install tangential-knife-cnc`). From a checkout, with
[uv](https://docs.astral.sh/uv/):

```sh
uv sync            # library + CLI into .venv
uv run tcnc --help
```

or as a tool: `uv tool install .` (or `pipx install .`).

## What it does

1. **Parse.** svgelements reads the file and composes the `viewBox` and
   every transform into a matrix per shape. Every visible path, rect,
   circle, ellipse, line, polyline, polygon and `<use>` clone is a
   candidate; `--id` (an element id or a clone's id) and `--layer` narrow
   the selection. Elements with `display:none` or `visibility:hidden` are
   skipped; opacity, clipping and masks are not considered. Malformed path
   data is an error, not a silently missing cut. Nothing else goes missing
   silently either: at the end of a run tcnc lists on standard error what
   the drawing contains that the program does not, one line per layer and
   reason (paths no operation selects, hidden content, text and images,
   which it cannot cut). The root `<svg>` must
   declare `width` and `height`; a physical unit there (mm, cm, in, pt,
   pc) is converted to px exactly before parsing, so the page has the size
   it declares and px content keeps its exact scale.
2. **Convert.** Every point goes through its shape's matrix exactly, then
   is scaled to millimetres and Y-flipped so the machine origin is the
   bottom-left corner of the page. Lines stay lines; circular arcs under a
   rotation, uniform scale or mirror stay arcs built from their exact
   endpoints (split to at most 90°, a nearly complete arc included);
   elliptical arcs and arcs under a shear or non-uniform scale become
   cubic Béziers, refined until a sampled error estimate is within
   `--biarc-tolerance` (an error, not a guess, if 4096 cubics are not
   enough); all Béziers then become biarcs (G2/G3) within the same
   tolerance. A zero-radius arc is the straight line SVG defines it as and
   an arc with identical endpoints is nothing, as SVG says; a thin ellipse
   is still an ellipse. The loader converts faithfully; the resolution
   policy lives in the toolpath stage: `--tolerance` is the job's
   resolution, circular arcs are checked (and if need be repaired) against
   their endpoints at it, a Bézier or an arc that never leaves it around
   its chord (and turns less than a degree) is the chord, and a path
   whose ends meet within it is closed. Runs of
   pieces shorter than it (dense polylines, tracer noise) are replaced by
   chords that stay within it of every vertex they replace, a run that
   fits inside it is left out, and where those chords sample a curve that
   is smooth at this resolution the blade heading follows the curve, so a
   densely sampled circle is still one smooth loop and a real corner is
   still a corner. It must be at least geom2d's numerical floor (`1e-8`);
   the toolpath remembers it and every later stage judges coincidence at
   the same distance.
3. **Order.** `--path-sort-method nearest` walks greedily from the origin,
   reversing open paths and rotating closed ones to start at the nearest
   vertex; `none` keeps file order.
4. **Blade offset** (optional). With `--blade-offset` the path is shifted
   forward, along the blade heading, by the distance the blade edge trails
   the axis (a chord whose heading turns along it is shifted in pieces so
   the edge stays within `--tolerance` of the artwork); corners get a
   small arc about the original vertex so the edge follows the artwork.
   Each connector remembers the whole turn of the source joint it spans (also after being split into 90° pieces), so a
   sharp corner is still a
   lift after compensation.
5. **Corners.** Wherever the blade heading would change by more than
   `--corner-angle` while cutting, the path is split and the knife lifts,
   turns and plunges again. Every run is extended at both ends along the
   blade heading by `--overcut` so the angled blade finishes the corner. A
   closed shape with no sharp corner is one loop that overruns its start.
   A closed shape keeps the start it was given when that start is already a
   corner (nearest-neighbour ordering picks a corner when there is one).
6. **Passes.** `--z-step` cuts each run in several passes down to
   `--z-depth`, lifting to `--z-safe` in between.
7. **Oscillation.** `--oscillation-mode operation` (or `program`, the
   same thing) switches the head on once at the start of the operation
   and off at its end; `cut` switches it around every plunge; `off` never
   emits `M3`/`M5`.
8. **Operations and tools** (job files). Each operation cuts one
   selection with one tool; a tool change (`T n M6` then `G43`, which
   applies the tool table's offsets) is written whenever the tool differs
   from the one in use. A knife and a creaser are tangential and lift at
   corners; a pen parks the A axis once at its mounting angle, never
   lifts at corners and writes no A words. See "Job files".

The A axis follows the cut tangent (plus `--a-offset` for the blade
mounting angle). At every corner and every rapid it takes the shortest
rotation, so its value accumulates around closed shapes. Configure the
A axis in LinuxCNC as an **unwrapped** rotary axis (no `WRAPPED_ROTARY`);
a wrapped axis rejects absolute values at or beyond ±360 and would need a
different encoding. Every program assumes A = 0 at its start and ends by
unwinding to `A0` after the last lift, with the head off, so one sheet
leaves the axis where the next one expects it.

## Options

All lengths are in millimetres, times in seconds, angles in degrees.

| Group    | Option                                     | Default     | Meaning                                                                     |
|----------|--------------------------------------------|-------------|-----------------------------------------------------------------------------|
| I/O      | `INPUT`                                    |             | SVG file to cut                                                             |
|          | `-o`, `--output PATH`                      | `INPUT.ngc` | G-code file                                                                 |
|          | `--preview PATH`                           |             | also write an SVG preview of the cut plan                                   |
|          | `--id ID`                                  |             | cut only these element ids (repeatable)                                     |
|          | `--layer NAME`                             |             | cut only elements inside this Inkscape layer label or group id (repeatable) |
|          | `--flip-y` / `--no-flip-y`                 | on          | machine origin at the bottom left                                           |
|          | `--gcode-comments` / `--no-gcode-comments` | on          | comments in the output                                                      |
|          | `--gcode-line-numbers`                     | off         | `N` line numbers                                                            |
|          | `--write-settings`                         | off         | list every option in the header                                             |
|          | `--timestamp` / `--no-timestamp`           | on          | the `Created` line; off makes reruns byte-identical                         |
|          | `--debug`                                  | off         | tracebacks on errors                                                        |
| Geometry | `--tolerance`                              | `0.01`      | job resolution (mm), at least `1e-8`                                        |
|          | `--biarc-tolerance`                        | `0.01`      | curve-to-biarc fit tolerance, at least `1e-8`                               |
|          | `--biarc-max-depth`                        | `8`         | curve subdivision limit (halvings per inflection-free span)                 |
|          | `--output-precision`                       | `3`         | decimals in G-code words                                                    |
| Machine  | `--xy-feed`                                | `250`       | XY feed, mm/min                                                             |
|          | `--z-feed`                                 | `250`       | plunge feed, mm/min                                                         |
|          | `--a-feed`                                 | `60`        | A feed, deg/min (used for in-place rotations)                               |
|          | `--z-safe`                                 | `10`        | Z for rapids; must be above the material surface (Z0)                       |
|          | `--z-depth`                                | `-1`        | final depth, at or below the surface                                        |
|          | `--z-step`                                 | `0`         | depth per pass (0 = one pass)                                               |
|          | `--tool-wait`                              | `0`         | dwell after plunge and lift                                                 |
|          | `--blend-mode`                             | `default`   | `default` (leave the controller's), `blend` (G64) or `exact` (G61)          |
|          | `--blend-tolerance`                        | `0`         | G64 P value                                                                 |
| Knife    | `--corner-angle`                           | `15`        | lift threshold, degrees                                                     |
|          | `--overcut`                                | `0`         | extension at both ends of every run                                         |
|          | `--blade-offset`                           | `0`         | blade trail behind the axis (0 = off)                                       |
|          | `--blade-width`                            | `0`         | blade width, for the preview's heading ticks                                |
|          | `--a-offset`                               | `0`         | blade mounting angle added to every A                                       |
|          | `--oscillation-mode`                       | `program`   | `program` (alias of `operation`), `cut` or `off`                            |
|          | `--spindle-speed`                          | `0`         | `S` word for the head (0 = none)                                            |
|          | `--spindle-wait-on`                        | `0`         | dwell after switching the head on                                           |
| Paths    | `--path-sort-method`                       | `none`      | `none` or `nearest`                                                         |

Exit codes: `0` success, `1` bad option or usage (including an output path
that collides with the input or the preview, and a job file that cannot be
read or validated), `2` SVG problem (missing or malformed file, nothing
cuttable, an operation that selects nothing), `3` geometry, planning or
output-file failure. Outputs are published as one unit: both files are generated in
memory, written to unique temporary files, and only then moved into place;
if any step fails, files (or symlinks) already replaced are restored, so a
failed run leaves the previous G-code and preview exactly as they were,
and should a restoration itself fail the error names the backup that
still holds the previous content.

## Job files

`tcnc --job box.toml` runs a TOML job file. It names the tools of the
machine's tool table, the operations in cutting order, and optionally the
files; a positional SVG and `-o`/`--preview` on the command line override
the files, and the knife options above cannot be combined with `--job`.
`--job` can be repeated: later files override earlier `[job]` keys, merge
tools by name and append operations, so an operator's machine file (tools
with their numbers, depths and feeds) combines with a layout's
operations-only file (`tcnc --job machine.toml --job layout.toml
sheet.svg`). An operation may name a tool by kind, and takes `z_depth`
from its tool when it sets none. `--only NAME` and `--skip NAME`
(repeatable) run a subset of the operations, so a machine without a pen
can still cut a job that lists a marking operation.

```toml
[job]                        # job-wide settings; every key is optional
input = "box.svg"
z_safe = 8

[tools.knife]                # one table per tool
kind = "knife"
number = 1                   # T number; leave out for the tool already mounted
spindle_speed = 1000

[tools.creaser]
kind = "creaser"
number = 2
a_offset = 90                # degrees
corner_angle = 8             # this tool's own lift threshold

[tools.pen]
kind = "pen"
number = 3

[[operations]]               # in cutting order
name = "crease"
tool = "creaser"
layers = ["Crease"]
z_depth = -0.4

[[operations]]
name = "cut"
tool = "knife"
layers = ["Cut"]
z_depth = -1.5
overcut = 1.0

[[operations]]
name = "marks"
tool = "pen"
layers = ["Marks"]
z_depth = -0.5
z_safe = 3                   # per-operation safe height
```

Settings resolve operation, then tool, then job, then the built-in
defaults; `docs/job-file.md` is the full reference. A `[meta]` table has
no effect on the cut; its entries are copied into the program header (see
"Program header"), so a producer's own numbers travel with the program.
`[job]` takes `flip_y`, `tolerance`, `biarc_tolerance`,
`biarc_max_depth`, `output_precision`, `z_safe`, `tool_change_z`,
`blend_mode`, `blend_tolerance`, `gcode_comments`, `gcode_line_numbers`,
`write_settings`, `timestamp`, the feeds and `tool_wait`, plus `input`, `output` and
`preview`. A tool takes `kind`, `number`, `a_offset`, `corner_angle`,
`blade_offset`, `blade_width`, `oscillation`, `spindle_speed`,
`spindle_wait_on` and its own feed and wait defaults. An operation takes
`name`, `tool`, `ids`, `layers`, `z_depth`, `z_step`, `z_safe`, `overcut`,
`corner_angle`, `sort_method`, `oscillation_mode` and feed and wait
overrides. Unknown keys are errors; angles are degrees.

The tool kinds: a **knife** oscillates (`M3`/`M5`) by default, follows
the heading with the A axis and lifts at corners above its threshold (15° by default). A **creaser** is tangential too,
never oscillates, and
lifts at corners above its own threshold (10° by default; a wheel cannot
pivot in the material). A **pen** parks the A axis once at its mounting
angle, never lifts at corners, has no overcut, no blade offset and a
single pass. A tool without a number is the one already mounted; it can
only be used by the leading operations, since the program cannot change
back to it. Nothing LinuxCNC does itself is repeated: the program does
not move to a change position, wait for the change or set offsets by
hand; `G43` after `M6` applies the loaded tool's tool-table offsets, and
each tool is assumed to have been touched off so that Z0 is the material
surface. With `tool_change_z` set, the program goes to that height in
machine coordinates (`G53 G0 Z`) before every change, a frame no work
offset or tool length can shift; without it, no retract is written and
the controller's `TOOL_CHANGE_QUILL_UP` is expected to lift the head.
After `G43` every axis is positioned again explicitly,
since `M6` may have moved the machine and `G43` changes the compensated
coordinates. The job file itself can never be an output.

## Program header

Every program says what produced it. For
`tcnc --job machine.toml --job layout.toml layout.svg`:

```gcode
%
; Generated by tcnc 1.3.0
; Created 2026-09-20T18:02:11+00:00
; source: layout.svg sha256=77d2a333da569bdd48616c25c3bbdc6498a543fe7f6ded97909e9467972033b9
; job-file: machine.toml sha256=5f0c…
; job-file: layout.toml sha256=a91e…
; meta: revision = "af34e779b5d020d0"
; meta: layout.name = "A"
; versions: tcnc 1.3.0, tangential-knife-cnc-geometry 1.0.0, svgelements 1.9.6, python 3.14.7
; Units: mm; A axis unwrapped; material surface at Z0
```

- `source:` is the drawing's file name, never its directory, and the
  SHA-256 of its bytes as they are on disk, the value `sha256sum` prints.
- `job-file:` is one line per `--job`, in command-line order, hashed the
  same way. Without job files the line is `job-file: none (command-line
  options)`; `--write-settings` lists those options.
- `meta:` is one line per `[meta]` entry. The files' tables layer like
  `[job]`: a later file overrides a key, which keeps the position where
  it first appeared. Nested tables become dotted keys and values are
  written in TOML form. A key or value with a control character, or an
  entry whose line would be longer than the 252 bytes LinuxCNC reads, is
  a usage error naming the key and the file.
- `versions:` names tcnc, the geometry kernel, svgelements and Python,
  since a kernel update can change arcs under the same tcnc version.
- These lines and `Generated by` are written even with
  `--no-gcode-comments`; every other comment follows that option.
- `Created` is the time of the run, or `SOURCE_DATE_EPOCH` when it is set.
  `--no-timestamp` or `timestamp = false` in `[job]` leaves the line out,
  and then the same drawing, job files and versions give a byte-identical
  program. `--timestamp` / `--no-timestamp` override the job file.

In the library, `write_program(plan, provenance=...)` writes the same
lines from a `tcnc.Provenance`, and `tcnc.cli.run` builds one from its
drawing and loaded job files; `load_job_files` returns the hashes and the
layered `[meta]` as `JobFile.files` and `JobFile.meta`.

## Machine contract

- Header modes: `G17` (XY plane), `G21` (millimetres), `G90` (absolute),
  `G94` (feed per minute), `G91.1` (arc centres relative to the start),
  `G97` (spindle speed in RPM), `G40`, then `G64`/`G61` only when
  `--blend-mode` asks for it. The active work coordinate system and the
  tool length compensation are left as the controller has them: a
  program without a tool change runs in the state it starts in, so Z0
  must be the material surface in that state.
- The material surface is Z0. `--z-depth` is below it, `--z-safe` above
  it and above every pass. Heights, the depth step and the feeds are
  validated on the values the machine will read, i.e. after rounding to
  `--output-precision`. Passes are planned on the grid of depths the
  output can represent, spaced by the largest representable step not
  above `--z-step`, so no written increment exceeds the step and no depth
  is written twice; a step below the output resolution is rejected, and a
  job may have at most 1000 passes.
- Every word is written at `--output-precision` decimals and the writer
  tracks the rounded values, so modal suppression, arc validation and the
  choice of feed see what the controller sees. The default feed is chosen
  from the axes that still move after rounding (XY, else Z, else A). An
  arc is validated the way LinuxCNC reads it: the radii at both rounded
  ends must agree within 0.005 mm, and the directed sweep the rounded
  words describe must be the nominal sweep (equal start and end angles
  mean a full turn to the controller). An arc that cannot be expressed at
  that precision (its ends collapse onto each other or onto the centre, or
  the written sweep would differ) becomes a straight move when its chord
  is within the output resolution of the arc, and an error otherwise. Segments that meet
  within `--tolerance` rather than exactly are joined by the next move;
  before an arc the writer first feeds to the arc's own start point (nothing is written when the rounded words do not
  change).
- The A axis is unwrapped, as above. Comments are sanitized so no text from
  the SVG can become a command.

## Preview

`--preview out.svg` writes a standalone SVG the size of the page, drawn the
way the part looks on the machine (Y up): cuts in red, lead-in and overcut
in light red, rapids as dashed green lines, blade-heading ticks along each
cut (spaced by `--blade-width`, but never more than about 2000 per plan),
and an orange dot wherever the knife lifts.

## Library

```python
from tcnc import KnifeOptions, load_document, plan_job, write_program

opts = KnifeOptions(z_depth=-1.5, overcut=1.0, blade_offset=0.2, sort_method="nearest")
doc = load_document("drawing.svg", opts)  # every loader setting from the options
plan = plan_job(doc, opts)  # selection, ordering, compensation, corners, per operation
gcode = write_program(plan)
```

A `Job` (tools plus operations) takes the place of `KnifeOptions`
everywhere; `KnifeOptions.to_job()` is the one-knife job, and
`load_job_file` reads a TOML job file. `Job.settings` resolves every
operation into an `OperationSettings` record; `plan_job` returns a
`JobPlan` of `OperationPlan`s; `plan_toolpaths(toolpaths, job)` plans
pre-built toolpaths under a single-operation job and `plan_cuts` builds
the cuts of one operation. `load_svg` is the loader with explicit keyword
settings; `SvgDocument.select` picks paths by id or layer. Every model
type is a frozen, slotted dataclass and the settings records are
keyword-only. `Toolpath` and `Cut` validate that their segments connect
within their `tolerance` (`None` means geom2d's `EPSILON`), that a closed
path or loop meets itself, and that arcs sweep at most 90°; `Hints`
rejects non-finite angles and a rotation that does not lead from its
start heading to its end heading. `Toolpath.from_geometry` takes the job
tolerance and the toolpath carries it through ordering, compensation and
corner planning into every `Cut`. Errors are `ValueError` subclasses:
`OptionError`, `SvgError`, `PlanError`, `OutputError` (also from
`write_preview`), plus `geom2d.GeometryError`.

## Dependencies

- [`tangential-knife-cnc-geometry`](https://github.com/Cabalist/tangential-knife-cnc-geometry)
  1.0 (import name `geom2d`): the 2D geometry kernel. tcnc relies on `P`, `Line`, `Arc`, `CubicBezier`, the
  `Segment` protocol and `Path` helpers (`path_length`, `path_bounding_box`,
  `path_is_closed`), `Arc.from_sweep` and its construction invariant,
  `split_max_sweep`, `biarc_approximation`, `calc_rotation`,
  `normalize_angle`, `angle_eq`, `segments_are_g1`, and the
  `GeometryError` hierarchy. Its dataclasses are frozen and hashable with
  positional field order `Line(p1, p2)` and `Arc(p1, p2, radius, angle,
  center)`, which the G-code writer matches on. `angle` is the signed
  sweep in radians, CCW positive. geom2d's `EPSILON` (`1e-8`) is only its
  numerical floor; the job's own resolution is passed explicitly through
  the `tolerance` keywords of `Arc.from_sweep`, `P.almost_equal`,
  `path_is_closed` and `segments_are_g1`, and both tolerances given to
  `KnifeOptions` must be at least `EPSILON` (`biarc_approximation` rejects
  anything finer).
- [`svgelements`](https://pypi.org/project/svgelements/): parsing only.
  tcnc reads unreified shapes and applies each shape's matrix itself.

## Development

```sh
uv sync --all-groups
uv run prek run --all-files   # ruff check + format, ty, pyrefly
uv run pytest                 # unit, fixture and golden tests
TCNC_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py   # regenerate goldens on purpose
make docs                     # the documentation site, into docs/_build/html
```

The [documentation site](https://cabalist.github.io/tangential-knife-cnc/)
is this README, `docs/job-file.md`, `docs/machine-deployment.md`, the
changelog and a library reference generated from the docstrings
(`docs/api.rst`). The docs workflow builds it with warnings as errors and
deploys it to GitHub Pages on every push to `main`.

Releasing:

```sh
make check                   # the CI checks, locally
make release VERSION=1.2.3   # set the version, lock, check, commit, tag v1.2.3 and push
```

`release` wants a clean tree on `main`, a `## X (unreleased)` section at
the top of `CHANGELOG.md` (it becomes `## 1.2.3 (date)`) and a tag that
exists neither locally nor on `origin`. The version lives in
`pyproject.toml` alone: `uv.lock` records it (`uv lock` refreshes that)
and `tcnc.__version__` reads it from the installed metadata. Pushing the
tag triggers the publish workflow, which runs the same checks, builds the
wheel and uploads it to PyPI. If `release` stops after the bump (a failed
check, a hook), fix the cause and run it again: it finds the version
already set and goes on to lock, check, commit, tag and push. It insists
on a clean tree, so with the bump still uncommitted either commit it or
run `make release-tag VERSION=1.2.3`, which does only the commit, tag and
push. The goldens pin the version they embed, so a bump does not change
them.

`stubs/svgelements/` holds the type stubs the checkers use for svgelements (its source is ISO-8859-1 encoded and
unreadable to them); keep the stubs
in step with what `src/tcnc/svg.py` uses. The hooks run `uv run --locked`,
so they fail rather than resolve or change dependencies. The geometry
kernel comes from PyPI (`tangential-knife-cnc-geometry`); to work against
a local checkout of it, add a `[tool.uv.sources]` path entry locally and
do not commit it. CI runs the checks, builds the wheel and installs it
into a clean environment; publishing runs the same steps before building.
Dependabot proposes weekly, grouped updates for the actions and for the
uv lock (runtime dependencies and tooling separately).

## Licence and provenance

LGPL-3.0-or-later. Rewritten in 2026 from
[utlco/utl-tcnc](https://github.com/utlco/utl-tcnc) by Claude Zervas, which
was an Inkscape extension for a brush and knife machine; this version drops
Inkscape support and the brush features, and targets the oscillating knife
only. See `CHANGELOG.md`.
