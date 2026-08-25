# Phase 3 verification — what was executed, what was not

Same discipline as `phase-0-verification.md` and `phase-2-verification.md`: three
categories, and the value of this document is that it does not blur them.

| Category | Meaning |
|---|---|
| **EXECUTED** | Actually run on this machine. Output quoted. |
| **TRACED** | Read end to end by hand across files. No interpreter involved. |
| **UNVERIFIED** | Nobody ran it and nobody could. Stated plainly, with the command that settles it. |

Phase 3 is **incomplete**. It was built across three workflow runs that were cut
short by an org spend limit; the pieces below were finished by hand afterwards.
Read §4 before trusting any of it.

---

## 1. What changed by hand after the workflow stopped

The workflow's `critic-gates`, `stage-a`, `stage-b`, `integrator`, reviewer and
fixer agents never ran to completion. These gaps were closed directly:

| File | Status |
|---|---|
| `services/dev_stubs.py` | **New.** Import-time stand-ins for structlog/pydantic so the dependency-free solver modules can actually be imported on a bare machine. A real package always wins, so it is inert in CI. |
| `services/solver/tests/conftest.py` | **New.** Installs the stubs before pytest collects, and puts the repo root + `apps/api` on `sys.path`. |
| `services/solver/furniture_fit.py` | **New.** The §5.4 furniture-fit test — catalogue loading, required sets per room type, a deterministic shelf packer, and the 0-100 score §5.6 gates on. |
| `services/solver/critic.py` | **Rewritten.** The five `NotImplementedError` sub-scores (adjacency, Vastu, furniture fit, plumbing stack, privacy) plus `critique()` are implemented. |
| `scripts/solver_smoke.py` | **New.** Drives the whole ortools-free chain on the demo plot. |

## 2. EXECUTED — evidence, not claims

Run `python3 scripts/solver_smoke.py` (exit 0) and `python3 scripts/run_rule_fixtures.py`.

- **26/26 smoke checks pass** on a real 30×40 ft Bengaluru plot with a 9 m road
  south, G+1, `vastu_mode=advisory`:
  - §5.1 envelope derives to 65 m² buildable, strictly inside the plot, within the
    60% coverage cap (65 ≤ 66 m²), and is byte-identical across two runs.
  - Furniture catalogue loads; all 45 items present; every id named by
    `REQUIRED_SETS` exists (a missing id raises `CatalogError` rather than skipping).
  - A 2.0 × 2.0 m master bedroom is **rejected** (`bed-queen` will not fit); a
    3.6 × 3.6 m master is **accepted** at 30% utilisation. The packer is deterministic.
  - `shared_edge_mm` measures 2900 mm between living and kitchen; corner-only
    contact returns 0; rooms on different storeys never share an edge.
  - Plumbing stack scores 100 when bath sits over bath; a single-storey plan is not
    penalised.
  - Vastu scores **26/100** from `rulepacks/vastu.json` via the pack loader — a
    discriminating number, not a constant, and `vastu_mode=off` returns 100.
  - `critique()` assembles a full breakdown (composite 70/100); the composite equals
    `composite_score(parts)` exactly, so the explained parts and the gated number
    cannot disagree; weights sum to 100; two runs are identical.
  - §5.6 gates: a compliant option passes; a single hard-rule `fail` makes it
    **not presentable** (golden rule 2); the circulation cap rejects 12% against an
    11% limit.
- **211 Python files compile clean.**
- **238/238 rule fixtures still pass** through the real engine (118 pass / 108 fail /
  12 warn, 0 mismatches) — Phase 2's guarantee is intact.
- `make tenancy-audit`, `make secret-audit`, `make env-audit` all **PASS**.

### A real defect this found

The smoke harness initially measured circulation with a ground-floor-only
denominator while passing every storey's rooms. That drives `circulation_percent`
to **0** and silently disables the §5.6 circulation cap — a gate that always passes
is worse than no gate. The denominator is now each storey's room bounding box,
summed. Any future caller of `critique()` must match the denominator to the
placements it passes; `pipeline.py` should be checked against this when stage A lands.

