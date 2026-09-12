# Changelog

## 1.0.1 (unreleased)

Metric only and a job-scoped tolerance (both API changes):

- Every length is a millimetre and the header sets `G21`; `--gcode-units`,
  `KnifeOptions.gcode_units` and `load_svg(unit_scale=...)` are gone. The
  loader derives the mm-per-px scale from the page's physical size (96 px
  per inch otherwise). Defaults are metric: 0.01 mm tolerances, 3 decimals,
  250 mm/min feeds, 10 mm safe height, 1 mm depth. LinuxCNC's arc radius
  tolerance is the metric 0.005 mm.
- `--tolerance` is the job's resolution and reaches geom2d through its new
  `tolerance` keywords: `Arc.from_sweep` validates and repairs parser arcs
  at it, `Toolpath` stores it and checks connectivity and closure at it,
  blade compensation and `segments_are_g1` judge joints at it. Runs of
  pieces shorter than it are replaced by their chord (or dropped when the
  chord is shorter too) instead of being dropped one by one. Both
  tolerances must be at least geom2d's `EPSILON`.
- The writer feeds to an arc's own start before `G2`/`G3` (a no-op when
  the rounded words do not change) and lets modal suppression decide when
  an in-place rotation is needed; `pass_count` forgives float noise only,
  not the job tolerance.
- `Toolpath.from_geometry` reports geom2d approximation failures as
  `PlanError` with the source id; a loop's overrun is validated against
  the loop's start.

Fixes from the second audit (2026-09-12), all with behavioural tests:

- Writer: an arc is validated on the sweep LinuxCNC reconstructs from the
  rounded words as well as on its radii, so rounded endpoints on one ray
  can no longer turn a tiny arc into a full circle; the default feed is
  chosen from the axes that still move after rounding, so an in-place
  rotation always gets the A feed; `GCodeWriter.arc` takes the signed
  ``sweep`` instead of ``clockwise``.
- Options: safe height, depth, depth step, feeds and blend tolerance are
  validated on their rounded words; `z_depth` must be below the surface;
  at most 1000 passes; a pass that would repeat the final depth's word is
  dropped; a sub-normal step no longer overflows.
- Compensation and corners: `Hints.joint_turn` carries the source joint's
  whole turn through connector splitting and subdivision, so a corner
  above `--corner-angle` still lifts after `--blade-offset` whatever the
  threshold; `Hints.turn` remains the rotation across one piece.
- Model: `Hints` rejects non-finite angles and an inconsistent rotation;
  `Cut` enforces the toolpath invariants (connectivity at its tolerance,
  loop closure, 90° arcs) on its core and extensions, so nothing built by
  hand can reach the writer unchecked.
- SVG: a nearly complete arc is kept (only identical endpoints or a zero
  radius follow SVG's special rules); a thin ellipse is an ellipse, not
  its chord; the ellipse refinement never returns an unverified count and
  raises `SvgError` at 4096 cubics; the small singular value comes from
  the determinant (a 1e9:1 scale is not "degenerate"); physical root
  sizes are converted to px exactly before parsing, so px content keeps
  its scale on a mm page; the loader no longer drops short pieces (the
  toolpath stage does, consistently), so runs of short links load.
- Output: `tcnc.output.publish` replaces the G-code and preview as one
  unit and restores the previous files on any failure; destinations are
  checked first; `write_preview` uses it and raises `OutputError`.
- Preview: heading ticks are bounded (about 2000 per plan) however small
  `--blade-width` is.
- Library: `load_document(path, options)` forwards every loader setting.
- Defaults: `--biarc-max-depth` is 8 (geom2d's own); 4 was not enough for
  some curves at the 0.01 mm metric tolerance.
- CI: both repositories are checked out inside the workspace (a checkout
  path outside it is rejected by `actions/checkout`); publishing runs the
  format check and the wheel smoke test; hooks run `uv run --locked`;
  ruff also enforces ERA, LOG, ICN, INP, SLOT and N.

Fixes from the first audit of the rewrite (2026-09-12):

- Writer: rounded-value tracking; sub-resolution arcs become straight
  moves and off-circle rounded arcs are errors; comments are sanitized;
  the header sets `G94`, `G91.1` and `G97`; pass depths are computed once.
- Compensation: connectors record the joint turn so sharp corners still
  lift after `--blade-offset`; connector arcs are split to 90°; lead-in
  and overcut follow the blade heading; subdivision interpolates headings
  with the shortest rotation, shared with the preview ticks.
- Planning: a closed path keeps a start that is already a corner; nearest
  ordering chooses among corners; `Toolpath` validates connectivity,
  closure and arc sweep; `Cut` validates its extensions; a shared
  `plan_job` pipeline serves the CLI and the library.
- SVG: explicit per-shape transforms (exact points, similarity-checked
  arcs, tolerance-budgeted cubics for ellipses and sheared arcs),
  zero-radius arcs as lines, `<use>` clones, `on_error="raise"`, XML and
  root-element errors as `SvgError`, exact page-declared unit scale,
  required `width`/`height`.
- Options: keyword-only, finiteness checks, `z_safe` above the surface and
  every pass, bounded pass count, `blend_mode="default"` instead of `""`.
- CLI: distinct input/output/preview paths, unique temporary files, output
  errors as exit code 3, explicit option construction.
- Preview: correct reflection when the lower Y bound is not zero.
- Documentation: unwrapped A axis (a wrapped axis is incompatible with this
  output), machine contract, visibility policy.
- Tooling: FBT, C90 and SLF rules on; PLR0912/PLR0915/PLR0917/PERF401/
  PERF403 no longer ignored; test annotations required; ty and pyrefly
  test exemptions removed; CI uses the locked resolution, checks stubs and
  installs the built wheel; publishing runs the checks first.

## 1.0.0 (2026-09-12)

A rewrite. Nothing from the 0.x API survives.

- Scope: SVG file in, LinuxCNC G-code out, for an oscillating tangential
  knife. Inkscape support (the inx, the extension class, in-document
  preview layers, `inkstall`) and every brush feature (reload, soft landing
  and takeoff, tool-width fillets, post-offset smoothing) are removed, as is
  the `rubens6k` target.
- Input: svgelements parses the SVG (transforms, viewBox, units, elliptical
  arcs); `--id` and `--layer` select elements; hidden elements are skipped.
  The utl-inkext dependency is gone.
- Corners: heading changes above `--corner-angle` lift, rotate and
  re-plunge, with `--overcut` extending every run at both ends.
- Passes and oscillation: `--z-step` passes per cut; the head is switched
  once per program, once per cut, or never (`--oscillation-mode`).
- Units: every length option is in the G-code units; Z heights are no
  longer scaled by the document unit factor; waits are seconds everywhere.
- Preview: optional standalone SVG (`--preview`).
- Model: frozen, slotted dataclasses throughout (`KnifeOptions`, `Segment`
  with heading hints, `Toolpath`, `Cut`, `CutPlan`); no tuple subclasses.
- Writer: every axis is tracked, feed words are recorded only when written,
  arc end points are validated, output is written atomically.
- Tooling: Python 3.14 only; ruff, ty and pyrefly; prek hooks; golden-file
  tests for the whole pipeline.
- Depends on the utl-geom2d 1.0.0 rebuild (frozen dataclasses, `Segment`
  protocol, wrap-safe `angle_eq`, `Arc.from_sweep`, `split_max_sweep`).

The 55 defects catalogued in the pre-rewrite review of the upstream code are
closed by this release.
