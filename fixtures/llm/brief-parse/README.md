# `fixtures/llm/brief-parse/` — the brief-parse eval corpus

Twelve realistic Indian client briefs (Hinglish welcome) with the exact structured
parse each must produce. One file per brief:

```json
{
  "id": "brief-parse-01-hinglish-3bhk-pooja",
  "text": "3BHK, pooja room chahiye, budget 60 lakh, plot 30x40 north facing",
  "expected": {
    "brief":       { "...": "the Brief object — counts, flags, preferences. NO geometry." },
    "assumptions": [ { "field": "...", "value": "...", "reason": "..." } ],
    "stated":      [ "brief.rooms", "brief.budgetInr" ],
    "unclear":     [ "..." ]
  }
}
```

## What this corpus is for — two jobs at once

1. **The mock's eval set.** `expected` is the byte-exact output of the deterministic
   keyword parser (`services/llm/brief_mock.py`) that powers `PROVIDER_LLM=mock`.
   `apps/api/tests/test_brief_parse.py` replays every `text` through the mock provider
   and asserts equality — the demo path is pinned, not hoped for.
2. **The contract tests for the real provider.** Every `expected` is validated against
   `BRIEF_PARSE_SCHEMA` (`services/llm/schemas.py`), the same schema the Anthropic
   structured-output call is held to. A shape a real model would not be allowed to
   return cannot live here.

## The product rule these fixtures exercise

**Anything not stated → assumption, never silence** (golden rule 4). `stated` and
`assumptions` must partition the brief: every field carries either a dotted path in
`stated` (the client said it) or an entry in `assumptions` with a plain-language
reason (we filled it in). The tests assert nothing falls between the two lists.

Also pinned by construction:

* **No geometry.** Plot mentions ("30x40 north facing") become `unclear` notes
  pointing at the plot step — the brief carries programme, never shape.
* **Integer money.** `budgetInr` is whole rupees ("60 lakh" → `6000000`). Never a
  float, never a unit string.
* **Closed room list.** Every `rooms[].type` is a model `RoomType` — the schema's
  enum is generated from `packages/model/schema/common.schema.json`.

## Regenerating (a deliberate act)

This corpus is DERIVED — house rule 2 of `fixtures/README.md`:

```sh
python3 fixtures/llm/brief-parse/_tools/generate.py           # rewrite from the parser
python3 fixtures/llm/brief-parse/_tools/generate.py --check   # CI drift gate
```

Changing `services/llm/brief_mock.py` changes parser output; regenerate in the same
commit and read the diff — that diff *is* the review of the behaviour change. To add
a brief, add an `(id, text)` row to `TEXTS` in `_tools/generate.py` and regenerate.
