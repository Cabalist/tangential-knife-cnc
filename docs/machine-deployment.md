# Deploying tcnc for the machine

Everything that has to be decided or configured on the real machine before
production cutting. tcnc's defaults were chosen without the machine; this
document lists them, says how to confirm each, and records what the
controller must provide. Keep the outcome in the operator's machine file (below), not in tcnc.

## What tcnc assumes of the controller

- LinuxCNC with X, Y, Z and a rotary **A axis about Z**, configured
  **unwrapped** (no `WRAPPED_ROTARY`): A values accumulate across closed
  shapes and can reach several full turns in one program. Do not put
  soft limits on A that a long program could hit. Every program assumes
  A = 0 at its start and ends by unwinding to `A0` after the last lift;
  re-home A between sheets all the same.
- The oscillating head is switched like a spindle, and that is all the
  program says about it: `M3` (with an optional `S` word) and `M5`, in
  `G97` mode. Everything else the head needs is derived in HAL. A
  frequency or stroke selection follows `spindle.0.on` and
  `spindle.0.speed-out` (the `S` value is then whatever that mapping
  expects, not a true RPM). A holding current for the blade rotation, a
  head enable, or anything else that must be live while the tool is in
  the material must not follow spindle-on: a creaser and a pen never get
  `M3`, and their A axis still holds an angle under load. Key such
  signals to the program state instead, so that they stay on through a
  feed hold or a pause and during MDI test cuts.
- Tool changes are `T n M6` followed by `G43`. The controller is
  responsible for the change itself: manual change prompts
  (`hal_manualtoolchange`) or a changer, `TOOL_CHANGE_POSITION` and
  `TOOL_CHANGE_QUILL_UP` as wanted. tcnc writes no dwell after the
  change; `M6` blocks until it is confirmed. Before a change the program
  either goes to `tool_change_z` in machine coordinates (`G53 G0 Z`, set
  in the machine file) or, without that setting, writes no retract at all
  and relies on `TOOL_CHANGE_QUILL_UP`. It never retracts in work
  coordinates before a change: before the first one it cannot know which
  tool length compensation is active, and `M2` leaves the last tool's on.
- **Tool offsets live in the tool table.** `G43` after `M6` applies the
  loaded tool's offsets, so every tool must be touched off so that **Z0
  is the material surface** with its offset active. A tool without a
  number in the machine file is the one already mounted and is never
  changed to. A program without a tool change never touches the
  compensation: it runs in the state the controller is in, so load the
  mounted tool (`T n M6` then `G43` in MDI) before running one.
- The header sets `G17 G21 G90 G94 G91.1 G97 G40` and, if asked,
  `G64 P` or `G61`; the work coordinate system and the tool length
  compensation are left as the controller has them.

## The machine file

The operator keeps one file per machine (and per blade, when several
knives exist: tcnc matches an operation's tool by kind and needs exactly
one tool of each kind used). Layout generators emit only operations; run
a sheet as:

```sh
tcnc --job machine.toml --job job.toml layouts/layout_a.svg -o layout_a.ngc --preview layout_a-preview.svg
tcnc --job machine.toml --job job.toml layouts/layout_a.svg -o layout_a.ngc --skip mark   # no pen mounted
```

Every program's header names the drawing and each job file by SHA-256,
carries the files' `[meta]` entries and the versions of tcnc, its
geometry kernel, svgelements and Python (README, "Program header"), so a
program on the controller can be matched to its inputs. Add
`--no-timestamp` when a rerun over the same inputs must give a
byte-identical program.

Template, with every value to confirm marked:

```toml
[job]
z_safe = 10             # confirm: clears the material and the holding fixture
tool_change_z = 0       # confirm: machine Z (G53) clear of everything for a change; omit to rely on TOOL_CHANGE_QUILL_UP
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
| `tool_change_z`                      | none                        | Machine Z (`G53`) for changes: the position with the head up, clear of the fixture; or unset, with `TOOL_CHANGE_QUILL_UP` retracting.       |
| `z_depth` per tool                   | -1.5, -0.4, -0.5 mm         | Knife: just through the sheet into the spoilboard; creaser: a clean fold without cracking; pen: a mark without a dent. Set per material.    |
| `z_step`                             | 0 (one pass)                | Only for thick material; every increment is at most the step.                                                                               |
| `corner_angle` knife / creaser       | 15° / 10°                   | Cut and crease a polygon with corners from 5° to 45°; the threshold is the smallest turn that shows tearing or scuffing when dragged round. |
| `overcut`                            | 0                           | Only for hand-made drawings; measure how far a corner is left joined at the top face with 0 and set the extension you want.                 |
| `blade_offset`                       | 0                           | See "Measuring". Expected to be non-zero for a drag-style knife edge.                                                                       |
| `a_offset` per tool                  | 0°                          | See "Measuring".                                                                                                                            |
| `spindle_speed`, `spindle_wait_on`   | 0, 0 s                      | The `S` value the head's HAL mapping expects (a frequency step or a rate), and the spin-up time if the controller does not wait itself.     |
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
   controller prompts or changes at each `M6` and that `G43` puts every tool's Z0 on the surface. `box.toml` sets no
   `tool_change_z`, so this run depends on `TOOL_CHANGE_QUILL_UP` for the retract before each change.
4. Load the knife in LinuxCNC (`T1 M6` then `G43` in MDI), cut the square for real in scrap and measure the corners
   (trail) and the line angle (mounting angle); update the machine file.
5. Run a layout generator package with `--skip mark` first, then with the pen. Read the skipped-content lines the
   run prints on standard error: with `--skip mark` the mark layer is listed as not selected, and anything else listed
   there (an extra group, hidden content, text) is content the program will not cut.
