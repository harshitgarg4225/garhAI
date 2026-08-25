"""THE tenancy enforcement point (playbook §13, locked decision "Multi-tenancy").

Design contract, in one paragraph: a route handler never touches a table. It builds a
:class:`TenantCtx` from the verified JWT (or from a resolved share-link token) and
hands it to a repository. Every repository derives from :class:`Repository`, whose
constructor *requires* a ``TenantCtx`` and whose only query builder,
``_scoped_select``, unconditionally appends ``WHERE firm_id = :ctx.firm_id``. There is
no method on the base class that yields an unfiltered query, and inserts overwrite
``firm_id`` from the context rather than trusting the caller. Cross-tenant reads
therefore return "not found" instead of another firm's data — structurally, not by
remembering to add a filter.

Escape hatch — exactly one, and it is loud:

    :func:`system_unscoped_session`

Cross-firm work genuinely exists (nightly snapshot compaction, orphaned-render
sweeps, queue-depth metrics). That function is the only sanctioned path to an
unscoped session. It demands ``task=`` and ``reason=`` keywords, logs at WARNING on
entry and exit, and writes an ``audit_log`` row under
:data:`SYSTEM_FIRM_ID`. CI should lint that its name appears only in worker/ops code
and never under ``garh_api/routers/``.

Deliberate, narrow non-tenant exceptions (documented in each module, all pre-auth or
global-config, none of them able to read tenant *content*):

* ``repositories.auth_directory.AuthDirectoryRepository`` — email → principal, signup.
* ``repositories.otp.OtpCodeRepository`` — OTP challenges (no ``firm_id`` exists yet).
* ``repositories.flags.FlagRepository`` — global feature flags read at boot.
* ``repositories.share_links.ShareTokenResolver`` — token hash → firm/project/scope,
  so the public viewer can *construct* a ``share_viewer`` context.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Generic, TypeVar

from sqlalchemy import Select, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from garh_api.logging import get_logger
from garh_api.models import AuditLog

#: Roles an actor can hold inside a :class:`TenantCtx`.
#:
#: ``admin``/``member`` mirror ``users.role``. ``share_viewer`` is an anonymous
#: client on a share link (read-only, may comment when the scope says so).
#: ``system`` is a worker acting for one firm — it still carries a real ``firm_id``
#: and is still fully scoped; it is NOT the escape hatch.
ACTOR_ROLES: tuple[str, ...] = ("admin", "member", "share_viewer", "system")

#: Roles that may mutate tenant data.
WRITE_ROLES: tuple[str, ...] = ("admin", "member", "system")

#: Sentinel firm for rows that belong to no tenant (escape-hatch audit entries).
SYSTEM_FIRM_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

#: Greppable name of the single sanctioned unscoped path. CI lints on this string.
UNSCOPED_ESCAPE_HATCH = "system_unscoped_session"

#: Audit action written on every escape-hatch use.
UNSCOPED_AUDIT_ACTION = "system.unscoped_session"

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Typed errors — the router layer maps these straight onto problem+json (§11)
# ---------------------------------------------------------------------------


class TenancyError(Exception):
    """Base class for every error raised by the repository layer.

    Carries the three fields §11's problem+json needs: ``code``, ``message``,
    ``action``. The router layer only needs one exception handler per subclass, or
    one generic handler keyed on :attr:`http_status`.
    """

    http_status: ClassVar[int] = 500
    code: ClassVar[str] = "internal_error"
    action: ClassVar[str] = "Try again. If it keeps happening, contact support."

    def as_problem(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "action": self.action}


class TenantContextRequiredError(TenancyError):
    """A repository was constructed without a usable :class:`TenantCtx`."""

    http_status = 401
    code = "tenant_context_required"
    action = "Sign in again."


class CrossTenantAccessError(TenancyError):
    """A row from another firm reached a scoped repository.

    Should be unreachable through normal use; it exists so that if a row ever
    arrives from outside a scoped query (a cache, a worker payload, a bug) the
    layer fails loudly instead of leaking. Routers must render this as **404**,
    never 403 — a 403 would confirm the resource exists.
    """

    http_status = 404
    code = "not_found"
    action = "Check the link or go back to your dashboard."


class EntityNotFoundError(TenancyError):
    """No such row *for this firm*. Also what a cross-tenant read looks like."""

    http_status = 404
    code = "not_found"
    action = "Check the link or go back to your dashboard."

    def __init__(self, entity: str, entity_id: Any) -> None:
        super().__init__("%s %s was not found." % (entity.replace("_", " ").capitalize(), entity_id))
        self.entity = entity
        self.entity_id = entity_id


class PermissionDeniedError(TenancyError):
    """The actor's role is insufficient (admin-only action, read-only viewer)."""

    http_status = 403
    code = "permission_denied"
    action = "Ask a firm admin to do this."


