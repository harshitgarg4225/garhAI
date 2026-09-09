# Phase 8 verification — drawings + exports (the moat)

**Date: 2026-08-25/26.** Unlike the earlier ledgers, this one was written the day
the phase first EXECUTED, so most rows are settled by a run rather than a trace.
Convention unchanged: **EXECUTED** names the run that proves it, **TRACED** means
read-verified only, **UNVERIFIED** names the command that would settle it.

## 1. What executed, end to end

The full pipeline ran against a live local stack (Postgres 16, Redis, uvicorn
api, `services.drawings.worker`, moto S3 standing in for minio) on a real
project arranged over the API (the e2e base plan: plot + road + storey + five
walls + two openings):

    ops → saved version → compliance gate (nbc-core, worst_status=pass)
        → queue → drawings worker → 9 sheets → SVG+DXF per sheet → summary

- **EXECUTED** — 9 municipal sheets: A-01 Site Plan, A-02 Ground Floor Plan,
  A-03A–D North/East/South/West Elevations, A-04 Section A-A, A-05 Door &
  Window Schedule, A-06 Area Statement. Worker timings: load 11ms, draw 23ms,
  publish 346ms, total 387ms.
- **EXECUTED** — auto-dimensioning: 17 chains across the set, `chainSumOk:
true` (§7 step 5 — chains must sum exactly), 0 label collisions. The floor
  plan carries 3 dimension levels (overall / wall runs / openings) both axes,
  plus per-room chains.
