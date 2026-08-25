# Phase 2 verification — what was traced, what was executed, what is still unproven

*Written 2026-08-05, at the close of the Phase 2 build + adversarial review + repair pass.*

Phase 2's Definition of Done is three clauses:

> every rule has ≥1 passing and ≥1 failing fixture and they all pass; changing the
> city preset re-validates live; brief → chips UI.

**Verdict: clause 1 is EXECUTED and passes. Clauses 2 and 3 are TRACED end to end
but have never rendered in a browser.** The gap is the same toolchain gap Phase 0
recorded — no Node, no Docker, no Python 3.11 on any authoring machine — with one
material improvement over Phase 0: the rules engine is pure Python stdlib, so this
pass could *run* it, and did.

The tier vocabulary is inherited from [`phase-0-verification.md`](./phase-0-verification.md)
§1 and means exactly the same thing here: **EXECUTED** = a command ran on this
machine and produced the stated output; **TRACED** = every file on the path was
read and the contract checked by hand or purpose-written script; **UNVERIFIED** =
nobody has run it, stated plainly with the command that settles it.

---

## 1. DoD clause 1 — "every rule has ≥1 pass and ≥1 fail fixture and they all pass"

**EXECUTED. All 238 fixtures pass under the real engine.**

Two independent levels, both run on this machine (Python 3.9.6):

1. **Data-side gate** — `python3 fixtures/rules/_tools/verify_fixtures.py`:

   ```
   OK
     packs      : 5
     rules      : 118 (all ids unique, all prefixed by their pack)
     fixtures   : 238 (every rule has >=1 pass and >=1 fail)
     check types: 18 in use
   ```

   This proves corpus *shape*: unique prefixed ids, ≥1 pass + ≥1 fail per rule,
   no floats anywhere, fixture geometry self-consistent (stored `areaMm2` /
   `leastWidthMm` / `centroidMm` recomputed from the polygons), every `when`
   field and check type inside the DSL schema, every seed rule carrying a
   citation.

2. **Engine execution** — because `garh_rules` imports nothing outside the
   stdlib, a driver script loaded each fixture's embedded `context`, ran the real
   `garh_rules.evaluate(context, root="rulepacks")`, found the result row for the
   fixture's `ruleId`, and asserted the fixture's `expected.status` **and**, where
   stated, `expected.actual` and `expected.limit`:

   ```
   fixtures executed: 238
   status counts    : {'fail': 108, 'pass': 118, 'warn': 12}
   ALL FIXTURES PASS under the real engine
   ```

   This is the same evaluation entry point `GET /compliance` calls in production
   (`garh_api/compliance.py → evaluate_document → garh_rules.evaluate`). What it
   does **not** exercise: the model→context *projection* in
   `garh_api/compliance.py` (fixtures carry pre-built contexts), the FastAPI
   route, or the repository write of `compliance_reports`. Those stay TRACED.

To re-run the engine execution, any Python ≥3.9 works:

```bash
cd "apps/api" && python3 - <<'EOF'
import json, os, sys
sys.path.insert(0, ".")
from garh_rules import evaluate
root = os.path.abspath("..")
for pack in ("nbc-core", "blr", "ncr", "hyd", "vastu"):
    d = os.path.join(root, "..", "fixtures", "rules", pack)
    for n in sorted(os.listdir(d)):
        if not n.endswith(".json"): continue
        fx = json.load(open(os.path.join(d, n)))
        report = evaluate(fx["context"], root=os.path.join(root, "..", "rulepacks"))
        row = [r for r in report.results if r.rule_id == fx["ruleId"]][0]
        assert row.status == fx["expected"]["status"], fx["fixtureId"]
print("OK")
EOF
```

(Adjust paths to taste; the CI job `rules-fixtures` runs the equivalent under
pytest via `apps/api/garh_rules/tests/`.)

---

## 2. DoD clause 2 — "changing the city preset re-validates live"