class InvalidCursorError(TenancyError):
    """Pagination cursor is malformed or not ours."""

    http_status = 400
    code = "invalid_cursor"
    action = "Reload the list from the start."


class OpSequenceConflictError(TenancyError):
    """Optimistic-concurrency failure on the op log — §11's 409.

    Raised by :meth:`garh_api.repositories.ops.OpRepository.append` when the caller's
    ``base_idx`` is not the current head of ``(project_id, version_branch)``: another
    writer (a copilot apply, a solver option, a second tab) got there first.

    The router turns this into ``409 Conflict`` and includes :attr:`head_idx` so the
    client can ``GET /projects/:id/ops?since=base_idx``, rebase its optimistic queue
    and retry. Never retry server-side: the ops may no longer be valid against the
    new state.
    """

    http_status = 409
    code = "op_sequence_conflict"
    action = "Fetch ops since your base index, rebase, and re-send."

    def __init__(
        self,
        *,
        project_id: uuid.UUID,
        version_branch: uuid.UUID,
        base_idx: int,
        head_idx: int,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            detail
            or (
                "This design moved on while you were editing (you were at op %d, it is now at "
                "op %d)." % (base_idx, head_idx)
            )
        )
        self.project_id = project_id
        self.version_branch = version_branch
        self.base_idx = base_idx
        self.head_idx = head_idx

    def as_problem(self) -> dict[str, Any]:
        problem = super().as_problem()
        problem.update(
            {
                "projectId": str(self.project_id),
                "versionBranch": str(self.version_branch),
                "baseIdx": self.base_idx,
                "headIdx": self.head_idx,
            }
        )
        return problem


class RepositoryUsageError(TenancyError):
    """Programmer error inside the API (bad enum value, empty batch, ...).

    Distinct from a validation error on user input: this means the caller wrote code
    the repository cannot honour, so it must not be mapped to a friendly 400.
    """

    http_status = 500
    code = "internal_error"


