# Garh AI — Engineering Playbook

Implementation source of truth. Read top-to-bottom once; return per-section while building.

**Table of contents**

1. Repo layout & tooling
2. Database schema (DDL)
3. Model core (geometry & document)
4. Op taxonomy (the ~30 ops)
5. Layout solver spec
6. Rules engine (DSL + seeded packs)
7. Drawings: auto-dimensioning & sheet engine
8. 3D & facade kits
9. Render service
10. LLM integration (brief parse + copilot)
11. API surface
12. Frontend architecture
13. Security checklist
14. Performance budgets
15. UX & delight rules
16. Testing strategy & golden files
17. Seed data & demo project
18. Env, config & deployment

---

## 1. Repo layout & tooling

```
garh-ai/
├── docker-compose.yml          # postgres:15, redis:7, api, web, worker-solver, worker-render, worker-drawings
├── pnpm-workspace.yaml
├── DECISIONS.md                # deviation log (see SKILL.md)
├── apps/
│   ├── web/                    # Vite + React 18 + TS strict + R3F + Zustand + Tailwind
│   └── api/                    # FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2
├── packages/
│   ├── model/                  # TS: model types, op types, fold/replay, units, geometry utils
│   │                           #   (mirrored in Python: apps/api/garh_model/ — keep in lockstep,
│   │                           #    shared JSON Schema in packages/model/schema/ is the contract)
│   └── ui/                     # shared UI primitives (buttons, chips, dialogs, toasts)
├── services/
│   ├── solver/                 # Python: OR-Tools CP-SAT layout engine (worker)
│   ├── drawings/               # Python: auto-dim, sheets, ezdxf/PDF export (worker)
│   └── render/                 # Python: provider interface + mock + diffusers impl (worker)
├── rulepacks/                  # JSON rule packs: nbc-core, blr, ncr, hyd, vastu
├── fixtures/                   # golden corpora: briefs/, plans/, sheets/, copilot-commands/
└── e2e/                        # Playwright
```

Tooling: TypeScript `strict: true`, ESLint + Prettier, Ruff + mypy (strict on `garh_model` and `services/*`), pytest + hypothesis, vitest, Playwright. Pre-commit runs all. CI = lint → typecheck → unit → golden → e2e(smoke). Node 20, Python 3.11, pnpm 9. One command to run everything: `docker compose up` (api/web hot-reload via bind mounts).

Dependency rule: before adding any package, check the license table in `market-research-and-oss-licenses.md`. Apache/MIT/BSD/MPL only. Add a line to `DECISIONS.md` for every new dependency.

## 2. Database schema (DDL sketch)

Postgres. All tables: `id uuid pk default gen_random_uuid()`, `created_at`, `updated_at`. Every tenant-owned table carries `firm_id uuid not null` + index. Use Alembic migrations from day one.

```sql
firms(id, name, logo_url, settings jsonb)
users(id, firm_id, email unique, name, role text check (role in ('admin','member')), coa_number text)
projects(id, firm_id, name, status text, architect_of_record uuid references users, units text default 'ft-in',
         city_pack text, demo boolean default false)
plots(id, firm_id, project_id unique, boundary jsonb, north_deg int, roads jsonb, reg_profile jsonb, source text)
briefs(id, firm_id, project_id unique, data jsonb, vastu_mode text check (vastu_mode in ('off','advisory','strict')),
       completeness int)
design_versions(id, firm_id, project_id, name, parent_id uuid, op_seq_start bigint, op_seq_end bigint,
                snapshot jsonb, snapshot_hash text, kind text check (kind in ('auto','named','option')))
ops(seq bigserial pk, firm_id, project_id, version_branch uuid, idx bigint, type text, payload jsonb,
    inverse jsonb, actor uuid, source text check (source in ('manual','copilot','solver','system')),
    client_op_id text, unique(project_id, version_branch, idx))
solver_jobs(id, firm_id, project_id, params jsonb, status text, progress int, options jsonb, error text)
render_jobs(id, firm_id, project_id, design_version_id, view jsonb, mode text, provider text, status text,
            output_url text, params jsonb, stale boolean default false)
sheets(id, firm_id, project_id, design_version_id, kind text, number text, layout jsonb, generated_at)
annotations(id, firm_id, sheet_id, anchor_element_id text, anchor_kind text, payload jsonb,
            orphaned boolean default false)
compliance_reports(id, firm_id, project_id, design_version_id, pack_versions jsonb, results jsonb)
share_links(id, firm_id, project_id, token_hash text, scope jsonb, expires_at, revoked boolean)
comments(id, firm_id, project_id, share_link_id nullable, anchor jsonb, body text, author_name text, resolved boolean)
credit_events(id, firm_id, kind text, qty int, meta jsonb)   -- render/solver/LLM metering from day one
audit_log(id, firm_id, user_id, action, entity, entity_id, meta jsonb)
```

