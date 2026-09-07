"""Request/response models for the practice surface: firm profile, members, invites.

Same conventions as :mod:`garh_api.schemas.auth` (strict, camelCase, ``extra="forbid"``
on requests). Responses omit ``firmId`` as every other response model does.

The practice profile — address, GSTIN, registration, phone — is stored under
``firms.settings["practice"]``. It is firm identity, not a drawing preference: the
title-block template the sheets print lives beside it under ``settings["drawings"]``
and is edited through ``PUT /firm/drawing-preferences`` (routers/sheets.py), which
the Practice page calls for exactly those fields. The two are kept apart so a title
block edit cannot clobber a GSTIN and vice versa.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from garh_api.billing.gst import GstError, validate_gstin
from garh_api.repositories.domain import FirmInvite, User
from garh_api.schemas.auth import AuthModel, Email, PersonName, _validate_email

Role = Literal["admin", "member"]
SeatType = Literal["editor", "viewer"]

ShortText = Annotated[str, StringConstraints(max_length=120)]
AddressText = Annotated[str, StringConstraints(max_length=400)]
PhoneText = Annotated[str, StringConstraints(max_length=24)]

#: Where the practice profile lives inside ``firms.settings``.
PRACTICE_SETTINGS_KEY = "practice"


# ---------------------------------------------------------------------------
# Firm profile
# ---------------------------------------------------------------------------


class PracticeProfile(AuthModel):
    """The practice's own identity, as it appears on letterheads and invoices.

    Every field is optional and blank by default: nothing here is invented, and a
    field the admin has not filled prints as nothing, not as a placeholder.
    """

    address: AddressText = Field(default="", description="Postal address, multi-line.")
    gstin: ShortText = Field(default="", description="15-character GSTIN, or blank.")
    registration_number: ShortText = Field(
        default="",
        description="Council of Architecture / licence number the PRACTICE holds. The "
        "architect of record's own CoA number is on their profile.",
    )
    phone: PhoneText = Field(default="", description="Practice contact number.")

    @field_validator("gstin")
    @classmethod
    def _check_gstin(cls, value: str) -> str:
        if not value.strip():
            return ""
        try:
            return validate_gstin(value)
        except GstError as exc:
            raise ValueError(str(exc)) from exc

    @classmethod
    def from_settings(cls, settings: dict[str, object]) -> PracticeProfile:
        raw = settings.get(PRACTICE_SETTINGS_KEY)
        if not isinstance(raw, dict):
            return cls()
        clean = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
        # Stored values were validated on the way in; a GSTIN that later fails the
        # checksum (a hand edit) must still render rather than 500 the settings page.
        try:
            return cls.model_validate(clean)
        except ValueError:
            return cls(
                address=clean.get("address", ""),
                registration_number=clean.get(
                    "registration_number", clean.get("registrationNumber", "")
                ),
                phone=clean.get("phone", ""),
            )


class FirmProfileOut(AuthModel):
    id: uuid.UUID
    name: str
    logo_url: str | None = None
    practice: PracticeProfile


class PracticeProfilePatch(AuthModel):
    """Any subset of :class:`PracticeProfile`. An explicit empty string clears."""

    address: AddressText | None = None
    gstin: ShortText | None = None
    registration_number: ShortText | None = None
    phone: PhoneText | None = None

    @field_validator("gstin")
    @classmethod
    def _check_gstin(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        try:
            return validate_gstin(value)
        except GstError as exc:
            raise ValueError(str(exc)) from exc


class FirmProfilePatch(AuthModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None = None
    practice: PracticeProfilePatch | None = None


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


class SeatSummaryOut(AuthModel):
    id: uuid.UUID
    seat_type: str


class MemberOut(AuthModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    coa_number: str | None = None
    seat: SeatSummaryOut | None = None
    last_sign_in_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_user(cls, user: User, *, seat: SeatSummaryOut | None) -> MemberOut:
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            coa_number=user.coa_number,
            seat=seat,
            last_sign_in_at=user.last_sign_in_at,
            created_at=user.created_at,
        )


class MembersOut(AuthModel):
    items: list[MemberOut]
    count: int
    admins: int = Field(description="How many admins the firm has — 1 means nobody is demotable.")


class MemberRolePatch(AuthModel):
    role: Role


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


class InviteCreateIn(AuthModel):
    email: Email
    name: PersonName = Field(description="The colleague's name; they can change it later.")
    role: Role = "member"
    seat_type: SeatType = Field(
        default="editor",
        description="``editor`` counts against the plan's paid seats; ``viewer`` is free.",
    )

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return _validate_email(value)


class InviteOut(AuthModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    seat_type: str
    status: str = Field(description="``pending`` | ``expired`` (open ones only are listed).")
    invited_by: uuid.UUID | None = None
    invited_by_name: str | None = None
    expires_at: datetime
    last_sent_at: datetime
    send_count: int
    created_at: datetime
    url: str | None = Field(
        default=None,
        description="The invite link. Present ONCE, on create and resend — the same "
        "link the email carries, for pasting into WhatsApp. Never in a list.",
    )

    @classmethod
    def from_invite(
        cls,
        invite: FirmInvite,
        *,
        invited_by_name: str | None,
        url: str | None = None,
    ) -> InviteOut:
        return cls(
            id=invite.id,
            email=invite.email,
            name=invite.name,
            role=invite.role,
            seat_type=invite.seat_type,
            status=invite.status(),
            invited_by=invite.invited_by,
            invited_by_name=invited_by_name,
            expires_at=invite.expires_at,
            last_sent_at=invite.last_sent_at,
            send_count=invite.send_count,
            created_at=invite.created_at,
            url=url,
        )


class InvitesOut(AuthModel):
    items: list[InviteOut]
    count: int


class InviteStatusOut(AuthModel):
    """``GET /auth/invites/{token}`` — what the sign-in screen shows the link holder.

    Honest in every state: ``pending`` pre-fills the address and names who is asking;
    ``expired`` and ``revoked`` say so and name whom to ask; ``accepted`` says the
    seat is already taken up. None of it says anything about accounts elsewhere.
    """

    status: str
    firm_name: str
    invited_by_name: str | None = None
    role: str
    email: str
    expires_at: datetime


__all__ = [
    "PRACTICE_SETTINGS_KEY",
    "FirmProfileOut",
    "FirmProfilePatch",
    "InviteCreateIn",
    "InviteOut",
    "InviteStatusOut",
    "InvitesOut",
    "MemberOut",
    "MemberRolePatch",
    "MembersOut",
    "PracticeProfile",
    "PracticeProfilePatch",
    "SeatSummaryOut",
]
