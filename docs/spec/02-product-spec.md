# Garh AI — Product Specification v2.0 (CPTO-Reviewed)

*July 30, 2026 · Status: Approved-for-build pending §17 decisions*
*v2.0 changes: full CPTO review applied — MVP re-scoped to municipal-set depth (GFC → v1.1), capacity-based re-baseline (launch M10–11 @ 10 heads, or M8–9 @ 13), new F0 Model Core, M0 de-risking spikes with go/no-go gates, new Team/Budget/COGS, QA-Eval, Legal, Data Policy, GTM/Beta sections; v1.1/v2.0/v2.5/v3.0/v3.5 fully specced with acceptance criteria and sizes.*

---

## 0. CPTO Review Verdict & Decision Log

**Verdict:** Right market (India + residential + documentation is an unowned gap), right architecture (deterministic solver + creator–critic; LLMs never emit geometry). Original v1.0 plan committed **~91 eng-months against ~48 available** (1.9× over) and treated two R&D programs — the layout solver and auto-dimensioning — as schedulable features. Approved after the re-scope below.

**Binding decisions:**

| # | Decision | Rationale |
|---|---|---|
| D1 | MVP drawings = **municipal submission set**, not full GFC. GFC completes in **v1.1** (+8–10 wks) | Halves F7 risk; municipal set is what gates construction start; preserves "through working drawings" positioning |
| D2 | Launch cities = **3** (Bengaluru, Delhi NCR, Hyderabad). Pune, Jaipur → v1.1 | Rule-pack authoring is ~6 wks/city of non-eng ops; 5 doesn't fit |
| D3 | Facade styles at launch = **2** (Contemporary, Modern Minimal). +2 → v1.1 | Each style kit ≈ 1.5 eng-months + content |
| D4 | MVP solver envelope = **rect/L/T plots**; irregular polygons = manual + copilot assist | Was contradictory in v1.0 (F1 vs risk table). Now explicit |
| D5 | Cut from MVP: PDF/image plot trace (v1.1), map pick (deleted; v2.5 site context), interior-Precise renders (v1.1; interior-Explore stays), 4K renders (v1.1), curved walls (v1.1), electrical/plumbing assist (v1.1), standard-details library (v1.1; attach-own-PDF stays), copilot capped at ~25 typed ops | ≈20 eng-months saved; post-cut MVP ≈ 70 em |
| D6 | Timeline: **Option A (default): 10 heads → closed beta M8, launch M10–11. Option B: staff to 13 by M2 → launch M8–9 (+~₹1 Cr)** | 8-month launch at v1.0 scope is not achievable at any quality bar; founder to pick A/B |
| D7 | **M0 spikes (2 eng, 6–8 wks) with go/no-go gates before full build** (§5) | Solver plausibility + auto-dim feasibility are company-killing bets; buy the information first |
| D8 | New **F0 Model Core & Op API** is M1's centerpiece; drawing-relevant schema contract-freezes end of M2 | Everything (editor, solver, copilot, drawings, versions) rides on it; was unbudgeted |
| D9 | Rules-engine skeleton + draft Bengaluru pack land in **M1**, before solver v1 | Solver constraints ARE rules; wrong order = rewrite |
| D10 | Commissioned Indian photography procurement starts **week 1** | 2–3 month lead time gates render LoRA quality |
| D11 | Credit metering ships in **M3 beta**, not launch | Learn real COGS behavior before pricing goes live |
| D12 | MVP concurrency = **single writer per project**; ops designed CRDT-compatible now (intent-level, serializable, server-sequenced, no derived geometry) so v2.5 multiplayer is an upgrade, not a rewrite | |
| D13 | Annotation persistence scoped honestly: persists across **manual/copilot edits**; solver re-runs route affected annotations to a **review tray**. Fuzzy re-matching = later | v1.0 overpromised |
| D14 | MCP server pulled forward to **v2.5** (cheap, strategically loud); Builder/developer mode leads v3.0 (10× ACV) | |

---

## 1. Product Vision

**Garh AI takes an Indian architect from client brief to a compliant, client-approved, submission-ready house design — AI floor plans, 3D model, photoreal renders, municipal drawing set (full GFC in v1.1) — in days instead of weeks.**

**One-line pitch:** "Brief in. Buildable house out."

| | Forma | Snaptrude | TestFit | Maket/Finch | **Garh AI** |
|---|---|---|---|---|---|
| Segment | Feasibility, large projects | Concept BIM, commercial | CRE feasibility | Floorplans only | **Complete house delivery** |
| Geography | Global | US-first | US/Canada | US/Canada | **India-first** |
| Depth | Schematic | Schematic | Feasibility | Plan only | **Through working drawings** |
| Code awareness | Generic | US codes | US zoning | None | **NBC + city bye-laws + Vastu** |

