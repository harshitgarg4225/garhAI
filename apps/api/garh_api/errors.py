"""The error contract (playbook §11 problem+json, golden rule 9).

Every error the API emits — every single one, including the ones we did not think of —
leaves as::

    Content-Type: application/problem+json

    {
      "code": "op_sequence_conflict",       # stable, machine-readable, never localised
      "message": "This design moved on while you were editing.",
      "action": "Fetch ops since your base index, rebase, and re-send.",
      "requestId": "9f2c…"                  # for support, always present
    }

``action`` is not decoration. Golden rule 9 says *errors say what to do next*, so the
field is mandatory on every error class here: if you cannot write a next step for a
failure mode, you have not finished designing it. ``message`` says what happened in
plain words and never blames the user (§15 tone); ``action`` says what to do.

Three rules the handlers enforce that are easy to get wrong:

1. **A missing row and another firm's row look identical.** A scoped repository read
   that matches nothing raises ``EntityNotFoundError`` whether the row does not exist or
   belongs to another firm — same class, same 404, same ``not_found`` code, same
   message. (``CrossTenantAccessError`` is the separate defensive path for a row that
   arrived from outside a scoped query; it is also 404 ``not_found``.) A 403 with a
   detailed reason would confirm the resource exists (§13 AuthZ).
2. **Stack traces never reach the client.** The catch-all returns a fixed 500 body and
   logs the exception with the request id, so support correlates by ``requestId``
   instead of by reading a traceback in a browser tab.
3. **Validation errors never echo the submitted value.** Pydantic puts the offending
   input in ``ctx``/``input``; for a body containing an OTP code or a token that would
   write the secret straight into the response and the access log. Both keys are
   dropped.

Usage — ``main.py`` calls :func:`install_error_handlers(app)` once, and route code
just raises::

    raise OtpVerificationError()                     # 400, generic on purpose
    raise RateLimitedError(retry_after_seconds=42)   # 429 + Retry-After
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from garh_api.logging import current_request_id, get_logger
from garh_api.tenancy import TenancyError

_log = get_logger(__name__)

#: RFC 7807 media type. Every error response uses it, success responses never do.
PROBLEM_CONTENT_TYPE = "application/problem+json"


# ---------------------------------------------------------------------------
# Code strings — the machine-readable half of the contract
# ---------------------------------------------------------------------------
# Clients switch on these. Adding one is additive; changing or removing one is a
# breaking API change and needs a DECISIONS.md row.

CODE_INTERNAL = "internal_error"
CODE_SERVICE_UNAVAILABLE = "service_unavailable"
CODE_NOT_FOUND = "not_found"
CODE_METHOD_NOT_ALLOWED = "method_not_allowed"
CODE_INVALID_REQUEST = "invalid_request"
CODE_VALIDATION_FAILED = "validation_failed"
CODE_CONFLICT = "conflict"
CODE_PAYLOAD_TOO_LARGE = "payload_too_large"
CODE_UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
CODE_RATE_LIMITED = "rate_limited"

# -- authentication / session ------------------------------------------------
CODE_UNAUTHENTICATED = "unauthenticated"
CODE_TOKEN_EXPIRED = "token_expired"
CODE_TOKEN_INVALID = "token_invalid"
CODE_TOKEN_REVOKED = "token_revoked"
CODE_REFRESH_MISSING = "refresh_token_missing"
CODE_REFRESH_INVALID = "refresh_token_invalid"
CODE_REFRESH_REVOKED = "refresh_token_revoked"
CODE_REFRESH_REUSED = "refresh_token_reused"
CODE_OTP_INVALID = "otp_invalid"
CODE_OTP_RATE_LIMITED = "otp_rate_limited"
CODE_EMAIL_ALREADY_REGISTERED = "email_already_registered"
CODE_ACCOUNT_UNKNOWN = "account_unknown"

# -- authorisation / tenancy (raised by garh_api.tenancy, listed for completeness) --
CODE_PERMISSION_DENIED = "permission_denied"
CODE_TENANT_CONTEXT_REQUIRED = "tenant_context_required"
CODE_OP_SEQUENCE_CONFLICT = "op_sequence_conflict"
CODE_INVALID_CURSOR = "invalid_cursor"

# -- share links -------------------------------------------------------------
CODE_SHARE_LINK_INVALID = "share_link_invalid"

#: Every code this module can emit, plus the four the tenancy layer owns. Tests assert
#: that no handler produces a code outside this set.
ERROR_CODES: tuple[str, ...] = (
    CODE_INTERNAL,
    CODE_SERVICE_UNAVAILABLE,
    CODE_NOT_FOUND,
    CODE_METHOD_NOT_ALLOWED,
    CODE_INVALID_REQUEST,
    CODE_VALIDATION_FAILED,
    CODE_CONFLICT,
    CODE_PAYLOAD_TOO_LARGE,
    CODE_UNSUPPORTED_MEDIA_TYPE,
    CODE_RATE_LIMITED,
    CODE_UNAUTHENTICATED,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_INVALID,
    CODE_TOKEN_REVOKED,
    CODE_REFRESH_MISSING,
    CODE_REFRESH_INVALID,
    CODE_REFRESH_REVOKED,
    CODE_REFRESH_REUSED,
    CODE_OTP_INVALID,
    CODE_OTP_RATE_LIMITED,
    CODE_EMAIL_ALREADY_REGISTERED,
    CODE_ACCOUNT_UNKNOWN,
    CODE_PERMISSION_DENIED,
    CODE_TENANT_CONTEXT_REQUIRED,
    CODE_OP_SEQUENCE_CONFLICT,
    CODE_INVALID_CURSOR,
    CODE_SHARE_LINK_INVALID,
)


# ---------------------------------------------------------------------------
# The wire model (also what OpenAPI shows)
# ---------------------------------------------------------------------------


class ProblemModel(BaseModel):
    """problem+json body. Extra keys are allowed — some errors add context.

    ``op_sequence_conflict`` adds ``headIdx`` so the client can rebase without a second
    round trip; ``validation_failed`` adds ``errors[]``; ``rate_limited`` adds
    ``retryAfterSeconds``.
    """

    model_config = ConfigDict(extra="allow")

    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Plain-language description of what happened.")
    action: str = Field(description="The one thing the user (or client) should do next.")
    request_id: str | None = Field(
        default=None,
        alias="requestId",
        serialization_alias="requestId",
        description="Correlates with server logs. Quote it to support.",
    )


class FieldErrorModel(BaseModel):
    """One entry of ``validation_failed``'s ``errors`` array."""

    model_config = ConfigDict(populate_by_name=True)

    field: str = Field(description="Dotted path to the offending field, e.g. 'ops.0.type'.")
    message: str
    code: str = Field(description="Pydantic error type, e.g. 'string_too_short'.")


