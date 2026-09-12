# tcnc

SVG in, LinuxCNC G-code out, for a 3.5-axis machine with an **oscillating
tangential knife**: X/Y position, Z depth, an A axis that keeps the blade
tangent to the cut, and the oscillating head switched like a spindle.

```sh
tcnc drawing.svg -o drawing.ngc --preview drawing-preview.svg \
    --z-depth -1.5 --corner-angle 15 --overcut 1
```

Everything is metric: option lengths are millimetres and the G-code is
written under `G21`.

## Requirements

- Python 3.14 or newer.
- A LinuxCNC-compatible controller with X, Y, Z and a rotary A axis about Z.
  The head is switched with `M3`/`M5` (with an optional `S` word).
- Dependencies: [`utl-geom2d`](https://github.com/Cabalist/utl-geom2d) (2D
  geometry kernel, LGPL) and [`svgelements`](https://pypi.org/project/svgelements/)
  (SVG parsing, MIT).

## Install

From a checkout, with [uv](https://docs.astral.sh/uv/):

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
   data is an error, not a silently missing cut. The root `<svg>` must
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
   their endpoints at it, a run of pieces shorter than it is replaced by
   its chord or dropped, and a path whose ends meet within it is closed.
   It must be at least geom2d's numerical floor (`1e-8`); the toolpath
   remembers it and every later stage judges coincidence at the same
   distance.
3. **Order.** `--path-sort-method nearest` walks greedily from the origin,
   reversing open paths and rotating closed ones to start at the nearest
   vertex; `none` keeps file order.
4. **Blade offset** (optional). With `--blade-offset` the path is shifted
   forward by the distance the blade edge trails the axis; corners get a
   small arc about the original vertex so the edge follows the artwork.
   Each connector remembers the whole turn of the source joint it spans
   (also after being split into 90° pieces), so a sharp corner is still a
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
7. **Oscillation.** `--oscillation-mode program` switches the head on once
   after the header and off before `M2`; `cut` switches it around every
   plunge; `off` never emits `M3`/`M5`.

The A axis follows the cut tangent (plus `--a-offset` for the blade
mounting angle). At every corner and every rapid it takes the shortest
rotation, so its value accumulates around closed shapes. Configure the
A axis in LinuxCNC as an **unwrapped** rotary axis (no `WRAPPED_ROTARY`);
a wrapped axis rejects absolute values at or beyond ±360 and would need a
different encoding.

## Options

All lengths are in millimetres, times in seconds, angles in degrees.

| Group | Option | Default | Meaning |
|---|---|---|---|
| I/O | `INPUT` | | SVG file to cut |
| | `-o`, `--output PATH` | `INPUT.ngc` | G-code file |
| | `--preview PATH` | | also write an SVG preview of the cut plan |
| | `--id ID` | | cut only these element ids (repeatable) |
| | `--layer NAME` | | cut only elements inside this Inkscape layer label or group id (repeatable) |
| | `--flip-y` / `--no-flip-y` | on | machine origin at the bottom left |
| | `--gcode-comments` / `--no-gcode-comments` | on | comments in the output |
| | `--gcode-line-numbers` | off | `N` line numbers |
| | `--write-settings` | off | list every option in the header |
| | `--debug` | off | tracebacks on errors |
| Geometry | `--tolerance` | `0.01` | job resolution (mm), at least `1e-8` |
| | `--biarc-tolerance` | `0.01` | curve-to-biarc fit tolerance, at least `1e-8` |
| | `--biarc-max-depth` | `8` | curve subdivision limit (halvings per inflection-free span) |
| | `--output-precision` | `3` | decimals in G-code words |
| Machine | `--xy-feed` | `250` | XY feed, mm/min |
| | `--z-feed` | `250` | plunge feed, mm/min |
| | `--a-feed` | `60` | A feed, deg/min (used for in-place rotations) |
| | `--z-safe` | `10` | Z for rapids; must be above the material surface (Z0) |
| | `--z-depth` | `-1` | final depth, at or below the surface |
| | `--z-step` | `0` | depth per pass (0 = one pass) |
| | `--tool-wait` | `0` | dwell after plunge and lift |
| | `--blend-mode` | `default` | `default` (leave the controller's), `blend` (G64) or `exact` (G61) |
| | `--blend-tolerance` | `0` | G64 P value |
| Knife | `--corner-angle` | `15` | lift threshold, degrees |
| | `--overcut` | `0` | extension at both ends of every run |
| | `--blade-offset` | `0` | blade trail behind the axis (0 = off) |
| | `--blade-width` | `0` | blade width, for the preview's heading ticks |
| | `--a-offset` | `0` | blade mounting angle added to every A |
| | `--oscillation-mode` | `program` | `program`, `cut` or `off` |
| | `--spindle-speed` | `0` | `S` word for the head (0 = none) |
| | `--spindle-wait-on` | `0` | dwell after switching the head on |
| Paths | `--path-sort-method` | `none` | `none` or `nearest` |

Exit codes: `0` success, `1` bad option or usage (including an output path
that collides with the input or the preview), `2` SVG problem (missing or
malformed file, nothing cuttable), `3` geometry, planning or output-file
failure. Outputs are published as one unit: both files are generated in
memory, written to unique temporary files, and only then moved into place;
if any step fails, files already replaced are restored, so a failed run
leaves the previous G-code and preview exactly as they were.

## Machine contract

- Header modes: `G17` (XY plane), `G21` (millimetres), `G90` (absolute),
  `G94` (feed per minute), `G91.1` (arc centres relative to the start),
  `G97` (spindle speed in RPM), `G40`, `G49`, then `G64`/`G61` only when
  `--blend-mode` asks for it. The active work coordinate system is left as
  the controller has it.
- The material surface is Z0. `--z-depth` is below it, `--z-safe` above
  it and above every pass. Heights, the depth step and the feeds are
  validated on the values the machine will read, i.e. after rounding to
  `--output-precision`, and a job may have at most 1000 passes.
- Every word is written at `--output-precision` decimals and the writer
  tracks the rounded values, so modal suppression, arc validation and the
  choice of feed see what the controller sees. The default feed is chosen
  from the axes that still move after rounding (XY, else Z, else A). An
  arc is validated the way LinuxCNC reads it: the radii at both rounded
  ends must agree within 0.005 mm, and the directed sweep the rounded
  words describe must be the nominal sweep (equal start and end angles
  mean a full turn to the controller). An arc that cannot be expressed at
  that precision becomes a straight move when its chord is within the
  output resolution of the arc, and an error otherwise. Segments that meet
  within `--tolerance` rather than exactly are joined by the next move;
  before an arc the writer first feeds to the arc's own start point
  (nothing is written when the rounded words do not change).
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
from tcnc import KnifeOptions, load_document, plan_job, toolpaths_from_document, write_program

opts = KnifeOptions(z_depth=-1.5, overcut=1.0, blade_offset=0.2, sort_method="nearest")
doc = load_document("drawing.svg", opts)                    # every loader setting from the options
plan = plan_job(toolpaths_from_document(doc, opts), opts)   # ordering, compensation, corners
gcode = write_program(plan)
```

`load_svg` is the same loader with explicit keyword settings. Every model
type (`KnifeOptions`, `Hints`, `Segment`, `Toolpath`, `Cut`, `CutPlan`) is
a frozen, slotted dataclass; `KnifeOptions` is keyword-only. `Toolpath` and
`Cut` validate that their segments connect within their `tolerance`
(`None` means geom2d's `EPSILON`), that a closed path or loop meets itself,
and that arcs sweep at most 90°; `Hints` rejects non-finite angles and a
rotation that does not lead from its start heading to its end heading.
`Toolpath.from_geometry` takes the job tolerance and the toolpath carries
it through ordering, compensation and corner planning into every `Cut`.
Errors are `ValueError` subclasses: `OptionError`, `SvgError`, `PlanError`,
`OutputError` (also from `write_preview`), plus `geom2d.GeometryError`.

## Dependencies

- [`utl-geom2d`](https://github.com/Cabalist/utl-geom2d) 1.0: the 2D
  geometry kernel. tcnc relies on `P`, `Line`, `Arc`, `CubicBezier`, the
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
```

`stubs/svgelements/` holds the type stubs the checkers use for svgelements
(its source is ISO-8859-1 encoded and unreadable to them); keep the stubs
in step with what `src/tcnc/svg.py` uses. The hooks run `uv run --locked`,
so they fail rather than resolve or change dependencies. CI checks this
repository and `utl-geom2d` out side by side inside the workspace (the
`../utl-geom2d` path source needs the sibling); publishing runs the same
checks, including the wheel smoke test, before building.

## Licence and provenance

LGPL-3.0-or-later. Rewritten in 2026 from
[utlco/utl-tcnc](https://github.com/utlco/utl-tcnc) by Claude Zervas, which
was an Inkscape extension for a brush and knife machine; this version drops
Inkscape support and the brush features, and targets the oscillating knife
only. See `CHANGELOG.md`.