# ---------------------------------------------------------------------------
# TenantCtx
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantCtx:
    """Who is acting, for which firm. Required by every repository.

    Constructor::

        TenantCtx(
            firm_id: uuid.UUID,
            user_id: uuid.UUID | None = None,
            role: str = "member",
            request_id: str | None = None,
            share_link_id: uuid.UUID | None = None,
            scope: dict[str, Any] = {},          # share-link scope, empty for firm users
        )

    Invariants (enforced in ``__post_init__``, so an invalid context cannot exist):

    * ``firm_id`` is a real UUID and is not :data:`SYSTEM_FIRM_ID`.
    * ``role`` ∈ :data:`ACTOR_ROLES`.
    * human roles (``admin``/``member``) must carry a ``user_id``.
    * ``share_viewer`` must carry a ``share_link_id`` and must not carry a ``user_id``.
    """

    firm_id: uuid.UUID
    user_id: uuid.UUID | None = None
    role: str = "member"
    request_id: str | None = None
    share_link_id: uuid.UUID | None = None
    scope: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.firm_id, uuid.UUID):
            raise TenantContextRequiredError("firm_id must be a UUID, got %r." % (self.firm_id,))
        if self.firm_id == SYSTEM_FIRM_ID:
            raise TenantContextRequiredError(
                "SYSTEM_FIRM_ID is not a tenant. Cross-firm work goes through %s()."
                % UNSCOPED_ESCAPE_HATCH
            )
        if self.role not in ACTOR_ROLES:
            raise TenantContextRequiredError(
                "role must be one of %s, got %r." % (", ".join(ACTOR_ROLES), self.role)
            )
        if self.role in ("admin", "member") and self.user_id is None:
            raise TenantContextRequiredError("role %r requires a user_id." % self.role)
        if self.role == "share_viewer":
            if self.share_link_id is None:
                raise TenantContextRequiredError("share_viewer requires a share_link_id.")
            if self.user_id is not None:
                raise TenantContextRequiredError("share_viewer must not carry a user_id.")

    # -- role helpers --------------------------------------------------
    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_share_viewer(self) -> bool:
        return self.role == "share_viewer"

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES

    def require_admin(self, what: str = "this action") -> None:
        if not self.is_admin:
            raise PermissionDeniedError("Only a firm admin can perform %s." % what)

    def require_write(self, what: str = "this change") -> None:
        if not self.can_write:
            raise PermissionDeniedError("This link is read-only, so %s isn't allowed." % what)

    def require_scope(self, section: str) -> None:
        """Share-link section gate. Firm users always pass."""
        if not self.is_share_viewer:
            return
        sections = self.scope.get("sections") or []
        if section not in sections:
            raise PermissionDeniedError("This link doesn't include %s." % section)

    # -- factories -----------------------------------------------------
    @classmethod
    def for_system(cls, firm_id: uuid.UUID, request_id: str | None = None) -> TenantCtx:
        """A worker acting on behalf of one firm. Still fully scoped."""
        return cls(firm_id=firm_id, user_id=None, role="system", request_id=request_id)

    @classmethod
    def for_share_viewer(
        cls,
        firm_id: uuid.UUID,
        share_link_id: uuid.UUID,
        scope: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> TenantCtx:
        return cls(
            firm_id=firm_id,
            user_id=None,
            role="share_viewer",
            request_id=request_id,
            share_link_id=share_link_id,
            scope=dict(scope or {}),
        )

    def log_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {"firm_id": str(self.firm_id), "role": self.role}
        if self.user_id is not None:
            fields["user_id"] = str(self.user_id)
        if self.request_id:
            fields["request_id"] = self.request_id
        return fields


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

DomainT = TypeVar("DomainT")
RowT = TypeVar("RowT")

#: Default / maximum page sizes for cursor pagination (§11 "cursor pagination").
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class Page(Generic[DomainT]):
    """One page of domain objects plus an opaque forward cursor."""

    items: list[DomainT]
    next_cursor: str | None = None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Any:
        return iter(self.items)


def encode_cursor(created_at: datetime, entity_id: uuid.UUID) -> str:
    """Keyset cursor over ``(created_at, id)`` — stable under inserts."""
    raw = "%s|%s" % (created_at.astimezone(timezone.utc).isoformat(), entity_id)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        stamp, _, ident = raw.partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursorError("That page cursor isn't valid.") from exc


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(limit), MAX_PAGE_SIZE))


# ---------------------------------------------------------------------------
# The base repository
# ---------------------------------------------------------------------------


