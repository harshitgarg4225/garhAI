# Jobs-to-be-done scorecard — an architect's view of Garh AI

Scored by fifteen independent readers on 2026-09-06 against the tree at that time, each
reading the code, the tests and the ledgers for one job and asked for the score a
professional architect would give: 10/10 means the minutest details are taken care of,
0/10 means nothing is done. Scores are the readers'; the "since the reading" notes are
what changed on 2026-09-06/07 and are honest about what is still open. The evidence
behind each score, including the gap lists this page abbreviates, is kept with the
session's audit output; the tests named here are in the tree.

| Job                                   | Score | Verdict                                                                                                                                      |
| ------------------------------------- | ----: | -------------------------------------------------------------------------------------------------------------------------------------------- |
| J01 Sign in and set up the practice   |   5.5 | Sign-up → OTP → session → sign-out is executed, hardened and green (280 API tests, CI browser smoke, one live Brevo sign-up on the deployed  |
| J02 Capture the plot                  |     6 | Rectangular-plot main path (quick-start → roads → city pack → compliance re-check → DXF round trip) is executed in a real browser and backed |
| J03 Capture the client brief          |     5 | The main path — typed form or pasted text → assumption chips → one undoable brief                                                            |
| J04 Generate compliant plan options   |   5.5 | The CP-SAT pipeline is real, executed and gated (129 solver tests + a real solve of the demo brief pass locally; 4 library plans fold and ar |
| J05 Edit the plan in 2D               |   6.5 | The core loop (typed-length ortho walls, doors/windows with swing, stairs, room typing, dimension click-to-edit, one-group undo/redo, live b |
| J06 See and tune the building in 3D   |   5.5 | The extrude-select-dress-scrub loop is real and was proven once in a browser (2026-08-26) with 435 unit tests green today, but opening holes |
| J07 Check compliance continuously     |   6.5 | The engine and live re-check are real and executed (1,240 engine tests, 238/238 fixtures, browser-proven strip); but "Fix it" is never wired |
| J08 Produce the municipal drawing set |     5 | The pipeline genuinely runs end to end (42 golden sheets diff-clean, 231 chains sum, DXF audits clean, a 42-page vector PDF produced here),  |
| J09 Produce client renders and mood   |     5 | The mock render pipeline, seed determinism, gallery/stale flag, client pack (server side) and the inspiration board are executed and green,  |
| J10 Collaborate with client and team  |     5 | Client share link is real and proven in a browser (create → anonymous view → comment → revoke), but team collaboration is unreachable (no in |
| J11 Edit by natural language          |     5 | The containment pipeline (schema gate → real fold on a fork → rules diff → human apply, one undo group) is executed and green on every layer |
| J12 Estimate fees, areas and costs    |   4.5 | A well-tested GET /projects/:id/estimate (envelope, binding caps, 4 cost tiers, 3 CoA-anchored fee bands, whole rupees, seed-disclaimed) and |
| J13 Manage plan, credits and billing  |     5 | The API half is thorough and executed green (186 pytest + 16 vitest run here: plans, quotas, refunds, spend cap, GST invoices, mock checkout |
| J14 Trust and operations              |     6 | The security core (tenancy 404s, RS256 OTP auth, headers/CSP, body caps, fail-closed auth limits, PII-scrubbed Sentry) is executed and green |
| J15 Interoperate and hand off         |   4.5 | The worker-side exporters are real and executed (audit-clean DXF, 42-page vector PDF, valid GLB), but the two things an architect would actu |

The user's bar for this pass: every job scored above 5 and at most 8 goes to 10/10.
That set is J01, J02, J04, J05, J06, J07 and J14. J08 and J15 sit at or below 5 but
are the drawings an architect's fee is earned on, so they are in the same wave.

## J01 — Sign in and set up the practice

**Score 5.5.** Sign-up → OTP → session → sign-out is executed, hardened and green (280 API tests, CI browser smoke, one live Brevo sign-up on the deployed stack), but "set up the practice" stops at a firm name: no invite/team/roles/account-settings UI or endpoints exist, seats and 2FA/devices are backend-only, and the login form collects a mobile number it silently discards.

Blocking gaps the reader found:

- Invite a colleague into the firm (team members) — There is no endpoint or UI to add a second user to a firm; AuthService.signup is the only User constructor and always creates a new firm
- Roles and permissions management — UserRepository.set_role/remove exist but no route calls them; there is no way to promote/demote or remove a member, and no UI to see roles
- Firm and account settings page — Signup copy promises CoA can be added 'later in firm settings'; no settings route exists
- Mobile number is collected and discarded — LoginPage renders a +91 PhoneInput with the hint 'used to send drawings to clients on WhatsApp' but the value is never sent to requestOtp or signUp
- Seats API unreachable from the product — GET/POST/DELETE /billing/seats work but no web code calls them and no user other than the founder can exist, so seat assignment is dead UI-wise
- 2FA and signed-in devices have no UI and the login page cannot complete a 2FA challenge — Backend F-3/F-4 are tested, but the web app never calls /sessions or /2fa and LoginPage drops a two_factor_required response into the generic ProblemPanel
- Login page error branches disagree with the server contract — Server emits only otp_invalid (errors.py:85) for expired/wrong/exhausted; LoginPage branches on otp_expired/otp_mismatch (dead) and counts 'tries left' locally, so an expired code shows '4 tries left' and a page reload r
- No unit tests for LoginPage or the session store — The countdown, resend enable/disable, signup→409→sign-in redirect, dev-code box and signOut(everywhere) paths are untested outside the single @smoke login

What no test looks at: No test asserts that the login form's mobile value reaches any request (it does not) — a field that is rendered, validated and dropped is invisible to every suite.; No test exercises a 'member'-role human calling an AdminDep route through the real sign-in flow; every real session minted by tests is the firm's founding admin because no other user can be created, so the role gate is only tested with fabricated TenantCtx objects.; No test checks that the copy on the signup screen ('invite the rest of the studio', 'add it later in firm settings') corresponds to a route that exists.

Since the reading: A reload that raced a token refresh no longer signs the architect out (30-second reuse leeway on refresh rotation, family still revoked on real theft). Team invites, roles, practice and account settings are in the build wave.

## J02 — Capture the plot: draw/type the boundary

**Score 6.** Rectangular-plot main path (quick-start → roads → city pack → compliance re-check → DXF round trip) is executed in a real browser and backed by green pure-logic tests, but irregular plots can only be approximated by dragging midpoints, the underlay is PNG/JPEG-only and lives on the Plan canvas rather than the plot editor, LINE-segment survey DXFs import nothing, and every direct-manipulation surface (vertex drag, edge typing, compass, road toggles) has never run in a browser test.

Blocking gaps the reader found:

- No way to enter an irregular plot from a sale deed (four sides + diagonal, or bearings) — The editor offers only a rectangle quick-start, midpoint '+' corners and 115/25 mm-snapped dragging; typing an edge length uses a 'stretch' that silently moves neighbouring edges on a non-rectilinear ring (geometry.ts:13
- Survey DXFs made of LINE/ARC entities import nothing — dxf_import.py:280-296 harvests only closed LWPOLYLINE/POLYLINE and `continue`s on everything else without incrementing `skipped.unsupported`, so a LINE-drawn boundary (the common Total-Station export) fails with 'no clos
- Underlay tracing is unavailable in the plot editor and rejects PDF — The underlay (PNG/JPEG only, UnderlayPanel.tsx:93, routers/underlay.py) renders on the Plan tab's R3F canvas; the SVG PlotEditor has no image layer, so a scanned survey or a PDF site plan cannot be traced into the bounda
- Direct-manipulation surfaces never executed in a browser — Vertex drag/snap, arrow nudge, Delete corner, click-to-type edge length, '+' add-corner, compass drag/typed bearing and road checkbox/width/name have no component test and no e2e step (happy-path.spec.ts:59-63 is still t
- Only one side-setback value; rear edge is guessed as 'opposite' on even-edge rings — REG_VALUE_KEYS has a single setbackSideMm (rules.ts:221-229) while bye-laws and the engine distinguish side-a/side-b; compliance.py:257-260 assigns 'rear' as front+n/2, which for a 6-edge L-plot picks an arbitrary edge
- No deed-area reconciliation or diagonal/perimeter readout in the editor — Architects check drawn area against the registered deed area (sq ft / gaj / sq m) and quote diagonals
- Editing the plot after a plan exists gives no warning — plot.set_boundary/set_road change setbacks and the front edge but never touch house geometry or tell the architect the applied plan is now out of its envelope; the only signal is the compliance strip
- PUT /plot drops road names and mirrors reg_profile raw — routers/projects.py:422-429 builds plot.set_road without `name`, and :461-469 upserts `reg_profile=body.reg_profile` verbatim while the op used the normalised {cityPack, overrides} shape, so the plots table can disagree

What no test looks at: Whether the SVG vertex handles, edge-label <text role=button> and '+' handles actually receive pointer/keyboard events in a real browser (the furniture-layer class of bug: code tags itself interactive but nothing proves the hit-test).; Edge-length 'stretch' on a non-rectilinear ring: plot.test.ts covers a rectangle and an L-shape only; a trapezoid silently changes the adjacent edges and no test states what the architect will see.; DXF entities that are not closed polylines: LINE/ARC/SPLINE/INSERT hit `continue` without being counted, so the skipped-summary the dialog shows can read 'nothing sk

## J03 — Capture the client brief

**Score 5.** The main path — typed form or pasted text → assumption chips → one undoable brief.update op — is real, unit-tested (35 vitest + 115 llm + 65 api file-only tests green today) and browser-proven in the CI smoke spec, but half the form's controls (per-room floor/facing/adjacency/bath choice) are written under names nothing downstream reads, template/seed briefs are written under names the form cannot read, a parse's assumed values overwrite what the architect already typed, the form cannot express a bath count or a basement, there are no feasibility hints at all, and the real Anthropic parser has never executed.

Blocking gaps the reader found:

- Per-room preferences (floor, facing, next-to, attached/common bath) never reach the solver — RoomPrefsEditor writes rooms[].floor/facing/adjacentTo and the bedroom row writes rooms[].bath; solver_enqueue.\_room_requests forwards only rooms[].storey, the solver program reads mustFace and a top-level adjacency[{a,b
- Form has no bath count and no basement toggle; a form-only brief generates a house with no bathrooms — OPTIONAL_ROOM_TYPES has no bath_wc/wc; the completeness 'baths' item is satisfied by a per-bedroom attached/common choice that produces no bath room; \_synthesise_support_rooms adds only staircases and passages
- Template/seed brief vocabulary is unreadable by the form (floorsAboveGround, carParking, styleId, poojaRoom) — Projects created from the ready-made plan library or the demo seed open the Brief tab with Floors 'Not decided', Car parking 'Not set', no style, and completeness that ignores them, while the solver reads the other spell
- A parse overwrites the architect's stated values with the parser's assumptions and wipes per-bedroom prefs — FreeTextParse never sends knownFields, so the parser re-assumes storeys/parking/vastu/family; apply() prunes only keys equal to the current brief, so an assumed storeys:2 overwrites a typed storeys:3, and the rooms array
- briefs projection table is stale for browser-edited briefs, and the Plan tab's briefReady gate reads it — Web brief edits dispatch brief.update via POST /ops; routers/ops.py never mirrors into `briefs`; GET /projects/:id serves BriefRepository; stores/project.ts derives progress.briefCompleteness from it and OptionsPanel hid
- No feasibility hints: programme and budget are never checked against the plot envelope before Generate — An architect entering 4BHK G+2 with a ₹40 L budget on a 20×30 plot gets no warning until the solver fails
- Real Anthropic brief parser has never executed and has no tests — services/llm/anthropic_provider.py has no unit test under services/llm/tests and the ledger's settling command (pytest -m anthropic_live) matches no marker
- Integration tests for PUT /brief and /brief/parse cannot run in the current tree — Untracked routers/admin.py imports garh_api.schemas.common which does not exist, so every API integration test errors at collection (5 brief endpoint tests)

What no test looks at: Whether a field the form WRITES is READ by anything downstream — test_brief_aliases.py gates only the reverse direction (API reads ⊆ web writes) at top level and explicitly excludes rooms/adjacency, so rooms[].floor/facing/adjacentTo/bath and adjacencies[] are silently dead (bug class 4).; Whether a field a template/seed writes is one the form can read (floorsAboveGround, carParking, styleId, poojaRoom) — no test opens a library plan's brief through readBriefData.; Whether the briefs projection table agrees with the folded document after web (POST /ops) edits — test_brief_vastu_mode checks PUT

## J04 — Generate compliant plan options

**Score 5.5.** The CP-SAT pipeline is real, executed and gated (129 solver tests + a real solve of the demo brief pass locally; 4 library plans fold and are walkable), but coverage is partial (18/60 configs, no NCR plan, no parking placement, rect/L/T plots only), the zero-options screen swallows the diagnosis the worker sends, rationale is raw fact chips, the Playwright Generate journey is still skipped, and the API-side refund/library tests could not be confirmed on this working tree.

Blocking gaps the reader found:

- Zero-options screen hides the worker's diagnosis and the ready-made fallback — pipeline.finalise puts the stage-A shortfall sentence in result.banner and the API row carries it, but OptionsPanel only renders `banner` when options.length>0; the succeededEmpty EmptyState shows 'The plot, setbacks and
- Coverage: 18/60 stage-A configs, zero NCR plans, 2BHK on small plots infeasible — Per trial-readiness the offline matrix is 42/60; my sample confirms 20x30 2BHK G+1 fails
- No car-parking placement; parking compliance passes on a declaration — An Indian residential plan on 30x40 needs an ECS/porch on the ground floor (blr.parking.\*, ncr.parking.ecs)
- Rationale is machine tokens, not prose — 'Why this plan' shows chips like composite:73 / zone:kitchen@SE / stairAnchor:se-1
- Browser Generate → options → apply journey has no executing e2e spec — e2e/tests/happy-path.spec.ts:87 is test.skip with a Phase-3-era reason; the live proof is API-level (scripts/first_run_journey.py)
- Irregular plots are refused (rect/L/T only) — grid.py raises UNSUPPORTED_SHAPE for any buildable mask needing >3 rectangles; trapezoidal/skewed survey plots are common in India
- Gate semantics are documented wrong and the tab/gate projection can still disagree — gates.check_option blocks on any status=='fail' (108 rules), not on `hard` (1 rule) as README/trial-readiness state; the real ventilation disagreement was a projection difference (fixed for windows)
- No executed time-budget or determinism test for the production profile — §14 '3 options ≤60 s' has no timing assertion; production profile (8 workers, wall clock) is admittedly non-deterministic (seed script tries 3 seeds)

What no test looks at: Whether the OptionsPanel renders the shortfall banner / ready-made link in the succeeded-with-zero-options state (no OptionsPanel component test at all; stats.test only checks bannerFor for 1–2 options); Whether generated plans place any car parking or a porch — no rule measures a parking space, the rule passes on a brief declaration; Wall-clock of a production-profile solve (≤60 s) and run-to-run stability of the option set under 8 workers

Since the reading: Generate retries up to three fresh seeds before answering "no plan cleared" and a run that delivers nothing is refunded; the Options screen now renders every delivered plan (the vastu rule rows had been sinking each one) and shows the solver's own banner. Diagnosis on the zero-option screen, prose rationale, seed controls and parking-as-geometry remain open.

## J05 — Edit the plan in 2D

**Score 6.5.** The core loop (typed-length ortho walls, doors/windows with swing, stairs, room typing, dimension click-to-edit, one-group undo/redo, live bye-law chip, endpoint/marquee select, layers with lock, storeys add/copy, measure, saved views, constraints) is real, unit-tested (2,052 green tests run here) and was proven once in a real browser (plan-canvas.spec, 2026-08-26) — but that spec is not in CI, the G+2 frame budget has never been measured, and daily-CAD table stakes are missing: wall split, copy/paste/mirror/array from the canvas, column placement, storey removal, touch, and trackpad two-finger pan.

Blocking gaps the reader found:

- Put the @canvas DoD spec and a real G+2 frame-budget measurement into CI — plan-canvas.spec has run green exactly once by hand; CI runs only @smoke, which never draws a wall (smoke.spec.ts:4 admits the last three steps of §16 are not there)
- Wall split from the canvas — wall.split exists in ops.ts:940 with fold and inverse but no tool or inspector action emits it
- Copy / paste / array / mirror surfaced on the canvas — packages/model/src/transform.ts (and the Python twin) implement all four with duplicate guards and a work cap, but nothing in apps/web imports them: no ⌘C/⌘V/⌘D bindings, no mirror axis picker, no array dialog
- Trackpad two-finger pan and touch/stylus input — useCanvasControls.ts:319-326 treats every wheel event as zoom (deltaY only), so a MacBook two-finger scroll zooms instead of panning and pinch is indistinguishable from scroll; there is no pointerType/touch-action handli
- Column placement and storey removal from the UI — column.set is emitted only as 'delete' (editOps.ts:457) and by storey copy; there is no column tool, so solver-placed columns cannot be added or moved by hand
- Dogleg / L / U stairs drawn as their real geometry — planGeometry.ts:368 draws every stair kind as one straight run because the model stores origin+direction+landing only
- Dimension skew walls and add perpendicular/intersection snaps — Only axis-aligned walls get dimension chains (chain.ts reports skewWallIds instead); Shift-inverted ortho lets an architect draw a skew wall that then carries no dimension
- Hatches visible on the 2D canvas — HatchBindingPanel binds material→pattern for the drawings service, but PlanScene renders walls as flat fills; the architect cannot see what the sheet will hatch

What no test looks at: Pixels: plan-canvas.spec asserts op log, folded model and compliance only (phase-4-verification.md §5) — a renderer that drew nothing, mis-scaled openings, or put walls on the wrong storey's elevation would pass every test in this job.; Frame time on a G+2: both canvas perf budgets are test.skip(true); no unit or e2e test bounds pan/zoom/drag cost with 3 storeys of geometry, room washes, dimension chains and troika labels mounted.; Trackpad and touch semantics: no test dispatches a WheelEvent with deltaX or ctrlKey, or a pointerType='touch' event; useCanvasControls' wheel path is exercised onl

## J06 — See and tune the building in 3D

**Score 5.5.** The extrude-select-dress-scrub loop is real and was proven once in a browser (2026-08-26) with 435 unit tests green today, but opening holes have never been asserted anywhere, the 3D e2e is outside CI and the 3D code has changed since, facades are unlit/no-shadow boxes with flat colours, and 3D export does not exist.

Blocking gaps the reader found:

- Prove opening holes actually render, then assert it — Manifold cutting has never been asserted in an executed test: solids.test uses a null cutter, three-d.spec only annotates the engine state, and no ledger records holes=true
- Put the 3D e2e (and a minted visual baseline) into CI — three-d.spec (@canvas) runs only by hand and last ran 2026-08-26; the 3D/canvas tree changed on 08-27..29
- 3D export (glTF/GLB, optionally OBJ) of building + facade — Nothing exports the 3D model
- Facade components must be lit and cast/receive shadows — FacadeLayer uses unlit MeshBasicMaterial with baked shading; chajjas, porches and cladding cast no shadow and ignore the sun scrub, so the sun study lies for exactly the elements meant to shade windows
- Terrace slab + parapet over set-back lower storeys — roofSolids only roofs the top storey's slab; a G+1 with a smaller first floor leaves the ground floor's exposed portion open to the sky with no parapet — the most common Indian house massing
- Make openings visible in the no-WASM fallback — The 40 mm opening panel is centred in the wall, so without holes it is buried inside the wall solid and openings vanish
- Real materials: textures/PBR, per-element assignment UI, more than 2 facade kits — Everything is a flat hex colour (textureUrl ignored); element-scoped material assignment exists in the op and resolver but has no UI; only two kits
- Walk mode: collision, look-up, and a browser test — Walk passes through walls, cannot pitch above level (MAX_ORBIT_POLAR_DEG clamp), and has never run in a browser

What no test looks at: Pixels: no executed test looks at what the 3D view draws — walls could render magenta, holes could be absent, shadows could point the wrong way, and every test still passes (visual-regression.spec is skipped).; Whether Manifold's cut mesh is geometrically right: triangle count, watertightness, or that a ray through a door misses the wall — only the holesApplied boolean is checked, and only with a null cutter.; Which side of an external wall is 'outside' for chajjas/porches when the storey outline is concave or the centroid falls outside (componentBoxes.ts centroid rule is documented for rectan

## J07 — Check compliance continuously

**Score 6.5.** The engine and live re-check are real and executed (1,240 engine tests, 238/238 fixtures, browser-proven strip); but "Fix it" is never wired, rule-acknowledgement overrides have no UI, the tab hides actual/limit/override/area/Vastu detail, all 118 values are unreviewed seeds, and the report still cannot see doors, shafts or real parking.

Blocking gaps the reader found:

- Wire 'Fix it' end to end or remove the affordance — ComplianceStrip/ComplianceChip accept onApplyFix/onFix but ProjectShell never passes one, and no client code builds an op group from autofix {opType, strategy}
- Override a failing rule with a reason from the Compliance tab — The engine already supports {ruleId:{reason}} acknowledgements (overridden=true, excluded from blocking_failures, kept in failures()), and the tab's header promises 'the override is logged, not prevented', but there is n
- Show the numbers an architect needs on the tab: actual vs limit, original limit, per-element instances, severity/hard, area statement, Vastu score, engine warnings/notes, pack versions — toComplianceIssue drops actual/limit/originalLimit/overridden/hard/severity/instances; the tab never renders report.areas, scores, warnings (e.g
- Put door reachability (and other model-level gates) into the compliance report, not only the solver gate — garh_model.circulation.reachability_problems runs for solver candidates and library plans only
- Surface a failed re-check instead of silently showing stale chips — useLiveCompliance exposes `error` but ProjectShell discards it; after a 5xx the strip keeps the previous chips with no 'last check failed' indicator, so a green strip may describe a state several edits old
- Reconcile 'hard' semantics between packs, solver gate and docs (open task #46) — Only vastu.toilet.never_ne carries hard:true; the solver gate in gates.py blocks on every status=='fail' row while CLAUDE.md, trial-readiness and program.py describe a hard-only gate, and the plan-library gate uses statu
- Empanelled review of all 118 seed values (launch gate, not code) — 0 of 118 values reviewed; every pack is review.status=unreviewed
- Replace declaration-based checks with geometry: parking spaces, RWH, shaft access, service elements — parking_min passes on brief.carParking (an integer the architect types) while the message implies spaces are shown; rwh_required reads a brief flag; hasShaftAccess is hard-coded False so internal wet-area ventilation via

What no test looks at: Door reachability / dead-end rooms in GET /compliance for hand-drawn, imported or edited plans (garh_model.circulation is a solver-only gate; no rule row, no fixture through evaluate_document); Whether a chip's elementIds actually resolve to the geometry the architect sees — no browser test clicks a chip and confirms the framed element is the offender; A re-check that fails after a green one — nothing asserts the strip signals staleness (ProjectShell drops `error`)

Since the reading: Unchanged in code today; the build wave is wiring Fix-it, overrides with reasons, the numbers on the tab and a surfaced failed re-check.

## J08 — Produce the municipal drawing set

**Score 5.** The pipeline genuinely runs end to end (42 golden sheets diff-clean, 231 chains sum, DXF audits clean, a 42-page vector PDF produced here), but what it draws is a submission skeleton, not a submission set: the section is an envelope box with no cut walls, stair or room names, plans carry no D/W tags although the schedule assigns them, the site plan dimensions only setbacks, and the richly-tested sections/elevations/autodim/projection/blocks packages are never imported by the shipped renderer.

Blocking gaps the reader found:

- Section A-A is an envelope box, not a section — The shipped section_primitives draws plinth, envelope, slab lines, sill/lintel lines and a height chain
- Door/window tags absent on plans while the schedule assigns them — A-05 lists D1–D5/W1–W2/V1 with per-storey counts, but every floor plan prints zero tags: reference_sheets prints opening.tag only when the ProjectDoc opening carries one, and no writer (solver, schedule generator, pipeli
- Site plan lacks plot boundary and footprint dimensions — Only the four setback chains are dimensioned; the plot edges (the 30 x 40 ft an Indian sanction plan quotes), the building footprint, and the road width are not chains
- Elevations are flat envelopes — Each elevation is one envelope polygon, floor lines, opening rectangles and level markers (27 lines, 6 polygons, 8 KB)
- Room labels omit dimensions and m² — Plans print NAME + 'xx.x sq ft'
- Per-sheet PDF never produced from the UI — SheetsTab generates with {} and the API/handler default formats are ('svg','dxf'), so the viewer's per-sheet PDF button is always disabled
- Two renderers: tested packages that ship nowhere — autodim/, projection/, elevations/, sections/, blocks/ and sheets/builder|compose (≈250 tests) are a parallel implementation the live pipeline never imports; reference_sheets.py (2,403 lines) re-implements outer/inner ch
- Hand-checked dimension reference set is empty (launch gate) — F7-A requires ≥90% of dimensions accepted unedited against sheets an architect has read

What no test looks at: Whether a tag printed in the schedule appears anywhere on a plan sheet (today: never) — the class-4 'module believes it is registered' bug, at data level.; Whether the section actually shows anything the cut line passes through (walls, stair, rooms); tests assert the cut line position and the height chain, not the presence of A-STAIR/A-WALL cut geometry.; Whether the shipped renderer (reference_sheets) and the tested packages (sections/, elevations/, autodim/, projection/) produce the same drawing — nobody folds one plan through both and diffs.

Since the reading: "Generate the set" no longer refuses a project that never saved a version: sheets and exports pin to a checkpoint minted at the head, so the set is of the design on the screen; the Sheets tab now listens on the stream the API serves, so the drawn set appears. Drawing content landed (merged 2026-09-09): every opening on the plans carries the schedule's own tag; rooms read name, dimensions and area in m² and sq ft; the site plan chains every plot edge, the footprint, the road width and the four setbacks under the rules engine's own role names, with the sheet refused if a chain disagrees with the frozen report by a millimetre; Section A-A is a real cut (hatched walls, slabs, stair flight, plinth, parapet, ten level markers) and the four elevations show the house with every external opening at its sill and lintel. 991 services tests, 42 goldens diff-clean, 0 label collisions. Still open: per-sheet PDF as a default format, DXF paper-space layouts, scale bar and key plan, the dogleg's return flight, and the hand-checked dimension reference set.

## J09 — Produce client renders and mood

**Score 5.** The mock render pipeline, seed determinism, gallery/stale flag, client pack (server side) and the inspiration board are executed and green, but no real AI render has ever been produced, 16 of the 23 offered presets (all elevations) are wired to neither the named facade nor the project's north, the architect has no prompt text field, and the browser-side client pack and @renders e2e have never run.

Blocking gaps the reader found:

- Elevation presets photograph the wrong face and ignore project north — All 16 elevation-\* presets are offered in the launcher but cameras.ts routes every non-street exterior to the same SE three-quarter station point, and handler.py never passes plot.northDeg into RenderRequest.north_deg, s
- No real AI render has ever been produced — Stability and diffusers adapters exist; only the mock (watermarked 'GARH AI · MOCK RENDER') has run
- Architect cannot describe materials or finishes for a render — prompt_extras is accepted by the API and sanitised in prompts.py but the launcher has no text field and features/renders/api.ts drops it
- Client pack from a real browser never proven; likely fails without minio CORS — e2e pack test is test.skip(true)
- @renders Playwright DoD walk is not in CI and has no execution record — CI greps @smoke only; the capture→render→stale-banner spec has never been recorded as run
- Shared links die after 10 minutes — WhatsApp share and the pack-zip share send a presigned S3 URL with the configured TTL; a client opening it later gets an S3 'Request has expired' page
- Client pack bypasses the render count allowance — POST /renders/client-pack has require_spend_budget only; the free plan's render allowance is 5 but a pack enqueues 8 with no count check, while the single route is gated
- Gallery lacks full-size view, real download, delete and captions — <a download> on a cross-origin URL navigates instead of saving and gives no filename; no lightbox, no delete/hide of a failed or unwanted render, no caption/note per image, no 'compare with previous version' for a stale

What no test looks at: Whether an elevation preset's camera actually faces the named facade: test_orientation.py proves camera_azimuth_deg in isolation, nothing calls it from cameras.ts, and no test folds a preset through presetCamera and checks the eye is north of the building (bug class #4: a module that believes it is registered).; Whether the project's plot.northDeg ever reaches RenderRequest.north_deg — the handler constructs the request without it and every test that touches orientation passes north_deg by hand (bug class #1: a gate that silently never fires).; Whether the mock render's image contains the buil

## J10 — Collaborate with client and team: share links

**Score 5.** Client share link is real and proven in a browser (create → anonymous view → comment → revoke), but team collaboration is unreachable (no invite/member route, one user per firm), resolved comments vanish with no reopen/list, there is no UI to save or restore a version, the viewer silently drops 3D/compliance grants, and there is no notification of any kind.

Blocking gaps the reader found:

- Team collaboration is unreachable: no invite/add-member route — Add POST /firms/members (admin) that calls UserRepository.create + seat assignment, an invite email via the existing mailer, and a Team page in the web; add a negative test that a member of firm B still 404s on firm A
- Resolved comments vanish; no reopen or 'show resolved' list — Add ?resolved=all|open filter to GET /projects/:id/comments (repo already has set_resolved(False)); UI toggle 'Show resolved' and a Reopen button; negative test: resolving then listing with the filter returns the row.
- Share viewer ignores 3D and compliance grants; client cannot see prior comments or replies — ShareDialog offers five sections but ShareViewerPage renders three
- No UI to save a named version or restore one — API has list/create/restore (tested)
- Architect cannot manage existing share links after reload — ProjectShell holds one link in state; api.share.list is never called
- No notifications for client feedback or colleague changes — Add an email digest / immediate mail to the project's architect on share-link comment creation (mailer exists), and an in-app unread indicator persisted per user (last_seen_at on comments)
- Comments are flat: no reply threads, no @mention, no attachment — An Indian residential client round usually needs 'reply to this pin' and marking a photo
- Order-dependent failure in test_version_compare — test_comparing_a_version_with_itself_reports_nothing returned 404 'Project not found' from GET /versions/compare when run after test_collab suites, passed alone and on rerun

What no test looks at: Whether a second real browser ever renders a colleague's cursor or presence chip — tests assert frame shapes and Redis keys, never pixels; and no two humans can currently be in one firm to try it; Whether a pinned comment lands on the correct storey after a storey is inserted or reordered (anchor uses storey id — traced, but no test folds a storey insert and re-projects pins); Whether a share link with sections=['three_d'] or ['compliance'] shows the client anything (it shows an empty-state; no test asserts the dialog's choices match the viewer's tabs)

## J11 — Edit by natural language

**Score 5.** The containment pipeline (schema gate → real fold on a fork → rules diff → human apply, one undo group) is executed and green on every layer against the MOCK provider, but the thing the job is actually for — a model understanding an architect's words — has never run: the Anthropic adapter has zero tests, streaming and multi-turn history exist only inside services/llm and never reach the route or the panel, and the mock's keyword matcher hands back the fixture's number for any paraphrase ("make the middle wall 150 thick" → a 230 mm proposal).

Blocking gaps the reader found:

- Real Anthropic provider never executed and has zero tests — services/llm/anthropic_provider.py (410 lines: output_config json_schema, \_strip_unsupported, parameter-drop-and-retry, parsed_output/stop_reason handling) has no unit test with an SDK double and has never been called wi
- Mock provider answers paraphrases with the fixture's numbers — \_best_overlap accepts any command sharing ≥50% of a fixture's keywords, so 'make the middle wall 150 thick' proposes 230 mm and 'widen the main door to 900' proposes 1200 mm — a confident wrong edit on the trial stack (w
- Streaming never reaches the route or the panel — propose_stream / guarded_stream / StageEvent are built and tested (27 tests) but routers/copilot.py calls the blocking propose() and the web client is a single 60 s POST; the panel shows a static skeleton
- No multi-turn context: 'now do the same on the first floor' is unanswerable — ConversationContext (redacted at construction, swept at render, 20 tests) is never passed by the route
- Corpus has no Indian-vocabulary, Hinglish or feet-inches rows — All 40 commands are English; nothing exercises 'deewar', 'pooja room', 'wash area', 'utility', '12\'6"', 'two hundred thirty'
- Client abort still spends provider budget and meters a credit — Client deadline is 60 s; server llm_timeout_seconds=60 with SDK max_retries=2 can run ~180 s
- @copilot e2e spec is not in CI — ci.yml's e2e job runs test:smoke only; the copilot DoD walk is executed by hand per the ledger
- Rules gate blocks only hard failures; a copilot edit that introduces a new warn passes silently — \_hard_failures keeps only status=='fail'

What no test looks at: Whether the mock's fuzzy match returns the WRONG NUMBER for a near-miss command — only the unrelated-text → cannotDo fallback is asserted (test_unknown_command_gets_the_honest_default); the executed probe shows 150→230 and 900→1200 pass every gate green; Whether the Anthropic request the code builds is accepted at all: the schema after \_strip_unsupported, the output_config.format shape, the guessed response attributes (parsed_output, stop_details.category) — nothing executes that code; Whether a proposal introduces a NEW warn-level rule regression — the gate reads status=='fail' only, so a cop

## J12 — Estimate fees, areas and costs

**Score 4.5.** A well-tested GET /projects/:id/estimate (envelope, binding caps, 4 cost tiers, 3 CoA-anchored fee bands, whole rupees, seed-disclaimed) and a proven municipal A-06 area statement exist on the backend, but no web screen calls the estimator, rates are one national table (not per city, not firm-editable), there is no BOQ/take-off, no estimate export, the compliance tab shows no FAR/coverage readout, and a reviewer-flagged mislabel (coverage-bound plots reported as envelope-bound) is still live.

Blocking gaps the reader found:

- Estimate has no screen: nothing in apps/web calls GET /projects/:id/estimate — The route, schema and 21 tests exist, but an architect cannot see the buildable envelope, cost band or fee band in the product
- builtUpBinding mislabelled 'envelope' on coverage-bound plots (reviewer finding from f6ecd50, still live) — stacked_mm2 = max_ground_floor_area_mm2 × max_storeys is always labelled "envelope" even when max_ground_floor_area_mm2 came from coverage
- Cost per sq ft is one national seed table — no per-city rates, no firm-editable rate library, no persistence — CONSTRUCTION_RATES is 4 tiers regardless of city_pack (probed: blr/hyd/ncr give identical bands)
- No BOQ / quantity take-off — Nothing derives brick, concrete, steel-indicative, plaster, flooring, openings or paint quantities from the model even though walls carry thickness, rooms carry polygons and openings carry sizes
- No export of the estimate (PDF/xlsx/CSV) and no client-facing fee proposal — An architect sends a fee proposal and a budget range to the client; today the estimate lives only in JSON
- Compliance tab shows no live area statement (FAR consumed vs allowed, coverage %, per-storey built-up, carpet) — report.areas is computed on every GET /compliance but CompliancePage renders rule rows only; the numbers surface only on the A-06 sheet and in the version ComparePanel
- Client-side pack resolver (plot tab) has no parity test against the Python engine — apps/web/src/features/plot/rules.ts re-implements predicate evaluation and band selection for FAR/coverage/setbacks and shows those numbers in RegProfilePanel
- Carpet area is derived in the drawings service, not carried by the rules helper or the TS model twin's compliance path — area_statement.py docstring itself names the long-term home as StoreyAreaRow.carpet_area_mm2

What no test looks at: builtUpBinding on a coverage-bound plot: test_both_built_up_caps_actually_bind_on_real_plots asserts only the envelope-bound 30x40 plot, so the live mislabel (probe: 30x30 m → coverage ground floor, 'envelope' built-up) is invisible to the suite — a check that cannot go red.; Whether any screen shows the estimate: no Playwright or vitest test references the estimate route, and none can, because no component calls it — the endpoint's 'disclaimer the UI is expected to render' is rendered nowhere.; Parity between apps/web/src/features/plot/rules.ts (TS band resolution shown in RegProfilePanel) an

## J13 — Manage plan, credits and billing

**Score 5.** The API half is thorough and executed green (186 pytest + 16 vitest run here: plans, quotas, refunds, spend cap, GST invoices, mock checkout, seats, owner markup), but an architect can only SEE usage — there is no UI to view plans, upgrade, enter GST details, see or pay an invoice, or manage seats; Razorpay has never run live; the `billing_live` flag is never read; and solver/export events are priced at 0 by their own call sites despite the ledger claiming otherwise.

Blocking gaps the reader found:

- No architect-facing billing UI: plans, upgrade, GST details, invoices, checkout, seats — The API exposes 15 routes under /billing/\*\* and the web binds exactly one (GET /billing/usage)
- Out-of-credits 402 is a dead-end toast with 'Try again' — ProjectShell.handleGenerate maps every non-409 error to a 'Try again' action, so a quota_exceeded / spend_cap_exceeded 402 offers a retry that will 402 again
- Solver and export are silently priced at 0 in the ledger — jobs.py writes solver/export meta with no 'provider' key; cost_micros_for treats '' as a free provider, so FLAT_PRICES for solver (6,000 µUSD) and export (2,000 µUSD) never apply, and the spend cap cannot count CPU work
- Export quota is unmounted; free plan says 'no drawing exports' but exports are unlimited — plans.py sets free export allowance to 0 on purpose, but require_quota('export') is not mounted on POST /export or the render pack (UNGATED_ON_PURPOSE), so the product's stated pricing is not enforced
- Razorpay has never run live; billing_live flag is dead; env docs stale — The adapter is proven only against an httpx.MockTransport double
- No payment webhook: an invoice paid after the browser closes never settles — payments.py documents that no webhook is mounted because tenant resolution from provider metadata is unsolved
- No invoice document (PDF) and no credit notes — An Indian practice files the tax invoice; the API returns JSON only
- Seats are unusable without a firm invite — assign_seat needs a user that already exists in the firm; signup only ever creates a new firm with one admin, so the seat gate, extra-seat pricing and downgrade-fits logic cannot be reached by a practice

What no test looks at: A solver or export credit_events row written by the REAL route meta costing >0 — every pricing test hands cost_micros_for an explicit provider; the call sites omit it and price at 0 (confirmed by execution).; Whether the plan a firm pays for is the plan enforced on export: free export allowance 0 is asserted in the catalogue, never on the route (UNGATED_ON_PURPOSE makes that exemption permanently green).; Sheet generation cost: routers/sheets.py writes no credit event, so no quota, cap, refund or usage test can ever see it.

Since the reading: The platform fee exists end to end: 5% by default, read from platform_settings on every charge, changeable by the owner through the API with no redeploy, recorded per credit event and shown on the usage card. Merged 2026-09-09: the owner changes the fee from `/platform/fee` and sees who set it and when; money reads in rupees at one dated rate with the dollar source on hover and the fee split from provider cost; a 402 opens a Billing page with plan cards, allowances, the credit ledger, GST details, invoices, a mock checkout and seats; solver and export rows are priced from every real route (they were 0); the usage response says which allowances are enforced. Still open: the export quota is not mounted (the API says so), Razorpay has never run live and has no webhook route.

## J14 — Trust and operations: security checklist, tenancy isolation, secrets, rate limits, Sentry/monitoring, backups/restore, load test, health checks, migrations on boot, error/empty states, onboarding tour/demo, accessibility, responsiveness, user docs, CI health

**Score 6.** The security core (tenancy 404s, RS256 OTP auth, headers/CSP, body caps, fail-closed auth limits, PII-scrubbed Sentry) is executed and green locally and in CI, but operations are half-real: Sentry is not enabled on the deployed stack, no scheduled backup exists, the "load test" is a read-only /healthz smoke, Railway runs migrations+seed on boot against the doc that forbids it, CI is red at HEAD on a ruff-format miss, the onboarding tour is a store with no UI, 2FA/devices/DPDP have no web surface, and the trust docs are stale by a month.

Blocking gaps the reader found:

- CI is red at HEAD on a formatting miss — Runs 71–73 fail at lint 'ruff format --check' (apps/api/garh_api/models.py would be reformatted), so typecheck/unit/golden/e2e never ran for the last three commits including the platform-fee and seed-round changes
- Sentry/monitoring is built but OFF in production — No SENTRY_DSN on the Railway api or worker services; no queue-depth metric, no job-duration histograms (deployment.md 'Still to add'), no alerting
- No scheduled backup and no recorded restore rehearsal — scripts/backup_db.sh exists; Railway has no cron/backup service; the 'ran once for real' claim in CLAUDE.md has no ledger line, no timestamp, no output
- Load test does not exercise the product path — scripts/load_smoke.py hits only /healthz
- Railway runs migrations + seed on every api boot, against the documented rule — startCommand 'alembic upgrade head && python -m garh_api.seed && exec uvicorn … --workers 4' with 1 replica
- Data residency claim vs deployment region — deployment.md and spec §15 promise India data residency (Mumbai inference, S3 ap-south-1); the api and workers run in us-west2
- Onboarding tour is a store with no UI — stores/ui.ts holds tourStep/tourDone/startTour but nothing renders a tour; phases.md Phase 9 marks it ⬜
- 2FA, signed-in devices, audit trail and DPDP export/erasure have no web surface — All four are API-complete with 60+ executed tests, but api.ts has no client for them, so no architect can enrol 2FA, revoke a lost laptop, read the firm's audit trail or export/erase their data

What no test looks at: Whether the deployed stack matches the docs at all: no test reads the Railway start command, region, or variable set, so migrations-on-boot, us-west2 and SENTRY_DSN-absent drifted silently from deployment.md and the runbook.; Backup restore correctness: rehearse checks that four table names exist, not row counts, op-log integrity, or that a restored project folds to the same state hash.; Queue behaviour under real concurrency: every rate-limit test is single-process; nothing enqueues 50 solves and watches the lifecycle consumer, dead-letter path and credit refunds at once.

Since the reading: CI is green again after two formatting misses; the production connections were read from the deployed stack's boot lines and recorded (Anthropic, Stability and Brevo live; Razorpay and Sentry absent; APP_ENV=dev on the api).

## J15 — Interoperate and hand off: DXF import

**Score 4.5.** The worker-side exporters are real and executed (audit-clean DXF, 42-page vector PDF, valid GLB), but the two things an architect would actually do daily are broken or missing: the web UI never surfaces the finished set-export download (JobList is mounted without onOpenResult, per-sheet PDF is never requested), and every exported DXF drops paper-space groups (title block, schedules, area statement) into model-mm blocks unscaled — a 594x420 mm frame with 1.8 mm text sitting inside a 1:100 plan; DXF import reads only top-level closed polylines (no INSERT traversal, LINE loops rejected, arc bulges silently flattened) and there is no DWG, IFC or OBJ.

Blocking gaps the reader found:

- DXF export writes paper-space groups (title block, schedules, area statement, north arrow) unscaled into model-mm blocks — \_model_point is identity and \_write_group ignores DrawingGroup.placement, so a Placement.paper() group lands as 594x420 model-mm geometry with 1.8-5 mm text overlapping a 1:100 plan; A-05/A-06 sheets are entirely miniatu
- Finished set exports (PDF/DXF/glTF/PNG) have no download control in the web UI — SheetsTab mounts JobList without onOpenResult and nothing reads JobDTO.downloadUrl; the toast promises an automatic download that never happens
- Per-sheet PDF button is permanently disabled — The web never requests formats on generate and DEFAULT_SHEET_FORMATS is (svg, dxf), so sheet.artifacts.pdf never exists
- DXF import reads only top-level closed LWPOLYLINE/POLYLINE — Boundaries inside INSERT blocks, closed loops made of LINE/ARC entities (the common surveyor output), and the product's own exported DXF all return dxf_no_boundary
- Arc bulges are silently flattened on import — get_points('xy') drops the bulge, so a curved plot edge becomes a chord with no warning and the area changes
- Units are not overridable on import — The dialog shows an 'assumed mm' chip but the only remedy is 'fix in CAD and re-export'
- DXF layout is nine blocks side by side in modelspace with no paperspace layouts — An AutoCAD user expects one layout tab per sheet with a viewport at 1:100 and the title block in paperspace; a 20 m gutter of blocks in modelspace is a cleanup task
- No test re-reads a written DXF; PDF path not gated in CI — Goldens count primitives before writing and audit the file, so geometry placement, text heights and dimension text cannot regress visibly

What no test looks at: Geometry placement in the written DXF: goldens count entities per layer from primitives and audit validity, so a title block 100x too small inside the plan block passes every gate (found only by re-reading the file with ezdxf).; Whether a succeeded export job's downloadUrl ever reaches a clickable control — no web test mounts SheetsTab with a succeeded export job, and the happy-path e2e step is test.skip with expect(true).; Whether the DXF import handles what surveyors actually send: INSERT-wrapped boundaries, LINE/ARC loops, bulges — the four fixtures are all clean closed LWPOLYLINEs.

Since the reading: The DXF and PDF-set export paths now pin to the head version like sheets do. The browser UAT's download step found three faults in a row — the signed link dropped by the view-model, a Content-Disposition on the redirect that browsers discard, and a request body the server refused with a 422 on every click — each fixed with a test that would have caught it (`test_download_disposition.py`, `JobCard.test.tsx`, `api.exports.test.ts`).
