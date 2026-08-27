# Environment reference

`.env.example` is the annotated source of truth — it carries a comment per variable
and is what you copy. This page covers the things a flat list can't: the naming
contract, the client/server boundary, and the fail-fast behaviour.

```bash
cp .env.example .env      # or: make env
```

Every value ships with a working local default. A fresh clone needs **zero secrets**.

---

## The naming contract (read this first)

`apps/api/garh_api/config.py` reads the environment through `pydantic-settings` with
`extra="ignore"`. That means:

> **A variable name that doesn't match a settings field is silently discarded, and
> the field keeps its default. No error. No warning.**

So a `.env` that looks correct can be doing nothing. The env var is the
`UPPER_SNAKE` of the field name unless a `validation_alias` says otherwise. If you
add or rename a variable, change `config.py` in the same commit.

Names that are easy to get wrong because the playbook's prose differs from the
implementation:

| Correct                       | Not                                   | Note                                                                                                                                                                                                                                             |
| ----------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `S3_ENDPOINT_URL`             | `S3_ENDPOINT`                         |                                                                                                                                                                                                                                                  |
| `S3_ACCESS_KEY_ID`            | `S3_ACCESS_KEY`                       |                                                                                                                                                                                                                                                  |
| `S3_SECRET_ACCESS_KEY`        | `S3_SECRET_KEY`                       |                                                                                                                                                                                                                                                  |
| `DB_POOL_SIZE`                | `DATABASE_POOL_SIZE`                  | `DATABASE_URL` itself is right                                                                                                                                                                                                                   |
| `SQL_ECHO`                    | `LOG_SQL`                             |                                                                                                                                                                                                                                                  |
| `ACCESS_TOKEN_TTL_SECONDS`    | `JWT_ACCESS_TTL_SECONDS`              |                                                                                                                                                                                                                                                  |
| `REFRESH_TOKEN_TTL_SECONDS`   | `JWT_REFRESH_TTL_SECONDS`             |                                                                                                                                                                                                                                                  |
| `OTP_CODE_LENGTH`             | `OTP_LENGTH`                          |                                                                                                                                                                                                                                                  |
| `SHARE_TOKEN_BYTES`           | `SHARE_LINK_TOKEN_BYTES`              |                                                                                                                                                                                                                                                  |
| `CORS_ALLOW_ORIGINS`          | `CORS_ALLOWED_ORIGINS`                | comma-separated, never `*`                                                                                                                                                                                                                       |
| `OP_SNAPSHOT_INTERVAL`        | `SNAPSHOT_EVERY_N_OPS`                |                                                                                                                                                                                                                                                  |
| `RATE_LIMIT_AUTH_PER_HOUR`    | `RATE_LIMIT_AUTH_OTP_PER_HOUR_PER_IP` |                                                                                                                                                                                                                                                  |
| `RENDER_CONCURRENCY_PER_FIRM` | `RENDER_MAX_CONCURRENT_PER_FIRM`      |                                                                                                                                                                                                                                                  |
| `MAX_DXF_UPLOAD_BYTES`        | `UPLOAD_MAX_DXF_BYTES`                | `[both]` — the API rejects at the edge, the drawings worker re-checks after fetching the blob. One variable on purpose: it used to be hard-coded in `services/drawings/handler.py`, so raising it made the API accept a file the worker refused. |

`APP_ENV` is a validated literal: **`dev` | `test` | `staging` | `prod`**. Anything
else — including `local` — fails validation and the API won't boot.

---

## Who reads what

`.env.example` marks every variable:

| Marker     | Meaning                                                 |
| ---------- | ------------------------------------------------------- |
| `[server]` | the API process                                         |
| `[worker]` | solver / render / drawings                              |
| `[both]`   | API and workers                                         |
| `[client]` | `VITE_`-prefixed — **compiled into the browser bundle** |

### The client boundary is enforced, not advisory

Anything not marked `[client]` must never appear in `apps/web/`. Browser code reads
`import.meta.env.VITE_*` and nothing else.

```bash
make secret-audit
```

fails on two things: a non-`VITE_` `import.meta.env` access, and a reference to any
known secret variable name anywhere under `apps/web/` (including a built `dist/`).
CI runs it in the `lint` job, and pre-commit runs it on changes under `apps/web/`.

`VITE_SENTRY_DSN` is fine — a browser DSN is public by design. A bare `SENTRY_DSN`
in client code is not, which is why the check keys on a non-`VITE_` prefix rather
than on the bare name.

---

## Fail-fast outside `dev`

When `APP_ENV` is anything but `dev`, `config.py` validates at boot and raises
`ConfigError` listing everything still on a local default:

