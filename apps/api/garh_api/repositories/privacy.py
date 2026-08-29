"""DPDP data access (F-6): what we hold on one person, and how to stop holding it.

India's Digital Personal Data Protection Act, 2023 gives a Data Principal the right to
a copy of their personal data (§11) and the right to have it erased (§12). This module
is the *data* half of both; the policy and the HTTP shapes are in
:mod:`garh_api.routers.privacy`, which is also where the erasure decision is written
down in full.

The one decision worth repeating here, because it constrains every query below:

    **An op is not the user's personal data. It is the design.**

``ops`` is the whole product — model state is ``fold(ops)``, and a project's history is
its drawing set's provenance. Deleting one architect's ops would corrupt every project
they ever touched, including projects belonging to colleagues who are not exercising
any right at all. So erasure **anonymises the actor and keeps the op**:
``ops.actor`` is ``ON DELETE SET NULL`` against ``users``, which means removing the
seat leaves ``actor = NULL`` — "someone at this firm, no longer identifiable" — and the
design intact. The same is true of ``share_links.created_by``.

The two places a *name* survives a deleted row are handled explicitly rather than by
the schema, because the schema cannot: ``comments.author_name`` is denormalised text
with no user id beside it, and ``audit_log`` has no foreign keys at all, by design, so
it survives.

That missing user id makes the name the only linkage, and the two rights need it
pointed in opposite directions. **Erasure** matches by name and over-matches on
purpose — scrubbing one row too many is safe, one row too few is not. **Export** must
not guess at all: a subject-access response containing a colleague's comment body is a
disclosure. So :meth:`PrivacyRepository.comments_for` attributes a comment only when
the display name belongs to exactly one seat in the firm and the comment was not left
through a share link, and it reports what it withheld and why. Adding
``comments.author_user_id`` is the follow-up that makes the export exact.

Retaining the audit trail is deliberate: it is the integrity record for a regulated
deliverable and is retained under the Act's legal-obligation basis. It holds an id and
an IP, never a name.

Every query here is built with :meth:`~garh_api.tenancy.Repository._scoped_select`, so
"export everything about me" cannot become "export everything".
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, update

from garh_api import models
from garh_api.repositories.audit_log import AuditLogRepository
from garh_api.repositories.domain import AuditEntry, Comment, User
from garh_api.repositories.two_factor import TwoFactorRepository
from garh_api.repositories.users import UserRepository
from garh_api.tenancy import Page, Repository, RepositoryUsageError, TenantCtx

#: What ``comments.author_name`` becomes when its author is erased. A fixed string, so
#: an erased comment reads the same everywhere and cannot be correlated back.
ERASED_AUTHOR_NAME = "Removed member"

#: Domain that can never receive mail, per RFC 2606 / RFC 6761. The erased ``users``
#: row is *deleted*, so this is only used if a future policy switches to tombstoning.
ERASED_EMAIL_DOMAIN = "erased.invalid"


@dataclass(frozen=True)
class ErasureOutcome:
    """What an erasure actually did, for the audit row and the response body."""

    user_id: uuid.UUID
    ops_anonymised: int
    comments_anonymised: int
    share_links_anonymised: int
    two_factor_removed: bool
    user_row_deleted: bool


#: Why an export withheld comments that carry the caller's display name. Both strings
#: are shown to the person, so they say what happened and what it means — never
#: "some records were omitted".
SHARED_NAME_REASON = (
    "Someone else at this firm uses the same display name, and comments record a name "
    "rather than an account. We can't tell which of you wrote them, so we've included "
    "none of them rather than show you somebody else's."
)
VIEWER_COMMENT_REASON = (
    "Comments left through a share link are written by whoever holds the link, under "
    "whatever name they type. They aren't attributed to your account."
)


@dataclass(frozen=True)
class AttributedComments:
    """Comments an export can attribute to one person, plus what it would not.

    ``withheld`` is a count of rows that carry the person's display name but could not
    be attributed to them. It is deliberately a number and never a body.
    """

    items: list[Comment]
    withheld: int
    reason: str | None


@dataclass(frozen=True)
class PersonalDataFootprint:
    """Counts and ids for the parts of the export that must not be inlined whole.

    An architect's op log can be tens of thousands of rows; a DPDP export is a
    subject-access response, not a database dump. The op *payloads* are design data
    the firm owns, so what the person gets is the fact of their authorship — which
    projects, how many ops, over what period.
    """

    op_count: int
    project_ids: list[uuid.UUID] = field(default_factory=list)
    first_op_at: datetime | None = None
    last_op_at: datetime | None = None
    share_link_ids: list[uuid.UUID] = field(default_factory=list)


class _ActorOpRepository(Repository[models.Op, models.Op]):
    """``ops`` restricted to one actor. Reads and one anonymising update."""

    row_type = models.Op
    entity_name = "op"

    def to_domain(self, row: models.Op) -> models.Op:  # pragma: no cover - identity
        # Rows never leave this module; the facade projects them into counts and ids.
        return row

    async def footprint(
        self, user_id: uuid.UUID
    ) -> tuple[int, list[uuid.UUID], datetime | None, datetime | None]:
        """``(op count, distinct project ids, first op, last op)`` for one actor.

        The timestamps come from two ``LIMIT 1`` reads rather than from pulling every
        ``created_at`` into Python: an architect's op log runs to tens of thousands of
        rows, and a subject-access response must not be the thing that pages out the
        API.
        """
        base = self._scoped_select().where(models.Op.actor == user_id)
        count = await self._count(base)
        if count == 0:
            return 0, [], None, None
        # ``cast``: ``_all`` is typed for whole-row selects and returns
        # ``result.scalars()``, which for a single-column select is that column.
        project_ids = cast(
            "list[uuid.UUID]",
            await self._all(
                self._scoped_select(models.Op.project_id)
                .where(models.Op.actor == user_id)
                .distinct()
                .order_by(models.Op.project_id)
            ),
        )
        first = cast(
            "datetime | None",
            await self._first(
                self._scoped_select(models.Op.created_at)
                .where(models.Op.actor == user_id)
                .order_by(models.Op.created_at.asc())
                .limit(1)
            ),
        )
        last = cast(
            "datetime | None",
            await self._first(
                self._scoped_select(models.Op.created_at)
                .where(models.Op.actor == user_id)
                .order_by(models.Op.created_at.desc())
                .limit(1)
            ),
        )
        return count, project_ids, first, last

    async def anonymise_actor(self, user_id: uuid.UUID) -> int:
        """Null the actor on this user's ops, keeping every op.

        Done explicitly rather than left to the ``ON DELETE SET NULL`` foreign key so
        the count is reportable and so the anonymisation is complete *before* the row
        goes — an erasure that half-succeeded should have dropped the identity, not
        kept it.

        A set-based UPDATE, not a Python loop: this is the one table where the row
        count is unbounded, and ``firm_id`` is on the statement, not on the loop.
        """
        stmt = (
            update(models.Op)
            .where(self._scoped_where())
            .where(models.Op.actor == user_id)
            .values(actor=None)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        self._session.expire_all()
        return int(result.rowcount or 0)


class _AuthoredCommentRepository(Repository[models.Comment, Comment]):
    """``comments`` matched by author name — the only linkage the table offers.

    Read and write pull in **opposite** directions here, and that asymmetry is the
    whole point of this class:

    * :meth:`anonymise_author` (erasure) over-matches on purpose. Scrubbing a name off
      one row too many removes personal data that did not have to go; scrubbing one row
      too few leaves it behind. Over-matching is the safe direction.
    * :meth:`authored_by_seat` (export) must **under**-match. A subject-access response
      that hands one person a colleague's comment body is a disclosure, which is the
      exact opposite of what DPDP §11 asks for. So the export never guesses: the
      ambiguity check lives in :meth:`PrivacyRepository.comments_for`, and comments left
      through a share link are excluded outright because their author is an anonymous
      viewer who typed a display name, not the seat that happens to match it.
    """

    row_type = models.Comment
    entity_name = "comment"

    def to_domain(self, row: models.Comment) -> Comment:
        return Comment.from_row(row)

    async def authored_by_seat(self, name: str) -> list[Comment]:
        """Comments a *firm seat* left under this display name, oldest first.

        ``share_link_id IS NULL`` is the seat filter: everything with a link id was
        written on the anonymous viewer surface, where ``author_name`` is whatever the
        client typed into a text box.
        """
        clean = (name or "").strip()
        if not clean:
            return []
        rows = await self._all(
            self._scoped_select()
            .where(models.Comment.author_name == clean)
            .where(models.Comment.share_link_id.is_(None))
            .order_by(models.Comment.created_at.asc())
        )
        return [self.to_domain(row) for row in rows]

    async def count_by_author_name(self, name: str) -> int:
        """How many rows carry this display name at all, viewer comments included.

        Used only to tell the person how many comments were withheld from their
        export and why — a count, never a body.
        """
        clean = (name or "").strip()
        if not clean:
            return 0
        return await self._count(self._scoped_select().where(models.Comment.author_name == clean))

    async def anonymise_author(self, name: str) -> int:
        """Replace the denormalised author name on this person's comments.

        Matching on the name over-matches two colleagues who share one. That is the
        safe direction for an erasure request and it is the only linkage the table
        offers — ``comments`` has no ``author_user_id``. Adding one is the follow-up
        that makes this exact.
        """
        clean = (name or "").strip()
        if not clean or clean == ERASED_AUTHOR_NAME:
            return 0
        stmt = (
            update(models.Comment)
            .where(self._scoped_where())
            .where(models.Comment.author_name == clean)
            .values(author_name=ERASED_AUTHOR_NAME)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        self._session.expire_all()
        return int(result.rowcount or 0)


class SeatNameRepository(Repository[models.User, User]):
    """``users``, read only for the two questions a privacy answer needs of a name.

    Public because :mod:`garh_api.routers.privacy` needs the same firm-scoped lookup to
    label audit rows, and the alternative it used to use — ``list_members(limit=200)``
    — silently rendered ``actorName: null`` for every seat past the two-hundredth.
    """

    row_type = models.User
    entity_name = "user"

    def to_domain(self, row: models.User) -> User:
        return User.from_row(row)

    async def names_for(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
        """``user_id -> name`` for the ids asked about, and no others.

        One query for a whole page of audit rows, bounded by the page rather than by an
        arbitrary member cap. Ids belonging to another firm resolve to nothing, because
        the select is scoped like every other statement in this file.
        """
        unique = list(dict.fromkeys(user_ids))
        if not unique:
            return {}
        result = await self._session.execute(
            self._scoped_select(models.User.id, models.User.name).where(models.User.id.in_(unique))
        )
        return {row[0]: str(row[1]) for row in result}

    async def count_named(self, name: str) -> int:
        """Seats in this firm whose display name is ``name``, case and spacing ignored.

        Case-insensitive on purpose: "Asha Rao" and "asha rao" are one human name on a
        screen, and this count exists to decide whether a name identifies exactly one
        person. Over-detecting ambiguity withholds; under-detecting it discloses.
        """
        clean = (name or "").strip()
        if not clean:
            return 0
        return await self._count(
            self._scoped_select().where(func.lower(func.btrim(models.User.name)) == clean.lower())
        )


class _CreatedShareLinkRepository(Repository[models.ShareLink, models.ShareLink]):
    """``share_links`` created by one user."""

    row_type = models.ShareLink
    entity_name = "share_link"

    def to_domain(self, row: models.ShareLink) -> models.ShareLink:  # pragma: no cover
        return row

    async def ids_created_by(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return cast(
            "list[uuid.UUID]",
            await self._all(
                self._scoped_select(models.ShareLink.id)
                .where(models.ShareLink.created_by == user_id)
                .order_by(models.ShareLink.created_at.asc())
            ),
        )

    async def anonymise_creator(self, user_id: uuid.UUID) -> int:
        stmt = (
            update(models.ShareLink)
            .where(self._scoped_where())
            .where(models.ShareLink.created_by == user_id)
            .values(created_by=None)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        self._session.expire_all()
        return int(result.rowcount or 0)


class _ActorAuditRepository(Repository[models.AuditLog, AuditEntry]):
    """``audit_log`` restricted to one actor — the person's own auth history."""

    row_type = models.AuditLog
    entity_name = "audit_entry"

    def to_domain(self, row: models.AuditLog) -> AuditEntry:
        return AuditEntry.from_row(row)

    async def for_actor(
        self, user_id: uuid.UUID, *, limit: int | None = None, cursor: str | None = None
    ) -> Page[AuditEntry]:
        return await self._page(
            self._scoped_select().where(models.AuditLog.user_id == user_id),
            limit=limit,
            cursor=cursor,
            newest_first=True,
        )

    async def count_for_actor(self, user_id: uuid.UUID) -> int:
        return await self._count(self._scoped_select().where(models.AuditLog.user_id == user_id))


