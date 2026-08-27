# Phases 6 + 7 verification — copilot and renders

_Adversarial review and repair pass, 2026-08-21. Reviewer had python3.9.6 and nothing
else: no Node, no pnpm, no Docker, no pip, no Postgres, no Redis, no Pillow, no git._

This document is the honest ledger for the copilot (§10) and the render pipeline (§9).
It is split three ways and the split is the point:

|                | meaning                                                                                  |
| -------------- | ---------------------------------------------------------------------------------------- |
| **EXECUTED**   | ran on this machine, in this pass, and the output is reproducible with the named command |
| **TRACED**     | read end to end by hand against the spec; no execution                                   |
| **UNVERIFIED** | needs a toolchain that does not exist here; named, with the command that would settle it |

Read the **Top risks for Phase 8** section before building on any of this.

---

## 1. What changed in this pass

The wave shipped a copilot route, a validation pipeline, a 40-command eval corpus, a
render router, a client pack, and two web features. The review found **ten defects, all
ten fixed**, and rejected four candidate findings with reasons (§4). Two of the ten were
§13 containment holes. The other
substantial change is not a fix at all: **four claims that previously existed only
inside pytest now run on a bare interpreter**, because a claim whose only proof needs
Postgres is, on this machine, indistinguishable from a claim nobody checked.

`make bare` grew from 4 gates to 7. The three new ones execute 105 checks: 40 corpus
commands, 46 containment assertions, 19 render-catalogue assertions.

---

## 2. EXECUTED

Everything in this section ran, in this pass, on python3.9.6 with
`services/dev_stubs.py` supplying structlog/pydantic stand-ins.

### 2.1 `make bare` — the whole dependency-free gate set

```
make bare
```

Green. Seven gates:

| Gate                              | Command                                                                             | What it executes                                           |
| --------------------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `rule-fixtures`                   | `scripts/run_rule_fixtures.py`                                                      | 238 rule fixtures through the real `garh_rules.evaluate()` |
| `solver-smoke`                    | `scripts/solver_smoke.py`                                                           | 26 checks on the OR-Tools-free half of §5                  |
| `fixture-drift`                   | `…/copilot-commands/_tools/generate.py --check`, `e2e/fixtures/generate.py --check` | both derived corpora re-derived and diffed                 |
| **`copilot-eval`** _(new)_        | `fixtures/llm/copilot-commands/_tools/check.py`                                     | the 40-command corpus through the real pipeline            |
| **`copilot-containment`** _(new)_ | `scripts/copilot_containment.py`                                                    | 46 §13 containment checks                                  |
| **`render-mirrors`** _(new)_      | `scripts/render_mirrors.py`                                                         | 19 catalogue-mirror + determinism checks                   |
| audits                            | tenancy / secret / env / asset                                                      | unchanged                                                  |

The `asset-audit` release blocker (`inter-medium.woff` missing) is still outstanding
and still printed on every run. It is not a Phase 6/7 issue.

### 2.2 The copilot eval corpus (Phase-6 DoD)

```
python3 fixtures/llm/copilot-commands/_tools/check.py
→ copilot eval: 40 commands | in-scope 28/28 applicable (100%) | worst dry-run 1.27ms (budget 10ms)
```

Real `garh_model.fold`, real `garh_rules` engine, mock provider. For every in-scope
command the ops additionally `apply_group` cleanly on that command's model state — the
diff is applicable, not merely well-formed. **DoD floor is ≥90%; actual is 100%.**
Refusal outcomes carry zero ops.

### 2.3 §13 containment — 46 checks, all passing

```
python3 scripts/copilot_containment.py
→ all 46 containment checks passed
```

This gate is new. It runs the real pipeline against a **deliberately hostile
provider**, because the mock corpus is uniformly well-behaved and therefore cannot
demonstrate that misbehaviour is contained.

