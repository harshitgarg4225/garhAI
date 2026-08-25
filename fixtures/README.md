# `fixtures/` — the golden corpora

Playbook §1 puts `fixtures/` at the repo root with `briefs/`, `plans/`, `sheets/`
and `copilot-commands/`. This is the index, including an honest column for what
is actually populated today.

| Directory | Populated? | Pins | Consumed by |
|---|---|---|---|
| [`briefs/`](briefs/) | **yes** — 20 briefs + `index.json` | the solver/brief-parse input corpus: 20 real Indian plots across BLR/NCR/HYD, rect + L + T, G+0…G+3 | `apps/api/tests/test_brief_corpus.py`, Phase 3 solver goldens |
| [`rules/`](rules/) | **yes** — 238 fixtures across 5 packs | every one of the 118 seeded rules has ≥1 passing and ≥1 failing fixture | `apps/api/garh_rules/tests/`, `_tools/verify_fixtures.py` |
| [`model/`](model/) | **yes** — `golden-units.json`, `golden-states.json` | the TS↔Python contract: 67 unit-parse pairs + 16 must-fails; 11 op logs and the `stateHash` each folds to | `apps/api/garh_model/tests/`, `packages/model/src/{units,fold}.test.ts` |
| [`catalog/`](catalog/) | **yes** — furniture, materials, facade kits | the catalogue the API serves when `GARH_CATALOG_DIR` points here | `apps/api/tests/test_catalog_fixtures.py` |
| [`plans/`](plans/) | **no — Phase 3** | solver output as op logs: ≥3 options per brief, critic scores, gate results | Phase 3 DoD |
| [`sheets/`](sheets/) | **no — Phase 8** | SVG/DXF sheet goldens, dimension chains, area statements | Phase 8 DoD |
| [`copilot-commands/`](copilot-commands/) | **override dir only — Phase 6** | the 40-command eval set; the shipped mock corpus lives in `services/llm/fixtures/` | Phase 6 DoD |

Each empty directory carries a README naming its phase and the exact shape of
what lands there. None of them contains invented data: a golden file's only value
is that it is a trustworthy record of real output (golden rule 10), and a
plausible-looking fake would destroy that before the feature exists.

## House rules for every corpus here

1. **Data is language-neutral JSON.** No TypeScript, no Python, no test
   framework — both sides of the TS/Python mirror read the same bytes.
2. **Derived corpora ship their generator** in `_tools/` with a `--check` mode CI
   can run, so "regenerate and read the diff" is a real workflow. `rules/` and
   `model/` already do; `plans/` and `sheets/` must.
3. **Hand-authored corpora say so.** `rulepacks/*.json` are hand-authored and
   `fixtures/rules/**` is derived from them; editing a pack means regenerating
   fixtures and reading the diff.
4. **Regenerating a golden is a deliberate act** with a note in the same commit.
5. **Op logs, never geometry snapshots**, wherever a design is involved. Derived
   room ids are history-dependent (see `model/README.md`), so a snapshot pins the
   accident of construction order while an op log pins the decision.

## CI order

`fixtures/rules/_tools/verify_fixtures.py` and
`fixtures/model/_tools/generate_golden_states.py --check` both run **before** the
engine tests they feed, so a broken fixture reports as a broken fixture rather
than as fifty failing assertions.
