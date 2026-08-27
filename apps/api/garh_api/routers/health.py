"""Health endpoints (playbook §18: "``/healthz`` per service").

Two probes, because they answer different questions and a deployment that conflates
them behaves badly:

``GET /healthz`` — **liveness**. "Is this process running and able to serve?" It
touches nothing external and cannot fail because Postgres is slow. An orchestrator
restarts a container that fails liveness, so wiring a database check in here means a
database outage turns into a restart storm that makes the outage worse.

``GET /readyz`` — **readiness**. "Should this instance receive traffic *right now*?"
Checks Postgres and Redis concurrently. A failure takes the instance out of the load
balancer without killing it, so it rejoins on its own when the dependency comes back.

``docker-compose.yml``'s api healthcheck and container-start ordering both target
``/healthz`` (scaffold contract note 4), so its shape is load-bearing: keep it fast,
dependency-free, and always 200 while the process is alive.

**These are the one deliberate exception to the problem+json error contract.** A failing
``/readyz`` returns 503 carrying the per-dependency check table rather than
``{code, message, action}``. An operator needs to know *which* dependency is down, and
a probe body is read by monitoring, not by a user who needs a next step. Everything
else in the API — including a request to these paths that goes wrong unexpectedly —
still goes through :func:`garh_api.errors.install_error_handlers`.

Neither route is authenticated, so neither leaks anything: no versions of dependencies,
no hostnames, no error strings from the failing check — just ``ok``/``down`` per
dependency and a duration.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from garh_api import __version__
from garh_api.config import get_settings
from garh_api.db import healthcheck as postgres_healthcheck
from garh_api.logging import get_logger
from garh_api.ratelimit import redis_healthcheck

_log = get_logger(__name__)

#: No prefix, on purpose. ``main.py`` must include this router **without** the
#: ``/api/v1`` prefix — compose, CI and any future orchestrator probe ``/healthz`` at
#: the root.
router = APIRouter(tags=["health"])

STATUS_OK = "ok"
STATUS_DOWN = "down"

#: A dependency slower than this is treated as down. Readiness must answer faster than
#: the load balancer's own probe timeout, or every instance flaps at once.
PROBE_TIMEOUT_SECONDS = 2.0


class HealthResponse(BaseModel):
    """``GET /healthz`` — process liveness."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    status: str = Field(default=STATUS_OK)
    service: str
    env: str
    version: str


class DependencyCheck(BaseModel):
    """One dependency's verdict. No error text — probes are unauthenticated."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    name: str
    status: str
    duration_ms: int


class ReadinessResponse(BaseModel):
    """``GET /readyz`` — returned with 200 when ready, 503 when not."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    status: str
    service: str
    env: str
    version: str
    checks: list[DependencyCheck]


async def _timed_check(name: str, probe: Awaitable[bool]) -> DependencyCheck:
    """Run one probe under a timeout and stopwatch it.

    ``except Exception`` and not ``BaseException``: a timeout or a connection error is
    "down", but ``CancelledError`` means the *request* is going away and must keep
    propagating rather than being reported as an unhealthy database.
    """
    started = time.perf_counter()
    try:
        ok = bool(await asyncio.wait_for(probe, timeout=PROBE_TIMEOUT_SECONDS))
    except Exception:
        ok = False
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return DependencyCheck(
        name=name, status=STATUS_OK if ok else STATUS_DOWN, duration_ms=elapsed_ms
    )


@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Liveness — is the process up?",
)
async def healthz() -> HealthResponse:
    """Always 200 while the process can serve a request. Touches no dependency."""
    settings = get_settings()
    return HealthResponse(
        status=STATUS_OK,
        service=settings.app_name,
        env=settings.env,
        version=__version__,
    )


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness — should this instance take traffic?",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "At least one dependency is unreachable.",
        }
    },
)
async def readyz(response: Response) -> ReadinessResponse:
    """Check Postgres and Redis concurrently; 503 if either is unreachable.

    Concurrently, not in sequence: two 2-second timeouts in series would take 4s and
    blow past most load-balancer probe budgets, and a slow readiness probe reads as a
    dead instance.
    """
    settings = get_settings()
    checks = await asyncio.gather(
        _timed_check("postgres", postgres_healthcheck(settings)),
        _timed_check("redis", redis_healthcheck(settings)),
    )
    ready = all(check.status == STATUS_OK for check in checks)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        _log.warning(
            "health.not_ready",
            down=[check.name for check in checks if check.status != STATUS_OK],
        )
    return ReadinessResponse(
        status=STATUS_OK if ready else STATUS_DOWN,
        service=settings.app_name,
        env=settings.env,
        version=__version__,
        checks=list(checks),
    )


__all__ = [
    "PROBE_TIMEOUT_SECONDS",
    "STATUS_DOWN",
    "STATUS_OK",
    "DependencyCheck",
    "HealthResponse",
    "ReadinessResponse",
    "router",
]
