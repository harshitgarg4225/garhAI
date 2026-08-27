"""The job envelope — the wire contract between the API and the workers.

THIS FILE IS A CROSS-PROCESS CONTRACT. The API enqueues exactly this JSON; workers
refuse anything else. Change it in lockstep with the API's enqueue helper and bump
:data:`ENVELOPE_SCHEMA_VERSION`.

Design rules, and why:

* **Self-contained.** Everything a handler needs is in ``payload`` (or behind a
  presigned URL in ``assets`` / ``payloadRef``). Workers hold no database connection,
  so a job can never half-read a project that changed underneath it, and a worker pod
  needs no tenant credentials at all (§13, §18 "workers stateless for later k8s move").
* **No credentials.** ``assets`` and ``outputs`` carry short-lived presigned URLs
  minted by the API (§13: signed URLs ≤10 min). The worker image has no S3 keys.
* **camelCase on the wire**, snake_case in Python — the same convention the model
  document and op payloads use, so one JSON style spans the whole system.
* **Integer milliseconds** for every timestamp, integer millimetres for every length.
  No floats cross this boundary.

Example (a render job)::

    {
      "schemaVersion": 1,
      "jobId": "6f1c…", "kind": "render.image", "queue": "garh:queue:render",
      "firmId": "…", "projectId": "…", "designVersionId": "…",
      "attempt": 1, "maxAttempts": 4, "enqueuedAtMs": 1769000000000,
      "payload": {"mode": "precise", "preset": "exterior-street-day", "seed": 42},
      "assets": {"viewport_png": {"getUrl": "https://…"}, "depth_png": {…}},
      "outputs": {"image": {"putUrl": "https://…", "getUrl": "https://…",
                            "contentType": "image/png"}}
    }
"""

from __future__ import annotations

import base64
import binascii
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from services.common.errors import InvalidJobError

#: Bump on any breaking change to the envelope shape.
ENVELOPE_SCHEMA_VERSION = 1

#: Every job kind, grouped by the queue that carries it. A worker refuses a kind that
#: does not belong to its queue — a misrouted job is a bug, not something to guess at.
JOB_KINDS_BY_WORKER: dict[str, tuple[str, ...]] = {
    "solver": ("solver.generate", "solver.resolve"),
    "render": ("render.image",),
    "drawings": ("drawings.generate_sheets", "drawings.export", "drawings.import_dxf"),
}

#: Flat tuple of all known kinds.
JOB_KINDS: tuple[str, ...] = tuple(kind for kinds in JOB_KINDS_BY_WORKER.values() for kind in kinds)


def now_ms() -> int:
    """Wall-clock milliseconds. The one place time enters the queue layer."""
    return int(time.time() * 1000)


def new_job_id() -> str:
    """A fresh job id. The API normally supplies one (it owns the DB row)."""
    return str(uuid.uuid4())


