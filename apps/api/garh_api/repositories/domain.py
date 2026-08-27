"""Domain objects returned by the repository layer.

Repositories return these frozen dataclasses, never ORM rows. Three reasons:

1. **No lazy loads escape the session.** A detached ORM row that touches an
   unloaded attribute raises at render time, inside the response serialiser, where
   the error is worst. A dataclass cannot.
2. **The tenancy boundary stays closed.** A domain object has no ``session``, so a
   route handler holding one cannot issue another query with it.
3. **The Pydantic schema layer gets a stable, boring shape to map from** — and it can
   drop ``firm_id`` on the way out (clients never need it; leaking it invites
   guessing).

Field names stay snake_case and mirror the DDL. ``firm_id`` is carried so worker code
can build a :class:`~garh_api.tenancy.TenantCtx` from an object it already has.
Geometry inside JSONB payloads is integer millimetres — these classes pass it through
untouched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _json_obj(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_arr(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Tenancy root
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Firm:
    id: uuid.UUID
    name: str
    logo_url: str | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Firm:
        return cls(
            id=row.id,
            name=row.name,
            logo_url=row.logo_url,
            settings=_json_obj(row.settings),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def flag_override(self, key: str) -> bool | None:
        """Per-firm feature-flag override (playbook §18 keeps global flags in ``flags``)."""
        overrides = self.settings.get("flags")
        if isinstance(overrides, dict) and key in overrides:
            return bool(overrides[key])
        return None


@dataclass(frozen=True)
class User:
    id: uuid.UUID
    firm_id: uuid.UUID
    email: str
    name: str
    role: str
    coa_number: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> User:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            email=row.email,
            name=row.name,
            role=row.role,
            coa_number=row.coa_number,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class AuthPrincipal:
    """The minimum needed to mint a JWT / build a :class:`TenantCtx`. Pre-auth safe."""

    user_id: uuid.UUID
    firm_id: uuid.UUID
    role: str
    email: str
    name: str
    firm_name: str


# ---------------------------------------------------------------------------
# Project + inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Project:
    id: uuid.UUID
    firm_id: uuid.UUID
    name: str
    status: str
    architect_of_record: uuid.UUID | None
    units: str
    city_pack: str | None
    demo: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Project:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            name=row.name,
            status=row.status,
            architect_of_record=row.architect_of_record,
            units=row.units,
            city_pack=row.city_pack,
            demo=row.demo,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class Plot:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    boundary: list[Any]
    north_deg: int
    roads: list[Any]
    reg_profile: dict[str, Any]
    source: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Plot:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            boundary=_json_arr(row.boundary),
            north_deg=row.north_deg,
            roads=_json_arr(row.roads),
            reg_profile=_json_obj(row.reg_profile),
            source=row.source,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class Brief:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    data: dict[str, Any]
    vastu_mode: str
    completeness: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Brief:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            data=_json_obj(row.data),
            vastu_mode=row.vastu_mode,
            completeness=row.completeness,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ---------------------------------------------------------------------------
# Op log + versions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    """A persisted op. ``idx`` is its position on the branch; ``seq`` is global."""

    seq: int
    firm_id: uuid.UUID
    project_id: uuid.UUID
    version_branch: uuid.UUID
    idx: int
    type: str
    payload: dict[str, Any]
    inverse: dict[str, Any] | None
    actor: uuid.UUID | None
    source: str
    client_op_id: str | None
    group_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Op:
        return cls(
            seq=row.seq,
            firm_id=row.firm_id,
            project_id=row.project_id,
            version_branch=row.version_branch,
            idx=row.idx,
            type=row.type,
            payload=_json_obj(row.payload),
            inverse=None if row.inverse is None else _json_obj(row.inverse),
            actor=row.actor,
            source=row.source,
            client_op_id=row.client_op_id,
            group_id=row.group_id,
            created_at=row.created_at,
        )


@dataclass(frozen=True)
class NewOp:
    """An op on its way in — no ``idx``/``seq`` yet, the server assigns those (§4).

    ``inverse`` is computed by the op engine (it needs the pre-fold state) and passed
    in; the repository stores it verbatim.
    """

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    inverse: dict[str, Any] | None = None
    client_op_id: str | None = None
    group_id: uuid.UUID | None = None


@dataclass(frozen=True)
class OpAppendResult:
    """Outcome of :meth:`OpRepository.append`.

    ``already_applied=True`` means every incoming ``client_op_id`` was already on the
    branch: a retried request, answered idempotently instead of with a 409 (§11).
    """

    ops: list[Op]
    first_idx: int
    last_idx: int
    head_idx: int
    already_applied: bool = False


@dataclass(frozen=True)
class DesignVersion:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    name: str | None
    parent_id: uuid.UUID | None
    version_branch: uuid.UUID
    op_seq_start: int | None
    op_seq_end: int | None
    snapshot: dict[str, Any] | None
    snapshot_hash: str | None
    kind: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> DesignVersion:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            name=row.name,
            parent_id=row.parent_id,
            version_branch=row.version_branch,
            op_seq_start=row.op_seq_start,
            op_seq_end=row.op_seq_end,
            snapshot=None if row.snapshot is None else _json_obj(row.snapshot),
            snapshot_hash=row.snapshot_hash,
            kind=row.kind,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @property
    def has_snapshot(self) -> bool:
        return self.snapshot is not None


@dataclass(frozen=True)
class DesignVersionSummary:
    """Version-timeline entry: everything except the (large) snapshot payload."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str | None
    parent_id: uuid.UUID | None
    version_branch: uuid.UUID
    op_seq_start: int | None
    op_seq_end: int | None
    snapshot_hash: str | None
    kind: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> DesignVersionSummary:
        return cls(
            id=row.id,
            project_id=row.project_id,
            name=row.name,
            parent_id=row.parent_id,
            version_branch=row.version_branch,
            op_seq_start=row.op_seq_start,
            op_seq_end=row.op_seq_end,
            snapshot_hash=row.snapshot_hash,
            kind=row.kind,
            created_at=row.created_at,
        )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolverJob:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    params: dict[str, Any]
    status: str
    progress: int
    options: list[Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> SolverJob:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            params=_json_obj(row.params),
            status=row.status,
            progress=row.progress,
            options=None if row.options is None else _json_arr(row.options),
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")


@dataclass(frozen=True)
class RenderJob:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    design_version_id: uuid.UUID | None
    view: dict[str, Any]
    mode: str
    provider: str
    status: str
    progress: int
    output_url: str | None
    params: dict[str, Any]
    stale: bool
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> RenderJob:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            design_version_id=row.design_version_id,
            view=_json_obj(row.view),
            mode=row.mode,
            provider=row.provider,
            status=row.status,
            progress=row.progress,
            output_url=row.output_url,
            params=_json_obj(row.params),
            stale=row.stale,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")


# ---------------------------------------------------------------------------
# Drawings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sheet:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    design_version_id: uuid.UUID | None
    kind: str
    number: str | None
    layout: dict[str, Any]
    generated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Sheet:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            design_version_id=row.design_version_id,
            kind=row.kind,
            number=row.number,
            layout=_json_obj(row.layout),
            generated_at=row.generated_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class Annotation:
    id: uuid.UUID
    firm_id: uuid.UUID
    sheet_id: uuid.UUID
    anchor_element_id: str | None
    anchor_kind: str
    payload: dict[str, Any]
    orphaned: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Annotation:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            sheet_id=row.sheet_id,
            anchor_element_id=row.anchor_element_id,
            anchor_kind=row.anchor_kind,
            payload=_json_obj(row.payload),
            orphaned=row.orphaned,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class ComplianceReport:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    design_version_id: uuid.UUID | None
    pack_versions: dict[str, Any]
    results: list[Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> ComplianceReport:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            design_version_id=row.design_version_id,
            pack_versions=_json_obj(row.pack_versions),
            results=_json_arr(row.results),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def failures(self) -> list[Any]:
        return [r for r in self.results if isinstance(r, dict) and r.get("status") == "fail"]


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShareLink:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    scope: dict[str, Any]
    expires_at: datetime | None
    revoked: bool
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    #: NOTE: ``token_hash`` is deliberately absent. The plaintext token is returned
    #: exactly once, by the create call, and never read back from storage.

    @classmethod
    def from_row(cls, row: Any) -> ShareLink:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            scope=_json_obj(row.scope),
            expires_at=row.expires_at,
            revoked=row.revoked,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class ResolvedShare:
    """A validated share token: enough to build a ``share_viewer`` TenantCtx."""

    share_link_id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    scope: dict[str, Any]
    expires_at: datetime | None

    @property
    def sections(self) -> list[str]:
        raw = self.scope.get("sections")
        return [str(s) for s in raw] if isinstance(raw, list) else []

    @property
    def can_comment(self) -> bool:
        return bool(self.scope.get("canComment"))


@dataclass(frozen=True)
class Comment:
    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    share_link_id: uuid.UUID | None
    anchor: dict[str, Any]
    body: str
    author_name: str
    resolved: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Comment:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            share_link_id=row.share_link_id,
            anchor=_json_obj(row.anchor),
            body=row.body,
            author_name=row.author_name,
            resolved=row.resolved,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class Underlay:
    """The tracing underlay for one project (see ``models.ProjectUnderlay``).

    ``mm_per_px`` is the one sanctioned float — a raster display scale, never a
    length that reaches an op payload. The origin is integer millimetres.
    """

    id: uuid.UUID
    firm_id: uuid.UUID
    project_id: uuid.UUID
    object_key: str
    width_px: int
    height_px: int
    mm_per_px: float
    origin_x_mm: int
    origin_y_mm: int
    opacity: float
    locked: bool
    visible: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Underlay:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            project_id=row.project_id,
            object_key=row.object_key,
            width_px=row.width_px,
            height_px=row.height_px,
            mm_per_px=row.mm_per_px,
            origin_x_mm=row.origin_x_mm,
            origin_y_mm=row.origin_y_mm,
            opacity=row.opacity,
            locked=row.locked,
            visible=row.visible,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ---------------------------------------------------------------------------
# Metering, audit, config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreditEvent:
    id: uuid.UUID
    firm_id: uuid.UUID
    kind: str
    qty: int
    meta: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> CreditEvent:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            kind=row.kind,
            qty=row.qty,
            meta=_json_obj(row.meta),
            created_at=row.created_at,
        )


@dataclass(frozen=True)
class AuditEntry:
    id: uuid.UUID
    firm_id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    entity: str
    entity_id: str | None
    meta: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> AuditEntry:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            user_id=row.user_id,
            action=row.action,
            entity=row.entity,
            entity_id=row.entity_id,
            meta=_json_obj(row.meta),
            created_at=row.created_at,
        )


@dataclass(frozen=True)
class Flag:
    id: uuid.UUID
    key: str
    enabled: bool
    description: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Flag:
        return cls(
            id=row.id,
            key=row.key,
            enabled=row.enabled,
            description=row.description,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class OtpChallenge:
    """An OTP row. The code itself is never returned — only its hash is stored."""

    id: uuid.UUID
    email: str
    expires_at: datetime
    attempts: int
    consumed_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> OtpChallenge:
        return cls(
            id=row.id,
            email=row.email,
            expires_at=row.expires_at,
            attempts=row.attempts,
            consumed_at=row.consumed_at,
            created_at=row.created_at,
        )

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None


__all__ = [
    "Annotation",
    "AuditEntry",
    "AuthPrincipal",
    "Brief",
    "Comment",
    "ComplianceReport",
    "CreditEvent",
    "DesignVersion",
    "DesignVersionSummary",
    "Firm",
    "Flag",
    "NewOp",
    "Op",
    "OpAppendResult",
    "OtpChallenge",
    "Plot",
    "Project",
    "RenderJob",
    "ResolvedShare",
    "Sheet",
    "ShareLink",
    "SolverJob",
    "Underlay",
    "User",
]
