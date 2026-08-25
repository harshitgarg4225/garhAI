# AI Design Software for Architects — Market Top 3, Jobs-to-be-Done & Open-Source Build Plan

*Research date: July 30, 2026. Licenses verified against raw GitHub LICENSE files / npm metadata where noted. Vendor claims flagged as such.*

---

## Part 1 — Market Landscape & Top 3

### Landscape (mid-2026)

| Category | Players | Notes |
|---|---|---|
| Feasibility / generative site planning | **Autodesk Forma**, **TestFit**, Giraffe, Digital Blue Foam, ARCHITEChTURES, ArkDesign.ai, Spacio | Deepest "real AI" (optimization + ML analysis) |
| AI-native browser BIM ("BIM 2.0") | **Snaptrude**, Arcol, Motif ($46M, ex-Autodesk founders), Qonic, Hypar | Concept → BIM in browser; agentic AI emerging |
| AI floorplan generators | Finch3D, Maket.ai (1M+ users), Rayon | Narrow but genuinely generative |
| AI documentation automation | Swapp ("Frank" agent, used by Stantec/Page/HGA), Qonic drawings, BricsCAD BIMify | Hardest, highest-value stage; no top-3 platform owns it yet |
| AI visualization only | Veras (acquired by Chaos 2025), LookX, mnml.ai, PromeAI | Cosmetic AI — excluded from "end-to-end" ranking |

Market context: generative-AI-in-architecture ≈ $1.5B (2025) → ~$2.1B (2026), ~41% CAGR. AEC Magazine calls 2026 the start of the "agentic BIM" era.

### Top 3 (ranked on end-to-end coverage, AI depth, adoption, interop, momentum)

