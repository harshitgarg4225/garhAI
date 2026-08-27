"""Object storage for the API — stdlib SigV4 against ``Settings.s3_*``.

This is the promotion ``routers/imports.py`` promised in its own docstring:
*"the SigV4 presigner lives here, stdlib-only … promote it to
``garh_api/storage.py`` when a second uploader appears."* The second uploader is
the underlay image route (``routers/underlay.py``), and by the time it arrived
three more routers (renders, sheets, jobs) had already reached into the imports
module for the private ``_sigv4_presign``. So the machinery now lives here, and
``routers.imports`` re-exports it under its old name — every existing import
keeps working, and there is still exactly ONE signer to audit against the §13
checklist.

Deliberately stdlib-only (hmac/hashlib): boto3 would be a heavyweight dependency
for the three HTTP verbs the API performs, and minio/moto speak SigV4 natively.

Three operations, and their §13 postures:

* :func:`sigv4_presign` — query-auth presigned URL (UNSIGNED-PAYLOAD, path-style).
  GETs handed to clients/workers are capped by ``s3_signed_url_ttl_seconds``
  (≤10 min); PUTs the API mints for itself use :data:`PUT_URL_TTL_SECONDS`.
* :func:`put_object` — durable or dead: a storage failure is a clean 503 with
  ``Retry-After``, never a half-stored upload.
* :func:`delete_object` — best-effort by contract. Deleting a row whose object
  lingers costs pennies; failing a user's DELETE because storage hiccuped costs
  trust. Returns whether the object is confirmed gone so callers can log it.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

import httpx

from garh_api.config import Settings, get_settings
from garh_api.errors import ServiceUnavailableError
from garh_api.logging import get_logger

_log = get_logger(__name__)

#: Presigned PUT the API uses for its own upload — short because the PUT happens
#: within the same request. Worker/client-facing GETs use
#: ``settings.s3_signed_url_ttl_seconds`` (§13: signed URLs ≤10 min).
PUT_URL_TTL_SECONDS = 300

STORAGE_TIMEOUT_SECONDS = 30


def sigv4_presign(
    method: str,
    key: str,
    *,
    ttl_seconds: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> str:
    """AWS Signature V4 presigned URL (query auth, UNSIGNED-PAYLOAD), path-style."""
    cfg = settings or get_settings()
    endpoint = urlparse(cfg.s3_endpoint_url)
    host = endpoint.netloc
    canonical_uri = "/%s/%s" % (cfg.s3_bucket, quote(key, safe="/-_.~"))
    at = now or datetime.now(UTC)
    amz_date = at.strftime("%Y%m%dT%H%M%SZ")
    datestamp = at.strftime("%Y%m%d")
    scope = "%s/%s/s3/aws4_request" % (datestamp, cfg.s3_region)

    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": "%s/%s" % (cfg.s3_access_key_id, scope),
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(int(ttl_seconds)),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        "%s=%s" % (quote(name, safe="-_.~"), quote(value, safe="-_.~"))
        for name, value in sorted(params.items())
    )
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_query, "host:%s\n" % host, "host", "UNSIGNED-PAYLOAD"]
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    def _hmac(key_bytes: bytes, message: str) -> bytes:
        return hmac.new(key_bytes, message.encode("utf-8"), hashlib.sha256).digest()

    signing_key = _hmac(
        _hmac(
            _hmac(
                _hmac(("AWS4" + cfg.s3_secret_access_key).encode("utf-8"), datestamp), cfg.s3_region
            ),
            "s3",
        ),
        "aws4_request",
    )
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return "%s://%s%s?%s&X-Amz-Signature=%s" % (
        endpoint.scheme or "http",
        host,
        canonical_uri,
        canonical_query,
        signature,
    )


async def put_object(key: str, data: bytes, *, content_type: str, settings: Settings) -> None:
    """PUT bytes to object storage via a presigned URL the API mints for itself.

    A storage outage is a clean 503 with Retry-After — the §13 posture is that
    the object is either durably stored or the request failed; nothing
    half-happens.
    """
    put_url = sigv4_presign("PUT", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(float(STORAGE_TIMEOUT_SECONDS)), follow_redirects=False
        ) as client:
            response = await client.put(
                put_url, content=data, headers={"content-type": content_type}
            )
    except httpx.HTTPError as exc:
        _log.error("storage.unreachable", key=key, error="%s: %s" % (type(exc).__name__, exc))
        raise ServiceUnavailableError(
            "We couldn't store that file just now.",
            dependency="object-storage",
            retry_after_seconds=10,
        ) from exc
    if response.status_code >= 400:
        _log.error("storage.put_failed", key=key, status_code=response.status_code)
        raise ServiceUnavailableError(
            "We couldn't store that file just now.",
            dependency="object-storage",
            retry_after_seconds=10,
        )


async def delete_object(key: str, *, settings: Settings) -> bool:
    """Best-effort DELETE. Returns True when the object is confirmed gone.

    Callers deleting a database row must not fail on a storage hiccup — an
    orphaned object is a cost problem, a failed user action is a trust problem.
    Failures are logged (with the key, so an operator can sweep) and swallowed.
    """
    delete_url = sigv4_presign("DELETE", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(float(STORAGE_TIMEOUT_SECONDS)), follow_redirects=False
        ) as client:
            response = await client.delete(delete_url)
    except httpx.HTTPError as exc:
        _log.warning("storage.delete_failed", key=key, error="%s: %s" % (type(exc).__name__, exc))
        return False
    # 404 counts as gone: the aim is "no orphan", not "we performed a delete".
    if response.status_code >= 400 and response.status_code != 404:
        _log.warning("storage.delete_failed", key=key, status_code=response.status_code)
        return False
    return True


__all__ = [
    "PUT_URL_TTL_SECONDS",
    "STORAGE_TIMEOUT_SECONDS",
    "delete_object",
    "put_object",
    "sigv4_presign",
]
