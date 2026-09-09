# Billing — what is proven, what is traced, what is not

The ledger for plans, credits, the platform fee, rupee display, invoices, payments and
seats, in the same split the phase ledgers use. Updated 2026-09-09. Trust this over any
summary, including `docs/trial-readiness.md`'s bullets.

Every command below was run on a throwaway database (`garh_test_g`) and Redis index
(db 10); the api fixture TRUNCATES every table it is pointed at.

```bash
# api — from apps/api, PYTHONPATH=<repo>:<repo>/apps/api
DATABASE_URL=postgresql+asyncpg://garh:garh@localhost:5432/garh_test_g \
REDIS_URL=redis://127.0.0.1:6379/10 \
python -m pytest tests/test_billing_api.py tests/test_spend_cap.py tests/test_credit_refund.py \
  tests/test_billing_core.py tests/test_config_env.py tests/test_inr_display.py \
  tests/test_markup.py tests/test_cross_tenant.py tests/test_no_unscoped_queries.py -q
# web — from apps/web
pnpm exec vitest run src/features/billing src/lib
```

## EXECUTED

- **The platform fee, end to end.** 5 % boot default (`BILLING_MARKUP_PERCENT`), the
  owner's value in `platform_settings` read on every charge, recorded per row
  (`markup_bps`, `charged_micros`) beside the honest provider cost; changing it changes
  the next charge and never an old row. `GET /admin/billing/markup` says who set it
  (`updatedByEmail`, kept beside the value under `billing.markup_set_by`), when, and
  whether the caller may change it (`canSet`); the PUT is the gate (403 for an admin of
  another firm, an empty allowlist refuses everyone). `test_markup.py` — 27 passed,
  self-sufficient on a bare database (negative control: billing tables dropped, file
  run alone, green).
- **The owner's page.** `/platform/fee`: fee in force with provenance, a field held to
  the server's 0–100 / two-decimal contract before any round trip, a confirm stating
  the next-charge rule, the usage card's own wording re-read after a change, read-only
  for `canSet: false`, the 403 branch. `markup.test.ts` 28, `PlatformFeePage.test.tsx` 8.
- **Rupees at one dated rate.** `BILLING_USD_INR_RATE` / `_AS_OF`, validated at boot
  (Settings refuses `eighty-four` and `last tuesday`), served on the usage response
  beside the micro-USD integers and nothing rupee-shaped; the web converts with BigInt
  and one half-up rounding. `test_inr_display.py` 22 and `money.test.ts` 33 pin the SAME
  table of cases, so the two implementations cannot drift by a paisa without one going
  red. Cost + fee = charged adds up on screen because the fee is derived as the
  difference (`usage.test.ts`, the 59/120 µUSD case).
- **A 402 opens the Billing page.** `billingRouteFor` for every 402 and the three billing
  codes, with the 409/500 negative controls (`outOfCredits.test.ts` 11); the page's
  sections, the arrival banner, plan change through the confirm with its PUT body, the
  GST form (409 → empty → PUT body), issue → checkout → mock widget → verify as a
  sequence of POSTs, the 503 refusal shown, a member seeing no write
  (`BillingPage.test.tsx` 8).
- **The ledger an architect may audit.** `GET /billing/credit-events`: cost, fee, charge,
  provider, detail, refund; firm B sees only its own rows; `?kind=`.
  `test_billing_api.py::test_credit_events_list_cost_fee_charge_and_refunds_newest_first`.
- **The mock checkout widget.** `POST /billing/payments/mock` completes checkout → verify
  under the mock with a real HMAC; 404 `mock_checkout_unavailable` with Razorpay
  configured (negative control); 403 for a member.
- **Solver and export runs are priced.** `OWN_COMPUTE_KINDS`: the real route meta (no
  provider key) prices at `FLAT_PRICES`; a mock render, a mock LLM call, an LLM row with
  no provider and an explicit `provider: mock` solver all stay 0. `test_spend_cap.py`
  20, including the row landing through the repository with the fee on top.
- **`enforced` per usage line equals what is mounted.** `GATED_KINDS` = {solver, render,
  llm}; `test_gated_kinds_equals_what_is_mounted_in_both_directions` walks the live
  dependency graph and requires equality both ways; the free plan's export line reads
  `allowance 0, enforced false`.
- **Totals on 2026-09-09:** the api set above — **334 passed**; web
  `src/features/billing src/lib` — **224 passed** (113 in `features/billing`); `tsc`
  strict clean; eslint 0 errors; prettier clean; ruff check + format clean; `make bare`
  green with the four new `.env.example` variables.

## TRACED (read and reasoned, not executed)

- The quota gate on solver/render/llm over HTTP, refunds on failed/dead-lettered jobs,
  the spend cap, GST arithmetic and invoice numbering, seat gates — all executed in
  their own suites (part of the 331 above), but no browser journey drives Generate to a
  402 and follows the toast into the Billing page: the pieces are unit-proven on both
  sides of the wire, the join is traced.
- Under `PROVIDER_BILLING=razorpay` the web's Pay button opens the order and stops
  (`checkout.js` is not loaded); the copy says so. Traced from `BillingPage.payInvoice`;
  not executed against a gateway.
- The plan card and the usage card show the same allowance numbers because both read
  the catalogue through the API; no test renders both from one response.

## UNVERIFIED

- **Razorpay has never run live.** The adapter is pinned only against a strict
  `httpx.MockTransport` double. Settles with the four-step test-key rehearsal in
  `docs/ops-runbook.md` "Going live with Razorpay".
- **No webhook route.** `verify_webhook_signature` fails closed and nothing calls it; a
  payment completed after the browser closes never settles by itself. Building the
  route (verify → resolve firm from `notes` → `settle_payment`) is the fix; until then
  `RAZORPAY_WEBHOOK_SECRET` must stay empty and no webhook URL registered.
- **The export gate is not mounted.** The free plan's export allowance is 0 by design and
  the routes are ungated; the usage response now says so (`enforced: false`). The mount
  is `require_quota("export")` in the dependency lists of `POST /projects/{id}/export`
  (`routers/jobs.py`) and `POST /projects/{id}/renders/{pack}/archive`
  (`routers/renders.py`), the two `UNGATED_ON_PURPOSE` entries moved into
  `QUOTA_GATED_ROUTES`, `GATED_KINDS` gaining `export` — and the prerequisites that make
  it a decision rather than a patch: the demo seed and every live trial firm on a paid
  plan (self-serve from the Billing page now exists), `test_cross_tenant`'s export
  case, `test_job_rate_limits` (3 exports), `test_drawing_version_pin` (1) and
  `test_spend_cap`'s gate list put on a paid plan first. Settles with the api set above
  plus `tests/test_cross_tenant.py tests/test_job_rate_limits.py tests/test_drawing_version_pin.py`.
- **`sheets/generate` writes no credit event.** No quota, cap, refund or usage figure can
  see it; metering it is the prerequisite for any of those. Outside this job's scope
  (drawings).
- **The client pack meters `qty=len(shots)` and stays ungated** (a static `qty=1` gate
  would under-check); wants an in-handler `check_quota(qty=len(shots))`.
- **Seats are unreachable for a real practice** until a firm invite exists; the section
  lists what the API holds and says so.
- **No invoice document (PDF) and no credit notes.** The invoice is JSON with every Rule
  46 field; rendering it through the drawings PDF primitives is the next step.
