"""Job asset IO — presigned URLs, local files, inline bytes.

§13 says downloads go through short-lived signed URLs, and it is the API that mints
them. The consequence for workers is a good one: **a worker holds no S3 credentials**.
It receives ``getUrl`` for each input and ``putUrl`` for each output inside the job
envelope, and object storage is otherwise invisible to it. No boto3, no key rotation
story for the worker pool, nothing to leak from a render container that also runs
third-party model code.

Three access paths, all handled here:

``https://`` / ``http://``
    presigned. GET to read, PUT to write.
``file://`` or a bare path
    developer scripts, golden-file runs, ``pytest``.
``inlineBase64``
    tiny fixtures embedded in the envelope.

Integrity: when a :class:`~services.common.envelope.BlobRef` carries ``sha256`` the
bytes are verified after fetch and the job fails cleanly on mismatch — a truncated
depth map should not become a bad render.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services.common.envelope import BlobRef
from services.common.errors import BlobError, InvalidJobError
from services.common.logging import get_logger

log = get_logger("blobs")

DEFAULT_CONTENT_TYPE = "application/octet-stream"


class BlobClient:
    """Fetches and stores job assets. One instance per worker process."""

    def __init__(self, *, timeout_seconds: int = 60, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self._http: Any | None = None

    # ------------------------------------------------------------------
    async def fetch(self, ref: BlobRef, *, what: str = "file") -> bytes:
        """Read a blob. Raises :class:`BlobError` (retryable) on transport failure."""
        if ref.inline_base64 is not None:
            data = base64.b64decode(ref.inline_base64, validate=True)
        elif ref.get_url:
            data = await self._http_get(ref.get_url, what=what)
        elif ref.path:
            data = self._read_file(ref.path, what=what)
        else:
            raise InvalidJobError(
                "This job is missing the %s it needs." % what,
                action="Start it again from the app.",
                detail="BlobRef has no readable source",
            )

        if len(data) > self.max_bytes:
            raise BlobError(
                "The %s attached to this job is too large to process." % what,
                detail="%d bytes exceeds the %d byte cap" % (len(data), self.max_bytes),
            )
        if ref.sha256:
            actual = hashlib.sha256(data).hexdigest()
            if actual != ref.sha256.lower():
                raise BlobError(
                    "The %s attached to this job arrived damaged." % what,
                    action="Try again.",
                    detail="sha256 mismatch: expected %s, got %s" % (ref.sha256, actual),
                )
        log.info("blob.fetched", what=what, bytes=len(data), source=ref.redacted()["access"])
        return data

    async def put(
        self, ref: BlobRef, data: bytes, *, content_type: str | None = None, what: str = "result"
    ) -> BlobRef:
        """Write a blob and return an updated ref carrying its size and digest."""
        ctype = content_type or ref.content_type or DEFAULT_CONTENT_TYPE
        if len(data) > self.max_bytes:
            raise BlobError(
                "This job produced a %s that is too large to store." % what,
                detail="%d bytes exceeds the %d byte cap" % (len(data), self.max_bytes),
            )
        if ref.put_url:
            await self._http_put(ref.put_url, data, content_type=ctype, what=what)
        elif ref.path:
            self._write_file(ref.path, data)
        else:
            raise InvalidJobError(
                "This job has nowhere to save its %s." % what,
                action="Start it again from the app.",
                detail="BlobRef has no writable destination",
            )
        digest = hashlib.sha256(data).hexdigest()
        log.info("blob.stored", what=what, bytes=len(data), content_type=ctype)
        return BlobRef(
            get_url=ref.get_url,
            put_url=ref.put_url,
            path=ref.path,
            key=ref.key,
            content_type=ctype,
            sha256=digest,
            size_bytes=len(data),
        )

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(float(self.timeout_seconds)),
                follow_redirects=False,  # a presigned URL never legitimately redirects
            )
        return self._http

    async def _http_get(self, url: str, *, what: str) -> bytes:
        import httpx

        _require_http(url, what=what)
        try:
            response = await self._client().get(url)
        except httpx.HTTPError as exc:
            raise BlobError(
                "We could not read the %s for this job." % what,
                action="Try again in a moment.",
                detail="GET failed: %s" % exc,
            ) from exc
        if response.status_code >= 400:
            raise BlobError(
                "We could not read the %s for this job." % what,
                action="Try again in a moment.",
                detail="GET returned HTTP %d" % response.status_code,
            )
        content = response.content
        return bytes(content)

    async def _http_put(self, url: str, data: bytes, *, content_type: str, what: str) -> None:
        import httpx

        _require_http(url, what=what)
        try:
            response = await self._client().put(
                url, content=data, headers={"content-type": content_type}
            )
        except httpx.HTTPError as exc:
            raise BlobError(
                "We could not save the %s for this job." % what,
                action="Try again in a moment.",
                detail="PUT failed: %s" % exc,
            ) from exc
        if response.status_code >= 400:
            raise BlobError(
                "We could not save the %s for this job." % what,
                action="Try again in a moment.",
                detail="PUT returned HTTP %d" % response.status_code,
            )

    def _read_file(self, path: str, *, what: str) -> bytes:
        target = _local_path(path)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise BlobError(
                "We could not read the %s for this job." % what,
                detail="reading %s failed: %s" % (target, exc),
            ) from exc

    def _write_file(self, path: str, data: bytes) -> None:
        target = _local_path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as exc:
            raise BlobError(
                "We could not save this job's result.",
                detail="writing %s failed: %s" % (target, exc),
            ) from exc


def _local_path(path: str) -> Path:
    if path.startswith("file://"):
        return Path(urlparse(path).path)
    return Path(path)


def _require_http(url: str, *, what: str) -> None:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidJobError(
            "This job's %s is not somewhere we can reach." % what,
            detail="unsupported URL scheme %r" % scheme,
        )


__all__ = ["DEFAULT_CONTENT_TYPE", "BlobClient"]
