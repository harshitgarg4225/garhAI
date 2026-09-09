# Can we put a few architects on this? — measured 2026-09-01

Trial readiness is a different bar from production readiness, and this file only
answers the trial one: **would a handful of architects hit a wall in the first hour?**

Production readiness is a separate question with a separate answer (the rule-pack
values are all `confidence: "seed"` and need empanelled review before anyone submits a
drawing to a municipality). Nothing here changes that.

Everything below was executed against a live stack — Postgres 16, Redis, moto S3, the
API and all four workers — not read.

---

## The end-to-end journey: 0 failures

`scripts/first_run_journey.py`, a brand-new architect from signup to a drawing set:

```
PASS  sign up a new practice
PASS  sign in with the emailed code
PASS  create a project on the BBMP rule pack
PASS  draw the 30 x 40 ft plot with a 9 m road
PASS  paste the client's brief and have it parsed
PASS  the parser returns rooms, not prose
PASS  press Generate
PASS  the solver finishes
PASS  it offers plan options            — 2 options
PASS  apply the option the architect picked
PASS  the project has a model with walls — walls=21 rooms=13
PASS  compliance reports against the BBMP pack — 23 results
PASS  generate the municipal sheet set
PASS  sheets appear                     — 10 sheets
```

---

## Generation

|                                          |                                                                                                     |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Stage-A coverage, 60 configurations      | **24 → 42** after today's two fixes, zero regressions                                               |
| Whole briefs through the live API, gated | **4 of 6** produce options; circulation 14–18% (inside the §5.6 cap), composite 75–89 (floor is 55) |

Two defects fixed, both found by running the product:

1. **A zero setback made the plot unbuildable.** `_segments_properly_intersect`
   compared orientation signs without rejecting a zero determinant, so an endpoint
   lying exactly on the other segment's line counted as a crossing. A zero setback
   puts the envelope corner on the plot boundary, so the envelope "escaped" its own
   plot and the site was refused. Zero side setbacks are ordinary on small Indian
   plots. This killed every brief in the live matrix.
2. **The in-model circulation cap was stricter than the gate it mirrors.** Stage A
   capped circulation per storey at the §5.6 gate's 18%, but the gate measures the
   whole building. The staircase counts as circulation on every floor and does not
   shrink, so a small storey spends most of an 18% budget on the stair alone — and a
   sparse storey became infeasible. Measured: a 2BHK G+1 was INFEASIBLE at 18%, and
   the layout found at 25% uses 9.3% and 3.3%. The constraint was rejecting layouts
   that pass §5.6.

Two candidate fixes rejected on evidence rather than taste: raising
`MAX_FRACTION_OF_TARGET` to 3/1 makes the same cases feasible but degrades plans that
already worked (a 3BHK living room 30.4 → 50.4 m², a 2BHK living room of 58.3 m²);
and moving a bedroom downstairs does not help.

**Still failing:** 18 of 60 offline configurations. Twelve are the 20×30 ft rows the
sweep's own docstring says its fixed 1.5/1.5/1.0 m setbacks judge unfairly (production
derives smaller setbacks for small plots). The rest are large briefs on one floor.

## Generate no longer answers with a blank screen

A solve that produced nothing reported `succeeded`, `progress: 100`, zero options and
NO text. The reason existed the whole time — `shortfall_banner` builds it and the
worker returns it — and the API dropped it. Migration 0010 carries it through. The
same brief now answers:

> The rooms fit this floor by area (20.5 m² needed, 108.0 m² available), but no
> arrangement satisfied every constraint at once. Loosening one thing usually unlocks
> it: a must-face in the brief, a room's minimum width, or an adjacency you asked for.

## Rendering

`scripts/reference_journey.py` — 10/10 against a live render worker: pin a picture,
the product asks what it is for, annotate, the question disappears and the architect's
own words appear in the prompt, render, and read back the reference the finished image
followed **by name**.

Proven under `PROVIDER_RENDER=mock` only. Whether a real diffusion model follows an
architect's phrasing needs the Stability key and a human panel.

## Collaboration — read this before planning the trial

`scripts/collab_journey.py` — 10/10, and the result is not what the feature list
suggests.

**Two colleagues could not share a project (closed 2026-09-07, see below).** Until
then `AuthService.signup` was the only caller of `create_firm_with_owner`, the single
place a `User` row was constructed; `POST /billing/seats` assigned a seat to a user that
had to already exist; and there was no invite endpoint anywhere. Every signup created a
NEW firm with exactly one admin, and the tenancy layer then correctly hid every project
from everyone else.

So presence, live cursors, op streaming between people and in-project comments between
colleagues were all built, all firm-scoped, and **unreachable by any two humans** — the
row at the bottom of this table was the whole finding.

