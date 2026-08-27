"""Schemas for the op sequencer (§4, §11) — the most contract-sensitive file here.

An op on the wire is exactly what the TypeScript model core emits::

    { "type": "wall.add", "payload": { ... }, "clientOpId": "...", "groupId": "..." }

``type`` is one of the 32 strings in the op taxonomy; the server does not enumerate
them in this schema on purpose. The taxonomy lives in
``packages/model/schema/ops.schema.json`` and is enforced by the model core during the
dry-run fold — duplicating the list here would give us two places to forget to update,
and the second one would reject valid ops with a confusing 422.

``payload`` is free-form JSON at this layer and fully validated one layer down. What
this layer does guarantee: it is an object, it is not enormous, and the request as a
whole is well-formed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator

from garh_api.schemas import CamelModel, ResponseModel

#: Guard rails on a single request. ``MAX_OPS_PER_APPEND`` is also enforced (lower) by
#: ``Settings.max_ops_per_append``; this is the hard ceiling that keeps a hostile body
#: from being parsed at all.
MAX_OPS_PER_APPEND = 1000

#: ``clientOpId`` is client-generated (a ULID in practice). Bounded because it is
#: stored, indexed, and echoed back.
MAX_CLIENT_OP_ID_LENGTH = 64


class OpIn(CamelModel):
    """One op on its way in. The server assigns ``idx``, ``seq`` and ``inverse``.

    ``clientOpId`` is the idempotency unit (§11). Send one for every op: a replayed
    request whose ops all carry ids that already landed returns the original result
    instead of applying them twice. Ops without a ``clientOpId`` cannot be deduplicated,
    so a retry after a timeout will double-apply — the optimistic client must always
    set it.
    """

    type: StrictStr = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    client_op_id: StrictStr | None = Field(
        default=None, min_length=1, max_length=MAX_CLIENT_OP_ID_LENGTH
    )
    group_id: uuid.UUID | None = Field(
        default=None,
        description="Batch id. Undo/redo operates on groups (§4), so a multi-op edit "
        "shares one.",
    )


class OpsAppendIn(CamelModel):
    """``POST /projects/:id/ops`` — ``{ops[], baseIdx}``.

    ``baseIdx`` is the index the client believes is HEAD; ``-1`` means "the branch is
    empty". A mismatch is a 409 carrying the real ``headIdx``, and the client rebases
    (§11). The server never rebases on the client's behalf: the ops may no longer be
    valid against the newer state, and silently applying them anyway is how two tabs
    produce a plan neither user drew.
    """

    ops: list[OpIn] = Field(min_length=1, max_length=MAX_OPS_PER_APPEND)
    base_idx: StrictInt = Field(
        ge=-1, description="-1 for an empty branch; otherwise the last idx you have."
    )
    source: StrictStr = Field(
        default="manual", description="manual | copilot | solver | system (provenance, §4)."
    )
    version_branch: uuid.UUID | None = Field(
        default=None, description="Defaults to the project's active branch."
    )
    group_id: uuid.UUID | None = Field(
        default=None, description="Group every op in this request together for undo."
    )

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        from garh_api import models

        if value not in models.OP_SOURCES:
            raise ValueError("source must be one of %s." % ", ".join(models.OP_SOURCES))
        return value


class OpOut(ResponseModel):
    """A persisted op. ``inverse`` is the undo payload the server computed."""

    seq: StrictInt
    idx: StrictInt
    type: StrictStr
    payload: dict[str, Any] = Field(default_factory=dict)
    inverse: dict[str, Any] | None = None
    source: StrictStr
    actor: uuid.UUID | None = None
    client_op_id: StrictStr | None = None
    group_id: uuid.UUID | None = None
    created_at: datetime

    @classmethod
    def of(cls, op: Any) -> OpOut:
        return cls(
            seq=op.seq,
            idx=op.idx,
            type=op.type,
            payload=dict(op.payload),
            inverse=op.inverse,
            source=op.source,
            actor=op.actor,
            client_op_id=op.client_op_id,
            group_id=op.group_id,
            created_at=op.created_at,
        )


class OpsAppendOut(ResponseModel):
    """Result of a successful append.

    ``stateHash`` lets the client assert its optimistic fold matched the server's
    authoritative one; a mismatch means the client should reload rather than keep
    diverging. ``snapshotVersionId`` is set on the request that crossed the
    ``OP_SNAPSHOT_INTERVAL`` boundary (§2), which is also the client's cue that a fast
    reload is now cheap.
    """

    applied: list[OpOut] = Field(default_factory=list)
    first_idx: StrictInt
    last_idx: StrictInt
    head_idx: StrictInt
    version_branch: uuid.UUID
    already_applied: StrictBool = Field(
        default=False, description="True when this was an idempotent replay."
    )
    state_hash: StrictStr | None = None
    snapshot_version_id: uuid.UUID | None = None
    renders_marked_stale: StrictInt = 0


class OpsSinceOut(ResponseModel):
    """``GET /projects/:id/ops?since=idx`` — incremental sync, ascending by idx."""

    ops: list[OpOut] = Field(default_factory=list)
    since_idx: StrictInt
    head_idx: StrictInt
    version_branch: uuid.UUID
    has_more: StrictBool = Field(
        default=False, description="True when the page hit its limit; call again."
    )


class ModelStateOut(ResponseModel):
    """``GET /projects/:id/model?version=`` — snapshot + tail (§2, §15 "<2s").

    The client folds ``ops`` onto ``snapshot`` with the same code the server uses, then
    checks ``stateHash``. ``snapshot`` is null for a project whose log is still short
    enough to replay from empty — in that case ``baseIdx`` is -1 and ``ops`` is the
    whole history.
    """

    project_id: uuid.UUID
    version_branch: uuid.UUID
    design_version_id: uuid.UUID | None = Field(
        default=None, description="The version whose snapshot anchors this payload."
    )
    schema_version: StrictInt
    snapshot: dict[str, Any] | None = None
    snapshot_hash: StrictStr | None = None
    base_idx: StrictInt = Field(
        description="Index the snapshot is folded up to; -1 when starting from empty."
    )
    head_idx: StrictInt
    ops: list[OpOut] = Field(default_factory=list)
    state_hash: StrictStr | None = Field(
        default=None,
        description="Hash of the folded document. Null when the server could not fold "
        "(model engine unavailable) — the client folds and verifies nothing.",
    )
    truncated: StrictBool = Field(
        default=False,
        description="True when the tail was capped; fetch the rest with GET /ops?since=.",
    )


class ValidationIssueOut(ResponseModel):
    """One machine-readable rejection reason from the model core (§3 invariants).

    The copilot feeds these straight back to the LLM for its one self-correction pass
    (§10), so the wording is part of the contract with the model core, not prose we
    invent here.
    """

    code: StrictStr
    message: StrictStr
    op_index: StrictInt | None = None
    op_type: StrictStr | None = None
    element_id: StrictStr | None = None
    field: StrictStr | None = None
    limit: Any | None = None
    actual: Any | None = None


class OpRejectionOut(ResponseModel):
    """422 body when an op is invalid. Superset of problem+json."""

    code: StrictStr = "op_rejected"
    message: StrictStr
    action: StrictStr = "Fix the highlighted values and try again."
    request_id: StrictStr | None = None
    head_idx: StrictInt | None = None
    issues: list[ValidationIssueOut] = Field(default_factory=list)


class UndoIn(CamelModel):
    """``POST /projects/:id/ops`` handles undo too — the client appends the stored
    inverses as ordinary ops. This model exists only so the OpenAPI docs can describe
    that convention; there is no separate undo endpoint, by design (§4: "undo appends
    the inverse op; it never deletes history")."""

    group_id: uuid.UUID
    base_idx: StrictInt = Field(ge=-1)


__all__ = [
    "MAX_CLIENT_OP_ID_LENGTH",
    "MAX_OPS_PER_APPEND",
    "ModelStateOut",
    "OpIn",
    "OpOut",
    "OpRejectionOut",
    "OpsAppendIn",
    "OpsAppendOut",
    "OpsSinceOut",
    "UndoIn",
    "ValidationIssueOut",
]
