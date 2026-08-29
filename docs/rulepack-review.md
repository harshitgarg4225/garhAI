# Reviewing a Garh AI rule pack

**For the empanelled architect doing the review.** One city, one sitting at a time.
Nothing here needs programming.

---

## What you are being asked to do

Garh AI checks a house design against the National Building Code and your city's
building bye-laws, and prints the result next to a citation. Every one of those
numbers — 118 of them today, across NBC, Bengaluru, Delhi NCR, Hyderabad and a
Vastu advisory pack — was **drafted by the Garh AI team from secondary summaries**.
Nobody has opened the bye-law and checked one of them.

The software says so. Each number carries the word `seed`, the compliance panel
shows a caution marker beside it, and every export carries a disclaimer. That is
honest, and it is also useless to you: a caution marker on every figure is a
caution marker on nothing.

Your job is to turn `seed` into a number an architect can put on a submission
drawing. You do that one value at a time, with the primary document open, and you
sign each one.

---

## Before you start

You need three things. There is no way around any of them.

1. **The primary document itself** — the actual bye-laws as notified, plus any
   amendment in force, and the NBC volume for the national rules. Not a summary,
   not a consultant's presentation, not a PDF someone forwarded. If the office
   does not have it, that is the first thing to buy.
2. **Your Council of Architecture registration number**, as printed on your
   certificate: `CA/2011/52341`, or `CA/97/21473` if you registered before 2000.
   It goes on every value you sign. This is the point of the exercise — an
   architect downstream is relying on a named professional, not on a company.
3. **The pack**, printed or on screen: `rulepacks/blr.json`, `ncr.json`,
   `hyd.json`, `nbc-core.json` or `vastu.json`. Ask the Garh team for the review
   sheet — it is the same content laid out one rule per row.

---

## The three states a number can be in

| State        | What it means                                                                                                                                         | Shown to the architect as                   |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| **seed**     | Drafted by the Garh AI team from secondary summaries. Where every value starts.                                                                       | Caution marker. Not authoritative.          |
| **reviewed** | _You_ read the value and its clause in the primary document and signed.                                                                               | No caution marker. **Authoritative.**       |
| **verified** | Reviewed, **and** confirmed against a real outcome — a project sanctioned under this value, or a written confirmation from the sanctioning authority. | Authoritative, with the sanction reference. |

There is no rung for "the office has the document but nobody has read it yet".
That was considered and left out on purpose: `confidence` is the word an architect
sees next to a number they are about to submit, so it has to mean exactly one
thing — _has a registered professional put their name on this_. Whether the office
holds the primary document is tracked separately, on the pack's `sources` list.

**A pack is only as good as its weakest rule.** The pack as a whole cannot be
called `reviewed` until every rule in it is.

---

## The loop, one rule at a time

For each rule, you are checking five things. Four of them are not the number.

1. **The value.** Is 2 m the front setback the bye-law actually states?
2. **The band.** The rule says it applies to plots over 120 m² and up to 240 m².
   Does the table band the same way? Bands that are one square metre off are the
   most common drafting error, and they are invisible until a plot lands on the
   boundary.
3. **The citation.** "Table 6 — Setbacks for residential plots" must be where you
   actually read it. This is what gets printed on the submission set.
4. **The severity.** `fail` means the design generator will refuse options that
   break it. `warn` means advice. A statutory minimum is `fail`; a good-practice
   recommendation is `warn`.
5. **The wording.** The message is what a client and a junior sees. It should
   read like a colleague, not a compiler.

Then one of three things happens.

### The seeded number is right

Mark it `confirmed`. Note the clause you read it in.

### The seeded number is wrong

Correct it, mark it `corrected`, and **record what it used to say**. A compliance
report issued last month pinned the old value; if nobody wrote down what it was,
that report can never be explained again. The software will not accept a
correction without it.

### The rule is structurally wrong

A band that needs splitting in two, a rule that should not exist, a requirement
the pack cannot express (Karnataka's `height ≤ 1.5 × (road width + front setback)`
is the known example — the packs cannot do formulas yet). **Do not repurpose the
existing rule.** A rule id means one thing forever, because six-month-old
compliance reports reference it. Flag it, and the Garh team retires the id and
adds a new one.

### You cannot find the clause

Say so. That is a finding, and a valuable one — it usually means the rule was
invented from a summary and should be deleted, not that you looked in the wrong
place. Do not sign a value you could not locate. Leave it `seed` with a note.