| Section | Claim proved by execution                                                                                                                                                                                                                                                                 |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A       | 5 classes of malformed op (unknown type, **float millimetres**, missing field, wrong type, non-object payload) are refused with the fold **never reached** — asserted with a fold that counts its own calls                                                                               |
| B       | An opening wider than its wall and a reference to a wall that does not exist both die in the real fold with zero ops surviving; a dry run leaves the input document **byte-identical**                                                                                                    |
| C       | A NEW hard rules failure blocks the diff and names the rule; a **pre-existing** failure does not block an unrelated edit; no plot boundary → `available=False` and nothing reported                                                                                                       |
| D       | `cannotDo` + ops, and `needsClarification` + ops, both drop the ops                                                                                                                                                                                                                       |
| E       | PII seeded into room name, room notes, storey name, wall name, plot address and the brief never reaches `task.system`/`task.user`, while the summary demonstrably _did_ walk those objects (ids and shape present) — non-vacuously. Plus: the §10 log masks the model's own `intent` line |
| F       | One bad answer + one good → applicable and `selfCorrected` in exactly 2 provider calls; two bad answers → honest `cannotDo`, zero ops, and **no third call**                                                                                                                              |
| G       | A 4-op batch dry-run folds in **1.43ms** against the §14 10ms budget                                                                                                                                                                                                                      |
| H       | Diff lines carry no op type, no raw id, spelled-out mm, and fit a rail row                                                                                                                                                                                                                |
| I       | Both tagged prompt-injection commands land on `cannotDo` with zero ops                                                                                                                                                                                                                    |

### 2.4 Render catalogue mirrors and seed determinism

```
python3 scripts/render_mirrors.py
→ render mirrors are in sync   (19 checks)
```

`services/render` is the source of truth; the API and the web app each keep a
hand-written copy because neither can import it. All three agree today, and the gate
**was negative-tested**: flipping `interior-kitchen` to allow `precise` in the API
mirror makes it fail with the drifted key named.

Also executed here: **"deterministic by seed"**. The mock provider's entire randomness
budget is `random.Random(material)`, and `material` is now derived by
`RenderRequest.grade_seed_material()` — which lives on the Pillow-free request, so the
claim runs without an image library. Proven: identical requests → identical material;
seed, preset, mode and size each change it; `mock.py` constructs exactly one `Random`
and calls no clock, `urandom`, `random.seed` or `id()` in the grade path. (The
_pixels_ remain unverified — see §4.)

### 2.5 Structural checks

- **Every Python file byte-compiles** — 286 files, `python3 -m py_compile`.
- **Every relative import across the Phase 6/7 web delta resolves** — 125 imports
  across 33 files, and every named import exists as a real export in the target
  module. No broken references in either direction.
- **All four new/regenerated JSON fixtures parse.**
- **No route-path collision** between `jobs.router` and `renders.router`; neither
  router is double-mounted; both tags are in `OPENAPI_TAGS`.

---

## 3. Defects found and fixed

### 3.1 §13 — a user-authored storey name was sent to the LLM _(containment)_

`STOREY_SUMMARY_FIELDS` in `services/llm/redaction.py` included `name`. The module's
own `_PII_SUSPECT_KEYS` classifies `name` as PII-suspect, so the allowlist contradicted
the denylist next to it, and a storey called `"Priya 9812345678 floor"` went to the
provider verbatim. Reproduced before the fix; a probe now prints `no leaks`.

**Fixed at root, three ways:** `name` dropped; a _derived_ `index` (array position)
added inside `summarise_model` so "the first floor" is still groundable without
forwarding user prose; and the two inline allowlists (plot, violations) hoisted into
named tuples so a new `check_allowlists_are_pii_free()` — run at import — covers every
allowlist and raises if any field is ever PII-suspect again. The allowlist is the
mechanism; it now checks itself.

### 3.2 §13 — the PII test was vacuous

`test_pii_seeded_into_the_document_never_reaches_the_prompt` seeded `brief.data`, which
`summarise_model` structurally never reads. It would have passed with the allowlist
deleted entirely, and it did pass while 3.1 was live.

**Fixed:** it now seeds every user-authored field the summariser actually walks (room
name, room notes, storey name, wall name, plot address) and asserts non-vacuity by
requiring the objects carrying them to be present by id. The same claim also runs
outside pytest, in `scripts/copilot_containment.py` section E.

### 3.3 §9 — a render in flight when the design changed was never marked stale

`RenderJobRepository.mark_stale_for_project` filtered `status == "succeeded"`. A job
queued or running at the moment of an edit was skipped; `succeed()` never touches
`stale`; so it completed, from a design version now behind head, and the gallery showed
a pre-edit image **with no banner**. Exactly the failure the code comment three files
away calls unacceptable ("a wrongly-fresh image costs the architect's credibility").

