# Changelog

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

Fixes relative to 0.2.x are listed in `REVIEW_CHECKLIST.md`.
