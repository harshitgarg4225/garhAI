# The binding specification

These four documents define **what Garh AI is meant to be**. Everything else in
`docs/` describes what has actually been built and how far it has been verified.
When the two disagree, these win — and the disagreement is a bug to file, not a
decision to re-make.

| File                                     | What it is                                                                                                                                                                                                                                                                                                          | When to read it                                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `00-build-playbook.md`                   | The locked decisions, the 12 golden rules, and the 10 build phases with their Definitions of Done                                                                                                                                                                                                                   | **Before writing any code.** Read it fully, every session.                                       |
| `01-engineering-playbook.md`             | Implementation source of truth: repo layout, DDL, model core, op taxonomy, solver spec, rules DSL, auto-dimensioning algorithm, sheet engine, facade kits, render service, LLM integration, API surface, frontend architecture, security checklist, perf budgets, UX rules, testing strategy, seed data, env/config | Continuously. Sections are numbered (§1–§18) and referenced throughout the code and the ledgers. |
| `02-product-spec.md`                     | The CPTO-reviewed product spec: features F0–F10, acceptance criteria, MVP vs v1.1/v2/v3 waves, launch metrics, legal and data policy                                                                                                                                                                                | When you need feature _intent_, an acceptance criterion, or an MVP cut line.                     |
| `03-market-research-and-oss-licenses.md` | Competitor patterns and the **verified OSS licence table**                                                                                                                                                                                                                                                          | **Before adding any dependency.** Apache/MIT/BSD/MPL only; never GPL/AGPL in app code.           |

## Why these live here

They arrived as a Claude Code skill (`garh-ai-builder`), which lives outside the
repository. That was fine while one agent held them in context and fatal the
moment the work is handed to anyone else: a clone of this repo contained a
detailed account of what had been built and no statement of what it was supposed
to be. Copied in verbatim on 2026-08-10 so the repository is self-contained.

They are unmodified. If a decision genuinely needs to change, do not edit these
files — add a row to `/DECISIONS.md` naming the section you are departing from
and why. That is the deviation protocol the playbook itself mandates.

## The rules that are not up for discussion

Restated here because they are the ones an agent under time pressure is most
tempted to bend, and each has already caused a real bug in this repo:

1. **Geometry is integer millimetres.** Never a float for a length, thickness or
   coordinate. Areas in mm². Four `Math.round` calls on op-payload paths were
   found and fixed in Phase 4 — half-up rounding versus the model's
   half-away-from-zero meant a wall drawn west landed 1 mm off the same wall
   drawn east.
2. **LLMs never emit geometry.** They emit typed ops from the taxonomy; the
   solver and rules engine produce and validate all geometry. Copilot output
   reaches the model only by the client re-submitting through the ordinary op
   sequencer.
3. **The op is the atom.** Model state is `fold(ops)`. The UI dispatches ops and
   never mutates state. If a feature cannot be expressed as ops, redesign it.
4. **Never show a hard-fail plan.** The §5.6 gates run before ranking and nothing
   downstream may re-admit a rejected option.
5. **Every tenant row carries `firm_id`**, and route handlers reach data only
   through the repository layer with a `TenantCtx`.
6. **Apache/MIT/BSD/MPL dependencies only.** No GPL/AGPL, no RPLAN-derived
   weights, never FLUX.1-dev.
7. **Golden files gate merges** — and a fabricated golden is worse than a missing
   one. If output cannot be generated, mark it pending, never invent it.