Model state storage: the op log is the source of truth; `design_versions.snapshot` stores folded state every N=200 ops and at named versions (fast load = latest snapshot + tail ops). `snapshot_hash` = sha256 of canonical JSON — used by tests and sync checks.

## 3. Model core (geometry & document)

**Units & coordinates.** Integer millimeters everywhere. Plot-local coordinate system: origin at plot SW corner, +X east, +Y north (plot `north_deg` rotates true north for sun/Vastu math). Areas in mm² internally; expose helpers `toSqft`, `toSqm`, `toGaj`. Angles: degrees, int or 0.1° resolution.

**Document shape (per design version):**

```ts
interface HouseModel {
  schemaVersion: number;
  storeys: Storey[];                    // ordered, ground = index 0; each: { id, level: LevelData, heightMm }
  walls: Wall[];                        // { id, storeyId, a: Pt, b: Pt, thicknessMm, kind: 'external'|'internal'|'parapet' }
  openings: Opening[];                  // { id, wallId, kind: 'door'|'window'|'ventilator', widthMm, heightMm,
                                        //   sillMm, offsetMm (along wall from a), swing: 'in-left'|'in-right'|'out-left'|'out-right', tag }
  rooms: Room[];                        // DERIVED (planar subdivision of walls per storey) but persisted with stable ids:
                                        //   { id, storeyId, type: RoomType, name, polygon: Pt[], areaMm2, tags[] }
  stairs: Stair[];                      // { id, storeyId, kind: 'straight'|'dogleg'|'L'|'U', origin, direction,
                                        //   riserMm, treadMm, widthMm, risersCount, landing }
  slabs: Slab[];                        // auto-derived per storey; explicit cutouts (stair wells, double-height)
  columns: Column[];                    // coordination-only: { id, storeyId, pt, sizeMm }
  furniture: FurnitureInstance[];       // { id, storeyId, catalogId, pt, rotationDeg }
  facade: FacadeModel;                  // isolated sub-model (§8) — cannot affect walls/rooms
  materials: MaterialAssignment[];
  levels: { plinthMm, fflPerStoreyMm[], sillDefaultMm, lintelDefaultMm, parapetMm };  // first-class (sections need these)
  balconies: Balcony[];
  meta: { unitsDisplay: 'ft-in'|'m'; regProfileRef; briefRef };
}
```

**Room detection:** after any wall op, recompute planar subdivision per storey (build half-edge graph from wall centerlines with thickness offsets; interior faces = room candidates). Match new faces to existing rooms by max-overlap (Jaccard on polygon area) to **preserve room ids** across edits — ids only die when a room genuinely disappears. This matching is load-bearing (annotations, locks, copilot references depend on stable ids); unit-test it hard.

**Element identity:** ids are `{type}_{ulid}`. Solver partial re-solve receives the set of locked room ids and must return them untouched (same id, same polygon). New solver output gets fresh ids; the diff engine matches old↔new rooms by type+overlap to present "Bedroom 2 moved" rather than "deleted + added".

**Validation invariants (enforced on every fold):** walls have non-zero length; openings fit within host wall length minus 115mm end margins; opening sill+height ≤ storey height; stairs' risersCount × riserMm ≈ storey height ±10mm; no two walls exactly overlap; rooms closed. Invalid op ⇒ reject with machine-readable reason (copilot uses these to self-correct once).

## 4. Op taxonomy

Ops are JSON `{ type, payload, clientOpId }`. Server assigns `idx`, computes `inverse` (for undo), validates, folds, broadcasts. Implement exactly these for MVP (copilot coverage = this list):