#: Drop-in for a router's ``responses=`` so the OpenAPI schema documents the contract::
#:
#:     @router.post("/verify", responses=PROBLEM_RESPONSES)
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemModel, "description": "Bad request"},
    401: {"model": ProblemModel, "description": "Not signed in / token rejected"},
    403: {"model": ProblemModel, "description": "Signed in, but not allowed"},
    404: {"model": ProblemModel, "description": "No such thing for this firm"},
    409: {"model": ProblemModel, "description": "Conflict"},
    422: {"model": ProblemModel, "description": "Validation failed"},
    429: {"model": ProblemModel, "description": "Rate limited"},
    500: {"model": ProblemModel, "description": "Unexpected server error"},
    503: {"model": ProblemModel, "description": "Dependency unavailable"},
}


def problem_body(
    code: str,
    message: str,
    action: str,
    *,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a problem+json dict, always stamped with the current request id."""
    body: dict[str, Any] = {"code": code, "message": message, "action": action}
    request_id = current_request_id()
    if request_id:
        body["requestId"] = request_id
    if extra:
        for key, value in extra.items():
            if key not in ("code", "message", "action"):
                body[key] = value
    return body


# ---------------------------------------------------------------------------
# ApiError — everything raised above the repository layer
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """Base class for errors the HTTP layer raises deliberately.

    Mirrors :class:`garh_api.tenancy.TenancyError` on purpose: both expose
    ``http_status`` and ``as_problem()``, so one handler shape serves both. Subclass
    and set the four class attributes; do not raise ``HTTPException`` anywhere in this
    codebase (it produces ``{"detail": ...}``, which is not our contract).
    """

    http_status: ClassVar[int] = 500
    code: ClassVar[str] = CODE_INTERNAL
    action: ClassVar[str] = "Try again. If it keeps happening, contact support."
    default_message: ClassVar[str] = "Something went wrong on our side."
    #: Headers required by the semantics of the status (WWW-Authenticate, Retry-After).
    base_headers: ClassVar[Mapping[str, str]] = {}

    def __init__(
        self,
        message: str | None = None,
        *,
        action: str | None = None,
        extra: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message or type(self).default_message)
        self._action = action or type(self).action
        self._extra: dict[str, Any] = dict(extra or {})
        self._headers: dict[str, str] = {**type(self).base_headers, **dict(headers or {})}

    @property
    def problem_action(self) -> str:
        return self._action

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._headers)

    def with_headers(self, headers: Mapping[str, str]) -> ApiError:
        """Attach extra response headers to an error already in flight, and return it.

        The reason this exists: a failed ``POST /auth/refresh`` must also *clear* the
        refresh cookie, or the browser keeps replaying a token we have just declared
        dead. Exception handlers build their own response, so a ``Set-Cookie`` the
        handler set on its injected ``Response`` would be discarded — the header has to
        travel on the exception::

            except AuthenticationError as exc:
                exc.with_headers(expired_cookie_header)
                raise
        """
        self._headers.update(headers)
        return self

    def as_problem(self) -> dict[str, Any]:
        # ``self.code``, not ``type(self).code``: RateLimitedError lets a caller pass a
        # narrower code (``otp_rate_limited``) with identical semantics, and that
        # override is an instance attribute. Reading the class attribute here would
        # silently discard it and emit the generic code instead. Mirrors
        # ``TenancyError.as_problem``.
        return problem_body(self.code, str(self), self._action, extra=self._extra)


# -- 4xx: the client can fix it ---------------------------------------------


class InvalidRequestError(ApiError):
    """Syntactically bad request that Pydantic did not catch (bad JSON, bad header)."""

    http_status = 400
    code = CODE_INVALID_REQUEST
    default_message = "We couldn't read that request."
    action = "Check the request format and try again."


class ValidationFailedError(ApiError):
    """Semantic validation failure raised by hand (Pydantic's own path is separate)."""

    http_status = 422
    code = CODE_VALIDATION_FAILED
    default_message = "Some of those values don't work."
    action = "Fix the highlighted fields and try again."


class ConflictError(ApiError):
    """Generic 409 for state that moved on. Op-log conflicts use the tenancy error."""

    http_status = 409
    code = CODE_CONFLICT
    default_message = "That changed while you were working on it."
    action = "Reload and try again."


class PayloadTooLargeError(ApiError):
    """§13: DXF ≤ 20 MB, images ≤ 10 MB."""

    http_status = 413
    code = CODE_PAYLOAD_TOO_LARGE
    default_message = "That file is too large."
    action = "Upload a smaller file."


class UnsupportedMediaTypeError(ApiError):
    http_status = 415
    code = CODE_UNSUPPORTED_MEDIA_TYPE
    default_message = "We can't read that file type."
    action = "Upload one of the supported formats."


class RateLimitedError(ApiError):
    """429 with a real ``Retry-After`` — never a bare 'slow down'."""

    http_status = 429
    code = CODE_RATE_LIMITED
    default_message = "That's a lot of requests in a short time."
    action = "Wait a few seconds and try again."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int = 1,
        action: str | None = None,
        limit: int | None = None,
        rule: str | None = None,
        code: str | None = None,
    ) -> None:
        retry = max(1, int(retry_after_seconds))
        extra: dict[str, Any] = {"retryAfterSeconds": retry}
        if limit is not None:
            extra["limit"] = limit
        if rule is not None:
            extra["rule"] = rule
        super().__init__(
            message,
            action=action or f"Wait {retry} second{'s' if retry != 1 else ''} and try again.",
            extra=extra,
            headers={"Retry-After": str(retry)},
        )
        self.retry_after_seconds = retry
        # Some call sites want a more specific code (otp_rate_limited) with identical
        # semantics; instance attribute shadows the class attribute for as_problem().
        if code is not None:
            self.code = code  # type: ignore[misc]


# -- 401: authentication -----------------------------------------------------

_BEARER_CHALLENGE: Mapping[str, str] = {"WWW-Authenticate": 'Bearer realm="garh-ai"'}


class AuthenticationError(ApiError):
    """No usable credential was presented."""

    http_status = 401
    code = CODE_UNAUTHENTICATED
    default_message = "You're not signed in."
    action = "Sign in and try again."
    base_headers = _BEARER_CHALLENGE


class TokenExpiredError(AuthenticationError):
    """Access token past ``exp``. Distinct code so the client refreshes silently."""

    code = CODE_TOKEN_EXPIRED
    default_message = "Your session timed out."
    action = "Refreshing your session — if this repeats, sign in again."


class TokenInvalidError(AuthenticationError):
    """Signature, issuer, audience or type check failed."""

    code = CODE_TOKEN_INVALID
    default_message = "That sign-in token isn't valid."
    action = "Sign in again."


class TokenRevokedError(AuthenticationError):
    """Valid signature, but the session was ended (logout-all bumped the generation)."""

    code = CODE_TOKEN_REVOKED
    default_message = "This session was signed out."
    action = "Sign in again."


class RefreshTokenMissingError(AuthenticationError):
    code = CODE_REFRESH_MISSING
    default_message = "There's no session to refresh."
    action = "Sign in again."


class RefreshTokenInvalidError(AuthenticationError):
    code = CODE_REFRESH_INVALID
    default_message = "We couldn't refresh your session."
    action = "Sign in again."


class RefreshTokenRevokedError(AuthenticationError):
    code = CODE_REFRESH_REVOKED
    default_message = "This session was signed out."
    action = "Sign in again."


class RefreshTokenReuseError(AuthenticationError):
    """A refresh token was presented twice.

    Either the token was stolen and the thief got there second, or the legitimate
    client replayed after a network retry. We cannot tell, so we assume theft and kill
    the whole family (§13 refresh rotation). The message stays calm — most occurrences
    are a double-submit, and blaming the user for a possible breach is bad copy.
    """

    code = CODE_REFRESH_REUSED
    default_message = "That session link was already used, so we ended the session."
    action = "Sign in again."


class OtpVerificationError(ApiError):
    """One error for every OTP failure mode.

    Wrong code, expired code, no challenge, and too-many-attempts all render the same
    body. Distinguishing them would tell an attacker whether an address has a live
    challenge and how many guesses remain.
    """

    http_status = 400
    code = CODE_OTP_INVALID
    default_message = "That code didn't work."
    action = "Request a new code and enter it within 10 minutes."


class AccountUnknownError(ApiError):
    """Only used where the caller already proved control of the address."""

    http_status = 404
    code = CODE_ACCOUNT_UNKNOWN
    default_message = "We couldn't find an account for that email."
    action = "Create a firm account, or check the address for typos."


class EmailAlreadyRegisteredError(ApiError):
    """Signup collision.

    Deliberate tradeoff: this *is* an enumeration oracle on the signup route, which is
    why the OTP route has none. A signup form that silently does nothing when the email
    exists strands the user with no idea why. Signup is rate-limited per IP; sign-in is
    not enumerable.
    """

    http_status = 409
    code = CODE_EMAIL_ALREADY_REGISTERED
    default_message = "That email already has an account."
    action = "Sign in instead — we'll email you a code."


class ShareLinkInvalidError(ApiError):
    """Unknown, revoked or expired share token — one answer for all three."""

    http_status = 404
    code = CODE_SHARE_LINK_INVALID
    default_message = "This link isn't available."
    action = "Ask whoever shared it for a fresh link."


class ServiceUnavailableError(ApiError):
    """A dependency we cannot safely work without (Redis for session revocation)."""

    http_status = 503
    code = CODE_SERVICE_UNAVAILABLE
    default_message = "We're having trouble reaching part of the service."
    action = "Try again in a few seconds."

    def __init__(
        self,
        message: str | None = None,
        *,
        dependency: str | None = None,
        retry_after_seconds: int = 5,
        action: str | None = None,
    ) -> None:
        retry = max(1, int(retry_after_seconds))
        super().__init__(
            message,
            action=action,
            extra={"dependency": dependency} if dependency else None,
            headers={"Retry-After": str(retry)},
        )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

#: Status → (code, action) for framework-raised HTTPExceptions we did not author.
_STATUS_FALLBACK: dict[int, tuple[str, str]] = {
    400: (CODE_INVALID_REQUEST, "Check the request format and try again."),
    401: (CODE_UNAUTHENTICATED, "Sign in and try again."),
    403: (CODE_PERMISSION_DENIED, "Ask a firm admin to do this."),
    404: (CODE_NOT_FOUND, "Check the link or go back to your dashboard."),
    405: (CODE_METHOD_NOT_ALLOWED, "This address doesn't accept that method."),
    409: (CODE_CONFLICT, "Reload and try again."),
    413: (CODE_PAYLOAD_TOO_LARGE, "Upload a smaller file."),
    415: (CODE_UNSUPPORTED_MEDIA_TYPE, "Upload one of the supported formats."),
    422: (CODE_VALIDATION_FAILED, "Fix the highlighted fields and try again."),
    429: (CODE_RATE_LIMITED, "Wait a few seconds and try again."),
    503: (CODE_SERVICE_UNAVAILABLE, "Try again in a few seconds."),
}

_GENERIC_MESSAGES: dict[int, str] = {
    404: "We couldn't find that.",
    405: "That isn't something you can do here.",
    500: "Something went wrong on our side.",
}

#: Pydantic puts the rejected value in these keys. Never echo them: a body with an OTP
#: code, a refresh token or a share token would land in the response and the access log.
_UNSAFE_ERROR_KEYS = frozenset({"input", "ctx", "url"})


def _field_path(loc: tuple[Any, ...]) -> str:
    """``('body', 'ops', 0, 'type')`` → ``'ops.0.type'``."""
    parts = [str(part) for part in loc if part not in ("body", "__root__")]
    return ".".join(parts) if parts else "(request)"


def _render_validation_errors(raw: list[Any]) -> list[dict[str, str]]:
    rendered: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        safe = {k: v for k, v in item.items() if k not in _UNSAFE_ERROR_KEYS}
        rendered.append(
            {
                "field": _field_path(tuple(safe.get("loc") or ())),
                "message": str(safe.get("msg") or "This value isn't valid."),
                "code": str(safe.get("type") or "value_error"),
            }
        )
    return rendered


def _is_json_syntax_failure(raw: list[Any]) -> bool:
    """True when the body was not parseable JSON at all (not a field problem)."""
    if not raw:
        return False
    return all(
        isinstance(item, dict) and str(item.get("type", "")).startswith("json_") for item in raw
    )


def install_error_handlers(app: Any) -> None:
    """Register every handler on the FastAPI app. Call once, from ``main.py``.

    ::

        from garh_api.errors import install_error_handlers
        app = FastAPI(...)
        install_error_handlers(app)

    Registration order does not matter — Starlette dispatches on the most specific
    registered class — but the ``Exception`` catch-all must be present or a bug becomes
    an HTML traceback page in production.
    """
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.requests import Request
    from starlette.responses import Response

    def _respond(
        status: int, body: dict[str, Any], headers: Mapping[str, str] | None = None
    ) -> Response:
        return JSONResponse(
            status_code=status,
            content=body,
            media_type=PROBLEM_CONTENT_TYPE,
            headers=dict(headers) if headers else None,
        )

    async def handle_api_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, ApiError):  # pragma: no cover - registration guard
            return await handle_unexpected(request, exc)
        body = exc.as_problem()
        log = _log.bind(
            error_code=body["code"],
            status=exc.http_status,
            http_path=request.url.path,
            http_method=request.method,
        )
        if exc.http_status >= 500:
            log.error("api.error", message=str(exc))
        elif exc.http_status in (401, 403, 429):
            # Security-relevant but expected: INFO keeps them greppable without noise.
            log.info("api.error", message=str(exc))
        else:
            log.info("api.error", message=str(exc))
        return _respond(exc.http_status, body, exc.headers)

    async def handle_tenancy_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, TenancyError):  # pragma: no cover - registration guard
            return await handle_unexpected(request, exc)
        body = exc.as_problem()
        body.setdefault("requestId", current_request_id())
        if body.get("requestId") is None:
            body.pop("requestId", None)
        status = exc.http_status
        log = _log.bind(
            error_code=body.get("code"),
            status=status,
            http_path=request.url.path,
            http_method=request.method,
        )
        if status >= 500:
            # RepositoryUsageError means we wrote a bug; the client gets the generic body
            # the class already defines, but we want the detail in the log.
            log.error("repository.error", message=str(exc), exc_info=exc)
        else:
            log.info("repository.error", message=str(exc))
        return _respond(status, body)

    async def handle_request_validation(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, RequestValidationError):  # pragma: no cover
            return await handle_unexpected(request, exc)
        raw = list(exc.errors())
        if _is_json_syntax_failure(raw):
            return _respond(
                400,
                problem_body(
                    CODE_INVALID_REQUEST,
                    "We couldn't read that request — the body wasn't valid JSON.",
                    "Send a valid JSON body and try again.",
                ),
            )
        errors = _render_validation_errors(raw)
        _log.info(
            "api.validation_failed",
            http_path=request.url.path,
            http_method=request.method,
            fields=[item["field"] for item in errors][:20],
        )
        return _respond(
            422,
            problem_body(
                CODE_VALIDATION_FAILED,
                "Some of those values don't work.",
                "Fix the highlighted fields and try again.",
                extra={"errors": errors},
            ),
        )

    async def handle_pydantic_validation(request: Request, exc: Exception) -> Response:
        """A model validated outside request parsing (a provider response, a fixture)."""
        if not isinstance(exc, ValidationError):  # pragma: no cover
            return await handle_unexpected(request, exc)
        errors = _render_validation_errors(list(exc.errors()))
        _log.error(
            "api.internal_validation_failed",
            http_path=request.url.path,
            fields=[item["field"] for item in errors][:20],
        )
        return _respond(
            422,
            problem_body(
                CODE_VALIDATION_FAILED,
                "Some of those values don't work.",
                "Fix the highlighted fields and try again.",
                extra={"errors": errors},
            ),
        )

    async def handle_http_exception(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
            return await handle_unexpected(request, exc)
        status = int(exc.status_code)
        code, action = _STATUS_FALLBACK.get(status, (CODE_INTERNAL, ApiError.action))
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail else None
        message = _GENERIC_MESSAGES.get(status) or detail or "That request didn't work."
        if status >= 500:
            # Never surface framework detail on a 5xx; it can carry internals.
            message = _GENERIC_MESSAGES[500]
        _log.info(
            "api.http_exception",
            status=status,
            error_code=code,
            http_path=request.url.path,
            http_method=request.method,
        )
        return _respond(status, problem_body(code, message, action), exc.headers)

    async def handle_unexpected(request: Request, exc: Exception) -> Response:
        """The last line of defence. Logs everything, tells the client nothing."""
        request_id = current_request_id()
        _log.error(
            "api.unhandled_exception",
            exc_info=exc,
            http_path=request.url.path,
            http_method=request.method,
            error_type=type(exc).__name__,
        )
        body = problem_body(
            CODE_INTERNAL,
            _GENERIC_MESSAGES[500],
            (
                f"Try again in a moment. If it keeps happening, quote reference {request_id}."
                if request_id
                else "Try again in a moment. If it keeps happening, contact support."
            ),
        )
        return _respond(500, body)

    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(TenancyError, handle_tenancy_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation)
    app.add_exception_handler(ValidationError, handle_pydantic_validation)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected)