|                                                      |                              |
| ---------------------------------------------------- | ---------------------------- |
| Op log append / read back, head advances             | works                        |
| Canvas-anchored comments                             | works                        |
| Cursor broadcast endpoint                            | accepts                      |
| Share link → anonymous client loads the model        | works                        |
| Client comments through the link → architect sees it | works                        |
| Revoke the link → client loses access                | works (`share_link_invalid`) |
| Another firm reading your project                    | 404, correct                 |
| Invite a colleague into your firm                    | works (`POST /firm/invites`) |

Two smaller gaps found in passing:

- **A resolved comment disappears.** `CommentRepository` filters
  `resolved.is_(False)` in both list queries, no route exposes a filter, and nothing
  calls the `set_resolved(..., False)` that already exists. Resolve a client's note by
  accident and it is gone.
- **A stale `baseIdx` is accepted, not refused** — the append rebases rather than
  conflicting. Unreachable today (one user per firm), but it is what a second editor
  would meet.

- **The drawn set was a submission skeleton, not a submission set (audit J08, fixed
  2026-09-07/09).** An architect opening the ten sheets found no D/W tag on any plan
  although A-05 tabulated D1–D5/W1–W2/V1; room labels with a name and sq ft only, on top
  of per-room dimension cross-hairs whose figures sat under the room name (the collision
  audit could not see them — a chain's figures are not `Text`); a site plan with the four
  setbacks chained and nothing else, its side-a/side-b names swapped against the rules
  engine's rows; a Section A-A that was an envelope box with no cut wall, slab, stair or
  level; and elevations that were an outline with rectangles. Now: every opening carries
  the schedule's own tag (one `door_window_schedule` feeds A-05 and every plan and
  elevation); rooms read NAME / 3623 x 2703 / 9.79 m² / 105.4 sq ft, sized down and turned
  to fit, off the stair treads; A-01 chains every plot edge, the footprint, the road width
  and each setback under the engine's role for that edge, refusing to draw if a chain
  disagrees with `providedMm`, with PLOT SIZE in mm and feet-inches and the engine's
  coverage / FAR / setback rows in a notes block; A-04 and A-03A–D are the tested
  `sections/` and `elevations/` projectors on the sheet — hatched cut walls, slabs at
  their thickness, plinth, parapet, the stair riser by riser, level markers, the
  foundation line 900 below plinth, every opening on its face at its sill and lintel —
  with the projector's assumptions printed under the drawing. `render/labels.py` is now
  the one text measurer for the worker, the harness and the tests, and it boxes dimension
  figures. `test_render.py` pins each of these: `test_plan_opening_tags_are_the_schedule_
sheets_tags` (+ the `{}` and wrong-mapping negative controls),
  `test_every_room_on_every_plan_is_labelled_and_a_real_room_gets_all_four_values`,
  `test_site_plan_dimensions_every_side_the_footprint_the_road_and_the_setbacks` (+ the
  1 mm doctored-row refusal), `test_section_is_a_real_cut_through_the_stair`,
  `test_elevations_project_every_opening_at_its_sill_and_lintel_with_its_tag`,
  `test_the_collision_audit_sees_dimension_figures`. Still open: a dogleg's return flight
  is not drawn (the model stores one flight — said on the sheet), a shaft-sized room keeps
  its name only, the hand-checked dimension reference set is still empty (launch gate),
  and no DXF has been opened in a human CAD.

## What a trial needs that is not code

- **The 10-generation free quota.** Each trial account gets 10 solves per billing
  period; the fourth architect to explore will hit it mid-session.
