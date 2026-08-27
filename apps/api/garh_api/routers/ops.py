"""THE op sequencer (playbook §4, §11) — and the model-engine adapter it runs on.

Everything the product can change about a design is an op. This module is the server
side of that: it assigns indexes, folds, computes inverses, persists, snapshots, and
tells a client when it has fallen behind. Three routes::

    POST /projects/:id/ops          {ops[], baseIdx} → applied ops + new head (409 on stale base)
    GET  /projects/:id/ops?since=   incremental sync, ascending by idx
    GET  /projects/:id/model        snapshot + tail (the <2s open-project payload)

The append path, in order, and why each step is where it is
-----------------------------------------------------------

1. **Advisory lock first** (locked decision D12, single writer per project). Taken
   before anything is read, because the whole handler is a read-modify-write: read
   head, fold against it, append at head+1. Without the lock two writers can both read
   head=7 and both try to write idx 8 — the unique index would save us, but one of them
   burns a fold and the user sees a 409 they did not need.
2. **Idempotent replay check before folding.** A retry whose ops already landed must
   return the original result. It cannot be detected *after* folding, because folding
   an already-applied ``wall.add`` fails validation (duplicate id) and the client would
   get a confusing 422 instead of its own earlier success.
3. **Stale-base check** → 409 carrying ``headIdx``, so the client fetches
   ``?since=baseIdx``, rebases its optimistic queue and retries. The server never
   rebases: the ops may no longer be valid against the newer state, and applying them
   anyway is how two tabs produce a plan neither user drew.
4. **Fold each op individually**, keeping its own inverse (the ``ops.inverse`` column is
   per-op, and undo of a group replays the inverses in reverse). Any rejection aborts
   the whole request — a half-applied group is not a state any user asked for.
5. **Append**, then **snapshot** if the branch crossed ``OP_SNAPSHOT_INTERVAL``, then
   **mark renders stale** because the design moved (§9's banner).

Snapshot envelope
-----------------

``design_versions.snapshot`` stores an *envelope*, not the bare document::

    {"snapshotVersion": 1, "schemaVersion": 1, "versionBranch": "…",
     "atIdx": 199, "atSeq": 4211, "stateHash": "<model core hash>", "doc": {…}}

Why: reloading "snapshot + tail" needs to know *which op index* the snapshot is folded
to, and §2's ``design_versions`` only records ``op_seq_start/op_seq_end`` — global
sequence numbers, not branch indexes. Rather than change a schema owned elsewhere, the
anchor travels with the payload. It also disambiguates two hashes that would otherwise
be confused: ``design_versions.snapshot_hash`` is a storage checksum over the envelope
(computed by the repository), while ``envelope.stateHash`` is the model core's canonical
``stateHash(doc)`` — the one the golden tests and the client compare.
"""

from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api import MODEL_SCHEMA_VERSION
from garh_api.collab import OpsAdvanced, queue_ops_advanced
from garh_api.config import get_settings
from garh_api.logging import get_logger
from garh_api.repositories import (
    EMPTY_BRANCH_HEAD,
    DesignVersion,
    DesignVersionRepository,
    NewOp,
    Op,
    OpRepository,
    OpSequenceConflictError,
    RenderJobRepository,
    TenantCtx,
)
from garh_api.routers import (
    ApiError,
    SessionDep,
    TenantDep,
    active_branch,
    enforce_rate_limit,
    require_project,
)
from garh_api.schemas.ops import (
    ModelStateOut,
    OpIn,
    OpOut,
    OpsAppendIn,
    OpsAppendOut,
    OpsSinceOut,
)
from garh_api.solver_apply import expand_solver_apply_ops, snapshot_after_solver_apply

_log = get_logger(__name__)

router = APIRouter(tags=["ops"])

#: Hard cap on how many tail ops one ``GET /model`` returns. Snapshots land every 200
#: ops, so hitting this means something is wrong (or a snapshot was pruned) — the
#: response says ``truncated: true`` rather than silently returning a partial state.
MAX_TAIL_OPS = 2000

#: Cap on one ``GET /ops?since=`` page.
MAX_OPS_PAGE = 1000

#: Ops that cannot change what a render looks like. Everything else marks existing
#: renders stale (§9). Erring towards "stale" is deliberate: a wrongly-stale banner
#: costs a re-render, a wrongly-fresh image costs the architect's credibility.
_NON_VISUAL_OP_TYPES = frozenset({"annotation.set", "brief.update", "room.set_target"})

