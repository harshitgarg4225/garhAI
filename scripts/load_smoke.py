"""Load smoke: is the API still honest under concurrency?

Not a capacity plan — a tripwire. It opens N concurrent clients against
read-only endpoints for a fixed duration and reports latency percentiles and
the error rate. Run it before and after infrastructure changes; a p95 that
jumps an order of magnitude, or any non-zero error rate on /healthz, is a
finding.

    python scripts/load_smoke.py --base-url http://localhost:8000 \
        --clients 20 --seconds 15

Deliberately dependency-light (httpx is already a project dependency) and
deliberately read-only: it never signs up, never sends OTP mail, never
enqueues jobs, so it cannot trip the auth rate limits or pollute a database.
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
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