- **A Brevo v3 API key (`BREVO_API_KEY`)** — sign-in is OTP by email, and on
  Railway's Hobby tier outbound SMTP is disabled on every port (_"SMTP is only
  available on the Pro plan and above"_). The first live sign-up timed out on
  `smtp-relay.brevo.com:587` after exactly the mailer's 15 s. Codes now go over
  Brevo's HTTPS API, which needs the `xkeysib-…` key from the **API Keys** tab —
  not the `xsmtpsib-…` SMTP key. The `SMTP_*` block is still honoured where SMTP
  is reachable, and `SMTP_FROM` remains the (Brevo-verified) sender either way.
  **EXECUTED 2026-09-02 10:52 UTC on the live stack:** with `BREVO_API_KEY` set,
  a real sign-up created a practice, Brevo answered `201 Created` to the HTTPS
  send (`mailer.otp_sent transport=brevo-http`, 565 ms end to end), and the code
  was verified fifteen seconds later (`otp.verified` → `auth.signed_in` → the new
  firm's project list). The first architect account on the deployed stack exists
  because of this send.
- **The first Generate on the deployed stack died in the worker image (fixed
  2026-09-02).** Three execution finds from one afternoon, all invisible to the
  suite:
  1. `services/Dockerfile`'s prod stage copied `services`, `apps/api`,
     `packages/model` and `rulepacks` but not `fixtures/`. The solver's furniture-fit
     stage opens `fixtures/catalog/furniture.json` from the repo root after stage B,
     so every generate job raised a bare `FileNotFoundError`, the runtime retried it
     four times, and the architect saw "something went wrong on our side". CI's
     compose e2e runs the `dev` stage with a bind mount, so the prod stage's COPY list
     had never been executed anywhere before that click. The catalogue is now copied,
     a missing catalogue is a permanent, path-free worker error (one attempt, honest
     copy), and `test_catalog_in_image.py` checks the Dockerfile against the path the
     loader opens.
  2. Credits were charged at enqueue and never refunded, so both failed generates
     counted against the ten free ones. `credit_events` now carries `job_id` and
     `refunded_at` (migration 0012, which also refunds every historical failed or
     cancelled job); the lifecycle consumer refunds on failed / dead-lettered /
     cancelled for solver, render and export jobs; every reader — count quota, money
     cap, `GET /billing/usage` — skips refunded rows. `test_credit_refund.py` pins it
     with three negative controls (no refund → 6 red; readers count refunded rows → 7
     red; refund on success would be a free trial forever → guarded).
  3. The dashboard fetched `GET /templates` and never passed the result to the
     new-project dialog, so no architect ever saw a template — the same class as the
     furniture-layer bug in `CLAUDE.md`. Fixed, with a dialog test and a source
     contract on the page. The Plan tab's failure card now offers "Start from a
     ready-made plan instead", which deep-links the dialog to the solved-plan
     template, and the failure copy no longer tells the architect to loosen the brief
     for a fault of ours.
     Also new: the trial allowance is visible — a card on the dashboard and a line in
     the Plan options header, read from the same rows the gate enforces — and the
     lifecycle consumer waits for the enqueue transaction to commit instead of dropping
     the first `started` event.
- **A ready-made plan library exists (2026-09-03), and every plan in it is the
  solver's own work.** No commercially usable, plug-and-play dataset of Indian
  two-storey house plans exists (research sets are single-storey apartments under
  non-commercial licences), so the library is seeded from real solver runs:
  `scripts/seed_plan_library.py` draws a plot, writes a brief, Generates, applies the
  best option exactly as the Options screen does, and captures the project's whole op
  log as `fixtures/plans/<id>.json` (flattened past the `solver.apply_option` wrapper,
  which would otherwise look up a job in another firm). `scripts/render_plan_previews.py`
  draws each plan through the sheet renderer's own primitives into `<id>.svg`, and the
  picker shows that as an `<img>`. Registration is data-driven: a recipe on disk is a
  template. Four plans ship: Bengaluru 30 × 40 G+1 3BHK, Hyderabad 30 × 40 G+1 3BHK,
  Bengaluru 30 × 50 G+2 3BHK, Bengaluru 40 × 60 G+2 4BHK. `test_plan_library.py` pins
  that each is flat, folds to the captured counts, renders to the stored thumbnail, and
  creates a project whose compliance report has no `fail`. That last gate dropped a
  30 × 40 2BHK that passed the solver's hard-rule gate but fails
  `nbc.ventilation.habitable.min` on the tab (the gate blocks only on `hard: true`; the
  tab shows every `fail`), and no NCR-pack brief cleared `ncr.parking.ecs`. Both are
  open items. Seeding lesson: a brief that declares no `carParking` fails every city
  pack's parking rule, so the seeder declares it — the same trap `solver_enqueue.py`
  documents for the web app's `parkingCount`.
- **The first library plans could not be walked through, and neither could any
  generated plan (found 2026-09-03, fixed the same day).** An adversarial review of
  the seeded plans found the flagship 30 × 40's front door opening into a 0.89 m²
  dead-end vestibule, seven of eight ground-floor rooms unreachable from the entrance,
  the kitchen entered only through the bath, and the first-floor bedroom an island.
  No loaded rule looks at doors, so the compliance report was green. Root cause in
  `services/solver/openings.py`: `place_doors` marked circulation rooms as reached
  across a shared wall without emitting an opening, and en-suite chaining let any
  reached room serve any other. Every plan the solver had ever produced carried this.
  Fixes: an archway is now placed on every circulation↔circulation span that carries
  reachability; a serving table says who may be walked through to reach whom (a bath
  never, a bedroom only to its own bath/dress/balcony, a kitchen only to its
  utility/store); door edges keep a 230 mm pier off a return wall (was 115, which put
  the frame flush with the corner). And the gate the rules engine never had:
  `garh_model.circulation` walks the door graph of a folded house (BFS from the
  entrance on the ground floor, from the stair above, doors only, baths never as
  corridors), `services/solver/gates.circulation_problems` folds every candidate's
  own ops through it before scoring, and `test_plan_library.py` requires every
  library plan to pass it. Negative controls: the two-room fixture with its solid
  partition is named unreachable until one door is added; the pipeline test stubs
  fold cleanly. The library was re-seeded under the fixed solver.
- **`PUT /brief` wrote the Vastu mode into the data patch (fixed 2026-09-03).** The fold
  reads `vastuMode` off the op's own field and the compliance pack set reads
  `brief.vastuMode`, so every brief saved from the form or a template kept the mode
  at "off" and never loaded the vastu pack — while the solver read the stray data key
  and optimised for advisory. One writer now, `test_brief_vastu_mode.py` pins both
  readers. Fixing that exposed the next one: the compliance projection sent every
  stair with `centroidMm: None`, and the engine refuses to classify a stair's Vastu
  zone without a centroid, so the first Generate with the mode actually on died in the
  solver's rules pass with `ComplianceUnavailable` (four retries, no options). Stair
  rows now carry the centroid of their footprint; `test_compliance_vastu_stair.py`
  evaluates a stair under the vastu pack end to end. Same review, also fixed: picker thumbnails drew a 300 mm paper margin
  around an 80 mm plan (unrecognisable at 112 px), carried hatch clip-paths the
  standalone fragment never defined (partitions as grey bands), and 2 px text —
  thumbnails are now the fabric only, walls, openings and stairs; and
  `dispatch_ops` refuses a `solver.apply_option` wrapper outright, so no template or
  form path can fold client-supplied geometry under a solver's name (the loader
  already refused it; the create-time claim in the test was untested and is now a
  control). Still open from the same review, as tasks: shafts sized under the
  room-detection threshold with ventilators credited into sealed cavities; the
  parking rule passing on a brief declaration while its message says spaces are
  shown; the solver gate blocking only `hard` rules while the tab shows every fail.
