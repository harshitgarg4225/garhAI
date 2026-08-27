"""``POST /projects/:id/solve`` must enqueue what the solver actually consumes.

The API and the solver worker were written to different contracts and never ran
together: the enqueue payload carried only ``{lockedRoomIds, optionCount,
storeys, versionBranch}`` while ``services/solver/handler._parse_params``
requires ``plot``, ``profile`` and ``brief`` — so every job died at the worker
with "This solve request is missing some of its details."

These tests hold the two sides to ONE contract by parsing the actually-enqueued
envelope through the worker's own parser. Per the repo rule about checks that
cannot fail: the pre-fix payload FAILS the main test (and
``test_the_old_payload_shape_cannot_parse`` keeps that provable), so a
regression to two contracts goes red instead of green.

The demo op log (30×40 ft Bengaluru, G+1, 3BHK — ``garh_api.seed.demo``) is the
fixture, appended through the real sequencer so the folded document is exactly
what the live demo project holds.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REQUIRE_INTEGRATION
from tests.helpers import problem

pytestmark = pytest.mark.integration

#: The api tests run from ``apps/api``; the worker's parser lives at the repo
#: root under ``services/``. Both roots go on the path so the test parses the
#: payload with the real consumer, not a replica of it.
REPO_ROOT = Path(__file__).resolve().parents[3]


def _worker_parse_params() -> Callable[..., Any]:
    for root in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "api")):
        if root not in sys.path:
            sys.path.insert(0, root)
    try:
        from services.solver.handler import _parse_params
    except ImportError as exc:  # pragma: no cover - environment, not product
        message = (
            "services.solver.handler is not importable from the api tests (%s) — "
            "the enqueue-contract assertion cannot run." % exc
        )
        if REQUIRE_INTEGRATION:
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    return _parse_params


async def _seed_demo_document(client: Any, api: str, actor: Any, project_id: Any) -> None:
    """Append the demo plot + brief + storeys through the real sequencer."""
    from garh_api.seed.demo import demo_op_log, load_demo_brief

    response = await client.post(
        "%s/projects/%s/ops" % (api, project_id),
        json={"ops": demo_op_log(load_demo_brief()), "baseIdx": -1, "source": "manual"},
        headers=actor.headers,
    )
    assert response.status_code == 200, response.text


def _enqueued_payload(clean_redis: Any, settings: Any) -> dict[str, Any]:
    raw = clean_redis.lindex(settings.queue_solver, 0)
    assert raw is not None, "nothing was enqueued on %s" % settings.queue_solver
    envelope = json.loads(raw)
    payload = envelope.get("payload")
    assert isinstance(payload, dict), envelope
    return payload


# ---------------------------------------------------------------------------
# The contract: the enqueued envelope parses through the worker's own parser
# ---------------------------------------------------------------------------


async def test_solve_enqueues_what_the_worker_parses(
    client: Any,
    api: str,
    clean_redis: Any,
    settings: Any,
    firm_a: Any,
    project_a: Any,
) -> None:
    """The whole point: ``_parse_params`` accepts the payload the API enqueues.

    This test fails against the old payload — the parser raises InvalidJobError
    on the first missing object — so the two contracts cannot drift apart again
    without a red test.
    """
    parse_params = _worker_parse_params()
    await _seed_demo_document(client, api, firm_a, project_a.id)

    response = await client.post(
        "%s/projects/%s/solve" % (api, project_a.id),
        json={"optionCount": 3, "seed": 7},
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text

    payload = _enqueued_payload(clean_redis, settings)
    params = parse_params(payload, kind="solver.generate")

    # Plot: the demo 30×40 ft rectangle, one edge per side, the 9 m road south.
    assert len(params.plot_polygon) == 4
    assert len(params.edges) == 4
    assert params.edges[0].role == "front"
    assert params.edges[0].road_width_mm == 9000
    # blr pack: the roaded front edge carries a real setback requirement.
    assert params.edges[0].setback_mm > 0
    assert all(edge.setback_mm >= 0 for edge in params.edges)

    # Profile: real pack-derived limits, not the "unregulated" ceilings — every
    # one strictly below its ceiling proves it came from a rule, not a default.
    from garh_api.solver_enqueue import (
        UNREGULATED_COVERAGE_PERCENT,
        UNREGULATED_FAR_X100,
        UNREGULATED_FLOORS,
        UNREGULATED_HEIGHT_MM,
    )

    assert params.profile.city_pack == "blr"
    assert 1 <= params.profile.coverage_percent < UNREGULATED_COVERAGE_PERCENT
    assert 1 <= params.profile.far_x100 < UNREGULATED_FAR_X100
    assert 1 <= params.profile.max_height_mm < UNREGULATED_HEIGHT_MM
    assert 1 <= params.profile.max_floors < UNREGULATED_FLOORS

    # Brief: the demo 3BHK expands per count with program.py's key scheme, and
    # the G+1 document resolves to two storeys.
    keys = [room.key for room in params.rooms]
    assert len(keys) == len(set(keys)), keys
    assert {"living_dining", "kitchen", "guest_bedroom", "bedroom_master", "bedroom"} <= set(keys)
    assert params.storeys == 2
    # The seed authors the demo brief with vastu off (garh_api.seed.demo says why).
    assert params.vastu_mode == "off"

    # The request's own knobs still ride along.
    assert params.seed == 7
    assert params.target_option_count == 3
    assert payload["versionBranch"], payload


async def test_the_old_payload_shape_cannot_parse() -> None:
    """Negative control: the pre-fix payload is exactly what the worker refuses.

    Keeps the main test falsifiable — if ``_parse_params`` ever started
    accepting a plotless payload, the contract assertion above would stop
    meaning anything, and this test would say so.
    """
    parse_params = _worker_parse_params()
    from services.common.errors import InvalidJobError

    old_payload = {
        "lockedRoomIds": [],
        "optionCount": 3,
        "storeys": 2,
        "versionBranch": str(uuid.uuid4()),
    }
    with pytest.raises(InvalidJobError):
        parse_params(old_payload, kind="solver.generate")


# ---------------------------------------------------------------------------
# Missing inputs are a 4xx at the API, never a doomed enqueue
# ---------------------------------------------------------------------------


async def test_solve_without_a_plot_is_a_409_and_enqueues_nothing(
    client: Any,
    api: str,
    clean_redis: Any,
    settings: Any,
    firm_a: Any,
    project_a: Any,
) -> None:
    response = await client.post(
        "%s/projects/%s/solve" % (api, project_a.id),
        json={"optionCount": 3},
        headers=firm_a.headers,
    )
    assert response.status_code == 409, response.text
    body = problem(response)
    assert body["code"] == "no_plot_boundary", body

    assert clean_redis.llen(settings.queue_solver) == 0
    jobs = await client.get(
        "%s/projects/%s/solver-jobs" % (api, project_a.id), headers=firm_a.headers
    )
    assert jobs.status_code == 200, jobs.text
    assert jobs.json()["items"] == [], "a refused solve must not leave a job row"


async def test_solve_with_plot_but_no_brief_rooms_is_a_409(
    client: Any,
    api: str,
    clean_redis: Any,
    settings: Any,
    firm_a: Any,
    project_a: Any,
) -> None:
    from garh_api.seed.demo import demo_op_log, load_demo_brief

    plot_only = [op for op in demo_op_log(load_demo_brief()) if not op["type"].startswith("brief.")]
    response = await client.post(
        "%s/projects/%s/ops" % (api, project_a.id),
        json={"ops": plot_only, "baseIdx": -1, "source": "manual"},
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text

    response = await client.post(
        "%s/projects/%s/solve" % (api, project_a.id),
        json={"optionCount": 3},
        headers=firm_a.headers,
    )
    assert response.status_code == 409, response.text
    body = problem(response)
    assert body["code"] == "no_brief_rooms", body
    assert clean_redis.llen(settings.queue_solver) == 0
