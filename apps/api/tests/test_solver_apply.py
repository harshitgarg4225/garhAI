"""``solver.apply_option`` (§4 op 31): server-side expansion through the sequencer.

What op 31 promises, and which test pins each promise:

* **The server's ops, never the client's** — the expansion is rebuilt from the stored
  ``solver_jobs.options`` row; whatever ``payload.ops`` the client sent is discarded
  (``test_client_supplied_ops_are_discarded``).
* **Same validate+fold path as any op batch** — the expanded op folds through the
  ordinary sequencer and lands with the exact ``stateHash`` an independent fold of the
  stored option's ops produces (``test_apply_option_folds_clean_and_matches_state_hash``).
  There is no side door to prove absent, so the test proves the front door: the hash.
* **Dependency order** — a stored option whose ops arrive scrambled still folds,
  because the expansion sorts storey → wall → opening → stair → room
  (``test_apply_option_orders_ops_by_dependency`` + the unit tests on
  :func:`dependency_order`).
* **Idempotent via clientOpId** — re-applying the same option is a replay no-op even
  when the client sent no ``clientOpId`` (the derived one fills in)
  (``test_reapply_is_a_replay_noop``).
* **Cross-tenant scoped** — a foreign firm's job id is a 404, and so is a job
  belonging to a *different project of the same firm*
  (``test_foreign_job_id_is_404``, ``test_same_firm_other_project_job_is_404``).
* **Snapshot afterwards** — the append that applies an option pins a ``kind='option'``
  design version, readable back through ``GET /model?version=``
  (``test_apply_option_snapshots_a_design_version``).

Integration tests need the real Postgres + Redis (see ``conftest.py``); the unit tests
at the bottom need neither and always run.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from garh_api.repositories import SolverJobRepository
from garh_api.schemas.ops import MAX_CLIENT_OP_ID_LENGTH
from garh_api.solver_apply import dependency_order, derived_client_op_id
from garh_model import apply_group, state_hash
from garh_model.model import empty_project_doc
from garh_model.testing import FIXTURE_IDS

from tests import factories
from tests.helpers import op_payload, problem

# ---------------------------------------------------------------------------
# Fixture PlanOption — the op set a solver job stores for one option
# ---------------------------------------------------------------------------


def _wall(
    key: str, a: tuple[int, int], b: tuple[int, int], thickness: int, kind: str
) -> dict[str, Any]:
    return {
        "type": "wall.add",
        "payload": {
            "id": FIXTURE_IDS[key],
            "storeyId": FIXTURE_IDS["groundStorey"],
            "a": {"x": a[0], "y": a[1]},
            "b": {"x": b[0], "y": b[1]},
            "thicknessMm": thickness,
            "kind": kind,
        },
    }


def _plan_option_ops() -> list[dict[str, Any]]:
    """A two-room ground floor with a main door, DELIBERATELY scrambled.

    The door precedes its wall and the storey comes last — the order a solver could
    plausibly emit and the model core can never fold. If these apply, it is because
    the server expansion re-sorted them into dependency order; nothing else could.
    Geometry mirrors ``garh_model.testing.two_room_plan_ops`` (integer mm).
    """
    return [
        {
            "type": "opening.add",
            "payload": {
                "id": FIXTURE_IDS["doorMain"],
                "wallId": FIXTURE_IDS["wallSouth"],
                "kind": "door",
                "widthMm": 900,
                "heightMm": 2100,
                "sillMm": 0,
                "offsetMm": 1500,
                "swing": "in-left",
            },
        },
        _wall("wallSouth", (0, 0), (6000, 0), 230, "external"),
        _wall("wallEast", (6000, 0), (6000, 4000), 230, "external"),
        _wall("wallNorth", (6000, 4000), (0, 4000), 230, "external"),
        _wall("wallWest", (0, 4000), (0, 0), 230, "external"),
        _wall("wallSpine", (3000, 0), (3000, 4000), 115, "internal"),
        {
            "type": "storey.add",
            "payload": {
                "id": FIXTURE_IDS["groundStorey"],
                "index": 0,
                "name": "Ground Floor",
                "heightMm": 3000,
            },
        },
    ]


def _plan_option(**extra: Any) -> dict[str, Any]:
    """The stored PlanOption JSON shape ``SolverJobRepository.succeed`` persists."""
    option: dict[str, Any] = {
        "id": "plan_fixture0000000",
        "rank": 0,
        "scores": {"composite": 72, "circulationPercent": 11},
        "ops": _plan_option_ops(),
        "signature": ["stair:anchor-e0", "bedroom@SW"],
        "stairAnchorId": "anchor-e0",
    }
    option.update(extra)
    return option


def _expected_state_hash() -> str:
    """Fold the stored option's ops (dependency-ordered) with the model core itself.

    This is the independent answer the sequencer must reproduce: same empty document,
    same ops, same canonical JSON hash. Any divergence — a skipped validation, a
    mutated payload, a different fold path — changes the hash.
    """
    ordered = dependency_order(_plan_option_ops())
    folded = apply_group(empty_project_doc(), ordered)
    return state_hash(folded.model)


async def _succeeded_job(
    session: Any, actor: Any, project_id: Any, options: list[dict[str, Any]]
) -> Any:
    job = await factories.create_solver_job(session, actor, project_id)
    updated = await SolverJobRepository(session, actor.ctx()).succeed(job.id, options)
    await session.commit()
    return updated


def _apply_body(
    job_id: Any, option_index: int = 0, *, base_idx: int = -1, **op_extra: Any
) -> dict[str, Any]:
    apply_op = op_payload("solver.apply_option", solverJobId=str(job_id), optionIndex=option_index)
    apply_op.update(op_extra)
    return {"ops": [apply_op], "baseIdx": base_idx, "source": "solver"}


async def _post_ops(
    client: Any, api: str, project_id: Any, headers: Any, body: dict[str, Any]
) -> Any:
    return await client.post("%s/projects/%s/ops" % (api, project_id), json=body, headers=headers)


# ---------------------------------------------------------------------------
# Expansion + fold + state hash
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_apply_option_folds_clean_and_matches_state_hash(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    job = await _succeeded_job(session, firm_a, project_a.id, [_plan_option()])

    response = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body(job.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["alreadyApplied"] is False
    # Op 31 lands as ONE op — the group — not as its inner ops spread on the log.
    assert [op["type"] for op in body["applied"]] == ["solver.apply_option"]
    # §4: a solver application is a single (non-null) undo group.
    assert body["applied"][0]["groupId"] is not None
    # The authoritative fold matches an independent fold of the stored option.
    assert body["stateHash"] == _expected_state_hash()


@pytest.mark.integration
async def test_apply_option_orders_ops_by_dependency(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """The stored ops are scrambled (door first, storey last); only the server's
    dependency sort makes them foldable. A 422 here means the sort is gone."""
    job = await _succeeded_job(session, firm_a, project_a.id, [_plan_option()])

    response = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body(job.id))
    assert response.status_code == 200, response.text

    # The persisted op carries the expansion in fold order: storey before walls
    # before openings — replay (GET /ops) must never depend on a re-sort.
    listed = await client.get(
        "%s/projects/%s/ops" % (api, project_a.id),
        params={"since": -1},
        headers=firm_a.headers,
    )
    assert listed.status_code == 200, listed.text
    stored = listed.json()["ops"][0]["payload"]["ops"]
    types = [item["type"] for item in stored]
    assert types[0] == "storey.add"
    assert types[-1] == "opening.add"
    assert types.count("wall.add") == 5


@pytest.mark.integration
async def test_client_supplied_ops_are_discarded(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """A client cannot smuggle geometry through op 31: whatever ``payload.ops`` the
    request carries, the expansion is rebuilt from the stored job row."""
    job = await _succeeded_job(session, firm_a, project_a.id, [_plan_option()])

    body = _apply_body(job.id)
    # A hostile payload that could never fold (wall on a storey that will not exist).
    body["ops"][0]["payload"]["ops"] = [
        {
            "type": "wall.add",
            "payload": {
                "id": FIXTURE_IDS["wallSouth"],
                "storeyId": "storey_01J000000000000000000NOPE",
                "a": {"x": 0, "y": 0},
                "b": {"x": 1, "y": 0},
                "thicknessMm": 230,
                "kind": "external",
            },
        }
    ]
    response = await _post_ops(client, api, project_a.id, firm_a.headers, body)
    assert response.status_code == 200, response.text
    # The fold succeeded AND produced the stored option's state — both are only
    # possible if the client's ops were thrown away.
    assert response.json()["stateHash"] == _expected_state_hash()


@pytest.mark.integration
async def test_locked_room_ids_survive_into_the_op_log(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """§5.7: a partial re-solve's locked ids ride along on the applied op, so the
    op log records which rooms this application promised not to touch."""
    option = _plan_option(lockedRoomIds=["room_01J0000000000000000000LCK"])
    job = await _succeeded_job(session, firm_a, project_a.id, [option])

    response = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body(job.id))
    assert response.status_code == 200, response.text

    listed = await client.get(
        "%s/projects/%s/ops" % (api, project_a.id),
        params={"since": -1},
        headers=firm_a.headers,
    )
    payload = listed.json()["ops"][0]["payload"]
    assert payload["lockedRoomIds"] == ["room_01J0000000000000000000LCK"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_reapply_is_a_replay_noop(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """Same job, same option, no clientOpId anywhere: the derived idempotency key
    must make the second request a replay, not a second application."""
    job = await _succeeded_job(session, firm_a, project_a.id, [_plan_option()])

    first = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body(job.id))
    assert first.status_code == 200, first.text
    first_body = first.json()

    second = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body(job.id))
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["alreadyApplied"] is True
    assert second_body["headIdx"] == first_body["headIdx"]

    # HEAD did not advance: the log holds exactly one op.
    listed = await client.get(
        "%s/projects/%s/ops" % (api, project_a.id),
        params={"since": -1},
        headers=firm_a.headers,
    )
    assert len(listed.json()["ops"]) == 1


# ---------------------------------------------------------------------------
# Scoping: 404s that keep one project's geometry out of another
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_foreign_job_id_is_404(
    client: Any, api: str, session: Any, firm_a: Any, firm_b: Any, project_a: Any
) -> None:
    """Firm B's solver job applied into firm A's project: 404, indistinguishable
    from a job that does not exist (§13 tenancy)."""
    project_b = await factories.create_project(session, firm_b, name="Iyer Villa")
    foreign_job = await _succeeded_job(session, firm_b, project_b.id, [_plan_option()])

    response = await _post_ops(
        client, api, project_a.id, firm_a.headers, _apply_body(foreign_job.id)
    )
    assert response.status_code == 404, response.text
    problem(response)


@pytest.mark.integration
async def test_same_firm_other_project_job_is_404(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """A job of ANOTHER project of the same firm is also a 404 — op 31 must not
    replay one project's plan into a different project."""
    other = await factories.create_project(session, firm_a, name="Rao Annexe")
    other_job = await _succeeded_job(session, firm_a, other.id, [_plan_option()])

    response = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body(other_job.id))
    assert response.status_code == 404, response.text
    problem(response)


