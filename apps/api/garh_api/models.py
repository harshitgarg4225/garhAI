"""SQLAlchemy 2.0 declarative schema for Garh AI (engineering playbook §2).

Rules baked into this module — do not relax them:

* Every table has ``created_at`` / ``updated_at`` (``timestamptz``, DB-side default
  ``now()``; ``updated_at`` is also maintained by the ``garh_set_updated_at`` trigger
  installed by the initial migration so raw-SQL writers from workers stay honest).
* Every table has ``id uuid primary key default gen_random_uuid()`` — the single
  documented exception is :class:`Op`, whose primary key is the monotonic
  ``seq`` (playbook §2 declares ``ops(seq bigserial pk, ...)``); it is implemented
  as an ``IDENTITY`` column, which is the SQL-standard equivalent of ``bigserial``.
* Every tenant-owned table carries ``firm_id uuid not null`` **plus an index**, and
  inherits the :class:`TenantOwned` marker. The marker is what
  :class:`garh_api.tenancy.Repository` checks: a table that is not marked cannot be
  served by a firm-scoped repository, and a table that IS marked can only be
  reached through one.
* Geometry is integer millimetres. Nothing in this schema stores a float length.
  Lengths/coordinates/areas live inside JSONB documents as ints (``mm`` / ``mm²``).
* No ORM ``relationship()`` is declared anywhere on purpose: implicit lazy loads are
  a footgun under asyncio, and the repository layer joins explicitly.

Enum-ish check constraints are mirrored by module-level tuples (``USER_ROLES``,
``PROJECT_STATUSES``, ...). Import those instead of retyping string literals — the
Pydantic schema layer and the router layer must use the same source of truth.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# Controlled vocabularies (mirror the CHECK constraints below, 1:1)
# ---------------------------------------------------------------------------

#: users.role — playbook §2 / product spec "admin/member roles".
USER_ROLES: tuple[str, ...] = ("admin", "member")

#: projects.status — dashboard status chips (Brief / Options / Design / Drawings).
PROJECT_STATUSES: tuple[str, ...] = (
    "draft",
    "brief",
    "options",
    "design",
    "drawings",
    "archived",
)

#: projects.units — display units only; storage is always integer mm.
PROJECT_UNITS: tuple[str, ...] = ("ft-in", "m")

#: plots.source — how the boundary got into the system.
PLOT_SOURCES: tuple[str, ...] = ("manual", "dxf", "seed")

#: briefs.vastu_mode — playbook §5.2/§6.
VASTU_MODES: tuple[str, ...] = ("off", "advisory", "strict")

#: design_versions.kind.
DESIGN_VERSION_KINDS: tuple[str, ...] = ("auto", "named", "option")

#: ops.source — provenance of a mutation (golden rule 3/4: provenance is visible).
OP_SOURCES: tuple[str, ...] = ("manual", "copilot", "solver", "system")

#: solver_jobs.status / render_jobs.status — shared worker job lifecycle.
JOB_STATUSES: tuple[str, ...] = ("queued", "running", "succeeded", "failed", "cancelled")

#: terminal job statuses (no further transitions).
JOB_TERMINAL_STATUSES: tuple[str, ...] = ("succeeded", "failed", "cancelled")

#: render_jobs.mode — playbook §9.
RENDER_MODES: tuple[str, ...] = ("precise", "explore")

#: sheets.kind — the MVP municipal set (playbook §7, product spec F6).
SHEET_KINDS: tuple[str, ...] = (
    "site",
    "floor",
    "elevation",
    "section",
    "schedule",
    "area-statement",
    # D-2, the setting-out plan: a WORKING drawing, not a submission one. It is
    # opt-in (services.drawings.pipeline.WORKING_KINDS) and numbered W-01 rather
    # than in the A-series, but it is still a sheet row, so it belongs in this
    # vocabulary and in ck_sheets_kind. See migration 0005.
    "setting-out",
    # D-7, the structural grid: also a working drawing (W-02). See migration 0006.
    "structural-grid",
)

#: annotations.anchor_kind — what ``anchor_element_id`` points at (playbook §7).
ANNOTATION_ANCHOR_KINDS: tuple[str, ...] = ("element", "dimension", "point", "sheet")

#: credit_events.kind — metering from day one (playbook §2 comment).
CREDIT_EVENT_KINDS: tuple[str, ...] = ("render", "solver", "llm", "export")

#: Tables that are NOT tenant-owned, by design. CI may assert this list is exhaustive.
NON_TENANT_TABLES: tuple[str, ...] = ("flags", "otp_codes")


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base. Every constraint/index in this module is explicitly named so
    the hand-written initial migration and ``alembic revision --autogenerate`` agree."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TenantOwned:
    """Marker for firm-scoped tables.

    Presence of this marker is a machine-checkable promise: the table has a
    ``firm_id`` column and may only be queried through
    :class:`garh_api.tenancy.Repository` (which refuses to serve unmarked tables and
    never builds an unscoped SELECT).
    """

    __tenant_owned__ = True


class UuidPk:
    """``id uuid primary key default gen_random_uuid()`` (needs the pgcrypto extension)."""

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class Timestamps:
    """``created_at`` / ``updated_at`` on every table.

    ``eager_defaults`` is load-bearing on the ASYNC session: an UPDATE flush
    expires ``updated_at`` (it changes server-side), and the next attribute
    read — every repository's ``to_domain(row)`` after a mutation — would
    otherwise trigger a SYNC lazy refresh inside the async session, which
    raises ``MissingGreenlet``. With eager_defaults the flush fetches the new
    server values in the same UPDATE ... RETURNING round trip instead.
    """

    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def _firm_fk(table: str) -> Any:
    """``firm_id`` foreign key with a deterministic constraint name."""
    return ForeignKey(
        "firms.id",
        ondelete="CASCADE",
        name="fk_%s_firm_id_firms" % table,
    )


def _in_check(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join("'%s'" % v for v in values)
    return "%s IN (%s)" % (column, rendered)


JSON_OBJ = text("'{}'::jsonb")
JSON_ARR = text("'[]'::jsonb")


# ---------------------------------------------------------------------------
# Tenancy root
# ---------------------------------------------------------------------------


class Firm(UuidPk, Timestamps, Base):
    """The tenant. ``firms.id`` *is* the ``firm_id`` every other row carries."""

    __tablename__ = "firms"
    __tenant_owned__ = True  # scoped by id, not firm_id — see FirmRepository.

    name: Mapped[str] = mapped_column(Text, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: firm preferences: title-block fields, dimToJamb, default city pack, flag overrides.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)

    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="ck_firms_name_not_blank"),
        Index("ix_firms_created_at", "created_at"),
    )


class User(UuidPk, Timestamps, TenantOwned, Base):
    __tablename__ = "users"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("users"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'member'"))
    #: Council of Architecture registration number (appears on municipal sheets).
    coa_number: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(_in_check("role", USER_ROLES), name="ck_users_role"),
        CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
        CheckConstraint("position('@' in email) > 1", name="ck_users_email_shape"),
        Index("ix_users_firm_id", "firm_id"),
        Index("ix_users_firm_id_created_at", "firm_id", "created_at"),
    )


class UserTwoFactor(UuidPk, Timestamps, TenantOwned, Base):
    """One user's TOTP enrolment (F-4). At most one row per user.

    Postgres and not Redis, deliberately. ``garh_api.auth`` keeps refresh families and
    logout-all generations in Redis and documents the consequence: a flush loses them.
    For a *second factor* that consequence is unacceptable in both directions — a lost
    row either silently downgrades every account to one factor (the "gate that never
    fires" bug class) or locks everyone out. A durable row makes the question
    answerable.

    ``confirmed_at is NULL`` means enrolment was started but never proved: the secret
    exists so the user can scan it, and :mod:`garh_api.twofactor` treats the account as
    single-factor until a live code arrives. ``last_counter`` is the replay guard — a
    TOTP code stays valid for its whole 30-second step, so a code observed over the
    user's shoulder (or in a proxy log) must not be spendable twice.

    ``recovery_hashes`` holds ``sha256`` of each *unused* recovery code and nothing
    else; a spent code is removed from the list rather than flagged, so "how many are
    left" is ``len()`` and there is no second field to drift. 80 bits of entropy per
    code is what makes an unsalted digest safe here (see
    :func:`garh_api.twofactor.generate_recovery_codes`).
    """

    __tablename__ = "user_two_factor"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("user_two_factor"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_two_factor_user_id_users"),
        nullable=False,
    )
    #: base32, no padding — what an authenticator app scans.
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Highest TOTP step already spent. ``-1`` means "nothing spent yet".
    last_counter: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("-1"))
    #: ``["<sha256 hex>", ...]`` for the codes still unused.
    recovery_hashes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=JSON_ARR
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_two_factor_user_id"),
        CheckConstraint("length(btrim(secret)) > 0", name="ck_user_two_factor_secret_not_blank"),
        CheckConstraint("last_counter >= -1", name="ck_user_two_factor_last_counter_range"),
        CheckConstraint(
            "jsonb_typeof(recovery_hashes) = 'array'",
            name="ck_user_two_factor_recovery_hashes_array",
        ),
        Index("ix_user_two_factor_firm_id", "firm_id"),
    )


# ---------------------------------------------------------------------------
# Project + inputs
# ---------------------------------------------------------------------------


class Project(UuidPk, Timestamps, TenantOwned, Base):
    __tablename__ = "projects"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("projects"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    architect_of_record: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_projects_architect_of_record_users",
        ),
        nullable=True,
    )
    units: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ft-in'"))
    #: rulepack id, e.g. ``blr`` / ``ncr`` / ``hyd`` (see ``rulepacks/``).
    city_pack: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    #: D-4 submission details, or NULL for a project nobody is submitting:
    #: ``{"authority": "bbmp", "fields": {"khataNumber": "...", "wardNumber": "..."}}``.
    #: Per-project rather than per-firm because a khata number is a fact about a plot —
    #: two projects in one practice have different ones, and sharing the firm's
    #: title-block template would put one project's number on another's sanction set.
    submission: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(_in_check("status", PROJECT_STATUSES), name="ck_projects_status"),
        CheckConstraint(_in_check("units", PROJECT_UNITS), name="ck_projects_units"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_projects_name_not_blank"),
        Index("ix_projects_firm_id", "firm_id"),
        Index("ix_projects_firm_id_created_at", "firm_id", "created_at"),
        Index("ix_projects_firm_id_status", "firm_id", "status"),
    )


class Plot(UuidPk, Timestamps, TenantOwned, Base):
    """Plot boundary + regulatory context. One per project.

    ``boundary``: ``[{"x": int, "y": int}, ...]`` closed ring, plot-local mm,
    origin at the SW corner, +X east / +Y north (playbook §3).
    ``roads``: ``[{"edgeIndex": int, "widthMm": int | null}, ...]``.
    ``reg_profile``: resolved setbacks/FAR/coverage/height + per-field overrides.
    """

    __tablename__ = "plots"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("plots"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_plots_project_id_projects"),
        nullable=False,
    )
    boundary: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_ARR)
    #: true-north rotation of +Y, integer degrees.
    north_deg: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    roads: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_ARR)
    reg_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=JSON_OBJ
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'manual'"))

    __table_args__ = (
        UniqueConstraint("project_id", name="uq_plots_project_id"),
        CheckConstraint("north_deg >= 0 AND north_deg < 360", name="ck_plots_north_deg_range"),
        CheckConstraint(_in_check("source", PLOT_SOURCES), name="ck_plots_source"),
        CheckConstraint("jsonb_typeof(boundary) = 'array'", name="ck_plots_boundary_array"),
        CheckConstraint("jsonb_typeof(roads) = 'array'", name="ck_plots_roads_array"),
        Index("ix_plots_firm_id", "firm_id"),
    )


class Brief(UuidPk, Timestamps, TenantOwned, Base):
    """Client brief. One per project. ``data`` is the Brief document (rooms with
    target areas in mm², adjacency wishes, facing, budget, style, assumptions[])."""

    __tablename__ = "briefs"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("briefs"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_briefs_project_id_projects"),
        nullable=False,
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    vastu_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'off'"))
    #: completeness meter, 0–100 (product spec F2).
    completeness: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint("project_id", name="uq_briefs_project_id"),
        CheckConstraint(_in_check("vastu_mode", VASTU_MODES), name="ck_briefs_vastu_mode"),
        CheckConstraint(
            "completeness >= 0 AND completeness <= 100", name="ck_briefs_completeness_range"
        ),
        Index("ix_briefs_firm_id", "firm_id"),
    )


# ---------------------------------------------------------------------------
# Op log + versions (the model core's storage, playbook §2/§3/§4)
# ---------------------------------------------------------------------------


class DesignVersion(UuidPk, Timestamps, TenantOwned, Base):
    """A point in the op log, optionally carrying a folded snapshot.

    Fast load = latest ``snapshot`` + ops with ``idx`` beyond ``op_seq_end``.
    ``snapshot_hash`` = sha256 of the canonical JSON of ``snapshot``.
    Snapshots are written every ``OP_SNAPSHOT_INTERVAL`` (200) ops and at every
    named version / solver option.

    ``version_branch`` is a deliberate addition to the §2 sketch: ops are keyed by
    ``(project_id, version_branch, idx)``, so a version row must name the branch it
    belongs to or the "snapshot + tail" load path is ambiguous once options fork.
    """

    __tablename__ = "design_versions"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("design_versions"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "projects.id", ondelete="CASCADE", name="fk_design_versions_project_id_projects"
        ),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "design_versions.id",
            ondelete="SET NULL",
            name="fk_design_versions_parent_id_design_versions",
        ),
        nullable=True,
    )
    version_branch: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    op_seq_start: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    op_seq_end: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'auto'"))

    __table_args__ = (
        CheckConstraint(_in_check("kind", DESIGN_VERSION_KINDS), name="ck_design_versions_kind"),
        CheckConstraint(
            "(snapshot IS NULL) = (snapshot_hash IS NULL)",
            name="ck_design_versions_snapshot_pair",
        ),
        CheckConstraint(
            "kind <> 'named' OR name IS NOT NULL", name="ck_design_versions_named_has_name"
        ),
        CheckConstraint(
            "op_seq_start IS NULL OR op_seq_end IS NULL OR op_seq_end >= op_seq_start",
            name="ck_design_versions_seq_order",
        ),
        Index("ix_design_versions_firm_id", "firm_id"),
        Index("ix_design_versions_project_id_created_at", "project_id", "created_at"),
        Index(
            "ix_design_versions_project_id_version_branch_op_seq_end",
            "project_id",
            "version_branch",
            "op_seq_end",
        ),
        Index("ix_design_versions_parent_id", "parent_id"),
    )


class Op(Timestamps, TenantOwned, Base):
    """The atom (golden rule 1). Model state = fold(ops).

    Primary key is the monotonic ``seq`` (playbook §2 ``bigserial``, implemented as
    ``IDENTITY``) so incremental sync can page by a single global cursor, while
    ``unique(project_id, version_branch, idx)`` is what makes appends optimistically
    concurrent-safe: a stale ``baseIdx`` collides and the client rebases (§11 → HTTP 409).

    ``group_id`` is a deliberate addition to the §2 sketch, required by §4 ("ops carry
    optional groupId; undo/redo operates on groups"; ``solver.apply_option`` and
    copilot multi-step edits are one group).
    """

    __tablename__ = "ops"

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False, start=1), nullable=False)
    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("ops"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_ops_project_id_projects"),
        nullable=False,
    )
    version_branch: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: 0-based position within (project_id, version_branch).
    idx: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: op type from the §4 taxonomy, e.g. ``wall.add``.
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    #: server-computed inverse for undo; null only for ops that are their own inverse.
    inverse: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    actor: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_ops_actor_users"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    #: client-generated idempotency key (§11 "idempotency via clientOpId").
    client_op_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("seq", name="pk_ops"),
        UniqueConstraint(
            "project_id", "version_branch", "idx", name="uq_ops_project_id_version_branch_idx"
        ),
        CheckConstraint("idx >= 0", name="ck_ops_idx_non_negative"),
        CheckConstraint(_in_check("source", OP_SOURCES), name="ck_ops_source"),
        CheckConstraint("length(btrim(type)) > 0", name="ck_ops_type_not_blank"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_ops_payload_object"),
        Index("ix_ops_firm_id", "firm_id"),
        Index("ix_ops_project_id_version_branch_seq", "project_id", "version_branch", "seq"),
        Index(
            "uq_ops_project_id_client_op_id",
            "project_id",
            "client_op_id",
            unique=True,
            postgresql_where=text("client_op_id IS NOT NULL"),
        ),
        Index(
            "ix_ops_project_id_group_id",
            "project_id",
            "group_id",
            postgresql_where=text("group_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class SolverJob(UuidPk, Timestamps, TenantOwned, Base):
    """CP-SAT layout job (playbook §5). ``options`` holds the presentable
    ``PlanOption[]`` (scores + rationale seed facts + plan JSON refs)."""

    __tablename__ = "solver_jobs"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("solver_jobs"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_solver_jobs_project_id_projects"),
        nullable=False,
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    options: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The sentence the solver wants the architect to read — most importantly when it
    #: produced NOTHING, where it carries the stage-A shortfall ("the ground floor is
    #: 8 m² short") instead of leaving a blank screen. Not ``error``: a succeeded job
    #: carrying an error is how a normal outcome gets a red banner.
    banner: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(_in_check("status", JOB_STATUSES), name="ck_solver_jobs_status"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_solver_jobs_progress_range"),
        Index("ix_solver_jobs_firm_id", "firm_id"),
        Index("ix_solver_jobs_project_id_created_at", "project_id", "created_at"),
        Index("ix_solver_jobs_firm_id_status", "firm_id", "status"),
    )


class RenderJob(UuidPk, Timestamps, TenantOwned, Base):
    """Render job (playbook §9). Results are pinned to ``design_version_id``;
    a model edit sets ``stale = true`` ("Design changed since this render")."""

    __tablename__ = "render_jobs"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("render_jobs"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_render_jobs_project_id_projects"),
        nullable=False,
    )
    design_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "design_versions.id",
            ondelete="SET NULL",
            name="fk_render_jobs_design_version_id_design_versions",
        ),
        nullable=True,
    )
    #: camera + preset + captured map object keys (viewport/depth/edges PNGs).
    view: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'mock'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    output_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: §11. The board references this render's prompt actually consumed, as
    #: ``[{"id", "label", "intent"}, …]``. Distinct from ``params["references"]``,
    #: which is the board as it stood when the job was enqueued: a render can carry
    #: a reference it could not apply to its own view, and conflating "sent" with
    #: "followed" is how the render card ends up claiming something untrue.
    references_used: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(_in_check("status", JOB_STATUSES), name="ck_render_jobs_status"),
        CheckConstraint(_in_check("mode", RENDER_MODES), name="ck_render_jobs_mode"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_render_jobs_progress_range"),
        Index("ix_render_jobs_firm_id", "firm_id"),
        Index("ix_render_jobs_project_id_created_at", "project_id", "created_at"),
        Index("ix_render_jobs_firm_id_status", "firm_id", "status"),
        Index("ix_render_jobs_design_version_id", "design_version_id"),
    )


# ---------------------------------------------------------------------------
# Drawings
# ---------------------------------------------------------------------------


class Sheet(UuidPk, Timestamps, TenantOwned, Base):
    """A drawing sheet (playbook §7). ``layout`` = frame/scale/viewport spec +
    generated primitive refs; the geometry itself is re-derived, never hand-stored."""

    __tablename__ = "sheets"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("sheets"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_sheets_project_id_projects"),
        nullable=False,
    )
    design_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "design_versions.id",
            ondelete="SET NULL",
            name="fk_sheets_design_version_id_design_versions",
        ),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    #: municipal sheet number, e.g. ``A-01``.
    number: Mapped[str | None] = mapped_column(Text, nullable=True)
    layout: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(_in_check("kind", SHEET_KINDS), name="ck_sheets_kind"),
        Index("ix_sheets_firm_id", "firm_id"),
        Index("ix_sheets_project_id_design_version_id", "project_id", "design_version_id"),
    )


class Annotation(UuidPk, Timestamps, TenantOwned, Base):
    """Sheet annotation anchored to a model element id (playbook §7).

    When a solver re-run drops the anchor's element id, the row is flagged
    ``orphaned = true`` and surfaces in the Review Tray. No fuzzy re-anchoring in MVP.
    """

    __tablename__ = "annotations"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("annotations"), nullable=False
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sheets.id", ondelete="CASCADE", name="fk_annotations_sheet_id_sheets"),
        nullable=False,
    )
    #: model element id, e.g. ``wall_01J...`` (``{type}_{ulid}``).
    anchor_element_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    anchor_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'element'"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    orphaned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        CheckConstraint(
            _in_check("anchor_kind", ANNOTATION_ANCHOR_KINDS), name="ck_annotations_anchor_kind"
        ),
        CheckConstraint(
            "anchor_kind = 'sheet' OR anchor_element_id IS NOT NULL",
            name="ck_annotations_anchor_present",
        ),
        Index("ix_annotations_firm_id", "firm_id"),
        Index("ix_annotations_sheet_id", "sheet_id"),
        Index("ix_annotations_sheet_id_orphaned", "sheet_id", "orphaned"),
    )


class ComplianceReport(UuidPk, Timestamps, TenantOwned, Base):
    """Frozen output of the rules engine for one design version (playbook §6).

    ``results``: ``[{ruleId, status, actual, limit, cite, fixHint, elements[]}, ...]``
    ``pack_versions``: ``{"nbc-core": "2026.07", "blr": "2026.07", "vastu": "..."}``
    """

    __tablename__ = "compliance_reports"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("compliance_reports"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "projects.id", ondelete="CASCADE", name="fk_compliance_reports_project_id_projects"
        ),
        nullable=False,
    )
    design_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "design_versions.id",
            ondelete="SET NULL",
            name="fk_compliance_reports_design_version_id_design_versions",
        ),
        nullable=True,
    )
    pack_versions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=JSON_OBJ
    )
    results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_ARR)

    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(results) = 'array'", name="ck_compliance_reports_results_array"
        ),
        Index("ix_compliance_reports_firm_id", "firm_id"),
        Index("ix_compliance_reports_project_id_created_at", "project_id", "created_at"),
        Index("ix_compliance_reports_design_version_id", "design_version_id"),
    )


# ---------------------------------------------------------------------------
# Sharing & collaboration
# ---------------------------------------------------------------------------


class ShareLink(UuidPk, Timestamps, TenantOwned, Base):
    """Client share link (§13): random 256-bit token, stored **hashed only**,
    scoped ``{projectId, sections[], canComment}``, expiring, revocable."""

    __tablename__ = "share_links"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("share_links"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_share_links_project_id_projects"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_share_links_created_by_users"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_share_links_token_hash"),
        Index("ix_share_links_firm_id", "firm_id"),
        Index("ix_share_links_project_id", "project_id"),
    )


class Comment(UuidPk, Timestamps, TenantOwned, Base):
    """Pin comment. Authored either by a firm user or by an anonymous share-link
    viewer (``share_link_id`` set, OTP-lite, no login)."""

    __tablename__ = "comments"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("comments"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_comments_project_id_projects"),
        nullable=False,
    )
    share_link_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "share_links.id", ondelete="SET NULL", name="fk_comments_share_link_id_share_links"
        ),
        nullable=True,
    )
    #: ``{"kind": "sheet"|"plan"|"render", "target": "...", "x": mm, "y": mm}``
    anchor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        CheckConstraint("length(btrim(body)) > 0", name="ck_comments_body_not_blank"),
        Index("ix_comments_firm_id", "firm_id"),
        Index("ix_comments_project_id_created_at", "project_id", "created_at"),
        Index("ix_comments_share_link_id", "share_link_id"),
    )


class ProjectUnderlay(UuidPk, Timestamps, TenantOwned, Base):
    """Tracing underlay: a plan IMAGE the architect traces over on the 2D canvas.

    Deliberately a sidecar table, NOT part of the op-fold model. An underlay is a
    tracing aid — it never affects geometry, compliance or drawings — and putting
    it in the model core would demand byte-identical TS/Python twin changes (and
    undo entries for an opacity tweak) for zero product value. One row per project,
    enforced by the unique index; replacing uploads overwrite the row.

    ``mm_per_px`` is a float ON PURPOSE, the one documented exception to the
    integer-mm rule: it is a display scale factor for a raster (set by two-point
    calibration, e.g. 25.37 mm/px), never a length that reaches an op payload or
    compliance arithmetic. The origin, being a model-space position, stays integer
    millimetres like everything else.
    """

    __tablename__ = "project_underlays"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("project_underlays"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "projects.id", ondelete="CASCADE", name="fk_project_underlays_project_id_projects"
        ),
        nullable=False,
    )
    #: storage key of the PNG/JPEG; presigned GETs are minted per response (§13).
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: pixel dimensions, parsed server-side from the actual bytes (never trusted).
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)
    #: model millimetres per image pixel — the calibration result (see class note).
    mm_per_px: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"))
    #: model-space position of image pixel (0,0), integer mm.
    origin_x_mm: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    origin_y_mm: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    opacity: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.5"))
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("project_id", name="uq_project_underlays_project_id"),
        CheckConstraint("width_px > 0", name="ck_project_underlays_width_px_positive"),
        CheckConstraint("height_px > 0", name="ck_project_underlays_height_px_positive"),
        CheckConstraint("mm_per_px > 0", name="ck_project_underlays_mm_per_px_positive"),
        CheckConstraint("opacity >= 0 AND opacity <= 1", name="ck_project_underlays_opacity_range"),
        CheckConstraint(
            "length(btrim(object_key)) > 0", name="ck_project_underlays_object_key_not_blank"
        ),
        Index("ix_project_underlays_firm_id", "firm_id"),
    )


#: Which part of the design a reference image speaks to. Mirrors
#: ``services.render.references.SCOPES`` — the render side owns the meaning, this owns
#: the storage, and `test_reference_vocabulary.py` asserts the two never drift.
REFERENCE_SCOPES: tuple[str, ...] = (
    "whole-house",
    "facade",
    "interior",
    "kitchen",
    "living",
    "bedroom",
    "bathroom",
    "landscape",
    "material",
)

#: How strongly to apply one. ``avoid`` is the opposite of ``guide``, not a weaker
#: version of it: "not like this" is what clients say most and nothing else records it.
REFERENCE_INTENTS: tuple[str, ...] = ("match", "guide", "avoid")


class ProjectReference(UuidPk, Timestamps, TenantOwned, Base):
    """One picture on a project's inspiration board, with what to do about it.

    Many rows per project, unlike :class:`ProjectUnderlay` which is one: a board with a
    single picture is not a board. Like the underlay it is a SIDECAR, never part of the
    op-fold model — a reference steers a render's prompt and touches no geometry, so
    putting it in the model core would demand byte-identical twin changes for nothing.

    The four annotation columns are the feature. A picture alone is ambiguous — "use
    this kitchen" could mean the cabinets, the island or the light — so the architect
    says where it applies, what to take, what to leave, and how hard to push. Nothing
    infers them: a guess here is wrong often enough to be untrustworthy, and its
    mistakes are invisible until a client is looking at the render.
    """

    __tablename__ = "project_references"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("project_references"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "projects.id", ondelete="CASCADE", name="fk_project_references_project_id_projects"
        ),
        nullable=False,
    )
    #: storage key of the image; presigned GETs are minted per response (§13).
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: the name it arrived with, kept so the architect recognises their own upload.
    filename: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: parsed server-side from the actual bytes, never taken from the client.
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)

    #: What the architect called it. Used verbatim in the conflict questions, so it has
    #: to be something they recognise rather than a hash.
    label: Mapped[str] = mapped_column(Text, nullable=False)
    #: WHERE — which part of the design it speaks to.
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    #: WHY — what to take from it, in their words.
    why: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    #: What to LEAVE. ``ignore`` is a Python builtin-ish name in SQLAlchemy contexts and
    #: a reserved word in some dialects, so the column carries the suffix.
    ignore_note: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    #: HOW — match | guide | avoid.
    intent: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'guide'"))
    #: The architect's own ordering, which is the only ranking the render side applies.
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        CheckConstraint(_in_check("scope", REFERENCE_SCOPES), name="ck_project_references_scope"),
        CheckConstraint(
            _in_check("intent", REFERENCE_INTENTS), name="ck_project_references_intent"
        ),
        CheckConstraint("width_px > 0", name="ck_project_references_width_px_positive"),
        CheckConstraint("height_px > 0", name="ck_project_references_height_px_positive"),
        CheckConstraint("length(btrim(label)) > 0", name="ck_project_references_label_not_blank"),
        CheckConstraint(
            "length(btrim(object_key)) > 0", name="ck_project_references_object_key_not_blank"
        ),
        Index("ix_project_references_firm_id", "firm_id"),
        Index("ix_project_references_project_id_position", "project_id", "position"),
    )


# ---------------------------------------------------------------------------
# Metering & audit
# ---------------------------------------------------------------------------


class CreditEvent(UuidPk, Timestamps, TenantOwned, Base):
    """Usage metering from day one (render / solver / llm / export)."""

    __tablename__ = "credit_events"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), _firm_fk("credit_events"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)

    __table_args__ = (
        CheckConstraint(_in_check("kind", CREDIT_EVENT_KINDS), name="ck_credit_events_kind"),
        CheckConstraint("qty > 0", name="ck_credit_events_qty_positive"),
        Index("ix_credit_events_firm_id", "firm_id"),
        Index("ix_credit_events_firm_id_created_at", "firm_id", "created_at"),
        Index("ix_credit_events_firm_id_kind", "firm_id", "kind"),
    )


class AuditLog(UuidPk, Timestamps, TenantOwned, Base):
    """Append-only audit trail (§13): auth events, exports, share creation,
    reg-profile overrides, deletions, and every use of the tenancy escape hatch.

    Deliberately has **no foreign keys**: the audit trail must survive deletion of
    the firm or user it describes. ``firm_id`` is still required and still indexed,
    and the all-zero UUID (:data:`garh_api.tenancy.SYSTEM_FIRM_ID`) marks
    system-level entries that belong to no tenant.
    """

    __tablename__ = "audit_log"

    firm_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str] = mapped_column(Text, nullable=False)
    #: free-form so it can hold a uuid, an ops ``seq``, or a sheet number.
    entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)

    __table_args__ = (
        CheckConstraint("length(btrim(action)) > 0", name="ck_audit_log_action_not_blank"),
        CheckConstraint("length(btrim(entity)) > 0", name="ck_audit_log_entity_not_blank"),
        Index("ix_audit_log_firm_id", "firm_id"),
        Index("ix_audit_log_firm_id_created_at", "firm_id", "created_at"),
        Index("ix_audit_log_entity_entity_id", "entity", "entity_id"),
        Index("ix_audit_log_action", "action"),
    )


# ---------------------------------------------------------------------------
# Non-tenant tables (see NON_TENANT_TABLES)
# ---------------------------------------------------------------------------


class Flag(UuidPk, Timestamps, Base):
    """Feature flags, read at boot (playbook §18). Global by design — per-firm
    overrides live in ``firms.settings["flags"]`` so a flag flip is one row.

    NOT tenant-owned: no ``firm_id``, served by
    :class:`garh_api.repositories.flags.FlagRepository` (non-tenant, read-mostly).
    """

    __tablename__ = "flags"

    key: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("key", name="uq_flags_key"),
        CheckConstraint("key = lower(key)", name="ck_flags_key_lowercase"),
        CheckConstraint("length(btrim(key)) > 0", name="ck_flags_key_not_blank"),
    )


class OtpCode(UuidPk, Timestamps, Base):
    """Email OTP challenge (§13: 10 min expiry, 5 attempts).

    NOT tenant-owned: this table is read **before** authentication, when no
    ``TenantCtx`` exists yet. Only the plaintext code's hash is stored.
    """

    __tablename__ = "otp_codes"

    email: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: request ip / user agent for auth rate limiting; never the code itself.
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)

    __table_args__ = (
        CheckConstraint("attempts >= 0 AND attempts <= 5", name="ck_otp_codes_attempts_range"),
        CheckConstraint("email = lower(email)", name="ck_otp_codes_email_lowercase"),
        Index("ix_otp_codes_email_created_at", "email", "created_at"),
        Index("ix_otp_codes_expires_at", "expires_at"),
    )


#: Every mapped class, in dependency order — used by the migration and by tests.
ALL_TABLES: tuple[str, ...] = (
    "firms",
    "users",
    "user_two_factor",
    "projects",
    "plots",
    "briefs",
    "design_versions",
    "ops",
    "solver_jobs",
    "render_jobs",
    "sheets",
    "annotations",
    "compliance_reports",
    "share_links",
    "comments",
    "project_underlays",
    "project_references",
    "credit_events",
    "audit_log",
    "flags",
    "otp_codes",
)

#: Tables that carry ``firm_id`` and must only be reached via a scoped Repository.
TENANT_OWNED_TABLES: tuple[str, ...] = tuple(
    name for name in ALL_TABLES if name not in NON_TENANT_TABLES
)

__all__ = [
    "ALL_TABLES",
    "ANNOTATION_ANCHOR_KINDS",
    "Annotation",
    "AuditLog",
    "Base",
    "Brief",
    "CREDIT_EVENT_KINDS",
    "Comment",
    "ComplianceReport",
    "CreditEvent",
    "DESIGN_VERSION_KINDS",
    "DesignVersion",
    "Firm",
    "Flag",
    "JOB_STATUSES",
    "JOB_TERMINAL_STATUSES",
    "NON_TENANT_TABLES",
    "OP_SOURCES",
    "Op",
    "OtpCode",
    "PLOT_SOURCES",
    "PROJECT_STATUSES",
    "PROJECT_UNITS",
    "Plot",
    "Project",
    "ProjectUnderlay",
    "RENDER_MODES",
    "RenderJob",
    "SHEET_KINDS",
    "Sheet",
    "ShareLink",
    "SolverJob",
    "TENANT_OWNED_TABLES",
    "TenantOwned",
    "Timestamps",
    "USER_ROLES",
    "User",
    "UserTwoFactor",
    "UuidPk",
    "VASTU_MODES",
]
