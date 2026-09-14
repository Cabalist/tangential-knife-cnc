# Job file reference

`tcnc --job JOB.toml [--job MORE.toml ...] [--only NAME] [--skip NAME] [DRAWING.svg] [-o OUT.ngc] [--preview OUT.svg]`

A job file is TOML with up to four tables: `[job]`, `[tools.<name>]`
(one per tool), `[[operations]]` (an array, in cutting order) and an
optional `[meta]` that tcnc copies into the program header. `--job` may be given several times:
the files are layered in order (see "Layering"). Unknown keys anywhere else are
errors, and every value must have the type its key expects: `true`/`false`
for booleans, integers for counts and tool numbers, numbers for lengths
and times, strings for names. Lengths are millimetres, times seconds,
feeds millimetres or degrees per minute, angles **degrees**. Paths in
`[job]` are relative to the job file's directory. A positional drawing and
`-o`/`--preview` on the command line override the file's `input`,
`output` and `preview`; the single-knife options cannot be combined with
`--job`.

Settings that exist at several levels resolve **operation, then tool,
then job, then the built-in default**.

A run ends by listing on standard error what the drawing contains that
the program does not: paths in layers no operation selects, hidden
content, and text or images, one line per layer and reason. A layer the
job is not meant to cut shows up there every time; a layer it should have
cut shows up there by mistake.

## `[job]`

| Key                  | Type                          | Default           | Meaning                                                              |
|----------------------|-------------------------------|-------------------|----------------------------------------------------------------------|
| `input`              | path                          | (command line)    | the drawing to cut                                                   |
| `output`             | path                          | input with `.ngc` | the program to write                                                 |
| `preview`            | path                          | none              | an SVG preview of the plan                                           |
| `flip_y`             | bool                          | `true`            | machine origin at the bottom left of the page                        |
| `tolerance`          | mm                            | `0.01`            | job resolution; at least geom2d's `1e-8`                             |
| `biarc_tolerance`    | mm                            | `0.01`            | curve to biarc fit tolerance                                         |
| `biarc_max_depth`    | int                           | `8`               | curve subdivision limit                                              |
| `output_precision`   | int                           | `3`               | decimals in G-code words (0 to 9)                                    |
| `z_safe`             | mm                            | `10`              | rapid height above the surface (Z0); the default for every operation |
| `tool_change_z`      | mm                            | none              | machine-coordinate Z (`G53`) before every tool change; omit: none    |
| `blend_mode`         | `default` / `blend` / `exact` | `default`         | trajectory mode (`G64` / `G61`)                                      |
| `blend_tolerance`    | mm                            | `0`               | `G64 P` value                                                        |
| `gcode_comments`     | bool                          | `true`            | comments in the program                                              |
| `gcode_line_numbers` | bool                          | `false`           | `N` numbers                                                          |
| `write_settings`     | bool                          | `false`           | list every setting in the header                                     |
| `timestamp`          | bool                          | `true`            | the `Created` header line; `false` for byte-identical reruns         |
| `xy_feed`            | mm/min                        | `250`             | default XY feed for every tool                                       |
| `z_feed`             | mm/min                        | `250`             | default plunge feed                                                  |
| `a_feed`             | deg/min                       | `60`              | default A feed for in-place rotations                                |
| `tool_wait`          | s                             | `0`               | default dwell after plunge and lift                                  |

## `[tools.<name>]`

The table key is the tool's name. An operation refers to a tool by that
name, or by its kind (`knife`, `creaser`, `pen`) when no tool has the
name and the job has exactly one tool of that kind.

| Key                                        | Type                        | Default                      | Meaning                                                                                |
|--------------------------------------------|-----------------------------|------------------------------|----------------------------------------------------------------------------------------|
| `kind`                                     | `knife` / `creaser` / `pen` | required                     | see "Tool kinds"                                                                       |
| `number`                                   | int                         | none                         | `T` number; omit for the tool already mounted (only the leading operations may use it) |
| `a_offset`                                 | degrees                     | `0`                          | mounting angle added to every A value; a pen parks there                               |
| `corner_angle`                             | degrees                     | knife `15`, creaser `10`     | lift threshold; not allowed on a pen                                                   |
| `blade_offset`                             | mm                          | `0`                          | trail of the edge or wheel contact behind the A axis; must be 0 on a pen               |
| `blade_width`                              | mm                          | `0`                          | preview tick spacing                                                                   |
| `oscillation`                              | bool                        | knife `true`, others `false` | switched with `M3`/`M5`; cannot be `true` on a creaser or pen                          |
| `spindle_speed`                            | int                         | `0`                          | `S` word with `M3`; 0 omits it                                                         |
| `spindle_wait_on`                          | s                           | `0`                          | dwell after `M3`                                                                       |
| `z_depth`                                  | mm                          | none                         | this tool's default depth for its operations (negative)                                |
| `z_step`                                   | mm                          | none                         | this tool's default depth per pass; must be 0 on a pen                                 |
| `xy_feed`, `z_feed`, `a_feed`, `tool_wait` | as in `[job]`               | job values                   | this tool's defaults                                                                   |

## `[[operations]]`