- **Two re-captured plans failed the tab's ventilation rule by under 0.06% (fixed
  2026-09-03).** `hyd-30x40-g1-3bhk` and `blr-30x50-g2-3bhk` came back from the fixed
  solver with every room reachable and one `fail` each: a master bedroom with 1.9068 m²
  of window against a 1.907152 m² requirement, a living-dining 1,046 mm² short. The
  solver sizes windows on its physical clear polygon (a 115 mm wall split 57/58 so the
  faces sum exactly); the model's room detection floors both faces to 57, so the tab
  divides by a room 1 mm wider on one side. `nbc.ventilation.habitable.min` is not
  `hard`, so the solver's own gate let both through. Windows are now sized against the
  detected polygon (`clear_polygon(..., as_detected=True)`), and `test_walls` folds
  real wall ops through the model and requires the two conventions to agree to the
  millimetre, with the physical polygon as the control that must not. The library was
  re-seeded under the fixed worker. Also found here: `scripts/sheet_goldens.py` takes
  `fixtures/plans/*.json` as its corpus the moment the directory has content, so the
  first library push turned the golden job red for want of goldens — the library plans
  now ARE the sheet-golden corpus, every ready-made plan renders its nine municipal
  sheets on every push.
- **The 230 mm door pier made stage A and stage B disagree (fixed 2026-09-03).**
  Raising the wall-end margin for doors left stage A floor-planning passages and
  stair arrivals at a naive 900 mm and giving circulation rooms no frontage floor at
  all, so the 40 × 60 and NCR cells produced layouts stage B then discarded at
  `DOOR_DOES_NOT_FIT` (an 800 mm door into a 1035 mm span). Every served span,
  passages included, is now floored at the door width plus both margins,
  snap-proofed. The first version also put the stair in that loop and made the
  30 × 40 Hyderabad program CP-infeasible; the cause was that a cased archway into a
  1200 mm passage entered at its end can never keep a 230 mm pier at both jambs — its
  jambs are the return walls. Archways keep the validator's 115 mm minimum
  (`ARCHWAY_END_MARGIN_MM`), framed doors keep the pier, and stage A floors the two
  kinds of span on the two figures (`test_archway_margin` pins both, with the span
  where an archway fits and a door cannot). CP-SAT under a wall-clock budget with
  eight workers is not deterministic, so the seed script tries three seeds per cell
  and records the one that produced the plan.
