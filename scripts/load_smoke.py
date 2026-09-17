"""Load smoke: is the API still honest under concurrency?

Not a capacity plan — a tripwire. Two modes:

**Read-only (default).** N concurrent clients against ``/healthz`` for a fixed
duration; latency percentiles and the error rate. Cannot trip an auth limit or
write a row, so it is safe against any stack::

    python scripts/load_smoke.py --base-url http://localhost:8000 --clients 20 --seconds 15

**The product path (``--journey``).** Each client signs up its own firm, then
repeats the loop an architect actually runs — create a project from a ready-made
plan, read its compliance report, generate the municipal sheet set (a real job
through the drawings worker), download one sheet — and every step is timed on its
own, p50/p95/max per step, so a regression in one of them cannot hide in an
average. It WRITES: firms, projects, jobs, objects in the bucket. Point it at a
scratch stack (``docs/ops-runbook.md`` says how), never at production::

    python scripts/load_smoke.py --journey --base-url http://localhost:8114 \
        --clients 5 --iterations 8

Needs ``DEV_ECHO_OTP`` on the target (the sign-in code comes back in the response)
and ``TRUSTED_PROXY_HOPS=1`` so each client's ``X-Forwarded-For`` gets its own
per-IP auth budget. Sheet generation is capped per firm per hour
(``RATE_LIMIT_EXPORT_JOBS_PER_HOUR``, 40); each client is a firm, so
``--iterations`` above that would be a 429, not a finding.

Deliberately dependency-light: httpx is already a project dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import httpx

DEFAULT_PATHS = ("/healthz",)


async def _client_loop(
    base_url: str,
    paths: tuple[str, ...],
    deadline: float,
    latencies_ms: list[float],
    errors: list[str],
) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        i = 0
        while time.monotonic() < deadline:
            path = paths[i % len(paths)]
            i += 1
            start = time.perf_counter()
            try:
                response = await client.get(path)
                elapsed_ms = (time.perf_counter() - start) * 1000
                if response.status_code >= 400:
                    errors.append(f"{path} -> {response.status_code}")
                else:
                    latencies_ms.append(elapsed_ms)
            except httpx.HTTPError as exc:  # connect refused, timeout, reset
                errors.append(f"{path} -> {type(exc).__name__}")


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    index = min(len(sorted_values) - 1, round(q * (len(sorted_values) - 1)))
    return sorted_values[index]


async def _run(args: argparse.Namespace) -> int:
    paths = tuple(args.path) if args.path else DEFAULT_PATHS
    deadline = time.monotonic() + args.seconds
    latencies_ms: list[float] = []
    errors: list[str] = []

    started = time.monotonic()
    await asyncio.gather(
        *(
            _client_loop(args.base_url, paths, deadline, latencies_ms, errors)
            for _ in range(args.clients)
        )
    )
    wall = time.monotonic() - started

    total = len(latencies_ms) + len(errors)
    if total == 0:
        print("no requests completed — is the server up?", file=sys.stderr)
        return 2

    latencies_ms.sort()
    rps = total / wall if wall > 0 else float("nan")
    print(f"requests   {total}  ({rps:.0f} req/s, {args.clients} clients, {wall:.1f}s)")
    print(f"errors     {len(errors)}  ({100 * len(errors) / total:.2f}%)")
    if latencies_ms:
        print(
            "latency ms "
            f"p50={_percentile(latencies_ms, 0.50):.1f}  "
            f"p95={_percentile(latencies_ms, 0.95):.1f}  "
            f"p99={_percentile(latencies_ms, 0.99):.1f}  "
            f"max={latencies_ms[-1]:.1f}  "
            f"mean={statistics.fmean(latencies_ms):.1f}"
        )
    if errors:
        # First few distinct failures — enough to see the shape without a dump.
        seen: dict[str, int] = {}
        for e in errors:
            seen[e] = seen.get(e, 0) + 1
        for label, count in sorted(seen.items(), key=lambda kv: -kv[1])[:5]:
            print(f"  {count:>5}x {label}", file=sys.stderr)

    threshold = args.max_error_rate / 100
    if len(errors) / total > threshold:
        print(f"FAIL: error rate above {args.max_error_rate}%", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# The product path
# ---------------------------------------------------------------------------

JOURNEY_STEPS = (
    "signIn",
    "createProject",
    "readCompliance",
    "generateSheets",
    "downloadSheet",
)

#: The ready-made plan every client starts from: a solved, compliant 30×40 house,
#: so compliance has a design to report on and the sheet set has walls to draw.
DEFAULT_TEMPLATE = "blr-30x40-g1-3bhk"
SHEETS_TIMEOUT_SECONDS = 180.0
SHEETS_POLL_SECONDS = 0.5


class StepFailed(Exception):
    def __init__(self, step: str, detail: str) -> None:
        super().__init__("%s: %s" % (step, detail))
        self.step = step
        self.detail = detail


class Timings:
    """Per-step latencies and failures, filled concurrently by every client."""

    def __init__(self) -> None:
        self.ms: dict[str, list[float]] = {step: [] for step in JOURNEY_STEPS}
        self.errors: dict[str, list[str]] = {step: [] for step in JOURNEY_STEPS}

    def record(self, step: str, elapsed_ms: float) -> None:
        self.ms[step].append(elapsed_ms)

    def fail(self, step: str, detail: str) -> None:
        self.errors[step].append(detail)


def _expect(response: httpx.Response, step: str, *codes: int) -> dict:
    if response.status_code not in codes:
        raise StepFailed(
            step, "%s -> %d %s" % (response.url.path, response.status_code, response.text[:160])
        )
    return response.json() if response.content else {}


async def _journey_client(
    index: int,
    base_url: str,
    api_prefix: str,
    iterations: int,
    template: str,
    timings: Timings,
) -> None:
    # One fake source address per client: each gets its own per-IP auth budget
    # (the target must run TRUSTED_PROXY_HOPS=1 for the header to count).
    headers = {"x-forwarded-for": "10.9.%d.%d" % (index // 250, index % 250 + 1)}
    email = "load-%d-%d@load.garh.test" % (index, int(time.time()))
    async with httpx.AsyncClient(
        base_url=base_url + api_prefix, timeout=30.0, headers=headers, follow_redirects=False
    ) as client:
        # -- signIn: sign up a firm, verify the echoed code -------------------
        started = time.perf_counter()
        try:
            issued = _expect(
                await client.post(
                    "/auth/signup",
                    json={
                        "firmName": "Load Studio %d" % index,
                        "name": "Load Tester",
                        "email": email,
                    },
                ),
                "signIn",
                201,
            )
            code = issued.get("devCode")
            if not code:
                raise StepFailed("signIn", "no devCode in the signup response — DEV_ECHO_OTP off?")
            session = _expect(
                await client.post("/auth/verify", json={"email": email, "code": code}),
                "signIn",
                200,
            )
            token = session["accessToken"]
        except StepFailed as exc:
            timings.fail(exc.step, exc.detail)
            return
        except httpx.HTTPError as exc:
            timings.fail("signIn", type(exc).__name__)
            return
        timings.record("signIn", (time.perf_counter() - started) * 1000)
        auth = {"authorization": "Bearer %s" % token}

        for iteration in range(iterations):
            try:
                await _journey_iteration(client, auth, template, index, iteration, timings)
            except StepFailed as exc:
                timings.fail(exc.step, exc.detail)
            except httpx.HTTPError as exc:
                timings.fail("createProject", type(exc).__name__)


async def _journey_iteration(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    template: str,
    index: int,
    iteration: int,
    timings: Timings,
) -> None:
    prefix = client.base_url.path.rstrip("/")

    # -- createProject --------------------------------------------------------
    started = time.perf_counter()
    project = _expect(
        await client.post(
            "/projects",
            json={"name": "Load %d/%d" % (index, iteration), "templateId": template},
            headers=auth,
        ),
        "createProject",
        201,
    )
    project_id = project["id"]
    timings.record("createProject", (time.perf_counter() - started) * 1000)

    # -- readCompliance -------------------------------------------------------
    started = time.perf_counter()
    report = _expect(
        await client.get("/projects/%s/compliance" % project_id, headers=auth),
        "readCompliance",
        200,
    )
    if "results" not in report and "summary" not in report:
        raise StepFailed("readCompliance", "no results in the report")
    timings.record("readCompliance", (time.perf_counter() - started) * 1000)

    # -- generateSheets: queue, then poll until the worker has drawn them -------
    started = time.perf_counter()
    _expect(
        await client.post("/projects/%s/sheets/generate" % project_id, json={}, headers=auth),
        "generateSheets",
        202,
    )
    deadline = time.monotonic() + SHEETS_TIMEOUT_SECONDS
    sheets: list[dict] = []
    while time.monotonic() < deadline:
        listing = _expect(
            await client.get("/projects/%s/sheets" % project_id, headers=auth),
            "generateSheets",
            200,
        )
        sheets = [s for s in listing.get("sheets", []) if s.get("artifacts", {}).get("svg")]
        if sheets:
            break
        await asyncio.sleep(SHEETS_POLL_SECONDS)
    if not sheets:
        raise StepFailed(
            "generateSheets", "no sheet with an SVG within %ds" % SHEETS_TIMEOUT_SECONDS
        )
    timings.record("generateSheets", (time.perf_counter() - started) * 1000)

    # -- downloadSheet: the signed link the listing carries, followed to the bytes -
    # `artifacts.svg` is already `/downloads/<signed token>`: the api answers it
    # with a 307 to the object store (routers/jobs.py: redirect, never proxy), so the
    # step is the redirect plus the object fetch — what a browser does on the click.
    started = time.perf_counter()
    artifact_path = sheets[0]["artifacts"]["svg"]
    if prefix and artifact_path.startswith(prefix):
        artifact_path = artifact_path[len(prefix) :]
    async with httpx.AsyncClient(
        base_url=str(client.base_url), timeout=30.0, follow_redirects=True
    ) as store:
        blob = await store.get(artifact_path)
    if blob.status_code != 200 or not blob.content:
        raise StepFailed("downloadSheet", "%s -> %d" % (artifact_path, blob.status_code))
    timings.record("downloadSheet", (time.perf_counter() - started) * 1000)


def _render_step(step: str, values: list[float], errors: list[str]) -> str:
    if not values:
        return "%-16s %5d ok %5d err   —" % (step, 0, len(errors))
    ordered = sorted(values)
    return "%-16s %5d ok %5d err   p50=%8.1f  p95=%8.1f  max=%8.1f  mean=%8.1f ms" % (
        step,
        len(values),
        len(errors),
        _percentile(ordered, 0.50),
        _percentile(ordered, 0.95),
        ordered[-1],
        statistics.fmean(ordered),
    )


async def _run_journey(args: argparse.Namespace) -> int:
    timings = Timings()
    started = time.monotonic()
    await asyncio.gather(
        *(
            _journey_client(
                i, args.base_url, args.api_prefix, args.iterations, args.template, timings
            )
            for i in range(args.clients)
        )
    )
    wall = time.monotonic() - started
    print(
        "journey    %d clients × %d iterations, template %s, %.1fs wall"
        % (args.clients, args.iterations, args.template, wall)
    )
    total_ok = total_err = 0
    for step in JOURNEY_STEPS:
        print(_render_step(step, timings.ms[step], timings.errors[step]))
        total_ok += len(timings.ms[step])
        total_err += len(timings.errors[step])
    for step in JOURNEY_STEPS:
        seen: dict[str, int] = {}
        for e in timings.errors[step]:
            seen[e] = seen.get(e, 0) + 1
        for label, count in sorted(seen.items(), key=lambda kv: -kv[1])[:3]:
            print("  %5dx %s: %s" % (count, step, label), file=sys.stderr)
    if total_ok == 0:
        print(
            "no step completed — is the stack up, with DEV_ECHO_OTP and a drawings worker?",
            file=sys.stderr,
        )
        return 2
    threshold = args.max_error_rate / 100
    if total_err / (total_ok + total_err) > threshold:
        print("FAIL: error rate above %s%%" % args.max_error_rate, file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--clients", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument(
        "--path",
        action="append",
        help="endpoint path to hit (repeatable; default /healthz)",
    )
    parser.add_argument(
        "--max-error-rate",
        type=float,
        default=1.0,
        help="fail (exit 1) above this error percentage",
    )
    parser.add_argument(
        "--journey",
        action="store_true",
        help="the product path: sign up → project from a template → compliance → sheets → download",
    )
    parser.add_argument("--iterations", type=int, default=5, help="journey loops per client")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, help="ready-made plan id")
    parser.add_argument("--api-prefix", default="/api/v1")
    args = parser.parse_args()
    if args.journey:
        return asyncio.run(_run_journey(args))
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
