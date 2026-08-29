"""The audit trail a firm can actually read (F-5), and DPDP rights (F-6).

=====================================  ======  ==========================================
route                                   auth    purpose
=====================================  ======  ==========================================
``GET  /audit``                        admin   this firm's audit trail, paginated
``GET  /audit/actions``                admin   the vocabulary, for the filter control
``GET  /privacy/export``               bearer  everything we hold on you (DPDP §11)
``POST /privacy/erasure``              bearer  delete your account (DPDP §12)
=====================================  ======  ==========================================

F-5: the trail existed and nobody could see it
----------------------------------------------

``audit_log`` has been written on every auth event, export, share-link creation and
deletion since Phase 0, and no route read it. A trail nobody can read is a compliance
artefact, not a security control — the point of it is that a firm admin can answer
"who exported the Sharma drawings, and when". ``GET /audit`` is that, firm-scoped
through the same :class:`~garh_api.tenancy.Repository` as everything else, so it can
only ever show the caller's own firm.

Two decisions inside it are worth naming:

* **Admin only.** The trail says which colleague did what and from which address.
  Inside a small practice that is a management tool, not a peer-visible feed.
* **Meta is redacted on the way out.** Audit ``meta`` is free-form JSONB written by a
  dozen call sites, and this route is the first thing that renders it to a human. A
  future writer who puts a code or a token in there must not turn this screen into a
  secret-disclosure surface, so :func:`redact_meta` blanks anything whose key looks
  like a credential — a belt on top of the braces of "don't write secrets to the
  audit log". ``tests/test_privacy.py`` breaks the redaction deliberately to prove the
  gate fires.

F-6: DPDP, and the one hard question it asks
--------------------------------------------

India's Digital Personal Data Protection Act, 2023 gives a Data Principal a right to
their data (§11) and a right to erasure (§12). ``GET /privacy/export`` is the first.
The second runs into the op log, and the answer is deliberate:

    **Erasure anonymises the actor and keeps the op.**

Model state is ``fold(ops)``. An op is not a record *about* a person, it is a
sentence of the design — "this wall moved 300mm east" — that happens to carry who
typed it. Deleting one architect's ops would silently corrupt every project they
touched, including colleagues' projects and municipal drawing sets already submitted,
and would destroy the provenance that makes those drawings defensible. So erasure
removes the ``users`` row (which is the personal data: name, email, CoA number), nulls
``ops.actor`` and ``share_links.created_by``, and replaces the denormalised author
name on their comments. What survives is "someone at this firm, no longer
identifiable, made this edit", which is not personal data.

The ``audit_log`` rows are kept, also deliberately. They hold a user id and an IP, no
name, and they are the integrity record for a regulated deliverable — retained under
the Act's legal-obligation/legitimate-use basis rather than erased on request. That is
a decision a lawyer should confirm before launch; it is written down here so it can be
argued with instead of discovered.

One refusal: erasing the firm's **only admin** is blocked, with a next step. It is not
this person's data that stops it — it is that erasing them strands every colleague
with a tenant nobody can administer, and §12 does not require us to take other
people's access away with our own.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Query
from pydantic import Field

from garh_api.auth import LiveSession
from garh_api.deps import AdminTenant, DbSession, Sessions, Tenant
from garh_api.errors import PROBLEM_RESPONSES, AuthenticationError, ConflictError
from garh_api.repositories.audit_log import (
    ACTION_USER_REMOVED,
    AUDIT_ACTIONS,
    AuditLogRepository,
)
from garh_api.repositories.domain import AuditEntry
from garh_api.repositories.privacy import PrivacyRepository, SeatNameRepository
from garh_api.repositories.two_factor import TwoFactorRepository
from garh_api.routers import PageDep
from garh_api.routers.sessions import describe_user_agent
from garh_api.schemas.auth import AuthModel, Email, UserProfile
from garh_api.seed.runner import ACTION_SEED_COMPLETED
from garh_api.tenancy import TenantCtx
from garh_api.twofactor import TWO_FACTOR_AUDIT_ACTIONS, TwoFactorService, status_payload

audit_router = APIRouter(prefix="/audit", tags=["audit"], responses=PROBLEM_RESPONSES)
router = APIRouter(prefix="/privacy", tags=["privacy"], responses=PROBLEM_RESPONSES)


# ---------------------------------------------------------------------------
# Meta redaction
# ---------------------------------------------------------------------------

#: Substrings that make a ``meta`` key look like a credential. Matched against the key
#: with case and separators stripped, so ``otpCode``, ``otp_code`` and ``OTPCODE`` all
#: hit. Kept narrow enough not to blank useful context: ``family``, ``ip`` and
#: ``userAgent`` are the whole value of an auth row and stay visible.
#:
#: This is defence in depth, not the primary control. The primary control is that no
#: call site writes a secret to ``meta`` in the first place (see
#: ``AuditLogRepository.record``). This is what stops the *next* call site from
#: turning an admin screen into a disclosure surface.
REDACTED_META_KEY_PARTS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "passcode",
    "otp",
    "apikey",
    "credential",
    "privatekey",
    "recoverycode",
    "hash",
)

#: Keys that are exactly a credential even though they contain none of the above.
REDACTED_META_KEYS: frozenset[str] = frozenset({"code", "codes", "pin"})

#: What a redacted value becomes. A fixed marker rather than removal, so the reader can
#: see that something was withheld instead of wondering whether it was ever written.
REDACTION_MARKER = "[redacted]"

#: A DPDP export is itself a security-relevant event — it puts a copy of personal data
#: outside the system. Declared as a constant here for the same ownership reason as the
#: 2FA actions in :mod:`garh_api.twofactor`; ``AUDIT_ACTIONS`` is its long-term home.
ACTION_PRIVACY_EXPORTED = "privacy.data_exported"

#: Every action that can appear in a *firm's* trail, and therefore everything the admin
#: filter must be able to select. Assembled from the modules that own the constants
#: rather than retyped here, so an action cannot exist in the log and be unselectable
#: in the UI — which is what happened to ``privacy.data_exported``, written on every
#: export by the route below while the filter had never heard of it.
#:
#: ``tenancy.UNSCOPED_AUDIT_ACTION`` is deliberately absent: it is written against the
#: system firm, so no firm admin can ever see one of those rows in their own trail.
#:
#: CONTRACT: ``repositories.audit_log.AUDIT_ACTIONS`` is the long-term home for all of
#: these; the 2FA, privacy and seed constants live beside their features for now (each
#: says so where it is declared) and this set is what keeps them visible meanwhile.
AUDIT_ACTION_VOCABULARY: frozenset[str] = frozenset(
    {*AUDIT_ACTIONS, *TWO_FACTOR_AUDIT_ACTIONS, ACTION_PRIVACY_EXPORTED, ACTION_SEED_COMPLETED}
)


def _normalise_key(key: str) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


#: How far :func:`redact_meta` will walk into nested ``meta`` before it stops looking
#: and redacts. Audit ``meta`` is two levels deep at its worst today; the cap is here
#: so a pathological document cannot recurse the request to death, and it redacts at
#: the boundary rather than passing the value through unread — a value we did not
#: inspect is exactly the one that must not be rendered.
MAX_META_DEPTH: Final = 8


def _redact_value(value: Any, depth: int) -> Any:
    """One ``meta`` value, redacted through whatever containers it is wrapped in.

    Lists are not a special case to be skipped: ``{"providers": [{"apiKey": ...}]}`` is
    the same document as ``{"provider": {"apiKey": ...}}`` with one more layer of
    brackets, and a redactor that walks dicts but not lists is a gate with a hole in
    the shape of the most natural way to write a config blob.
    """
    if depth > MAX_META_DEPTH:
        return REDACTION_MARKER
    if isinstance(value, dict):
        return redact_meta(value, _depth=depth)
    if isinstance(value, list | tuple):
        return [_redact_value(item, depth + 1) for item in value]
    return value


def redact_meta(meta: dict[str, Any] | None, *, _depth: int = 0) -> dict[str, Any]:
    """Blank credential-shaped values in one audit row's ``meta``.

    Recurses into nested objects **and lists of them**, because a writer who nests
    ``{"provider": {"apiKey": ...}}`` or ``{"providers": [{"apiKey": ...}]}`` has the
    same problem one level down. ``_depth`` is internal bookkeeping for
    :data:`MAX_META_DEPTH`; callers pass one argument.
    """
    if not meta:
        return {}
    clean: dict[str, Any] = {}
    for key, value in meta.items():
        normalised = _normalise_key(key)
        if normalised in REDACTED_META_KEYS or any(
            part in normalised for part in REDACTED_META_KEY_PARTS
        ):
            clean[str(key)] = REDACTION_MARKER
        else:
            clean[str(key)] = _redact_value(value, _depth + 1)
    return clean


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class AuditEntryResponse(AuthModel):
    """One row of the trail, as an admin reads it."""

    id: uuid.UUID
    at: datetime = Field(description="When it happened (UTC).")
    action: str = Field(description="``<entity>.<verb>``, e.g. ``export.created``.")
    entity: str
    entity_id: str | None = None
    actor_id: uuid.UUID | None = Field(
        default=None, description="Null for system actions and for erased accounts."
    )
    actor_name: str | None = Field(
        default=None, description="Resolved from the firm's seats; null once erased."
    )
    meta: dict[str, Any] = Field(description="Context, with credential-shaped keys redacted.")


class AuditPageResponse(AuthModel):
    items: list[AuditEntryResponse]
    next_cursor: str | None = None


class AuditActionsResponse(AuthModel):
    """The filter vocabulary, so the UI does not hard-code a copy of it."""

    actions: list[str]


class ErasureRequest(AuthModel):
    """Typed confirmation. Irreversible actions should cost a sentence."""

    confirm_email: Email = Field(
        description="Your own email address, typed back, to confirm this is deliberate."
    )


class ErasureResponse(AuthModel):
    erased: bool = True
    ops_anonymised: int = Field(description="Ops whose actor was cleared. The ops remain.")
    comments_anonymised: int
    share_links_anonymised: int
    sessions_ended: int
    audit_entries_retained: int = Field(
        description="Audit rows kept under the Act's legal-obligation basis."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_user(ctx: TenantCtx) -> uuid.UUID:
    if ctx.user_id is None:  # pragma: no cover - require_tenant guarantees a user
        raise AuthenticationError()
    return ctx.user_id


async def _actor_names(
    session: Any, ctx: TenantCtx, entries: Sequence[AuditEntry]
) -> dict[uuid.UUID, str]:
    """``user_id -> name`` for the actors on *this page* of the trail.

    One query for the whole page rather than one per row — an N+1 on a screen that
    lists 50 audit rows is 50 round trips for data that fits in a dict — but keyed on
    the ids actually present rather than on a page of members. The previous version
    read ``list_members(limit=200)``, which is not a lookup, it is a guess: seat 201
    resolved to ``actorName: null`` and the row read exactly like an erased account.
    """
    return await SeatNameRepository(session, ctx).names_for(
        entry.user_id for entry in entries if entry.user_id is not None
    )


def _audit_response(entry: AuditEntry, names: dict[uuid.UUID, str]) -> AuditEntryResponse:
    return AuditEntryResponse(
        id=entry.id,
        at=entry.created_at,
        action=entry.action,
        entity=entry.entity,
        entity_id=entry.entity_id,
        actor_id=entry.user_id,
        actor_name=names.get(entry.user_id) if entry.user_id else None,
        meta=redact_meta(entry.meta),
    )


def _session_payload(live: LiveSession) -> dict[str, Any]:
    return {
        "id": live.family,
        "startedAt": live.started_at,
        "lastUsedAt": live.last_used_at,
        "ip": live.ip or None,
        "userAgent": live.user_agent or None,
        "device": describe_user_agent(live.user_agent),
    }


# ---------------------------------------------------------------------------
# F-5: the audit trail
# ---------------------------------------------------------------------------


@audit_router.get(
    "",
    response_model=AuditPageResponse,
    summary="This firm's audit trail",
)
async def list_audit(
    ctx: AdminTenant,
    session: DbSession,
    page: PageDep,
    action: Annotated[str | None, Query(max_length=64)] = None,
    entity: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[datetime | None, Query()] = None,
) -> AuditPageResponse:
    """Newest first, cursor-paginated, and never another firm's rows.

    The tenancy guarantee is not re-implemented here: ``AuditLogRepository`` derives
    from the same scoped base as every other repository, so ``WHERE firm_id = :ctx``
    is on the statement whatever filters the caller sends. There is no query parameter
    that widens the scope, because there is no code path that could honour one.
    """
    entries = await AuditLogRepository(session, ctx).list_recent(
        limit=page.limit,
        cursor=page.cursor,
        action=action,
        entity=entity,
        since=since,
    )
    names = await _actor_names(session, ctx, entries.items)
    return AuditPageResponse(
        items=[_audit_response(entry, names) for entry in entries.items],
        next_cursor=entries.next_cursor,
    )


@audit_router.get(
    "/actions",
    response_model=AuditActionsResponse,
    summary="Every action the trail can contain",
)
async def list_audit_actions(ctx: AdminTenant) -> AuditActionsResponse:
    """The filter vocabulary. Admin-only like the trail itself — the *shape* of what a
    product audits is not something to hand an anonymous caller."""
    del ctx  # the dependency is the authorisation; the context is not needed
    return AuditActionsResponse(actions=sorted(AUDIT_ACTION_VOCABULARY))


# ---------------------------------------------------------------------------
# F-6: DPDP export
# ---------------------------------------------------------------------------


@router.get(
    "/export",
    summary="Everything we hold on you (DPDP §11)",
)
async def export_personal_data(
    ctx: Tenant, session: DbSession, sessions: Sessions
) -> dict[str, Any]:
    """A subject-access response for the caller, about the caller.

    Not a `response_model`: the document is a report whose sections will grow with the
    schema, and pinning it to a frozen Pydantic shape guarantees that the next table
    someone adds is silently missing from it. The shape is asserted in
    ``tests/test_privacy.py`` instead, which is the assertion that can actually fail.

    What is deliberately **not** in it: op payloads and drawing content. Those are the
    firm's design data, not the individual's personal data; what the person is owed is
    the fact and extent of their authorship, which is what ``footprint`` reports.
    """
    user_id = _require_user(ctx)
    privacy = PrivacyRepository(session, ctx)
    profile = await privacy.profile(user_id)
    footprint = await privacy.footprint(user_id)
    trail = await privacy.audit_trail(user_id, limit=200)
    comments = await privacy.comments_for(user_id)
    two_factor = TwoFactorService(TwoFactorRepository(session, ctx))

    await AuditLogRepository(session, ctx).record(
        ACTION_PRIVACY_EXPORTED,
        entity="user",
        entity_id=user_id,
        meta={"auditEntries": len(trail.items), "comments": len(comments.items)},
    )

    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "subject": UserProfile.from_user(profile).model_dump(by_alias=True, mode="json"),
        "firm": {"id": str(ctx.firm_id), "role": profile.role},
        "twoFactor": status_payload(await two_factor.status(user_id)),
        "signedInDevices": [
            _session_payload(entry) for entry in await sessions.list_families(user_id)
        ],
        "authTrail": [
            {
                "at": entry.created_at.isoformat(),
                "action": entry.action,
                "entity": entry.entity,
                "meta": redact_meta(entry.meta),
            }
            for entry in trail.items
        ],
        "authTrailTruncated": trail.has_more,
        "comments": [
            {
                "id": str(comment.id),
                "projectId": str(comment.project_id),
                "at": comment.created_at.isoformat(),
                "body": comment.body,
                "resolved": comment.resolved,
            }
            for comment in comments.items
        ],
        # Withheld rows are counted and explained, never silently dropped: a person
        # exercising §11 is owed either their data or the reason they cannot have it.
        "commentsWithheld": comments.withheld,
        "commentsNote": comments.reason,
        "designActivity": {
            "opCount": footprint.op_count,
            "projectIds": [str(value) for value in footprint.project_ids],
            "firstOpAt": footprint.first_op_at.isoformat() if footprint.first_op_at else None,
            "lastOpAt": footprint.last_op_at.isoformat() if footprint.last_op_at else None,
            "shareLinkIds": [str(value) for value in footprint.share_link_ids],
            "note": (
                "Op contents are the firm's design data and are not included. What is "
                "recorded about you is that you authored these edits."
            ),
        },
        "retention": {
            "auditLog": (
                "Audit rows are retained after erasure as the integrity record for "
                "regulated drawing sets. They hold an account id and an IP address, "
                "never your name or email."
            ),
        },
    }


# ---------------------------------------------------------------------------
# F-6: DPDP erasure
# ---------------------------------------------------------------------------


@router.post(
    "/erasure",
    response_model=ErasureResponse,
    summary="Delete your account (DPDP §12)",
)
async def erase_personal_data(
    payload: ErasureRequest,
    ctx: Tenant,
    session: DbSession,
    sessions: Sessions,
) -> ErasureResponse:
    """Erase the caller's account. Irreversible, and it takes effect immediately.

    Order is load-bearing:

    1. confirm the typed email matches the caller (a mis-click must not do this);
    2. refuse if this is the firm's last admin, naming the fix;
    3. **end every session first** — the generation bump fails closed, so if Redis
       cannot answer we stop here with the account intact rather than erase somebody
       whose live access token would keep working for another 15 minutes;
    4. anonymise ops, comments and share links, then delete the ``users`` row;
    5. write the audit row, which outlives the account by design.

    Steps 4 and 5 are one database transaction — the request's — so a failure part-way
    leaves the account untouched rather than half-erased.
    """
    user_id = _require_user(ctx)
    privacy = PrivacyRepository(session, ctx)
    profile = await privacy.profile(user_id)

    if payload.confirm_email.strip().lower() != profile.email.strip().lower():
        raise ConflictError(
            "That email doesn't match the account you're signed in as.",
            action="Type the address you signed in with, exactly.",
        )

    refusal = await privacy.can_erase(user_id)
    if refusal is not None:
        raise ConflictError(refusal, action="Promote another admin, then try again.")

    # Counted before the erasure, because after it the actor id is still on the rows
    # but the account it names is gone — and the number is what the response promises.
    retained = await privacy.audit_trail_size(user_id)

    await sessions.bump_generation(user_id)
    sessions_ended = await sessions.revoke_all_families(user_id)

    outcome = await privacy.erase(user_id)

    await AuditLogRepository(session, ctx).record(
        ACTION_USER_REMOVED,
        entity="user",
        entity_id=user_id,
        meta={
            "reason": "dpdp_erasure",
            "opsAnonymised": outcome.ops_anonymised,
            "commentsAnonymised": outcome.comments_anonymised,
            "shareLinksAnonymised": outcome.share_links_anonymised,
            "sessionsEnded": sessions_ended,
            "twoFactorRemoved": outcome.two_factor_removed,
        },
    )
    return ErasureResponse(
        ops_anonymised=outcome.ops_anonymised,
        comments_anonymised=outcome.comments_anonymised,
        share_links_anonymised=outcome.share_links_anonymised,
        sessions_ended=sessions_ended,
        audit_entries_retained=retained,
    )


__all__ = [
    "ACTION_PRIVACY_EXPORTED",
    "REDACTED_META_KEYS",
    "REDACTED_META_KEY_PARTS",
    "REDACTION_MARKER",
    "audit_router",
    "redact_meta",
    "router",
]
