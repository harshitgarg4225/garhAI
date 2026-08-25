# `fixtures/plans/` — solver golden corpus

**Status: empty. Filled by Phase 3 (layout solver). Nothing here yet, and that is
the honest state — a fabricated plan golden would be worse than none, because a
golden file's whole job is to be a trustworthy record of real output.**

Playbook §1 lists this directory; §16 says what it will hold and Phase 3's DoD
says when.

## What lands here

For each of the 20 briefs in [`fixtures/briefs/`](../briefs/), the solver's
output, frozen:

| File | Content |
|---|---|
| `plan-NN-<brief-id>.json` | the ≥3 generated options as **op logs**, not pictures — `{briefId, seed, options: [{optionId, ops[], criticScore, gateResults}]}` |
| `index.json` | manifest: brief id → plan file, expected option count, expected wall-clock budget |

## Why op logs and never geometry snapshots

Two reasons, both learned the hard way elsewhere in this repo:

1. **Room ids are history-dependent.** `fixtures/model/README.md` documents it:
   the same drawing built in a different wall order gets different derived room
   ids and therefore a different `stateHash`. A geometry snapshot would pin the
   accident of ordering; an op log pins the decision.
2. **A snapshot cannot be replayed.** An op log can be folded by *both*
   `packages/model` and `apps/api/garh_model`, so one fixture tests the solver
   *and* the cross-language contract at once.

## The gate this corpus becomes

Phase 3 DoD, verbatim: *20-brief golden corpus solves ≤60s each with ≥3 options;
all options pass hard rules; locked-room regen preserves IDs; plan JSON goldens
stable.* Concretely, once populated:

```bash
cd apps/api && pytest -q -m golden        # fold each plan, assert the pinned stateHash
python3 fixtures/rules/_tools/verify_fixtures.py   # rules fixtures first, as ever
```

CI already has a `golden` job (`.github/workflows/ci.yml`) and it already runs
`pytest -q -m golden`; it will start covering this directory the moment the
generator writes into it.

## Rule for whoever fills it

The generator must live in `_tools/` alongside the data with a `--check` mode, the
same pattern as `fixtures/model/_tools/generate_golden_states.py` and
`fixtures/rules/_tools/generate_fixtures.py`. A golden set nobody can regenerate
becomes a golden set nobody dares change.
