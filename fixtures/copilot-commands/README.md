# `fixtures/copilot-commands/` — copilot evaluation corpus

**Status: this directory holds no fixture files yet, and it does not need to for
the mock provider to work.** The shipped corpus lives *inside the package that
reads it* — [`services/llm/fixtures/`](../../services/llm/fixtures/) — and this
directory is the documented **override** location. Phase 6 fills it with the
40-command evaluation set.

## How the two locations relate

`services/llm/mock.py` loads, in order:

1. `services/llm/fixtures/{brief-parse,copilot-commands,rationales}.json` — always,
   and they are validated against the same JSON Schemas the real provider's output
   must satisfy (`services/llm/schemas.py`);
2. `${LLM_FIXTURE_DIR}/<same filenames>` — merged over the built-ins **if the
   directory exists**.

`LLM_FIXTURE_DIR` defaults to `fixtures/copilot-commands` (see
`services/common/config.py`), which is why this directory exists at all: without
it the documented default pointed at nothing. An absent override directory is
handled, not an error — the built-in corpus is what makes `PROVIDER_LLM=mock`
work on a fresh clone with zero API keys.

## What Phase 6 puts here

Phase 6's DoD: *40-command eval fixture set: ≥90% of in-scope commands produce
valid applicable diffs with mock LLM fixtures + prompt-contract tests for the real
provider; zero ops bypass validation.*

So: `copilot-commands.json`, keyed by the command text, each entry carrying the
expected outcome — one of

| Outcome | Meaning |
|---|---|
| `ops` | typed ops from the 32-op taxonomy, which then go through dry-run fold + rules check + diff preview |
| `needsClarification` | the ask is in scope but under-specified |
| `cannotDo` | out of scope, answered honestly and logged (golden rule 9) — **this is also where prompt-injection attempts must land** |

## The invariant this corpus defends

**LLM output only ever becomes validated ops.** Never text that is executed,
never coordinates. `services/llm/copilot.py` runs every proposal through a
`DryRunFolder` before a human sees a diff, and the shipped corpus already
includes an injection case that must resolve to `cannotDo`.

Keep at least one injection fixture here for every new capability the copilot
gains. An eval set that only contains well-behaved commands measures the wrong
thing.

## Not to be confused with

* [`fixtures/briefs/`](../briefs/) — the 20-brief corpus for brief parsing and the
  solver, which is populated.
* `services/llm/fixtures/brief-parse.json` — the brief-parse half of the mock
  corpus, which is populated and validated.