class PrivacyRepository:
    """Facade over the tables a DPDP request touches.

    Not a :class:`~garh_api.tenancy.Repository` subclass because a DPDP request spans
    five tables and a repository serves one. Every query it runs goes through a real
    firm-scoped repository, so the tenancy guarantee is unchanged: there is no
    statement in this file that is not filtered on ``ctx.firm_id``.
    """

    def __init__(self, session: Any, ctx: TenantCtx) -> None:
        if ctx.user_id is None:
            raise RepositoryUsageError("A privacy request needs a signed-in user.")
        self._ctx = ctx
        self._users = UserRepository(session, ctx)
        self._ops = _ActorOpRepository(session, ctx)
        self._comments = _AuthoredCommentRepository(session, ctx)
        self._seats = SeatNameRepository(session, ctx)
        self._share_links = _CreatedShareLinkRepository(session, ctx)
        self._audit_reads = _ActorAuditRepository(session, ctx)
        self._audit = AuditLogRepository(session, ctx)
        self._two_factor = TwoFactorRepository(session, ctx)

    # -- export --------------------------------------------------------
    async def profile(self, user_id: uuid.UUID) -> User:
        return await self._users.require(user_id)

    async def footprint(self, user_id: uuid.UUID) -> PersonalDataFootprint:
        count, project_ids, first_at, last_at = await self._ops.footprint(user_id)
        return PersonalDataFootprint(
            op_count=count,
            project_ids=project_ids,
            first_op_at=first_at,
            last_op_at=last_at,
            share_link_ids=await self._share_links.ids_created_by(user_id),
        )

    async def comments_for(self, user_id: uuid.UUID) -> AttributedComments:
        """The comments this export can honestly say are the caller's.

        ``comments`` carries no ``author_user_id`` — only ``author_name``, denormalised
        free text that the *client* supplies (``POST /projects/:id/comments`` takes it
        from the request body). Matching a subject-access response on that name alone
        is how one person ends up reading a colleague's comment bodies, so this method
        refuses to guess:

        * a display name shared by two seats in the firm attributes nothing to either;
        * comments left through a share link are never attributed to a seat, because
          the author was an anonymous viewer typing into a text box.

        What is withheld is *counted and explained* rather than silently dropped: a
        person exercising §11 is owed either their data or the reason they cannot have
        it. The exact fix is an ``author_user_id`` column on ``comments`` — see the
        handoff note; until it exists, under-matching is the only safe direction.
        """
        user = await self._users.require(user_id)
        name = (user.name or "").strip()
        if not name:
            return AttributedComments(items=[], withheld=0, reason=None)

        total = await self._comments.count_by_author_name(name)
        if await self._seats.count_named(name) > 1:
            return AttributedComments(items=[], withheld=total, reason=SHARED_NAME_REASON)

        items = await self._comments.authored_by_seat(name)
        return AttributedComments(
            items=items,
            withheld=max(0, total - len(items)),
            reason=VIEWER_COMMENT_REASON if total > len(items) else None,
        )

    async def audit_trail(
        self, user_id: uuid.UUID, *, limit: int | None = None, cursor: str | None = None
    ) -> Page[AuditEntry]:
        return await self._audit_reads.for_actor(user_id, limit=limit, cursor=cursor)

    async def audit_trail_size(self, user_id: uuid.UUID) -> int:
        """How many audit rows name this person — the number erasure *keeps*."""
        return await self._audit_reads.count_for_actor(user_id)

    # -- erasure -------------------------------------------------------
    async def can_erase(self, user_id: uuid.UUID) -> str | None:
        """``None`` if erasure may proceed, otherwise the reason it may not.

        One rule: a firm must keep at least one admin. Erasing the last one would
        strand every colleague with a tenant nobody can administer, and DPDP §12 does
        not require us to destroy other people's access to their own firm.
        """
        user = await self._users.require(user_id)
        if user.role == "admin" and await self._users.count_admins() <= 1:
            return (
                "You're the only admin of this firm. Make someone else an admin first, "
                "then delete your account."
            )
        return None

    async def erase(self, user_id: uuid.UUID) -> ErasureOutcome:
        """Anonymise, then remove the seat. See the module docstring for the shape.

        Order matters and is not cosmetic: every anonymising write happens *before*
        the ``users`` row is deleted, so an erasure interrupted halfway has dropped
        identity rather than kept it. The delete then lets the database do the rest —
        ``user_two_factor`` cascades away, and any actor reference this method somehow
        missed is set to NULL by its own foreign key rather than left dangling.
        """
        user = await self._users.require(user_id)
        ops = await self._ops.anonymise_actor(user_id)
        comments = await self._comments.anonymise_author(user.name)
        share_links = await self._share_links.anonymise_creator(user_id)
        two_factor = await self._two_factor.remove(user_id)
        deleted = await self._users_delete(user_id)
        return ErasureOutcome(
            user_id=user_id,
            ops_anonymised=ops,
            comments_anonymised=comments,
            share_links_anonymised=share_links,
            two_factor_removed=two_factor,
            user_row_deleted=deleted,
        )

    async def _users_delete(self, user_id: uuid.UUID) -> bool:
        """Delete the seat.

        ``UserRepository.remove`` is admin-only seat management and explicitly refuses
        to remove the caller — correct for "an admin removes a colleague", wrong for
        "I am exercising my own right to erasure". The generic scoped
        :meth:`~garh_api.tenancy.Repository.delete` is the same firm-filtered
        statement without that policy, and the policy that *does* apply here
        (:meth:`can_erase`) has already run.
        """
        return await self._users.delete(user_id)

    # -- audit ---------------------------------------------------------
    def audit(self) -> AuditLogRepository:
        """The firm's audit log, for writing the erasure/export rows."""
        return self._audit


__all__ = [
    "ERASED_AUTHOR_NAME",
    "ERASED_EMAIL_DOMAIN",
    "SHARED_NAME_REASON",
    "VIEWER_COMMENT_REASON",
    "AttributedComments",
    "ErasureOutcome",
    "PersonalDataFootprint",
    "PrivacyRepository",
    "SeatNameRepository",
]