__all__ = [
    "ERROR_CODES",
    "PROBLEM_CONTENT_TYPE",
    "PROBLEM_RESPONSES",
    "AccountUnknownError",
    "ApiError",
    "AuthenticationError",
    "CODE_ACCOUNT_UNKNOWN",
    "CODE_CONFLICT",
    "CODE_EMAIL_ALREADY_REGISTERED",
    "CODE_INTERNAL",
    "CODE_INVALID_CURSOR",
    "CODE_INVALID_REQUEST",
    "CODE_METHOD_NOT_ALLOWED",
    "CODE_NOT_FOUND",
    "CODE_OP_SEQUENCE_CONFLICT",
    "CODE_OTP_INVALID",
    "CODE_OTP_RATE_LIMITED",
    "CODE_PAYLOAD_TOO_LARGE",
    "CODE_PERMISSION_DENIED",
    "CODE_RATE_LIMITED",
    "CODE_REFRESH_INVALID",
    "CODE_REFRESH_MISSING",
    "CODE_REFRESH_REUSED",
    "CODE_REFRESH_REVOKED",
    "CODE_SERVICE_UNAVAILABLE",
    "CODE_SHARE_LINK_INVALID",
    "CODE_TENANT_CONTEXT_REQUIRED",
    "CODE_TOKEN_EXPIRED",
    "CODE_TOKEN_INVALID",
    "CODE_TOKEN_REVOKED",
    "CODE_UNAUTHENTICATED",
    "CODE_UNSUPPORTED_MEDIA_TYPE",
    "CODE_VALIDATION_FAILED",
    "ConflictError",
    "EmailAlreadyRegisteredError",
    "FieldErrorModel",
    "InvalidRequestError",
    "OtpVerificationError",
    "PayloadTooLargeError",
    "ProblemModel",
    "RateLimitedError",
    "RefreshTokenInvalidError",
    "RefreshTokenMissingError",
    "RefreshTokenReuseError",
    "RefreshTokenRevokedError",
    "ServiceUnavailableError",
    "ShareLinkInvalidError",
    "TokenExpiredError",
    "TokenInvalidError",
    "TokenRevokedError",
    "UnsupportedMediaTypeError",
    "ValidationFailedError",
    "install_error_handlers",
    "problem_body",
]
