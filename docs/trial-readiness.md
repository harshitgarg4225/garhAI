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