- **The library, as shipped (2026-09-03).** Four plans, every one captured from a
  real solver run under the final code and passing all five gates: Bengaluru 30 × 40
  G+1 3BHK, Hyderabad 30 × 40 G+1 3BHK, Bengaluru 30 × 50 G+2 3BHK, Bengaluru 40 × 60
  G+2 4BHK. The picker shows them with their thumbnails; the drawings pipeline renders
  their 42 sheets as the golden corpus. Six cells produced no plan and are not in the
  library: the 25 × 40 and 30 × 40 two-bedroom programs and the 40 × 60 G+1 four-bedroom
  are CP-infeasible under the seeded packs ("no arrangement satisfied every constraint"),
  and the three NCR cells lose every candidate to `BATH_VENTILATION` — a third bath
  with no non-road external wall and no shaft, which is the shaft finding above. Those
  are solver coverage work, not library work; a plan that is not here is one the
  product could not generate today. Found while regenerating the goldens: the plan
  sheets put the FFL marker 300 mm inside the building's top-left corner, on top of
  whatever room lived there — two label collisions on the 40 × 60 plan's upper-floor
  sheets. It now sits outside the footprint beside the north arrow, on every plan.
- **The checks strip said "Nothing to check yet" over a fully evaluated plan (fixed
  2026-09-03).** Seen in the library screenshots and traced in the browser: the API
  answered the plan page's compliance request with 65 evaluated results, and the strip
  still read "nothing to check yet" ten seconds later. The web schema admitted only
  scalar `actual`/`limit` values, the seven vastu zone rules report a list of zones and
  an `{allow}` object, zod rejected the WHOLE report, and the hook's catch kept the
  strip on its "not run" text. Any project with vastu mode on — every library plan,
  and the demo — was affected, on the strip and on the Compliance tab, which parse the
  same response. The schema now takes lists and objects, the options vastu map reads a
  one-zone list as the zone, and `schemas.compliance.test.ts` pins the row shape.
- **The platform fee (2026-09-06).** The owner's rule — 5 % on every dollar of credit,
  changeable tomorrow — is a percentage of provider cost in basis points, booted from
  `BILLING_MARKUP_PERCENT` and thereafter read from `platform_settings` on every charge,
  so `PUT /admin/billing/markup` (platform-owner emails only) changes the next charge
  without a redeploy. Each credit event records the fee it was charged at and what was
  charged beside the honest provider cost; budgets are spent in charges, reconciliation
  reads cost. `test_markup.py`: the arithmetic (half-up, once), the next-charge /
  never-the-last-row negative control, the owner gate (a firm admin is not an owner, an
  empty allowlist refuses everyone), and the usage card showing the percentage.
- **The fee has a page, and money reads in rupees (2026-09-07).** An owner signed in
  with an address on `PLATFORM_OWNER_EMAILS` gets a **Platform fee** link on the dashboard
  (`/platform/fee`): the fee in force, who set it and when (`billing.markup_set_by` beside
  the value), a field held to the server's own 0–100 / two-decimal contract before any
  round trip, and a confirm that states the next-charge rule. The link is a courtesy from
  `GET /admin/billing/markup`'s `canSet`; the PUT's 403 is the gate, and another firm's
  admin reads everything and changes nothing (`test_markup.py`, 27). Budgets and charges
  now show in rupees at ONE dated, hand-set rate (`BILLING_USD_INR_RATE` /
  `_AS_OF`, refused at boot if malformed, never fetched live) with the dollar source and
  the rate's date on hover; the fee is a separate line from provider cost and the three
  figures add up because the fee is derived as their difference. The ledger stays
  micro-USD — `test_inr_display.py` asserts nothing rupee-shaped leaves the API, and it
  pins the same conversion table `money.test.ts` pins, so the two implementations
  cannot drift by a paisa without one going red.
- **Out of credits is a page, not a dead end (2026-09-09).** A 402 from Generate used to
  toast "Try again", which 402'd again. `billingRouteFor` now sends any 402 to
  `/billing?reason=…&kind=…`, and the Billing page opens on a banner that states what ran
  out, the numbers, the reset date and the cheapest plan that lifts it, with the plan
  cards beneath — a firm admin can move to Studio there and then (immediate, no
  pay-first gate, no proration; stated on the confirm). The page also carries the ledger
  (every charge with provider cost, the fee as its own column, refunds with their
  reason, rupees with the dollars on hover; `GET /billing/credit-events`), the GST
  details form, invoices with their CGST/SGST or IGST split and a Pay button that walks
  checkout → mock widget (`POST /billing/payments/mock`, 404 under a real gateway) →
  verify, and seats. Members get every read and no write. Two ledger fixes landed with
  it: solver and export runs were priced at **0** from every real route (the call sites
  write no provider — it is our CPU — and `""` counted as free), so the fee was 5 % of
  nothing on every generation; `OWN_COMPUTE_KINDS` prices them now, with the route meta
  pinned byte for byte and a mock render beside them still 0. And the usage response
  says which allowances are actually **enforced**: the free plan's export allowance is 0
  by design, `require_quota("export")` is mounted nowhere, and the page now says "not
  enforced yet" rather than showing a wall that is not there. **Not done, and why:**
  mounting the export gate is one token on two routes, but every trial firm is on the
  free plan and would 402 on its next export, the demo seed needs a paid plan, and
  `test_cross_tenant`'s walker plus three other files export from free firms — a
  decision for the owner, with the recipe in `docs/billing-verification.md`. Razorpay
  has still never run live; the exact switch, what the web does and does not do under
  it (no `checkout.js`, no webhook route), and the test-key rehearsal are in
  `docs/ops-runbook.md` "Going live with Razorpay".
