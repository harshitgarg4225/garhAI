"""A download link must download.

The browser UAT pressed "PDF set" and nothing was saved: the signed link redirected to
the object store, the redirect carried a Content-Disposition, and the browser — which
only honours the header on the final response — rendered the PDF in a tab. S3 lets a
presigned GET override the response headers through signed ``response-*`` query
parameters; that is the only header the browser will see.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from garh_api.config import Settings
from garh_api.routers.sheets import attachment_headers
from garh_api.storage import sigv4_presign

AT = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)


def test_the_attachment_rides_in_the_signed_query(settings: Settings) -> None:
    plain = sigv4_presign("GET", "sheets/x/set.pdf", ttl_seconds=600, settings=settings, now=AT)
    saved = sigv4_presign(
        "GET",
        "sheets/x/set.pdf",
        ttl_seconds=600,
        settings=settings,
        now=AT,
        response_headers=attachment_headers("garh-export.pdf"),
    )
    query = parse_qs(urlparse(saved).query)
    assert query["response-content-disposition"] == ['attachment; filename="garh-export.pdf"']
    # Signed, not appended: a tampered disposition would not verify.
    assert parse_qs(urlparse(plain).query)["X-Amz-Signature"] != query["X-Amz-Signature"]
    assert "response-content-disposition" not in parse_qs(
        urlparse(plain).query
    ), "negative control: a plain GET must not carry a disposition"


def test_only_response_overrides_may_be_signed(settings: Settings) -> None:
    with pytest.raises(ValueError):
        sigv4_presign(
            "GET",
            "k",
            ttl_seconds=60,
            settings=settings,
            response_headers={"x-amz-acl": "public-read"},
        )


def test_a_filename_is_sanitised_and_empty_means_no_override() -> None:
    assert attachment_headers(None) is None
    assert attachment_headers("") is None
    assert attachment_headers('A-01".pdf\r\nX: y') == {
        "response-content-disposition": 'attachment; filename="A-01.pdfX y"'
    }


@pytest.mark.integration
def test_the_object_store_answers_with_the_attachment(settings: Settings) -> None:
    """Round trip through the S3-compatible store the stack runs on.

    Skipped, not passed, when no store is listening: a green that never touched the
    store would prove nothing about what the browser receives.
    """
    key = "tests/disposition/%s.txt" % AT.strftime("%H%M%S")
    put = sigv4_presign("PUT", key, ttl_seconds=60, settings=settings)
    try:
        r = httpx.put(put, content=b"%PDF-1.4 not really", timeout=5.0)
    except httpx.HTTPError as exc:  # pragma: no cover - depends on the local stack
        pytest.skip("no object store at %s: %s" % (settings.s3_endpoint_url, exc))
    assert r.status_code in (200, 201), r.text
    get = sigv4_presign(
        "GET",
        key,
        ttl_seconds=60,
        settings=settings,
        response_headers=attachment_headers("garh-export.pdf"),
    )
    got = httpx.get(get, timeout=5.0)
    assert got.status_code == 200, got.text
    assert got.headers.get("content-disposition") == 'attachment; filename="garh-export.pdf"'
    assert got.content == b"%PDF-1.4 not really"