- **EXECUTED** — `ezdxf.audit()` on the A-02 DXF: **0 errors, 0 fixes** (80,447
  bytes; the DoD's "opens without errors" clause). Layers follow the
  convention: `A-WALL, A-WALL-PART, A-DOOR, A-WIND, A-STAIR, A-DIM, A-TEXT,
A-AREA, A-TITL, Defpoints`. Content arrives as an INSERT (block reference),
  which LibreCAD/ODA resolve; a human open in LibreCAD remains UNVERIFIED.
- **EXECUTED** — the SVG twin renders the same sheet (13,626 bytes, 145
  elements) with the title block populated (sheet number, project, client,
  architect).
- **EXECUTED** — §13 downloads: `GET /projects/:id/sheets/:id.dxf` answers a
  short-lived signed envelope; `GET /downloads/{token}` 307-redirects to a
  10-minute presigned object URL. Followed and byte-verified.
- **EXECUTED** — the DXF **import** half (Phase 2's F1, same worker): upload →
  sandboxed ezdxf parse → layer picker → boundary op, driven from a real
  browser in the @smoke suite.
- **EXECUTED** — 296 `services/drawings/tests` (autodim, projection,
  elevations, sections, schedules, sheets, export, pipeline, handler, render)
  under real pytest with real ezdxf; plus the api-side sheet routes inside the
  1,923-test api run.

## 1b. What the sheets draw — re-verified 2026-09-09 (audit J08)

Run: `PYTHONPATH=.:apps/api .venv/bin/python -m pytest services/drawings/tests -q`
(537 passed) and `PYTHONPATH=.:apps/api .venv/bin/python scripts/sheet_goldens.py`
(4 models, 42 sheets, 137 chains, 0 label collisions, DXF audits clean, diff-clean).

- **EXECUTED** — every opening on every floor plan and elevation carries the tag
  A-05 assigns it; one `door_window_schedule()` feeds all three
  (`test_plan_opening_tags_are_the_schedule_sheets_tags`, with `{}` and a wrong
  mapping as negative controls).
- **EXECUTED** — room labels: NAME / clear W x D / m² / sq ft inside every room,
  the block stepping down 2.5→1.8 mm and turning 90° before shedding lines; a
  room of 2 m² or more never loses a value, and a shaft keeps its name
  (`test_every_room_on_every_plan_is_labelled_and_a_real_room_gets_all_four_values`).
  The per-room dimension cross-hairs are gone; the outer three levels remain.
- **EXECUTED** — the collision audit boxes dimension figures too, through one
  measurer (`render/labels.py`) shared by the worker, the harness and the test;
  it caught the "115" level-2 end stubs and the section bubbles on level 3, both
  fixed (`test_the_collision_audit_sees_dimension_figures`).
- **EXECUTED** — A-01: plot edges, footprint, road width and each setback
  chained; setbacks named by the rules engine's role for the edge and equal to
  its `providedMm` or the sheet is refused; PLOT SIZE in mm and ft-in; the
  engine's coverage / FAR / setback rows printed
  (`test_site_plan_dimensions_every_side_the_footprint_the_road_and_the_setbacks`,
  `test_site_plan_refuses_a_setback_that_disagrees_with_the_compliance_report`).
- **EXECUTED** — A-04 is `services.drawings.sections.build_section` on the sheet:
  hatched cut walls, slabs at their thickness minus the stair well, plinth,
  terrace slab, parapet at both ends, the stair riser by riser, level markers,
  the dashed foundation line 900 below plinth with §7's exact label, the height
  chain as a native DIMENSION; assumptions printed as NOTES
  (`test_section_is_a_real_cut_through_the_stair`,
  `test_section_without_a_stair_cuts_the_centre_and_says_so`).
- **EXECUTED** — A-03A–D are `services.drawings.elevations.build_elevation` on
  the sheet: every external-wall opening on exactly one face at FFL + sill to
  FFL + sill + height, leaf/glazing, mullion, sill course, plinth band, ground
  line, floor lines, parapet, level markers, one height chain
  (`test_elevations_project_every_opening_at_its_sill_and_lintel_with_its_tag`).
- **TRACED** — the "two renderers" finding is closed for the vertical drawings
  and the schedule (the pipeline now imports `sections/`, `elevations/`,
  `schedules/door_window`); `autodim/`, `projection/` and `blocks/` are still
  exercised only by their own tests.
- **UNVERIFIED** — a dogleg's return flight (the model stores one flight; the
  section says so in its notes), the empty hand-checked dimension set, and a
  DXF opened in a human CAD — unchanged.

## 2. Known gaps (the honest edge)

- **EXECUTED (2026-09-07) — PDF.** `scripts/sheet_goldens.py --pdf` produces the
  set through rsvg-convert + qpdf (42 pages); the per-sheet PDF from the UI's own
  Generate is still the open item (defaults are svg+dxf).
- **UNVERIFIED — glTF / PNG / WhatsApp preset.** Same shape:
  `export/{gltf,png}.py` exist, never run.
- **UNVERIFIED — annotation anchoring + review tray.** Routes exist
  (`/sheets/review-tray`, `/sheets/:id/annotations`), tests cover units, no
  live exercise after a solver re-run (blocked on Phase 3's first green solve).
- **UNVERIFIED — the 10-project golden corpus.** `fixtures/plans/` holds only a
  README; the ledgered regen tool (`python -m services.solver.golden --regen`)
  does not exist. Blocked on Phase 3 producing plans.
- **UNVERIFIED — dims ≥90% vs a hand-checked reference.** Needs the corpus
  above plus a human reference set (launch gate).
- **TRACED — dead scaffolding.** `services/drawings/dimensions.py` and
  `services/drawings/dxf.py` carry superseded `NotImplementedError` stubs;
  the live pipeline imports `autodim/` and `export/dxf.py` instead. Candidates
  for deletion once nothing imports them (check before removing).
- **RESOLVED (2026-08-26) — sheet text face.** The Inter face landed with its
  OFL text; `make asset-audit` runs clean.

## 3. Fixes first execution forced (all committed)

- `ezdxf.LayerTable.add()` has no `description` kwarg; layer descriptions now
  set post-create (`services/drawings/dxf.py` path superseded, fix carried in
  the live layer setup).
- `_add_text` alignment: `TextEntityAlignment` enum lookup replaced a string
  kwarg ezdxf rejects.
- The api image now ships `fixtures/` (sheets need the catalog).
- The api → worker handoff PUTs the folded model to S3 and 503s honestly when
  the store is down (`sheets_unavailable`) — verified both ways (down: 503;
  up: job runs).
