"""The seed's stale-demo auto-migration (``runner._demo_seed_is_stale``).

An already-deployed environment keeps whatever demo the seed wrote at the time —
idempotency by op-log head means an improved seed (2026-08-27: the infeasible
16-room brief → the solver-feasible 3BHK) never reaches it. The migration closes
that gap, under one hard constraint: a demo any human has ever edited is theirs,
and must never be rebuilt.

Three behaviours, each with the failure mode it guards:

1. untouched + differing → rebuilt (the healing case);
2. untouched + identical → reused (a spurious ``True`` here would rebuild the
   demo on EVERY boot — the constant-reseed loop);
3. one human op → reused forever, stale or not (rebuilding would destroy work).
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api import models
from garh_api.seed.runner import RECREATED, REUSED, SeedOptions, seed
from sqlalchemy import select, update

pytestmark = pytest.mark.usefixtures("clean_db")


async def _demo_ops(session: Any, project_id: Any) -> list[models.Op]:
    rows = await session.execute(
        select(models.Op).where(models.Op.project_id == project_id).order_by(models.Op.idx)
    )
    return list(rows.scalars())


async def test_untouched_but_differing_demo_is_rebuilt(session: Any) -> None:
    first = await seed(session, SeedOptions())
    await session.commit()

    # Simulate "a previous seed authored different content": rewrite one seed op's
    # payload in place. Source and client_op_id stay seed-shaped — this is exactly
    # what an old deployment's op log looks like after the seed's content improves.
    ops = await _demo_ops(session, first.project_id)
    assert ops, "the seed must have written an op log"
    await session.execute(
        update(models.Op)
        .where(models.Op.seq == ops[-1].seq)
        .values(payload={"stale": "previous-seed-content"})
    )
    await session.commit()

    second = await seed(session, SeedOptions())
    await session.commit()

    assert second.steps["demoProject"] == RECREATED
    assert second.project_id != first.project_id, "rebuild must be a fresh project row"
    rebuilt = await _demo_ops(session, second.project_id)
    assert rebuilt, "the rebuilt demo must carry a fresh op log"
    assert all(op.source == "system" for op in rebuilt)


async def test_untouched_and_identical_demo_is_reused_not_reseeded(session: Any) -> None:
    """The constant-reseed-loop guard: identical content must read as NOT stale."""
    first = await seed(session, SeedOptions())
    await session.commit()

    second = await seed(session, SeedOptions())
    await session.commit()

    assert second.steps["demoProject"] == REUSED
    assert second.steps["opLog"] == REUSED
    assert second.project_id == first.project_id


async def test_one_human_op_makes_the_demo_permanently_hands_off(session: Any) -> None:
    first = await seed(session, SeedOptions())
    await session.commit()

    # Make the content stale AND add one human-authored op. Staleness must lose.
    ops = await _demo_ops(session, first.project_id)
    await session.execute(
        update(models.Op)
        .where(models.Op.seq == ops[0].seq)
        .values(payload={"stale": "previous-seed-content"})
    )
    await session.execute(
        update(models.Op).where(models.Op.seq == ops[-1].seq).values(source="manual")
    )
    await session.commit()

    second = await seed(session, SeedOptions())
    await session.commit()

    assert second.steps["demoProject"] == REUSED
    assert second.project_id == first.project_id
    kept = await _demo_ops(session, first.project_id)
    assert kept[0].payload == {"stale": "previous-seed-content"}, "an edited demo is theirs"