**Fixed:** the filter is now `("queued", "running", "succeeded")`. Correct without a
race: ops append holds the branch lock, so every row that exists at that moment
predates the edit, and a render enqueued afterwards is not in the table yet.
`failed`/`cancelled` are still skipped — they carry no image.

### 3.4 The rules gate blocked edits it could not measure

`NewFailureRulesGate.check` returned post-edit failures diffed against the baseline,
but only short-circuited when the _post-edit_ rules could not run. With no baseline
(`available=False`) every post-edit failure looked new — so an edit that merely made
the design measurable, such as setting the plot boundary, would be rejected for
setbacks that were always going to be there.

**Fixed:** no baseline → no diff → report nothing, and `available` already says so.
A gate that cannot compare must not pretend it did.

### 3.5 A render download link expired 10 minutes after the render finished

`GET /downloads/{token}` for `kind == "render"` redirected to `job.output_url` — the
presigned GET the worker reported at completion, TTL ~10 minutes. The renders router
added `fresh_image_url()` for exactly this reason and used it for history, but the
download path was never updated. A link redeemed the next morning landed on an S3
"Request has expired" page.

**Fixed:** the download path re-signs from the deterministic object key via
`fresh_image_url`, matching history. Non-storage URLs (developer golden runs) still
pass through unchanged.

### 3.6 The copilot's 429 told architects they were out of "brief parses"

`llm_per_firm_rule` is deliberately one shared spend bucket for both LLM routes, but
its message was written for brief parsing only. A copilot user hitting the limit read
copy about a feature they were not using — which reads as a bug, not a limit.

**Fixed:** `llm_per_firm_rule(settings, feature="copilot"|"brief-parse")` selects the
sentence; `name` (and therefore the counter) is unchanged, so a firm still cannot
double its provider budget by alternating routes. A test asserts both halves: same
bucket, different copy, unknown feature falls back to real copy rather than a KeyError
mid-429.

### 3.7 A bad preset was a 422 on one route and a dead job on the other

`POST /projects/:id/renders/client-pack` validated preset/mode up front (one 422).
`POST /projects/:id/renders` did not — an unknown preset queued a job that died in the
worker. Two answers to one mistake, and the worse one on the path people use most.

**Fixed:** the single-render route runs the same up-front check. The worker remains
authoritative and still re-validates.

### 3.8 The §13 injection claim could go vacuous, and did

Both consumers found the corpus's prompt-injection commands by grepping the _prose_
("ignore", "injection"). One matched only one of the two rows — so the claim silently
covered half of what it said. Deleting an injection row would have shrunk the claim
instead of failing a gate.

**Fixed:** `generate.py` now names them explicitly in `INJECTION_COMMANDS`, emits
`tags: ["injection"]` into `commands.json`, and refuses to generate a corpus where an
injection row is missing, expects anything other than `cannotDo`, or carries ops.
`MIN_INJECTION = 2` joins the DoD floor. Consumers key on the tag.

### 3.9 A docstring pointed at a test file that does not exist

`services/render/mock.py` claimed "`tests/test_mock_provider.py` asserts byte
equality." No such file exists anywhere in the tree. The byte-equality test is
`apps/api/tests/test_render_jobs.py::test_mock_provider_is_deterministic_by_seed_and_under_budget`,
and it `importorskip`s Pillow — so on this machine it does not run at all. A reader
chasing the determinism claim would have found nothing and had no way to know whether
the claim was untested or the file merely moved.

**Fixed:** the docstring now names both real checks and says how far each reaches — the
bare-interpreter seed-material gate, and the Pillow-gated pixel test that skips.

### 3.10 Diff copy defects (§12/§15)

Surfaced by the new gate rather than by reading: `room.assign` on an unassigned room
rendered as `"…(unassigned, kitchen)"` — the fallback used the room's _current_ type as
its name, so the line read like a contradiction. Walls read `"a internal wall"`. A
catalog description enumerating every settable field filled a rail row before reaching
the mm values. Also a duplicated `"heightMm"` in the payload-field tuple.

**Fixed:** unassigned rooms are "the room"; article agreement (`_article`); long stems
trim at the first comma (`STEM_MAX_CHARS`); tuple de-duplicated. The diff now reads
`Set a detected room's programme type (the room, kitchen)` and
`Change wall thickness (an internal wall, thickness 230mm)`.

---

## 4. Rejected findings, with reasons