@dataclass(frozen=True)
class BlobRef:
    """A pointer to one binary input or output.

    Exactly one access path is used at a time:

    ``get_url`` / ``put_url``
        presigned HTTPS (production and compose). The worker never sees S3 keys.
    ``inline_base64``
        the bytes themselves — small fixtures and unit tests only, capped by
        ``BLOB_MAX_INLINE_BYTES`` so nobody ships a 40 MB PNG through Redis.
    ``path``
        a local file, for developer scripts and golden-file runs (``file://`` too).
    """

    get_url: str | None = None
    put_url: str | None = None
    path: str | None = None
    inline_base64: str | None = None
    key: str | None = None
    content_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None

    #: Anything larger belongs in object storage, not in a queue message.
    BLOB_MAX_INLINE_BYTES = 1_048_576

    def __post_init__(self) -> None:
        if self.inline_base64 is not None:
            try:
                raw = base64.b64decode(self.inline_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise InvalidJobError(
                    "This job's inline attachment could not be read.",
                    detail="inline_base64 is not valid base64: %s" % exc,
                ) from exc
            if len(raw) > self.BLOB_MAX_INLINE_BYTES:
                raise InvalidJobError(
                    "This job's inline attachment is too large.",
                    action="Upload it to storage and pass a presigned URL instead.",
                    detail="inline blob is %d bytes (max %d)"
                    % (len(raw), self.BLOB_MAX_INLINE_BYTES),
                )

    @property
    def readable(self) -> bool:
        return bool(self.get_url or self.path or self.inline_base64 is not None)

    @property
    def writable(self) -> bool:
        return bool(self.put_url or self.path)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for wire, value in (
            ("getUrl", self.get_url),
            ("putUrl", self.put_url),
            ("path", self.path),
            ("inlineBase64", self.inline_base64),
            ("key", self.key),
            ("contentType", self.content_type),
            ("sha256", self.sha256),
            ("sizeBytes", self.size_bytes),
        ):
            if value is not None:
                out[wire] = value
        return out

    @classmethod
    def from_json(cls, data: Any, *, where: str) -> BlobRef:
        if not isinstance(data, dict):
            raise InvalidJobError(
                "This job is missing one of its files.",
                detail="%s must be an object, got %s" % (where, type(data).__name__),
            )
        size = data.get("sizeBytes")
        return cls(
            get_url=_opt_str(data.get("getUrl"), where=where + ".getUrl"),
            put_url=_opt_str(data.get("putUrl"), where=where + ".putUrl"),
            path=_opt_str(data.get("path"), where=where + ".path"),
            inline_base64=_opt_str(data.get("inlineBase64"), where=where + ".inlineBase64"),
            key=_opt_str(data.get("key"), where=where + ".key"),
            content_type=_opt_str(data.get("contentType"), where=where + ".contentType"),
            sha256=_opt_str(data.get("sha256"), where=where + ".sha256"),
            size_bytes=int(size) if isinstance(size, int) else None,
        )

    def redacted(self) -> dict[str, Any]:
        """Log-safe form: a presigned URL is a bearer credential, so show its shape."""
        out: dict[str, Any] = {}
        if self.key:
            out["key"] = self.key
        if self.content_type:
            out["contentType"] = self.content_type
        if self.size_bytes is not None:
            out["sizeBytes"] = self.size_bytes
        out["access"] = (
            "presigned"
            if (self.get_url or self.put_url)
            else "inline"
            if self.inline_base64 is not None
            else "path"
            if self.path
            else "none"
        )
        return out


@dataclass(frozen=True)
class JobEnvelope:
    """One unit of queued work."""

    job_id: str
    kind: str
    firm_id: str
    queue: str = ""
    project_id: str | None = None
    design_version_id: str | None = None
    actor_user_id: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    attempt: int = 1
    max_attempts: int = 4
    enqueued_at_ms: int = field(default_factory=now_ms)
    #: Delay-until, used by the retry scheduler. 0 = ready immediately.
    not_before_ms: int = 0
    #: Hard wall-clock deadline. Past it, the runtime fails the job instead of
    #: starting work nobody is waiting for any more.
    deadline_ms: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    payload_ref: BlobRef | None = None
    assets: dict[str, BlobRef] = field(default_factory=dict)
    outputs: dict[str, BlobRef] = field(default_factory=dict)
    schema_version: int = ENVELOPE_SCHEMA_VERSION

    # -- derived ---------------------------------------------------------
    @property
    def worker(self) -> str:
        """Which worker owns this kind (``solver`` / ``render`` / ``drawings``)."""
        for worker, kinds in JOB_KINDS_BY_WORKER.items():
            if self.kind in kinds:
                return worker
        raise InvalidJobError(
            "We do not recognise this job type.",
            detail="unknown job kind %r; known kinds: %s" % (self.kind, ", ".join(JOB_KINDS)),
        )

    @property
    def attempts_left(self) -> int:
        return max(0, self.max_attempts - self.attempt)

    @property
    def is_last_attempt(self) -> bool:
        return self.attempts_left <= 0

    def expired(self, *, at_ms: int | None = None) -> bool:
        if self.deadline_ms is None:
            return False
        return (at_ms if at_ms is not None else now_ms()) > self.deadline_ms

    def next_attempt(self, *, not_before_ms: int) -> JobEnvelope:
        """A copy for the retry queue: attempt+1 and a delay."""
        return JobEnvelope(
            job_id=self.job_id,
            kind=self.kind,
            firm_id=self.firm_id,
            queue=self.queue,
            project_id=self.project_id,
            design_version_id=self.design_version_id,
            actor_user_id=self.actor_user_id,
            request_id=self.request_id,
            idempotency_key=self.idempotency_key,
            attempt=self.attempt + 1,
            max_attempts=self.max_attempts,
            enqueued_at_ms=self.enqueued_at_ms,
            not_before_ms=not_before_ms,
            deadline_ms=self.deadline_ms,
            payload=dict(self.payload),
            payload_ref=self.payload_ref,
            assets=dict(self.assets),
            outputs=dict(self.outputs),
            schema_version=self.schema_version,
        )

    def require_asset(self, name: str) -> BlobRef:
        """Fetch a required input or fail the job permanently with useful copy."""
        ref = self.assets.get(name)
        if ref is None or not ref.readable:
            raise InvalidJobError(
                "This job is missing the %s it needs to run." % name.replace("_", " "),
                action="Start it again from the app.",
                detail="assets[%r] absent or has no readable source" % name,
            )
        return ref

    def require_output(self, name: str) -> BlobRef:
        ref = self.outputs.get(name)
        if ref is None or not ref.writable:
            raise InvalidJobError(
                "This job has nowhere to save its result.",
                action="Start it again from the app.",
                detail="outputs[%r] absent or has no writable destination" % name,
            )
        return ref

    # -- (de)serialisation ------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "jobId": self.job_id,
            "kind": self.kind,
            "queue": self.queue,
            "firmId": self.firm_id,
            "attempt": self.attempt,
            "maxAttempts": self.max_attempts,
            "enqueuedAtMs": self.enqueued_at_ms,
            "notBeforeMs": self.not_before_ms,
            "payload": self.payload,
        }
        for wire, value in (
            ("projectId", self.project_id),
            ("designVersionId", self.design_version_id),
            ("actorUserId", self.actor_user_id),
            ("requestId", self.request_id),
            ("idempotencyKey", self.idempotency_key),
            ("deadlineMs", self.deadline_ms),
        ):
            if value is not None:
                out[wire] = value
        if self.payload_ref is not None:
            out["payloadRef"] = self.payload_ref.to_json()
        if self.assets:
            out["assets"] = {name: ref.to_json() for name, ref in self.assets.items()}
        if self.outputs:
            out["outputs"] = {name: ref.to_json() for name, ref in self.outputs.items()}
        return out

    def encode(self) -> str:
        """Canonical JSON for the wire (sorted keys, no whitespace)."""
        return json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: Any) -> JobEnvelope:
        if not isinstance(data, dict):
            raise InvalidJobError(
                "We could not read this job.",
                detail="envelope must be a JSON object, got %s" % type(data).__name__,
            )
        version = data.get("schemaVersion", ENVELOPE_SCHEMA_VERSION)
        if not isinstance(version, int) or version > ENVELOPE_SCHEMA_VERSION:
            raise InvalidJobError(
                "This job was created by a newer version of the app.",
                action="Refresh the page and try again.",
                detail="envelope schemaVersion=%r, this worker understands <=%d"
                % (version, ENVELOPE_SCHEMA_VERSION),
            )

        kind = _req_str(data.get("kind"), where="kind")
        if kind not in JOB_KINDS:
            raise InvalidJobError(
                "We do not recognise this job type.",
                detail="unknown kind %r; known: %s" % (kind, ", ".join(JOB_KINDS)),
            )

        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise InvalidJobError(
                "This job's details could not be read.",
                detail="payload must be an object, got %s" % type(payload).__name__,
            )

        assets_raw = data.get("assets") or {}
        outputs_raw = data.get("outputs") or {}
        if not isinstance(assets_raw, dict) or not isinstance(outputs_raw, dict):
            raise InvalidJobError(
                "This job's files could not be read.",
                detail="assets/outputs must be objects",
            )
        payload_ref_raw = data.get("payloadRef")

        return cls(
            job_id=_req_str(data.get("jobId"), where="jobId"),
            kind=kind,
            firm_id=_req_str(data.get("firmId"), where="firmId"),
            queue=_opt_str(data.get("queue"), where="queue") or "",
            project_id=_opt_str(data.get("projectId"), where="projectId"),
            design_version_id=_opt_str(data.get("designVersionId"), where="designVersionId"),
            actor_user_id=_opt_str(data.get("actorUserId"), where="actorUserId"),
            request_id=_opt_str(data.get("requestId"), where="requestId"),
            idempotency_key=_opt_str(data.get("idempotencyKey"), where="idempotencyKey"),
            attempt=_int(data.get("attempt", 1), where="attempt", minimum=1),
            max_attempts=_int(data.get("maxAttempts", 4), where="maxAttempts", minimum=1),
            enqueued_at_ms=_int(data.get("enqueuedAtMs", now_ms()), where="enqueuedAtMs"),
            not_before_ms=_int(data.get("notBeforeMs", 0), where="notBeforeMs"),
            deadline_ms=(
                _int(data["deadlineMs"], where="deadlineMs")
                if data.get("deadlineMs") is not None
                else None
            ),
            payload=payload,
            payload_ref=(
                BlobRef.from_json(payload_ref_raw, where="payloadRef")
                if payload_ref_raw is not None
                else None
            ),
            assets={
                str(name): BlobRef.from_json(ref, where="assets.%s" % name)
                for name, ref in assets_raw.items()
            },
            outputs={
                str(name): BlobRef.from_json(ref, where="outputs.%s" % name)
                for name, ref in outputs_raw.items()
            },
            schema_version=version,
        )

    @classmethod
    def decode(cls, raw: str | bytes) -> JobEnvelope:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidJobError(
                "We could not read this job.",
                detail="envelope is not valid JSON: %s" % exc,
            ) from exc
        return cls.from_json(data)

    def log_fields(self) -> dict[str, Any]:
        """Fields safe to attach to a log line (no URLs, no free text)."""
        fields: dict[str, Any] = {
            "job_id": self.job_id,
            "job_kind": self.kind,
            "firm_id": self.firm_id,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
        }
        if self.project_id:
            fields["project_id"] = self.project_id
        if self.design_version_id:
            fields["design_version_id"] = self.design_version_id
        if self.request_id:
            fields["request_id"] = self.request_id
        if self.assets:
            fields["assets"] = sorted(self.assets)
        return fields


# ---------------------------------------------------------------------------
# small typed coercions — every failure names the field and stays user-safe
# ---------------------------------------------------------------------------
def _req_str(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidJobError(
            "We could not read this job.",
            detail="%s is required and must be a non-empty string (got %r)" % (where, value),
        )
    return value


def _opt_str(value: Any, *, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidJobError(
            "We could not read this job.",
            detail="%s must be a string when present (got %s)" % (where, type(value).__name__),
        )
    return value


def _int(value: Any, *, where: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidJobError(
            "We could not read this job.",
            detail="%s must be an integer (got %r)" % (where, value),
        )
    if minimum is not None and value < minimum:
        raise InvalidJobError(
            "We could not read this job.",
            detail="%s must be >= %d (got %d)" % (where, minimum, value),
        )
    return int(value)


__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "JOB_KINDS",
    "JOB_KINDS_BY_WORKER",
    "BlobRef",
    "JobEnvelope",
    "new_job_id",
    "now_ms",
]