| Key                                        | Type                        | Default                                        | Meaning                                                                                         |
|--------------------------------------------|-----------------------------|------------------------------------------------|-------------------------------------------------------------------------------------------------|
| `name`                                     | string                      | `op N`                                         | label for comments, the summary and the preview legend                                          |
| `tool`                                     | string                      | required                                       | a tool name from `[tools]`, or a kind                                                           |
| `layers`                                   | list of strings             | all                                            | cut only elements inside a group whose id or Inkscape label is listed                           |
| `ids`                                      | list of strings             | all                                            | cut only elements with these ids (or clones of them)                                            |
| `z_depth`                                  | mm                          | tool value                                     | Z while the tool is down; below the surface, so negative; required on the operation or its tool |
| `z_step`                                   | mm                          | tool value, else `0`                           | depth per pass; 0 for one pass; must be 0 on a pen                                              |
| `z_safe`                                   | mm                          | job value                                      | this operation's rapid height                                                                   |
| `overcut`                                  | mm                          | `0`                                            | extension at both ends of every run; must be 0 on a pen                                         |
| `corner_angle`                             | degrees                     | tool value                                     | lift threshold for this operation; not allowed on a pen                                         |
| `sort_method`                              | `none` / `nearest`          | `none`                                         | file order, or nearest neighbour                                                                |
| `oscillation_mode`                         | `operation` / `cut` / `off` | `operation` if the tool oscillates, else `off` | head on for the operation, around every cut, or never                                           |
| `xy_feed`, `z_feed`, `a_feed`, `tool_wait` | as in `[job]`               | tool values                                    | overrides                                                                                       |

Both `layers` and `ids` empty means everything visible.

## Layering

With several `--job` files the later ones win: their `[job]` keys
override earlier ones, their tools merge into earlier tools of the same
name key by key, and their operations are appended in order. Paths in a
file's `[job]` are relative to that file. A **machine file** (the tools
with their numbers, depths and feeds) combines this way with a **layout
file** that only lists operations:

```sh
tcnc --job machine.toml --job layout.toml sheet.svg -o sheet.ngc
```

```toml
# machine.toml: the machine's tools and job-wide settings
[job]
z_safe = 8

[tools.knife]
kind = "knife"
number = 1
z_depth = -1.5          # through this material

[tools.creaser]
kind = "creaser"
number = 2
z_depth = -0.4          # score depth for this material

[tools.pen]
kind = "pen"
number = 3
z_depth = -0.5
```

```toml
# layout.toml: the operations for one drawing
[[operations]]
name = "mark"
tool = "pen"            # a kind: the machine file's pen, whatever it is called
layers = ["MARK"]

[[operations]]
name = "score"
tool = "creaser"
layers = ["SCORE"]

[[operations]]
name = "cut"
tool = "knife"
layers = ["CUT"]
overcut = 0
sort_method = "none"
```

A layout file on its own is not a job: it has no tools.

`--only NAME` and `--skip NAME` (both repeatable) run a subset of the
operations, matched by name (`op N` for an unnamed one) and kept in job
order: `--skip mark` on a machine without a pen, `--only cut` to cut
alone. A name the job does not have, or a selection that leaves nothing,
is a usage error. In the library, `Job.select(only=..., skip=...)`.

## `[meta]`

Any keys and nesting, with no effect on the cut. Whoever writes the file
keeps provenance and numbers that have no tcnc meaning there, and tcnc
copies them into the program header as `meta:` lines (README, "Program
header"). The tables of several files layer like `[job]`: a later file
overrides a key, and the key keeps the position where it first appeared.
Nested tables become dotted keys, so `[meta.layout]` with `name = "A"` is
written `meta: layout.name = "A"`; values are written in TOML form. A key
or value containing a newline or other control character, or an entry
whose header line would exceed the 252 bytes LinuxCNC reads, is a usage
error naming the key and the file.

## Tool kinds

|                              | knife                | creaser              | pen                           |
|------------------------------|----------------------|----------------------|-------------------------------|
| A axis follows the heading   | yes                  | yes                  | no: parked once at `a_offset` |
| Corners above `corner_angle` | lift, rotate, plunge | lift, rotate, plunge | never lifts                   |
| `overcut`                    | allowed              | allowed              | must be 0                     |
| `blade_offset`               | allowed              | allowed              | must be 0                     |
| `oscillation`                | default on           | must be off          | must be off                   |
| `z_step`                     | allowed              | allowed              | must be 0                     |

There is no kerf setting: a knife slits rather than removing material,
so outlines are cut as drawn, and `blade_offset` compensates the edge
trailing the axis, a property of the tool. There is no lead-in setting;
`overcut` extends every run at both ends.

## Program layout

The operations follow each other in order. Before a change of tool the
oscillation is off and, with `tool_change_z` set, the program goes to
that height in machine coordinates (`G53 G0 Z`), which no work offset or
tool length can shift; without it no retract is written and the
controller's `TOOL_CHANGE_QUILL_UP` is expected to lift the head. It then
writes `T n M6` and `G43`, which applies the tool table's offsets
(LinuxCNC does not apply them on `M6` by itself), retracts to the
operation's safe height, parks a pen, and cuts. Nothing LinuxCNC does
itself (moving to the change position, waiting for the change, offsets)
is repeated. Each tool is assumed to have been touched off so that Z0 is
the material surface with its offset active; a program without a tool
change runs in the compensation state it starts in.

## Example

```toml
[meta]
generator = "example"        # any keys: copied into the program header, no effect on the cut

[job]
z_safe = 8

[tools.knife]
kind = "knife"
number = 1
spindle_speed = 1000

[tools.creaser]
kind = "creaser"
number = 2
corner_angle = 8

[tools.pen]
kind = "pen"
number = 3

[[operations]]
name = "mark"
tool = "pen"
layers = ["Marks"]
z_depth = -0.5

[[operations]]
name = "score"
tool = "creaser"
layers = ["Score"]
z_depth = -0.4

[[operations]]
name = "cut"
tool = "knife"
layers = ["Cut"]
z_depth = -1.5
overcut = 0
sort_method = "none"
```