Patterns adopted from the market research: TestFit's deterministic-solver trust model, Snaptrude's creator–critic orchestration + human-in-the-loop, Forma's Precise/Explore render contract + published assumptions, Swapp's documentation-automation moat thesis.

**Why documentation depth matters:** an Indian architect's fee is earned on drawings, not concepts. Concept-only tools save ~10% of their effort; municipal set + GFC targets ~70%.

---

## 2. Users & Personas

**Primary: independent architect / small firm (1–15 people), India.** 60–80% of projects are houses (plots 20×30 ft to 60×90 ft, G+1 to G+3). Today's stack: AutoCAD + SketchUp + Lumion/D5 + Excel + WhatsApp. Pain: 2–4 weeks per house of drawing labor; endless revision loops; bye-law/Vastu rework discovered late. The real incumbent competitor is **piracy + a ₹20k/month junior drafter** — the product must visibly beat that on speed and cost.

**Secondary (supported, not optimized):** small builders doing duplex/row-house catalogs; design-build civil engineers.
**Not MVP:** homeowners (v3.5), large firms, commercial buildings.

**Jobs hired for (MVP):** (1) brief + plot → 3–5 compliant options same day; (2) iterate live with client without redrawing; (3) 3D + renders for sign-off without SketchUp/Lumion; (4) municipal submission set without a drafting team; (5) never violate setbacks/FAR/room minimums.

---

## 3. Scope

**MVP = one project type done completely:** detached/semi-detached house (bungalow/villa/duplex), plot ≤ ~10,000 sq ft, ≤ G+3, RCC frame + masonry infill, rect/L/T plots solver-generated (irregular = manual + copilot).

```
Plot + Brief → AI Plan Options → Edit (canvas + copilot) → Auto 3D + AI Facades
   → AI Renders → Compliance Check → Municipal Drawing Set → PDF/DXF/glTF
```

**Wave map:** MVP (municipal set, 3 cities, 2 styles) → **v1.1** (full GFC, MEP-assist, details, +2 cities, +2 styles, DWG) → **v2.0** (BOQ, templates, IFC, structural coordination) → **v2.5** (multiplayer, interiors, sloped sites, MCP) → **v3.0** (builder mode, Garh Agent, e-submission pilot) → **v3.5** (homeowner tier, multifamily). Full specs in §11.

---

## 4. F0 — Model Core & Op API (new; M1 centerpiece)

The substrate everything rides on. Unbudgeted in v1.0; now weeks 1–6 of the build.

