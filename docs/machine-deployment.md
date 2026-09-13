# Deploying tcnc for the machine

Everything that has to be decided or configured on the real machine before
production cutting. tcnc's defaults were chosen without the machine; this
document lists them, says how to confirm each, and records what the
controller must provide. Keep the outcome in the operator's machine file (below), not in tcnc.

## What tcnc assumes of the controller

- LinuxCNC with X, Y, Z and a rotary **A axis about Z**, configured **unwrapped** (no `WRAPPED_ROTARY`): A values
  accumulate across closed
  shapes and can reach several full turns in one program. Do not put
  soft limits on A that a long program could hit.
- The oscillating head is switched like a spindle: `M3` (with an optional
  `S` word) and `M5`, `G97` mode. A creaser and a pen never get `M3`.
- Tool changes are `T n M6` followed by `G43`. The controller is
  responsible for the change itself: manual change prompts (`hal_manualtoolchange`) or a changer, `TOOL_CHANGE_POSITION`
  and
  `TOOL_CHANGE_QUILL_UP` as wanted. tcnc retracts to safe height before
  every change and writes no dwell after it; `M6` blocks until the change
  is confirmed.
- **Tool offsets live in the tool table.** `G43` after `M6` applies the
  loaded tool's offsets, so every tool must be touched off so that **Z0
  is the material surface** with its offset active. A tool without a
  number in the machine file is the one already mounted and is never
  changed to.
- The header sets `G17 G21 G90 G94 G91.1 G97 G40 G49` and, if asked,
  `G64 P` or `G61`; the work coordinate system is left as the controller
  has it. Before the first tool change the program retracts to the first
  operation's safe height with tool length compensation cancelled: make
  sure that height clears the material for whatever is mounted, or let
  `TOOL_CHANGE_QUILL_UP` do the retract.

## The machine file

The operator keeps one file per machine (and per blade, when several
knives exist: tcnc matches an operation's tool by kind and needs exactly
one tool of each kind used). Layout generators emit only operations; run
a sheet as:

```sh
tcnc --job machine.toml --job job.toml layouts/layout_a.svg -o layout_a.ngc --preview layout_a-preview.svg
tcnc --job machine.toml --job job.toml layouts/layout_a.svg -o layout_a.ngc --skip mark   # no pen mounted
```

Template, with every value to confirm marked:

```toml
[job]
z_safe = 10             # confirm: clears the material and the holding fixture
xy_feed = 250           # confirm: mm/min for the material
z_feed = 250            # confirm: plunge feed
a_feed = 60             # confirm: deg/min for in-place rotations
tool_wait = 0           # dwell after plunge and lift, seconds
# tolerance = 0.01, biarc_tolerance = 0.01, biarc_max_depth = 8, output_precision = 3: geometry defaults, see below

[tools.knife]
kind = "knife"
number = 1              # the tool table entry
z_depth = -1.5          # confirm per material: through the sheet
a_offset = 0            # confirm: blade mounting angle, degrees (see "Measuring")
blade_offset = 0.25     # confirm: the edge trails the axis by this much, mm (see "Measuring")
corner_angle = 15       # confirm: below this heading change the blade is dragged round, above it lifts
spindle_speed = 1000    # S word, 0 to omit
spindle_wait_on = 0     # dwell after M3 if the controller does not wait for at-speed

[tools.creaser]
kind = "creaser"
number = 2
z_depth = -0.4          # confirm per material: score, not through
a_offset = 0            # confirm: wheel mounting angle
corner_angle = 10       # confirm: a wheel scuffs sooner than a blade
blade_offset = 0        # confirm: wheel contact behind the axis, if any

[tools.pen]
kind = "pen"
number = 3
z_depth = -0.5          # confirm: pen pressure
a_offset = 0            # confirm: the pen is parked at this angle before drawing
```

`overcut` is per operation, not per tool: the layout generator's nests
reserve no room for one, so its files leave it at 0; hand-made drawings
may set it (`overcut = 1.0` extends every run by 1 mm at both ends).

## Values to confirm on the machine

| Setting                              | Default                     | How to confirm                                                                                                                              |
|--------------------------------------|-----------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| `xy_feed`, `z_feed`, `a_feed`        | 250, 250 mm/min, 60 deg/min | Cut test squares at increasing feeds; watch for tearing and for the A axis lagging at corners.                                              |
| `z_safe`                             | 10 mm                       | Must clear the material, clamps and any bridge; a pen operation may use a smaller `z_safe` of its own.                                      |
| `z_depth` per tool                   | -1.5, -0.4, -0.5 mm         | Knife: just through the sheet into the spoilboard; creaser: a clean fold without cracking; pen: a mark without a dent. Set per material.    |
| `z_step`                             | 0 (one pass)                | Only for thick material; every increment is at most the step.                                                                               |
| `corner_angle` knife / creaser       | 15° / 10°                   | Cut and crease a polygon with corners from 5° to 45°; the threshold is the smallest turn that shows tearing or scuffing when dragged round. |
| `overcut`                            | 0                           | Only for hand-made drawings; measure how far a corner is left joined at the top face with 0 and set the extension you want.                 |
| `blade_offset`                       | 0                           | See "Measuring". Expected to be non-zero for a drag-style knife edge.                                                                       |
| `a_offset` per tool                  | 0°                          | See "Measuring".                                                                                                                            |
| `spindle_speed`, `spindle_wait_on`   | 0, 0 s                      | The head's rated oscillation setting and spin-up time if the controller does not wait for at-speed itself.                                  |
| `tolerance`                          | 0.01 mm                     | The job's resolution; leave unless the artwork is finer than the machine can hold. Must stay at least 1e-8.                                 |
| `biarc_tolerance`, `biarc_max_depth` | 0.01 mm, 8                  | Curve fit; tighter costs segments. Leave.                                                                                                   |
| `output_precision`                   | 3                           | Decimals in every word; the controller's resolution. Leave at 3 for a metric machine.                                                       |
| `blend_mode`, `blend_tolerance`      | `default`                   | `blend` with `G64 P` for a smoother, faster path, `exact` for the sharpest corners.                                                         |

## Measuring the blade

- **Mounting angle (`a_offset`).** With the head at A0, cut a short
  straight line along +X. The angle between the cut and +X, measured with
  the sense that a counter-clockwise rotation of A is positive, is the
  offset to add, negated. Repeat after every blade change.
- **Trail (`blade_offset`).** Cut a square with `blade_offset = 0` and
  `overcut = 0`. At each corner the cut stops short of the vertex along
  the incoming edge and overshoots along the outgoing one by the same
  distance; that distance is the trail. Enter it, cut again, and the
  corners meet. A creasing wheel has a trail only if its contact point is
  behind the A axis.
- Preview ticks (`blade_width`) are for the eye only.

## First runs

1. `tcnc tests/files/square.svg -o square.ngc --preview square.svg --overcut 1` and open the preview: four runs with
   lead-in and overcut, one lift marker per cut.
2. Air-cut `square.ngc` with `z_depth` above the surface (`--z-depth` cannot be positive; raise the material instead or
   run with the head retracted) to watch the A axis follow the edges and the corners lift, rotate and plunge.
3. `tcnc --job tests/files/box.toml -o box.ngc --preview box.svg`: three operations and two tool changes; confirm the
   controller prompts or changes at each `M6` and that `G43` puts every tool's Z0 on the surface.
4. Cut the square for real in scrap and measure the corners (trail) and the line angle (mounting angle); update the
   machine file.
5. Run a layout generator package with `--skip mark` first, then with the pen.
