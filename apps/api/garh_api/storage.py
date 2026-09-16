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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

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
    response_headers: Mapping[str, str] | None = None,
    extra_query: Mapping[str, str] | None = None,
) -> str:
    """AWS Signature V4 presigned URL (query auth, UNSIGNED-PAYLOAD), path-style.

    ``extra_query`` is for the bucket-level operations the backup tooling needs
    (``list-type=2&prefix=…``); it is NOT the download path — ``response_headers``
    keeps its ``response-*`` gate, and nothing user-facing passes this argument.

    ``response_headers`` are S3's ``response-*`` overrides (``response-content-disposition``
    and friends): they ride in the signed query string, so the object store answers the
    GET with the header the caller asked for. That is how a download link makes the
    browser SAVE a PDF instead of rendering it in a tab — a 307 that carries its own
    Content-Disposition is discarded by the browser, which only honours the final
    response's.
    """
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
    for name, value in (response_headers or {}).items():
        if not name.startswith("response-"):
            raise ValueError("only S3 response-* overrides may be presigned: %r" % name)
        params[name] = value
    for name, value in (extra_query or {}).items():
        if name.startswith("X-Amz-"):
            raise ValueError("signing parameters are not caller-supplied: %r" % name)
        params[name] = value
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


@dataclass(frozen=True, slots=True)
class StoredObject:
    """One key from a bucket listing."""

    key: str
    size: int
    last_modified: datetime


def _local(tag: str) -> str:
    """``{ns}Key`` → ``Key``: S3's ListObjects XML carries a namespace, MinIO's may not."""
    return tag.rsplit("}", 1)[-1]


def parse_listing(xml_text: str) -> tuple[list[StoredObject], str | None]:
    """ListObjectsV2 XML → objects plus the continuation token, if truncated."""
    root = ElementTree.fromstring(xml_text)
    objects: list[StoredObject] = []
    truncated = False
    token: str | None = None
    for child in root:
        name = _local(child.tag)
        if name == "IsTruncated":
            truncated = (child.text or "").strip().lower() == "true"
        elif name == "NextContinuationToken":
            token = (child.text or "").strip() or None
        elif name == "Contents":
            fields = {_local(item.tag): (item.text or "").strip() for item in child}
            if "Key" not in fields:
                continue
            stamp = fields.get("LastModified", "").replace("Z", "+00:00")
            try:
                modified = datetime.fromisoformat(stamp)
            except ValueError:
                modified = datetime.fromtimestamp(0, tz=UTC)
            objects.append(
                StoredObject(
                    key=fields["Key"],
                    size=int(fields.get("Size") or 0),
                    last_modified=modified,
                )
            )
    return objects, (token if truncated else None)


async def list_objects(prefix: str, *, settings: Settings) -> list[StoredObject]:
    """Every object under ``prefix``, following continuation tokens.

    Used by the backup tooling only (``scripts/backup_s3.py``): retention needs to
    see what is in the bucket. Raises on a storage failure — a retention pass that
    silently saw nothing would delete nothing and report success.
    """
    found: list[StoredObject] = []
    token: str | None = None
    while True:
        query: dict[str, str] = {"list-type": "2", "prefix": prefix}
        if token is not None:
            query["continuation-token"] = token
        url = sigv4_presign(
            "GET", "", ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings, extra_query=query
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(float(STORAGE_TIMEOUT_SECONDS)), follow_redirects=False
        ) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            raise ServiceUnavailableError(
                "Object storage refused the listing (HTTP %d)." % response.status_code,
                dependency="object-storage",
                retry_after_seconds=10,
            )
        page, token = parse_listing(response.text)
        found.extend(page)
        if token is None:
            return found


async def get_object(key: str, *, settings: Settings) -> bytes:
    """GET an object's bytes via a presigned URL. Backup tooling only."""
    url = sigv4_presign("GET", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(float(STORAGE_TIMEOUT_SECONDS) * 10), follow_redirects=False
    ) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise ServiceUnavailableError(
            "Object storage refused the download (HTTP %d)." % response.status_code,
            dependency="object-storage",
            retry_after_seconds=10,
        )
    return response.content


__all__ = [
    "PUT_URL_TTL_SECONDS",
    "STORAGE_TIMEOUT_SECONDS",
    "StoredObject",
    "delete_object",
    "get_object",
    "list_objects",
    "parse_listing",
    "put_object",
    "sigv4_presign",
]
