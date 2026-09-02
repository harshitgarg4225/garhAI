"""Redis sliding-window rate limiting (playbook §11 limits, §13 checklist).

Algorithm: one sorted set per (rule, identity). Members are timestamped hits; scores
are epoch milliseconds. A check trims everything older than the window, counts what is
left, and either admits the request (adding a member) or reports how long until the
oldest hit falls out. That is a true sliding window — no fixed-bucket boundary where a
client can fire 2× the limit across the tick.

The whole check is one Lua script, so trim/count/admit happen atomically. Two API
processes cannot both see "59 used, limit 60" and both admit. Pure Redis: no
``redis-limits``, no ``slowapi``, nothing added to the dependency table.

**Limits shipped** (§11 "rate limits per firm (60 ops/s, 10 solver jobs/hr on free
tier)", §13 "rate limits (per firm + per IP on auth)"):

===========================  ==================  ==========  ============
rule                         identity            limit       window
===========================  ==================  ==========  ============
``ops.per_firm``             firm                60          1 s
``solver_jobs.per_firm``     firm                10          1 h
``render_jobs.per_firm``     firm                30          1 h
``export_jobs.per_firm``     firm                40          1 h
``llm.per_firm``             firm                60          1 h
``auth.per_ip``              client IP           20          1 h
``auth.otp_per_email``       email (hashed)      5           1 h
``auth.otp_resend``          email (hashed)      1           60 s
``auth.verify_per_ip``       client IP           30          1 h
``2fa_attempt``              user                5           10 min
===========================  ==================  ==========  ============

The configurable numbers come from ``Settings`` so an operator can raise them without a
deploy; the derived ones are constants here.

**Failure policy is per rule, and it is a real decision.** If Redis is unreachable:

* ``fail_closed=False``: admit, log a warning, set ``degraded``. A Redis blip must not
  stop architects from editing walls (``ops``), and for the queue-backed jobs
  (``solver``, ``export``) the enqueue that follows needs the *same* Redis — a limiter
  outage cannot admit work the queue will accept, so refusing here would only swap an
  honest "the job queue is unreachable" for a vaguer one.
* ``fail_closed=True``: refuse with 503. Two families qualify. Auth (an unmetered
  sign-in endpoint is an open brute-force target, and sign-in is already down if Redis
  is down), and **anything that spends money at a third party per job** — ``llm`` and,
  since F-7, ``render``. There the queue-shares-Redis argument is not enough: the check
  and the push are separate round trips against a server that can be slow rather than
  dead, and an uncounted call to a metered API is worse than a job the architect retries.

**Usage.** Service code calls :func:`enforce_rate_limit` directly. HTTP routes use the
``IpRateLimit`` / ``FirmRateLimit`` dependencies in :mod:`garh_api.deps` (they live
there because resolving "which firm" needs the tenant dependency, and this module
stays framework-free)::

    # service layer
    await enforce_rate_limit(solver_jobs_per_firm_rule(), f"firm:{ctx.firm_id}")

    # route layer
    @router.post("/projects/{pid}/solve", dependencies=[Depends(rate_limit_solver_jobs)])

    # any async callable
    @rate_limited(auth_ip_rule, identity=lambda ip, **_: f"ip:{ip}")
    async def send_otp(ip: str) -> None: ...
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Final, Literal, TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from garh_api.config import Settings, get_settings
from garh_api.errors import RateLimitedError, ServiceUnavailableError
from garh_api.logging import get_logger

_log = get_logger(__name__)

#: Every key this module writes lives under one prefix, so ``redis-cli --scan
#: --pattern 'garh:rl:*'`` shows the whole limiter and nothing else.
KEY_PREFIX: Final = "garh:rl"

#: Sliding window, atomic. Returns ``{allowed, used, retry_after_ms}``.
#:
#: KEYS[1] = window key
#: ARGV[1] = now (ms), ARGV[2] = window (ms), ARGV[3] = limit,
#: ARGV[4] = unique member prefix, ARGV[5] = cost
_SLIDING_WINDOW_LUA: Final = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local cost = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local used = tonumber(redis.call('ZCARD', KEYS[1]))

if used + cost > limit then
  local retry = window
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  if oldest[2] then
    retry = (tonumber(oldest[2]) + window) - now
  end
  if retry < 1 then retry = 1 end
  redis.call('PEXPIRE', KEYS[1], window)
  return {0, used, math.ceil(retry)}
end

for i = 1, cost do
  redis.call('ZADD', KEYS[1], now, ARGV[4] .. ':' .. i)
end
redis.call('PEXPIRE', KEYS[1], window)
return {1, used + cost, 0}
"""

