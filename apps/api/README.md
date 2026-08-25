# `apps/api` — Garh AI API

FastAPI + SQLAlchemy 2 + Alembic + Postgres 15. Python 3.11.

This README covers the **data foundation**: schema, tenancy, configuration, logging.
The HTTP surface (`main.py`, routers, auth, Pydantic schemas) and the mirrored model
core (`garh_model/`) are documented by their own owners.

```
apps/api/
├── alembic.ini
├── pyproject.toml
├── migrations/
│   ├── env.py                       # sync engine, URL from settings (no creds in ini)
│   └── versions/0001_initial_schema.py
└── garh_api/
    ├── config.py                    # pydantic-settings, §18 env surface, fail-fast
    ├── db.py                        # async engine (API) + sync engine (alembic/workers)
    ├── logging.py                   # structlog JSON, request-id, Sentry hook
    ├── models.py                    # the 18 tables (playbook §2)
    ├── tenancy.py                   # TenantCtx + firm-scoped Repository base
    └── repositories/                # the only code allowed to touch tables
```

---

## Multi-tenancy: how it is enforced

Every tenant-owned row carries `firm_id`. That is necessary but not sufficient — the
guarantee comes from *where queries can be written*.

```
route handler ──► Repository(session, ctx) ──► SELECT ... WHERE firm_id = ctx.firm_id
                        ▲
                  TenantCtx from the verified JWT
```

`garh_api.tenancy.Repository`:

- **requires** a `TenantCtx` in its constructor — passing anything else raises
  `TenantContextRequiredError`;
- exposes exactly one query builder, `_scoped_select()`, which always appends the
  `firm_id` predicate. There is **no** method that returns an unfiltered query, and no
  public handle on the session;
- refuses to serve a table that is not marked `TenantOwned`;
- forces `firm_id` from the context on insert (`_new_row`), and raises
  `CrossTenantAccessError` if a caller passes a different one;
- returns `EntityNotFoundError` (→ **404**) for another firm's row, never 403 — a 403
  would confirm the row exists.

So the Phase 0 DoD test ("fetch another firm's project → 404") passes structurally, not
because someone remembered a `.where()`.

### The escape hatch

Cross-firm work is real (snapshot compaction, stale-render sweeps, queue metrics, the
seed script). There is exactly one sanctioned path:

```python
from garh_api.tenancy import system_unscoped_session

async with system_unscoped_session(
    task="snapshot_compaction",
    reason="fold snapshots for every project past 200 ops (playbook §2)",
) as session:
    ...
    await session.commit()
```

It requires `task=` and `reason=`, logs WARNING on open and close, and writes an
`audit_log` row under `SYSTEM_FIRM_ID` (`00000000-…-0000`) in its own transaction, so
the audit survives a rollback.

**CI lint** — keep both of these true:

```bash
# 1. the escape hatch never appears in request-serving code
! grep -rn "system_unscoped_session" garh_api/routers/

# 2. nothing outside the repository layer builds its own query
! grep -rnE "\b(select|insert|update|delete)\(" \
    --include='*.py' garh_api/routers/ garh_api/auth* 2>/dev/null
```

### Documented non-tenant repositories

Four classes take `(session)` and no `TenantCtx`, because no tenant context can exist
at that point (pre-auth) or the data is global config. Each is deliberately narrow and
cannot reach tenant content:

| Class | Why | Returns |
|---|---|---|
| `AuthDirectoryRepository` | `POST /auth/verify` must *discover* the firm from an email | `AuthPrincipal` only |
| `OtpCodeRepository` | OTP is issued before any `firm_id` exists | `OtpChallenge` (never the code) |
| `FlagRepository` | `flags` is global config read at boot (§18) | `{key: enabled}` |
| `ShareTokenResolver` | the public viewer presents only a token | `ResolvedShare` (ids + scope) |

Everything after those four goes through a scoped repository. A resolved share token
becomes `TenantCtx.for_share_viewer(...)`, a read-only role.

---

## Schema notes (playbook §2)

18 tables: `firms, users, projects, plots, briefs, design_versions, ops, solver_jobs,
render_jobs, sheets, annotations, compliance_reports, share_links, comments,
credit_events, audit_log` + `flags` (§18) + `otp_codes` (§13).

Invariants: `id uuid primary key default gen_random_uuid()` (pgcrypto),
`created_at`/`updated_at` everywhere — maintained by the `garh_set_updated_at`
`BEFORE UPDATE` trigger so raw-SQL writers from workers stay honest — and `firm_id` +
index on every tenant-owned table.

**Geometry is integer millimetres.** No float length exists in this schema; coordinates
and areas live inside JSONB documents as ints (`mm`, `mm²`). Display conversion
(ft-in / m / gaj) happens at the HTTP boundary only.

Load-bearing constraints:

| Constraint | Why it matters |
|---|---|
| `uq_ops_project_id_version_branch_idx` | makes the op append optimistically concurrent-safe → 409 → client rebases (§11) |
| `uq_ops_project_id_client_op_id` (partial) | idempotent replay of a retried append |
| `uq_plots_project_id`, `uq_briefs_project_id` | one plot / one brief per project |
| `ck_design_versions_snapshot_pair` | a `snapshot_hash` without its `snapshot` is a lie |
| `uq_share_links_token_hash` | tokens are stored hashed, looked up by hash |
| `ck_otp_codes_attempts_range` | the 5-attempt cap is in the database, not just in code |