| #   | Op                                                             | Payload (all lengths mm)                                                |
| --- | -------------------------------------------------------------- | ----------------------------------------------------------------------- | ------- |
| 1   | `plot.set_boundary`                                            | `{ polygon: Pt[] }` (validates closed, area>0)                          |
| 2   | `plot.set_north`                                               | `{ deg }`                                                               |
| 3   | `plot.set_road`                                                | `{ edgeIndex, widthMm                                                   | null }` |
| 4   | `plot.set_reg_profile`                                         | `{ cityPack, overrides: {...} }`                                        |
| 5   | `brief.update`                                                 | `{ patch }` (RFC7386 merge-patch on brief data)                         |
| 6   | `storey.add` / 7 `storey.remove`                               | `{ index }`                                                             |
| 8   | `storey.set_height`                                            | `{ storeyId, heightMm }`                                                |
| 9   | `wall.add`                                                     | `{ storeyId, a, b, thicknessMm, kind }`                                 |
| 10  | `wall.move`                                                    | `{ wallId, a, b }` (both endpoints; joins re-resolve)                   |
| 11  | `wall.split`                                                   | `{ wallId, atMm }`                                                      |
| 12  | `wall.delete`                                                  | `{ wallId }`                                                            |
| 13  | `wall.set_thickness`                                           | `{ wallId, thicknessMm }`                                               |
| 14  | `opening.add`                                                  | `{ wallId, kind, widthMm, heightMm, sillMm, offsetMm, swing }`          |
| 15  | `opening.move`                                                 | `{ openingId, offsetMm }` / also `wallId` to re-host                    |
| 16  | `opening.resize`                                               | `{ openingId, widthMm?, heightMm?, sillMm? }`                           |
| 17  | `opening.flip`                                                 | `{ openingId, swing }`                                                  |
| 18  | `opening.delete`                                               | `{ openingId }`                                                         |
| 19  | `room.assign`                                                  | `{ roomId, type, name? }`                                               |
| 20  | `room.set_target`                                              | `{ roomId, targetAreaMm2?, mustFace? }` (feeds solver)                  |
| 21  | `stair.add` / 22 `stair.edit` / 23 `stair.delete`              | stair fields                                                            |
| 24  | `column.add` / `column.move` / `column.delete`                 | `{ ... }` (one op type, `action` field)                                 |
| 25  | `furniture.place` / `furniture.transform` / `furniture.delete` | `{ ... }` (one op type, `action` field)                                 |
| 26  | `balcony.add` / `balcony.edit` / `balcony.delete`              | polygon + railing kind                                                  |
| 27  | `facade.apply_kit`                                             | `{ kitId, seed }` (replaces facade sub-model)                           |
| 28  | `facade.edit_component`                                        | `{ componentId, patch }`                                                |
| 29  | `material.assign`                                              | `{ target: surfaceGroup, materialId }`                                  |
| 30  | `levels.set`                                                   | `{ plinthMm?, sillDefaultMm?, lintelDefaultMm?, parapetMm? }`           |
| 31  | `solver.apply_option`                                          | `{ solverJobId, optionIndex }` — expands to a batched op group (atomic) |
| 32  | `annotation.add/edit/delete`                                   | sheet annotations (anchored to element ids)                             |

Batching: ops carry optional `groupId`; undo/redo operates on groups. `solver.apply_option` and copilot multi-step edits are single groups.

## 5. Layout solver spec

Input: plot polygon (rect/L/T), reg profile (setbacks per edge, FAR, coverage, height/floors), brief (rooms with min/target areas from NBC + benchmarks, adjacency wishes, facing, floor assignment, Vastu mode), locked rooms (partial re-solve). Output: 3–5 `PlanOption`s, each = full multi-storey wall/opening/room/stair set + scores + rationale seed data.

**5.1 Envelope.** Offset plot polygon inward by per-edge setbacks → buildable envelope. Validate coverage: envelope area vs allowed ground coverage; if brief's target built area / floors > envelope, shrink footprint target and record the assumption chip.

**5.2 Stage A — topology (CP-SAT, 300mm module).** Grid the envelope (rect/L/T = union of ≤3 rects; solve on rect cells with L/T handled by mandatory-void cells). Variables per room: interval vars x/y (position+size) with `no_overlap_2d`; sizes bounded by min/max from brief (aspect ratio 1:1–1:2.2 habitable, 1:3 baths/store). Constraints:

- **Stairs first:** enumerate 3–6 stair anchor candidates (near entry, edge-adjacent, repeatable across floors); solve per candidate (parallel workers), keep best.
- **Circulation spine:** corridor cells connecting entry → stair → every room door zone; total circulation ≤12% target (soft, penalized).
- **Adjacency:** required (kitchen↔dining touch: shared edge ≥ 900mm) via interval arithmetic booleans; wishes as soft bonuses.
- **External face:** habitable rooms + kitchen must have ≥1 edge on envelope boundary (window feasibility); baths may be internal only if a shaft is adjacent.
- **Facing/Vastu (strict mode):** room centroid within allowed 3×3 plot zone(s); entrance edge on allowed side. Advisory mode: skip constraints, score instead.
- **Wet clustering:** baths/kitchen within N mm of a shared shaft column across storeys (soft, strong weight — buildability signal).
- **Multi-floor:** solve ground floor, fix stair + shafts + load-bearing external walls, then solve uppers with continuity constraints; terrace = parapet + mumty over stair + OHT over shaft.