- `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` must be set
- `DATABASE_URL`, `REDIS_URL` must not be the dev values
- `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` must not be the local MinIO credentials
- `APP_URL` must be the real public URL
- `ANTHROPIC_API_KEY` required when `PROVIDER_LLM=anthropic`
- `RAZORPAY_KEY_ID` required when `PROVIDER_BILLING=razorpay`

A misconfigured deploy therefore fails to start, rather than failing later on one
unlucky request.

---

## JWT keys (§13)

RS256, not HS256 — so the read-only share-link surface can verify with the public
key alone and never holds signing material.

**The keys are passed inline**, as a single-line PEM with a literal `\n` between
lines. `config.py`'s `_normalise_pem` validator turns those two characters back into
real newlines. There is no `*_FILE` variant.

```bash
make dev-keys     # writes .keys/*.pem AND sets both vars in .env
```

Leaving `JWT_PRIVATE_KEY` empty is also fine: `docker compose up` mints a throwaway
dev keypair on first boot, which is what keeps the quickstart a single command. Pin a
real pair with `make dev-keys` when you need host-run tests and the container to
agree on tokens.

To do it by hand:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out .keys/jwt-private.pem
openssl rsa -in .keys/jwt-private.pem -pubout -out .keys/jwt-public.pem
awk 'BEGIN{ORS="\\n"}1' .keys/jwt-private.pem     # the single-line form to paste
```

`.keys/` and `.env` are both gitignored, and a `detect-private-key` pre-commit hook
catches an accidental paste into a tracked file.

---

## Providers — mock by default

| Variable           | Default | Real option                                            |
| ------------------ | ------- | ------------------------------------------------------ |
| `PROVIDER_LLM`     | `mock`  | `anthropic` (+ `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`) |
| `PROVIDER_RENDER`  | `mock`  | `diffusers` (+ `RENDER_DEVICE=cuda`)                   |
| `PROVIDER_BILLING` | `mock`  | `razorpay` (+ keys)                                    |

The mocks are deterministic, not stubs: the LLM mock is fixture-driven and the render
mock composites the real viewport with a preset tint and watermark, seeded so the
same request produces the same image. The whole product — including e2e — runs green
on mocks with no API keys and no GPU.

### The render weights allowlist is a licence gate

`RENDER_MODEL_ALLOWLIST` is asserted against `RENDER_MODEL_ID` by the worker. It
exists to make one specific mistake impossible: **FLUX.1-dev is non-commercial and
must never be used.** Permitted are SDXL (OpenRAIL, commercial OK), FLUX.1-**schnell**
(Apache-2.0) and Qwen-Image (Apache-2.0). Never add RPLAN-derived weights.

---

## Ports

All published ports are env vars, so a conflict is a one-line fix and nothing
container-internal changes:

| Variable             | Default |
| -------------------- | ------- |
| `API_PORT`           | 8000    |
| `WEB_PORT`           | 5173    |
| `WEB_HMR_PORT`       | 24678   |
| `POSTGRES_PORT`      | 5432    |
| `REDIS_PORT`         | 6379    |
| `MINIO_PORT`         | 9000    |
| `MINIO_CONSOLE_PORT` | 9001    |

---

## Container vs host DSNs

Inside the network, services resolve each other by name (`postgres`, `redis`,
`minio`). From your laptop, only `localhost` resolves. Both forms are in `.env`:

| In-container      | Host equivalent          |
| ----------------- | ------------------------ |
| `DATABASE_URL`    | `DATABASE_URL_HOST`      |
| `REDIS_URL`       | `REDIS_URL_HOST`         |
| `S3_ENDPOINT_URL` | `S3_PUBLIC_ENDPOINT_URL` |

`S3_PUBLIC_ENDPOINT_URL` is not just convenience: signed download URLs are generated
for a **browser** to fetch, so they must be signed against the host-visible origin,
not the in-cluster one.

---

## Feature flags

`FLAG_*` variables are boot defaults for the `flags` table (§18). The MVP cut lines
are encoded here — `FLAG_INTERIOR_PRECISE` and `FLAG_PDF_TRACE_IMPORT` are v1.1
(spec D5), `FLAG_MULTIPLAYER` is v2.5 (D12) — so a flag flipped on early is a
scope change, not a toggle.

---

## Adding a variable

1. Add the field to `apps/api/garh_api/config.py` (typed, with a default).
2. Add it to `.env.example` with a comment and a `[server]`/`[worker]`/`[both]`/`[client]` marker.
3. If a container needs it, add it to `docker-compose.yml` — the `x-backend-env` or
   `x-provider-env` anchor, with a `${VAR:-default}` so the stack still comes up with
   no `.env` at all.
4. If it's client-visible, prefix it `VITE_` and be certain it's safe to publish.
5. Run `make secret-audit`.
