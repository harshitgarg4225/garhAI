"""The op sequencer: ``POST /projects/:id/ops`` (playbook §4, §11).

Golden rule 1 — *"the op is the atom"* — means this endpoint is the only way a design ever
changes. Everything else in the product (canvas drag, copilot, solver, plot form) funnels
through it, so its four behaviours are load-bearing for the whole app:

1. **append** — ops land in order and HEAD advances by exactly the number appended;
2. **stale ``baseIdx`` → 409** with ``headIdx``, which is what the client rebases against;
3. **``clientOpId`` replay is idempotent** — a retried request must not apply the ops twice;
4. **snapshots at the 200-op boundary**, so "open project → interactive" stays snapshot+tail;
5. **single writer per branch** — two concurrent appends at the same base cannot both land.

(3) deserves a note on why it is not "nice to have": the client applies ops optimistically
and retries on any network error it cannot classify. Without idempotency, one dropped
response turns a single wall into two walls stacked on each other — a corruption the user
sees days later and cannot explain.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from garh_api.config import Settings
from garh_model.testing import FIXTURE_IDS
from tests.helpers import main_branch, op_payload, problem, sorted_codes

pytestmark = pytest.mark.integration


def _body(ops: list[dict[str, Any]], base_idx: int, **extra: Any) -> dict[str, Any]:
    return {"ops": ops, "baseIdx": base_idx, "source": "manual", **extra}


def _north(deg: int) -> dict[str, Any]:
    """The cheapest valid op there is: one integer field, no dependencies, no geometry."""
    return op_payload("plot.set_north", deg=deg)


async def _append(client: Any, api: str, project_id: Any, headers: Any, **kwargs: Any) -> Any:
    return await client.post(
        "%s/projects/%s/ops" % (api, project_id), json=_body(**kwargs), headers=headers
    )


# ---------------------------------------------------------------------------
# 1. Append
# ---------------------------------------------------------------------------


async def test_append_assigns_contiguous_indexes(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    first = await _append(
        client, api, project_a.id, firm_a.headers, ops=[_north(0)], base_idx=-1
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["firstIdx"] == 0
    assert body["lastIdx"] == 0
    assert body["headIdx"] == 0
    assert body["alreadyApplied"] is False
    assert len(body["stateHash"]) == 64, "stateHash must be 64 lowercase hex chars"
    assert body["stateHash"] == body["stateHash"].lower()

    second = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[_north(90), _north(180)],
        base_idx=0,
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert [op["idx"] for op in body["applied"]] == [1, 2]
    assert body["headIdx"] == 2
    # A multi-op append is one undo unit, so the server must group it (§4).
    group_ids = {op["groupId"] for op in body["applied"]}
    assert len(group_ids) == 1 and None not in group_ids, body["applied"]


async def test_appended_ops_come_back_from_the_sync_endpoint(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """``GET /ops?since=`` is what a 409'd client calls; it must return the real tail."""
    await _append(client, api, project_a.id, firm_a.headers, ops=[_north(0)], base_idx=-1)
    await _append(client, api, project_a.id, firm_a.headers, ops=[_north(45)], base_idx=0)

    response = await client.get(
        "%s/projects/%s/ops" % (api, project_a.id), params={"since": 0}, headers=firm_a.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [op["idx"] for op in body["ops"]] == [1]
    assert body["headIdx"] == 1
    assert body["sinceIdx"] == 0
    assert body["ops"][0]["payload"] == {"deg": 45}
    assert body["ops"][0]["source"] == "manual"
    # Provenance: the op is attributed to the user who sent it (§4).
    assert body["ops"][0]["actor"] == str(firm_a.user_id)


async def test_an_invalid_op_is_rejected_with_machine_readable_issues(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """422 ``op_rejected`` carrying ``issues[]`` — the copilot's self-correction input."""
    response = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[
            op_payload(
                "wall.add",
                id=FIXTURE_IDS["wallSouth"],
                storeyId=FIXTURE_IDS["groundStorey"],  # never added
                a={"x": 0, "y": 0},
                b={"x": 4000, "y": 0},
                thicknessMm=230,
                kind="external",
            )
        ],
        base_idx=-1,
    )
    assert response.status_code == 422, response.text
    body = problem(response)
    assert body["code"] == "op_rejected"
    assert body["issues"], body
    assert "STOREY_UNKNOWN" in sorted_codes(body), body["issues"]
    assert body["opIndex"] == 0
    assert body["headIdx"] == -1


async def test_a_rejected_batch_lands_nothing(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """Atomic: a batch whose *second* op fails must not persist the first one."""
    response = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[_north(30), op_payload("plot.set_north", deg=900)],
        base_idx=-1,
    )
    assert response.status_code == 422, response.text

    head = await client.get("%s/projects/%s/branch" % (api, project_a.id), headers=firm_a.headers)
    assert head.status_code == 200, head.text
    assert head.json()["headIdx"] == -1, "a rejected batch left an op behind"


# ---------------------------------------------------------------------------
# 2. Stale baseIdx → 409
# ---------------------------------------------------------------------------


async def test_stale_base_idx_is_409_with_head_idx(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """The client's whole rebase strategy is built on this number being right."""
    await _append(client, api, project_a.id, firm_a.headers, ops=[_north(0)], base_idx=-1)

    stale = await _append(
        client, api, project_a.id, firm_a.headers, ops=[_north(180)], base_idx=-1
    )
    assert stale.status_code == 409, stale.text
    body = problem(stale)
    assert body["code"] == "op_sequence_conflict"
    assert body["headIdx"] == 0, body

    # Nothing was written, so rebasing onto headIdx succeeds.
    rebased = await _append(
        client, api, project_a.id, firm_a.headers, ops=[_north(180)], base_idx=body["headIdx"]
    )
    assert rebased.status_code == 200, rebased.text
    assert rebased.json()["headIdx"] == 1


async def test_base_idx_ahead_of_head_is_also_409(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """A client claiming a future base is as wrong as one claiming a past base."""
    response = await _append(
        client, api, project_a.id, firm_a.headers, ops=[_north(0)], base_idx=17
    )
    assert response.status_code == 409, response.text
    assert problem(response)["headIdx"] == -1


# ---------------------------------------------------------------------------
# 3. clientOpId replay
# ---------------------------------------------------------------------------


async def test_client_op_id_replay_is_idempotent(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """The identical request twice = the identical answer, and one op in the log."""
    ops = [dict(_north(0), clientOpId="op-a"), dict(_north(90), clientOpId="op-b")]

    first = await _append(client, api, project_a.id, firm_a.headers, ops=ops, base_idx=-1)
    assert first.status_code == 200, first.text
    assert first.json()["alreadyApplied"] is False
    assert first.json()["headIdx"] == 1

    replay = await _append(client, api, project_a.id, firm_a.headers, ops=ops, base_idx=-1)
    assert replay.status_code == 200, (
        "a retried append must be answered idempotently, not with a 409: %s" % replay.text
    )
    body = replay.json()
    assert body["alreadyApplied"] is True
    assert [op["idx"] for op in body["applied"]] == [0, 1]
    assert body["headIdx"] == 1

    tail = await client.get(
        "%s/projects/%s/ops" % (api, project_a.id), params={"since": -1}, headers=firm_a.headers
    )
    assert len(tail.json()["ops"]) == 2, "the replay applied the ops a second time"


async def test_partial_overlap_is_a_conflict_not_a_replay(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """Half-known ids mean the client's view of history is wrong — a genuine 409.

    Appending "the rest" would interleave two edit streams into a design nobody drew,
    which is worse than making the client re-read and rebase.
    """
    await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[dict(_north(0), clientOpId="op-a")],
        base_idx=-1,
    )

    mixed = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[dict(_north(0), clientOpId="op-a"), dict(_north(90), clientOpId="op-new")],
        base_idx=-1,
    )
    assert mixed.status_code == 409, mixed.text
    assert problem(mixed)["headIdx"] == 0


async def test_client_op_id_is_unique_per_project_not_globally(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """Two projects may legitimately use the same client-side op id ("op-1")."""
    from tests.factories import create_project

    other = await create_project(session, firm_a, name="Second project")

    for project_id in (project_a.id, other.id):
        response = await _append(
            client,
            api,
            project_id,
            firm_a.headers,
            ops=[dict(_north(0), clientOpId="op-1")],
            base_idx=-1,
        )
        assert response.status_code == 200, (project_id, response.text)
        assert response.json()["alreadyApplied"] is False


# ---------------------------------------------------------------------------
# 4. The 200-op snapshot boundary
# ---------------------------------------------------------------------------


async def test_snapshot_is_written_at_the_op_snapshot_interval(
    client: Any, api: str, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    """§4/§15: a checkpoint every ``op_snapshot_interval`` ops keeps open-project fast.

    ``plot.set_north`` is used ``interval`` times because it is idempotent in effect and
    valid in isolation — the test is about the sequencer's bookkeeping, not about geometry.
    """
    interval = settings.op_snapshot_interval
    assert interval == 200, "playbook §4 says 200; the assertions below assume it"

    below = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[_north(i % 360) for i in range(interval - 1)],
        base_idx=-1,
    )
    assert below.status_code == 200, below.text
    assert below.json()["headIdx"] == interval - 2
    assert below.json()["snapshotVersionId"] is None, "snapshotted early"

    crossing = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[_north(7)],
        base_idx=interval - 2,
    )
    assert crossing.status_code == 200, crossing.text
    body = crossing.json()
    assert body["headIdx"] == interval - 1
    snapshot_id = body["snapshotVersionId"]
    assert snapshot_id is not None, "no snapshot at the %d-op boundary" % interval

    # It is an *auto* checkpoint, it carries the state hash, and GET /model can anchor on
    # it — which is the only reason to write it at all.
    versions = await client.get(
        "%s/projects/%s/versions" % (api, project_a.id), headers=firm_a.headers
    )
    assert versions.status_code == 200, versions.text
    auto = [v for v in versions.json()["items"] if v["id"] == snapshot_id]
    assert auto and auto[0]["kind"] == "auto", versions.json()
    assert auto[0]["hasSnapshot"] is True

    model = await client.get(
        "%s/projects/%s/model" % (api, project_a.id), headers=firm_a.headers
    )
    assert model.status_code == 200, model.text
    state = model.json()
    assert state["baseIdx"] == interval - 1, "GET /model did not anchor on the snapshot"
    assert state["ops"] == [], "the tail should be empty right after a snapshot"
    assert state["snapshot"] is not None
    assert state["stateHash"] == body["stateHash"]

    # And the next op does not snapshot again.
    after = await _append(
        client, api, project_a.id, firm_a.headers, ops=[_north(11)], base_idx=interval - 1
    )
    assert after.status_code == 200, after.text
    assert after.json()["snapshotVersionId"] is None


# ---------------------------------------------------------------------------
# 5. Single writer per branch
# ---------------------------------------------------------------------------


async def test_concurrent_appends_at_the_same_base_cannot_both_land(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """D12 single-writer: the advisory lock serialises, the base check rejects the loser.

    Both requests are in flight before either commits. The lock (``pg_advisory_xact_lock``)
    means the second one reads HEAD *after* the first committed, sees ``baseIdx`` is stale,
    and 409s — instead of both inserting ``idx = 0`` and one dying on the unique index with
    a 500.
    """
    results = await asyncio.gather(
        _append(client, api, project_a.id, firm_a.headers, ops=[_north(10)], base_idx=-1),
        _append(client, api, project_a.id, firm_a.headers, ops=[_north(20)], base_idx=-1),
        return_exceptions=True,
    )
    for result in results:
        assert not isinstance(result, BaseException), result

    statuses = sorted(response.status_code for response in results)  # type: ignore[union-attr]
    assert statuses == [200, 409], "concurrent appends produced %s" % statuses

    tail = await client.get(
        "%s/projects/%s/ops" % (api, project_a.id), params={"since": -1}, headers=firm_a.headers
    )
    body = tail.json()
    assert len(body["ops"]) == 1, "both writers landed: %s" % body["ops"]
    assert body["headIdx"] == 0


@pytest.mark.parametrize("count", [1, 5])
async def test_ops_are_serialised_per_branch_not_per_firm(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, count: int
) -> None:
    """The lock is per (project, branch): two projects must not block each other."""
    from tests.factories import create_project

    other = await create_project(session, firm_a, name="Parallel project")

    results = await asyncio.gather(
        _append(
            client,
            api,
            project_a.id,
            firm_a.headers,
            ops=[_north(i) for i in range(count)],
            base_idx=-1,
        ),
        _append(
            client,
            api,
            other.id,
            firm_a.headers,
            ops=[_north(i) for i in range(count)],
            base_idx=-1,
        ),
    )
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert all(r.json()["headIdx"] == count - 1 for r in results)


# ---------------------------------------------------------------------------
# Guardrails around the endpoint
# ---------------------------------------------------------------------------


async def test_empty_op_list_is_rejected_at_the_boundary(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    response = await _append(client, api, project_a.id, firm_a.headers, ops=[], base_idx=-1)
    assert response.status_code == 422, response.text
    assert problem(response)["code"] == "validation_failed"


async def test_base_idx_below_minus_one_is_rejected(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """``-1`` means "empty branch"; anything lower is a client bug, not a rebase."""
    response = await _append(
        client, api, project_a.id, firm_a.headers, ops=[_north(0)], base_idx=-2
    )
    assert response.status_code == 422, response.text


async def test_more_ops_than_the_per_request_limit_is_refused(
    client: Any, api: str, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    """A 10,000-op paste must not be answered by folding for a minute."""
    response = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[_north(i % 360) for i in range(settings.max_ops_per_append + 1)],
        base_idx=-1,
    )
    # 422 from the schema's max_length, or 400 from the handler's own guard — both are the
    # contract ("split the change into smaller batches"), and which one fires depends on
    # whether the schema limit and the settings limit agree.
    assert response.status_code in (400, 422), response.text
    body = problem(response)
    assert body["code"] in ("validation_failed", "too_many_ops"), body


async def test_unknown_op_type_is_rejected_not_stored(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """The op taxonomy is closed (§4). An unknown type is a client bug, not an extension."""
    response = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[op_payload("wall.teleport", wallId="nope")],
        base_idx=-1,
    )
    assert response.status_code == 422, response.text
    body = problem(response)
    assert body["code"] == "op_rejected"
    assert body["issues"], body


async def test_explicit_version_branch_is_honoured(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """A client that names the branch it read from must write to that branch."""
    branch = main_branch(project_a.id)
    response = await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[_north(0)],
        base_idx=-1,
        versionBranch=str(branch),
    )
    assert response.status_code == 200, response.text
    assert response.json()["versionBranch"] == str(branch)


async def test_appending_to_an_unknown_project_is_404(
    client: Any, api: str, firm_a: Any
) -> None:
    response = await _append(
        client, api, uuid.uuid4(), firm_a.headers, ops=[_north(0)], base_idx=-1
    )
    assert response.status_code == 404, response.text
    assert problem(response)["code"] == "not_found"