Objective: weighted sum — target-area deviation, adjacency satisfaction, circulation area, external-face bonus, Vastu score (advisory), compactness (perimeter penalty). Time budget: 15s/stair-candidate, `num_search_workers=8`.

**5.3 Stage B — refinement.** Snap all coordinates to 115mm module; convert cell layout → wall network (dedupe shared walls: two rooms sharing an edge get ONE wall, 115mm internal / 230mm external); insert doors (from circulation into each room, swing into room, clear of fixtures; 900mm bedrooms/main, 750mm baths), windows (on external edges, area ≥ 1/10 room floor area, sill 900mm, avoid road-facing baths), ventilators for internal baths on shafts. Run model invariants; auto-repair trivial violations (nudge by one module) else discard candidate.

**5.4 Critic.** Run full rules engine (hard rules must pass — discard otherwise) + soft scores: circulation %, daylight orientation (habitable rooms E/N/NE bonus), Vastu score, furniture-fit (place standard set per room type from catalog; fail room if unplaceable), plumbing-stack score, privacy score (master not visible from entry). Composite 0–100.

**5.5 Diversity & ranking.** Signature = multiset of (roomType → plot zone) + stair anchor. Reject candidates within Hamming distance 2 of an already-kept signature. Keep top 3–5 by composite. Rationale seed = structured facts (scores, key placements, assumption chips) — the LLM only verbalizes these facts; it adds none.

**5.6 Gates.** An option is presentable iff: all hard rules pass, furniture-fit passes for all habitable rooms, circulation ≤18%, composite ≥55. If <3 options clear gates, relax soft weights once and re-run; if still <3, return what passed with an honest banner ("2 strong options found for this plot").

**5.7 Partial re-solve.** Locked room ids → their polygons become fixed obstacles (exact geometry preserved); stage A solves remaining rooms in residual space; stage B re-runs but never touches locked walls except shared-wall dedupe (locked side wins).

## 6. Rules engine

**DSL (JSON, in `rulepacks/`):**

```json
{
  "pack": "blr",
  "version": "2026.07",
  "extends": "nbc-core",
  "citations_base": "BBMP Bye-laws 2020",
  "rules": [
    {
      "id": "blr.setback.front.9m",
      "severity": "fail",
      "when": { "roadWidthMm": { "lt": 12000 }, "plotAreaSqm": { "lte": 360 } },
      "check": { "type": "setback_min", "edge": "front", "valueMm": 1500 },
      "cite": "Table 6a",
      "fix": "Increase front setback to 1.5m"
    }
  ]
}
```

Check types to implement (pure functions, exhaustively unit-tested): `setback_min`, `far_max`, `coverage_max`, `height_max`, `floors_max`, `room_area_min`, `room_width_min`, `ceiling_height_min`, `ventilation_ratio_min`, `stair_riser_max`, `stair_tread_min`, `stair_width_min`, `headroom_min`, `projection_max`, `parking_min`, `zone_check` (Vastu), `custom` (named registered fn).

**Seed `nbc-core` (values to encode; verify against NBC 2016 during pack authoring — keep values in pack, never in code):** habitable room ≥9.5m² & width ≥2.4m; kitchen ≥5.0m² (≥7.5 where dining combined) & width ≥1.8m; bath ≥1.8m² & width ≥1.2m; WC ≥1.1m²; combined ≥2.8m²; habitable ceiling ≥2.75m (kitchen 2.6, bath 2.2); ventilation openings ≥1/10 floor area habitable (kitchen 1/10, bath ≥0.3m²); stair riser ≤190mm, tread ≥250mm, width ≥900mm, headroom ≥2.1m; door mins: main 900, internal 800, bath 750mm.

**Seed city packs:** `blr`, `ncr`, `hyd` — setback tables by plot size + road width, FAR/coverage/height tables. Mark every value `"confidence": "seed"` — real authoring replaces them; UI shows citation + confidence.