**TRACED**, file by file. The path:

| Step | File / contract | Checked |
|---|---|---|
| Preset select | `features/plot/RegProfilePanel.tsx` — the "Rule preset" `SelectField` maps UI value through `cityPackToStored()` (`'custom'` → `null`) | option ids match `rules.ts CITY_PACK_OPTIONS` and the server's `CITY_PACK_IDS` (`blr`/`ncr`/`hyd`) |
| Op, not mutation | `features/plot/ops.ts regProfileOp()` builds `plot.set_reg_profile {cityPack, overrides}` — field-for-field against `packages/model/src/ops.ts` | payload audit done across **every** Phase-2 dispatch site (boundary/north/road/regProfile/brief.update/DXF-apply) |
| One writer | `usePlot.ts` dispatches through `useModelStore` — the only writer; the op group flushes to `POST /ops`, the server folds and confirms, `baseIdx` advances | store contract unchanged from Phase 1 |
| Re-check trigger | `pages/useLiveCompliance.ts` re-checks when `baseIdx` advances (server-confirmed state, never the optimistic document — re-checking on local dispatch would race the flush), debounced 450ms (§14 allows ≤500) | deliberate indirection documented in the hook header |
| Server evaluation | `GET /compliance` → `evaluate_document(state.document)` → `packs_for()` resolves `nbc-core` + city pack + `vastu`-if-enabled → `build_evaluation_context` → `garh_rules.evaluate` | the evaluate call itself is the EXECUTED path from §1 |
| Chips | `components/ComplianceStrip.tsx` renders the mapped issues; `issues === null` ("nothing checked yet") is kept distinct from `issues === []` ("checked, clean") | honesty states verified in `useLiveCompliance` + strip |

Also traced on this path: the **client-side pack mirror** (`features/plot/rules.ts`)
that shows the panel's numbers instantly. It mirrors `garh_rules/predicates.py`
operator-for-operator, including the two subtle rules — `plotAreaSqm` thresholds
scale ×1,000,000 against exact mm² **for every operator including `eq`/`in`**, and
the scaling is **int-only** (a fractional threshold passes through unscaled on both
sides). Both are pinned by synthetic-pack tests in `plot.test.ts` (a shipped-pack
sweep confirmed no live pack uses `eq`/`in` on `plotAreaSqm`, so the pin is the
only thing keeping the mirror honest).

**UNVERIFIED:** nothing on this path has rendered in a browser; the 450ms
debounce + <100ms op budget adding up to "inside a second" is arithmetic, not a
measurement. Settled by: `make up`, draw a boundary, flip the preset, watch the
strip; then `make e2e-smoke` (the smoke spec asserts the "Rule preset" control and
compliance region exist and react).

---

## 3. DoD clause 3 — "brief → chips UI"

**TRACED.** Two routes into the same op:

* **Form**: `features/brief/BriefForm.tsx` + `fields.tsx` → `useBrief.update()` →
  `briefUpdateOp()` builds a single `brief.update` op (merge patch + recomputed
  completeness, optionally `vastuMode`) → one dispatch group = one undo step.
  `CompletenessMeter.tsx` renders the completeness the op wrote — the meter can
  never disagree with the stored document because it has no second computation.
* **Free text**: `FreeTextParse.tsx` → `POST /projects/:id/brief/parse`
  (rate-limited fail-closed, metered via `credit_events(kind='llm')`, PII-redacted,
  schema-gated by `BRIEF_PARSE_SCHEMA`) → stated vs. assumed fields render as
  editable **assumption chips** (`@garh/ui` `AssumptionChip` — golden rule 4:
  seeded values never look verified) → `apply()` prunes unchanged keys and
  dispatches the same `brief.update` op. A present-but-off-enum `vastuMode` from
  the parser is **dropped**, never written into `brief.data` (see §4, finding 6).