class Repository(Generic[RowT, DomainT]):
    """Firm-scoped data access. Subclass this; never query a table anywhere else.

    Constructor::

        Repository(session: AsyncSession, ctx: TenantCtx)

    Subclass contract — set two class attributes and implement one method::

        class ProjectRepository(Repository[models.Project, Project]):
            row_type = models.Project
            entity_name = "project"

            def to_domain(self, row: models.Project) -> Project:
                return Project.from_row(row)

    What the base class deliberately does **not** provide: any way to build a query
    without the ``firm_id`` filter, any public handle on the session, and any
    ``commit()``. Those omissions are the security property — see the module
    docstring. Repositories ``flush()`` so generated ids/defaults are readable;
    committing belongs to the request or worker unit of work.
    """

    #: The mapped class this repository serves. Must inherit ``TenantOwned``.
    row_type: ClassVar[Any]
    #: Singular snake_case name used in errors and audit rows, e.g. ``design_version``.
    entity_name: ClassVar[str] = "record"
    #: Column pair used for keyset pagination.
    _order_columns: ClassVar[tuple[str, str]] = ("created_at", "id")

    def __init__(self, session: AsyncSession, ctx: TenantCtx) -> None:
        if session is None:
            raise RepositoryUsageError("%s needs a database session." % type(self).__name__)
        if not isinstance(ctx, TenantCtx):
            raise TenantContextRequiredError(
                "%s requires a TenantCtx; got %r. Route handlers must resolve the tenant "
                "before touching data." % (type(self).__name__, type(ctx).__name__)
            )
        row_type = getattr(type(self), "row_type", None)
        if row_type is None:
            raise RepositoryUsageError("%s must set row_type." % type(self).__name__)
        if not getattr(row_type, "__tenant_owned__", False):
            raise RepositoryUsageError(
                "%s is not marked TenantOwned, so it cannot be served by a firm-scoped "
                "repository. Non-tenant tables get an explicitly documented non-tenant "
                "repository instead." % getattr(row_type, "__name__", row_type)
            )
        self._session = session
        self._ctx = ctx
        self._log = _log.bind(repository=type(self).__name__, **ctx.log_fields())

    # -- context -------------------------------------------------------
    @property
    def ctx(self) -> TenantCtx:
        return self._ctx

    @property
    def firm_id(self) -> uuid.UUID:
        return self._ctx.firm_id

    @property
    def actor_id(self) -> uuid.UUID | None:
        return self._ctx.user_id

    # -- scoping -------------------------------------------------------
    def _tenant_column(self) -> ColumnElement[Any]:
        """The column that carries the tenant id.

        ``firm_id`` for every table except ``firms`` itself, where the tenant id *is*
        the primary key. :class:`~garh_api.repositories.firms.FirmRepository`
        overrides this; nothing else should.
        """
        return type(self).row_type.firm_id

    def _scoped_select(self, *entities: Any) -> Select[Any]:
        """The ONLY query builder. Always firm-filtered — there is no opt-out."""
        row_type = type(self).row_type
        stmt = select(*(entities or (row_type,)))
        return stmt.where(self._tenant_column() == self.firm_id)

    def _scoped_where(self) -> ColumnElement[bool]:
        return self._tenant_column() == self.firm_id

    def _assert_owned(self, row: Any) -> None:
        """Guard for rows that arrive from outside a scoped query."""
        owner = getattr(row, "firm_id", None)
        if owner is None:
            owner = getattr(row, "id", None)  # firms
        if owner != self.firm_id:
            self._log.warning(
                "tenancy.cross_tenant_row_rejected",
                entity=type(self).entity_name,
                row_firm_id=str(owner),
            )
            raise CrossTenantAccessError("%s was not found." % type(self).entity_name)

    # -- reads ---------------------------------------------------------
    async def _row_by_id(self, entity_id: Any, *, for_update: bool = False) -> RowT | None:
        stmt = self._scoped_select().where(type(self).row_type.id == entity_id).limit(1)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def _require_row(self, entity_id: Any, *, for_update: bool = False) -> RowT:
        row = await self._row_by_id(entity_id, for_update=for_update)
        if row is None:
            # A row belonging to another firm is indistinguishable from a missing one,
            # on purpose (§13: cross-tenant fetch → 404).
            raise EntityNotFoundError(type(self).entity_name, entity_id)
        return row

    async def _first(self, stmt: Select[Any]) -> RowT | None:
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def _all(self, stmt: Select[Any]) -> list[RowT]:
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _count(self, stmt: Select[Any] | None = None) -> int:
        base = stmt if stmt is not None else self._scoped_select()
        result = await self._session.execute(
            select(func.count()).select_from(base.subquery())
        )
        return int(result.scalar_one())

    async def _page(
        self,
        stmt: Select[Any] | None = None,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        newest_first: bool = True,
    ) -> Page[DomainT]:
        """Keyset-paginate a scoped statement and map rows to domain objects."""
        row_type = type(self).row_type
        order_col = getattr(row_type, type(self)._order_columns[0])
        tiebreak_col = getattr(row_type, type(self)._order_columns[1])
        size = clamp_limit(limit)
        query = stmt if stmt is not None else self._scoped_select()

        if cursor:
            at, ident = decode_cursor(cursor)
            if newest_first:
                query = query.where(
                    (order_col < at) | ((order_col == at) & (tiebreak_col < ident))
                )
            else:
                query = query.where(
                    (order_col > at) | ((order_col == at) & (tiebreak_col > ident))
                )

        ordering = (
            (order_col.desc(), tiebreak_col.desc())
            if newest_first
            else (order_col.asc(), tiebreak_col.asc())
        )
        rows = await self._all(query.order_by(*ordering).limit(size + 1))

        next_cursor: str | None = None
        if len(rows) > size:
            rows = rows[:size]
            last = rows[-1]
            next_cursor = encode_cursor(
                getattr(last, type(self)._order_columns[0]),
                getattr(last, type(self)._order_columns[1]),
            )
        return Page(items=[self.to_domain(row) for row in rows], next_cursor=next_cursor)

    # -- writes --------------------------------------------------------
    def _new_row(self, **values: Any) -> RowT:
        """Build a row with ``firm_id`` forced from the context.

        A caller cannot insert into another firm even by passing ``firm_id`` —
        a mismatching value is a :class:`CrossTenantAccessError`, not a silent
        override.
        """
        supplied = values.pop("firm_id", None)
        if supplied is not None and supplied != self.firm_id:
            raise CrossTenantAccessError(
                "Refusing to write a %s owned by another firm." % type(self).entity_name
            )
        row_type = type(self).row_type
        row: Any = row_type(firm_id=self.firm_id, **values)
        return row

    async def _insert(self, row: RowT) -> RowT:
        self._assert_owned(row)
        self._session.add(row)
        await self._session.flush()
        return row

    async def _insert_many(self, rows: Sequence[RowT]) -> list[RowT]:
        for row in rows:
            self._assert_owned(row)
        self._session.add_all(list(rows))
        await self._session.flush()
        return list(rows)

    async def _apply_patch(self, row: RowT, patch: dict[str, Any]) -> RowT:
        """Assign only non-None values, never ``firm_id``/``id``/timestamps."""
        self._assert_owned(row)
        blocked = {"firm_id", "id", "created_at", "updated_at", "seq"}
        for key, value in patch.items():
            if value is None or key in blocked:
                continue
            if not hasattr(row, key):
                raise RepositoryUsageError(
                    "%s has no field %r." % (type(self).entity_name, key)
                )
            setattr(row, key, value)
        await self._session.flush()
        return row

    async def _delete_by_id(self, entity_id: Any) -> bool:
        stmt = (
            delete(type(self).row_type)
            .where(self._scoped_where())
            .where(type(self).row_type.id == entity_id)
        )
        result = await self._session.execute(stmt)
        return bool(result.rowcount)

    async def flush(self) -> None:
        """Push pending changes so DB defaults/ids are readable. Never commits."""
        await self._session.flush()

    # -- mapping -------------------------------------------------------
    def to_domain(self, row: RowT) -> DomainT:
        raise NotImplementedError(
            "%s must implement to_domain()." % type(self).__name__
        )

    # -- generic public surface ---------------------------------------
    async def get(self, entity_id: Any) -> DomainT | None:
        row = await self._row_by_id(entity_id)
        return None if row is None else self.to_domain(row)

    async def require(self, entity_id: Any) -> DomainT:
        return self.to_domain(await self._require_row(entity_id))

    async def exists(self, entity_id: Any) -> bool:
        stmt = (
            self._scoped_select(type(self).row_type.id)
            .where(type(self).row_type.id == entity_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def count(self) -> int:
        return await self._count()

    async def delete(self, entity_id: Any) -> bool:
        """Delete one row. Returns False when it did not exist for this firm."""
        self._ctx.require_write("deleting a %s" % type(self).entity_name)
        deleted = await self._delete_by_id(entity_id)
        if deleted:
            self._log.info(
                "repository.deleted", entity=type(self).entity_name, entity_id=str(entity_id)
            )
        return deleted


class ProjectScopedRepository(Repository[RowT, DomainT]):
    """Base for tables hanging off a project.

    Adds ``_project_scoped_select(project_id)``: firm filter **and** project filter,
    so a valid project id from another firm still yields nothing.
    """

    def _project_scoped_select(self, project_id: uuid.UUID, *entities: Any) -> Select[Any]:
        return self._scoped_select(*entities).where(
            type(self).row_type.project_id == project_id
        )

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        return await self._count(self._project_scoped_select(project_id))

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        newest_first: bool = True,
    ) -> Page[DomainT]:
        return await self._page(
            self._project_scoped_select(project_id),
            limit=limit,
            cursor=cursor,
            newest_first=newest_first,
        )


# ---------------------------------------------------------------------------
# The escape hatch
# ---------------------------------------------------------------------------


@asynccontextmanager
async def system_unscoped_session(
    *,
    task: str,
    reason: str,
    actor: str = "worker",
) -> AsyncIterator[AsyncSession]:
    """The ONE sanctioned cross-firm database path. Audited, logged, greppable.

    Legitimate uses — all of them background/ops work that spans tenants by nature:

    * snapshot compaction across every project (playbook §2, N=200 folding),
    * marking renders stale / sweeping orphaned annotations after schema changes,
    * queue-depth and usage metrics (§18 observability),
    * the seed script and Alembic data migrations,
    * expired-OTP and expired-share-link purges.

    Illegitimate uses — if you reach for it here, you have a bug: anything serving an
    HTTP request, anything that already knows its ``firm_id`` (use
    ``TenantCtx.for_system(firm_id)`` and a normal repository), anything "just to
    check whether the row exists in another firm".

    Guarantees: WARNING log on open and on close (with duration and outcome), plus an
    ``audit_log`` row under :data:`SYSTEM_FIRM_ID` recording ``task``/``reason``/
    ``actor``. It commits nothing on your behalf — commit inside the block.

    CI lint (please keep this true)::

        grep -rn "system_unscoped_session" apps/api/garh_api/routers/ && exit 1

    Example::

        async with system_unscoped_session(
            task="snapshot_compaction",
            reason="fold snapshots for every project past 200 ops (playbook §2)",
        ) as session:
            ...
            await session.commit()
    """
    if not task or not reason:
        raise RepositoryUsageError(
            "%s() requires task= and reason=; an unaudited unscoped session is not "
            "allowed." % UNSCOPED_ESCAPE_HATCH
        )

    # Imported here: db imports config, and importing it at module scope would make
    # `tenancy` unimportable in unit tests that never touch a database.
    from garh_api.db import get_sessionmaker

    started = datetime.now(timezone.utc)
    hatch_log = _log.bind(task=task, actor=actor, escape_hatch=UNSCOPED_ESCAPE_HATCH)
    hatch_log.warning("tenancy.unscoped_session.opened", reason=reason)

    session = get_sessionmaker()()
    outcome = "ok"
    error: str | None = None
    try:
        yield session
    except BaseException as exc:
        outcome = "error"
        error = "%s: %s" % (type(exc).__name__, exc)
        await session.rollback()
        raise
    finally:
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        try:
            await _write_unscoped_audit(
                task=task,
                reason=reason,
                actor=actor,
                outcome=outcome,
                duration_ms=duration_ms,
                error=error,
            )
        except Exception as audit_exc:  # noqa: BLE001 - audit must not mask the real error
            hatch_log.error(
                "tenancy.unscoped_session.audit_failed",
                error="%s: %s" % (type(audit_exc).__name__, audit_exc),
            )
        await session.close()
        hatch_log.warning(
            "tenancy.unscoped_session.closed",
            outcome=outcome,
            duration_ms=duration_ms,
            error=error,
        )


async def _write_unscoped_audit(
    *,
    task: str,
    reason: str,
    actor: str,
    outcome: str,
    duration_ms: int,
    error: str | None,
) -> None:
    """Write the escape-hatch audit row in its own session/transaction.

    Its own transaction on purpose: the audit must survive the caller rolling back.
    """
    from garh_api.db import get_sessionmaker

    async with get_sessionmaker()() as audit_session:
        await audit_session.execute(
            insert(AuditLog).values(
                firm_id=SYSTEM_FIRM_ID,
                user_id=None,
                action=UNSCOPED_AUDIT_ACTION,
                entity="database",
                entity_id=task,
                meta={
                    "task": task,
                    "reason": reason,
                    "actor": actor,
                    "outcome": outcome,
                    "duration_ms": duration_ms,
                    "error": error,
                },
            )
        )
        await audit_session.commit()


__all__ = [
    "ACTOR_ROLES",
    "CrossTenantAccessError",
    "DEFAULT_PAGE_SIZE",
    "EntityNotFoundError",
    "InvalidCursorError",
    "MAX_PAGE_SIZE",
    "OpSequenceConflictError",
    "Page",
    "PermissionDeniedError",
    "ProjectScopedRepository",
    "Repository",
    "RepositoryUsageError",
    "SYSTEM_FIRM_ID",
    "TenancyError",
    "TenantContextRequiredError",
    "TenantCtx",
    "UNSCOPED_AUDIT_ACTION",
    "UNSCOPED_ESCAPE_HATCH",
    "WRITE_ROLES",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
    "system_unscoped_session",
]