#: Envelope format version for ``design_versions.snapshot`` (see the module docstring).
SNAPSHOT_ENVELOPE_VERSION = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ModelEngineUnavailableError(ApiError):
    """``garh_model`` (the Python mirror of ``packages/model``) is not importable.

    Raised instead of guessing. The server cannot validate an op, compute its inverse or
    fold state without the model core, and accepting ops it cannot check would poison
    the log — the one artefact the whole product is derived from.

    Subclasses :class:`~garh_api.routers.ApiError` rather than
    :class:`garh_api.errors.ServiceUnavailableError` because it carries diagnostic
    ``extra`` (which import failed, which symbol is missing) and that class's
    constructor takes ``dependency=`` instead.
    """

    http_status = 503
    code = "model_engine_unavailable"
    action = "The design engine is not loaded on this server. Contact support."


class OpRejectedError(ApiError):
    """An op failed the model core's invariants (§3). 422 with machine-readable issues.

    The copilot feeds ``issues`` straight back to the LLM for its single self-correction
    pass (§10), so the codes are an API, not log noise.
    """

    http_status = 422
    code = "op_rejected"
    action = "Fix the highlighted values and try again."

    def __init__(
        self,
        message: str,
        *,
        issues: list[dict[str, Any]] | None = None,
        op_index: int | None = None,
        head_idx: int | None = None,
    ) -> None:
        extra: dict[str, Any] = {"issues": list(issues or [])}
        if op_index is not None:
            extra["opIndex"] = op_index
        if head_idx is not None:
            extra["headIdx"] = head_idx
        super().__init__(message, extra=extra)


class SnapshotPrunedError(ApiError):
    http_status = 409
    code = "snapshot_pruned"
    action = "Pick another version, or restore this one to rebuild it from the op log."


# ---------------------------------------------------------------------------
# Model-engine adapter
# ---------------------------------------------------------------------------

#: Package holding the Python mirror of ``packages/model`` (playbook §1), and the
#: submodules to search when the package itself does not re-export a symbol.
#:
#: Searching submodules is not defensive vagueness — ``garh_model`` currently has no
#: ``__init__.py``, so ``import garh_model`` yields an empty namespace package and every
#: symbol lives in ``garh_model.fold`` / ``garh_model.model``. Looking in both means the
#: API works whether or not the model-core agent adds a re-exporting ``__init__``, and
#: the contract stays "these callables exist somewhere in the mirror".
MODEL_ENGINE_MODULE = "garh_model"
MODEL_ENGINE_SUBMODULES = ("garh_model.fold", "garh_model.model")

#: Accepted names for each capability. The TypeScript core is camelCase and a Python
#: mirror is normally snake_case; accepting both means the mirror can be a thin
#: transliteration without a naming argument.
_FOLD_NAMES = ("fold", "fold_op")
_TRY_FOLD_NAMES = ("try_fold", "tryFold")
_STATE_HASH_NAMES = ("state_hash", "stateHash", "doc_hash", "docHash")
_REPLAY_NAMES = ("replay",)
_EMPTY_DOC_NAMES = (
    "empty_project_doc",
    "empty_doc",
    "EMPTY_DOC",
    "EMPTY_PROJECT_DOC",
    "initial_doc",
    "create_empty_doc",
)
_TO_JSONABLE_NAMES = ("to_jsonable",)
_DOC_TYPE_NAMES = ("ProjectDoc", "Model")


@dataclass
class FoldOutcome:
    """One op folded: the new document plus the ops that undo it. Both as plain JSON."""

    document: dict[str, Any]
    inverse_ops: list[dict[str, Any]] = field(default_factory=list)