**Fractional millimetres in the render camera.** `cameras.ts` computes bbox centres as
`(min+max)/2` and the street-day station point from an unrounded `distance`, so the
intermediate `eyeMm`/`targetMm` carry `.5` values. Rejected as a defect: `buildView`
rounds every component into `viewMeta`, and `viewMeta` is what is persisted and sent.
The unrounded values only position the local capture camera, a sub-millimetre
difference in a photograph. Integer-mm holds on the wire.

**The `_run_gates` rules check running after the fold rather than inside it.** Looks
like a second traversal, but the fold's output document is the only correct input to
the rules engine, and the two gates report different things. No change.

**`jobSchema` reading `queuePosition` while solver rows send `queueDepth`.** Real, and
still real — solver queue position is always `null`. Pre-existing, outside this delta,
and the fix belongs to the solver owner. Left alone, noted here and in the previous
wave's notes.

**The pack archive's stored `download_url` outliving its 10-minute presign.** The
archive endpoint is rebuild-on-demand and idempotent, so re-requesting mints a fresh
link. Not worth a second mechanism. Listed as a Phase 8 risk instead.

---

## 5. TRACED (read, not executed)

- **The containment boundary itself.** Every path from LLM output to the op log was
  followed by hand. There is exactly one: route → validation loop → client →
  `POST /ops` → sequencer. The route holds no `OpRepository`; `copilot_loop` holds no
  session, no repository and no project id; `ModelFolder` folds `ProjectDoc.from_json`
  on a fork of a JSON snapshot. `grep` for copilot references in the API finds no write
  path. Client-side, `useCopilot.apply` dispatches `toModelOps(proposal.ops)` — a 1:1
  map with no mutation — through the ordinary model store, and `reject` dispatches
  nothing. **No side door found.**
- **`baseIdx` is enforced.** `POST /ops` raises 409 when `head_idx != body.base_idx`,
  so a proposal validated against a moved design cannot land silently.
- **The server mints `groupId`**; the client applies with `proposal.groupId`, which is
  what keeps the propose log line and the decision log line joinable.
- **§12 DiffPreview reuse.** One component, in `components/DiffPreview.tsx`, used by
  both copilot and solver. `MiniDocPlan` is a thumbnail _inside_ it, not a second diff
  component.
- **One-Canvas rule.** `RenderCaptureBridge` mounts as a child of the single
  `CanvasRoot`; `RenderLauncher` is a DOM overlay outside the WebGL tree, in the 3D
  branch only. No second `<Canvas>` anywhere in the tree.
- **Per-firm render concurrency is real**, and firm-scoped: `count_active()` counts
  queued+running via `_scoped_select()`, checked against
  `render_concurrency_per_firm = 4` on both the single and the pack route (once per
  pack, by design).
- **Credit metering.** `credit_events(kind='llm')` is written on every copilot call,
  before the response and regardless of outcome, with `{route, outcome, opsCount,
tokens}`; `kind='render'` with `qty=N` on a pack; `kind='export'` on an archive. All
  four kinds are in `CREDIT_EVENT_KINDS` and the DB check constraint. `LlmUsage.to_json`
  keys do not collide with the meta keys they are spread beside.
- **The fail-closed rate limit** is the same `RateLimitRule` object brief-parse uses,
  with `fail_closed=True`, checked before any state is loaded so a limited request
  costs nothing.
- **Stale end to end**: worker/ops marks (`mark_stale_for_project` from
  `_append_ops`, gated on `_NON_VISUAL_OP_TYPES`) → API serves (`RenderJobOut.stale`
  from the column) → UI shows ("Design changed since this render" banner in
  `RendersTab`). All three legs exist; only the middle leg is executed anywhere.
- **`mint_render_outputs`** closes the real Phase-0 gap: the worker's
  `require_output("image")` now has a presigned PUT to write through. Both enqueue
  paths (single and pack) mint it.
- **Wire contracts** were compared field by field: `CopilotCommandIn` ↔
  `api.copilot.propose` body (`text`, not `command`), `CopilotProposeOut` ↔
  `copilotProposeSchema`, `RenderIn` ↔ `api.renders.start`. All agree. camelCase
  aliasing is `CamelModel`'s `alias_generator=to_camel` with FastAPI's default
  `by_alias=True`.

---

## 6. UNVERIFIED — and the exact command that settles each