## 3. TRACED

- `critic.score_vastu` reads zone rules through `program.load_vastu_zone_rules` /
  `zone_allowance_for` rather than re-deriving them, so the solver and the UI's
  compass wheel cannot disagree about the same plan. Field names (`allow`, `deny`,
  `preferred`, `weight`) were checked against the real dataclass, not assumed.
- `score_privacy` uses exact integer segment/rectangle intersection — no float, no
  division, identical on every machine.

## 4. UNVERIFIED — and the command that settles each

| Item | Why | Settles it |
|---|---|---|
| **Stage A (CP-SAT) has never run** | OR-Tools is pinned at 9.11.4210 but not installed; `stage_a.py` is 1314 lines that no interpreter has executed | `pip install ortools==9.11.4210 && pytest services/solver/tests -m ci` |
| **CP-SAT API calls are unchecked** | The adversarial reviewer that was to line-check `NewIntervalVar`/`AddNoOverlap2D`/`solver.parameters` against 9.11 never ran | as above — an import-time or call-time error surfaces immediately |
| **20-brief golden corpus** | Goldens cannot be generated without stage A, and fabricating them would be worse than having none | `python -m services.solver.golden --regen` after OR-Tools installs |
| **≤60 s for 3 options** | No solve has occurred | pytest timing test in CI |
| **Partial re-solve preserves locked ids** | `resolve.py` (776 lines) is stage-A-dependent | CI |
| **`solver.apply_option` expansion** | `solver_apply.py` (309 lines) needs a live Postgres to fold and snapshot | `pytest apps/api/tests/test_solver_apply.py` |
| **Options UI** | 15 files, never rendered — no Node on this machine | `pnpm --filter @garh/web test` |
| **Are the plans any good?** | The question §5 exists to answer. Legal ≠ plausible | The 5-architect blind panel in the product spec's launch gate |

## 5. Carried findings — both now CLOSED and proven

Re-checked on 2026-08-07 by executing them, not by reading. Both had in fact been
fixed by a later agent; this document previously said otherwise and was wrong.

**1. `building_use` default — CLOSED, and the fix is load-bearing.**
`garh_api.compliance.DEFAULT_BUILDING_USE` is now `"dwelling-single"`, a member of
the packs' own enum. Measured on a real `blr` fixture context:

| `profile.buildingUse` | `blr.*` rules that bind |
|---|---|
| `dwelling-single` (current) | **12** |
| `residential` (the old default) | **1** |

83 rules across the packs gate on `when.buildingUse in [dwelling-single,
dwelling-two, row-house]`. With the old default they reported `not_applicable` —
every BBMP/DDA/GHMC setback, FAR, coverage and height band silently inert while the
report still looked green. The client mirror
(`apps/web/src/features/plot/rules.ts`) already used the correct value, so the two
sides now agree.

**2. Value-override substitution — CLOSED, with provenance.**
`substitute_value_override` in `checks.py` is wired into `engine._evaluate_rule`
per instance. Executed: setting `profile.overrides.values.coveragePct = 95` on a
failing coverage fixture gives

```
status           = pass          (was fail)
limit            = 228000000     (substituted)
original_limit   = 156000000     (pack value retained for display)
value_overridden = True
overridden       = False
```

Note the two flags are deliberately distinct and must not be conflated:
`overridden` means the architect *acknowledged* a failing rule; `value_overridden`
means a *limit was changed*. `EvaluationReport.hard_failures` excludes the former
and not the latter — a value override changes the limit, it does not excuse a
failure. Golden rule 4 holds: the seeded pack value survives in `original_limit`
so the UI can show "1.2 m (pack value 1.5 m, overridden)".

**3. Still open:** DXF fixtures have never been loaded through real `ezdxf`
(no ezdxf on this machine — CI must).