Three deliberate additions to the §2 sketch, each forced by another section:

1. `ops.group_id` — §4 requires ops to carry an optional `groupId`; undo/redo operates
   on groups, and `solver.apply_option` is one group.
2. `design_versions.version_branch` — ops are keyed by `(project_id, version_branch,
   idx)`, so a version must name its branch or "snapshot + tail" is ambiguous once
   solver options fork.
3. `render_jobs.progress` / `render_jobs.error`, `share_links.created_by` — parity with
   the other job table and with §13's audit requirements.

`ops.seq` is an `IDENTITY` column (the SQL-standard successor to `bigserial`) so the
sequence is owned by the column and survives a table rewrite.

### Snapshots

The op log is the source of truth. `design_versions.snapshot` stores the folded state
every `OP_SNAPSHOT_INTERVAL` (200) ops and at every named version / applied solver
option; `snapshot_hash` is the sha256 of the canonical JSON. Fast open =
`latest_snapshot()` + `list_since(op_seq_end)`.

---

## Migrations

```bash
cd apps/api
alembic upgrade head          # apply
alembic downgrade base        # full teardown (drops the trigger fn and pgcrypto too)
alembic revision --autogenerate -m "add x"    # after editing models.py
```

`0001_initial_schema` is hand-written, not autogenerated, so constraint names, partial
indexes and the identity column match `models.py` exactly. **An autogenerate run
against a database at head should produce an empty diff** — if it does not, `models.py`
and the migration have drifted, and that is a bug worth fixing before shipping the next
revision.

---

## Configuration (§18)

All of `.env` is optional locally — defaults point at the compose stack:

| Var | Default | Notes |
|---|---|---|
| `ENV` | `dev` | `dev` / `test` / `staging` / `prod` |
| `DATABASE_URL` | `postgresql+psycopg://garh:garh@localhost:5432/garh` | any Postgres URL is normalised onto psycopg 3 |
| `REDIS_URL` | `redis://localhost:6379/0` | worker queues |
| `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` | empty | RS256 PEM; empty is dev-only |
| `S3_*` | minio dev creds | signed URLs ≤10 min |
| `PROVIDER_LLM` | `mock` | `anthropic` also needs `ANTHROPIC_API_KEY` |
| `PROVIDER_RENDER` | `mock` | `diffusers` + `RENDER_DEVICE=cpu\|cuda` |
| `PROVIDER_BILLING` | `mock` | `razorpay` also needs `RAZORPAY_KEY_*` |
| `APP_URL` | `http://localhost:5173` | share links, WhatsApp deep links |
| `LOG_FORMAT` | `json` | `console` for local readability |
| `OP_SNAPSHOT_INTERVAL` | `200` | §2 snapshot cadence |

**Fail-fast:** outside `dev`, `Settings` refuses to construct if a required secret is
missing *or still on a local default*, and the error names every offending variable.
Startup dies immediately rather than at the first request that needs a key.

```python
from garh_api.config import get_settings
settings = get_settings()   # cached; raises ConfigError on a bad non-dev env
```

---

## Sessions and transactions

```python
# request-scoped (FastAPI dependency; commits once if the handler returns normally)
async def handler(session: AsyncSession = Depends(get_db_session), ctx: TenantCtx = ...):
    return await ProjectRepository(session, ctx).list()

# worker / script
async with session_scope() as session: ...      # async
with sync_session_scope() as session: ...       # sync (alembic, seed, workers)
```

Repositories `flush()`; they never `commit()`. That is what lets one request append
ops, write a snapshot and record a credit event atomically.

---

## Logging (§18)

```python
configure_logging(settings)        # structlog JSON + stdlib bridge (uvicorn, SQLAlchemy)
init_error_reporting(settings)     # Sentry, only if SENTRY_DSN is set and the SDK exists
request_id = bind_request_context(request_id=..., method=..., path=...)
```

Every line inside a request carries `request_id`, and `firm_id`/`user_id` once auth has
resolved. `scrub_pii` drops known secret and PII keys (`email`, `otp`, `token`, …) —
§13 says logs and model summaries exclude PII, so log ids and email *domains*, never
addresses.

`sentry-sdk` is intentionally not a pinned dependency: the platform must run with zero
third-party telemetry. `init_error_reporting` is the seam.

---

## The op append path (§11)

```python
repo = OpRepository(session, ctx)
try:
    result = await repo.append(
        project_id, version_branch, base_idx,
        [NewOp(type="wall.add", payload={...}, client_op_id="c-17")],
        source="manual",
    )
except OpSequenceConflictError as exc:
    # → 409 with exc.as_problem(): {code, message, action, baseIdx, headIdx}
    ...
```

1. per-project advisory lock (`pg_advisory_xact_lock`) — the playbook's single writer;
2. idempotency check on `client_op_id` — a retry returns `already_applied=True`;
3. head check against `base_idx` → `OpSequenceConflictError`;
4. bulk insert; a unique-violation race also converts to the same typed conflict.

Never retry server-side: the ops may no longer be valid against the newer state. The
client fetches `GET /projects/:id/ops?since=base_idx`, rebases its optimistic queue,
and re-sends.

---

## Local checks

```bash
ruff check . && ruff format --check .
mypy garh_api
pytest                      # unit
alembic upgrade head && alembic downgrade base && alembic upgrade head   # migration round-trip
```