@pytest.mark.integration
async def test_unparseable_job_id_is_404(
    client: Any, api: str, firm_a: Any, project_a: Any, clean_db: None, clean_redis: Any
) -> None:
    response = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body("not-a-uuid"))
    assert response.status_code == 404, response.text
    problem(response)


# ---------------------------------------------------------------------------
# Jobs that cannot be applied: honest 409s
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unfinished_job_is_409(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    job = await factories.create_solver_job(session, firm_a, project_a.id)  # queued

    response = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body(job.id))
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "solver_option_not_applicable"


@pytest.mark.integration
async def test_option_index_out_of_range_is_409(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    job = await _succeeded_job(session, firm_a, project_a.id, [_plan_option()])

    response = await _post_ops(
        client, api, project_a.id, firm_a.headers, _apply_body(job.id, option_index=5)
    )
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "solver_option_not_applicable"


# ---------------------------------------------------------------------------
# Snapshot afterwards
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_apply_option_snapshots_a_design_version(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """Accepting a plan is a named moment: the append pins a design version whose
    snapshot folds to the same state the append reported."""
    job = await _succeeded_job(session, firm_a, project_a.id, [_plan_option()])

    response = await _post_ops(client, api, project_a.id, firm_a.headers, _apply_body(job.id))
    assert response.status_code == 200, response.text
    body = response.json()
    version_id = body["snapshotVersionId"]
    assert version_id, "op 31 must snapshot afterwards (§4)"

    pinned = await client.get(
        "%s/projects/%s/model" % (api, project_a.id),
        params={"version": version_id},
        headers=firm_a.headers,
    )
    assert pinned.status_code == 200, pinned.text
    pinned_body = pinned.json()
    assert pinned_body["stateHash"] == body["stateHash"]
    assert pinned_body["baseIdx"] == body["headIdx"]
    assert pinned_body["snapshot"] is not None


# ---------------------------------------------------------------------------
# Unit tests — no datastore, always run
# ---------------------------------------------------------------------------


def test_dependency_order_sorts_into_fold_bands() -> None:
    scrambled = _plan_option_ops()
    ordered = dependency_order(scrambled)
    types = [item["type"] for item in ordered]
    assert types[0] == "storey.add"
    assert types[-1] == "opening.add"
    # Stable within a band: the five walls keep their original relative order.
    walls = [item["payload"]["id"] for item in ordered if item["type"] == "wall.add"]
    assert walls == [
        FIXTURE_IDS["wallSouth"],
        FIXTURE_IDS["wallEast"],
        FIXTURE_IDS["wallNorth"],
        FIXTURE_IDS["wallWest"],
        FIXTURE_IDS["wallSpine"],
    ]


def test_dependency_order_is_deterministic_and_pure() -> None:
    scrambled = _plan_option_ops()
    once = dependency_order(scrambled)
    twice = dependency_order(list(scrambled))
    assert once == twice
    # Unknown op types sink to the last band instead of raising.
    mixed = dependency_order([{"type": "future.op", "payload": {}}, scrambled[-1]])
    assert [item["type"] for item in mixed] == ["storey.add", "future.op"]


def test_derived_client_op_id_is_deterministic_and_fits_the_column() -> None:
    job_id = uuid.UUID("00000000-0000-4000-8000-000000000042")
    key = derived_client_op_id(job_id, 2)
    assert key == derived_client_op_id(job_id, 2)
    assert key != derived_client_op_id(job_id, 3)
    assert key.startswith("sapply-")
    assert len(key) <= MAX_CLIENT_OP_ID_LENGTH