class ModelEngine:
    """Adapter over ``garh_model``, and the JSON boundary around it.

    The API depends on a *contract*, not on an import: a handful of callables under any
    of the accepted names. A missing mirror produces one honest 503 naming exactly what
    is absent, rather than a hundred import errors.

    **Everything crossing this class is plain JSON.** Inside the mirror a document is a
    frozen ``ProjectDoc`` dataclass; outside it is the camelCase dict that goes into the
    ``design_versions.snapshot`` jsonb column, over the wire, and through the client's
    identical model core. Converting here — once, in the adapter — is what keeps every
    caller (the sequencer, the version writer, the share viewer) free of the question,
    and it is what makes ``stateHash`` agree between Python and TypeScript: the hash is
    defined over the JSON form, not over Python attribute names.
    """

    def __init__(self, modules: tuple[Any, ...]) -> None:
        self._modules = modules
        self._fold = _first_callable(modules, _FOLD_NAMES)
        self._try_fold = _first_callable(modules, _TRY_FOLD_NAMES)
        self._state_hash = _first_callable(modules, _STATE_HASH_NAMES)
        self._replay = _first_callable(modules, _REPLAY_NAMES)
        self._empty_doc = _first_attr(modules, _EMPTY_DOC_NAMES)
        self._to_jsonable = _first_callable(modules, _TO_JSONABLE_NAMES)
        self._doc_type = _first_attr(modules, _DOC_TYPE_NAMES)
        missing = [
            name
            for name, value in (
                ("fold/try_fold", self._fold or self._try_fold),
                ("state_hash", self._state_hash),
                ("empty_project_doc", self._empty_doc),
            )
            if value is None
        ]
        if missing:
            raise ModelEngineUnavailableError(
                "%s is importable but does not expose %s."
                % (MODEL_ENGINE_MODULE, ", ".join(missing)),
                extra={"missing": missing},
            )

    @property
    def schema_version(self) -> int:
        for module in self._modules:
            value = getattr(module, "SCHEMA_VERSION", None)
            if isinstance(value, int):
                return value
        return MODEL_SCHEMA_VERSION

    # -- JSON boundary --------------------------------------------------

    def to_json(self, value: Any) -> Any:
        """Mirror object → JSON. Already-JSON values pass through untouched."""
        if value is None or isinstance(value, str | bool | int | float):
            return value
        to_json = getattr(value, "to_json", None)
        if callable(to_json):
            return to_json()
        if self._to_jsonable is not None:
            return self._to_jsonable(value)
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list | tuple):
            return [self.to_json(item) for item in value]
        raise ModelEngineUnavailableError(
            "The design engine returned a %s this server cannot serialise." % type(value).__name__,
            extra={"type": type(value).__name__},
        )

    def from_json(self, document: dict[str, Any]) -> Any:
        """JSON → the mirror's document type.

        A mirror that folds plain dicts gets its dict back unchanged, so this adapter
        supports both shapes without the callers knowing which one they have.
        """
        loader = getattr(self._doc_type, "from_json", None) if self._doc_type else None
        if callable(loader):
            return loader(document)
        return document

    def empty_document(self) -> dict[str, Any]:
        produced = self._empty_doc() if callable(self._empty_doc) else self._empty_doc
        return dict(self.to_json(produced))

    def state_hash(self, document: dict[str, Any]) -> str:
        """``stateHash`` of a JSON document.

        Passed as JSON deliberately: the mirror's ``state_hash`` canonicalises whatever
        it is given, and hashing the exact bytes the client will hash is the only way
        the two sides can be compared. Hashing the dataclass would work today and break
        the moment a field's JSON name and its Python name diverge.
        """
        return str(self._state_hash(document))

    def fold(self, document: dict[str, Any], op: dict[str, Any]) -> FoldOutcome:
        """Fold one op. Raises :class:`OpRejectedError` with issues when invalid."""
        doc = self.from_json(document)
        if self._try_fold is not None:
            result = self._try_fold(doc, op)
            if _result_get(result, "ok") is False:
                issues = self._normalise_issues(_result_get(result, "issues"))
                raise OpRejectedError(_first_issue_message(issues, op.get("type")), issues=issues)
            return self._to_outcome(result, document)
        try:
            result = self._fold(doc, op)
        except Exception as exc:
            issues = self._normalise_issues(getattr(exc, "issues", None))
            if not issues:
                issues = [
                    {
                        "code": getattr(exc, "code", "OP_REJECTED"),
                        "message": str(exc),
                        "opType": op.get("type"),
                    }
                ]
            raise OpRejectedError(
                _first_issue_message(issues, op.get("type")), issues=issues
            ) from exc
        return self._to_outcome(result, document)

    def replay(
        self, ops: list[dict[str, Any]], initial: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fold a list of ops from ``initial`` (or empty). Used by the model reader."""
        if self._replay is not None:
            base = self.from_json(initial) if initial is not None else None
            produced = self._replay(ops, base) if base is not None else self._replay(ops)
            return dict(self.to_json(produced))
        document = dict(initial) if initial is not None else self.empty_document()
        for op in ops:
            document = self.fold(document, op).document
        return document

    def _to_outcome(self, result: Any, previous: dict[str, Any]) -> FoldOutcome:
        document = _result_get(result, "model")
        if document is None:
            document = _result_get(result, "document")
        if document is None:
            # A mirror that returns the document directly rather than a wrapper.
            document = result if isinstance(result, dict) and "inverse" not in result else previous
        inverse = _result_get(result, "inverse")
        if inverse is None:
            inverse_ops: list[dict[str, Any]] = []
        elif isinstance(inverse, dict):
            inverse_ops = [inverse]
        else:
            inverse_ops = [dict(self.to_json(item)) for item in inverse]
        return FoldOutcome(document=dict(self.to_json(document)), inverse_ops=inverse_ops)

    def _normalise_issues(self, raw: Any) -> list[dict[str, Any]]:
        """Validation issues → the JSON the copilot's self-correction pass reads."""
        if not raw:
            return []
        issues: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                issues.append(dict(item))
                continue
            try:
                converted = self.to_json(item)
            except ModelEngineUnavailableError:
                converted = None
            if isinstance(converted, dict):
                issues.append(converted)
            else:
                issues.append(
                    {
                        "code": str(getattr(item, "code", "OP_REJECTED")),
                        "message": str(getattr(item, "message", item)),
                    }
                )
        return issues


def _first_callable(modules: tuple[Any, ...], names: tuple[str, ...]) -> Any | None:
    for name in names:
        for module in modules:
            value = getattr(module, name, None)
            if callable(value):
                return value
    return None


def _first_attr(modules: tuple[Any, ...], names: tuple[str, ...]) -> Any | None:
    for name in names:
        for module in modules:
            value = getattr(module, name, None)
            if value is not None:
                return value
    return None


def _result_get(result: Any, key: str) -> Any:
    """Read a field from either a dict or an object result (the mirror may use either)."""
    if isinstance(result, dict):
        return result.get(key)
    return getattr(result, key, None)


def _first_issue_message(issues: list[dict[str, Any]], op_type: Any) -> str:
    for issue in issues:
        message = issue.get("message")
        if message:
            return str(message)
    return "That change isn't valid for this design (%s)." % (op_type or "unknown op")


_engine: ModelEngine | None = None
_engine_error: str | None = None


def get_model_engine() -> ModelEngine:
    """Cached :class:`ModelEngine`, or a 503 that names exactly what is missing.

    The package must import; its submodules are best-effort, because a mirror that
    re-exports everything from ``__init__`` is equally valid.
    """
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    try:
        modules = [importlib.import_module(MODEL_ENGINE_MODULE)]
    except ImportError as exc:
        _engine_error = str(exc)
        raise ModelEngineUnavailableError(
            "The design engine (%s) is not installed on this server." % MODEL_ENGINE_MODULE,
            extra={"module": MODEL_ENGINE_MODULE, "importError": str(exc)},
        ) from exc
    for name in MODEL_ENGINE_SUBMODULES:
        try:
            modules.append(importlib.import_module(name))
        except ImportError:
            continue
    _engine = ModelEngine(tuple(modules))
    _log.info(
        "model_engine.loaded",
        module=MODEL_ENGINE_MODULE,
        submodules=[m.__name__ for m in modules[1:]],
        schema_version=_engine.schema_version,
    )
    return _engine


def model_engine_available() -> bool:
    """For ``/readyz`` and ``/api/v1/meta``. Never raises."""
    try:
        get_model_engine()
        return True
    except ModelEngineUnavailableError:
        return False


# ---------------------------------------------------------------------------
# Snapshot envelope
# ---------------------------------------------------------------------------


def wrap_snapshot(
    document: dict[str, Any],
    *,
    version_branch: uuid.UUID,
    at_idx: int,
    at_seq: int | None,
    state_hash: str | None,
    schema_version: int,
) -> dict[str, Any]:
    return {
        "snapshotVersion": SNAPSHOT_ENVELOPE_VERSION,
        "schemaVersion": schema_version,
        "versionBranch": str(version_branch),
        "atIdx": at_idx,
        "atSeq": at_seq,
        "stateHash": state_hash,
        "doc": document,
    }


@dataclass(frozen=True)
class UnwrappedSnapshot:
    document: dict[str, Any]
    at_idx: int
    state_hash: str | None
    schema_version: int


def unwrap_snapshot(snapshot: dict[str, Any]) -> UnwrappedSnapshot | None:
    """Read a snapshot envelope. ``None`` when the payload is not a usable anchor.

    A bare (un-enveloped) document is accepted for forward compatibility but returns
    ``None``, because without ``atIdx`` there is no way to know which tail to apply —
    and folding the wrong tail is worse than replaying the whole log.
    """
    if not isinstance(snapshot, dict):
        return None
    document = snapshot.get("doc")
    at_idx = snapshot.get("atIdx")
    if not isinstance(document, dict) or not isinstance(at_idx, int):
        return None
    raw_hash = snapshot.get("stateHash")
    raw_schema = snapshot.get("schemaVersion")
    return UnwrappedSnapshot(
        document=document,
        at_idx=at_idx,
        state_hash=str(raw_hash) if raw_hash else None,
        schema_version=int(raw_schema) if isinstance(raw_schema, int) else MODEL_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# State loading (shared with projects.py and share.py)
# ---------------------------------------------------------------------------


@dataclass
class LoadedState:
    """The folded document plus where it sits on the branch."""

    document: dict[str, Any]
    head_idx: int
    version_branch: uuid.UUID
    anchor_version: DesignVersion | None = None
    anchor_idx: int = EMPTY_BRANCH_HEAD
    state_hash: str | None = None


async def load_project_state(
    session: AsyncSession,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    version_branch: uuid.UUID,
    *,
    upto_idx: int | None = None,
) -> LoadedState:
    """Fold the current (or historical) state: latest snapshot + tail ops.

    Requires the model engine — this is the only way to get an authoritative document
    server-side, and the only caller that can live without one is ``GET /model``, which
    ships snapshot+tail for the client to fold itself.
    """
    engine = get_model_engine()
    op_repo = OpRepository(session, ctx)
    dv_repo = DesignVersionRepository(session, ctx)

    head_idx = await op_repo.head_idx(project_id, version_branch)
    target_idx = head_idx if upto_idx is None else min(upto_idx, head_idx)

    anchor_version = await dv_repo.latest_snapshot(project_id, version_branch)
    anchor: UnwrappedSnapshot | None = None
    if anchor_version is not None and anchor_version.snapshot is not None:
        candidate = unwrap_snapshot(anchor_version.snapshot)
        if candidate is not None and candidate.at_idx <= target_idx:
            anchor = candidate

    if anchor is not None:
        # The snapshot is an ANCHOR, not the source of truth — the op log is.
        # An inner document this build cannot load (corruption, a future schema)
        # must not brick every later append with a 500: drop the anchor, say so
        # loudly, and refold from the log, which has everything.
        try:
            engine.from_json(dict(anchor.document))
        except Exception as exc:
            _log.error(
                "ops.snapshot_unreadable",
                project_id=str(project_id),
                design_version_id=str(anchor_version.id) if anchor_version else None,
                at_idx=anchor.at_idx,
                error=str(exc),
            )
            anchor = None

    if anchor is None:
        document = engine.empty_document()
        anchor_idx = EMPTY_BRANCH_HEAD
        anchor_version = None
    else:
        document = dict(anchor.document)
        anchor_idx = anchor.at_idx

    if target_idx > anchor_idx:
        tail = await op_repo.list_range(project_id, version_branch, anchor_idx + 1, target_idx)
        for op in tail:
            document = engine.fold(document, _op_to_engine_dict(op)).document

    return LoadedState(
        document=document,
        head_idx=head_idx,
        version_branch=version_branch,
        anchor_version=anchor_version,
        anchor_idx=anchor_idx,
        state_hash=engine.state_hash(document),
    )


def _op_to_engine_dict(op: Op) -> dict[str, Any]:
    """Persisted op → the shape the model core's ``fold`` expects."""
    payload: dict[str, Any] = {"type": op.type, "payload": dict(op.payload)}
    if op.client_op_id:
        payload["clientOpId"] = op.client_op_id
    if op.group_id:
        payload["groupId"] = str(op.group_id)
    return payload


# ---------------------------------------------------------------------------
# POST /projects/:id/ops — the sequencer
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/ops",
    response_model=OpsAppendOut,
    status_code=status.HTTP_200_OK,
    summary="Append ops to a project's design (the op sequencer)",
    responses={
        409: {"description": "Stale baseIdx — fetch ops since it, rebase, retry."},
        422: {"description": "An op failed the model invariants; see `issues`."},
        503: {"description": "The model engine is not available on this server."},
    },
)
async def append_ops(
    project_id: uuid.UUID,
    body: OpsAppendIn,
    session: SessionDep,
    ctx: TenantDep,
) -> OpsAppendOut:
    """Validate, fold, and append a batch of ops. Atomic: all land or none do."""
    ctx.require_write("editing this design")
    settings = get_settings()
    await require_project(session, ctx, project_id)

    if len(body.ops) > settings.max_ops_per_append:
        raise ApiError(
            "That is %d ops in one request; the limit is %d."
            % (len(body.ops), settings.max_ops_per_append),
            code="too_many_ops",
            action="Split the change into smaller batches.",
        )
    await enforce_rate_limit(
        "ops",
        ctx.firm_id,
        limit=settings.rate_limit_ops_per_second,
        window_seconds=1,
        what="Editing",
    )

    branch = body.version_branch or await active_branch(session, ctx, project_id)
    op_repo = OpRepository(session, ctx)

    # 1. Single writer per project (D12). Held until this request's transaction ends.
    await op_repo.acquire_branch_write_lock(project_id, branch)

    # 1b. §4 op 31: expand `solver.apply_option {solverJobId, optionIndex}` into its
    # stored op group BEFORE the replay check, so its (possibly derived) clientOpId
    # participates in idempotent replay. Everything else passes through untouched,
    # and the expanded op folds through the same path as any other op below.
    incoming = await expand_solver_apply_ops(session, ctx, project_id, list(body.ops))

    # 2. Idempotent replay — must precede the fold (see the module docstring).
    replayed = await _replayed_result(op_repo, project_id, branch, incoming)
    if replayed is not None:
        return replayed

    # 3. Optimistic concurrency.
    head_idx = await op_repo.head_idx(project_id, branch)
    if head_idx != body.base_idx:
        raise OpSequenceConflictError(
            project_id=project_id,
            version_branch=branch,
            base_idx=body.base_idx,
            head_idx=head_idx,
        )

    result = await _append_core(
        session,
        ctx,
        project_id=project_id,
        branch=branch,
        base_idx=body.base_idx,
        incoming=incoming,
        source=body.source,
        group_id=body.group_id,
    )
    # §4 op 31, last step: an accepted option is pinned as a kind='option' design
    # version ("snapshot afterwards"). No-op for batches without op 31.
    return await snapshot_after_solver_apply(
        session, ctx, project_id=project_id, branch=branch, incoming=incoming, result=result
    )


async def dispatch_ops(
    session: AsyncSession,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    ops: list[OpIn],
    *,
    source: str = "manual",
    group_id: uuid.UUID | None = None,
    branch: uuid.UUID | None = None,
) -> OpsAppendOut:
    """Append ops on the server's own behalf, at whatever HEAD currently is.

    Used by the form-shaped endpoints (``PUT /plot``, ``PUT /brief``) and by anything
    else that must not bypass the op log — golden rule 1: "if a feature can't be
    expressed as ops, redesign the feature".

    Last-writer-wins by construction: there is no ``baseIdx`` to conflict with, because
    a form submit has no optimistic queue to rebase. The canvas uses ``POST /ops`` with
    an explicit base precisely because it does.
    """
    ctx.require_write("editing this design")
    target_branch = branch or await active_branch(session, ctx, project_id)
    op_repo = OpRepository(session, ctx)
    await op_repo.acquire_branch_write_lock(project_id, target_branch)
    head_idx = await op_repo.head_idx(project_id, target_branch)
    return await _append_core(
        session,
        ctx,
        project_id=project_id,
        branch=target_branch,
        base_idx=head_idx,
        incoming=ops,
        source=source,
        group_id=group_id,
    )


async def _append_core(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    project_id: uuid.UUID,
    branch: uuid.UUID,
    base_idx: int,
    incoming: list[OpIn],
    source: str,
    group_id: uuid.UUID | None,
) -> OpsAppendOut:
    """Fold → append → snapshot → stale-mark. Assumes the branch lock is already held."""
    engine = get_model_engine()
    op_repo = OpRepository(session, ctx)
    state = await load_project_state(session, ctx, project_id, branch)
    document = state.document
    new_ops: list[NewOp] = []

    for index, op_in in enumerate(incoming):
        engine_op: dict[str, Any] = {"type": op_in.type, "payload": op_in.payload}
        if op_in.client_op_id:
            engine_op["clientOpId"] = op_in.client_op_id
        try:
            outcome = engine.fold(document, engine_op)
        except OpRejectedError as exc:
            exc.extra["opIndex"] = index
            exc.extra["headIdx"] = base_idx
            _log.info(
                "ops.rejected",
                project_id=str(project_id),
                op_type=op_in.type,
                op_index=index,
                issue_count=len(exc.extra.get("issues") or []),
            )
            raise
        document = outcome.document
        new_ops.append(
            NewOp(
                type=op_in.type,
                payload=op_in.payload,
                # The op log stores ONE jsonb per op, but undoing a delete needs several
                # ops (the wall, then each opening it hosted). Wrapping keeps the column
                # shape while preserving order.
                inverse={"ops": outcome.inverse_ops} if outcome.inverse_ops else None,
                client_op_id=op_in.client_op_id,
                group_id=op_in.group_id or group_id,
            )
        )

    result = await op_repo.append(
        project_id,
        branch,
        base_idx,
        new_ops,
        source=source,
        group_id=group_id,
        lock=False,  # already held by the caller, for the whole read-modify-write
    )

    state_hash = engine.state_hash(document)

    snapshot_version_id = await _maybe_snapshot(
        session,
        ctx,
        project_id=project_id,
        branch=branch,
        document=document,
        state_hash=state_hash,
        head_idx=result.head_idx,
        anchor_idx=state.anchor_idx,
        schema_version=engine.schema_version,
    )

    stale_count = 0
    if any(op.type not in _NON_VISUAL_OP_TYPES for op in new_ops):
        stale_count = await RenderJobRepository(session, ctx).mark_stale_for_project(project_id)

    # Live collab (the §SSE contract): every append funnels through this function —
    # POST /ops (manual edits, copilot applies, solver op-31 applies) and
    # dispatch_ops (PUT /plot, PUT /brief) alike — so this is the ONE seam that
    # tells connected clients the log advanced. Registered, not published: the
    # commit belongs to the caller's session scope, and a notification sent before
    # it races the reader's refetch (garh_api.collab explains the after_commit
    # hook). ``system`` is skipped: today that source is only the seed, which runs
    # before anyone can be connected, and a firehose of seed frames during a
    # ``--reset-demo`` would be noise even if someone were.
    if source != "system":
        queue_ops_advanced(
            session,
            OpsAdvanced(
                project_id=str(project_id),
                head_idx=result.head_idx,
                version_branch=str(branch),
                actor_id=str(ctx.user_id) if ctx.user_id is not None else None,
                source=source,
                group_id=str(group_id) if group_id is not None else None,
            ),
        )

    _log.info(
        "ops.append_ok",
        project_id=str(project_id),
        count=len(result.ops),
        head_idx=result.head_idx,
        op_source=source,
        snapshotted=snapshot_version_id is not None,
    )
    return OpsAppendOut(
        applied=[OpOut.of(op) for op in result.ops],
        first_idx=result.first_idx,
        last_idx=result.last_idx,
        head_idx=result.head_idx,
        version_branch=branch,
        already_applied=result.already_applied,
        state_hash=state_hash,
        snapshot_version_id=snapshot_version_id,
        renders_marked_stale=stale_count,
    )


async def _replayed_result(
    op_repo: OpRepository,
    project_id: uuid.UUID,
    branch: uuid.UUID,
    incoming: list[OpIn],
) -> OpsAppendOut | None:
    """Detect a retry of a request that already landed.

    Only a *complete* match counts: every incoming op carries a ``clientOpId`` and every
    one of them is already on the branch. A partial overlap is a genuine conflict — the
    client's view of history is wrong, and appending the remainder would interleave two
    edit streams into a design nobody drew.
    """
    client_ids = [op.client_op_id for op in incoming if op.client_op_id]
    if not client_ids or len(client_ids) != len(incoming):
        return None

    found: list[Op] = []
    for client_op_id in client_ids:
        existing = await op_repo.get_by_client_op_id(project_id, client_op_id)
        if existing is None:
            return None
        found.append(existing)

    found.sort(key=lambda op: op.idx)
    head_idx = await op_repo.head_idx(project_id, branch)
    _log.info(
        "ops.replayed",
        project_id=str(project_id),
        count=len(found),
        head_idx=head_idx,
    )
    return OpsAppendOut(
        applied=[OpOut.of(op) for op in found],
        first_idx=found[0].idx,
        last_idx=found[-1].idx,
        head_idx=head_idx,
        version_branch=branch,
        already_applied=True,
        state_hash=None,
        snapshot_version_id=None,
        renders_marked_stale=0,
    )


async def _maybe_snapshot(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    project_id: uuid.UUID,
    branch: uuid.UUID,
    document: dict[str, Any],
    state_hash: str,
    head_idx: int,
    anchor_idx: int,
    schema_version: int,
) -> uuid.UUID | None:
    """Write an auto checkpoint when the branch has moved far enough past the last one."""
    settings = get_settings()
    if head_idx - anchor_idx < settings.op_snapshot_interval:
        return None
    op_repo = OpRepository(session, ctx)
    head_seq = await op_repo.head_seq(project_id, branch)
    version = await DesignVersionRepository(session, ctx).create_checkpoint(
        project_id,
        version_branch=branch,
        snapshot=wrap_snapshot(
            document,
            version_branch=branch,
            at_idx=head_idx,
            at_seq=head_seq,
            state_hash=state_hash,
            schema_version=schema_version,
        ),
        op_seq_start=None,
        op_seq_end=head_seq,
    )
    _log.info(
        "ops.snapshot_written",
        project_id=str(project_id),
        version_id=str(version.id),
        at_idx=head_idx,
    )
    return version.id


# ---------------------------------------------------------------------------
# GET /projects/:id/ops?since= — incremental sync
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/ops",
    response_model=OpsSinceOut,
    summary="Ops after an index (incremental sync / rebase after a 409)",
)
async def list_ops(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    since: int = Query(default=EMPTY_BRANCH_HEAD, ge=EMPTY_BRANCH_HEAD),
    limit: int = Query(default=MAX_OPS_PAGE, ge=1, le=MAX_OPS_PAGE),
    version_branch: uuid.UUID | None = Query(default=None),
) -> OpsSinceOut:
    """Everything with ``idx > since``, ascending. This is what a 409'd client calls."""
    await require_project(session, ctx, project_id)
    branch = version_branch or await active_branch(session, ctx, project_id)
    op_repo = OpRepository(session, ctx)
    ops = await op_repo.list_since(project_id, branch, since, limit=limit)
    head_idx = await op_repo.head_idx(project_id, branch)
    return OpsSinceOut(
        ops=[OpOut.of(op) for op in ops],
        since_idx=since,
        head_idx=head_idx,
        version_branch=branch,
        has_more=len(ops) == limit and (ops[-1].idx < head_idx if ops else False),
    )


