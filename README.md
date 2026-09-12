# tcnc

SVG in, LinuxCNC G-code out, for a 3.5-axis machine with an **oscillating
tangential knife**: X/Y position, Z depth, an A axis that keeps the blade
tangent to the cut, and the oscillating head switched like a spindle.

```sh
tcnc drawing.svg -o drawing.ngc --preview drawing-preview.svg \
    --gcode-units in --z-depth -0.06 --corner-angle 15 --overcut 0.05
```

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

1. **Parse.** svgelements reads the file, applies every transform and the
   `viewBox`, and returns px at 96 px/in. Every visible path, rect, circle,
   ellipse, line, polyline and polygon is a candidate; `--id` and `--layer`
   narrow the selection. Hidden elements (`display:none`,
   `visibility:hidden`) are skipped.
2. **Convert.** Coordinates are scaled to the G-code units and Y is flipped
   so the machine origin is the bottom-left corner of the page. Lines stay
   lines, circular arcs stay arcs (split to at most 90°), quadratic and
   cubic Béziers and elliptical arcs become biarcs (G2/G3) within
   `--biarc-tolerance`. Zero-length pieces are dropped.
3. **Order.** `--path-sort-method nearest` walks greedily from the origin,
   reversing open paths and rotating closed ones to start at the nearest
   vertex; `none` keeps file order.
4. **Blade offset** (optional). With `--blade-offset` the path is shifted
   forward by the distance the blade edge trails the axis; corners get a
   small arc about the original vertex so the edge follows the artwork.
5. **Corners.** Wherever the heading would change by more than
   `--corner-angle` while cutting, the path is split and the knife lifts,
   turns and plunges again. Every run is extended along its tangent by
   `--overcut` at both ends so the angled blade finishes the corner. A
   closed shape with no sharp corner is one loop that overruns its start.
6. **Passes.** `--z-step` cuts each run in several passes down to
   `--z-depth`, lifting to `--z-safe` in between.
7. **Oscillation.** `--oscillation-mode program` switches the head on once
   after the header and off before `M2`; `cut` switches it around every
   plunge; `off` never emits `M3`/`M5`.

The A axis follows the cut tangent (plus `--a-offset` for the blade
mounting angle). At every corner and every rapid it takes the shortest
rotation, so its value accumulates around closed shapes; configure the
rotary axis as wrapped in LinuxCNC or accept large A values.

## Options

All lengths are in the G-code units (`--gcode-units in|mm`, default `in`),
times in seconds, angles in degrees.

| Group | Option | Default | Meaning |
|---|---|---|---|
| I/O | `INPUT` | | SVG file to cut |
| | `-o`, `--output PATH` | `INPUT.ngc` | G-code file |
| | `--preview PATH` | | also write an SVG preview of the cut plan |
| | `--id ID` | | cut only these element ids (repeatable) |
| | `--layer NAME` | | cut only elements inside this Inkscape layer label or group id (repeatable) |
| | `--gcode-units` | `in` | `in` or `mm` |
| | `--flip-y` / `--no-flip-y` | on | machine origin at the bottom left |
| | `--gcode-comments` / `--no-gcode-comments` | on | comments in the output |
| | `--gcode-line-numbers` | off | `N` line numbers |
| | `--write-settings` | off | list every option in the header |
| | `--debug` | off | tracebacks on errors |
| Geometry | `--tolerance` | `1e-6` | geometry tolerance |
| | `--biarc-tolerance` | `0.001` | curve-to-biarc fit tolerance |
| | `--biarc-max-depth` | `4` | curve subdivision limit |
| | `--output-precision` | `4` | decimals in G-code words |
| Machine | `--xy-feed` | `10` | XY feed, units/min |
| | `--z-feed` | `10` | plunge feed, units/min |
| | `--a-feed` | `60` | A feed, deg/min (used for in-place rotations) |
| | `--z-safe` | `1` | Z for rapids |
| | `--z-depth` | `-0.25` | final depth |
| | `--z-step` | `0` | depth per pass (0 = one pass) |
| | `--tool-wait` | `0` | dwell after plunge and lift |
| | `--blend-mode` | | `blend` (G64) or `exact` (G61) |
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

Exit codes: `0` success, `1` bad option or usage, `2` SVG problem (missing
file, nothing cuttable), `3` geometry or planning failure.

## Preview

`--preview out.svg` writes a standalone SVG the size of the page, drawn the
way the part looks on the machine (Y up): cuts in red, lead-in and overcut
in light red, rapids as dashed green lines, blade-heading ticks along each
cut (spaced by `--blade-width`), and an orange dot wherever the knife lifts.

## Library

```python
from tcnc import KnifeOptions, load_svg, plan_cuts, write_program, Toolpath

opts = KnifeOptions(gcode_units="mm", z_depth=-1.5, overcut=1.0)
doc = load_svg("drawing.svg", unit_scale=opts.unit_scale_from_px)
paths = [tp for p in doc.paths if (tp := Toolpath.from_geometry(p.geometry, source_id=p.source_id))]
gcode = write_program(plan_cuts(paths, opts))
```

Every model type (`KnifeOptions`, `Segment`, `Toolpath`, `Cut`, `CutPlan`)
is a frozen, slotted dataclass. Errors are `ValueError` subclasses:
`OptionError`, `SvgError`, `PlanError`, plus `geom2d.GeometryError`.

## Development

```sh
uv sync --all-groups
uv run prek run --all-files   # ruff check + format, ty, pyrefly
uv run pytest                 # unit, fixture and golden tests
TCNC_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py   # regenerate goldens on purpose
```

`stubs/svgelements/` holds the type stubs the checkers use for svgelements
(its source is ISO-8859-1 encoded and unreadable to them); keep the stubs
in step with what `src/tcnc/svg.py` uses.

## Licence and provenance

LGPL-3.0-or-later. Rewritten in 2026 from
[utlco/utl-tcnc](https://github.com/utlco/utl-tcnc) by Claude Zervas, which
was an Inkscape extension for a brush and knife machine; this version drops
Inkscape support and the brush features, and targets the oscillating knife
only. See `CHANGELOG.md`.
