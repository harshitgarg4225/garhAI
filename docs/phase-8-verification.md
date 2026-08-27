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

## 2. Known gaps (the honest edge)

- **UNVERIFIED — PDF.** `formatsAvailable` came back `["dxf","svg"]`;
  `services/drawings/export/pdf.py` exists but no run has produced a PDF.
  Settles it: the export-job path (`POST` the export with a pdf format — find
  the exact body in `routers/jobs.py`) against the live stack, then open the
  bytes.
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
- **Human-blocked — sheet text face.** Canvas/sheet labels reference the
  missing `inter-medium.woff` (OFL, human fetch); SVG text currently renders in
  a fallback face. `make asset-audit` flags it every run.

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