# ---------------------------------------------------------------------------
# GET /projects/:id/model — snapshot + tail
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/model",
    response_model=ModelStateOut,
    summary="Folded design state as snapshot + tail ops",
)
async def get_model(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    version: uuid.UUID | None = Query(
        default=None, description="A design version id. Omit for the live head."
    ),
    version_branch: uuid.UUID | None = Query(default=None),
) -> ModelStateOut:
    """The "<2s to interactive" payload (§15).

    Deliberately does **not** fold on the server: the client has the same model core and
    folding 0–200 tail ops locally is faster than shipping a re-serialised document. The
    server therefore returns ``stateHash`` only when the payload *is* the snapshot (no
    tail), where the hash is already known — an honest null beats a hash we did not
    compute.
    """
    await require_project(session, ctx, project_id)
    ctx.require_scope("plan")
    branch = version_branch or await active_branch(session, ctx, project_id)
    op_repo = OpRepository(session, ctx)
    dv_repo = DesignVersionRepository(session, ctx)
    head_idx = await op_repo.head_idx(project_id, branch)

    if version is not None:
        pinned = await dv_repo.require(version)
        if pinned.snapshot is None:
            raise SnapshotPrunedError(
                "That version's saved state has been pruned, so it cannot be opened " "directly."
            )
        unwrapped = unwrap_snapshot(pinned.snapshot)
        if unwrapped is None:
            raise SnapshotPrunedError("That version was saved in a format this server cannot open.")
        return ModelStateOut(
            project_id=project_id,
            version_branch=pinned.version_branch,
            design_version_id=pinned.id,
            schema_version=unwrapped.schema_version,
            snapshot=unwrapped.document,
            snapshot_hash=pinned.snapshot_hash,
            base_idx=unwrapped.at_idx,
            head_idx=unwrapped.at_idx,
            ops=[],
            state_hash=unwrapped.state_hash,
            truncated=False,
        )

    anchor_version = await dv_repo.latest_snapshot(project_id, branch)
    anchor: UnwrappedSnapshot | None = None
    if anchor_version is not None and anchor_version.snapshot is not None:
        candidate = unwrap_snapshot(anchor_version.snapshot)
        if candidate is not None and candidate.at_idx <= head_idx:
            anchor = candidate

    base_idx = anchor.at_idx if anchor is not None else EMPTY_BRANCH_HEAD
    tail = await op_repo.list_since(project_id, branch, base_idx, limit=MAX_TAIL_OPS)
    truncated = len(tail) == MAX_TAIL_OPS and bool(tail) and tail[-1].idx < head_idx

    return ModelStateOut(
        project_id=project_id,
        version_branch=branch,
        design_version_id=anchor_version.id if anchor is not None and anchor_version else None,
        schema_version=anchor.schema_version if anchor is not None else MODEL_SCHEMA_VERSION,
        snapshot=anchor.document if anchor is not None else None,
        snapshot_hash=(
            anchor_version.snapshot_hash if anchor is not None and anchor_version else None
        ),
        base_idx=base_idx,
        head_idx=head_idx,
        ops=[OpOut.of(op) for op in tail],
        state_hash=(anchor.state_hash if anchor is not None and not tail else None),
        truncated=truncated,
    )


__all__ = [
    "MAX_OPS_PAGE",
    "MAX_TAIL_OPS",
    "MODEL_ENGINE_MODULE",
    "SNAPSHOT_ENVELOPE_VERSION",
    "LoadedState",
    "ModelEngine",
    "ModelEngineUnavailableError",
    "OpRejectedError",
    "UnwrappedSnapshot",
    "dispatch_ops",
    "get_model_engine",
    "load_project_state",
    "model_engine_available",
    "router",
    "unwrap_snapshot",
    "wrap_snapshot",
]
