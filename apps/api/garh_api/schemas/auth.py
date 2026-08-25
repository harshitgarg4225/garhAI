"""Request/response models for the auth routes (playbook §11, §13 "Pydantic strict").

Conventions this file establishes for every schema module that follows it:

* **camelCase on the wire, snake_case in Python.** ``alias_generator=to_camel`` plus
  ``populate_by_name=True``, so ``{"firmName": ...}`` and ``{"firm_name": ...}`` both
  parse and responses always serialise camel. That matches the TypeScript model core,
  which is camelCase throughout.
* **``strict=True`` and ``extra="forbid"``.** No silent coercion (``"5"`` is not ``5``)
  and no silently-ignored fields — a typo'd key is a 422 with the field named, not a
  setting that mysteriously did nothing.
* **No ``EmailStr``.** ``pydantic[email]`` is not in the pinned dependency set and the
  licence gate means we do not add one for a regex. :data:`Email` below is the shared
  constrained type; it is deliberately permissive about the local part (real addresses
  are stranger than most regexes allow) and strict about overall shape and length.

Nothing here contains a secret. Note in particular that no response model carries the
refresh token: it only ever travels in the ``HttpOnly`` cookie set by
:func:`garh_api.security.set_refresh_cookie`, so JavaScript cannot read it.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Final

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)
from pydantic.alias_generators import to_camel

from garh_api.repositories.domain import AuthPrincipal, User

# ---------------------------------------------------------------------------
# Shared field types
# ---------------------------------------------------------------------------

#: RFC 5321's practical ceiling.
MAX_EMAIL_LENGTH: Final = 254

_EMAIL_RE: Final = re.compile(
    r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

#: Whitespace plus every dash a mail client or a phone keyboard might produce
#: (hyphen-minus, the U+2010–U+2015 dash block, and the maths minus sign). Spelled with
#: escapes rather than literal characters so the intent survives a copy-paste.
_CODE_NOISE_RE: Final = re.compile(r"[\s\u002d\u2010-\u2015\u2212]")


def _normalise_email(value: Any) -> Any:
    """Lowercase and trim before validation, mirroring ``users.normalise_email``.

    The DB has a ``email = lower(email)`` CHECK, so this is not cosmetic: without it a
    user who types ``Asha@Studio.in`` would fail at insert time with a 500 instead of
    signing in.
    """
    if isinstance(value, str):
        return value.strip().lower()
    return value


def _validate_email(value: str) -> str:
    if not _EMAIL_RE.match(value):
        raise ValueError("Enter an email address like name@studio.in")
    return value


def _clean_code(value: Any) -> Any:
    """Accept what people actually paste: ``123 456``, ``123-456``, ``  123456``."""
    if isinstance(value, str):
        return _CODE_NOISE_RE.sub("", value)
    return value


Email = Annotated[
    str,
    BeforeValidator(_normalise_email),
    StringConstraints(min_length=6, max_length=MAX_EMAIL_LENGTH),
]

#: 4–10 digits — the window :attr:`Settings.otp_code_length` is allowed to span. The
#: exact length is not asserted here on purpose: a "wrong length" 422 would be a
#: different response from a "wrong code" 400 and therefore an oracle.
OtpCode = Annotated[
    str,
    BeforeValidator(_clean_code),
    StringConstraints(min_length=4, max_length=10, pattern=r"^[0-9]+$"),
]

PersonName = Annotated[str, StringConstraints(min_length=1, max_length=120)]
FirmName = Annotated[str, StringConstraints(min_length=1, max_length=160)]
#: Council of Architecture registration, e.g. ``CA/2011/52345``. Printed on sheets.
CoaNumber = Annotated[str, StringConstraints(min_length=3, max_length=40)]


class AuthModel(BaseModel):
    """Base config for every model in this module."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        frozen=True,
    )


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class OtpRequest(AuthModel):
    """``POST /auth/otp`` — "email me a sign-in code"."""

    email: Email = Field(description="Where to send the code.")

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return _validate_email(value)