**Vastu pack (advisory scoring + strict constraints):** zones = 3×3 grid oriented to true north. entrance edge ∈ {N,NE,E}; pooja ∈ NE; kitchen ∈ SE (NW fallback, half score); master ∈ SW; toilets ∈ {W,NW}, hard-never NE; stairs ∈ {S,SW,W}; brahmasthan (center cell) keep open (no walls enclosing center cell fully); water tank NE. Score = weighted rule satisfaction, 0–100, per-rule breakdown for the compass-wheel UI.

**Evaluation:** engine takes (model, plot, profile, packs) → `results[] {ruleId, status: pass|warn|fail, actual, limit, cite, fixHint, elements[]}`. Pure, deterministic, <100ms for a house — safe to run debounced on every edit and inside the solver critic.

## 7. Drawings: auto-dimensioning & sheet engine

**Sheet model:** `Sheet { kind, scale (1:100 default), frame A2 landscape default, viewport (storeyId | elevation dir | section line), annotations[] }`. Rendering pipeline: model → 2D projection primitives (lines/arcs/text/hatches with layer tags) → SVG (screen + PDF via headless print) and DXF (ezdxf, mm units, layers `A-WALL, A-WALL-PART, A-DOOR, A-WIND, A-STAIR, A-DIM, A-TEXT, A-AREA, A-TITL`).

**Plan projection:** walls as double lines w/ thickness (fill hatch external), openings break walls (door arc + leaf, window triple line), stairs w/ arrow + `UP 15R`, room label block (name, area in sqft one decimal), FFL markers, section markers, north arrow, grid of column bubbles if columns exist.

**Auto-dimensioning algorithm (plans):**

1. Collect wall axes per storey; cluster by orientation (H/V; MVP is orthogonal-only).
2. **Outer chains** per side of building (4 sides): level 1 = overall extent; level 2 = external wall segment breakpoints; level 3 = opening centerlines on that facade. Offsets: L1 at 2400mm from building line (paper-scaled), L2 1800, L3 1200.
3. **Inner dims:** per room, one width + one depth chain along the room's principal axes, placed near the door-side wall inner face; skip if duplicate of an adjacent chain (same value, shared wall).
4. **Label placement:** greedy on a collision grid (dims, text, symbols all register); on collision try flip side → shift along chain → shrink text one step → leader line. Never overlap; leader as last resort.
5. Values from integer mm — chains must sum exactly (assert in tests: Σ segments == overall, every chain).
6. All dim text in mm on drawings regardless of display units; openings dimensioned to centerline (config flag `dimToJamb` for firm preference).

**Elevations:** project facade sub-model + openings per direction; dims: floor lines (plinth/FFL/lintel/parapet levels as level markers, not chains), overall height chain; material callout leaders from facade kit metadata.

**Section (through stair):** section line auto-chosen through stair flight + one wet area if possible; show storey heights chain, sill/lintel heights, plinth, parapet, mumty, foundation indicative line (900mm below plinth, dashed, labeled "INDICATIVE — REFER STRUCTURAL").

**Schedules & area statement:** door/window schedule = group openings by (kind, w, h) → tags D1.., W1.., V1..; counts per storey. Area statement per municipal format: plot area, per-storey built-up, total, FAR achieved vs allowed, coverage achieved vs allowed, setbacks provided vs required (from rules results — same numbers, one source).

**Annotation anchoring:** user-added/edited annotations store `anchor_element_id`. Manual/copilot edits: anchor follows element (dims re-derive, notes track element centroid delta). Solver re-run (`solver.apply_option`): all annotations whose anchors didn't survive id-matching → `orphaned=true` → Review Tray UI (list with "re-attach" picker or delete). Do not attempt fuzzy re-anchoring in MVP.

**Golden discipline:** every sheet renderer change runs `fixtures/plans/*` → SVG + DXF; byte-diff (SVG normalized: strip timestamps/ids). A failing golden is a build failure.

## 8. 3D & facade kits

3D synthesis (client-side, from model): wall prisms (Manifold: extrude wall rects, boolean-subtract opening boxes), slabs per storey (envelope polygon extrude 150mm), stair solids from parameters, parapet on terrace perimeter, mumty box over stair, OHT cylinder over shaft, plinth base. Rebuild incrementally: only dirty storeys re-mesh; target <100ms for an edit on G+2.

Facade kit = data + generator: `Kit { id, name, components: {windowTrim, chajja (600|750 projection), parapetProfile, claddingZones (rules: e.g., 'stack full-height at entry bay'), porch, railing, colorway[] }, rules }`. Generator walks external walls + openings → instantiates parametric components as separate meshes tagged `facadeComponentId` (editable via op 28, never mutates walls). Two kits: **contemporary** (flat chajjas, vertical cladding band at stair, slim MS railing, monochrome + wood accent) and **modern-minimal** (hidden chajja/recessed windows, plain parapet, glass railing, white + grey). Seeded variation via `seed`.

