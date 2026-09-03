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

**Two colleagues cannot share a project.** `AuthService.signup` only ever calls
`create_firm_with_owner`, which is the single place a `User` row is constructed;
`POST /billing/seats` assigns a seat to a user that must already exist; and there is no
invite endpoint anywhere. Every signup creates a NEW firm with exactly one admin, and
the tenancy layer then correctly hides every project from everyone else.

So presence, live cursors, op streaming between people and in-project comments between
colleagues are all built, all firm-scoped, and today **unreachable by any two humans**.

|                                                      |                              |
| ---------------------------------------------------- | ---------------------------- |
| Op log append / read back, head advances             | works                        |
| Canvas-anchored comments                             | works                        |
| Cursor broadcast endpoint                            | accepts                      |
| Share link → anonymous client loads the model        | works                        |
| Client comments through the link → architect sees it | works                        |
| Revoke the link → client loses access                | works (`share_link_invalid`) |
| Another firm reading your project                    | 404, correct                 |
| **Invite a colleague into your firm**                | **no such endpoint**         |

Two smaller gaps found in passing:

- **A resolved comment disappears.** `CommentRepository` filters
  `resolved.is_(False)` in both list queries, no route exposes a filter, and nothing
  calls the `set_resolved(..., False)` that already exists. Resolve a client's note by
  accident and it is gone.
- **A stale `baseIdx` is accepted, not refused** — the append rebases rather than
  conflicting. Unreachable today (one user per firm), but it is what a second editor
  would meet.

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
- **Sign-in must not spend sign-up's cooldown (fixed 2026-09-02, first live trial).**
  Execution find: an architect with no account pressed _Sign in_ (202, nothing sent — the
  anti-enumeration path), then _Create an account_ thirty seconds later and got 429 "We
  just sent a code to that address". Both routes shared one 60-second resend key. The key
  is now per route (`otp_resend_identity` in `ratelimit.py`, the only place its shape
  lives); the hourly per-address cap stays shared. The naive fix — not charging unknown
  addresses — was rejected because it opens an enumeration oracle, and
  `test_auth_resend_scope.py` pins both properties with a negative control in each
  direction (revert the fix → the live-defect test reds; over-fix → the oracle guard reds).
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
- **Anthropic and Stability keys** if the trial is meant to exercise the copilot or
  real renders rather than mocks.
- **Seed rule values.** Fine for a trial provided the UI's confidence/citation chips
  are visible and no one submits to a municipality on them.