- **Generate on a plan the solver itself had produced answered "No plan cleared the
  quality checks" twice, and charged for both (found by the browser UAT, 2026-09-06).**
  CP-SAT under a wall-clock budget with eight workers is not deterministic; the plan
  library needed up to three seeds per cell and the product tried one. The solver now
  runs up to `SOLVER_SEED_ROUNDS` (3) fresh-seed rounds when nothing cleared — each
  announced on the progress stream, bounded, never checkpointed — and a run that
  delivers zero options refunds its credit (`no_options`), with the delivered-plan
  control beside it in `test_credit_refund`. `test_pipeline` holds the round
  behaviour: a seed-gated fake stage A that opens on round two, the single-round
  negative control, and no retry when the first search clears.
- **A reload during a refresh signed the architect out (found by the browser UAT,
  2026-09-06).** The journey landed on the login page from the 3D tab onward. The API
  log showed one `refresh_token_reused` then "no session to refresh" on every boot: a
  full navigation aborted an in-flight refresh after the server had rotated, the
  browser never received the new cookie, its next boot presented the token just
  spent, and strict rotation revoked the family. Rotation now carries a 30-second
  reuse leeway (`REFRESH_REUSE_LEEWAY_SECONDS`, 0 = strict): inside it a spent token
  chains forward once, the abandoned successor dies, and a third presentation — or
  anyone presenting the abandoned token — still revokes the family. Tests: the raced
  reload chains once, the abandoned successor is theft, the leeway off is strict.
- **Three delivered plans rendered as "No plan cleared the quality checks" (found by
  the browser UAT, 2026-09-06).** The solver log said `options=3`, the job row held
  three, `GET /solver-jobs/:id` returned three — and the Plan tab showed the
  loosen-your-brief card. The options screen validates every option at its boundary
  and dropped, one by one and silently, any whose rule rows carried the vastu zone
  lists and `{allow}` objects (the same shape that had already sunk the Compliance tab
  once, fixed there and not here). The option schema now takes the shared compliance
  value; an option this build genuinely cannot read is COUNTED and shown as a client
  defect with a reload, never as the solver's verdict; and the job row's `banner` is
  declared on the schema — zod strips undeclared keys, so the "why nothing cleared"
  sentence had never reached the screen. `types.test.ts` holds a vastu row verbatim
  and the unreadable-option negative control.
- **"Generate the set" refused every project that had never saved a version, and the
  product has no save-version button (found by the browser UAT, 2026-09-06).** A
  project started from a ready-made plan, drawn by hand or imported from DXF got 409
  "Save a version first"; a project with an old checkpoint was silently drawn at that
  checkpoint, hundreds of ops behind the screen. Sheets and exports now pin to the
  version at the head of the branch: the latest one when the design has not moved past
  it, else a checkpoint minted in the request (snapshot plus the frozen compliance
  report, exactly what `POST /versions` stores). Only a project with nothing to draw
  is refused, and the message says to generate or draw a plan.
  `test_drawing_version_pin.py`: minted at the head, re-minted after an edit and
  reused without one (the negative control), the export path, and the honest refusal.
- **The drawn set never appeared: the Sheets tab was listening on the wrong stream
  (found by the browser UAT, 2026-09-07).** The worker drew ten sheets in one second
  and the API persisted them, but the tab stayed on "No drawings yet" for four
  minutes. Two client faults stacked: the export-job row's `kind` is the export kind
  ("sheets", "dxf", …), and the generic job schema's `catch('solver')` relabelled
  every sheet job a solver job — the same trap the render row had already fallen
  into and been pulled out of; and the SSE path table guessed `/drawings-jobs/…`
  "until the drawings router lands", while the router had landed as
  `/export-jobs/:id/events`. The export row is now mirrored as sent and stamped
  (`kind: drawings`, `type: sheets | export.<kind>`), the table names the route the
  API mounts, and `schemas.jobs.test.ts` pins both — including that no drawings-worker
  row can ever parse as a solver job, and that the table agrees with the `eventsUrl`
  the server sends on the row.