* **LLM corpus**: `python3 fixtures/llm/brief-parse/_tools/generate.py --check` —
  **EXECUTED**: `brief-parse corpus check: 12 fixtures OK` (each fixture validates
  against the schema and round-trips byte-exactly through the real
  `synthesize_brief_parse`).

**UNVERIFIED:** the React components have never mounted; vitest suites
(`brief.test.ts`, `plot.test.ts` and 22 other TS test files) have never run under
vitest. Settled by: `pnpm -r test`, then the browser walk.

---

## 4. The adversarial review — 7 findings, all verified against the code, all fixed at root

An independent reviewer audited the Phase-2 delta (47 files). Every finding was
re-verified against the tree before this document was written; none was a misread.
All seven are fixed **in the shipped code**, not deferred:

| # | Finding (abridged) | Root fix | Proof |
|---|---|---|---|
| 1 | **major** — `regProfile.overrides.values` would crash the engine's override parser (`RuleOverride.from_json` demands a `reason` per key) the moment overrides were wired into evaluation | `garh_rules/context.py` reserves `VALUE_OVERRIDES_KEY = "values"` and routes it **before** iterating rule-id acknowledgements; `ProfileSummary` gains a typed `value_overrides` map that parses, audits and round-trips (substitution into check values is explicitly Phase 3). `garh_api/compliance.py evaluate_document` now passes the document's own `regProfile.overrides` through by default, so the shape is *live*, not latent. | **EXECUTED** on this machine: a profile carrying both shapes (`values` + a rule acknowledgement) parses, round-trips, and evaluates through the real `evaluate()` — the acknowledged rule reports `overridden: True` and stays in the fail list (overrides never silence a check). Pinned by `test_value_overrides_coexist_with_rule_acknowledgements` and `test_value_overrides_round_trip_and_reject_non_integers` in `garh_rules/tests/test_engine.py`. Decision row in DECISIONS.md. |
| 2 | **major** — panel copy claimed overrides "reach the compliance report", which they do not yet | `RegProfilePanel.tsx` copy now states the truth twice: the chip reason ("…the compliance report still checks against the pack value for now") and the footer ("stored and audited, but the compliance report still checks against the pack values until value overrides reach the engine") | copy read back in the shipped file |
| 3 | **minor** — TS predicate mirror didn't scale `eq` thresholds; predicates.py scales every operator | `rules.ts predicateMatches` routes `eq` and `in` through `scaled()`, mirroring the int-only rule; header documents the invariant | pinned in `plot.test.ts` ("scales eq and in thresholds on plotAreaSqm like predicates.py (int-only)") with a synthetic pack |
| 4 | **minor** — setting `self.timeout_seconds` inside `handle()` took effect one job late | `services/common/runtime.py` reads `handler.timeout_for(ctx)` **before** `create_task`; `DrawingsJobHandler.timeout_for()` returns the per-kind budget (sheets 420s, import `max(30, 3× parse timeout)`, export 600s); the docstring names the old bug | code + comment at `runtime.py` (timeout read pre-task) re-read this pass |
| 5 | **minor** — rate-limit slot charged and 20 MB body buffered before the idempotency replay check | `routers/imports.py`: `guard.begin()` runs first; a replay returns the stored job before `enforce_rate_limit` and before the body is read — a mobile-uplink retry costs the firm nothing | order re-read this pass (guard at the top of the handler, rate limit + body read inside the `try` after it) |
| 6 | **minor** — `FreeTextParse.apply()` fed `state.data` (still carrying an off-enum `vastuMode` string) into the patch in the undefined branch | both branches build on `rest` (destructured without `vastuMode`); comment states why | code re-read this pass |
| 7 | **minor** — `services/llm/schemas.py` reads `packages/model/schema/common.schema.json` at import time; packaging `services/` without the schema tree breaks the LLM layer | no code change (failure is loud by design); the image-content invariant is now a DECISIONS.md row so it survives as a decision, not an accident | both Dockerfiles COPY `packages/model`; DECISIONS.md row added 2026-08-05 |