**1. Autodesk Forma** — Only verified end-to-end chain: Site Design (feasibility) → **Forma Building Design** (schematic, GA Apr 7, 2026) → Revit as first "Forma Connected Client" → documentation. ML surrogate analyses (sun/wind/noise/carbon), generative massing, bundled free with every Revit subscription. Owner: Autodesk (Spacemaker acquired $240M, 2020). ~$185/mo standalone.
Sources: [Architosh](https://architosh.com/2026/04/autodesk-intros-new-forma-building-design/), [Autodesk blog](https://blogs.autodesk.com/forma/2026/04/07/introduction-to-forma-building-design/), [engineering.com](https://www.engineering.com/autodesk-launches-forma-building-design-to-complement-revit/)

**2. Snaptrude** — Deepest single-tool AI: multi-agent orchestration (LLM + physics models, creator–critic) takes RFP → zoning/code-aware ~LOD 250/300 BIM model in 7–12 min, then auto-diagrams/presentations; two-way Revit/Rhino. $21.5M raised (Accel); ~20k users (VMDO, OPN, Clark Nexsen). Free–$100/user/mo, moving to token pricing.
Sources: [AEC Magazine](https://aecmag.com/bim/snaptrude-on-ai/), [Dezeen](https://www.dezeen.com/2025/10/15/snaptrudes-ai-platform-architects/)

**3. TestFit** — Most commercially proven generative engine: deterministic solvers co-optimize site plan, building, units, corridors, cores, egress, parking, pro forma in real time (~3,000 variants in seconds). 10 years old, $20M Series A (Prologis Ventures); customers incl. Prologis, Perkins&Will, DPR. Parking Solver $195/mo; Site Solver from $10k/yr. 2026: **MCP server** ("no hallucinations — LLM just pushes the buttons"), announced Jul 28, rollout from Aug 5, 2026.
Sources: [testfit.io/pricing](https://www.testfit.io/pricing), [TestFit MCP](https://www.testfit.io/mcp), [10-year news](https://www.testfit.io/news/testfit-celebrates-10-years-and-launches-free-platform-access-for-cities-ready-to-build-more-housing)

Why not the others: Motif's AI is still mostly rendering/collab; Arcol has little native generative AI; Swapp is documentation-only; Hypar pivoted narrow; Finch/Maket are floorplan-stage only.

---

## Part 2 — Exhaustive Jobs-to-be-Done (per tool, with nuances)

### 2A. Autodesk Forma — JTBD

**Site context & setup**
- **J1. Stand up a geolocated 3D site in minutes** — address search → terrain, existing buildings, roads, parcels (region-gated), vegetation, satellite imagery. Nuances: multi-provider data (OSM, Mapbox, Nearmap, Airbus, Maxar); height inference when OSM lacks it (3m/level, min 1); public per-country data-coverage map; CRS workaround docs; ArcGIS extension for authoritative GIS.
- **J2. Bring your own context** — OBJ/IFC (3D mesh), DXF (2D), raster underlays; 100MB cap; imported meshes placeable/georeferenced/editable; no native DWG/SKP (documented DXF/OBJ paths).
- **J3. Organize alternatives** — proposals (options) over shared bases (context); duplicate/rename; bases lettered so options share or diverge context.

**Zoning & constraints**
- **J4. Encode zoning envelopes** — transparent constraint volumes with exact numeric heights; stackable; violations instantly visible; excluded from analyses; setbacks/density as generative inputs.

**Modeling & massing**
- **J5. Purpose-built massing primitives** — Basic building (footprint → auto floor split, per-floor function), Line building (bars), House tool (parametric townhouse/row/semi/detached w/ garages), 3D Sketch (cantilevers, pitched roofs via ridge-line, arcs, mesh edit incl. imported OBJ).
- **J6. Terrain edit + cut/fill** — pads/pits along contours; slopes as ratios or %; real-time pad volume + site mass balance.
- **J7. Parking layout** — native parking metrics tab; TestFit-powered extension: stall types (ADA/EV/compact/trailer/custom), drive aisles, angles, turn radius, live ratio checks, exports Revit/DXF/SKP/CSV.

**Generative & AI design**
- **J8. Auto-generate site layouts** (Site Automation, ex-Spacemaker Explore) — options from heights/setbacks/density/typology rules, each paired with environmental performance.
- **J9. AI interior/unit layouts** (Building Layout Explorer, beta; "neural CAD" foundation model) — massing + type + structure → unit layouts w/ counts; auto-regenerates when footprint changes (AU25 demo: 36→56 units in seconds).
- **J10. Custom automations** — Dynamo Player runs graphs on Forma data; ShapeDiver hosts Grasshopper.

**Metrics**
- **J11. Live feasibility numbers** — GFA, FAR, coverage, rentable, net usable; **region-specific default metric sets**; custom metric formulas; units + parking tabs; CSV export.

**Environmental analysis (core differentiator)** — pattern: cloud, zero-setup, **rapid ML mode (seconds, live) vs detailed physics mode (sign-off grade)**, same UI/legend; all feed Compare.
- **J12. Sun hours** — per-point, multiple dates, custom periods, surface filters; NVIDIA OptiX ray tracing.
- **J13. Daylight potential** — Vertical Sky Component per facade point; thresholds mapped to indoor-daylight achievability and implied window sizing (BRE/EN 17037-style arguments).
- **J14. Wind** — rapid: ML surrogate trained on CFD per direction; detailed: OpenFOAM simpleFoam + atmospheric boundary layer, 30–90 min cloud; Global Wind Atlas/ERA5 roses; Lawson LDDC comfort classes; trees modeled (LAD 0.25, drag 0.2).
- **J15. Noise** — rapid: NN trained on tens of thousands of simulations; detailed: CNOSSOS-EU on ground/facades/roofs; per-road speed + ADT inputs; time-of-day filters.
- **J16. Microclimate / operative temperature** — sun+wind+sky+historical weather; climate-stress scenarios up to +20% above recorded max; adjustable comfort ranges; rooftop microclimate.
- **J17. Operational energy** — ML trained on EnergyPlus/Insight simulations; kWh/m²/yr; sensitive to WWR, constructions.
- **J18. Solar/PV** — surface irradiation + annual yield; panel coverage % + efficiency params; context shading.
- **J19. Embodied carbon** — EHDD C.Scale model; 1–10s for any massing; ML-estimated structural BoM (trained on 1,200+ real buildings); 20th/50th/80th-percentile material specs; hot spots + benchmarks.
- **J20. Total carbon dashboards** (Forma Carbon Insights, 2026) — embodied + operational, shared browser dashboards, thread into Revit.
- **J21. Third-party analyses in-canvas** — e.g. FenestraPro envelope analysis.

**Decide & communicate**
- **J22. Compare proposals** — synchronized side-by-side canvases, identical legends/criteria; 4K PNG/SVG captures with analysis overlays.
- **J23. Collaborate** — browser multi-user projects, roles, invite links.
- **J24. Forma Board** — BIM-connected whiteboard: live Revit sheet/view tiles, markups, @-comments, frames, presentation mode; **AI rendering with Precise (geometry-locked) vs Explore modes**.

**Schematic (Forma Building Design, 2026)**
- **J25. Massing → LoD 200 schematic** — footprint → auto floors (3m default, editable); typical-floor plans; unit types + mix schedules; **facade automations** (window/door/balcony groups propagate across plan/elevation/perspective); integrated sun/daylight/total-carbon; **"Continue in Revit": geolocated native Revit model with real wall/slab/window families** (not dumb masses).

**Interop & export**
- **J26. Revit round-trip** — element mapping (basic buildings → real walls 400mm/floors 100mm/roofs 225mm; 3D sketch → masses; terrain → toposolid); georeferencing preserved via Project Base Point; Connected Client: run Forma analyses inside Revit, data marketplace context in Revit, API-metered.
- **J27. Rhino/Grasshopper round-trip**; Data Exchange to Tekla/Power BI/IFC.
- **J28. Extensibility** — Embedded View SDK (JS), Forma API on APS, extension marketplace (TestFit, Finch, Veras, ShapeDiver, ArcGIS...).
- **J29. Exports** — OBJ, IFC 4.3 (beta), CSV metrics, 4K/SVG captures, native Revit.

*Cross-cutting production nuances worth copying:* dual rapid/detailed contract; named engines + published assumptions (OpenFOAM, CNOSSOS-EU, C.Scale, ERA5, tree LAD); everything comparative; region-awareness; graceful data degradation.

### 2B. Snaptrude — JTBD

**Import**
- **J1. PDF/image plan → trace to BIM** — scale calibration, snapping underlays, imported-files manager.
- **J2. DWG import per storey** — documented pre-flight (explode blocks, 2018 format, near origin); Graebert ARES DWG engine licensed.
- **J3. Revit import, two-way** — .rvt up to 150MB in-app (walls/floors/columns/doors/windows → editable families; Levels → storeys; Rooms + room parameters; fidelity % report post-import; parametric arc walls w/ documented exceptions; RFA custom families; one-click family extraction to team library). Marketed for TI/retrofit shells.
- **J4. Rhino two-way** — connector in, FBX out (cm units caveat); roadmap: AI packs program inside imported signature envelopes.
- **J5. Excel/CSV program import** — Interpret agent parses arbitrary sheets (names, areas, counts, departments, dims, stories) w/ review table → auto space blocks.
- **J6. OBJ/FBX 3D context.**

**Site**
- **J7. Real site w/ terrain + parcels** — US/Canada parcel picking (up to 10, merge), elevation/buildings/satellite toggles, editable neighbors, cut/fill with take-off dimensions, toposolids, draw-a-site, North visualizer; topo exports to Revit.
- **J8. Solar/shadow/illuminance analysis** — saved analysis views usable in presentations.

**Program & space planning**
- **J9. Live spreadsheet Program Mode** — departments/spaces/areas/counts target-vs-achieved; edits sync both ways with canvas; custom program sheets ("Revit schedules for early design"): chosen columns, grouping hierarchies, auto rollups, linked dims.
- **J10. AI program generation** — from prompt/RFP/benchmarks; agents: Generate Program, Update Program (surgical edits), Assign Dimension, Assign Stories; documented overwrite behaviors.
- **J11. Adjacency/blocking/stacking** — AI Adjacency from codes+program; auto bubble/adjacency diagrams; stories panel; split volumes; area/height locks; tags drive color modes/filters.
- **J12. Pack in Envelope** — arrange (adjacency-preserving, ≤20 objects, rectilinear) vs pack (morphs shapes, ±5% tolerance, ~80% adjacency auto-resolution); **program repacking when envelope changes**; shortfalls surface as target-vs-achieved violations.
- **J13. Area dashboard** — net/gross/excluded, FAR (site area from typed value or drawn site; per-space include/exclude), deviation thresholds w/ color coding, tag-based filters affecting rollups.
- **J14. Residential density/unit-mix studies** — instant density tests, unit mix, cost + ROI per option via program calcs.

**Agentic AI (Oct 6, 2025)**
- **J15. RFP → concept model autonomously** — 6-agent stack (site analysis, envelope, program, sizing, stacking, design inspiration); ~LOD 250/300 in 7–12 min honoring adjacencies + zoning/code; each step human-approved; re-promptable; documented v1 limits (no Excel in RFP upload, no mid-run pause, geometry edits not re-promptable).
- **J16. Zoning/code/ADA research agent** — citation-backed answers, compliance checks, occupancy calcs, benchmarking; ADA/IBC/BOMA/Neufert built in; limitation: output not machine-readable by other agents.
- **J17. Creator–critic anti-hallucination** — LLM creates, deterministic physics/climate models critique ("almost no hallucination" — vendor).
- **J18. In-context micro-delegation** — AI chatbot, command bar, chart agent, standalone site-analysis agent w/ documented data-source priority.

**Modeling & BIM**
- **J19. BIM-native sketching** — labeled spaces = rooms w/ areas; push/pull, booleans, arrays, edge/vertex edit, layers, multi-building files, section planes; "LOD hopping" philosophy.
- **J20. One-click Sketch-to-BIM** — mass → parametric walls/slabs/roofs/ceilings from presets or firm wall types; labels become floor labels feeding Areas.
- **J21. Native BIM elements** — parametric wall graph (works on imported Revit projects too), curtain/stacked walls, columns/beams/stairs, doors/windows, materials w/ Revit fidelity, construction layers.
- **J22. Smart Layouts at scale** — define room layout once → place across 200+ rooms; walls remap/stretch, door swings preserved, corridor-facing auto-orientation, shared boundary walls auto-merged (clean Revit export); documented v1 limits.

**Data & cost**
- **J23. Live BOQ** — by category (areas/volumes/counts), team-library-defined calc conventions.
- **J24. Live project cost / ROI** — cost reacts to design moves; program-based custom calcs.

**Visualization**
- **J25. AI render in ~20s from live model** — geometry-respecting (SD on Nvidia GPUs); AI Inspiration w/ model dropdown: Nano-Banana, Veo 3 (video), Flux-Kontext, Omnigen v2; style transfer, materials, entourage.

**Docs & presentation**
- **J26. Present Mode (Miro-like)** — live plan/3D/render/table/diagram tiles that update with model; sheet sizes/scales, dimensions on sheets, PDF/DWG export.
- **J27. 2D drawings** — labelled drawings, saved views, sections; auto-dimension/tag in development; goal: no Revit until schematic handoff.

**Collaboration**
- **J28. Multiplayer editing** + Brainstorm mode (broadcast camera path).
- **J29. Comments/markups** — 3D + plan, revision clouds, statuses, viewer commenting.
- **J30. Dashboard/versioning/sharing** — folders, share links, version history, teams w/ roles.

**Export**
- **J31. Native editable .rvt (flagship)** — walls as families w/ correct structure linked to Levels; per-room flooring; tags → Revit Room parameters populating schedules; custom parameters; bi-directional reconciliation, not one-shot dump.
- **J32. IFC** (ArchiCAD/SketchUp paths), **J33. DWG/PDF/FBX/OBJ** + program tables to spreadsheet/PDF.

**Standardization & enterprise**
- **J34. Project templates**; **J35. Central firm library** (materials, families, wall types, BOQ conventions).
- **J36. Firm knowledge base + AI query** — connectors (Drive, Sheets, Dropbox, Box; ACC/Procore/SharePoint/Egnyte announced); Project Query agent across past projects; per-tenant ring-fencing.
- **J37. Governance** — SOC 2 Type 2, roles, data residency (enterprise), education tier; token-based AI pricing signalled.

### 2C. TestFit — JTBD

**Site definition**
- **J1. Pick a site from parcels** — US+Canada parcels w/ ownership, APN search, multi-parcel merge, unlimited massing studies before consuming a site-lock credit.
- **J2. Define boundary manually** — metes-and-bounds entry (auto-close), freehand, KML/KMZ import, scaled raster underlays.
- **J3. Master-plan structure** — regions per typology/phase, custom road networks, copy/paste sites/regions/layers across files, low-detail massing mode.

**Site intelligence**
- **J4. Zoning data instead of reading ordinances** — Zoneomics: permitted uses, FAR, coverage, setbacks, height, DU/acre, parking requirements → one-click zoning profile driving the solver.
- **J5. Environmental risk screen** — FEMA flood, wetlands w/ auto exclusion easements, SSURGO soils (water table, bedrock, slope).
- **J6. Terrain/slope in 3D** — auto-sloped parcels/townhomes/driveways, daylight heatmaps, auto-stepped massing, wind rose orientation.
- **J7. Power/utility layers** — plants, transmission, gas, water, telecom (key for industrial/DC).

**Zoning & life safety in the solver**
- **J8. Pass/fail compliance per scheme** — FAR/DU-acre/height/coverage/parking as constraints or goals; building setbacks separate from landscape setbacks; impervious coverage tracking.
- **J9. Egress solved, not sketched** — stairs (travel/dead-end distances, dims), elevators (travel distance, bank depth, units/lift), auto firewalls at area thresholds, pinnable cores, fire-hose-length checks.

**Typology solvers**
- **J10. Multifamily (core)** — podium/wrap/tower/garden/"gurban"/townhome/blocks (core- or corridor-based, incl. European); double/single-loaded corridors, liner units, courtyard aspect control, multiple unit mixes per building by level range, amenity/retail/BOH insertion.
- **J11. Single-family/BTR subdivision** — lot-size + municipal road rules; detached/TH/ADU/bungalow/tiny; organic (curved) subdivisions; kit-of-parts homes; QTO per home + road length.
- **J12. Industrial** — cross-dock/single-dock, docks/trailer stalls/retention ponds/easements auto-generated, prototype building libraries, office insertion, internal/perimeter/external circulation.
- **J13. Data centers** — min/max MW bounds, power density + PUE params, halls/electric bays/generators/cooling yards, substation layouts, campus + infill modes.
- **J14. Retail** — pad/power/lifestyle/neighborhood centers; **drive-thru automation** (turn radii, stacking counts, multi-lane profiles); anchored parking perpendicular to anchor entrances.
- **J15. Hotel** — brand room standards as presets, auto key counts/mix/GFA.
- **J16. Parking structures standalone**; **J17. Office/core-based buildings** (lighter).

**Generative engine**
- **J18. Thousands of options ranked by your KPI** — ~3,000 variants in ~3s; sort by units, FAR, ratio, **yield-on-cost**, efficiency; stacked min/max filters; "regenerate" breeds similar; per-parameter GD activation ranges or locks; deterministic = auditable ("30x faster than Grasshopper scripting").
- **J19. Live recalculation** — drag a building/change a setback → geometry, stats, financials update instantly.

**Unit granularity**
- **J20. Dynamic unit mix** — % + avg SF per category; engine hits mix exactly, units stretch (e.g. 2BR 35% @1,200SF avg → 1,077–1,318SF range); balcony/corner/inset controls.
- **J21. Kit-of-parts unit library** — per-unit editor (dims, entries, balconies, bays); inline/corner/dead-end types; min/max corner angles; "library health" score predicting corridor solvability; shipped libraries (hotel, student, micro, modules...); cloud-shared; per-unit-type parking ratios; per-unit outputs (NRSF, demising length, skins).

**Parking**
- **J22. Surface parking maximization** — fill modes, angled stalls, multi-size stall types (ADA/EV/compact/trailer/custom), max-run planters, green-gap fill, access points, vehicle-path turning simulation.
- **J23. Structured/podium/wrap garages** — tray counts, rotation snapping, min length for ramping, levels above/below grade, ramp slopes, column buffers; liner units; **TT Core Studio (Thornton Tomasetti) structural column check + cost impact**.
- **J24. Ratio tracking live** — required vs provided in stats bar; ratios as target/constraint/GD goal.

**Civil & earthwork**
- **J25. Cut/fill balance + pricing** — visual gradation, balance to minimize haul, per-m³ costs → earthwork estimate; retention/detention pond volumes w/ max depth/slope.
- **J26. Roads/circulation** — municipal standards, auto-sloped driveways, tree placement w/ density/maturity.

**Economics**
- **J27. Auto QTO** — SF, units, stalls, paving, tilt-up wall area, road length → cost model.
- **J28. Deal economics in-tool** — land/hard/soft costs, revenue, **yield-on-cost per scheme as a sort key**.
- **J29. Live-linked Pro Forma (2026)** — import your own Excel preserving formulas; drag model-linked fields into cells; templates; embed live viewport in reports. Caveat: no market/comps data — assumptions are user inputs.

**Decide, report, collaborate**
- **J30. Scheme comparison** — persisted options w/ own design + financials; NRSF/efficiency/YoC stats.
- **J31. Reports/boards** — web reports, PDF w/ live links (stakeholders always see current version), markups on PDFs, glTF/CSV exports, Rayon visualization integration.
- **J32. Deal pipeline ("OneMap")** — active/won/lost/paused filters, version history w/ revert, browser editing (all but GD), SSO on Portfolio tier.

**Control & interop**
- **J33. Override anything** — manual modes per configurator; pin units/cores; locked params; edits survive re-solve.
- **J34. Revit handoff** — .tfrvt; map **your Revit system families** to TestFit elements → native walls/roofs/floors; interior unit fitouts as model groups; reverse: export kit-of-parts from Revit into TestFit.
- **J35. SKP / DXF (geolocated WGS84, clean layers) / CSV / glTF / PDF.**
- **J36. MCP server (2026)** — prompt outcomes in plain language from any AI assistant; same deterministic engine generates everything; shared editable model (prompt ↔ hand-edit, no reconciliation); BYO-assistant (conversations stay with AI vendor).
- **J37. Cities/urban planning module** — corridor-level zoning scenario testing linking parking minimums/density to unit counts and pro forma; free 1-yr city access (2026).

*Known limits:* no market data; outputs are optimized schematics, not permit docs; GD desktop-only; no mechanical-stacker parking; hospitals/labs uncovered.

---

## Part 3 — Open-Source Mapping per Job Cluster

The three tools' jobs collapse into 14 canonical clusters. For each: OSS candidates (license 🟢 permissive / 🟡 weak-copyleft workable / 🔴 copyleft-or-NC constrained), fit, and gaps. Licenses verified from raw LICENSE files / npm metadata during research unless noted.

### C1. Site context & geodata (Forma J1-J2, Snaptrude J7, TestFit J1-J7)

| Job | OSS | License | Fit |
|---|---|---|---|
| Streets, footprints, POIs | [OSMnx](https://github.com/gboeing/osmnx) | 🟢 MIT | Auto site context from OSM |
| 1.4B building footprints, roads, places | [Overture Maps](https://github.com/OvertureMaps/data) | 🟡 CDLA-P-2.0; buildings theme ODbL (share-alike on derived *databases* — architect your storage accordingly) | Instant global context |
| Map canvas | MapLibre GL JS | 🟢 BSD-3 | Parcel-picking UI (OSS Mapbox fork) |
| GIS ingestion (parcels, DEMs, imagery) | [GDAL](https://github.com/OSGeo/gdal) | 🟢 MIT | Everything raster/vector |
| LiDAR terrain | [PDAL](https://github.com/PDAL/PDAL) | 🟢 BSD-3 | Existing conditions from USGS 3DEP |
| 3D city models | [CityJSON](https://www.cityjson.org/)/[cjio](https://github.com/cityjson/cjio), [3DCityDB](https://github.com/3dcitydb/3dcitydb) | 🟢 MIT / Apache-2.0 | Store/serve urban context |
| Urban morphology metrics | [momepy](https://github.com/pysal/momepy), [UrbanSim](https://github.com/UDST/urbansim) | 🟢 BSD-3 | Density/typology analytics |

Gap: **US parcel geometry + ownership is commercial data** (Regrid/Lightbox; TestFit uses similar). No OSS equivalent — budget a data license.

### C2. Zoning & code rules (Forma J4, Snaptrude J16, TestFit J4, J8)

| Job | OSS | License | Fit |
|---|---|---|---|
| Zoning data schema | [OZFS](https://research.gsd.harvard.edu/vibelab/2025/08/19/open-zoning-feed-specification-ozfs) (Harvard, 2025) | open spec | Adopt early — "GTFS for zoning" |
| US zoning data | [National Zoning Atlas](https://github.com/National-Zoning-Atlas) | 🟡 open data, per-team terms | 33k+ jurisdictions coverage seed |
| LLM extraction of rules from ordinance PDFs | [zoning-gpt](https://github.com/National-Zoning-Atlas/zoning-gpt) | check repo | Pattern to replicate, not embed |
| Rules engine (setback/FAR/height evaluation, pass/fail) | — | — | **Build from scratch** (no mature OSS exists; core IP) |

### C3. Program & space planning (Snaptrude J9-J14, Forma J11)

| Job | OSS | License | Fit |
|---|---|---|---|
| Adjacency graphs | NetworkX | 🟢 BSD-3 | Program adjacency modeling |
| Space topology/adjacency reasoning | [topologicpy](https://github.com/wassimj/topologicpy) | 🔴 AGPL-3.0 | Exactly the space-graph engine needed — isolate as sidecar service or avoid |
| Spreadsheet UI (program mode) | Univer / AG Grid Community | 🟢 Apache-2.0 / MIT | Live program tables (avoid HyperFormula — GPL) |
| Live program↔model sync | — | — | **Build** (the sync semantics are the product) |

### C4. Generative massing, floorplans & layout solving (Forma J8-J9, Snaptrude J12/J15, TestFit J10-J21)

⚠️ Poison pill: nearly all floorplan-ML research trains on the **RPLAN dataset (research-only, no redistribution)** — even MIT/Apache repos ship unusable weights. Plan to retrain on licensed/synthetic data.

| Job | OSS | License | Fit |
|---|---|---|---|
| Constraint solving (unit mix, core placement, parking counts) | [OR-Tools](https://github.com/google/or-tools) CP-SAT/MIP | 🟢 Apache-2.0 | Backbone of a TestFit-style deterministic solver |
| Multi-objective option ranking (daylight vs FAR vs cost) | [pymoo](https://github.com/anyoptimization/pymoo) (NSGA-II/III) | 🟢 Apache-2.0 | Forma-style Pareto option generation |
| Evolutionary massing search | [DEAP](https://github.com/DEAP/deap) | 🟡 LGPL-3.0 | GA search, fine server-side |
| Procedural facade/module tiling | [WaveFunctionCollapse](https://github.com/mxgmn/WaveFunctionCollapse) | 🟢 MIT | Facade automations (Forma J25) |
| "Autocomplete my plan" transformer | [MaskPLAN](https://github.com/HangZhangZ/MaskPLAN) (CVPR'24) | 🟢 MIT code, 🔴 RPLAN weights | Best-licensed scaffold to fork + retrain |
| Chat-driven floorplan editing | [ChatHouseDiffusion](https://github.com/ChatHouseDiffusion/chathousediffusion) | 🟢 Apache-2.0 code, 🔴 RPLAN weights | Architecture reference for NL plan editing |
| Language→layout training pattern | [Tell2Design](https://github.com/LengSicong/Tell2Design) | 🟡 Apache code; verify dataset | NL→plan copilot recipe |
| Unusable but instructive | HouseGAN/++ (🔴 research-only), HouseDiffusion (🔴 NC), Graph2Plan (🔴 no license), GSDiff (🔴 GPL), CubiCasa5k (🔴 CC-BY-NC) | 🔴 | Read the papers; don't ship the code/weights |

Gap: **the deterministic typology solver (units/corridors/cores/egress/parking co-solve) does not exist in OSS. It's TestFit's decade-long moat — build from scratch on OR-Tools + custom heuristics.**

### C5. Geometry kernel & BIM data model (Forma J5, Snaptrude J19-J22)

| Job | OSS | License | Fit |
|---|---|---|---|
| In-browser IFC parse/write | [web-ifc](https://github.com/ThatOpen/engine_web-ifc) (WASM) | 🟡 MPL-2.0 (file-level) | Core of a web BIM editor |
| Web BIM viewer/components | [@thatopen/components](https://github.com/ThatOpen/engine_components) | 🟢 MIT | Fastest path to viewer, clipping, measurement, streaming |
| Server-side IFC + geometry | [IfcOpenShell](https://github.com/IfcOpenShell/IfcOpenShell) | 🟡 LGPL-3.0 (process/dynamic-link boundary) | Import/export, QTO, authoring pipeline |
| Fast robust mesh booleans | [Manifold](https://github.com/elalish/manifold) | 🟢 Apache-2.0 (JS/WASM bindings) | Real-time massing booleans in browser + server |
| Full B-rep (fillets, STEP) | [OCCT](https://github.com/Open-Cascade-SAS/OCCT) / opencascade.js | 🟡 LGPL-2.1 + exception (statically linkable) | Heavy modeling ops server-side |
| Parametric components as code | [CadQuery](https://github.com/CadQuery/cadquery) / [build123d](https://github.com/gumyr/build123d) | 🟢 Apache-2.0 | LLM-friendly codegen target for components |
| Rhino interop | [rhino3dm/opennurbs](https://github.com/mcneel/rhino3dm) | 🟢 MIT-style | .3dm read/write (architect adoption) |
| Mesh utilities | [trimesh](https://github.com/mikedh/trimesh) | 🟢 MIT | Cleanup, volumes, raycasts |
| Straight skeletons (roofs), exact predicates | [CGAL](https://github.com/CGAL/cgal) | 🔴 GPL for most packages | Isolate as microservice or buy GeometryFactory license |
| Reference native-IFC authoring UX | Bonsai/BlenderBIM | 🔴 GPL-3.0 | Study only; don't embed |

Gap: **the parametric building object model (wall graph, storey logic, live constraints) is from-scratch work** on top of IFC semantics.

### C6. Environmental & performance analysis (Forma J12-J21, Snaptrude J8)

| Job | OSS | License | Fit |
|---|---|---|---|
| Energy engine | [EnergyPlus](https://github.com/NREL/EnergyPlus) | 🟢 BSD-style | The engine Forma's ML was trained against |
| Energy model authoring | [OpenStudio](https://github.com/NREL/OpenStudio) SDK, [eppy](https://github.com/santoshphilip/eppy) | 🟢 BSD-style / MIT | License-safe automation path (skip AGPL honeybee) |
| Daylight/glare | [Radiance](https://github.com/LBNL-ETA/Radiance) | 🟡 LBNL custom (commercial OK w/ conditions; confirm w/ LBNL for embedding) | Validated daylight + VSC computation |
| Wind CFD | [OpenFOAM](https://github.com/OpenFOAM/OpenFOAM-dev) (simpleFoam + ABL) | 🔴 GPL-3.0 — fine server-side as isolated process (GPL triggers on distribution, not SaaS) | Exactly Forma's detailed wind stack |
| Urban heat island weather morphing | [UWG](https://github.com/ladybug-tools/uwg) | 🔴 AGPL | Microclimate-adjusted EPWs — isolate or rebuild |
| Full env-design suite | [Ladybug Tools](https://github.com/ladybug-tools) | 🔴 AGPL-3.0 org-wide | Gold standard, but AGPL: run unmodified sidecar, license via Pollination, or rebuild on E+/Radiance directly |
| Sun-hours ray tracing | Three.js GPU / Embree / custom OptiX | 🟢 | Simple to build directly — it's raycasting |
| Embodied carbon factors | ÖKOBAUDAT + open EPD sets; (C.Scale & EC3 are proprietary/API-gated) | 🟡 verify per dataset | Build a factors DB; ML BoM estimator = from scratch |
| Noise (CNOSSOS-EU) | opeNoise (QGIS plugin, GPL); research implementations | 🔴 sparse | Mostly **build** (CNOSSOS-EU spec is public) |

Gap: **the ML surrogates (seconds-fast wind/noise/energy) are Forma's real moat. OSS gives you the physics engines to generate training data; the surrogate models are from-scratch** (standard GNN/CNN regression — feasible with a sim farm).

### C7. Cost, QTO & pro forma (Snaptrude J23-J24, TestFit J27-J29)

| Job | OSS | License | Fit |
|---|---|---|---|
| QTO from IFC | IfcOpenShell utils | 🟡 LGPL | Areas/volumes/counts by category |
| Spreadsheet engine (pro forma w/ Excel import) | Univer; SheetJS CE for xlsx I/O | 🟢 Apache-2.0 | Avoid HyperFormula (GPL/commercial) |
| Cost data | — | — | Commercial (RSMeans etc.) or user-supplied — same as TestFit |
| Yield-on-cost / deal model | — | — | **Build** (thin, well-understood domain logic) |

### C8. Civil & terrain (Forma J6, TestFit J25-J26)

Delaunay/TIN: delaunator (MIT), CDT (MPL). Cut/fill = prism volume diff on TINs — **build** (small, on trimesh/GDAL). Roads/drive networks: no usable OSS — **build** parametric curve tools. Pond volumes: straightforward geometry.

### C9. Visualization & AI rendering (Forma J24, Snaptrude J25, TestFit J31)

| Job | OSS | License | Fit |
|---|---|---|---|
| Geometry-locked AI render (the Veras/Forma trick) | [diffusers](https://github.com/huggingface/diffusers) + [ControlNet](https://github.com/lllyasviel/ControlNet) (depth/canny/MLSD) | 🟢 Apache-2.0 | Render from viewport depth+edges; "Precise vs Explore" = ControlNet weight |
| Model weights | SDXL (OpenRAIL — commercial OK), FLUX.1-schnell (Apache-2.0), Qwen-Image (Apache-2.0); **avoid FLUX.1-dev (NC)** | 🟡/🟢 | License-clean image stack |
| Orchestration | ComfyUI | 🔴 GPL-3.0 | Isolate as service, or code pipelines directly in diffusers |
| Upscaling | [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) | 🟢 BSD-3 | Output polish (avoid SUPIR — NC) |
| Beauty renders | Blender headless (Cycles) | 🔴 GPL as external process — outputs unencumbered | Server render farm |

### C10. Documentation & drawings (Snaptrude J26-J27, Forma J29)

| Job | OSS | License | Fit |
|---|---|---|---|
| DXF/DWG-world export w/ dimensions | [ezdxf](https://github.com/mozman/ezdxf) | 🟢 MIT | CD-set primitives (dims, blocks, layouts) |
| IFC → SVG/DXF plans/sections | IfcConvert (IfcOpenShell) | 🟡 LGPL | Auto-2D extraction from BIM (engine behind Bonsai docs) |
| Native DWG read | libredwg | 🔴 GPL | Isolated microservice, or license ODA (~$) |
| Sheet PDF | typst / Playwright print / react-pdf | 🟢 | Report + sheet output |
| **Auto-dimensioning / auto-tagging / sheet layout logic** | — none exists — | — | **Greenfield. Biggest open IP opportunity in the whole space** (Swapp's moat) |

### C11. Comparison, reports, dashboards (Forma J22, TestFit J30-J32)

All buildable with standard web stack (React, d3/recharts MIT). Live-link reports = your own doc model. Nothing AEC-specific needed.

### C12. Collaboration & versioning (Forma J23, Snaptrude J28-J30)

| Job | OSS | License | Fit |
|---|---|---|---|
| Multiplayer CRDT editing | Yjs + Hocuspocus | 🟢 MIT | Snaptrude-style multiplayer, cursors, presence |
| Versioned AEC geometry backbone | [Speckle server](https://github.com/specklesystems/speckle-server) | 🟢 Apache-2.0 (verified — no license change; avoid the proprietary `gatekeeper` EE module) | Object DB, versioning, GraphQL, viewer + Revit/Rhino/GH connectors (Apache) |
| Comments/markups | — | — | Build on Yjs (thin) |

### C13. Interop (Forma J26-J28, Snaptrude J31-J33, TestFit J34-J35)

| Job | OSS | License | Fit |
|---|---|---|---|
| Revit two-way | Speckle Revit connector (Apache-2.0) as base; native add-in via Revit API | 🟢 | **Native .rvt fidelity (family mapping, reconciliation) is from-scratch C# work** — it's Snaptrude/TestFit's hardest-won feature |
| IFC in/out | IfcOpenShell + web-ifc | 🟡 | Covered |
| Rhino | rhino3dm | 🟢 MIT | Covered |
| DXF | ezdxf | 🟢 MIT | Covered |
| SketchUp | (no OSS writer of quality) | — | Export via glTF/OBJ/DXF instead |
| glTF | three.js exporters | 🟢 MIT | Covered |

### C14. AI copilot & agents (Snaptrude J15-J18, TestFit J36)

| Job | OSS | License | Fit |
|---|---|---|---|
| Expose your engine to any assistant | MCP SDKs (Anthropic) | 🟢 MIT | TestFit's exact 2026 move — table stakes |
| Agent orchestration | LangGraph (MIT) / pydantic-ai (MIT) / plain SDK loops | 🟢 | RFP→concept pipeline plumbing |
| LLM emits parametric code | Text-to-CadQuery pattern (2025 papers); BlenderLLM (CC-BY-4.0) | 🟢 | Generate build123d/CadQuery = verifiable, license-clean copilot |
| Creator–critic guardrails | — | — | **Build**: LLM proposes, your deterministic solver/rules engine validates (Snaptrude's pattern, TestFit's MCP philosophy) |

---

## Part 4 — The Build Plan

### 4.1 Reference architecture

```
Browser (React + TypeScript)
├── 3D canvas: Three.js + react-three-fiber (MIT)
├── BIM engine: web-ifc (MPL) + @thatopen/components (MIT)
├── Booleans/massing: Manifold WASM (Apache)
├── Map/site: MapLibre GL (BSD) + Overture/OSM tiles
├── Program/pro-forma grids: Univer (Apache) / AG Grid CE (MIT)
└── Multiplayer: Yjs + Hocuspocus (MIT)

API layer (Node/TS or Python)
├── Versioned geometry store: Speckle server (Apache) or Postgres+S3 custom
├── Auth/tenancy/permissions: standard (from scratch, thin)
└── MCP server exposing solver + model tools (MIT SDK)

Compute workers (Python, containerized — process isolation = license firewall)
├── Geometry: IfcOpenShell (LGPL) + OCCT (LGPL+exc) + trimesh (MIT)
├── Solver: OR-Tools CP-SAT + pymoo (Apache) + CUSTOM typology engine
├── Sim farm: EnergyPlus (BSD) · Radiance (LBNL) · OpenFOAM (GPL, isolated)
├── ML surrogates: PyTorch models trained on sim-farm output (CUSTOM)
├── AI render: diffusers + ControlNet + SDXL/FLUX-schnell (Apache/RAIL)
└── Docs: IfcConvert (LGPL) + ezdxf (MIT) + CUSTOM auto-dimensioning
```

License strategy: keep 🟢/🟡 in-process; run every 🔴 (OpenFOAM, CGAL-GPL, ComfyUI, Blender, libredwg) as an unmodified, process-isolated container talking over HTTP/files — or replace it. Never link AGPL (Ladybug, topologicpy, xeokit) into proprietary code; prefer rebuilding on the underlying permissive engines (EnergyPlus/Radiance direct instead of honeybee).

### 4.2 What each OSS project is for (the reuse map)

| Layer | Use this | Instead of building |
|---|---|---|
| IFC parsing, geometry, QTO | IfcOpenShell + web-ifc | ~3-5 yrs of schema work |
| Web BIM viewer/editor chrome | @thatopen/components + Three.js/R3F | 1-2 yrs of viewer engineering |
| Robust massing booleans | Manifold | Numerical-robustness hell |
| B-rep ops, STEP, skeletons | OCCT (+ CGAL via paid license if needed) | A geometry kernel (decade+) |
| Site context | OSMnx + Overture + GDAL/PDAL + MapLibre | Global GIS pipeline |
| Constraint/multi-objective solving | OR-Tools CP-SAT + pymoo | Solver R&D |
| Energy/daylight/wind physics | EnergyPlus + OpenStudio/eppy + Radiance + OpenFOAM | Validated simulation engines (impossible to rebuild credibly) |
| Versioning + Revit/Rhino/GH connectors | Speckle (fork connectors) | Multi-year interop grind |
| Multiplayer | Yjs | CRDT research |
| AI rendering | diffusers + ControlNet + SDXL/FLUX-schnell | Image-model training |
| 2D/DXF/sheets primitives | ezdxf + IfcConvert + svg | DXF spec implementation |
| Rhino/DXF interop | rhino3dm, ezdxf | File-format reverse engineering |
| Agent plumbing + MCP | MCP SDK, LangGraph/pydantic-ai | Protocol work |

### 4.3 What we code from scratch (= the product's IP)

Ordered by moat value:

1. **Deterministic typology solver** — the TestFit-class engine co-solving footprint, units, corridors, cores, egress, parking on OR-Tools + custom geometric heuristics. No OSS exists. This is the moat; everything else orbits it.
2. **Zoning rules engine + data pipeline** — OZFS-aligned schema, LLM ordinance extraction (zoning-gpt pattern) with human review, deterministic evaluation (setback/FAR/height/parking pass-fail). Second moat: data quality compounds.
3. **Parametric building object model** — wall graph, storeys, spaces, live constraints, sketch-to-BIM presets; IFC-semantic in-memory model with CRDT sync. (Snaptrude's core.)
4. **ML analysis surrogates** — run your own EnergyPlus/OpenFOAM/Radiance farm to generate training data; train GNN/CNN surrogates for seconds-fast wind/noise/energy/daylight. (Forma's pattern — the engines are free, the surrogate + UX is yours.)
5. **Auto-dimensioning & sheet generation** — placement logic for dims/tags/annotations on auto-extracted plans. Zero OSS competition; the documentation frontier (Swapp's territory) is the most defensible long-term bet.
6. **Program mode** — live spreadsheet↔model sync, target-vs-achieved, pack-in-envelope repacking.
7. **Agentic layer** — RFP→program→envelope→stacking pipeline with creator–critic loop: LLM proposes, solver/rules-engine/simulators validate, violations surface as diffs. Plus an MCP server over your own engine (TestFit's play, table stakes by 2027).
8. **Revit add-in** (C#) — native family mapping + bidirectional reconciliation (start from Speckle's Apache connector, extend to Snaptrude-grade fidelity: tags→room parameters, storey↔level mapping).
9. **Floorplan generative model (optional, later)** — fork MaskPLAN/ChatHouseDiffusion architectures but retrain on licensed/synthetic data (generate synthetic plans with your own solver — solver output becomes training data, RPLAN-free).
10. **Pro forma + deal model** — thin domain logic; Excel import via SheetJS.

### 4.4 Phasing

**Phase 1 (months 0–6): Feasibility MVP** — site canvas (MapLibre + Overture/OSM + GDAL terrain), massing tools (Manifold), live metrics (GFA/FAR/units), zoning profiles v1 (manual entry), sun-hours + VSC daylight (own raycaster + Radiance), proposal compare, PDF report. *Proves the loop: site → massing → numbers → decision.*

**Phase 2 (6–12): The solver** — multifamily typology engine v1 (bar/podium + surface parking + unit mix on OR-Tools), parking solver, cut/fill, yield-on-cost, scheme comparison, DXF/glTF/CSV export, Revit export v1 (via Speckle fork). *This is when the product becomes sellable — TestFit's wedge (parking solver first) is the proven GTM.*

**Phase 3 (12–18): Analysis depth + BIM** — sim farm (EnergyPlus/OpenFOAM/Radiance) + first ML surrogates (wind, energy), noise (CNOSSOS-EU impl), embodied carbon v1, sketch-to-BIM + IFC export, multiplayer (Yjs), AI rendering (ControlNet), program mode.

**Phase 4 (18–30): The frontier** — agentic RFP→concept with creator–critic, MCP server, auto-drawings/auto-dimensioning, firm knowledge base (RAG), unit-plan kit-of-parts libraries, pro forma live-link.

### 4.5 Key risks & mitigations

- **Parcel/zoning data cost** — commercial parcel data (Regrid-class) is unavoidable for US parity; start with drawn boundaries + KML import (TestFit's own fallback) and add data tiers later.
- **RPLAN taint** — never ship weights trained on it; use your deterministic solver to mass-generate synthetic training plans instead.
- **AGPL contamination** — codify the container firewall in CI (license scanner blocking AGPL/GPL imports into app code).
- **Revit fidelity** — underestimated by everyone; budget a dedicated C# engineer from Phase 2.
- **Credibility of analyses** — copy Forma's playbook: name your engines, publish assumptions, benchmark vs consultants, offer rapid-vs-detailed modes.

### 4.6 Bottom line

OSS gets you ~60-70% of the commodity stack (viewer, IFC, geometry, physics engines, GIS, collab, rendering, interop formats) with a fully permissive backbone. The four things you cannot download — the typology solver, the zoning data+rules engine, the ML surrogates, and auto-documentation — are exactly the four moats of TestFit, TestFit/Forma, Forma, and Swapp respectively. Build those; assemble the rest.