Sun widget: solar position from date/time + city lat/long (implement NOAA solar position algorithm — ~40 lines, no dependency needed); directional light in R3F, soft shadows, date/time scrubber.

## 9. Render service

Provider interface (`services/render`):

```python
class RenderProvider(Protocol):
    def render(self, req: RenderRequest) -> RenderResult: ...
# RenderRequest: { viewport_png, depth_png, edges_png, mode: 'precise'|'explore',
#                  preset: str, prompt_extras: str, seed: int, size: (w,h) }
```

- **MockProvider (default):** composites viewport PNG + preset-tinted gradient + watermark text; instant. Deterministic by seed. Keeps the whole product testable without GPUs.
- **DiffusersProvider:** SDXL or FLUX.1-schnell via `diffusers`; ControlNet depth + MLSD from the supplied maps; `precise` = ControlNet scale 0.9 / denoise 0.45; `explore` = scale 0.35 / denoise 0.8; prompt templates per preset (`exterior-street-day`, `exterior-34-dusk`, `interior-living`, ...); Real-ESRGAN 2x; NSFW/safety checker on. Weights license guard: assert model id in allowlist (no FLUX.1-dev).

Client captures viewport + depth (R3F depth pass) + edges (Sobel on normals/depth) and uploads with the job. Jobs: queue with per-firm concurrency 4, progress via SSE, results pinned to `design_version_id`, model edits mark renders `stale=true` (banner: "Design changed since this render").

## 10. LLM integration

Provider interface with `mock` (fixture-driven, used in tests/dev) and `anthropic` implementations. All calls use structured outputs (JSON schema), temperature ≤0.3, max 2 retries on schema violation.

**Brief parse:** input = free text (+ optional key-values); output schema = the Brief object + `assumptions[] {field, value, reason}`. Anything not stated → assumption, never silence. Show all assumptions as chips.

**Copilot:** system prompt = op catalog (from §4, machine-generated from the schema — single source of truth) + current model summary (rooms, storeys, key dims — compact JSON, not raw geometry) + rules context (current violations). Output schema: `{ intent: string, ops: Op[], needsClarification?: string, cannotDo?: string }`. Pipeline: dry-run fold on a fork → invariants + rules check → if invalid, feed reasons back once for self-correction → present diff (2D before/after + op list in plain language) → apply as one group on accept. Log `{command, ops, applied|rejected|invalid}` for the eval set. Out-of-scope (anything not expressible in §4 ops): return `cannotDo` with a friendly explanation — never approximate with wrong ops.

**Rationales:** solver facts → 60-word paragraph. Prompt forbids introducing facts not in input (list-then-write pattern).

## 11. API surface (FastAPI, `/api/v1`)

```
POST /auth/otp  /auth/verify                       # email OTP → JWT (15min access + refresh)
GET/POST /projects   GET/PATCH/DELETE /projects/:id
PUT  /projects/:id/plot      PUT /projects/:id/brief     POST /projects/:id/brief/parse
POST /projects/:id/ops                              # {ops[], baseIdx} → 409 on stale base (client rebases)
GET  /projects/:id/ops?since=idx                    # incremental sync
GET  /projects/:id/model?version=                   # snapshot + tail
POST /projects/:id/versions  GET .../versions  POST .../versions/:vid/restore
POST /projects/:id/solve     GET /solver-jobs/:id   # + SSE /solver-jobs/:id/events
POST /projects/:id/renders   GET /render-jobs/:id   # + SSE
GET  /projects/:id/compliance?version=
POST /projects/:id/sheets/generate   GET .../sheets  GET .../sheets/:sid.(svg|dxf|pdf)
POST /projects/:id/export    # {kind: pdf-set|dxf|gltf|png-pack} → job → signed download URL
POST /projects/:id/share     DELETE /share/:id      # scoped signed links
GET  /share/:token/**                               # read-only surface (viewer + comments POST)
GET  /rulepacks  GET /catalog/furniture  GET /catalog/materials  GET /catalog/facade-kits
```

Conventions: Pydantic request/response models everywhere; cursor pagination; idempotency via `clientOpId`/`Idempotency-Key`; problem+json errors `{code, message, action}`; rate limits per firm (60 ops/s, 10 solver jobs/hr on free tier); all downloads via short-lived signed URLs.

