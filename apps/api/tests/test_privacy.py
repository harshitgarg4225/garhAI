"""F-5 the readable audit trail, and F-6 the DPDP export and erasure.

The two features share a file because they share a claim: that the API can tell a firm
what it recorded, and tell a person what it holds — truthfully, and only about them.

The three tests that matter most, and why:

* :func:`test_meta_that_looks_like_a_credential_is_redacted` — ``audit_log.meta`` is
  free-form JSONB written by a dozen call sites, and ``GET /audit`` is the first thing
  that renders it to a human. The redaction is negative-tested (see the note at the
  bottom of this file): with the key list emptied, the same test goes red.
* :func:`test_erasure_keeps_every_op_and_only_drops_the_actor` — the whole F-6 design
  decision in one assertion. An op is the design, not a record about a person; erasing
  one architect must not corrupt a colleague's project or a submitted drawing set.
* :func:`test_the_last_admin_cannot_erase_themselves` — the refusal that stops one
  person's right from taking a whole practice's tenant away with it.
"""

from __future__ import annotations

import importlib
import pkgutil
import uuid
from typing import Any

import pytest
from garh_api.repositories.audit_log import AuditLogRepository
from garh_api.repositories.privacy import (
    ERASED_AUTHOR_NAME,
    SHARED_NAME_REASON,
    VIEWER_COMMENT_REASON,
)
from garh_api.repositories.two_factor import TwoFactorRepository
from garh_api.repositories.users import UserRepository
from garh_api.routers.privacy import (
    ACTION_PRIVACY_EXPORTED,
    MAX_META_DEPTH,
    REDACTION_MARKER,
    redact_meta,
)
from garh_api.tenancy import UNSCOPED_AUDIT_ACTION, EntityNotFoundError, TenantCtx

from tests import factories
from tests.helpers import main_branch, problem

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def record(session: Any, actor: Any, action: str, **meta: Any) -> Any:
    """Write one audit row as ``actor``, committed so an HTTP request can see it."""
    entry = await AuditLogRepository(session, actor.ctx()).record(
        action, entity="user", entity_id=actor.user_id, meta=meta
    )
    await session.commit()
    return entry


#: Modules under ``garh_api`` that legitimately cannot be imported by the API test
#: environment. ``copilot_loop`` imports ``services.*``, which is a sibling package the
#: API's own test run does not put on the path. The scans below assert that the set of
#: import failures stays a SUBSET of this — a new module that stops importing fails the
#: scan instead of quietly shrinking what it can see.
_UNIMPORTABLE_MODULES = frozenset({"garh_api.copilot_loop"})


def _walk_garh_api() -> tuple[dict[str, Any], frozenset[str]]:
    """Import every module in ``garh_api``; return ``(modules, failures)``.

    Two tests here derive an expectation from the source tree rather than from a list
    somebody maintained by hand — the vocabulary of audit actions, and (in
    ``test_job_rate_limits.py``) the registry of rate-limit rules. Both failures being
    guarded against are omissions, and you cannot spot an omission against a list that
    was written by the same person who made it.
    """
    import garh_api

    modules: dict[str, Any] = {}
    failures: set[str] = set()
    for info in pkgutil.walk_packages(garh_api.__path__, prefix="garh_api."):
        try:
            modules[info.name] = importlib.import_module(info.name)
        except Exception:
            failures.add(info.name)
    return modules, frozenset(failures)


async def some_ops(session: Any, actor: Any, project_id: uuid.UUID, count: int = 3) -> None:
    from garh_api.repositories.domain import NewOp

    await factories.append_ops(
        session,
        actor,
        project_id,
        [NewOp(type="plot.set_north", payload={"deg": index * 10}) for index in range(count)],
    )


# ---------------------------------------------------------------------------
# F-5: the audit trail
# ---------------------------------------------------------------------------


