# Testing

```bash
make test        # unit: pytest + vitest
make golden      # golden-file suites
make e2e-smoke   # Playwright smoke
make verify      # lint → typecheck → unit → golden + security guards
```

CI runs the same stages in the playbook order — `lint → typecheck → unit → golden →
e2e(smoke)` — plus `supply-chain` in parallel.

---

## What gets tested how

| Layer                     | Approach                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Rules checks              | every rule needs **≥1 passing and ≥1 failing** fixture — a pack cannot ship red                                          |
| Units conversion          | golden pairs that **TS and Python must both satisfy**                                                                    |
| Geometry / room detection | property tests (hypothesis): random rect subdivisions → expected rooms                                                   |
| Model core                | property-based fold/replay determinism via state-hash equality; undo/redo inverses; op-validation rejections             |
| Solver                    | 20-brief golden corpus: gates, per-seed determinism, time budget, locked-room preservation, plan JSON at **tolerance 0** |
| Drawings                  | 10 plan fixtures → SVG/DXF goldens; chain-sum assertions; collision-free label assertion; `ezdxf.audit()` clean          |
| Copilot                   | 40-command fixture set against the mock LLM + a schema-contract test against the real provider (behind an env flag)      |
| E2E                       | smoke on every PR; full happy path nightly                                                                               |
| Visual regression         | options screen, 3D with facade, one sheet — 0.1% pixel tolerance                                                         |

Two of these deserve emphasis because they catch bugs nothing else can:

**Plan JSON goldens run at tolerance 0.** Geometry is integer millimetres, so
"approximately equal" is not a meaningful concept — a one-millimetre difference is a
real difference and the test should say so.

**Dimension chains must sum exactly.** Every chain asserts `Σ segments == overall`.
This is the assertion that catches float contamination, and it catches it at the
point of damage rather than three phases later on a municipal sheet.

---

## Golden files

Golden files gate merges. A diff is a **build failure**, not a warning.

```
fixtures/
├── briefs/            # 20-brief solver corpus
├── plans/             # plan JSON — integer mm, tolerance 0
├── sheets/            # SVG + DXF sheet output
├── rules/             # per-rule pass/fail fixtures
└── copilot-commands/  # command → expected ops
```

SVG is normalised before comparison (timestamps and generated ids stripped) so a
diff means the _drawing_ changed, not that the clock did.

### When a golden legitimately changes

1. Understand the diff first. A changed dimension chain or area is a claim that the
   old output was wrong — be able to say why.
2. Regenerate.
3. Commit the regenerated goldens **in the same commit** as the code change, and say
   in the message what changed and why.

Never regenerate to turn a red build green without understanding the diff. That is
the specific failure mode that ships a wrong dimension to a municipal office.

CI uploads a `golden-diffs` artefact on failure so you can inspect the actual vs
expected output without reproducing locally.

---

## The cross-tenant test is mandatory

§13 requires a standing test that fetching another firm's project returns 404/403.
CI runs it as a named step, and **treats "no matching test" as a failure** — pytest
exits 5 when nothing matched, and a check that silently passes when the test has been
deleted is not a check.

---

## Performance budgets are tests

From §14 — these are assertions, not aspirations:

| Surface                                      | Budget                                 | Enforced by                |
| -------------------------------------------- | -------------------------------------- | -------------------------- |
| Canvas frame during pan/zoom/drag (G+2 demo) | <16ms                                  | Playwright trace assertion |
| Optimistic op apply (local fold)             | <10ms                                  | vitest perf test           |
| Compliance run                               | <100ms; ≤500ms debounced               | pytest timing              |
| Room re-detection                            | <50ms per storey                       | pytest timing              |
| Solver, 3 options                            | ≤60s (≤120s in CI, 2 workers)          | pytest timing              |
| 3D rebuild after an edit                     | <100ms dirty-storey                    | vitest perf                |
| Sheet set, G+1 3BHK                          | ≤5min                                  | worker test                |
| Initial web load                             | <3s on 4G mid-range; <1.5MB gz initial | Lighthouse CI ≥85          |
| Render (mock)                                | <1s                                    | e2e                        |

A change that blows a budget is a review blocker, not a follow-up ticket.

---

## Writing tests

- A bug fix comes with the test that would have caught it.
- Prefer fixtures over inline construction — the demo project is the universal
  fixture, and reusing it means the tours, goldens, perf budgets and screenshots all
  exercise the same thing.
- Mark slow or infra-dependent tests: `@pytest.mark.integration`,
  `@pytest.mark.golden`. The markers are declared in `apps/api/pyproject.toml` and
  CI splits stages on them.
- Tests use the mock providers. A test that needs a real API key belongs behind an
  env flag and must be non-blocking in CI.

## Running a subset

```bash
cd apps/api && pytest -q -k "rules and setback"    # pytest config lives here
cd apps/api && pytest -q -m "not integration"
pnpm --filter @garh/model test -- units
pnpm --filter @garh/e2e test -- --grep "undo"
```

`pytest` is run from `apps/api` because that's where `[tool.pytest.ini_options]`
lives — running it from the repo root picks up a different rootdir and skips the
configuration.