class VerifyRequest(AuthModel):
    """``POST /auth/verify`` — exchange the code for a session."""

    email: Email
    code: OtpCode = Field(description="The code from the email.")

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return _validate_email(value)


class SignupRequest(AuthModel):
    """``POST /auth/signup`` — create a firm and its first admin.

    Returns a code to verify, not a session: a new user still has to prove they own
    the address, so signup ends exactly where sign-in does.
    """

    firm_name: FirmName = Field(description="Practice name, as it appears on drawings.")
    name: PersonName = Field(description="Your name.")
    email: Email
    coa_number: CoaNumber | None = Field(
        default=None,
        description="Council of Architecture registration number. Optional; it can be "
        "added later in firm settings, but municipal sheets need it.",
    )

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return _validate_email(value)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class OtpIssuedResponse(AuthModel):
    """Deliberately says nothing about whether the address exists.

    ``sent`` is always ``true``: it means "we have finished handling your request",
    not "an email left the building". Reporting the difference would turn this
    endpoint into a customer directory.
    """

    sent: bool = Field(default=True, description="Always true — see the class docstring.")
    expires_in_seconds: int = Field(description="How long the code stays valid (600).")
    resend_after_seconds: int = Field(
        description="Seconds before another code can be requested — count this down "
        "on the resend button instead of letting the user hit a 429."
    )
    dev_code: str | None = Field(
        default=None,
        description="The code itself. Present **only** when the API runs with "
        "APP_ENV=dev/test and DEV_ECHO_OTP enabled, so a fresh clone is usable with no "
        "mail provider. Always null in staging and production.",
    )


class UserProfile(AuthModel):
    """The signed-in person. No tokens, no internal ids beyond the uuid."""

    id: uuid.UUID
    email: str
    name: str
    role: str = Field(description="``admin`` or ``member``.")
    coa_number: str | None = None

    # Built from the ``users`` row rather than from ``AuthPrincipal``: the principal
    # is the pre-auth projection and carries no ``coa_number``, so a
    # ``from_principal`` shortcut would quietly return a different shape on sign-in
    # than on ``GET /auth/me``. One constructor, one shape.
    @classmethod
    def from_user(cls, user: User) -> UserProfile:
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            coa_number=user.coa_number,
        )


class FirmSummary(AuthModel):
    """Just enough firm identity for the app shell to render."""

    id: uuid.UUID
    name: str

    @classmethod
    def from_principal(cls, principal: AuthPrincipal) -> FirmSummary:
        return cls(id=principal.firm_id, name=principal.firm_name)


class SessionResponse(AuthModel):
    """A signed-in session.

    The refresh token is **not** in this body — it is in the ``HttpOnly``,
    ``SameSite=Lax``, path-scoped cookie the same response sets. Keeping it out of
    JavaScript's reach is the entire point of the cookie.
    """

    access_token: str = Field(description="Send as ``Authorization: Bearer <token>``.")
    token_type: str = Field(default="Bearer")
    expires_in: int = Field(description="Seconds until the access token expires (900).")
    expires_at: int = Field(description="Absolute expiry, epoch seconds — survives clock drift.")
    user: UserProfile
    firm: FirmSummary


class MeResponse(AuthModel):
    """``GET /auth/me`` — who the current token belongs to."""

    user: UserProfile
    firm: FirmSummary


class LogoutResponse(AuthModel):
    """``POST /auth/logout`` and ``/auth/logout-all``. Always succeeds."""

    signed_out: bool = Field(default=True)
    sessions_ended: int = Field(
        default=0,
        description="Refresh-token families revoked. 1 for a normal logout, N for "
        "logout-all, 0 if there was nothing left to end.",
    )


__all__ = [
    "MAX_EMAIL_LENGTH",
    "AuthModel",
    "CoaNumber",
    "Email",
    "FirmName",
    "FirmSummary",
    "LogoutResponse",
    "MeResponse",
    "OtpCode",
    "OtpIssuedResponse",
    "OtpRequest",
    "PersonName",
    "SessionResponse",
    "SignupRequest",
    "UserProfile",
    "VerifyRequest",
]
