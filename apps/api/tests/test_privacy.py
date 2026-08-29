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

import uuid
from typing import Any

import pytest
from garh_api.repositories.audit_log import AuditLogRepository
from garh_api.repositories.privacy import ERASED_AUTHOR_NAME
from garh_api.repositories.two_factor import TwoFactorRepository
from garh_api.repositories.users import UserRepository
from garh_api.routers.privacy import REDACTION_MARKER
from garh_api.tenancy import EntityNotFoundError

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

    assert meta["ip"] == "203.0.113.9"
    assert meta["userAgent"] == "Chrome/126"
    assert meta["family"] == "abc123"

    body = response.text
    for secret in ("123456", "s3cr3t", "eyJ...", "999999", "deadbeef", "sk-live-xyz"):
        assert secret not in body, "%r reached the response body" % secret


async def test_the_action_vocabulary_is_admin_only_and_complete(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    """The filter control reads this instead of hard-coding its own copy."""
    response = await client.get("%s/audit/actions" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    actions = response.json()["actions"]
    assert "auth.otp_verified" in actions
    assert "export.created" in actions
    assert "auth.two_factor_enabled" in actions, "F-4's actions are missing from the list"
    assert actions == sorted(actions)

    denied = await client.get("%s/audit/actions" % api, headers=member_a.headers)
    assert denied.status_code == 403


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
# Two gates in this file are the kind that pass silently when they stop working, so
# both were broken on purpose and re-run before this file was committed:
#
# * emptying ``REDACTED_META_KEY_PARTS`` and ``REDACTED_META_KEYS`` makes
#   ``test_meta_that_looks_like_a_credential_is_redacted`` fail on the first key;
# * stubbing ``_ActorOpRepository.anonymise_actor`` to return 0 makes
#   ``test_erasure_keeps_every_op_and_only_drops_the_actor`` fail on ``opsAnonymised``.
#   Making it report the right count while doing no work did NOT turn that test red —
#   ``ops.actor ON DELETE SET NULL`` was covering for it — which is why
#   ``test_op_actors_are_cleared_by_the_erasure_itself`` exists and is asserted before
#   any row is deleted.
#
# The verbatim red output is in the agent's report; if you change either gate, break it
# again and check that the test still notices.