#: Read-only: how much of the window is used, without consuming any of it.
#:
#: KEYS[1] = window key; ARGV[1] = now (ms), ARGV[2] = window (ms)
_PEEK_LUA: Final = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
return tonumber(redis.call('ZCARD', KEYS[1]))
"""


# ---------------------------------------------------------------------------
# Redis client
# ---------------------------------------------------------------------------
#
# CONTRACT: this is currently the only Redis accessor in the API. Queue publishing and
# SSE fan-out should import ``get_redis`` from here until a dedicated ``garh_api/redis.py``
# exists; two independent connection pools to the same server would be a waste and a
# second thing to close on shutdown.

_client: Redis | None = None


def get_redis(settings: Settings | None = None) -> Redis:
    """Process-wide async Redis client (lazy, pooled, ``decode_responses=True``)."""
    global _client
    if _client is None:
        cfg = settings or get_settings()
        _client = Redis.from_url(
            cfg.redis_url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
            health_check_interval=30,
            retry_on_timeout=True,
        )
    return _client


async def close_redis() -> None:
    """Close the pool. Call from the FastAPI lifespan shutdown hook."""
    global _client, _sliding_script, _peek_script
    if _client is not None:
        await _client.aclose()
    _client = None
    _sliding_script = None
    _peek_script = None


async def redis_healthcheck(settings: Settings | None = None) -> bool:
    """``PING`` — backs ``/readyz`` (§18). Never raises."""
    try:
        return bool(await get_redis(settings).ping())
    except Exception:
        return False


_sliding_script: Any = None
_peek_script: Any = None


def _scripts(client: Redis) -> tuple[Any, Any]:
    """Registered scripts. ``register_script`` does EVALSHA with a NOSCRIPT fallback,
    so a Redis restart re-loads them transparently."""
    global _sliding_script, _peek_script
    if _sliding_script is None or _peek_script is None:
        _sliding_script = client.register_script(_SLIDING_WINDOW_LUA)
        _peek_script = client.register_script(_PEEK_LUA)
    return _sliding_script, _peek_script


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitRule:
    """One limit. Immutable, hashable, cheap to build per request."""

    name: str
    limit: int
    window_seconds: int
    scope: str  # "firm" | "ip" | "email" | "user" | "global" — documentation + key part
    #: Refuse the request when Redis is unreachable instead of admitting it.
    fail_closed: bool = False
    #: Overrides the generic 429 copy. Auth limits say something more specific.
    message: str | None = None
    action: str | None = None
    #: A more specific problem+json code (e.g. ``otp_rate_limited``).
    code: str | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(f"rate limit {self.name!r} must allow at least 1 request")
        if self.window_seconds < 1:
            raise ValueError(f"rate limit {self.name!r} needs a window of at least 1s")

    @property
    def window_ms(self) -> int:
        return self.window_seconds * 1000

    def key(self, identity: str) -> str:
        return f"{KEY_PREFIX}:{self.name}:{identity}"

    def describe(self) -> str:
        return f"{self.limit} per {self.window_seconds}s per {self.scope}"


# -- the shipped rules -------------------------------------------------------
# Factories, not constants: three of them read Settings, and Settings is only valid
# once the process has booted.


def ops_per_firm_rule(settings: Settings | None = None) -> RateLimitRule:
    """§11: 60 ops/s per firm. Guards ``POST /projects/:id/ops``.

    Fails open — a limiter outage must not stop people editing. The op sequencer's
    per-project lock is the correctness control; this is a fairness control.
    """
    cfg = settings or get_settings()
    return RateLimitRule(
        name="ops.per_firm",
        limit=cfg.rate_limit_ops_per_second,
        window_seconds=1,
        scope="firm",
        message="Your edits are arriving faster than we can sequence them.",
        action="Pause for a second — your work is saved and will catch up.",
    )


def solver_jobs_per_firm_rule(settings: Settings | None = None) -> RateLimitRule:
    """§11: 10 solver jobs/hour per firm on the free tier."""
    cfg = settings or get_settings()
    return RateLimitRule(
        name="solver_jobs.per_firm",
        limit=cfg.rate_limit_solver_jobs_per_hour,
        window_seconds=3600,
        scope="firm",
        message="You've used this hour's plan generations.",
        action="Wait for the hour to roll over, or upgrade your plan for more.",
    )


#: Per-feature 429 copy for :func:`llm_per_firm_rule`. The *bucket* is deliberately
#: shared — one hourly budget across every route that spends money at the provider —
#: but the sentence must name what the architect was actually doing, and what they can
#: do instead. Telling a copilot user they are out of "brief parses" is a small lie
#: that makes the whole limit look broken (golden rule 9: say what happened and what
#: to do next).
LLM_LIMIT_COPY: Final[dict[str, tuple[str, str]]] = {
    "brief-parse": (
        "Your firm has used this hour's AI brief parses.",
        "Edit the brief fields directly, or try the parse again next hour.",
    ),
    "copilot": (
        "Your firm has used this hour's AI editing requests.",
        "Make the change directly on the plan, or ask the copilot again next hour.",
    ),
}


def llm_per_firm_rule(
    settings: Settings | None = None, *, feature: str = "brief-parse"
) -> RateLimitRule:
    """§13: per-firm cap on the routes that call a language model.

    ``POST /projects/:id/brief/parse`` and, since Phase 6, ``POST /projects/:id/copilot``.
    These are the only endpoints in the API that spend money at a third party on every
    request, which makes them the only ones where "no limit" is a billing incident
    rather than a capacity problem. ``credit_events(kind='llm')`` records the spend;
    this stops it running away in the first place.

    ``feature`` selects the 429 wording only. The ``name`` — and therefore the counter
    both routes charge against — is the same on purpose: the budget is a spend budget,
    not a per-endpoint quota, so a firm cannot double it by alternating routes.

    Fails **closed**, unlike the other product limits. The reasoning is the opposite of
    ``ops_per_firm_rule``: if Redis is unreachable we cannot count calls, and an
    uncounted call to a metered API is worse than a brief parse the architect has to
    retry. The route degrades to "try again in a moment", never to "spend freely".
    """
    cfg = settings or get_settings()
    message, action = LLM_LIMIT_COPY.get(feature, LLM_LIMIT_COPY["brief-parse"])
    return RateLimitRule(
        name="llm.per_firm",
        limit=cfg.rate_limit_llm_per_hour,
        window_seconds=3600,
        scope="firm",
        fail_closed=True,
        message=message,
        action=action,
    )


def render_jobs_per_firm_rule(settings: Settings | None = None) -> RateLimitRule:
    """F-7: per-firm hourly cap on the render enqueue routes.

    ``POST /projects/:id/renders`` and ``POST /projects/:id/renders/client-pack``, the
    latter charging one slot per shot — a pack is eight renders and must cost eight, or
    the cheapest way past this limit is to ask for packs.

    Fails **closed**, on the same reasoning as :func:`llm_per_firm_rule` and not on the
    reasoning that covers the other job limits. With ``PROVIDER_RENDER=stability`` every
    admitted job is a metered third-party call, so the resource this protects is an
    invoice, not our own capacity. "The worker cannot pop from a dead Redis anyway" does
    not cover it: the limiter check and the queue push are separate round trips, and a
    Redis that is slow rather than dead can time out the first (2 s socket timeout) while
    serving the second — which is exactly the window in which an unmetered render route
    bills the company. A refused render costs the architect one retry.
    """
    cfg = settings or get_settings()
    return RateLimitRule(
        name="render_jobs.per_firm",
        limit=cfg.rate_limit_render_jobs_per_hour,
        window_seconds=3600,
        scope="firm",
        fail_closed=True,
        message="Your firm has used this hour's renders.",
        action="Review the renders you have, or start another next hour.",
    )


#: Per-feature 429 copy for :func:`export_jobs_per_firm_rule`. Same shape and same
#: reasoning as :data:`LLM_LIMIT_COPY`: the *bucket* is shared because both routes feed
#: one drawings worker, but an architect who just clicked "Generate drawings" must not be
#: told they are out of "exports".
EXPORT_LIMIT_COPY: Final[dict[str, tuple[str, str]]] = {
    "export": (
        "Your firm has used this hour's exports.",
        "Download an export you already have, or try again next hour.",
    ),
    "sheets": (
        "Your firm has used this hour's drawing-set generations.",
        "Open the set you already generated, or try again next hour.",
    ),
}


def export_jobs_per_firm_rule(
    settings: Settings | None = None, *, feature: str = "export"
) -> RateLimitRule:
    """F-7: per-firm hourly cap on the two routes that queue the drawings worker.

    ``POST /projects/:id/export`` and ``POST /projects/:id/sheets/generate``. A sheet-set
    run renders nine municipal sheets in three formats and a pdf-set export vectorises
    ten pages through headless Chromium, then writes every artefact to object storage —
    the most expensive work in the product that is not a solve, and until F-7 the only
    expensive work with no ceiling at all.

    ``feature`` selects the 429 wording only; ``name`` is shared on purpose, exactly as
    :func:`llm_per_firm_rule` shares one spend budget across its two routes. Both routes
    consume one drawings worker and one storage bill, so a firm must not double its
    budget by alternating between them.

    Fails **open**. Unlike the render rule there is no third-party meter behind it: the
    cost is our own CPU and our own bucket. And the openness is close to inert — both
    routes push to the same Redis immediately afterwards (``queue.put_export_job`` then
    ``_enqueue_or_rollback``), so a limiter that cannot reach Redis is followed within
    milliseconds by an enqueue that cannot either, and the request 503s with the message
    that actually names the problem.
    """
    cfg = settings or get_settings()
    message, action = EXPORT_LIMIT_COPY.get(feature, EXPORT_LIMIT_COPY["export"])
    return RateLimitRule(
        name="export_jobs.per_firm",
        limit=cfg.rate_limit_export_jobs_per_hour,
        window_seconds=3600,
        scope="firm",
        message=message,
        action=action,
    )


def sheet_jobs_per_firm_rule(settings: Settings | None = None) -> RateLimitRule:
    """The sheets face of :func:`export_jobs_per_firm_rule` — same bucket, own copy.

    A named factory rather than a ``partial`` so ``deps.FirmRateLimit`` can mount it
    like any other rule and so it appears in :data:`RULE_FACTORIES` by name.
    """
    return export_jobs_per_firm_rule(settings, feature="sheets")


def auth_ip_rule(settings: Settings | None = None) -> RateLimitRule:
    """§13: per-IP limit on the OTP-issuing routes. Fails closed."""
    cfg = settings or get_settings()
    return RateLimitRule(
        name="auth.per_ip",
        limit=cfg.rate_limit_auth_per_hour,
        window_seconds=3600,
        scope="ip",
        fail_closed=True,
        message="Too many sign-in attempts from this network.",
        action="Wait a few minutes before trying again.",
    )


#: Codes an address may be sent per hour. Five is generous for a human and cheap to
#: exhaust for someone using our mail bill as a weapon.
OTP_PER_EMAIL_PER_HOUR: Final = 5

#: Minimum gap between two codes to the same address — the "resend" cooldown the UI
#: counts down against.
OTP_RESEND_COOLDOWN_SECONDS: Final = 60

#: Verification attempts per IP per hour. Higher than the issue limit (typos are
#: normal); the 5-attempts-per-challenge cap in the OTP repository is the real guard.
VERIFY_PER_IP_PER_HOUR: Final = 30


def otp_per_email_rule(settings: Settings | None = None) -> RateLimitRule:
    return RateLimitRule(
        name="auth.otp_per_email",
        limit=OTP_PER_EMAIL_PER_HOUR,
        window_seconds=3600,
        scope="email",
        fail_closed=True,
        message="We've sent that address several codes already.",
        action="Check your inbox and spam folder, then try again in a while.",
        code="otp_rate_limited",
    )


OtpRoute = Literal["signin", "signup"]


def otp_resend_identity(identity: str, route: OtpRoute) -> str:
    """The identity the 60-second resend cooldown is keyed on: per address AND per route.

    Sign-in and sign-up must not share one cooldown. First live trial, 2026-09-02: a
    sign-in for an address with no account (a 202 that sends nothing, by design) spent
    the cooldown, so the sign-up thirty seconds later — the request that would actually
    have sent a code — was refused with 429. The hourly per-address cap stays shared.

    This is the ONE place the key shape lives: ``AuthService`` charges it and the tests
    that legitimately sign the same user in twice reset it through the same function.
    """
    return "%s:%s" % (identity, route)


def otp_resend_rule(settings: Settings | None = None) -> RateLimitRule:
    return RateLimitRule(
        name="auth.otp_resend",
        limit=1,
        window_seconds=OTP_RESEND_COOLDOWN_SECONDS,
        scope="email",
        fail_closed=True,
        message="We just sent a code to that address.",
        action=f"Give it {OTP_RESEND_COOLDOWN_SECONDS} seconds, then ask for a new one.",
        code="otp_rate_limited",
    )


def verify_ip_rule(settings: Settings | None = None) -> RateLimitRule:
    return RateLimitRule(
        name="auth.verify_per_ip",
        limit=VERIFY_PER_IP_PER_HOUR,
        window_seconds=3600,
        scope="ip",
        fail_closed=True,
        message="Too many code attempts from this network.",
        action="Wait a few minutes before trying again.",
    )


#: Attempts allowed against a *second factor* before the account is held, and the
#: window they are counted over. Six digits is 10^6, which falls in minutes at HTTP
#: speed, so this is the number that decides whether TOTP is worth having.
TWO_FACTOR_MAX_ATTEMPTS: Final = 5
TWO_FACTOR_ATTEMPT_WINDOW_SECONDS: Final = 600


def two_factor_attempt_rule(settings: Settings | None = None) -> RateLimitRule:
    """5 second-factor attempts per 10 minutes per user, fail-closed.

    Fail-closed is the whole point: if Redis cannot tell us how many guesses have been
    made we must refuse rather than admit. A 503 here is a worse day for a legitimate
    user than a 200; it is a much better day than an attacker walking in.

    It lives *here* rather than beside the rest of the second-factor policy in
    :mod:`garh_api.twofactor` for one reason, and it is the reason bug pattern 4 exists:
    :data:`RULE_FACTORIES` is the registry the security checklist audits, and a rule
    defined in another module is a rule somebody has to remember to register. Defining
    it next to the tuple makes "registered" structural rather than remembered.
    :mod:`garh_api.twofactor` re-exports it, so every existing import still resolves.
    """
    del settings  # the numbers are policy, not configuration
    return RateLimitRule(
        name="2fa_attempt",
        limit=TWO_FACTOR_MAX_ATTEMPTS,
        window_seconds=TWO_FACTOR_ATTEMPT_WINDOW_SECONDS,
        scope="user",
        fail_closed=True,
        message="Too many two-factor attempts. Wait a few minutes and try again.",
        action="Wait 10 minutes, then use a recovery code if the app still won't work.",
    )


#: Every rule factory the API ships, for the security-checklist test that asserts the
#: playbook's numbers are actually wired up.
#:
#: ``tests/test_job_rate_limits.py::test_every_shipped_rule_is_registered`` walks the
#: whole ``garh_api`` package for factories that return a :class:`RateLimitRule` and
#: fails if one is missing from this tuple, so a new limit cannot go quietly
#: unaudited — which is exactly what happened to ``2fa_attempt``.
RULE_FACTORIES: tuple[Callable[..., RateLimitRule], ...] = (
    ops_per_firm_rule,
    solver_jobs_per_firm_rule,
    render_jobs_per_firm_rule,
    export_jobs_per_firm_rule,
    sheet_jobs_per_firm_rule,
    llm_per_firm_rule,
    auth_ip_rule,
    otp_per_email_rule,
    otp_resend_rule,
    verify_ip_rule,
    two_factor_attempt_rule,
)


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of one check. Carries what the response headers need."""

    allowed: bool
    rule: RateLimitRule
    used: int
    retry_after_seconds: int = 0
    #: True when Redis was unreachable and a fail-open rule admitted the request.
    degraded: bool = False

    @property
    def remaining(self) -> int:
        return max(0, self.rule.limit - self.used)

    def headers(self) -> dict[str, str]:
        """``X-RateLimit-*`` (draft convention, widely understood by clients)."""
        out = {
            "X-RateLimit-Limit": str(self.rule.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Window": str(self.rule.window_seconds),
        }
        if not self.allowed:
            out["Retry-After"] = str(max(1, self.retry_after_seconds))
        return out

    def raise_if_limited(self) -> None:
        if self.allowed:
            return
        raise RateLimitedError(
            self.rule.message,
            retry_after_seconds=self.retry_after_seconds,
            action=self.rule.action,
            limit=self.rule.limit,
            rule=self.rule.name,
            code=self.rule.code,
        )


def _now_ms() -> int:
    return int(time.time() * 1000)


async def check_rate_limit(
    rule: RateLimitRule,
    identity: str,
    *,
    cost: int = 1,
    settings: Settings | None = None,
) -> RateLimitDecision:
    """Consume ``cost`` slots if available. Never raises for a normal denial.

    ``identity`` must already be scoped and non-PII — ``f"firm:{firm_id}"``,
    ``f"ip:{ip}"``, ``f"email:{pseudonymise(address)}"``.
    """
    if cost < 1:
        raise ValueError("rate-limit cost must be at least 1")
    if not identity:
        raise ValueError("rate-limit identity cannot be empty")

    client = get_redis(settings)
    sliding, _ = _scripts(client)
    now = _now_ms()
    member = f"{now}-{uuid.uuid4().hex}"

    try:
        raw = await sliding(
            keys=[rule.key(identity)],
            args=[now, rule.window_ms, rule.limit, member, cost],
        )
    except (RedisError, OSError, TimeoutError) as exc:
        return _degraded(rule, exc)

    allowed = bool(int(raw[0]))
    used = int(raw[1])
    retry_ms = int(raw[2])
    decision = RateLimitDecision(
        allowed=allowed,
        rule=rule,
        used=used,
        retry_after_seconds=max(1, (retry_ms + 999) // 1000) if not allowed else 0,
    )
    if not allowed:
        _log.info(
            "ratelimit.blocked",
            rule=rule.name,
            scope=rule.scope,
            limit=rule.limit,
            window_seconds=rule.window_seconds,
            retry_after_seconds=decision.retry_after_seconds,
        )
    return decision


def _degraded(rule: RateLimitRule, exc: BaseException) -> RateLimitDecision:
    """Redis is unreachable. Apply the rule's declared failure policy."""
    _log.warning(
        "ratelimit.backend_unavailable",
        rule=rule.name,
        fail_closed=rule.fail_closed,
        error=type(exc).__name__,
    )
    if rule.fail_closed:
        raise ServiceUnavailableError(
            "We can't check sign-in limits right now, so we're holding off.",
            dependency="redis",
            retry_after_seconds=5,
        ) from exc
    return RateLimitDecision(allowed=True, rule=rule, used=0, degraded=True)


async def enforce_rate_limit(
    rule: RateLimitRule,
    identity: str,
    *,
    cost: int = 1,
    settings: Settings | None = None,
) -> RateLimitDecision:
    """:func:`check_rate_limit`, but raises :class:`RateLimitedError` on denial."""
    decision = await check_rate_limit(rule, identity, cost=cost, settings=settings)
    decision.raise_if_limited()
    return decision


async def peek_rate_limit(
    rule: RateLimitRule, identity: str, *, settings: Settings | None = None
) -> int:
    """Slots used in the current window, consuming nothing.

    Used for "resend in N seconds" copy: the UI should not burn a slot to render a
    countdown.
    """
    client = get_redis(settings)
    _, peek = _scripts(client)
    try:
        return int(await peek(keys=[rule.key(identity)], args=[_now_ms(), rule.window_ms]))
    except (RedisError, OSError, TimeoutError):
        return 0


async def reset_rate_limit(
    rule: RateLimitRule, identity: str, *, settings: Settings | None = None
) -> None:
    """Clear one bucket. For tests and for support un-sticking a customer."""
    try:
        await get_redis(settings).delete(rule.key(identity))
    except (RedisError, OSError, TimeoutError):  # pragma: no cover - best effort
        _log.warning("ratelimit.reset_failed", rule=rule.name)


# ---------------------------------------------------------------------------
# Decorator (service layer, not routes)
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def rate_limited(
    rule_factory: Callable[..., RateLimitRule],
    *,
    identity: Callable[..., str],
    cost: int = 1,
) -> Callable[[F], F]:
    """Wrap an async service function in a limit.

    Deliberately **not** for FastAPI endpoints: a decorator hides parameters from
    FastAPI's signature inspection, which silently breaks dependency injection and the
    OpenAPI schema. Routes use ``Depends(...)`` — see :mod:`garh_api.deps`.

    ``identity`` receives the wrapped call's arguments and returns the bucket key::

        @rate_limited(
            otp_per_email_rule,
            identity=lambda *, email, **_: f"email:{pseudonymise(email)}",
        )
        async def issue_code(*, email: str) -> None: ...
    """

    def decorate(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            await enforce_rate_limit(rule_factory(), identity(*args, **kwargs), cost=cost)
            return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate


__all__ = [
    "OtpRoute",
    "otp_resend_identity",
    "EXPORT_LIMIT_COPY",
    "KEY_PREFIX",
    "LLM_LIMIT_COPY",
    "OTP_PER_EMAIL_PER_HOUR",
    "OTP_RESEND_COOLDOWN_SECONDS",
    "RULE_FACTORIES",
    "TWO_FACTOR_ATTEMPT_WINDOW_SECONDS",
    "TWO_FACTOR_MAX_ATTEMPTS",
    "VERIFY_PER_IP_PER_HOUR",
    "RateLimitDecision",
    "RateLimitRule",
    "auth_ip_rule",
    "check_rate_limit",
    "close_redis",
    "enforce_rate_limit",
    "export_jobs_per_firm_rule",
    "get_redis",
    "llm_per_firm_rule",
    "ops_per_firm_rule",
    "otp_per_email_rule",
    "otp_resend_rule",
    "peek_rate_limit",
    "rate_limited",
    "redis_healthcheck",
    "render_jobs_per_firm_rule",
    "reset_rate_limit",
    "sheet_jobs_per_firm_rule",
    "solver_jobs_per_firm_rule",
    "two_factor_attempt_rule",
    "verify_ip_rule",
]