## 12. Frontend architecture

- **State:** Zustand stores — `session`, `project`, `model` (folded state + op dispatch + optimistic queue + rebase-on-409), `selection`, `jobs`, `ui`. Model store is the ONLY writer; components dispatch ops.
- **Canvas:** one R3F `<Canvas>`; 2D = orthographic camera + 2D layer components (walls as flat meshes, dims/text as instanced sprites/troika-text); 3D = perspective. Shared raycast hit-testing; selection state common to both. Tools = state machines (idle→drawing→preview→commit(op)); every tool: Esc cancels, Enter commits, numeric keyboard entry overrides mouse (type exact lengths while drawing).
- **Keyboard map:** V select · W wall · D door · N window · S stair · B balcony · M measure · F furniture · Cmd/Ctrl-Z/Y undo/redo · 1/2/3 storey switch · Tab 2D↔3D · G snap toggle.
- **Optimistic ops:** apply locally, queue to server, rollback+toast on reject; op round-trip indicator = subtle autosave badge ("Saved · v214").
- **Panels:** left tool rail · right inspector (selection properties, all editable, mm/ft-in aware inputs) · bottom compliance chip strip · top bar (project, storey tabs, units toggle, share, generate buttons).
- **Diff preview component** (used by copilot + solver): split before/after mini-canvases + plain-language op list + apply/reject. One component, both features.
- Routing: dashboard → project (tabs: Brief · Plan · 3D · Renders · Sheets · Compliance). Lazy-load heavy tabs.

## 13. Security checklist (each phase's DoD includes relevant items)

