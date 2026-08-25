"""Test data builders.

Two firms, two users, and whatever rows a test needs inside them — built through the
**real repositories**, so a factory cannot create a row shape the product cannot.

Why tokens are minted directly instead of going through ``POST /auth/otp`` +
``/auth/verify``: most tests are not about authentication, and a six-round-trip sign-in in
every one of them would make the suite slow and would couple every failure to the auth
flow. ``tests/test_auth_flow.py`` exercises the real flow end to end; everything else takes
:func:`access_token`, which signs the same claims the same way (``garh_api.security``) and
therefore proves the same thing about ``require_tenant``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.config import Settings, get_settings
from garh_model import empty_project_doc
from garh_api.repositories import (
    AuthDirectoryRepository,
    CommentRepository,
    DesignVersionRepository,
    OpRepository,
    ProjectRepository,
    RenderJobRepository,
    ShareLinkRepository,
    SolverJobRepository,
    UserRepository,
)
from garh_api.repositories.domain import NewOp, Project
from garh_api.security import create_access_token, generate_opaque_token
from garh_api.tenancy import TenantCtx


@dataclass(frozen=True)
class Actor:
    """A signed-in user of one firm, with everything a test needs to act as them."""

    firm_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: str
    firm_name: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer %s" % self.token}

    def ctx(self, *, request_id: str | None = "test") -> TenantCtx:
        return TenantCtx(
            firm_id=self.firm_id,
            user_id=self.user_id,
            role=self.role,
            request_id=request_id,
        )


def access_token(
    *,
    firm_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "admin",
    generation: int = 0,
    settings: Settings | None = None,
) -> str:
    """Sign an access token for a user.

    ``generation=0`` matches a Redis with no ``garh:auth:gen:*`` key, which is what a
    freshly-purged test Redis has — so the token verifies unless the test itself calls
    ``logout-all``.
    """
    token, _expires = create_access_token(
        user_id=user_id,
        firm_id=firm_id,
        role=role,
        generation=generation,
        settings=settings or get_settings(),
    )
    return token


async def create_firm(
    session: AsyncSession,
    *,
    firm_name: str = "Studio One",
    email: str | None = None,
    name: str = "Asha Rao",
    role: str = "admin",
) -> Actor:
    """Create a firm and its first admin through the signup path, then commit.

    Commits deliberately: an HTTP request opens its own session, and an uncommitted firm
    would be invisible to it — which looks exactly like a tenancy bug and wastes an hour.
    """
    clean_email = email or "owner-%s@studio.test" % uuid.uuid4().hex[:10]
    principal = await AuthDirectoryRepository(session).create_firm_with_owner(
        firm_name=firm_name, email=clean_email, name=name
    )
    await session.commit()
    actor = Actor(
        firm_id=principal.firm_id,
        user_id=principal.user_id,
        email=principal.email,
        role=principal.role,
        firm_name=principal.firm_name,
        token=access_token(firm_id=principal.firm_id, user_id=principal.user_id, role=role),
    )
    return actor


async def add_member(
    session: AsyncSession,
    admin: Actor,
    *,
    email: str | None = None,
    name: str = "Rahul Verma",
    role: str = "member",
) -> Actor:
    """Add a second seat to an existing firm (admin-only, as the product enforces)."""
    user = await UserRepository(session, admin.ctx()).create(
        email=email or "member-%s@studio.test" % uuid.uuid4().hex[:10],
        name=name,
        role=role,
    )
    await session.commit()
    return Actor(
        firm_id=admin.firm_id,
        user_id=user.id,
        email=user.email,
        role=user.role,
        firm_name=admin.firm_name,
        token=access_token(firm_id=admin.firm_id, user_id=user.id, role=user.role),
    )


async def create_project(
    session: AsyncSession,
    actor: Actor,
    *,
    name: str = "Sharma Residence",
    status: str = "draft",
    city_pack: str | None = "blr",
    demo: bool = False,
) -> Project:
    project = await ProjectRepository(session, actor.ctx()).create(
        name=name, status=status, city_pack=city_pack, demo=demo
    )
    await session.commit()
    return project


async def append_ops(
    session: AsyncSession,
    actor: Actor,
    project_id: uuid.UUID,
    ops: list[NewOp],
    *,
    base_idx: int = -1,
    branch: uuid.UUID | None = None,
    source: str = "manual",
) -> Any:
    """Append raw ops through the repository, skipping the model core.

    For sequencer tests that care about indexes, conflicts and idempotency rather than
    about geometry: the repository layer deliberately does not validate payloads (the
    model core does, one layer up), so a test can append ``{"type": "test.noop"}`` and
    still exercise the real advisory lock, the real unique index and the real 409.
    """
    from tests.helpers import main_branch  # local import: avoids a cycle at module load

    result = await OpRepository(session, actor.ctx()).append(
        project_id,
        branch or main_branch(project_id),
        base_idx,
        ops,
        source=source,
    )
    await session.commit()
    return result


async def seed_plot_and_brief(
    session: AsyncSession, actor: Actor, project_id: uuid.UUID
) -> None:
    """The demo plot + brief + storeys, appended as ops, so ``/solve`` has real inputs.

    Reuses the seeder's own op log (30×40 ft Bengaluru, G+1, 3BHK) rather than a
    hand-typed copy that could drift from what the product seeds. Raw repository
    append: these ops are valid — the tests that call this are about the routes
    that *fold* them, not about op validation.
    """
    from garh_api.seed.demo import demo_op_log, load_demo_brief

    ops = [
        NewOp(type=op["type"], payload=op["payload"])
        for op in demo_op_log(load_demo_brief())
    ]
    await append_ops(session, actor, project_id, ops)


async def create_version(
    session: AsyncSession,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    name: str = "Checkpoint",
    branch: uuid.UUID | None = None,
) -> Any:
    from tests.helpers import main_branch

    version = await DesignVersionRepository(session, actor.ctx()).create_named(
        project_id,
        name=name,
        version_branch=branch or main_branch(project_id),
        snapshot={
            "snapshotVersion": 1,
            "schemaVersion": 1,
            "versionBranch": str(branch or main_branch(project_id)),
            "atIdx": -1,
            "atSeq": None,
            "stateHash": None,
            # A REAL empty document, not {}: this snapshot becomes the fold
            # anchor for every later op append in the test, and the model
            # loader (rightly) refuses a document with no schemaVersion.
            "doc": empty_project_doc().to_json(),
        },
    )
    await session.commit()
    return version


async def create_solver_job(
    session: AsyncSession, actor: Actor, project_id: uuid.UUID
) -> Any:
    job = await SolverJobRepository(session, actor.ctx()).enqueue(project_id, params={})
    await session.commit()
    return job


async def create_render_job(
    session: AsyncSession, actor: Actor, project_id: uuid.UUID, *, mode: str = "precise"
) -> Any:
    job = await RenderJobRepository(session, actor.ctx()).enqueue(
        project_id, mode=mode, view={}, params={}
    )
    await session.commit()
    return job


async def create_share_link(
    session: AsyncSession, actor: Actor, project_id: uuid.UUID, *, can_comment: bool = True
) -> tuple[Any, str]:
    """Returns ``(link, plaintext_token)`` — the token is only ever returned once."""
    token = generate_opaque_token(32)
    link = await ShareLinkRepository(session, actor.ctx()).create(
        project_id, token=token, can_comment=can_comment
    )
    await session.commit()
    return link, token


async def create_comment(
    session: AsyncSession, actor: Actor, project_id: uuid.UUID, *, body: str = "Move the door"
) -> Any:
    comment = await CommentRepository(session, actor.ctx()).create(
        project_id, body=body, author_name="Asha Rao"
    )
    await session.commit()
    return comment


__all__ = [
    "Actor",
    "access_token",
    "add_member",
    "append_ops",
    "create_comment",
    "create_firm",
    "create_project",
    "create_render_job",
    "create_share_link",
    "create_solver_job",
    "create_version",
    "seed_plot_and_brief",
]
