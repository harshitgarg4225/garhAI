"""F-4: TOTP two-factor — enrolment, the sign-in gate, and the way back in.

Three groups, in order of how badly they can hurt someone:

1. **The algorithm** (:func:`test_rfc6238_test_vectors`). If the codes this server
   computes are not the codes Google Authenticator computes, nobody can ever enrol.
   That is asserted against RFC 6238's own published vectors rather than against our
   own output, because a test that checks an implementation against itself passes
   while the implementation is wrong.

2. **The gate** (:func:`test_sign_in_stops_at_the_second_factor`). The reason this
   feature exists is that ``dev_echo_otp`` hands the sign-in code back in the response
   body on this deployment, so knowing an email is enough to sign in as that person.
   The gate must fire: ``POST /auth/verify`` must return **no session and no refresh
   cookie** once a factor is enrolled. Every other test here is worth less than that
   one, and it is negative-tested — see the module note at the bottom.

3. **Recovery** (everything from ``test_a_recovery_code_signs_you_in`` down). Getting
   two-factor wrong locks people out of their own practice permanently, so the
   lost-phone path gets the most cases: sign in with a recovery code, spend it once
   and only once, use one to *turn the factor off*, and rotate the set.

Codes are computed here with :func:`garh_api.twofactor.totp_at` from the secret the
enrolment endpoint returned — the same way a phone would, from the same secret.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import pytest
from garh_api.config import Settings
from garh_api.errors import RateLimitedError
from garh_api.ratelimit import peek_rate_limit
from garh_api.repositories.two_factor import TwoFactorRepository
from garh_api.security import (
    _AUDIENCE_BY_TYPE,
    AUDIENCE_ACCESS,
    AUDIENCE_TWO_FACTOR,
    JWT_ALGORITHM,
    REFRESH_COOKIE_NAME,
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_TWO_FACTOR,
    create_two_factor_challenge,
    decode_token,
    get_jwt_keys,
)
from garh_api.twofactor import (
    RECOVERY_CODE_COUNT,
    TOTP_STEP_SECONDS,
    TWO_FACTOR_MAX_ATTEMPTS,
    TwoFactorInvalidError,
    TwoFactorService,
    counter_at,
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    match_recovery_code,
    normalise_recovery_code,
    otpauth_uri,
    totp_at,
    two_factor_attempt_rule,
    verify_totp,
)

from tests.helpers import problem
from tests.test_sessions import clear_email_budget, sign_in

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def code_now(secret: str, *, offset_steps: int = 0) -> str:
    """The code a phone holding ``secret`` would be showing right now."""
    return totp_at(secret, counter_at() + offset_steps)


async def enrol(client: Any, api: str, headers: dict[str, str]) -> dict[str, Any]:
    """Walk enrol → activate and return the secret plus the recovery codes."""
    started = await client.post("%s/auth/2fa/enrol" % api, headers=headers)
    assert started.status_code == 201, started.text
    secret = started.json()["secret"]

    activated = await client.post(
        "%s/auth/2fa/activate" % api, json={"code": code_now(secret)}, headers=headers
    )
    assert activated.status_code == 200, activated.text
    return {"secret": secret, "recovery_codes": activated.json()["recoveryCodes"]}


# ---------------------------------------------------------------------------
# 1. The algorithm
# ---------------------------------------------------------------------------

#: RFC 6238 Appendix B, the SHA-1 rows. The seed is the ASCII string
#: "12345678901234567890"; base32 of it is what an authenticator would be given.
_RFC6238_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
_RFC6238_VECTORS = (
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
)


@pytest.mark.parametrize(("unix_time", "expected"), _RFC6238_VECTORS)
def test_rfc6238_test_vectors(unix_time: int, expected: str) -> None:
    """Our TOTP is *the* TOTP, checked against the RFC's own numbers.

    This is the only assertion in the file that can catch "the maths is subtly wrong
    but self-consistent" — the failure mode where every test that generates its own
    code passes and no real authenticator app ever works.
    """
    assert totp_at(_RFC6238_SECRET, counter_at(unix_time), digits=8) == expected


def test_a_secret_is_32_unpadded_base32_characters() -> None:
    """160 bits, and no ``=`` — several apps mishandle padding inside an otpauth URI."""
    secret = generate_secret()
    assert len(secret) == 32 and "=" not in secret
    assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def test_drift_of_one_step_is_accepted_and_two_is_not() -> None:
    """±30s covers a phone with a slightly wrong clock. ±60s does not.

    The window is a direct trade against brute force — every extra step multiplies the
    codes that are live at any moment — so it is pinned rather than left implicit.
    """
    secret = generate_secret()
    now = counter_at()
    for offset in (-1, 0, 1):
        assert verify_totp(secret, totp_at(secret, now + offset)) is not None, offset
    for offset in (-2, 2):
        assert verify_totp(secret, totp_at(secret, now + offset)) is None, offset


def test_a_spent_code_cannot_be_spent_again() -> None:
    """The replay guard, at the unit level.

    A TOTP code is valid for its whole step. Without ``last_counter`` a code read over
    a shoulder — or out of a proxy log, or off a phishing page — works a second time
    for up to 90 seconds.
    """
    secret = generate_secret()
    step = counter_at()
    code = totp_at(secret, step)

    first = verify_totp(secret, code)
    assert first == step
    assert verify_totp(secret, code, last_counter=first) is None
    # And the *previous* step is dead too: it is inside the drift window but below the
    # high-water mark, so replaying it is the same attack one step earlier.
    assert verify_totp(secret, totp_at(secret, step - 1), last_counter=first) is None


def test_codes_are_read_the_way_people_type_them() -> None:
    secret = generate_secret()
    code = totp_at(secret, counter_at())
    spaced = "%s %s" % (code[:3], code[3:])
    assert verify_totp(secret, spaced) is not None
    assert verify_totp(secret, "  %s  " % code) is not None
    assert verify_totp(secret, code[:5]) is None
    assert verify_totp(secret, "abcdef") is None


def test_otpauth_uri_names_the_issuer_twice() -> None:
    """Both in the label and as a parameter — the Key Uri Format asks for both, and it
    is what makes the entry read "Garh AI (asha@studio.in)" in a list of twenty."""
    uri = otpauth_uri("JBSWY3DPEHPK3PXP", account="asha@studio.in")
    assert uri.startswith("otpauth://totp/Garh%20AI%3Aasha%40studio.in?")
    assert "issuer=Garh+AI" in uri
    assert "digits=6" in uri and "period=30" in uri
    assert "secret=JBSWY3DPEHPK3PXP" in uri


# ---------------------------------------------------------------------------
# Recovery codes, as values
# ---------------------------------------------------------------------------


def test_recovery_codes_are_unique_high_entropy_and_readable() -> None:
    codes = generate_recovery_codes()
    assert len(codes) == RECOVERY_CODE_COUNT
    assert len(set(codes)) == RECOVERY_CODE_COUNT
    for code in codes:
        assert len(code) == 19 and code.count("-") == 3, code
        assert len(normalise_recovery_code(code)) == 16, code


def test_a_recovery_code_matches_however_it_is_typed() -> None:
    codes = generate_recovery_codes(3)
    hashes = [hash_recovery_code(code) for code in codes]
    typed = codes[1].lower().replace("-", " ")
    assert match_recovery_code(typed, hashes) == hash_recovery_code(codes[1])
    assert match_recovery_code(codes[1].replace("-", ""), hashes) == hashes[1]


def test_a_wrong_recovery_code_matches_nothing() -> None:
    hashes = [hash_recovery_code(code) for code in generate_recovery_codes(3)]
    assert match_recovery_code("AAAA-BBBB-CCCC-DDDD", hashes) is None
    assert match_recovery_code("", hashes) is None
    assert match_recovery_code("---", hashes) is None


# ---------------------------------------------------------------------------
# 2. Enrolment
# ---------------------------------------------------------------------------


async def test_enrol_then_activate_turns_the_factor_on(client: Any, api: str, firm_a: Any) -> None:
    """Enrolling stages a secret; only a live code turns the factor on."""
    before = await client.get("%s/auth/2fa" % api, headers=firm_a.headers)
    assert before.json() == {
        "enabled": False,
        "pending": False,
        "confirmedAt": None,
        "recoveryCodesRemaining": 0,
    }

    started = await client.post("%s/auth/2fa/enrol" % api, headers=firm_a.headers)
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["digits"] == 6 and body["periodSeconds"] == TOTP_STEP_SECONDS
    assert body["otpauthUri"].startswith("otpauth://totp/")
    assert firm_a.email in body["otpauthUri"].replace("%40", "@")

    # Staged but not proved: sign-in is still single-factor at this point, which is
    # what stops a mis-scanned QR code from locking someone out of their own firm.
    pending = await client.get("%s/auth/2fa" % api, headers=firm_a.headers)
    assert pending.json()["enabled"] is False
    assert pending.json()["pending"] is True

    activated = await client.post(
        "%s/auth/2fa/activate" % api,
        json={"code": code_now(body["secret"])},
        headers=firm_a.headers,
    )
    assert activated.status_code == 200, activated.text
    assert len(activated.json()["recoveryCodes"]) == RECOVERY_CODE_COUNT

    after = await client.get("%s/auth/2fa" % api, headers=firm_a.headers)
    assert after.json()["enabled"] is True
    assert after.json()["pending"] is False
    assert after.json()["recoveryCodesRemaining"] == RECOVERY_CODE_COUNT
    assert after.json()["confirmedAt"]


async def test_activating_with_a_wrong_code_leaves_the_factor_off(
    client: Any, api: str, firm_a: Any
) -> None:
    """A failed activation must not half-enable anything.

    Half-enabled is the lock-out shape: the account demands a code that the user's app
    cannot produce, because the app never held the secret.
    """
    await client.post("%s/auth/2fa/enrol" % api, headers=firm_a.headers)
    response = await client.post(
        "%s/auth/2fa/activate" % api, json={"code": "000000"}, headers=firm_a.headers
    )
    assert response.status_code == 400, response.text
    assert problem(response)["code"] == "two_factor_invalid"

    status = await client.get("%s/auth/2fa" % api, headers=firm_a.headers)
    assert status.json()["enabled"] is False
    assert status.json()["recoveryCodesRemaining"] == 0

    signed_in = await sign_in(client, api, firm_a.email, user_agent="curl/8.4.0", ip="203.0.113.10")
    assert signed_in["access"], "a failed activation locked the account"


async def test_re_enrolling_replaces_an_unproved_secret(client: Any, api: str, firm_a: Any) -> None:
    """ "The QR code wouldn't scan, give me another" must work, and the first secret
    must stop working — two live secrets would both verify forever."""
    first = (await client.post("%s/auth/2fa/enrol" % api, headers=firm_a.headers)).json()
    second = (await client.post("%s/auth/2fa/enrol" % api, headers=firm_a.headers)).json()
    assert first["secret"] != second["secret"]

    stale = await client.post(
        "%s/auth/2fa/activate" % api,
        json={"code": code_now(first["secret"])},
        headers=firm_a.headers,
    )
    assert stale.status_code == 400, stale.text

    fresh = await client.post(
        "%s/auth/2fa/activate" % api,
        json={"code": code_now(second["secret"])},
        headers=firm_a.headers,
    )
    assert fresh.status_code == 200, fresh.text


async def test_enrolling_again_once_it_is_live_is_refused(
    client: Any, api: str, firm_a: Any
) -> None:
    """Replacing a *confirmed* secret by pressing "enrol" would be a silent lock-out."""
    await enrol(client, api, firm_a.headers)
    response = await client.post("%s/auth/2fa/enrol" % api, headers=firm_a.headers)
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "two_factor_state"


# ---------------------------------------------------------------------------
# 3. The sign-in gate — the reason this feature exists
# ---------------------------------------------------------------------------


async def test_sign_in_stops_at_the_second_factor(client: Any, api: str, firm_a: Any) -> None:
    """Knowing the emailed code is no longer enough.

    This is the whole point of F-4 on this deployment: ``POST /auth/otp`` echoes the
    code in its own response body, so the first factor is effectively public. After
    enrolment, ``POST /auth/verify`` must hand back **no access token and no refresh
    cookie** — only a challenge.
    """
    await enrol(client, api, firm_a.headers)
    client.cookies.clear()

    issued = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    code = issued.json()["devCode"]
    response = await client.post("%s/auth/verify" % api, json={"email": firm_a.email, "code": code})

    assert response.status_code == 403, response.text
    body = problem(response)
    assert body["code"] == "two_factor_required", body
    assert isinstance(body["challenge"], str) and body["challenge"]
    assert body["expiresInSeconds"] == 300

    assert "accessToken" not in body, body
    assert REFRESH_COOKIE_NAME not in response.cookies, "a session cookie leaked past the gate"
    assert not client.cookies.get(REFRESH_COOKIE_NAME), "the gate still set a refresh cookie"


def _claims_of(token: str) -> dict[str, Any]:
    """The payload, unverified — a test reading what was actually minted."""
    return dict(
        jwt.decode(
            token,
            options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
            algorithms=[JWT_ALGORITHM],
        )
    )


def _mint(claims: dict[str, Any], settings: Settings) -> str:
    """Sign arbitrary claims with the API's own key.

    Needed because the interesting attacker is not one who forges a signature — they
    cannot — but one holding a *legitimately signed* token of the wrong kind. That is
    exactly what a 2FA challenge is, and the only thing standing between it and a
    session is the audience.
    """
    return str(jwt.encode(claims, get_jwt_keys(settings).private_pem, algorithm=JWT_ALGORITHM))


async def test_the_challenge_is_not_a_bearer_token(
    client: Any, api: str, firm_a: Any, settings: Settings
) -> None:
    """A challenge presented as ``Authorization: Bearer`` must be rejected **by its
    audience**, which is what this test's name has always claimed and what it did not
    used to check.

    The old version asserted only that ``GET /auth/me`` answered 401. It does — but on
    the ``typ`` belt, not the audience brace: a reviewer collapsed
    ``_AUDIENCE_BY_TYPE[2fa]`` onto the access audience and minted the challenge with
    it, and the test stayed green while the separation it is named for was gone. So it
    now asserts three things the regression cannot survive:

    1. the minted challenge really does carry ``garh-api/2fa``;
    2. a token that satisfies ``typ`` and differs *only* in audience is still refused;
    3. the same claims with the access audience are accepted — so (2) is the audience
       doing the work and not some other check.
    """
    await enrol(client, api, firm_a.headers)
    challenge = await _gate_and_challenge(client, api, firm_a.email)

    claims = _claims_of(challenge)
    assert claims["typ"] == TOKEN_TYPE_TWO_FACTOR
    assert claims["aud"] == AUDIENCE_TWO_FACTOR, (
        "the challenge was minted with %r — as a bearer credential this is now a "
        "working session ticket" % claims["aud"]
    )
    assert AUDIENCE_TWO_FACTOR != AUDIENCE_ACCESS
    assert _AUDIENCE_BY_TYPE[TOKEN_TYPE_TWO_FACTOR] == AUDIENCE_TWO_FACTOR

    response = await client.get(
        "%s/auth/me" % api, headers={"Authorization": "Bearer %s" % challenge}
    )
    assert response.status_code == 401, response.text
    assert problem(response)["code"] == "token_invalid"

    # The belt is satisfied and the brace is not: same signature, same subject, same
    # expiry, ``typ`` says "access". Only the audience is wrong.
    forged = _mint({**claims, "typ": TOKEN_TYPE_ACCESS}, settings)
    refused = await client.get("%s/auth/me" % api, headers={"Authorization": "Bearer %s" % forged})
    assert refused.status_code == 401, (
        "a 2FA-audience token was accepted as a bearer credential once it claimed "
        "typ=access: %s" % refused.text
    )
    assert problem(refused)["code"] == "token_invalid"

    # Positive control: identical claims, access audience. If this did not pass, the
    # 401 above would prove nothing about audiences.
    accepted = _mint({**claims, "typ": TOKEN_TYPE_ACCESS, "aud": AUDIENCE_ACCESS}, settings)
    allowed = await client.get(
        "%s/auth/me" % api, headers={"Authorization": "Bearer %s" % accepted}
    )
    assert allowed.status_code == 200, allowed.text


async def test_a_live_code_completes_the_sign_in(client: Any, api: str, firm_a: Any) -> None:
    """Challenge + code → exactly the session ``/auth/verify`` would have issued."""
    enrolment = await enrol(client, api, firm_a.headers)
    challenge = await _gate_and_challenge(client, api, firm_a.email)

    # A step ahead of the one activation just spent: the replay guard is doing its job,
    # and re-using the same 30-second window would (correctly) be refused.
    response = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": challenge, "code": code_now(enrolment["secret"], offset_steps=1)},
    )
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["user"]["email"] == firm_a.email
    assert session["expiresIn"] == 900
    assert response.cookies.get(REFRESH_COOKIE_NAME), "no refresh cookie after the second factor"

    me = await client.get(
        "%s/auth/me" % api, headers={"Authorization": "Bearer %s" % session["accessToken"]}
    )
    assert me.status_code == 200, me.text


async def test_a_wrong_second_factor_issues_nothing(client: Any, api: str, firm_a: Any) -> None:
    await enrol(client, api, firm_a.headers)
    challenge = await _gate_and_challenge(client, api, firm_a.email)

    response = await client.post(
        "%s/auth/2fa/verify" % api, json={"challenge": challenge, "code": "000000"}
    )
    assert response.status_code == 400, response.text
    assert problem(response)["code"] == "two_factor_invalid"
    assert "accessToken" not in response.json()
    assert not client.cookies.get(REFRESH_COOKIE_NAME)


@pytest.mark.parametrize("mangle", ["tampered-signature", "not-a-jwt-at-all-0123456789"])
async def test_an_unusable_challenge_says_start_again(
    client: Any, api: str, firm_a: Any, mangle: str
) -> None:
    """A challenge that will not decode gets its own message, on purpose.

    Distinct from "wrong code" because the client cannot recover by retrying — it has
    to restart sign-in — and saying so leaks nothing about the account. A tampered
    signature and a string that is not a JWT answer identically.
    """
    enrolment = await enrol(client, api, firm_a.headers)
    challenge = await _gate_and_challenge(client, api, firm_a.email)
    broken = challenge + mangle if mangle.startswith("tampered") else mangle

    response = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": broken, "code": code_now(enrolment["secret"], offset_steps=1)},
    )
    assert response.status_code == 400, response.text
    body = problem(response)
    assert body["code"] == "two_factor_invalid"
    assert "expired" in body["message"].lower()
    assert "new sign-in code" in body["action"].lower()


def test_a_challenge_carries_no_session_authority() -> None:
    """Decoded on its own it is an identity claim with generation 0 and nothing else.

    If it ever grew a ``fam`` claim or a real generation it would start to look like a
    session, and the 403 body that carries it would become a credential leak.
    """
    token, expires = create_two_factor_challenge(
        user_id=uuid.uuid4(), firm_id=uuid.uuid4(), role="admin", ttl_seconds=300
    )
    claims = decode_token(token, expected_type=TOKEN_TYPE_TWO_FACTOR)
    assert claims.token_type == TOKEN_TYPE_TWO_FACTOR
    assert claims.generation == 0
    assert claims.family is None
    assert expires - int(time.time()) <= 300


# ---------------------------------------------------------------------------
# 4. Recovery — the path that decides whether people get locked out
# ---------------------------------------------------------------------------


async def _gate_and_challenge(client: Any, api: str, email: str) -> str:
    """First factor, then stop. Returns the challenge from the 403.

    The per-address resend cooldown is cleared first: a test that walks the gate three
    times is not a mail cannon, and ``reset_rate_limit`` is the supported way to say
    so without mocking the limiter that the brute-force tests rely on.
    """
    await clear_email_budget(email)
    client.cookies.clear()
    issued = await client.post("%s/auth/otp" % api, json={"email": email})
    gated = await client.post(
        "%s/auth/verify" % api, json={"email": email, "code": issued.json()["devCode"]}
    )
    assert gated.status_code == 403, gated.text
    return str(problem(gated)["challenge"])


async def test_a_recovery_code_signs_you_in(client: Any, api: str, firm_a: Any) -> None:
    """The lost-phone path works, and costs exactly one code."""
    enrolment = await enrol(client, api, firm_a.headers)
    challenge = await _gate_and_challenge(client, api, firm_a.email)

    response = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": challenge, "code": enrolment["recovery_codes"][0]},
    )
    assert response.status_code == 200, response.text
    token = response.json()["accessToken"]

    status = await client.get("%s/auth/2fa" % api, headers={"Authorization": "Bearer %s" % token})
    assert status.json()["recoveryCodesRemaining"] == RECOVERY_CODE_COUNT - 1
    assert status.json()["enabled"] is True


async def test_a_recovery_code_works_exactly_once(client: Any, api: str, firm_a: Any) -> None:
    """Single-use is the entire security property of a recovery code.

    A printed code that keeps working is a permanent second key to the account, sitting
    in whatever drawer the user left it in.
    """
    enrolment = await enrol(client, api, firm_a.headers)
    code = enrolment["recovery_codes"][0]

    first = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": await _gate_and_challenge(client, api, firm_a.email), "code": code},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": await _gate_and_challenge(client, api, firm_a.email), "code": code},
    )
    assert second.status_code == 400, "a spent recovery code was accepted again: %s" % second.text
    assert problem(second)["code"] == "two_factor_invalid"

    # And the *other* nine still work — spending one must not burn the set.
    third = await client.post(
        "%s/auth/2fa/verify" % api,
        json={
            "challenge": await _gate_and_challenge(client, api, firm_a.email),
            "code": enrolment["recovery_codes"][1],
        },
    )
    assert third.status_code == 200, third.text


async def test_a_recovery_code_can_turn_the_factor_off(client: Any, api: str, firm_a: Any) -> None:
    """The real lost-phone story, end to end.

    Sign in with a recovery code, use another to disable the factor, then sign in
    normally again. If disabling required a TOTP code, a user whose phone is gone would
    be able to get in and then be stuck behind a credential they can never produce.
    """
    enrolment = await enrol(client, api, firm_a.headers)
    challenge = await _gate_and_challenge(client, api, firm_a.email)
    session = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": challenge, "code": enrolment["recovery_codes"][0]},
    )
    headers = {"Authorization": "Bearer %s" % session.json()["accessToken"]}

    disabled = await client.post(
        "%s/auth/2fa/disable" % api,
        json={"code": enrolment["recovery_codes"][1]},
        headers=headers,
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert disabled.json()["recoveryCodesRemaining"] == 0

    client.cookies.clear()
    back_in = await sign_in(client, api, firm_a.email, user_agent="curl/8.4.0", ip="203.0.113.10")
    assert back_in["access"], "the account stayed locked after two-factor was turned off"


async def test_disabling_needs_a_real_code(client: Any, api: str, firm_a: Any) -> None:
    """Otherwise a stolen *session* could quietly remove the second factor."""
    await enrol(client, api, firm_a.headers)
    response = await client.post(
        "%s/auth/2fa/disable" % api, json={"code": "000000"}, headers=firm_a.headers
    )
    assert response.status_code == 400, response.text
    status = await client.get("%s/auth/2fa" % api, headers=firm_a.headers)
    assert status.json()["enabled"] is True


async def test_regenerating_recovery_codes_retires_the_old_set(
    client: Any, api: str, firm_a: Any
) -> None:
    """ "I think someone saw my codes" has to actually invalidate them."""
    enrolment = await enrol(client, api, firm_a.headers)
    old = enrolment["recovery_codes"]

    regenerated = await client.post(
        "%s/auth/2fa/recovery-codes" % api,
        json={"code": code_now(enrolment["secret"], offset_steps=1)},
        headers=firm_a.headers,
    )
    assert regenerated.status_code == 200, regenerated.text
    new = regenerated.json()["recoveryCodes"]
    assert len(new) == RECOVERY_CODE_COUNT
    assert not set(new) & set(old)

    retired = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": await _gate_and_challenge(client, api, firm_a.email), "code": old[0]},
    )
    assert retired.status_code == 400, "a retired recovery code still worked: %s" % retired.text

    accepted = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": await _gate_and_challenge(client, api, firm_a.email), "code": new[0]},
    )
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# 5. Brute force and replay, over HTTP
# ---------------------------------------------------------------------------


async def test_a_code_cannot_be_replayed_over_http(client: Any, api: str, firm_a: Any) -> None:
    """The unit-level replay guard, proved through the routes and the database.

    The counter has to survive the request that spent it — which is why the failure
    path commits it (``_persist_failure_record``) instead of letting the 400 roll it
    back.
    """
    enrolment = await enrol(client, api, firm_a.headers)
    code = code_now(enrolment["secret"], offset_steps=1)

    first = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": await _gate_and_challenge(client, api, firm_a.email), "code": code},
    )
    assert first.status_code == 200, first.text

    replay = await client.post(
        "%s/auth/2fa/verify" % api,
        json={"challenge": await _gate_and_challenge(client, api, firm_a.email), "code": code},
    )
    assert replay.status_code == 400, "a TOTP code was accepted twice: %s" % replay.text


async def test_guessing_is_rate_limited_per_user(client: Any, api: str, firm_a: Any) -> None:
    """Six digits is 10^6; without a limit that falls in minutes at HTTP speed.

    The budget is charged per *user*, not per IP, so rotating source addresses does not
    reset it — which is the only version of this limit that is worth having.
    """
    await enrol(client, api, firm_a.headers)
    challenge = await _gate_and_challenge(client, api, firm_a.email)

    statuses = []
    for attempt in range(TWO_FACTOR_MAX_ATTEMPTS + 2):
        response = await client.post(
            "%s/auth/2fa/verify" % api,
            json={"challenge": challenge, "code": "%06d" % attempt},
            headers={"x-forwarded-for": "203.0.113.%d" % (attempt + 1)},
        )
        statuses.append(response.status_code)

    assert 429 in statuses, statuses
    # Activation already spent one slot, so the cut-off lands at or before the cap.
    assert statuses.index(429) <= TWO_FACTOR_MAX_ATTEMPTS, statuses


async def test_a_successful_verification_hands_the_attempt_budget_back(
    session: Any, clean_redis: Any, firm_a: Any
) -> None:
    """Five wrong guesses, not five uses.

    The budget was charged before every verification and released after none, so it
    counted *successes* too: sign in on a sixth device inside ten minutes — or activate,
    sign in, then rotate your recovery codes — and the product refuses you for doing
    everything right. Worse, a user who fat-fingers two codes has three real attempts
    left rather than five, on the screen where being locked out is the expensive
    failure. A proof of possession is not a guess, so it gives the slot back.

    Driven through :class:`TwoFactorService` rather than HTTP because seven *live*
    verifications need seven credentials, and TOTP only accepts one step either side of
    now — recovery codes are the credential a real lost-phone user spends in a row.
    """
    repo = TwoFactorRepository(session, firm_a.ctx())
    service = TwoFactorService(repo)
    identity = "user:%s" % firm_a.user_id
    rule = two_factor_attempt_rule()

    secret = generate_secret()
    await repo.upsert_pending(firm_a.user_id, secret=secret)
    codes = await service.activate(firm_a.user_id, code_now(secret))
    assert await peek_rate_limit(rule, identity) == 0, "activation kept the slot it spent"

    # Two more than the budget, all of them legitimate.
    for code in codes[: TWO_FACTOR_MAX_ATTEMPTS + 2]:
        result = await service.verify_second_factor(firm_a.user_id, code)
        assert result.used_recovery_code is True
        assert await peek_rate_limit(rule, identity) == 0, (
            "a successful verification left a slot spent — the %dth legitimate one in "
            "the window will 429" % TWO_FACTOR_MAX_ATTEMPTS
        )

    # The control: the limit is still a limit. Wrong codes accumulate and bite.
    for _ in range(TWO_FACTOR_MAX_ATTEMPTS):
        with pytest.raises(TwoFactorInvalidError):
            await service.verify_second_factor(firm_a.user_id, "000000")
    with pytest.raises(RateLimitedError):
        await service.verify_second_factor(firm_a.user_id, "000000")


# ---------------------------------------------------------------------------
# 6. Tenancy and storage
# ---------------------------------------------------------------------------


async def test_the_secret_is_never_read_back(client: Any, api: str, firm_a: Any) -> None:
    """After activation there is no route that returns the secret or the codes.

    They are shown once. ``recoveryHashes`` in the database are ``sha256`` digests, so
    there is nothing for a support tool to leak even if one were written.
    """
    enrolment = await enrol(client, api, firm_a.headers)
    status = await client.get("%s/auth/2fa" % api, headers=firm_a.headers)
    rendered = status.text
    assert enrolment["secret"] not in rendered
    for code in enrolment["recovery_codes"]:
        assert code not in rendered


async def test_recovery_codes_are_stored_hashed(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """Read the row directly: plaintext in this column would be the whole ballgame."""
    enrolment = await enrol(client, api, firm_a.headers)
    session.expire_all()
    record = await TwoFactorRepository(session, firm_a.ctx()).for_user(firm_a.user_id)
    assert record is not None
    assert len(record.recovery_hashes) == RECOVERY_CODE_COUNT
    for code in enrolment["recovery_codes"]:
        assert code not in record.recovery_hashes
        assert hash_recovery_code(code) in record.recovery_hashes


async def test_another_firm_cannot_see_or_touch_this_enrolment(
    client: Any, api: str, session: Any, firm_a: Any, firm_b: Any
) -> None:
    """The repository is firm-scoped, so firm B's read of firm A's row is ``None``."""
    await enrol(client, api, firm_a.headers)
    session.expire_all()

    assert await TwoFactorRepository(session, firm_b.ctx()).for_user(firm_a.user_id) is None
    assert await TwoFactorRepository(session, firm_b.ctx()).remove(firm_a.user_id) is False
    assert await TwoFactorRepository(session, firm_a.ctx()).for_user(firm_a.user_id) is not None

    # And over HTTP: firm B's own status is its own, never firm A's.
    status = await client.get("%s/auth/2fa" % api, headers=firm_b.headers)
    assert status.json()["enabled"] is False


async def test_a_colleagues_factor_does_not_gate_your_sign_in(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    """Enrolment is per seat. One admin turning it on must not lock out the firm."""
    await enrol(client, api, firm_a.headers)
    signed_in = await sign_in(
        client, api, member_a.email, user_agent="curl/8.4.0", ip="203.0.113.20"
    )
    assert signed_in["access"]


async def test_the_enrolment_row_is_erased_with_its_user(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """``ON DELETE CASCADE`` — F-6 must not leave a live credential behind a dead seat."""
    started = await client.post("%s/auth/2fa/enrol" % api, headers=member_a.headers)
    secret = started.json()["secret"]
    await client.post(
        "%s/auth/2fa/activate" % api,
        json={"code": totp_at(secret, counter_at())},
        headers=member_a.headers,
    )
    session.expire_all()
    assert await TwoFactorRepository(session, firm_a.ctx()).for_user(member_a.user_id) is not None

    from garh_api.repositories.users import UserRepository

    await UserRepository(session, firm_a.ctx()).remove(member_a.user_id)
    await session.commit()
    session.expire_all()
    assert await TwoFactorRepository(session, firm_a.ctx()).for_user(member_a.user_id) is None


async def test_the_replay_high_water_mark_never_goes_backwards(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """``record_counter`` raises the mark; it never lowers it.

    Two verifications inside the drift window can legitimately match different steps.
    If the lower one landed last it would re-open the higher step for replay — a race
    that is invisible single-threaded, so it is asserted against the repository
    directly rather than hoped for.
    """
    await enrol(client, api, firm_a.headers)
    session.expire_all()
    repo = TwoFactorRepository(session, firm_a.ctx())
    high = counter_at() + 5

    assert await repo.record_counter(firm_a.user_id, high) == high
    assert (
        await repo.record_counter(firm_a.user_id, high - 3) == high
    ), "a later verification at a lower step lowered the replay guard"
    await session.commit()

    record = await repo.for_user(firm_a.user_id)
    assert record is not None and record.last_counter == high


# ---------------------------------------------------------------------------
# Negative controls added by the F-4 repair pass
# ---------------------------------------------------------------------------
#
# * ``test_the_challenge_is_not_a_bearer_token`` was green while the audience
#   separation it is named for was gone (``typ`` alone was refusing the token). Both
#   halves of the regression now redden it: setting
#   ``_AUDIENCE_BY_TYPE[TOKEN_TYPE_TWO_FACTOR] = AUDIENCE_ACCESS`` and minting the
#   challenge with the access audience fails the ``claims["aud"]`` assertion, and
#   flipping ``verify_aud`` to False in ``decode_token`` fails the forged-``typ``
#   assertion with a 200 where a 401 belongs.
# * deleting either ``_clear_attempts`` call in ``TwoFactorService`` reddens
#   ``test_a_successful_verification_hands_the_attempt_budget_back`` — on the peek
#   after activation, or (with the peek muted) on a ``RateLimitedError`` raised by the
#   sixth legitimate verification inside the window.
