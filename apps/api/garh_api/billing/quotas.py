"""Per-firm quotas, metered against the ``credit_events`` rows that already exist (G-2).

THE METER WAS ALREADY RUNNING
-----------------------------
``credit_events`` has been written since the first commit — one row per solve, render,
export and LLM call, with a quantity and a firm id. What was missing was anything that
*read* it and said no. This module is that, and it deliberately adds no second meter:
usage comes from
:meth:`garh_api.repositories.credits.CreditEventRepository.usage_by_kind`, the same
aggregation the billing page renders, over the same rows, for the same period. One
source for the number the customer is charged on.

A QUOTA THAT CANNOT DENY IS NOT A QUOTA
---------------------------------------
Three ways this could be a green check that never goes red, and what stops each:

1. *keyed on a kind the meter never writes* — :mod:`garh_api.billing.plans` asserts at
   import that every plan's allowance keys are exactly ``CREDIT_EVENT_KINDS``, and
   :func:`check_quota` refuses a kind outside that tuple instead of admitting it;
2. *counting the wrong window* — the period comes from the subscription row
   (:func:`garh_api.billing.subscriptions.entitlement`), and usage is summed with
   ``since``/``until`` bounds from that same object, so the numerator and the
   denominator cannot disagree;
3. *never called* — see WIRING below, and ``tests/test_billing_api.py``, which mounts
   :func:`require_quota` on a route and asserts a real 402 over HTTP.

WIRING — AND THE ONE FORM THAT WORKS
------------------------------------
:func:`require_quota` **already returns** ``Depends(...)``. Mount it bare::

    @router.post("/projects/{project_id}/renders", dependencies=[require_quota("render")])

Do **not** wrap it in a second ``Depends()``. That hands FastAPI a ``Depends`` marker
where it expects the dependency *callable*, and the router dies at import with
``AssertionError: A parameter-less dependency must have a callable dependency``. The
docstrings here used to instruct exactly that, which is why the working form is spelled
out above and ``tests/test_billing_core.py`` mounts both forms to prove which is which.

Mounted, as of this change, on the spending routes with a positive free allowance:

============================================  ==========================================
``POST /projects/:id/solve``                  ``require_quota("solver")`` (jobs.py)
``POST /projects/:id/renders``                ``require_quota("render")`` (jobs.py)
``POST /projects/:id/copilot``                ``require_quota("llm")`` (copilot.py)
``POST /projects/:id/brief/parse``            ``require_quota("llm")`` (projects.py)
============================================  ==========================================

Each of those routes writes the matching ``credit_events`` row in its own handler, so the
gate and the meter read the same rows over the same window.

STILL UNMOUNTED, named rather than implied
------------------------------------------
* ``POST /projects/:id/export`` and ``POST /projects/:id/renders/client-pack`` are metered
  but ungated. Export is not a one-line mount: the free plan's ``export`` allowance is
  ``0`` **on purpose**, so mounting it flips the free tier from "exports work" to 402 and
  needs the demo seed on a paid plan; the client pack meters ``qty=len(shots)``, which a
  static ``qty=1`` would under-check. Both are in the handoff with the exact edit.
* ``POST /projects/:id/sheets/generate`` writes no ``credit_events`` row at all, so there
  is nothing to gate it against yet — metering it is the prerequisite, not the mount.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, params
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.billing.errors import QuotaExceededError
from garh_api.billing.plans import QUOTA_KINDS
from garh_api.billing.subscriptions import Entitlement, entitlement
from garh_api.logging import get_logger
from garh_api.repositories import CreditEventRepository
from garh_api.tenancy import TenantCtx

_log = get_logger(__name__)


@dataclass(frozen=True)
class QuotaLine:
    """Usage against allowance for one metered kind, over one billing period."""

    kind: str
    used: int
    #: ``None`` means unmetered on this plan. Distinct from ``0``, which means the plan
    #: does not include this at all.
    allowance: int | None

    @property
    def remaining(self) -> int | None:
        if self.allowance is None:
            return None
        return max(0, self.allowance - self.used)

    def would_exceed(self, qty: int) -> bool:
        if self.allowance is None:
            return False
        return self.used + qty > self.allowance


async def usage_lines(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    ent: Entitlement | None = None,
    now: datetime | None = None,
) -> tuple[Entitlement, list[QuotaLine]]:
    """Every metered kind, used vs allowed, for the firm's current period.

    Returns the entitlement alongside the lines so a caller that needs both (the
    billing page, :func:`check_quota`) resolves the subscription once.
    """
    resolved = ent or await entitlement(session, ctx, now=now)
    used = await CreditEventRepository(session, ctx).usage_by_kind(
        since=resolved.period_start, until=resolved.period_end
    )
    lines = [
        QuotaLine(
            kind=kind,
            used=int(used.get(kind, 0)),
            allowance=resolved.effective_plan.allowance(kind),
        )
        for kind in QUOTA_KINDS
    ]
    return resolved, lines


async def check_quota(
    session: AsyncSession,
    ctx: TenantCtx,
    kind: str,
    *,
    qty: int = 1,
    now: datetime | None = None,
) -> QuotaLine:
    """Raise :class:`QuotaExceededError` if ``qty`` more units would go over.

    Returns the line it checked, so a caller can log or echo the remaining allowance.

    ``kind`` is validated against the meter's own vocabulary. A typo raises
    ``ValueError`` — a programming error at the call site, surfaced as a 500 — rather
    than admitting the request. "I don't recognise this kind" must never read as "no
    limit applies", which is precisely how 83 rules in this repository went inert.
    """
    if kind not in QUOTA_KINDS:
        raise ValueError(
            "%r is not a metered kind. Expected one of: %s." % (kind, ", ".join(QUOTA_KINDS))
        )
    if qty < 1:
        raise ValueError("A quota check is for at least one unit.")
    resolved, lines = await usage_lines(session, ctx, now=now)
    line = next(item for item in lines if item.kind == kind)
    if line.would_exceed(qty):
        _log.info(
            "billing.quota_denied",
            credit_kind=kind,
            used=line.used,
            allowance=line.allowance,
            plan_code=resolved.effective_plan.code,
        )
        raise QuotaExceededError.for_kind(
            kind=kind,
            used=line.used,
            allowance=line.allowance or 0,
            plan_code=resolved.effective_plan.code,
        )
    return line


def require_quota(kind: str, *, qty: int = 1) -> params.Depends:
    """FastAPI dependency: 402 before the handler runs if the firm is out of allowance.

    Returns ``Depends(...)``, so it is mounted **bare**::

        dependencies=[require_quota("render")]

    Never wrapped in a second ``Depends()``: that hands FastAPI a ``Depends`` marker
    where it expects a callable and raises at import of the router that does it. The
    return annotation says so in the type system as well as here.

    The kind is validated **here**, at import time of the router that mounts it, so a
    typo is a boot failure rather than a permanently open gate.
    """
    if kind not in QUOTA_KINDS:
        raise ValueError(
            "require_quota(%r): not a metered kind. Expected one of: %s."
            % (kind, ", ".join(QUOTA_KINDS))
        )

    # Imported inside the factory: ``garh_api.deps`` pulls in auth, the repositories
    # package and the rate limiter, and this module is imported by ``garh_api.billing``
    # itself — a module-level import would make the package's import graph depend on the
    # whole request stack.
    from garh_api.deps import db_session, require_tenant

    # Annotated with the CLASSES (``AsyncSession``/``TenantCtx``) and ``Depends`` in the
    # defaults, NOT with the ``DbSession``/``Tenant`` aliases. This module has
    # ``from __future__ import annotations``, and FastAPI resolves a dependency's
    # annotations through ``call.__globals__`` — an alias imported inside this function
    # is not in them, so the annotation stays an unresolvable string and every router
    # mounting this dies at import with ``PydanticUndefinedAnnotation``. That is the
    # same trap ``garh_api/deps.py`` documents at the top of the file; here the fix is
    # to name types the module already imports.
    async def dependency(
        session: AsyncSession = Depends(db_session),
        ctx: TenantCtx = Depends(require_tenant),
    ) -> None:
        await check_quota(session, ctx, kind, qty=qty)

    return Depends(dependency)


__all__ = ["QuotaLine", "check_quota", "require_quota", "usage_lines"]