### Two rules seem to contradict each other

They probably do not. Minimums **stack**: city setback tables are indexed by plot
size _and_ by road width, and the pack encodes them as two separate families whose
larger requirement governs. That is deliberate. If two rules genuinely conflict
(a floor count that cannot fit under a height cap, say), flag it — the build
already refuses that particular combination and found a real error in the
Hyderabad tables that way.

---

## What you sign

Each value you confirm gets a record like this. The Garh team types it; you check
it reads back correctly.

```json
{
  "id": "blr.setback.front.plot.121-240",
  "confidence": "reviewed",
  "review": {
    "reviewer": "R. Iyer",
    "coaNumber": "CA/2011/52341",
    "reviewedAt": "2026-09-14",
    "source": "BBMP Building Bye-laws 2020",
    "clause": "Table 6, row 121-240 m2",
    "outcome": "confirmed"
  }
}
```

A correction adds one line:

```json
    "outcome":       "corrected",
    "previousValue": "2000 mm front setback"
```

`verified` adds the real-world evidence:

```json
    "verification": {
      "kind":      "sanctioned-drawing",
      "reference": "BBMP/ADD/0921/2025-26",
      "date":      "2026-10-02"
    }
```

And your name goes once at the top of the pack, on the roster:

```json
"review": {
  "status": "in-review",
  "reviewers": [
    { "name": "R. Iyer",
      "role": "Empanelled architect, Bengaluru",
      "coaNumber": "CA/2011/52341",
      "signedAt": "2026-09-14" }
  ],
  "lastReviewedAt": "2026-09-14",
  "nextReviewDue":  "2027-09-14"
}
```

Set `status` to `in-review` on the day the pack is assigned to you, and to
`reviewed` only when the last rule is done.

---

## What the software will refuse

These are not style preferences. The build fails and the pack does not ship.

- A value marked `reviewed` or `verified` with **no review record**. One word
  changed in a file is not a review.
- A record with **no clause**, or no source, or no date, or no CoA number.
- A record naming **a document the pack lists as not obtained**. You cannot read
  a clause out of a book nobody has. Mark the source `obtained: true` when it is
  on your desk.
- A record signed by **someone not on the pack's roster**, or whose CoA number
  does not match their roster entry.
- A review **dated in the future**.
- A correction with **no previous value**.
- `verified` with **no sanction number or municipal confirmation**.
- A pack calling itself `reviewed` while any rule in it is still `seed`.
- The pack list the app serves (`rulepacks/index.json`) **claiming more than the
  packs themselves do** — that manifest is what labels every citation in the UI,
  so it is checked against the packs on every build.

Two commands run all of that:

```
python3 scripts/rulepack_review.py coverage   # progress, per pack and per city
python3 scripts/rulepack_review.py verify     # the gate; non-zero exit on findings
```

`coverage` is the one to watch during a review programme:

```
pack          city          rules   seed    reviewed   verified   authoritative   status        due
nbc-core      national      23      23      0          0          0%              unreviewed    -
blr           Bengaluru     33      33      0          0          0%              unreviewed    -
ncr           Delhi         19      19      0          0          0%              unreviewed    -
hyd           Hyderabad     34      34      0          0          0%              unreviewed    -
vastu         advisory      9       9       0          0          0%              unreviewed    -
TOTAL                       118     118     0          0          0%
```

---

## Finishing a pack

1. Every rule `reviewed` (or explicitly left `seed` with a note saying why).
2. Roster entry signed and dated.
3. `status` moved to `reviewed`; `lastReviewedAt` set; `nextReviewDue` set —
   twelve months, or sooner if an amendment is in circulation.
4. The Garh team regenerates the test fixtures and **reads the diff with you**.
   Every value you moved shifts exactly two fixtures, and those two lines are the
   clearest possible record of what changed and by how much. If a fixture moved
   that you did not expect, something else changed too.
5. The pack version becomes the review month, e.g. `2026.09`. Compliance reports
   pin the version, so a report issued today stays explainable by exactly the
   rules that produced it.

## Re-review

`nextReviewDue` is not decorative. Bye-laws are amended, sometimes quietly. A pack
past its due date shows as stale in `coverage`, and the app should say so next to
its citations. Re-review on the date, and immediately on any amendment you hear
about — you will hear about them before we do.
