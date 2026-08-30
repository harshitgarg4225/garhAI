# What a brand-new user actually gets

**Executed 2026-08-30** against a live local stack (Postgres 16, Redis, MinIO-compatible
S3, the API, the solver and drawings workers) by driving the real HTTP API as a new
account — not a fixture, not the seeded demo.

Everything in this file was run. Where something is unresolved it says so.

## The question

Every previous verification in this repository starts from the **seeded demo project**.
The `@smoke` suite signs in as `demo@garh.ai`, opens the demo project, and walks the six
tabs. `happy-path.spec.ts` — the file that describes the actual job, signup → plot →
brief → generate → edit → sheets → export → share — is seven tests, six of them
`test.skip`, and the bodies of those six are `expect(true).toBeTruthy()` placeholders.

So the journey an architect comes to the product to do had never been executed.

## What works

| Step | Result |
| --- | --- |
| Sign up a new practice | ✅ 201 |
| Sign in with the emailed code (dev echo) | ✅ 200 |
| Create a project on the BBMP pack | ✅ 201 |
| Draw a 30 × 40 ft plot with a 9 m road | ✅ |
| Paste a client's brief and have it parsed | ✅ |
| Save the brief | ✅ |
| Press Generate — job accepted and runs | ✅ 202 → `succeeded` |
| Compliance evaluates against the BBMP pack | ✅ 23 results |

## What does not

**A brief a user typed produces zero plan options.** The solver finishes, reports
`succeeded`, and returns nothing. The Options screen handles this honestly — "No plan
cleared the quality checks" — but it is the first thing a new user sees after doing
everything right.

The seeded demo project, on the **same plot**, produces 3 options. The difference is
entirely in the brief, and bisecting the demo brief toward a parsed one showed three
independent breakages — change any one and generation drops from 3 options to 0:

| Mutation applied to the demo brief | Options |
| --- | --- |
| none (control) | 3 |
| `bedroom` count 2 instead of a distinct `guest_bedroom` | 0 |
| storey pins removed | 0 |
| room sizes stripped | 0 |
| rooms reduced to `{type, count}` — what the parser emitted | 0 |

## Three defects found and fixed

All three were silent: no error, no log, a job that still reported success.

1. **Parsed rooms had no sizes at all.** The parser emitted `{"type": "bedroom",
   "count": 2}`; the program layer read `int(raw.get("minAreaMm2") or 0)`. Every room
   reached Stage A as a zero-area, zero-width rectangle. Nothing in the product turned
   "3BHK" into dimensions — the seeded demo's sizes are written out by hand, which is
   why it was the only brief that ever generated anything.
   Fixed: `services/llm/room_defaults.py`, applied in the shared parse path so both the
   mock and the real provider get it. Minimums are read from `rulepacks/nbc-core.json`
   — the same numbers the compliance tab cites — and targets are practice defaults
   emitted as editable assumptions.

2. **`count` was read by nothing.** `_parse_rooms` in the solver's payload parser keyed
   rooms by type and ignored `count` entirely, so two bedrooms became one. Two entries
   of the same type collided on the key and lost a room. The seed's own docstring
   documents the workaround ("which is also why the ground bedroom is a
   `guest_bedroom`") — a brief authored around a bug rather than the bug being fixed.

3. **Storey pins were discarded.** The parser read `storeyIndex`; the brief document,
   which the API forwards verbatim, writes `storey`. Every pin an architect set on the
   Brief tab went nowhere — including the demo's own two.

Six gates cover these, each broken on purpose and observed to fail.

## Still open

Fixing all three did **not** make a typed brief generate options on a 30 × 40 ft plot.
Stage A still reports `infeasible` for every stair anchor. The demo brief remains the
only input that solves.

What is now known: it is not the sizes, not the counts, and not the storey pins. What is
not known is what else the demo brief carries that a derived one does not. The next step
is a feasibility diagnostic — Stage A should be able to say *which* constraint it could
not satisfy, and today it says only "infeasible", which is why this took a bisection
rather than a read.

Until that is closed, **the product cannot take a new user from their own brief to a
plan.** The demo works; a real project does not.