Pre-existing issues the review confirmed but did not re-report (already flagged by
the authors, still open): see §6.

---

## 5. Everything actually EXECUTED in this pass

On Python 3.9.6 + GNU Make 3.81:

```
make tenancy-audit   → ok — no direct session access outside repositories
make secret-audit    → ok — no secret names or non-VITE_ env reads
make env-audit       → 134 documented names, 96 settings fields, 24 direct reads; no drift
make license-check   → scanned 16 python distributions: 0 denied, 0 unknown (JS half skipped: no lockfile)
python3 -m py_compile over all 185 Python files (apps/api + services)        → clean
python3 fixtures/rules/_tools/verify_fixtures.py                             → OK (118 rules / 238 fixtures / 18 check types)
REAL-ENGINE fixture run: 238/238 evaluate with expected status+actual+limit  → ALL PASS (§1)
python3 fixtures/llm/brief-parse/_tools/generate.py --check                  → 12 fixtures OK
python3 fixtures/model/_tools/generate_golden_states.py --check              → OK (11 cases)
JSON parse over all 286 fixture/rulepack JSON files                          → clean
ProfileSummary both-shapes parse + round-trip + evaluate() with overrides    → OK (§4 finding 1)
```

---

## 6. UNVERIFIED — the honest list, with the command that settles each

| # | Claim | Settles it |
|---|---|---|
| 1 | The plot editor, brief form, reg-profile panel and DXF dialog render and behave | `make lockfile` (Node 20) → `make up` → browser walk; then `make e2e-smoke` |
| 2 | 24 TS test files pass under vitest (incl. the predicate-mirror pins and `boundaryGroupOps` fold assumptions) | `pnpm -r test` |
| 3 | The DXF parse works against **real ezdxf** — the hand-written `fixtures/dxf/*.dxf` have never been loaded by it (ezdxf is not installable here) | `pytest apps/api/tests/test_dxf_import.py services/` in CI / Docker |
| 4 | The subprocess sandbox (spawn context, `RLIMIT_AS`/`RLIMIT_DATA`, terminate→kill after timeout+grace) behaves as written on Linux | the same CI run; the rlimits are Linux-semantics and were only traced here |
| 5 | `POST /projects/:id/import/dxf` end to end: 20 MB streaming cap, sniff, S3 SigV4 presigned PUT, queue envelope, SSE `eventsUrl`, `GET /import-jobs/:id` firm-scoping (cross-tenant = 404) | `pytest apps/api/tests/` + `make e2e` with minio up |
| 6 | Idempotent replay returns the stored job without burning a rate-limit slot (§4 finding 5) — traced only | `apps/api/tests/test_dxf_import.py` (replay case) under pytest |
| 7 | `useLiveCompliance` re-check lands "inside a second" of a preset change | measured in the browser, `performance.spec.ts` |
| 8 | The real Anthropic brief-parse provider (schema-gated, metadata-only logging) against the live API | `PROVIDER_LLM=anthropic` + a key, `pytest -m anthropic_live` (env-flagged) |
| 9 | Value overrides SUBSTITUTED into check values — **deliberately not built**; the engine parses/audits them only | Phase 3 wiring; DECISIONS.md row marks the boundary |
| 10 | `build_evaluation_context` defaults `building_use="residential"` while the packs and the client mirror use `"dwelling-single"` — city-pack `when: {buildingUse: …}` clauses can mis-band until the brief supplies the real use. Pre-existing, flagged by the authors, out of the Phase-2 delta | fix alongside Phase-3 brief→profile plumbing; test with a `when: {buildingUse: {eq: "dwelling-single"}}` rule through `GET /compliance` |
| 11 | The 118 seed rules match current bye-laws — every rule is `confidence: seed`, `review.status: unreviewed` | the architect review loop (launch gate), not a command |

---

*If you catch this document over-claiming, that is a bug in the document, and a
serious one.*