async def test_an_admin_can_read_their_own_firms_trail(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """The rows have been written since Phase 0; this is the first route that reads them."""
    await record(session, firm_a, "export.created", kind="dxf")
    await record(session, firm_a, "share.created", projectId="x")

    response = await client.get("%s/audit" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["action"] for item in items} == {"export.created", "share.created"}
    newest = items[0]
    assert newest["actorId"] == str(firm_a.user_id)
    assert newest["actorName"] == "Asha Rao", newest
    assert newest["at"], newest
    assert newest["entity"] == "user"


async def test_a_member_cannot_read_the_trail(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """It says which colleague did what, from which address. That is a management tool."""
    await record(session, firm_a, "export.created", kind="dxf")
    response = await client.get("%s/audit" % api, headers=member_a.headers)
    assert response.status_code == 403, response.text
    assert problem(response)["code"] == "permission_denied"


async def test_a_firm_sees_none_of_another_firms_trail(
    client: Any, api: str, session: Any, firm_a: Any, firm_b: Any
) -> None:
    """Both firms have rows. Each sees exactly its own."""
    await record(session, firm_a, "export.created", note="firm-a-only")
    await record(session, firm_b, "export.created", note="firm-b-only")

    from_a = await client.get("%s/audit" % api, headers=firm_a.headers)
    from_b = await client.get("%s/audit" % api, headers=firm_b.headers)

    assert [item["meta"]["note"] for item in from_a.json()["items"]] == ["firm-a-only"]
    assert [item["meta"]["note"] for item in from_b.json()["items"]] == ["firm-b-only"]
    assert "firm-b-only" not in from_a.text
    assert str(firm_a.user_id) not in from_b.text


async def test_the_trail_can_be_filtered_and_paged(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    for index in range(5):
        await record(session, firm_a, "export.created", index=index)
    await record(session, firm_a, "share.created", index=99)

    filtered = await client.get("%s/audit?action=export.created" % api, headers=firm_a.headers)
    assert {item["action"] for item in filtered.json()["items"]} == {"export.created"}
    assert len(filtered.json()["items"]) == 5

    first_page = await client.get("%s/audit?limit=2" % api, headers=firm_a.headers)
    body = first_page.json()
    assert len(body["items"]) == 2 and body["nextCursor"]
    second_page = await client.get(
        "%s/audit?limit=2&cursor=%s" % (api, body["nextCursor"]), headers=firm_a.headers
    )
    assert len(second_page.json()["items"]) == 2
    seen = {item["id"] for item in body["items"]} | {
        item["id"] for item in second_page.json()["items"]
    }
    assert len(seen) == 4, "the cursor returned overlapping pages"


async def test_meta_that_looks_like_a_credential_is_redacted(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """The gate that stops this admin screen becoming a disclosure surface.

    Nothing in the product writes these keys today — that is the primary control. This
    is the second one, for the call site somebody adds next year. The useful context
    (``ip``, ``userAgent``, ``family``) must survive, or the screen is worthless.
    """
    await record(
        session,
        firm_a,
        "export.created",
        otpCode="123456",
        secret="s3cr3t",
        refreshToken="eyJ...",
        code="999999",
        codeHash="deadbeef",
        provider={"apiKey": "sk-live-xyz", "name": "stability"},
        # A list of objects is the same document one bracket deeper, and it is the
        # shape a config blob is most naturally written in.
        providers=[{"apiKey": "sk-live-LEAK", "name": "anthropic"}],
        nested={"list": [{"secret": "LEAK2"}]},
        ip="203.0.113.9",
        userAgent="Chrome/126",
        family="abc123",
    )

    response = await client.get("%s/audit" % api, headers=firm_a.headers)
    meta = response.json()["items"][0]["meta"]

    for key in ("otpCode", "secret", "refreshToken", "code", "codeHash"):
        assert meta[key] == REDACTION_MARKER, "%s survived redaction: %r" % (key, meta)
    assert meta["provider"]["apiKey"] == REDACTION_MARKER, meta
    assert meta["provider"]["name"] == "stability", "redaction ate a non-secret sibling"
    assert meta["providers"][0]["apiKey"] == REDACTION_MARKER, meta
    assert meta["providers"][0]["name"] == "anthropic", "redaction ate a non-secret sibling"
    assert meta["nested"]["list"][0]["secret"] == REDACTION_MARKER, meta

    assert meta["ip"] == "203.0.113.9"
    assert meta["userAgent"] == "Chrome/126"
    assert meta["family"] == "abc123"

    body = response.text
    for secret in (
        "123456",
        "s3cr3t",
        "eyJ...",
        "999999",
        "deadbeef",
        "sk-live-xyz",
        "sk-live-LEAK",
        "LEAK2",
    ):
        assert secret not in body, "%r reached the response body" % secret


def _declared_audit_actions() -> dict[str, str]:
    """Every ``ACTION_*`` string declared anywhere in ``garh_api``, value -> where.

    Derived, not typed out. The previous version of the vocabulary test spot-checked
    three strings it had written by hand, so it could not notice that
    ``privacy.data_exported`` — written by ``GET /privacy/export`` on every single call
    — was missing from the filter the admin screen is built on. A hand-written list of
    what the code contains is a second copy of the code, and it drifts.
    """
    modules, failed = _walk_garh_api()
    assert failed <= _UNIMPORTABLE_MODULES, (
        "a garh_api module stopped importing, so this scan may be missing actions: %s" % failed
    )
    found: dict[str, str] = {}
    for name, module in modules.items():
        for attribute, value in vars(module).items():
            if attribute.startswith("ACTION_") and isinstance(value, str):
                found.setdefault(value, "%s.%s" % (name, attribute))
    return found


def test_redact_meta_walks_lists_exactly_as_it_walks_objects() -> None:
    """A list of objects is an object with brackets round it.

    The first version of this redactor recursed into ``dict`` and returned everything
    else untouched, so ``{"providers": [{"apiKey": ...}]}`` — the ordinary shape of a
    provider config — came back with the key in plaintext. The gate exists for the call
    site somebody adds next year, and that call site is at least as likely to write a
    list as a dict.
    """
    redacted = redact_meta(
        {
            "providers": [{"apiKey": "sk-live-LEAK"}],
            "nested": {"list": [{"secret": "LEAK2"}]},
            "deeper": [[{"otpCode": "123456"}]],
            "kept": ["stability", {"name": "anthropic"}],
        }
    )

    assert redacted["providers"][0]["apiKey"] == REDACTION_MARKER
    assert redacted["nested"]["list"][0]["secret"] == REDACTION_MARKER
    assert redacted["deeper"][0][0]["otpCode"] == REDACTION_MARKER
    assert "LEAK" not in str(redacted) and "123456" not in str(redacted)
    # Non-secret siblings survive, or the audit screen is worthless.
    assert redacted["kept"] == ["stability", {"name": "anthropic"}]


def test_redaction_stops_rather_than_recursing_forever() -> None:
    """Past the depth cap a value is withheld, not passed through unread.

    ``meta`` is free-form JSONB. A document nested deeper than anything we write is
    either a bug or an attack, and the safe answer to "I did not look inside this" is
    the marker, never the value.
    """
    payload: dict[str, Any] = {"apiKey": "sk-live-DEEP"}
    for _ in range(MAX_META_DEPTH + 2):
        payload = {"wrap": payload}

    redacted = redact_meta(payload)
    assert "sk-live-DEEP" not in str(redacted)
    assert REDACTION_MARKER in str(redacted)


async def test_the_action_vocabulary_is_admin_only_and_complete(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    """The filter control reads this instead of hard-coding its own copy.

    And so does this test: it walks ``garh_api`` for the action constants rather than
    naming a few, because the failure being guarded against is an action that exists in
    the log and not in the vocabulary — which no spot-check of three known-good strings
    can ever see.
    """
    response = await client.get("%s/audit/actions" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    actions = response.json()["actions"]
    assert actions == sorted(actions)

    declared = _declared_audit_actions()
    # ``system.unscoped_session`` is written against the SYSTEM firm, so it can never
    # appear in a firm's own trail and must not be offered as a filter. It is excluded
    # here by name so the omission is a decision rather than an accident of naming.
    expected = set(declared) - {UNSCOPED_AUDIT_ACTION}
    missing = expected - set(actions)
    assert not missing, "actions the trail can contain but the filter cannot select: %s" % {
        action: declared[action] for action in sorted(missing)
    }
    assert set(actions) <= expected, "the filter offers actions nothing can write: %s" % (
        set(actions) - expected
    )
    assert ACTION_PRIVACY_EXPORTED in actions, "the privacy surface's own action is unselectable"

    denied = await client.get("%s/audit/actions" % api, headers=member_a.headers)
    assert denied.status_code == 403


async def test_the_action_filter_actually_selects_the_export_action(
    client: Any, api: str, firm_a: Any
) -> None:
    """The vocabulary is only worth anything if the value it hands the UI works.

    ``privacy.data_exported`` was in neither list, so an admin asking "who exported
    their data" got an empty page from a route that had written the row seconds before.
    """
    await client.get("%s/privacy/export" % api, headers=firm_a.headers)

    vocabulary = await client.get("%s/audit/actions" % api, headers=firm_a.headers)
    assert ACTION_PRIVACY_EXPORTED in vocabulary.json()["actions"]

    filtered = await client.get(
        "%s/audit?action=%s" % (api, ACTION_PRIVACY_EXPORTED), headers=firm_a.headers
    )
    assert [item["action"] for item in filtered.json()["items"]] == [ACTION_PRIVACY_EXPORTED]


async def test_actor_names_resolve_past_the_first_two_hundred_seats(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """A name lookup, not a guess at how big a firm gets.

    ``_actor_names`` used to read ``list_members(limit=200)`` and index it. Seat 201
    resolved to ``actorName: null``, which on this screen reads as "an erased account"
    — a wrong answer, silently, on the page whose whole job is to say who did what.
    So the test asks that capped page which seat it would have missed, and then insists
    the route names it.
    """
    users = UserRepository(session, firm_a.ctx())
    everyone: dict[uuid.UUID, str] = {}
    for index in range(205):
        seat = await users.create(email="seat-%03d@studio.test" % index, name="Seat %03d" % index)
        everyone[seat.id] = seat.name
    await session.commit()

    # ``limit=200`` is ``MAX_PAGE_SIZE``: the old lookup could not have read more than
    # this however it was called, so whatever this page omits is what it forgot.
    capped = {member.id for member in (await users.list_members(limit=200)).items}
    missed = [user_id for user_id in everyone if user_id not in capped]
    assert missed, "fewer than 200 seats reached — this test is not exercising the cap"

    forgotten = missed[0]
    await AuditLogRepository(
        session,
        TenantCtx(firm_id=firm_a.firm_id, user_id=forgotten, role="member", request_id="test"),
    ).record("export.created", entity="user", entity_id=forgotten, meta={"kind": "dxf"})
    await session.commit()

    response = await client.get("%s/audit?limit=1" % api, headers=firm_a.headers)
    row = response.json()["items"][0]
    assert row["actorId"] == str(forgotten)
    assert row["actorName"] == everyone[forgotten], row


async def test_an_erased_actor_leaves_a_row_with_no_name(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """The trail outlives the account, which is the point of it having no foreign keys."""
    await record(session, member_a, "export.created", kind="pdf")
    await UserRepository(session, firm_a.ctx()).remove(member_a.user_id)
    await session.commit()

    response = await client.get("%s/audit" % api, headers=firm_a.headers)
    row = next(item for item in response.json()["items"] if item["action"] == "export.created")
    assert row["actorId"] == str(member_a.user_id)
    assert row["actorName"] is None, "a deleted seat still resolved to a name"


# ---------------------------------------------------------------------------
# F-6: the export
# ---------------------------------------------------------------------------


async def test_the_export_describes_the_caller_and_nobody_else(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """A subject-access response: mine, all of it, and only mine."""
    project = await factories.create_project(session, firm_a, name="Sharma Residence")
    await some_ops(session, member_a, project.id, count=4)
    await record(session, member_a, "export.created", kind="dxf")
    await record(session, firm_a, "export.created", kind="pdf")

    response = await client.get("%s/privacy/export" % api, headers=member_a.headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["subject"]["email"] == member_a.email
    assert body["subject"]["name"] == "Rahul Verma"
    assert body["firm"]["id"] == str(member_a.firm_id)
    assert body["designActivity"]["opCount"] == 4
    assert body["designActivity"]["projectIds"] == [str(project.id)]
    assert body["designActivity"]["firstOpAt"] and body["designActivity"]["lastOpAt"]
    assert body["retention"]["auditLog"], "the retention decision must be stated to the user"
    assert body["twoFactor"]["enabled"] is False

    # The colleague's audit row is not in *this* person's trail.
    actions = [(entry["action"], entry.get("meta", {}).get("kind")) for entry in body["authTrail"]]
    assert ("export.created", "dxf") in actions
    assert ("export.created", "pdf") not in actions
    assert firm_a.email not in response.text


async def test_the_export_carries_no_design_payloads(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """Op contents are the firm's design data, not the individual's personal data.

    The person is owed the fact and extent of their authorship, which is what the
    counts and project ids are. Shipping payloads would hand one seat a copy of the
    practice's work under cover of a privacy right.
    """
    project = await factories.create_project(session, firm_a)
    await some_ops(session, firm_a, project.id, count=2)

    body = (await client.get("%s/privacy/export" % api, headers=firm_a.headers)).json()
    assert "plot.set_north" not in str(body)
    assert body["designActivity"]["note"]


async def test_the_export_includes_devices_and_comments(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    project = await factories.create_project(session, firm_a)
    # ``create_comment`` authors as "Asha Rao", which is firm_a's own name.
    await factories.create_comment(session, firm_a, project.id, body="Move the door")

    body = (await client.get("%s/privacy/export" % api, headers=firm_a.headers)).json()
    assert [comment["body"] for comment in body["comments"]] == ["Move the door"]
    assert isinstance(body["signedInDevices"], list)


async def test_the_export_never_carries_a_colleagues_comments(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """**The disclosure this file exists to prevent.**

    ``comments`` has no ``author_user_id`` — only ``author_name``, denormalised text
    the client supplies. The export used to match on it, so two people at one practice
    called "Priya Sharma" (which is not a contrived collision in India, and neither is
    a couple sharing a display name) each received the other's comment bodies in their
    own §11 subject-access response. A subject-access response containing somebody
    else's data is the exact thing the Act is written against.

    Both halves are asserted: neither person gets the other's body, and the withheld
    count and reason are actually shown rather than the comments silently vanishing.
    """
    project = await factories.create_project(session, firm_a)
    from garh_api.repositories import CommentRepository

    priya_one = await factories.add_member(session, firm_a, name="Priya Sharma")
    priya_two = await factories.add_member(session, firm_a, name="Priya Sharma")

    for member, body in ((priya_one, "One's private note"), (priya_two, "Two's private note")):
        await CommentRepository(session, member.ctx()).create(
            project.id, body=body, author_name="Priya Sharma"
        )
    await session.commit()

    for member, theirs, not_theirs in (
        (priya_one, "One's private note", "Two's private note"),
        (priya_two, "Two's private note", "One's private note"),
    ):
        response = await client.get("%s/privacy/export" % api, headers=member.headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert (
            not_theirs not in response.text
        ), "a colleague's comment body reached this person's subject-access response"
        # Under-matching is the only safe direction while the column is missing: we
        # cannot tell these two apart, so neither is handed a guess.
        assert theirs not in response.text
        assert [comment["body"] for comment in body["comments"]] == []
        assert body["commentsWithheld"] == 2, body
        assert body["commentsNote"] == SHARED_NAME_REASON, body


async def test_an_unambiguous_name_still_gets_its_comments(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """The positive control for the refusal above — it is a guard, not a wall.

    Without this, deleting the whole comments section would pass the test above, and
    a §11 response that omits data it holds is its own kind of wrong.
    """
    project = await factories.create_project(session, firm_a)
    await factories.create_comment(session, firm_a, project.id, body="Move the door")

    body = (await client.get("%s/privacy/export" % api, headers=firm_a.headers)).json()
    assert [comment["body"] for comment in body["comments"]] == ["Move the door"]
    assert body["commentsWithheld"] == 0
    assert body["commentsNote"] is None


async def test_a_share_link_viewers_comment_is_not_attributed_to_a_seat(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """``author_name`` on the viewer surface is a text box, not an identity.

    Anyone holding a share link can type "Asha Rao" into it. Attributing that to the
    seat called Asha Rao would put a stranger's words into her subject-access response
    and — worse — invite her to read them as her own.
    """
    project = await factories.create_project(session, firm_a)
    link, _token = await factories.create_share_link(session, firm_a, project.id)
    from garh_api.repositories import CommentRepository

    await CommentRepository(session, firm_a.ctx()).create(
        project.id,
        body="Typed by whoever holds the link",
        author_name="Asha Rao",
        share_link_id=link.id,
    )
    await session.commit()

    body = (await client.get("%s/privacy/export" % api, headers=firm_a.headers)).json()
    assert body["comments"] == []
    assert body["commentsWithheld"] == 1, body
    assert body["commentsNote"] == VIEWER_COMMENT_REASON, body


async def test_erasure_still_scrubs_a_shared_name_from_every_row(session: Any, firm_a: Any) -> None:
    """Erasure keeps over-matching, on purpose, and the two rights pull apart here.

    Export must under-match: handing one person a colleague's comment is a disclosure.
    Erasure must over-match: leaving a name behind is the thing §12 asked us to remove.
    Both behaviours are asserted because "match by name" is one line that has to be
    read in two directions, and a later tidy-up that unified them would break one of
    them silently.
    """
    project = await factories.create_project(session, firm_a)
    from garh_api.repositories import CommentRepository
    from garh_api.repositories.privacy import _AuthoredCommentRepository

    priya_one = await factories.add_member(session, firm_a, name="Priya Sharma")
    priya_two = await factories.add_member(session, firm_a, name="Priya Sharma")
    for member in (priya_one, priya_two):
        await CommentRepository(session, member.ctx()).create(
            project.id, body="Note from %s" % member.email, author_name="Priya Sharma"
        )
    await session.commit()

    scrubbed = await _AuthoredCommentRepository(session, firm_a.ctx()).anonymise_author(
        "Priya Sharma"
    )
    assert scrubbed == 2, "erasure left a colleague's copy of the erased name behind"
    await session.commit()
    session.expire_all()

    page = await CommentRepository(session, firm_a.ctx()).list_for_project(project.id)
    assert {comment.author_name for comment in page.items} == {ERASED_AUTHOR_NAME}


async def test_exporting_is_itself_audited(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """Putting a copy of personal data outside the system is a security event."""
    await client.get("%s/privacy/export" % api, headers=firm_a.headers)
    trail = await client.get("%s/audit?action=privacy.data_exported" % api, headers=firm_a.headers)
    assert len(trail.json()["items"]) == 1, trail.json()


async def test_the_export_needs_a_token(client: Any, api: str) -> None:
    response = await client.get("%s/privacy/export" % api)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# F-6: erasure
# ---------------------------------------------------------------------------


async def test_erasure_keeps_every_op_and_only_drops_the_actor(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """**The F-6 decision, asserted.**

    Model state is ``fold(ops)``. If erasure deleted the member's ops, the branch head
    would move backwards, every later op's ``idx`` would be wrong, and the drawings
    generated from that project would silently change. So the op stays and the actor
    goes.
    """
    project = await factories.create_project(session, firm_a)
    await some_ops(session, member_a, project.id, count=3)

    from garh_api.repositories import OpRepository

    branch = main_branch(project.id)
    before = await OpRepository(session, firm_a.ctx()).list_since(project.id, branch, -1)
    assert len(before) == 3
    assert {op.actor for op in before} == {member_a.user_id}
    head_before = await OpRepository(session, firm_a.ctx()).head_idx(project.id, branch)

    erased = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": member_a.email},
        headers=member_a.headers,
    )
    assert erased.status_code == 200, erased.text
    assert erased.json()["opsAnonymised"] == 3

    session.expire_all()
    after = await OpRepository(session, firm_a.ctx()).list_since(project.id, branch, -1)
    assert len(after) == 3, "erasure deleted design history"
    assert [op.idx for op in after] == [op.idx for op in before]
    assert [op.payload for op in after] == [op.payload for op in before]
    assert {op.actor for op in after} == {None}, "the actor survived erasure"
    assert await OpRepository(session, firm_a.ctx()).head_idx(project.id, branch) == head_before


async def test_op_actors_are_cleared_by_the_erasure_itself(
    session: Any, firm_a: Any, member_a: Any
) -> None:
    """The explicit anonymisation works on its own, with the seat still in place.

    Written after a negative control found that removing
    ``_ActorOpRepository.anonymise_actor`` did **not** turn the route-level test red:
    ``ops.actor`` is ``ON DELETE SET NULL``, so deleting the ``users`` row was quietly
    doing the same job. That makes the route test unable to fail for this reason —
    exactly the shape this repo has shipped before — so the step gets its own
    assertion, run *before* any delete.

    It is not redundant belt-and-braces: the moment erasure switches from deleting the
    seat to tombstoning it (which is a plausible future for audit reasons), the foreign
    key stops firing and this call is the only thing still clearing the actor.
    """
    from garh_api.repositories.privacy import _ActorOpRepository

    project = await factories.create_project(session, firm_a)
    await some_ops(session, member_a, project.id, count=3)

    repo = _ActorOpRepository(session, firm_a.ctx())
    assert await repo.anonymise_actor(member_a.user_id) == 3
    await session.commit()
    session.expire_all()

    from garh_api.repositories import OpRepository

    ops = await OpRepository(session, firm_a.ctx()).list_since(
        project.id, main_branch(project.id), -1
    )
    assert len(ops) == 3
    assert {op.actor for op in ops} == {None}
    # The seat is untouched: this step anonymises, it does not delete.
    assert await UserRepository(session, firm_a.ctx()).require(member_a.user_id) is not None


async def test_anonymising_one_actor_leaves_a_colleagues_ops_alone(
    session: Any, firm_a: Any, member_a: Any
) -> None:
    """The positive control: a statement scoped to one actor, not to the table."""
    project = await factories.create_project(session, firm_a)
    await some_ops(session, member_a, project.id, count=2)
    from garh_api.repositories.domain import NewOp

    await factories.append_ops(
        session, firm_a, project.id, [NewOp(type="plot.set_north", payload={"deg": 90})], base_idx=1
    )

    from garh_api.repositories.privacy import _ActorOpRepository

    assert await _ActorOpRepository(session, firm_a.ctx()).anonymise_actor(member_a.user_id) == 2
    await session.commit()
    session.expire_all()

    from garh_api.repositories import OpRepository

    ops = await OpRepository(session, firm_a.ctx()).list_since(
        project.id, main_branch(project.id), -1
    )
    assert [op.actor for op in ops] == [None, None, firm_a.user_id]


async def test_erasure_removes_the_seat_and_its_second_factor(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    started = await client.post("%s/auth/2fa/enrol" % api, headers=member_a.headers)
    assert started.status_code == 201
    session.expire_all()
    assert await TwoFactorRepository(session, firm_a.ctx()).for_user(member_a.user_id) is not None

    response = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": member_a.email},
        headers=member_a.headers,
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    with pytest.raises(EntityNotFoundError):
        await UserRepository(session, firm_a.ctx()).require(member_a.user_id)
    assert await TwoFactorRepository(session, firm_a.ctx()).for_user(member_a.user_id) is None

    # The audit row reports what the erasure did, and this flag is the only record
    # that the factor went. A flag that says True while nothing happened is worse
    # than no flag, so it is asserted here and proved independently below.
    trail = await client.get("%s/audit?action=user.removed" % api, headers=firm_a.headers)
    meta = trail.json()["items"][0]["meta"]
    assert meta["twoFactorRemoved"] is True, meta
    assert meta["reason"] == "dpdp_erasure"


async def test_the_second_factor_is_removed_by_the_erasure_itself(
    session: Any, firm_a: Any, member_a: Any
) -> None:
    """The removal is the erasure's own work, not the foreign key's.

    ``user_two_factor.user_id`` is ``ON DELETE CASCADE``, so deleting the seat removes
    the enrolment whatever ``erase()`` does or does not do — which is why replacing
    ``self._two_factor.remove(...)`` with a no-op left the route test above perfectly
    green while ``twoFactorRemoved`` in the audit row started lying. Exactly the shape
    of the ``anonymise_actor`` control a few tests up, and the same fix: neutralise the
    step that covers for it, then assert.

    It is not belt-and-braces. The moment erasure switches from deleting the seat to
    tombstoning it — plausible, for audit reasons — the cascade stops firing and this
    call is the only thing that takes the second factor away from an account whose
    owner has just exercised §12.
    """
    from garh_api.repositories.privacy import PrivacyRepository
    from garh_api.twofactor import generate_secret

    await TwoFactorRepository(session, firm_a.ctx()).upsert_pending(
        member_a.user_id, secret=generate_secret()
    )
    await session.commit()

    privacy = PrivacyRepository(session, member_a.ctx())

    async def _seat_survives(user_id: uuid.UUID) -> bool:
        del user_id
        return False

    privacy._users_delete = _seat_survives  # type: ignore[method-assign]
    outcome = await privacy.erase(member_a.user_id)
    await session.commit()
    session.expire_all()

    assert outcome.two_factor_removed is True, "the outcome flag the audit row copies"
    assert await TwoFactorRepository(session, firm_a.ctx()).for_user(member_a.user_id) is None
    # The control that makes the assertion above mean anything: the seat is still here,
    # so the cascade cannot be what removed the enrolment.
    assert await UserRepository(session, firm_a.ctx()).require(member_a.user_id) is not None


async def test_erasure_scrubs_the_name_off_their_comments(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """``comments.author_name`` is denormalised text — no foreign key clears it."""
    project = await factories.create_project(session, firm_a)
    from garh_api.repositories import CommentRepository

    member_name = "Rahul Verma"
    await CommentRepository(session, member_a.ctx()).create(
        project.id, body="Widen this corridor", author_name=member_name
    )
    await session.commit()

    response = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": member_a.email},
        headers=member_a.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["commentsAnonymised"] == 1

    session.expire_all()
    page = await CommentRepository(session, firm_a.ctx()).list_for_project(project.id)
    bodies = [comment.body for comment in page.items]
    authors = {comment.author_name for comment in page.items}
    assert "Widen this corridor" in bodies, "the comment itself was deleted"
    assert member_name not in authors
    assert authors == {ERASED_AUTHOR_NAME}


async def test_erasure_ends_every_session_immediately(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """The access token is a self-contained JWT; only the generation bump kills it.

    Without that, an erased account keeps working for up to 15 more minutes — with a
    ``users`` row that no longer exists.
    """
    still_valid = await client.get("%s/auth/me" % api, headers=member_a.headers)
    assert still_valid.status_code == 200

    erased = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": member_a.email},
        headers=member_a.headers,
    )
    assert erased.status_code == 200, erased.text

    dead = await client.get("%s/auth/me" % api, headers=member_a.headers)
    assert dead.status_code == 401, "an erased account's token still worked: %s" % dead.text
    assert problem(dead)["code"] == "token_revoked"


async def test_erasure_keeps_the_audit_trail(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """Retained under the Act's legal-obligation basis, and the response says how many."""
    await record(session, member_a, "export.created", kind="dxf")

    response = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": member_a.email},
        headers=member_a.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["auditEntriesRetained"] >= 1

    trail = await client.get("%s/audit" % api, headers=firm_a.headers)
    actions = [item["action"] for item in trail.json()["items"]]
    assert "export.created" in actions, "erasure destroyed the integrity record"
    assert "user.removed" in actions, "erasure was not itself audited"


async def test_erasure_needs_the_right_email_typed_back(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    """An irreversible action should cost a sentence, not a mis-click."""
    response = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": firm_a.email},
        headers=member_a.headers,
    )
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "conflict"

    still_there = await client.get("%s/auth/me" % api, headers=member_a.headers)
    assert still_there.status_code == 200, "a refused erasure erased anyway"


async def test_the_last_admin_cannot_erase_themselves(client: Any, api: str, firm_a: Any) -> None:
    """Their right does not extend to taking their colleagues' tenant with it.

    The refusal names the fix, because "no" without a next step is where a support
    ticket comes from (golden rule 9).
    """
    response = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": firm_a.email},
        headers=firm_a.headers,
    )
    assert response.status_code == 409, response.text
    body = problem(response)
    assert "admin" in body["message"].lower()
    assert "admin" in body["action"].lower()

    still_there = await client.get("%s/auth/me" % api, headers=firm_a.headers)
    assert still_there.status_code == 200


async def test_an_admin_can_erase_once_someone_else_is_promoted(
    client: Any, api: str, session: Any, firm_a: Any, member_a: Any
) -> None:
    """The positive control for the refusal above — it is a guard, not a wall."""
    await UserRepository(session, firm_a.ctx()).set_role(member_a.user_id, "admin")
    await session.commit()

    response = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": firm_a.email},
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text


async def test_erasure_touches_nothing_in_another_firm(
    client: Any, api: str, session: Any, firm_a: Any, firm_b: Any, member_a: Any
) -> None:
    """Firm B's ops, comments and seats are untouched by firm A's erasure."""
    project_b = await factories.create_project(session, firm_b, name="Other Studio House")
    await some_ops(session, firm_b, project_b.id, count=2)
    await factories.create_comment(session, firm_b, project_b.id, body="Their comment")

    project_a = await factories.create_project(session, firm_a)
    await some_ops(session, member_a, project_a.id, count=1)

    response = await client.post(
        "%s/privacy/erasure" % api,
        json={"confirmEmail": member_a.email},
        headers=member_a.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["opsAnonymised"] == 1, response.json()

    from garh_api.repositories import CommentRepository, OpRepository

    session.expire_all()
    ops_b = await OpRepository(session, firm_b.ctx()).list_since(
        project_b.id, main_branch(project_b.id), -1
    )
    assert len(ops_b) == 2
    assert {op.actor for op in ops_b} == {firm_b.user_id}, "another firm's ops were anonymised"

    comments_b = await CommentRepository(session, firm_b.ctx()).list_for_project(project_b.id)
    assert {comment.author_name for comment in comments_b.items} == {"Asha Rao"}
    assert await UserRepository(session, firm_b.ctx()).require(firm_b.user_id) is not None


# ---------------------------------------------------------------------------
# Negative controls
# ---------------------------------------------------------------------------
#
# Every gate in this file that could pass silently while broken was broken on purpose
# and re-run. The verbatim red output is in the agent's report; if you touch one of
# these, break it again and check that the test still notices.
#
# * emptying ``REDACTED_META_KEY_PARTS`` and ``REDACTED_META_KEYS`` makes
#   ``test_meta_that_looks_like_a_credential_is_redacted`` fail on the first key;
# * deleting the ``list``/``tuple`` branch of ``_redact_value`` (the state this shipped
#   in) makes the same test and
#   ``test_redact_meta_walks_lists_exactly_as_it_walks_objects`` fail on
#   ``providers[0].apiKey``;
# * dropping the shared-name check from ``PrivacyRepository.comments_for``, or the
#   ``share_link_id IS NULL`` filter from ``authored_by_seat``, makes
#   ``test_the_export_never_carries_a_colleagues_comments`` fail with the colleague's
#   body in the response text — the disclosure this lane was sent back to fix;
# * removing ``ACTION_PRIVACY_EXPORTED`` from ``AUDIT_ACTION_VOCABULARY`` makes
#   ``test_the_action_vocabulary_is_admin_only_and_complete`` name the missing action
#   AND the module that declares it, because it derives the expectation instead of
#   listing three known-good strings;
# * restoring ``list_members(limit=200)`` in ``_actor_names`` makes
#   ``test_actor_names_resolve_past_the_first_two_hundred_seats`` fail with
#   ``actorName: None``;
# * stubbing ``_ActorOpRepository.anonymise_actor`` to return 0 makes
#   ``test_erasure_keeps_every_op_and_only_drops_the_actor`` fail on ``opsAnonymised``.
#   Making it report the right count while doing no work did NOT turn that test red —
#   ``ops.actor ON DELETE SET NULL`` was covering for it — which is why
#   ``test_op_actors_are_cleared_by_the_erasure_itself`` exists and is asserted before
#   any row is deleted;
# * the same trick with ``self._two_factor.remove(...)``: replacing it with
#   ``two_factor = True`` leaves the route test green (``ON DELETE CASCADE`` covers it)
#   and turns ``test_the_second_factor_is_removed_by_the_erasure_itself`` red, because
#   that one keeps the seat alive so the cascade cannot fire. ``two_factor = False``
#   reddens both, the route test through the ``twoFactorRemoved`` flag in the audit row.