- **Parametric house graph:** per-storey wall-axis graph, openings hosted on walls, rooms derived from enclosed regions, stairs, slabs, facade sub-model (isolated so facade churn can't break drawings).
- **Level semantics as first-class data:** plinth, FFL, sill, lintel, floor-to-floor, parapet, mumty — sections and compliance consume these; the v1.0 data model lacked them.
- **Element identity rules:** stable IDs through parametric edits; **solver partial re-solve preserves IDs of locked rooms** (day-one requirement — F3.2 lock/regenerate and F7 annotation anchoring both die without it).
- **Typed op API (~25 ops, appendix A):** move/resize wall, add/remove/resize opening, swap rooms, assign room type, add bath, move stair, set floor height, change material, etc. Copilot is a thin LLM layer over exactly these ops — op taxonomy freezes at M1.
- **Op-log architecture:** intent-level, serializable, server-sequenced, no derived geometry in ops (CRDT-compatible per D12). Powers undo/redo, autosave, versions, diffs, provenance. Single writer per project in MVP.
- **"IFC-shaped" defined precisely:** IFC-mappable class names, GUID discipline, storey structure, pset-shaped metadata. Explicitly rejected for MVP: IFC's full attribute graph, curved-wall generality, IfcOpenShell in the runtime path. The house typology permits a radically simple kernel — protect that.
- **Contract freeze:** drawing-relevant schema (walls/openings/levels/stairs) freezes end of M2 with contract tests.

---

## 5. M0 — De-risking Spikes (new; go/no-go gates)

Two engineers, 6–8 weeks, before full build. These are the bets that kill the company if wrong.

| Spike | Question | Gate (go/no-go) |
|---|---|---|
| S1 Solver plausibility | Can CP-SAT two-stage (coarse topology at 300–575mm module via no_overlap_2d → continuous refinement snapping to 115mm brick module) produce *architecturally plausible* plans, not just legal ones? | ≥3/5 mean blind score from 5-architect panel on 20 briefs by wk 6; ≤60s for 3 options |
| S2 Auto-dimensioning prototype | Can chain-generation + collision-free label placement reach professional quality on municipal-set plans? | ≥80% of dimension chains accepted unedited on 10 golden plans by wk 8 |
| S3 Editor renderer | Canvas2D/SVG vs orthographic Three.js scene (one scene graph + one hit-testing system across 2D/3D)? | 2-wk spike; decision memo; 60fps pan/zoom on G+2 house |
| S4 Render pipeline | ControlNet on bare massing + furnished-interior test; schnell-class few-step vs SDXL-30-step on L4 latency | ≤30s 2K Precise exterior on L4 confirmed; interior asset needs documented |

Failure paths: S1 fail → increase manual-first + copilot positioning, solver becomes assistant not generator (pivot, not death). S2 fail → drawings ship as dimension-assist (place-and-snap) not auto-dim; re-scope marketing.

---

## 6. MVP Feature Specification

### F1. Project Setup & Plot
- **Input:** parametric rectangle (ft/m/gaj); irregular via vertex editor (edge lengths + bearings, auto-close validation); **DXF import** (layer picker → boundary). *PDF/image trace → v1.1. Map pick → deleted (v2.5 site context).*
- **Attributes:** north angle (drag compass), road(s) per edge + width (drives entry + setbacks), neighbor walls (shared/open), flat plots only (sloped → v2.5). Sun position falls back to city-centroid lat/long.
- **Regulatory profile:** city preset (**BLR, NCR, HYD** + Custom) auto-fills setbacks by plot size/road width, FAR/FSI, ground coverage, height/floors, parking. Every value overridable; overrides logged. Custom = manual entry.
- **AC:** plot from dims <60s; DXF boundary <2 min; profile change re-validates project live.

### F2. Brief Capture
Form or **paste/dictate free text → LLM parse** (structured-output). Rooms (bedrooms + attached/common baths, kitchen type, living/dining, pooja, study, guest, servant, store, garage, balconies, terrace access, stilt toggle, future-expansion toggle); per-room prefs (size or "AI decides", floor, facing, adjacency wishes); style (2 launch styles + reference image); budget band (→ area target via editable ₹/sq ft); **Vastu: OFF / Advisory / Strict** (entrance N/NE/E; pooja NE; kitchen SE↔NW; master SW; toilets W/NW never NE; stairs S/SW/W; brahmasthan open; tank NE — all editable). Brief-completeness meter; every AI assumption shown as an editable chip.

### F3. AI Floor Plan Generation (core moat)
- **Pipeline:** program graph (areas/adjacency/facing/Vastu constraints) → **layout solver** (buildable envelope from setbacks → staircase + circulation spine first (stairs must repeat across floors, land near entry, meet riser/tread) → two-stage CP-SAT room packing per S1 → wall-thickness-aware dims (230/115mm, brick-module) → door/window auto-placement (swing clearances; ventilation ≥ 1/10 floor area/habitable room)) → **critic** (rules engine hard-fail + scores: Vastu, circulation ≤12%, daylight orientation, **plumbing-shaft stacking across floors**, furniture-fit test) → **3–5 diverse ranked options** (layout-signature distance) each with area statement, compliance badge, Vastu score, AI rationale.
- **Controls:** lock rooms → partial re-solve (IDs preserved per F0); "more like this"; per-floor regen; seed variation.
- **Multi-floor:** stair/shaft/load-wall continuity; ground floor may differ (parking/stilt); terrace auto-adds mumty + OHT.
- **Envelope (per D4):** rect/L/T plots. Irregular → manual draw + copilot.
- **Perf:** 3 options ≤60s; partial re-solve ≤15s.
- **Training data:** NO RPLAN/research-dataset weights (license-tainted per OSS research). Solver bootstraps data-free; learned ranker later from (a) solver-synthetic corpus scored by critic, (b) licensed Indian plan sets, (c) opt-in user designs. Solver first, ML second = the TestFit trust play.

### F4. Plan Editor (2D canvas)
- **Tools:** walls (draw/move/split; 230/115/150/200mm + custom), auto-detected rooms (planar subdivision) w/ live name/area tags, doors/windows/ventilators (parametric w/h/sill, slide, flip), staircase tool (straight/dog-leg/L/U; auto riser-tread from floor height; headroom validation), columns (visual grid suggestion, coordination-only), balcony/projection (checks projection-vs-setback rules). *Curved walls/arches → v1.1.*
- **Furniture:** ~80 items MVP, Indian sizes, **3D assets** (not 2D blocks — interiors renders depend on this); powers furniture-fit critic.
- **UX:** snap default = **115mm half-brick module (4.5″)**, fine-grid toggle; ortho; dimension-first editing (click dim → type value); undo/redo on op-log; copy floor; measure.
- **Guardrails:** compliance re-check ≤500ms debounced; violations = non-blocking red chips → explanation + auto-fix suggestion.
- **F4.5 Copilot:** NL edits ("swap kitchen and dining", "attached bath to bed 2", "make it Vastu-compliant") → **LLM emits typed ops only** (never geometry) → solver/critic validates → **diff preview, apply/reject**. MVP coverage = the ~25-op taxonomy; out-of-scope asks get honest "can't do that yet" + logged for roadmap.

### F5. Auto 3D + AI Facades
- **Instant 3D:** plan IS the model — walls extrude (default 10′/3.05m, per-floor editable), openings cut, slabs, stairs, parapet/mumty/OHT; 2D/3D synced selection; orbit/walk; sun widget (date/time shadows).
- **Facade generator:** massing + style (+optional reference image) → **3 options as applied 3D geometry** (parapet profiles, window trims/chajjas, cladding zones, porch, railings) via parametric per-style component kits + LLM selection; per-element editable after. **2 style kits at launch (D3).**
- **Materials:** ~60-item Indian library (brick, stone cladding, texture paint, wood louvers, MS railing), surface-group assignment.

### F6. AI Renders
- **Pipeline (license-clean):** viewport → depth + MLSD edges → **diffusers + ControlNet** on **FLUX.1-schnell / SDXL / Qwen-Image** (never FLUX.1-dev — NC) → Real-ESRGAN. LoRA fine-tune on **commissioned Indian photography (procurement starts wk 1, D10)**.
- **Modes:** **Precise** (geometry-locked) vs **Explore** (moodboard). MVP: exterior Precise+Explore, **interior Explore only** (interior-Precise → v1.1 with full 3D furniture coverage).
- **Targets:** ≤30s per 2K render on L4 (schnell-class few-step confirmed in S4); 4 concurrent; render pinned to design version; stale-flag when model changes. Client pack: 6 exteriors + living + kitchen, one click. 2K only (4K → v1.1).

### F7-A. Drawings — Municipal Submission Set (MVP)
1. **Site plan** — plot, dimensioned setbacks, footprint, road, north, coverage/FAR table.
2. **Floor plans** (1:100) — auto-dimensioned (outer chains → openings; inner room dims; wall thicknesses), room labels + areas, door/window tags, FFL markers, stair arrows + riser count, section markers.
3. **Elevations (all 4)** — heights, floor lines, finish callouts from facade model.
4. **Section (1, through staircase)** — floor heights, sill/lintel/plinth/parapet levels (from F0 level semantics).
5. **Door/window schedule** — tag, size, count, type.
6. **Area statement** — plot/built-up/carpet per floor, FAR vs allowed, coverage, setback table, municipal format.
- **Auto-dim engine (from-scratch IP, gated by S2):** chain generation + collision-free label placement; dims to unfinished faces; mm on drawings regardless of display units; openings to centerline (firm-configurable).
- **Title block:** firm logo/fields template; sheet numbering; auto revision table.
- **Regeneration contract (per D13):** annotations anchored to element IDs persist across manual/copilot edits; solver re-runs route affected annotations to a review tray.
- **AC:** full set for G+1 3BHK ≤5 min compute; **≥90% dims accepted unedited** on golden corpus (launch gate); DXF opens clean in AutoCAD 2018+ (A-WALL/A-DOOR/A-DIM layer convention); print-true vector PDF.

### F7-B. Drawings — GFC completion (v1.1, +8–10 wks post-launch)
Second section (wet areas), full GFC dimensioning depth, lintel/sill level plans, terrace drainage (khurra + slopes), OHT platform detail, **electrical layout (assisted: symbol library + per-room-type AI suggestions + auto-legend/counts; no circuit design)**, **plumbing layout (assisted: fixtures, shaft suggestions, slope arrows; no pipe sizing)**, standard-details library (~30 curated Indian details, licensed; firm can attach own PDF/DXF from MVP).

### F8. Compliance Engine
- **Architecture:** versioned data-driven rule packs (JSON/DSL, OZFS-inspired) — id, source citation (NBC 2016 clause / city bye-law), severity, check fn, auto-fix hint. Authored via internal tooling; **empaneled local-architect review per city**; advisory-not-approval framing.
- **MVP domains:** setbacks (plot size/road width), FAR/FSI, ground coverage, height/floors, NBC room minimums (habitable ≥9.5 m² & ≥2.4m width; kitchen ≥5 m²; bath ≥1.8 m²; WC ≥1.1 m²; combined ≥2.8 m² — values maintained in-pack, verified at authoring), ceiling heights, ventilation ≥1/10, stair code (riser ≤190mm, tread ≥250mm, headroom ≥2.1m, width ≥900mm), projection limits, parking count, RWH flag.
- **Vastu pack:** separate toggleable pack, direction-zone checks, compass-wheel advisory UI.
- **UX:** live badge per option + editor; exportable compliance annexure; generations pre-validated (no hard-fail plan ever shown).

### F9. Exports
**PDF** (vector, print-true) · **DXF** (ezdxf, layer-mapped; AutoCAD-in-CI golden files from M2) · **glTF/OBJ** (Lumion/D5 bridge = adoption, not threat) · **PNG/JPG** renders + "send to client" WhatsApp preset. **DWG** via ODA license → v1.1 (budget line). IFC → v2.0.

### F10. Projects, Versions, Sharing
Dashboard (status chips: Brief/Options/Design/Drawings) · named snapshots + auto-checkpoints + option lineage · restore/duplicate · **client share link** (read-only viewer: 2D/3D/renders, pin comments, OTP-lite, no client login) · firm workspace, seats, **admin/member roles** (viewer granularity → v1.1) · Razorpay billing + **credit metering live in M3 beta (D11)**.

---

## 7. AI System Architecture

```
ORCHESTRATOR: brief-parse → program → generate → critique → present
   ├─ LLM services (parse, copilot ops, facade selection, rationales)
   ├─ LAYOUT SOLVER (CP-SAT two-stage + heuristics — deterministic)
   ├─ RULES ENGINE (NBC/bye-law/Vastu packs — deterministic)
   └─ every LLM output validated by solver/rules before user sees it
MODEL CORE (F0: house graph, op API, op-log, provenance)
   ├─ 3D synth (Manifold/three.js)
   ├─ RENDER svc (diffusers + ControlNet + ESRGAN)
   └─ DRAWING engine (auto-dim + sheets → ezdxf/PDF)
```

**Non-negotiables:** (1) LLMs never emit geometry — typed ops only; (2) every AI step previewable + reversible (diff/apply/reject); (3) determinism where trust matters (dims, compliance, areas), ML where taste matters (facades, renders, ranking, language); (4) assumptions visible as editable chips with citations.

**Models:** frontier LLM API for parse/copilot (structured output; no fine-tune at MVP); image LoRAs on licensed data; learned layout-ranker deferred until solver-synthetic corpus ≥100k plans.

---

## 8. Tech Stack (licenses verified in research doc)

| Layer | Choice | License |
|---|---|---|
| Frontend | React + TS; Three.js + R3F; 2D layer per S3 spike outcome (Canvas/SVG vs ortho-Three — one hit-testing system preferred) | MIT |
| Client geometry | Manifold WASM | Apache-2.0 |
| Model core | Custom house graph, IFC-shaped per F0 (IfcOpenShell only at v2.0 export boundary, LGPL process-isolated) | ours |
| Solver | OR-Tools CP-SAT + custom heuristics | Apache-2.0 + ours |
| Rules | Custom DSL + JSON packs | ours |
| Renders | diffusers + ControlNet; FLUX.1-schnell/SDXL/Qwen-Image; Real-ESRGAN | Apache/RAIL/BSD |
| Drawings | ezdxf + custom auto-dim; headless print PDF | MIT + ours |
| Backend | FastAPI + Node BFF; Postgres + S3; Redis queues; **GPU: L4 in Mumbai (GCP asia-south1 / AWS g6)** — keeps the India data-residency claim honest for inference; frontier-LLM API residency position per §15 | — |
| Guardrail | CI license scanner blocking GPL/AGPL in app code; render cache by (view, seed, weights) hash; warm-pool policy | — |

---

## 9. Data Model

`Firm → User → Project(architectOfRecord*) → Plot(boundary, north, roads[], regProfile) → Brief(rooms[], vastuMode, style, budget) → DesignVersion(op-log range) → Storey[] → {Wall(axis, thickness), Opening(type, w×h, sill, host), Room(region, type, tags), Stair, Slab, FacadeKit, Furniture[]} + Levels{plinth, FFL, sill, lintel, floorHt, parapet} → SheetSet(sheets[], titleBlock, annotations[]→elementId) → RenderJob(view, mode, seed, weights, version) → ComplianceReport(packVersion, results[]) → ShareLink(scope, comments[])`

Every entity: id, storey, IFC-mappable class, **provenance** (solver-run / manual / copilot-op) — powers diffs, regeneration contract, training consent. *architectOfRecord required per §15.*

---

## 10. Release Plan (re-baselined per D6)

**Option A (default): 10 heads.** M0 spikes (mo 1–2) → M1 (mo 3): F0 core + editor alpha + rules skeleton + Bengaluru draft pack + solver v0 (single floor) → M2 (mo 5): multi-floor solver + compliance (BLR+NCR) + 3D + render pipeline up; **schema contract-freeze**; *5-firm solver taste panel* → M3 (mo 7): facades (2 kits) + copilot + share links + drawings alpha + **metered credits**; *25-firm alpha* → M4 (mo 8–9): municipal set hardened + DXF golden-files + HYD pack + billing; **closed beta 50 firms** → **Launch mo 10–11** gated on §13 metrics → **v1.1** (mo 11–13).

**Option B: 13 heads by M2 → launch mo 8–9.** Costs ~+₹1 Cr; buys ~2 months. Founder decision by M1.

*(What is not on the table at any headcount: v1.0's full-GFC-in-8-months scope.)*

---

## 11. v1.1 / v2 / v3 — Full Specs

### v1.1 (launch +1 to +3 months) — "Complete the GFC" (~11–12 em)

| Feature | Requirement | Acceptance criteria | Deps | Size |
|---|---|---|---|---|
| GFC drawing completion (F7-B) | Full dim depth, 2nd section, lintel/sill plans, khurra/OHT, MEP-assisted layouts, details library | Edit-rate ≤10% holds on GFC sheets; MEP legends auto-count correctly | F7-A telemetry | **L** |
| DWG export | ODA SDK integration | Opens native in AutoCAD 2018+, DIMSTYLE-faithful | ODA license (§17) | M |
| PDF/image plot trace | Scale-calibrated trace with snapping | Survey → boundary ≤5 min | F1 | M |
| +2 facade styles (Traditional, Colonial) | Parametric kits + content | Panel-approved quality | F5.2 | M |
| +2 city packs (Pune, Jaipur) | Authored + reviewed | Fixture suite green | data-ops | M (ops) |
| Interior-Precise renders + 4K | Furnished-viewport ControlNet | Interior geometry-locked renders match plan furniture | F4 3D furniture ≥150 items | M |
| Curved walls/arches; viewer role | — | — | editor | S |

### v2.0 (mo ~10–15 from start) — "Monetize the architect you won" (~15–18 em)

| Feature | Requirement | Acceptance criteria | Deps | Size |
|---|---|---|---|---|
| **BOQ & estimation** | Model quantities (brick/concrete/steel-indicative/plaster/flooring/openings/paint) + editable city rate libraries → branded budget sheet. Highest willingness-to-pay in pipeline | Quantities ±5% vs manual takeoff on 10 golden projects | stable quantities; materials lib | **L** |
| Team libraries & templates | Firm wall types, title blocks, detail sets, project templates (split from multiplayer — no CRDT needed) | New project from template <2 min; versioned, admin-managed | F10 | M |
| IFC export + SketchUp import | IFC4 via IfcOpenShell (process boundary); SKP context import | Opens as Revit IFC link; areas ±0.5% | F0 IFC-shaped core | M |
| Structural coordination | RCC column/beam grid suggestion (3–5m spans, floor continuity) + engineer share-mode markup | ≥70% suggested columns unmoved by reviewing engineer | model core | M |
| Renders v2 | Project-locked style consistency (IP-adapter/LoRA), night/dusk, 720p walkthrough video | Consistency passes panel; video ≤5 min gen | render infra | M |
| Hindi UI | Full translation (strings externalized at MVP) | 100% coverage; tier-2 activation lift measured | i18n scaffold | M |
| City-pack authoring tool + 8–12 cities | Internal authoring + reviewer workflow | New city ≤3 wks; zero live regressions | rules engine | M + ops |

### v2.5 (mo ~15–21) — "Expand seats & segments"

| Feature | Requirement | Acceptance criteria | Deps | Size |
|---|---|---|---|---|
| Multiplayer (Yjs) | Concurrent editing, presence/cursors (Studio tier) | 3 concurrent editors conflict-free on full op suite; offline-reconnect safe | D12 op design | **L** |
| MEP upgrade | Electrical circuits/DB schedule/load calc; plumbing sizing + drainage sheets | DB schedule ≥90% accepted by electrical engineer | v1.1 layouts | L |
| Interiors mode | Modular kitchen/wardrobe parametrics, false ceiling + electrical linkage, finishes schedules | Kitchen configurator → production elevation + counts | editor, electrical | **XL** (own squad) |
| Site context & sloped plots | Terrain import/contours, stepped plinths, compound wall + landscape | Sloped G+1 solves with plinth steps | solver changes | L–XL |
| Environmental lite | Sun-hours heatmap + per-room daylight score + orientation advice | Matches Radiance within tolerance; <10s | MVP sun widget | M |
| **MCP server** (pulled forward, D14) | Expose solver/model/drawing ops to Claude/ChatGPT etc. | Scripted plot→options→export flow completes externally; authed, rate-limited | F0 op API | S–M |
| Mobile client viewer (PWA) + 2 regional languages | Plans/3D/renders + comments on mid-range Android | G+2 model loads <5s on 4G | share links | M |
| Revit add-in | Only if beta firm-size data justifies | — | C# eng | L |
| Vastu marketplace | Regional Vastu schools as installable packs | 3 third-party packs live | rules engine | S–M |

### v3.0 (mo ~21–27) — "New engines on proven revenue"

| Feature | Requirement | Acceptance criteria | Deps | Size |
|---|---|---|---|---|
| **Builder/developer mode** (leads the wave — 10× ACV) | Row-house colony solver (roads + plot subdivision + catalog variants) + sales configurator | 1–5 acre colony <5 min meeting DTCP-class norms; per-plot variants | solver generalization | **XL** |
| Garh Agent | Overnight brief→shortlisted/rendered/documented design w/ review checkpoints over existing engines | ≥50% runs yield shortlisted option zero-fix; all steps reversible | engine stability + eval harness | L |
| Learned layout model | Proposal model on ≥100k solver-synthetic + consented plans; critic still validates | Beats solver-only on acceptance at equal compliance; <10s | corpus accrues from MVP | L |
| Municipal e-submission pilot (1 state) | AutoDCR-class formatting + liaison workflow. Potentially the deepest moat — pilot early | 10 real submissions accepted | drawings maturity, gov relations | L |

### v3.5 (mo ~27–33) — "New markets"

| Feature | Requirement | AC | Size |
|---|---|---|---|
| Homeowner self-serve tier | Wizard → concept + renders + budget → partner-architect marketplace handoff (different GTM + liability surface) | Funnel + architect-match SLA; legal review passed | **XL** |
| Apartment/low-rise multifamily | Corridor/core solver — enters TestFit/Snaptrude territory; dedicated squad only | G+4 walk-up, corridor efficiency ≥ benchmark | **XL** |
| E-submission scale-out (4–6 states) | Extend pilot | ≥ pilot acceptance rate | L |
| Marketplace (details/materials/vendors/render packs) | Rev-share + vendor monetization (needs traffic first) | 50 paid SKUs | M–L |
| Middle East/SEA | Arabic RTL, villa packs — option value, decision point not commitment | 2 GCC city packs | L–XL |

---

## 12. Team & Budget (new)

**Org (12–14 by M3):** 2 geometry/solver (OR-Tools/C++) · 2 frontend-canvas (Three.js/WebGL) · 1 drawings/CAD eng · 1 ML (diffusion) · 2 full-stack platform · 1 QA-automation (from M2) · 1 designer · 1 PM · **1 data-ops lead + 2–3 contract architects** (rule packs, golden corpus, panel) · content contractor (furniture/materials/details). **Key-person risk:** solver lead & drawings lead — named backups + documented design notes mandatory.

**Program budget to launch ≈ ₹6–7 Cr** (Option B staffing, GPUs, content, data, legal).

**Unit economics (validate in beta via D11 metering):**

| Item | Est. |
|---|---|
| 2K Precise render (L4 ≈ $0.70/hr, 15–25s) | ₹1–2; 8-image client pack ₹10–15 |
| LLM per project (parse + rationales + ~30-op copilot sessions) | ₹150–300 |
| **Project COGS all-in** (incl. regen waste, solver CPU, storage) | **₹200–450** |
| Solo ₹2,500–4,000/mo (3 projects) → gross margin | ~70–85% |
| Pay-per-project ₹3,000–5,000 → margin | 90%+ (priced above a Solo month deliberately — convenience premium for the long tail; states the cannibalization logic) |
| Fixed floor | 1 warm L4 24/7 ≈ ₹45k/mo; photo licensing ₹20–50L one-time; ODA ~₹8L/yr; rule-pack ops ₹40–60L/yr |

## 13. QA & AI Evaluation (new; gates launch)

- **100-brief golden corpus** stratified by plot size/shape/floors/city.
- **Monthly blind panel:** 5 architects score plans 1–5 (buildability, circulation, plausibility) vs junior-architect baseline. Launch gate: mean ≥3.5, ≥70% of briefs yield ≥1 shortlistable option.
- **Compliance:** positive/negative fixture per rule; packs can't ship red.
- **Drawings:** golden-file visual diff in CI + **AutoCAD-in-CI rig from M2** (DXF fidelity). Launch gate: dim edit-rate ≤10% on F7-A sheets.
- **Copilot:** 200-command eval set; apply-rate ≥60% gate.
- **Renders:** preference test vs Lumion-baseline images.
- Harness budget: ~2 em. §14 metrics are wired from M1 (they gate launch, not report on it).

## 14. Success Metrics

Activation: ≥60% of new firms generate options on a real plot in week 1 · **Money metric: ≥35% of *paid* projects export a submission set by month 3 post-launch** · option-acceptance ≥70% · copilot apply-rate ≥60% · dim edit-rate ≤10% · median brief→submission-set ≤3 days (instrumented) · month-3 logo retention ≥70% · NPS ≥40.

## 15. Legal, Liability & Data Policy (new)

- **Architect-of-record required per project** (Architects Act 1972 / COA registration — only registered architects sign/submit); Garh outputs framed as "instruments of service authored by the architect"; advisory-not-approval disclaimer surfaced **at export**, not buried in ToS.
- Standard-details indemnity position + PI-insurance guidance; construction-law counsel review **before beta**.
- **DPDP Act 2023:** briefs contain family composition, budget, religious inference (pooja/Vastu) = sensitive PII → explicit consent, retention limits, deletion rights.
- Designs owned by the firm; **training consent default OFF**, contractual, per-tenant; provenance field enables clean opt-in corpora.
- Data residency: inference GPUs in Mumbai; frontier-LLM API processing location disclosed + DPDP-reviewed position; commissioned-photo licenses must cover training + derivative display.

## 16. GTM & Beta Program (new)

- **Beta cohorts:** 5 design partners @M2 (*solver taste panel* — the product isn't there yet; compensated: free + stipend for golden-corpus contributions) → 25 @M3 (workflow alpha) → 50 @M4 (closed beta with launch gates + explicit kill/pivot criteria).
- **Channels:** design-partner case studies ("house designed in a day"); IIA chapter events; Hindi/regional CAD-tutorial YouTube ecosystem; architect WhatsApp networks; student edition (COA colleges); **cement/steel-brand home-building programs (UltraTech-class) as distribution partnerships**.
- **Motion:** PLG for Solo; sales-assist for Studio. Pricing tested against the anchor: a ₹40–60k-fee house consuming 40+ hrs drops to <10 — subscription pays for itself on one project, and beats the ₹20k/mo junior-drafter status quo on both speed and cost.
- **Competitive playbook:** Snaptrude ships house mode (Bangalore-origin — plausible) → defend on drawings depth + city-pack data + design-partner lock-ins · Forma House tool free-with-Revit → compete on India codes/drawings/price + DXF-in · Maket/Finch add "Vastu/NBC prompts" → market "citable rules vs vibes" · **₹500/mo AutoCAD auto-dim plugin attacker** (scariest cheap attack on F7) → integrated regeneration contract is the answer; consider shipping our own AutoCAD companion later.

## 17. Decisions & Owners (was "open questions")

| Decision | Status | Owner / due |
|---|---|---|
| Launch cities | **Decided: BLR, NCR, HYD** (D2) | — |
| Option A vs B staffing | Open | Founder, by M1 |
| ODA DWG license (~₹8L/yr) | **Decided: v1.1 line item** | Eng lead, budget M2 |
| Plan corpus sourcing | **Decided: partner-firm licensing deal** (per-plan license + beta stipend), not open-market purchase | PM + data-ops, M1 |
| Vastu consultant on advisory board | **Decided: yes, before beta** | Founder, M2 |
| Trademark "Garh AI" + garh.ai domain | Open | Founder, immediately |
| Frontier-LLM residency position (DPDP) | Open | Counsel, before beta |

## 18. Risks

| Risk | Mitigation |
|---|---|
| Solver legal-but-ugly plans (feasible ≠ plausible) | M0 S1 gate w/ architect panel; heavy post-processing heuristics; pivot path = copilot-assist positioning |
| Auto-dim below professional bar | M0 S2 gate; edit-rate telemetry gates launch; fallback = dimension-assist mode |
| Bye-law data wrong → liability | Versioned cited packs, architect review, architect-of-record + export disclaimers, override-everything UX |
| 2D editor slips (largest deterministic cost) | S3 spike; interaction primitives budgeted at 8–12 em, not hand-waved |
| Regeneration contract overpromise | Scoped per D13 (review tray, not magic) |
| Render style generic/Western | Wk-1 photo procurement; LoRA on licensed Indian imagery |
| GPU idle cost at low scale | Warm-pool policy + cache; metering from M3 |
| DWG expectation vs DXF | ODA in v1.1; "opens in AutoCAD" messaging tested in beta |
| License contamination (GPL/AGPL/RPLAN) | CI scanner; no research weights; provenance on training data |
| Adoption fear ("AI replaces me") | "Your drafting team, not your replacement"; architect-authored outputs, firm branding on every sheet |

---

*Appendix A (op taxonomy, ~25 ops) and Appendix B (rule-pack schema) to be authored in M1. Companion doc: ai-architecture-design-research-and-build-plan.md (market, JTBD, OSS licensing).*
