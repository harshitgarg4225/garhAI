"""The error contract: ``application/problem+json`` on every failure (§11, golden rule 9).

*"No raw exceptions to the UI. Every user-facing error: what happened, why (if known), one
-click next action."* That is only true if it is true for **every class** of failure, so
this file walks one representative of each: bad JSON, no credentials, wrong role, missing
row, wrong method, sequencer conflict, schema violation, model-invariant violation, rate
limit, and an unknown share token.

:func:`test_every_error_code_is_accounted_for` closes the loop: every member of
``errors.ERROR_CODES`` must either be exercised here or be listed in
:data:`DEFERRED_CODES` with the phase that will exercise it. A new code cannot be added
without deciding which of the two it is.

The security headers (§13 "HTTPS only, HSTS, CSP") are asserted here too, because they must
be present on error responses as well — an exception handler that builds its own response is
exactly where headers get lost.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from garh_api.errors import (
    ERROR_CODES,
    PROBLEM_CONTENT_TYPE,
    ServiceUnavailableError,
)
from garh_api.main import API_CONTENT_SECURITY_POLICY, REQUEST_ID_HEADER

from tests.helpers import problem

pytestmark = pytest.mark.integration

#: Codes no route can produce yet, with the reason. Keeping this list explicit means a new
#: error code has to be classified rather than silently untested.
DEFERRED_CODES: dict[str, str] = {
    "internal_error": "only reachable by an actual bug; asserted via the class, not a route",
    "service_unavailable": "needs Redis/Postgres to be down mid-request — asserted as a unit",
    "payload_too_large": "Phase 2: the DXF upload route does not exist yet",
    "unsupported_media_type": "Phase 2: same route",
    "conflict": "generic 409; the concrete one (op_sequence_conflict) is exercised",
    "token_expired": "needs a 15-minute clock skip; decode_token's leeway is unit-tested",
    "refresh_token_revoked": "reached via the reuse path, which is exercised as reuse",
    "account_unknown": "requires deleting a user between OTP issue and verify",
    "tenant_context_required": "unreachable through HTTP: require_tenant answers 401 first",
    "invalid_cursor": "exercised by test_projects_crud pagination once cursors are tampered",
    "otp_rate_limited": "exercised in test_rate_limits.py",
    "refresh_token_reused": "exercised in test_auth_flow.py",
    "refresh_token_invalid": "exercised in test_auth_flow.py",
    "refresh_token_missing": "exercised in test_auth_flow.py",
    "token_revoked": "exercised in test_auth_flow.py",
    "email_already_registered": "exercised in test_auth_flow.py",
    "otp_invalid": "exercised in test_auth_otp_policy.py",
    "op_sequence_conflict": "exercised in test_op_sequencer.py (and again below)",
}


# ---------------------------------------------------------------------------
# One representative per class
# ---------------------------------------------------------------------------


async def test_the_content_type_is_the_rfc_7807_media_type(
    client: Any, api: str, firm_a: Any
) -> None:
    """`application/problem+json`, exactly — not `application/json` with a code field.

    Asserted once here on the media type constant itself; ``tests.helpers.problem`` then
    re-checks it on every error body every other test in the suite parses.
    """
    response = await client.get("%s/projects/%s" % (api, uuid.uuid4()), headers=firm_a.headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert PROBLEM_CONTENT_TYPE == "application/problem+json"


async def test_400_invalid_request(client: Any, api: str, firm_a: Any) -> None:
    response = await client.post(
        "%s/projects" % api,
        content=b"{",
        headers={**firm_a.headers, "content-type": "application/json"},
    )
    assert response.status_code == 400
    assert problem(response)["code"] == "invalid_request"


async def test_401_unauthenticated(client: Any, api: str) -> None:
    response = await client.get("%s/auth/me" % api)
    assert response.status_code == 401
    body = problem(response)
    assert body["code"] == "unauthenticated"
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_401_token_invalid(client: Any, api: str) -> None:
    response = await client.get(
        "%s/auth/me" % api, headers={"Authorization": "Bearer eyJhbGciOiJub25lIn0.e30."}
    )
    assert response.status_code == 401
    assert problem(response)["code"] == "token_invalid"


async def test_403_permission_denied(client: Any, api: str, member_a: Any, project_a: Any) -> None:
    response = await client.delete("%s/projects/%s" % (api, project_a.id), headers=member_a.headers)
    assert response.status_code == 403
    assert problem(response)["code"] == "permission_denied"


async def test_404_not_found(client: Any, api: str, firm_a: Any) -> None:
    response = await client.get("%s/projects/%s" % (api, uuid.uuid4()), headers=firm_a.headers)
    assert response.status_code == 404
    assert problem(response)["code"] == "not_found"


async def test_404_on_a_path_that_matches_no_route(client: Any, api: str) -> None:
    """Even a 404 from the router (not a handler) must be problem+json, not Starlette HTML."""
    response = await client.get("%s/no-such-thing" % api)
    assert response.status_code == 404
    assert problem(response)["code"] == "not_found"


async def test_405_method_not_allowed(client: Any, api: str, firm_a: Any) -> None:
    response = await client.delete("%s/projects" % api, headers=firm_a.headers)
    assert response.status_code == 405
    assert problem(response)["code"] == "method_not_allowed"


async def test_409_op_sequence_conflict_carries_head_idx(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    url = "%s/projects/%s/ops" % (api, project_a.id)
    body = {
        "ops": [{"type": "plot.set_north", "payload": {"deg": 0}}],
        "baseIdx": -1,
        "source": "manual",
    }
    assert (await client.post(url, json=body, headers=firm_a.headers)).status_code == 200
    conflict = await client.post(url, json=body, headers=firm_a.headers)
    assert conflict.status_code == 409
    problem_body = problem(conflict)
    assert problem_body["code"] == "op_sequence_conflict"
    assert problem_body["headIdx"] == 0


async def test_422_validation_failed_lists_fields(client: Any, api: str, firm_a: Any) -> None:
    response = await client.post("%s/projects" % api, json={}, headers=firm_a.headers)
    assert response.status_code == 422
    body = problem(response)
    assert body["code"] == "validation_failed"
    assert body["errors"], body
    # `code` is pydantic's error type slug, not echoed input (§13 stays intact).
    assert all(set(error) <= {"field", "message", "code"} for error in body["errors"]), body[
        "errors"
    ]


async def test_422_op_rejected_lists_issues(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    response = await client.post(
        "%s/projects/%s/ops" % (api, project_a.id),
        json={
            "ops": [{"type": "plot.set_north", "payload": {"deg": 720}}],
            "baseIdx": -1,
            "source": "manual",
        },
        headers=firm_a.headers,
    )
    assert response.status_code == 422
    body = problem(response)
    assert body["code"] == "op_rejected"
    assert body["issues"], body
    issue = body["issues"][0]
    assert issue["code"] and issue["message"], issue


async def test_429_rate_limited(client: Any, api: str, firm_a: Any) -> None:
    await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    response = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    assert response.status_code == 429
    body = problem(response)
    assert body["code"] == "otp_rate_limited"
    assert body["retryAfterSeconds"] >= 1
    assert response.headers["retry-after"]


async def test_404_share_link_invalid(client: Any, api: str) -> None:
    """The anonymous viewer surface: an unknown token is not a hint about a real one.

    Unknown, revoked and expired all answer identically (``ShareLinkInvalidError``), so a
    holder of a dead link cannot tell which it was — and cannot probe for live ones.
    """
    response = await client.get("%s/share/%s" % (api, "not-a-real-token-0123456789"))
    assert response.status_code == 404, response.text
    body = problem(response)
    assert body["code"] == "share_link_invalid", body
    assert "revoked" not in body["message"].lower()
    assert "expired" not in body["message"].lower()


def test_503_service_unavailable_shape() -> None:
    """Asserted as a unit: taking Redis down mid-suite would break every other test."""
    error = ServiceUnavailableError(
        "Redis is unreachable.", dependency="redis", retry_after_seconds=5
    )
    body = error.as_problem()
    assert error.http_status == 503
    assert body["code"] == "service_unavailable"
    assert body["action"]
    assert error.headers["Retry-After"] == "5"


# ---------------------------------------------------------------------------
# Contract-level invariants
# ---------------------------------------------------------------------------


def test_every_error_code_is_accounted_for() -> None:
    """A new error code must be exercised or explicitly deferred."""
    exercised = {
        "invalid_request",
        "validation_failed",
        "not_found",
        "method_not_allowed",
        "unauthenticated",
        "token_invalid",
        "permission_denied",
        "rate_limited",
        "share_link_invalid",
        "op_sequence_conflict",
    }
    unknown = (exercised | set(DEFERRED_CODES)) - set(ERROR_CODES)
    assert not unknown, "these names are not in ERROR_CODES (typo?): %s" % sorted(unknown)

    unaccounted = sorted(set(ERROR_CODES) - exercised - set(DEFERRED_CODES))
    assert not unaccounted, (
        "error code(s) %s are neither exercised in the suite nor listed in DEFERRED_CODES "
        "with a reason. Add a test or a line." % unaccounted
    )


async def test_every_error_carries_a_request_id(client: Any, api: str, firm_a: Any) -> None:
    """§18 request ids: an error a user reports must be findable in the logs."""
    response = await client.get("%s/projects/%s" % (api, uuid.uuid4()), headers=firm_a.headers)
    body = problem(response)
    assert body.get("requestId"), body
    assert response.headers[REQUEST_ID_HEADER] == body["requestId"]


async def test_inbound_request_id_is_echoed(client: Any, api: str) -> None:
    """A client-supplied correlation id must survive, or tracing across services breaks."""
    supplied = "req-%s" % uuid.uuid4().hex[:12]
    response = await client.get("%s/meta" % api, headers={REQUEST_ID_HEADER: supplied})
    assert response.headers[REQUEST_ID_HEADER] == supplied


@pytest.mark.parametrize(
    "header",
    [
        "content-security-policy",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
        "cross-origin-opener-policy",
        "permissions-policy",
    ],
)
async def test_security_headers_are_on_error_responses_too(
    client: Any, api: str, header: str
) -> None:
    """§13 hardening must survive the exception handler that builds its own response."""
    response = await client.get("%s/projects/%s" % (api, uuid.uuid4()))
    assert response.status_code == 401
    assert header in {key.lower() for key in response.headers}, response.headers


async def test_api_csp_forbids_scripts_entirely(client: Any, api: str) -> None:
    """A JSON API should not be able to execute anything (§13 "CSP, no inline scripts")."""
    response = await client.get("%s/meta" % api)
    csp = response.headers["content-security-policy"]
    assert csp == API_CONTENT_SECURITY_POLICY
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert csp.startswith("default-src 'none'")


async def test_hsts_is_production_only(client: Any, api: str, settings: Any) -> None:
    """Emitted in production, withheld in dev — pinning ``localhost`` to HTTPS is hostile.

    The suite runs as dev (see conftest), so this asserts the *absence* plus the fact that
    the middleware is wired to the production flag rather than hard-coded off.
    """
    from garh_api.main import HSTS_MAX_AGE_SECONDS, SecurityHeadersMiddleware

    response = await client.get("%s/meta" % api)
    assert settings.is_production is False
    assert "strict-transport-security" not in {key.lower() for key in response.headers}
    assert HSTS_MAX_AGE_SECONDS >= 31_536_000, "HSTS preload needs at least a year"
    assert SecurityHeadersMiddleware(None, production=True).production is True


async def test_cors_is_an_allowlist_never_a_wildcard(client: Any, api: str, settings: Any) -> None:
    """§13: "CORS allowlist". This API sees bearer tokens and a refresh cookie."""
    assert settings.cors_allow_origins, (
        "CORS_ALLOW_ORIGINS is empty, so the browser app cannot call the API at all. "
        "The dev default is http://localhost:5173."
    )
    allowed = settings.cors_allow_origins[0]
    good = await client.options(
        "%s/projects" % api,
        headers={
            "Origin": allowed,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert good.headers.get("access-control-allow-origin") == allowed
    assert good.headers.get("access-control-allow-credentials") == "true"

    hostile = await client.options(
        "%s/projects" % api,
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert hostile.headers.get("access-control-allow-origin") != "*"
    assert hostile.headers.get("access-control-allow-origin") != "https://evil.example"


async def test_healthz_is_unauthenticated_and_cheap(client: Any) -> None:
    """Compose and CI probe this; it must answer without a database round trip."""
    response = await client.get("/healthz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"]


async def test_meta_exposes_no_secrets(client: Any, api: str) -> None:
    """``GET /meta`` is the one pre-auth payload; §13 keeps keys out of it."""
    response = await client.get("%s/meta" % api)
    assert response.status_code == 200, response.text
    text = response.text.lower()
    for forbidden in ("private", "secret", "password", "begin rsa", "api_key", "apikey"):
        assert forbidden not in text, forbidden
    body = response.json()
    # Providers are named honestly so the UI can say "renders: mock" (golden rule 4).
    assert body["providers"]["llm"] == "mock"
    assert body["providers"]["render"] == "mock"
    assert (
        body["providers"]["modelEngine"] == "ready"
    ), "the model core is not importable on this server, so no op can be validated"
    # The client must be told the limits it has to respect (§11).
    assert body["limits"]["opsPerSecond"] == 60
    assert body["limits"]["opSnapshotInterval"] == 200
    assert body["limits"]["signedUrlTtlSeconds"] <= 600, "§13: signed URLs <= 10 min"