- AuthN: email OTP (10min expiry, 5 attempts), JWT RS256, refresh rotation, logout-all.
- AuthZ: tenancy repository layer — every query takes `TenantCtx`; handlers cannot touch tables directly (lint rule: no `session.query` outside repos). Cross-tenant tests in CI (fetch other firm's project → 404).
- Share links: random 256-bit token, stored hashed, scoped `{projectId, sections[], canComment}`, expiry, revocation; viewer surface is a separate read-only router with no write deps imported.
- Input: Pydantic strict + zod at client; file uploads (DXF ≤20MB, images ≤10MB) type-sniffed, parsed in worker with 10s timeout + memory cap (malicious DXF = crash-safe); SVG output sanitized (no scripts/foreignObject).
- Secrets: env only, never client bundle (`VITE_`-prefix audit in CI); API keys per provider in worker env only.
- Rate limits (per firm + per IP on auth); audit_log on auth events, exports, share creation, reg-profile overrides, deletions.
- Web: HTTPS only, HSTS, CSP (no inline scripts), SameSite=Lax cookies for refresh, CORS allowlist, signed S3 URLs ≤10min.
- LLM: prompt-injection containment — copilot output only ever becomes validated ops (never executed text); model summaries exclude PII; brief text sent to LLM flagged in privacy policy; per-tenant isolation of any future fine-tune corpora (consent default OFF).
- Dependencies: lockfiles, `pnpm audit`/`pip-audit` in CI, license scanner (fail on GPL/AGPL/unknown in app deps).

## 14. Performance budgets (CI-enforced where possible)

| Surface               | Budget                                        | Enforcement                              |
| --------------------- | --------------------------------------------- | ---------------------------------------- |
| Canvas frame          | <16ms during pan/zoom/drag on G+2 demo        | Playwright trace assertion               |
| Op apply (optimistic) | <10ms local fold                              | vitest perf test                         |
| Compliance run        | <100ms model, ≤500ms debounce                 | pytest timing                            |
| Room re-detection     | <50ms per storey                              | pytest timing                            |
| Solver 3 options      | ≤60s (fixtures)                               | pytest timing (CI uses 2 workers: ≤120s) |
| 3D rebuild after edit | <100ms dirty-storey                           | vitest perf                              |
| Sheet set G+1 3BHK    | ≤5min                                         | worker test                              |
| Initial web load      | <3s on 4G mid-range, bundle <1.5MB gz initial | Lighthouse CI ≥85                        |
| Render (mock)         | <1s                                           | e2e                                      |

## 15. UX & delight rules (implement literally)

- **First-run:** seeded demo project opens on first login with a 5-step coach-mark tour (plot → generate → edit → 3D → sheets). "Try with demo plot" button on every empty state.
- **Generation theater:** solver progress = staged, honest messages ("Placing staircase… packing rooms… checking BBMP setbacks… scoring Vastu") driven by real worker events, with plan silhouettes appearing as they pass gates — never a fake bar.
- **Options screen:** cards with mini-plan SVG, composite score ring, compliance badge, Vastu wheel, 3 key stats (built-up, bedrooms fit, circulation %); compare-two side-by-side; "why this plan" rationale expander showing assumption chips.
- **Everything undoable, visibly:** undo toast after destructive ops ("Wall deleted — Undo"); version timeline scrubber in header menu.
- **Numbers editable everywhere:** any dimension/area label on canvas is click-to-edit (dispatches op). No dead text.
- **Indian defaults:** ft-in primary display w/ gaj for plot area ("1,200 sq ft · 133 gaj"), ₹ Indian digit grouping (₹12,45,000), +91 phone fields, dates DD-MM-YYYY, "Share on WhatsApp" on renders/share links (wa.me deep link with preformatted message).
- **Compliance chips:** severity color, one-line human text ("Bedroom 2 is 8.9m² — NBC needs 9.5m²"), cite on hover, "Fix it" applies the suggested op diff where computable.
- **Loading:** skeletons everywhere (never blank, never spinner-only); job cards show queue position; renders stream in progressively.
- **Micro-speed:** open project → interactive canvas <2s (snapshot + tail); switching storeys instant (pre-built meshes).
- **Tone:** UI copy is plain, warm, professional; no jargon ("Setback check" not "Regulatory validation module"); error copy never blames the user.
- **Accessibility:** full keyboard operability of panels/forms, focus rings, WCAG AA contrast, canvas tools have toolbar-button equivalents (mouse-only users OK).

## 16. Testing strategy

- **Unit:** every rules check fn (pass+fail fixtures per rule); units conversion (golden pairs TS↔Python must agree); geometry utils (room detection property tests via hypothesis: random rect subdivisions → rooms found == expected).
- **Model core:** property-based fold/replay determinism (state hash equality); undo/redo inverses; op validation rejections.
- **Solver:** 20-brief golden corpus (`fixtures/briefs/`) → assert gates (§5.6), determinism per seed, time budget, locked-room preservation; plan JSON goldens with tolerance 0 (integer mm!).
- **Drawings:** 10 plan fixtures → SVG/DXF goldens; dimension-chain sum assertions; collision-free assertion (no overlapping text bboxes); `ezdxf.audit()` clean.
- **Copilot:** 40-command fixture set with mock LLM (fixtures map command→expected ops) + schema-contract test against real provider (behind env flag, non-blocking in CI).
- **E2E (Playwright):** smoke on every PR (login → open demo → edit wall → undo → compliance chip); full happy path nightly (Phase 9 DoD scenario).
- **Visual regression:** Playwright screenshots of options screen, 3D w/ facade, one sheet — 0.1% pixel tolerance.

## 17. Seed data & demo project

Seed script creates: demo firm ("Studio Demo"), user (`demo@garh.ai`), furniture catalog (≥30 items with real Indian dims: bed 1900×1525 queen, sofa 2100×900 3-seat, dining 1500×900 6-seat, kitchen counter depth 600, wardrobe depth 600, WC 700×400, washbasin 550×450, car 4800×1800...), material catalog (≥20), 2 facade kits, 3 rule packs + nbc-core + vastu, and **one complete demo project**: 30×40 ft Bengaluru plot, north up, 9m road south, G+1 3BHK brief, a solved+edited plan, facade applied, 2 mock renders, generated sheet set. The demo project is the universal fixture: tours, goldens, perf budgets, screenshots all use it.

## 18. Env, config & deployment

`.env` (12-factor, all defaulted for local): `DATABASE_URL, REDIS_URL, JWT_PRIVATE_KEY, S3_* (minio in compose), PROVIDER_LLM=mock|anthropic, ANTHROPIC_API_KEY?, PROVIDER_RENDER=mock|diffusers, RENDER_DEVICE=cpu|cuda, PROVIDER_BILLING=mock|razorpay, APP_URL`. Feature flags table read at boot (`flags`: facade_v2, interior_precise... default off). Deployment target: single VM/compose for beta (documented), architecture keeps workers stateless for later k8s move. Backups: nightly pg_dump + S3 versioning. Observability: structlog JSON, request ids, Sentry-compatible error hook, `/healthz` per service, worker queue-depth metric.

---

**Final reminder:** functional > fancy. A boring, correct, fast dimension chain beats a clever, wrong one. When in doubt, re-read SKILL.md Golden Rules and the product spec's acceptance criteria — they are the contract.