- **A finished job could still read as "still generating": the terminal frame outran
  the row (found by the browser UAT on a rebooted box, 2026-09-07).** The worker
  publishes its last event on pub/sub and appends the lifecycle record in the same
  breath; the API's consumer writes the row a few tens of milliseconds later. The
  store re-read the row on the terminal frame, saw `running`, overwrote `succeeded`
  with it, and — the stream having closed — waited forever; the same race stalled the
  sheet set. It is timing-dependent, which is why the run before had passed. Two
  fixes: the API's event stream now holds a terminal frame until the row agrees
  (bounded at five seconds, then logged and sent anyway — a down consumer must not
  hide the worker's last word), and the store never lets a non-terminal row overwrite
  a terminal frame, re-reading up to eight times before keeping the frame's status.
  `test_sse_terminal_settles.py` (the frame waits for the third read; no reader, no
  wait; a stuck row still yields the outcome) and `jobs.refetch.test.ts` (kept
  through two running reads, settled on the third; the cap never downgrades).
- **Sign-in must not spend sign-up's cooldown (fixed 2026-09-02, first live trial).**
  Execution find: an architect with no account pressed _Sign in_ (202, nothing sent — the
  anti-enumeration path), then _Create an account_ thirty seconds later and got 429 "We
  just sent a code to that address". Both routes shared one 60-second resend key. The key
  is now per route (`otp_resend_identity` in `ratelimit.py`, the only place its shape
  lives); the hourly per-address cap stays shared. The naive fix — not charging unknown
  addresses — was rejected because it opens an enumeration oracle, and
  `test_auth_resend_scope.py` pins both properties with a negative control in each
  direction (revert the fix → the live-defect test reds; over-fix → the oracle guard reds).
- **A practice is one person until it can invite a second (closed 2026-09-07 → 09).**
  The J01/J10 audits scored sign-in 5.5 and collaboration 5 for the same reason: no
  route could add a member, no screen existed for the team, the firm or the account,
  and the sign-up copy promised all three. `POST /firm/invites` (admin; role + seat)
  emails a link through the same mailer as the OTP; the invitee accepts by the ORDINARY
  sign-in — a code to the invited address creates the member in the inviting firm with
  the invited role and the promised seat. The link resolves only to an honest pre-auth
  status page (pending / expired / withdrawn / used); it is never the credential, and
  only its `sha256` is stored. Two properties pull against each other and both are
  pinned in `test_team_invites.py` with the negative control that catches the over-fix:
  an expired or withdrawn invite refuses honestly on the status page while
  `POST /auth/otp` stays a uniform 202, and inviting an address that belongs to
  ANOTHER practice is byte-for-byte the same 201 as a fresh one (the admin is not an
  enumeration oracle) while inviting your own member is a firm-scoped 409. The resend
  cooldown is a third `OtpRoute` (`invite`) — the sign-in/sign-up lesson applied again —
  so an admin's resend never spends the colleague's own sign-in cooldown. Editor
  invites count against the plan's seats at creation (402), viewers are free. Members
  can be promoted, demoted (never the last admin) and removed (never yourself; the
  seat is released and every live session ends now, asserted on a real bearer and
  refresh cookie). `GET/PATCH /firm` holds name, address, GSTIN (check digit
  enforced), registration and phone; a rename reaches the sheet title block because
  that reads `firms.name`. The web gained `/settings/{practice,team,account}` and a
  login page matched to the server's error contract code by code — no invented
  "tries left", the server's Retry-After on the countdown, a step for
  `two_factor_required`, an invite banner, and no discarded mobile field.
  `docs/team-verification.md` is the ledger; what is still UNVERIFIED there is the
  invite email through Brevo on the deployed stack and the loop in a real browser.
- **Four more OTP findings closed the same day (from the delivery audit).** All
  execution finds on the deployed stack, all invisible to a suite that had never run
  with a mailer installed: (1) the response echoed the code whenever the dev echo was
  _enabled_ rather than _used_ — with a mailer installed on a dev-env deployment the
  code went by mail AND came back in the body, so any caller could sign in as any
  address (masked only while SMTP itself was failing); the body now mirrors the
  channel, and `DEV_ECHO_OTP=0` is set on the Railway api service as the belt to that
  brace. (2) A delivery 503 said "try again in a few seconds" but the resend cooldown
  had already been charged, so the retry it invited was a 429 for a code never sent;
  the cooldown is refunded on a 503 (only the 60 s one — the hourly and per-IP caps
  still bound a mail-bombing loop). (3) `SmtpMailer` upgraded with an UNVERIFIED TLS
  context (Python's `starttls()` default); it now verifies the relay. (4) A
  whitespace `SMTP_FROM` switched mail on with a blank sender. Every one carries a
  test in `test_otp_delivery_channel.py` / `test_mailer.py` and a negative control.
  Also set on the api service: `TRUSTED_PROXY_HOPS=1`, because behind Railway's edge
  every browser shared ONE per-IP bucket of 20 sign-in requests an hour — the fourth
  trial architect would have been throttled by the first three.
- **"PDF set" and "DXF" produced a job and never a file (found by the browser UAT,
  2026-09-07).** The export ran, the job row carried a signed link, and the view-model
  mapping dropped it, so the Sheets tab's job list showed a green tick and nothing to
  click; the toast promised the file would "download on its own" from code that never
  opened anything. Two more faults behind that one: the signed link redirected to the
  object store with a Content-Disposition on the redirect, which browsers discard —
  a PDF would have rendered in a tab rather than saved — and a 307 to an unsigned
  header override would not verify. Now the row's link reaches the job card as a real
  download link (right-click save works, popup blockers do not matter), an export
  started on this visit opens once on success, and both export and per-sheet
  redemptions presign S3's `response-content-disposition` so the FINAL response is an
  attachment with the file's name. `test_download_disposition.py`: the override rides
  in the signed query and changes the signature, the plain link carries none (the
  negative control), only `response-*` overrides may be signed, the filename is
  sanitised, and a round trip through the local object store answers with the
  attachment header. `JobCard.test.tsx`: a finished export renders the link, a
  running one does not, and `toJobVM` names the kind.
- **Every export request died with a 422 before a job existed (found by the browser
  UAT, run 9, 2026-09-07).** With the link and the disposition fixed, the harness still
  saw no download: the api log held `api.validation_failed fields=["params"]` for each
  click. The client posted `{kind, params: {}}`; the server's `ExportIn` names
  `kind`, `designVersionId`, `sheetIds`, `includeDisclaimer` and `options`, and every
  request schema forbids unknown keys, so the stray `params` was a 422 on every export,
  and the Sheets tab showed "Preparing your download" over a request that had already
  been refused. Nothing had ever compared the client's body with the server's model.
  The client now sends the model's own fields (`options` carries renderer options),
  and `api.exports.test.ts` reads the field names out of `schemas/jobs.py` on every run
  and asserts each key the client sends is one of them, with `params` as the negative
  control — renaming a server field turns the test red instead of turning the button
  into a 422.
- **Downloads were named `garh-export.pdf` and `A-01.pdf` (browser UAT run 10,
  2026-09-09).** The first saved PDF set said nothing about which house or which day;
  every project's A-01 was the same file name, so two exports of two houses would have
  landed as "garh-export (1).pdf". Export files are now
  `<project>-<drawing-set | drawings | model | renders>-<YYYY-MM-DD>.<ext>` and sheets
  `<project>-<sheet number>.<fmt>`, the name decided at export time with the project
  in hand and frozen on the export record (the download route is unauthenticated and
  has no project to ask); a later rename does not rename a file already sent to a
  municipal office. `test_export_filename.py`: the slug rules (accents fold, punctuation
  collapses, never empty, capped), every export kind has both a label and an extension
  (one table missing a kind would ship `.bin`), the export and sheet redemptions carry
  the name in the signed disposition, a record written before names were kept still
  redeems under the generic stem, and a sheet token naming another firm is a 404, not
  a name (the project lookup added for the name goes through the firm-scoped
  repository). Browser UAT run 11 saved the set as
  `uat-sharma-residence-drawing-set-2026-09-09.pdf` through the app's own link.
- **Production connections, as read from the deployed stack's own boot lines
  (2026-09-07, Railway project `garhai`, environment `production`).** Live: the
  copilot provider is `anthropic` (key set on the api service), the render provider is
  `stability` on the render worker (key set; `render.provider.selected
base_url=https://api.stability.ai`), sign-in mail goes through Brevo's HTTP transport
  (`auth.mailer_installed transport=brevo-http`), object storage is the project's
  MinIO with a public endpoint for browser downloads, Alembic runs on boot
  (`API_MIGRATE_ON_BOOT`), the solver worker boots with `seed_rounds=3`, and the
  platform fee reads `BILLING_MARKUP_PERCENT` with `PLATFORM_OWNER_EMAILS` naming the
  owner. Not live, and needing the owner's accounts rather than code: billing is the
  `mock` provider (no `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`), so plans and
  checkout exercise the mock; Sentry is wired but off (no `SENTRY_DSN` on any
  service); there is no custom domain, the web serves at the Railway-generated
  hostname and proxies the api over the private network. One setting to change with
  a rollback plan: the api boots with `APP_ENV=dev`, which leaves `/docs` open and
  skips the production readiness validator in `config.py`; `APP_ENV=production`
  turns both on and will refuse to boot until every variable the validator names is
  set (the S3 credentials must not be the MinIO defaults). The keys pasted into chat
  during this work (Brevo, Anthropic, Stability) must be rotated.
- **Seed rule values.** Fine for a trial provided the UI's confidence/citation chips
  are visible and no one submits to a municipality on them.