| Claim                                                                                                                | Blocked by                                     | Command                                                      |
| -------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------ |
| The route wiring: auth, tenancy, 503s, metering rows, the 429                                                        | no fastapi/pytest/Postgres/Redis               | `make test-py`                                               |
| `apps/api/tests/test_copilot.py` (incl. 4 integration tests)                                                         | same                                           | `pytest apps/api/tests/test_copilot.py`                      |
| `apps/api/tests/test_render_jobs.py` — 15 tests incl. the §14 <1s budget and the stale flip through real `POST /ops` | same, + minio for the archive test             | `pytest apps/api/tests/test_render_jobs.py`                  |
| **Mock render pixels** — byte-equality for a given seed, the composite itself, the §14 <1s budget                    | no Pillow (the test `importorskip`s and skips) | `pytest apps/api/tests/test_render_jobs.py -k mock_provider` |
| Web typecheck and vitest (copilot + renders features)                                                                | no Node/pnpm                                   | `pnpm --filter @garh/web test && pnpm typecheck`             |
| Playwright `copilot.spec.ts`, `renders.spec.ts`                                                                      | no Node, no browser                            | `pnpm --filter @garh/e2e test:copilot`                       |
| **Real GL capture** — depth unpack, Sobel edges, readback from the live renderer                                     | no browser                                     | Phase 9 e2e                                                  |
| Browser → minio CORS for presigned PUTs (the 8-shot pack path)                                                       | no Docker                                      | `make up` then the pack button                               |
| Whether a real LLM understands Indian architectural vocabulary                                                       | no provider key                                | prompt-contract tests with `LLM_PROVIDER=anthropic`          |

The load-bearing gap is the last one. Everything proved above is about _containment_:
that a wrong answer cannot hurt the document. Nothing here shows the copilot is
_useful_ against a real model — the mock answers from a fixture keyed on the command
text, so 100% on the corpus measures the pipeline, not the comprehension.

---

## 7. Top risks for Phase 8

1. **Nothing in Phases 6 or 7 has touched a database.** Seven bare gates
   notwithstanding, every claim involving a session, a repository, a queue
   or an HTTP status is traced. Phase 8's exports read render jobs and design versions;
   the first real `pytest` run will find things here, and it should run before Phase 8
   builds on these tables.
2. **The stale flag is now correct in three places and executed in none.** 3.3 fixed a
   real hole by reasoning about a lock. If the branch lock's scope ever changes, the
   reasoning silently stops holding. A test that enqueues, edits, completes and then
   asserts the banner is the single highest-value test to write next.
3. **Render pixels are entirely unproven.** No Pillow here means the mock composite,
   its determinism _in bytes_, and the <1s budget are all untested. Phase 8's PNG/
   WhatsApp export and the pack zip both assume a working image path.
4. **The three-way render catalogue mirror will drift.** It is gated now, on a bare
   interpreter, which is the best available answer — but adding a preset still means
   editing three files in two languages. If Phase 8 adds sheet presets, consider
   generating the API and web copies from `services/render` rather than gating them.
5. **`/downloads/{token}` for exports still hands out a stored presigned URL.** Fixed
   for renders (3.5); the export branch reads `record.download_url` from Redis, which
   carries the same 10-minute expiry. Phase 8 is the export phase — fix it there, the
   same way, or accept rebuild-on-demand as the contract and document it.
6. **No read side for the §10 eval log.** Both copilot endpoints write structured log
   lines; §11 defines no endpoint to read them, and none was invented. The DEV-only
   `/dev/copilot-eval` page tallies the current session from the store and carries the
   shape a real endpoint should take. Until that exists, the eval corpus cannot learn
   from production, which is the entire stated purpose of logging it.
7. **`copilot_advanced_ops` is a feature flag defaulting to `False`** in
   `repositories/flags.py` and nothing reads it. Either wire it or delete it before it
   becomes load-bearing folklore.

---

## 8. Reproducing this pass

```sh
make bare                                          # all 7 dependency-free gates
python3 scripts/copilot_containment.py             # 46 §13 checks, verbose
python3 scripts/render_mirrors.py                  # 19 mirror + determinism checks
python3 fixtures/llm/copilot-commands/_tools/check.py
python3 fixtures/llm/copilot-commands/_tools/generate.py --check
find apps/api services scripts fixtures e2e -name '*.py' -not -path '*/__pycache__/*' \
  -print0 | xargs -0 python3 -m py_compile
```
