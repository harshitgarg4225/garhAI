"""The seed runner (playbook §17). Idempotent, audited, refuses production by default.

    python -m garh_api.seed              # seed / re-seed everything that is missing
    python -m garh_api.seed --json       # same, machine-readable report
    python -m garh_api.seed --reset-demo # delete and rebuild the demo project
    python -m garh_api.seed --dry-run    # validate the seed data, write nothing

Ordering, and why it is this order:

1. **Validate every input first** — catalogue, rule packs, the seed-authored demo brief.
   All of it happens before the first write, so a typo in a fixture (or a float smuggled
   into the brief) cannot leave a half-seeded database behind.
2. **Firm + owner**, via the same ``AuthDirectoryRepository.create_firm_with_owner`` that
   signup uses. Keyed on ``demo@garh.ai``, which has a unique index — that key, not a
   "have I run before?" marker, is what makes this idempotent.
3. **Feature flags** (``§18``: read at boot) — ``seed_defaults`` only inserts missing keys,
   so a flag an operator flipped by hand survives a re-seed.
4. **Firm settings** — default city pack, title-block fields, and the resolved
   ``{pack: version}`` map plus the catalogue digest. That map is what lets a compliance
   report be traced back to the pack revision that produced it.
5. **Demo project** — created only if the firm has none, then its op log, then a named
   version with a folded snapshot.

Idempotency, concretely: re-running changes nothing and reports every step as ``reused``.
Each step decides for itself by looking for what it would create — there is no seed-marker
table, because a marker can be true while the rows it claims are gone.

Production: :class:`SeedOptions` refuses to run when ``APP_ENV`` is ``staging``/``prod``
unless ``allow_production=True`` (``--allow-production``, or ``GARH_SEED_ALLOW_PROD=1``).
The demo firm is a real tenant with a mailbox nobody owns; creating it in production is a
mistake, not a convenience.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.config import Settings, get_settings
from garh_api.logging import configure_logging, get_logger
from garh_api.repositories import (
    AuditLogRepository,
    AuthDirectoryRepository,
    BriefRepository,
    DesignVersionRepository,
    FirmRepository,
    FlagRepository,
    OpRepository,
    PlotRepository,
    ProjectRepository,
)
from garh_api.repositories.domain import AuthPrincipal
from garh_api.seed import demo as demo_data
from garh_api.seed.catalog import (
    CatalogBundle,
    RulepackRegistry,
    SeedDataError,
    load_catalog_bundle,
    load_rulepack_registry,
)
from garh_api.tenancy import TenantCtx

_log = get_logger(__name__)

#: Audit action written when a seed run completes.
#:
#: CONTRACT: not in ``repositories.audit_log.AUDIT_ACTIONS`` yet — ``record()`` accepts any
#: non-blank action, and this one belongs in that tuple next time it is touched (the auth
#: layer left three actions in the same position).
ACTION_SEED_COMPLETED = "seed.completed"

#: Env escape hatch for the production guard, for an operator who genuinely wants a demo
#: tenant in a staging environment.
ALLOW_PROD_ENV = "GARH_SEED_ALLOW_PROD"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

CREATED = "created"
REUSED = "reused"
SKIPPED = "skipped"
RECREATED = "recreated"


class SeedError(RuntimeError):
    """The seed cannot proceed. Always carries what to do about it."""


# ---------------------------------------------------------------------------
# Options and result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedOptions:
    """How to run. Defaults are what ``make seed`` and the CI e2e job want."""

    reset_demo: bool = False
    allow_production: bool = False
    dry_run: bool = False

    def assert_allowed(self, settings: Settings) -> None:
        if not settings.is_production:
            return
        env_ok = os.environ.get(ALLOW_PROD_ENV, "").strip().lower() in _TRUE_VALUES
        if self.allow_production or env_ok:
            _log.warning(
                "seed.production_override",
                env=settings.env,
                consequence="creating the demo tenant in a production environment",
            )
            return
        raise SeedError(
            "Refusing to seed with APP_ENV=%s. The demo firm is a real tenant with an "
            "email address nobody owns. Pass --allow-production (or set %s=1) if you "
            "really mean it." % (settings.env, ALLOW_PROD_ENV)
        )


@dataclass
class SeedResult:
    """What the run did. Printed as a report and asserted by ``tests/test_seed.py``."""

    firm_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None
    steps: dict[str, str] = field(default_factory=dict)
    ops_appended: int = 0
    head_idx: int = -1
    state_hash: str | None = None
    flags_created: int = 0
    catalog: dict[str, Any] = field(default_factory=dict)
    rulepacks: dict[str, str] = field(default_factory=dict)
    brief_source: str | None = None
    warnings: list[str] = field(default_factory=list)
    pending: list[dict[str, str]] = field(default_factory=list)
    dry_run: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "dryRun": self.dry_run,
            "firmId": None if self.firm_id is None else str(self.firm_id),
            "userId": None if self.user_id is None else str(self.user_id),
            "projectId": None if self.project_id is None else str(self.project_id),
            "versionId": None if self.version_id is None else str(self.version_id),
            "steps": dict(self.steps),
            "opsAppended": self.ops_appended,
            "headIdx": self.head_idx,
            "stateHash": self.state_hash,
            "flagsCreated": self.flags_created,
            "catalog": dict(self.catalog),
            "rulepacks": dict(self.rulepacks),
            "briefSource": self.brief_source,
            "warnings": list(self.warnings),
            "pending": list(self.pending),
        }

    def render(self) -> str:
        """A report an operator can read at a glance."""
        lines = ["", "Garh AI seed (playbook §17)%s" % ("  [DRY RUN]" if self.dry_run else "")]
        lines.append("-" * 62)
        for step, state in self.steps.items():
            lines.append("  %-22s %s" % (step, state))
        lines.append("")
        lines.append("  firm                   %s" % (self.firm_id or "-"))
        lines.append(
            "  user                   %s (%s)" % (self.user_id or "-", demo_data.DEMO_USER_EMAIL)
        )
        lines.append("  demo project           %s" % (self.project_id or "-"))
        lines.append("  version                %s" % (self.version_id or "-"))
        lines.append(
            "  op log                 %d op(s), head idx %d" % (self.ops_appended, self.head_idx)
        )
        lines.append("  state hash             %s" % (self.state_hash or "-"))
        counts = self.catalog.get("counts") or {}
        lines.append(
            "  catalogue              %s: %d furniture, %d materials, %d facade kits"
            % (
                self.catalog.get("source", "-"),
                counts.get("furniture", 0),
                counts.get("materials", 0),
                counts.get("facadeKits", 0),
            )
        )
        lines.append(
            "  rule packs             %s"
            % (", ".join("%s@%s" % (k, v) for k, v in sorted(self.rulepacks.items())) or "-")
        )
        lines.append("  brief                  %s" % (self.brief_source or "-"))
        lines.append("  flags inserted         %d" % self.flags_created)
        if self.warnings:
            lines.append("")
            lines.append("  Warnings")
            for warning in self.warnings:
                lines.append("    ! %s" % warning)
        if self.pending:
            lines.append("")
            lines.append("  Not seeded yet — later phases own these (§17):")
            for item in self.pending:
                lines.append(
                    "    · %-11s Phase %-2s %s"
                    % (item["item"], item["phase"], item["extensionPoint"])
                )
        lines.append("")
        lines.append(
            "  Sign in with %s — in dev the code is echoed by POST /auth/otp."
            % demo_data.DEMO_USER_EMAIL
        )
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


async def _ensure_firm_and_owner(session: AsyncSession, result: SeedResult) -> AuthPrincipal:
    """Find or create the demo firm and its admin. Keyed on the user's email."""
    directory = AuthDirectoryRepository(session)
    principal = await directory.find_principal_by_email(demo_data.DEMO_USER_EMAIL)
    if principal is not None:
        result.steps["firm"] = REUSED
        result.steps["user"] = REUSED
        return principal
    principal = await directory.create_firm_with_owner(
        firm_name=demo_data.DEMO_FIRM_NAME,
        email=demo_data.DEMO_USER_EMAIL,
        name=demo_data.DEMO_USER_NAME,
        coa_number=demo_data.DEMO_COA_NUMBER,
    )
    result.steps["firm"] = CREATED
    result.steps["user"] = CREATED
    return principal


def _firm_settings_patch(catalog: CatalogBundle, rulepacks: RulepackRegistry) -> dict[str, Any]:
    """What the seed records on the firm.

    ``rulePacks`` is the load-bearing entry: playbook §2 has no ``rulepacks`` table, and
    a compliance report needs to name the pack *revision* it was produced from
    (``compliance_reports.pack_versions``). Recording the resolved map here gives the
    engine a default to copy and gives an auditor something to compare against.
    """
    return {
        "defaultCityPack": demo_data.DEMO_CITY_PACK,
        "units": demo_data.DEMO_UNITS,
        "titleBlock": {
            "firmName": demo_data.DEMO_FIRM_NAME,
            "architectOfRecord": demo_data.DEMO_USER_NAME,
            "coaNumber": demo_data.DEMO_COA_NUMBER,
            "address": "Demo studio, Bengaluru",
            "disclaimer": "Compliance checks are advisory. Drawings require an "
            "architect of record.",
        },
        # §7: dimension openings to the centreline unless the firm says otherwise.
        "dimToJamb": False,
        "rulePacks": rulepacks.summary(),
        "catalog": catalog.summary(),
        "seededBy": "garh_api.seed",
    }


async def _seed_demo_project(
    session: AsyncSession,
    ctx: TenantCtx,
    result: SeedResult,
    brief: demo_data.DemoBrief,
    options: SeedOptions,
) -> None:
    """Create (or reuse) the demo project, its op log and its first named version."""
    # Imported here, not at module scope: these are the *route* implementations, and the
    # seeder calls them on purpose so the demo project takes the exact path a user's
    # click takes — same sequencer, same snapshot envelope, same projections. A local
    # import keeps `garh_api.seed` importable without the router tree (tests import the
    # validators alone).
    from garh_api.routers.ops import dispatch_ops
    from garh_api.routers.projects import create_version
    from garh_api.schemas.ops import OpIn
    from garh_api.schemas.project import VersionCreate

    projects = ProjectRepository(session, ctx)
    project = await projects.get_demo_project()

    if project is not None and options.reset_demo:
        await projects.delete(project.id)
        _log.warning("seed.demo_project_deleted", project_id=str(project.id))
        project = None
        result.steps["demoProject"] = RECREATED

    if project is None:
        project = await projects.create(
            name=demo_data.DEMO_PROJECT_NAME,
            status=demo_data.DEMO_PROJECT_STATUS,
            units=demo_data.DEMO_UNITS,
            city_pack=demo_data.DEMO_CITY_PACK,
            architect_of_record=ctx.user_id,
            demo=True,
        )
        result.steps.setdefault("demoProject", CREATED)
    else:
        result.steps["demoProject"] = REUSED
    result.project_id = project.id

    op_repo = OpRepository(session, ctx)
    from garh_api.routers import active_branch

    branch = await active_branch(session, ctx, project.id)
    head_idx = await op_repo.head_idx(project.id, branch)

    if head_idx >= 0:
        # Already seeded (or edited by a user). Appending again would duplicate the plot
        # boundary and, worse, re-run brief.update over an edited brief.
        result.steps["opLog"] = REUSED
        result.head_idx = head_idx
    else:
        wire_ops = demo_data.demo_op_log(brief)
        storey_ids = list(demo_data.demo_storey_ids())
        wire_ops.extend(demo_data.solved_plan_ops(storey_ids=storey_ids))
        wire_ops.extend(demo_data.facade_ops(storey_ids=storey_ids))
        group_id = uuid.uuid4()
        appended = await dispatch_ops(
            session,
            ctx,
            project.id,
            [
                OpIn(
                    type=str(op["type"]),
                    payload=dict(op["payload"]),
                    # Stable per project, so a re-run of an interrupted seed is an
                    # idempotent replay rather than a second boundary.
                    client_op_id="seed-%02d" % index,
                )
                for index, op in enumerate(wire_ops)
            ],
            source="system",
            group_id=group_id,
        )
        result.steps["opLog"] = CREATED
        result.ops_appended = len(appended.applied)
        result.head_idx = appended.head_idx
        result.state_hash = appended.state_hash

    # The plot/brief projections (§11: the tables the solver and rules engine read).
    # dispatch_ops appends the ops; mirroring is the route's job, so the seeder does the
    # same two upserts PUT /plot and PUT /brief do.
    await PlotRepository(session, ctx).upsert(
        project.id,
        boundary=demo_data.demo_plot_polygon(),
        north_deg=demo_data.DEMO_NORTH_DEG,
        roads=[
            {
                "edgeIndex": demo_data.DEMO_ROAD_EDGE_INDEX,
                "widthMm": demo_data.DEMO_ROAD_WIDTH_MM,
                "name": demo_data.DEMO_ROAD_NAME,
            }
        ],
        reg_profile={"cityPack": demo_data.DEMO_CITY_PACK, "overrides": {}},
        source="seed",
    )
    await BriefRepository(session, ctx).upsert(
        project.id,
        data=brief.data,
        vastu_mode=brief.vastu_mode,
        completeness=brief.completeness,
    )
    result.steps["plot"] = CREATED if result.steps.get("opLog") == CREATED else REUSED
    result.steps["brief"] = result.steps["plot"]

    versions = DesignVersionRepository(session, ctx)
    existing = await versions.latest(project.id, branch)
    if existing is not None:
        result.steps["version"] = REUSED
        result.version_id = existing.id
        return
    created = await create_version(
        project.id, VersionCreate(name=demo_data.DEMO_VERSION_NAME), session, ctx
    )
    result.steps["version"] = CREATED
    result.version_id = created.id


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def seed(
    session: AsyncSession,
    options: SeedOptions | None = None,
    *,
    settings: Settings | None = None,
) -> SeedResult:
    """Seed everything §17 asks for that exists today. Idempotent.

    The caller owns the transaction: :func:`run` wraps this in
    :func:`garh_api.db.session_scope`, and ``tests/test_seed.py`` calls it inside a test
    session it rolls back. Nothing here commits.
    """
    opts = options or SeedOptions()
    cfg = settings or get_settings()
    opts.assert_allowed(cfg)

    result = SeedResult(dry_run=opts.dry_run)
    result.pending = [dict(item) for item in demo_data.PENDING_PHASES]

    # --- 1. validate every input before writing anything ---------------
    catalog = load_catalog_bundle()
    rulepacks = load_rulepack_registry()
    brief = demo_data.load_demo_brief()
    result.catalog = catalog.summary()
    result.rulepacks = rulepacks.versions()
    result.brief_source = brief.source
    if catalog.serving_warning:
        result.warnings.append(catalog.serving_warning)
    unreviewed = [p["id"] for p in rulepacks.packs if p["reviewStatus"] != "reviewed"]
    if unreviewed:
        result.warnings.append(
            "Rule pack(s) %s are still 'seed' confidence and unreviewed. The UI must show "
            "that on every citation (golden rule 4)." % ", ".join(unreviewed)
        )

    if opts.dry_run:
        for step in ("firm", "user", "flags", "firmSettings", "demoProject", "opLog", "version"):
            result.steps[step] = SKIPPED
        return result

    # --- 2. firm + owner ----------------------------------------------
    principal = await _ensure_firm_and_owner(session, result)
    result.firm_id = principal.firm_id
    result.user_id = principal.user_id
    ctx = TenantCtx(
        firm_id=principal.firm_id,
        user_id=principal.user_id,
        role=principal.role,
        request_id="seed",
    )

    # --- 3. feature flags (§18) ---------------------------------------
    result.flags_created = await FlagRepository(session).seed_defaults()
    result.steps["flags"] = CREATED if result.flags_created else REUSED

    # --- 4. firm settings: catalogue digest + rule-pack versions -------
    await FirmRepository(session, ctx).merge_settings(_firm_settings_patch(catalog, rulepacks))
    result.steps["firmSettings"] = CREATED

    # --- 5. the demo project ------------------------------------------
    await _seed_demo_project(session, ctx, result, brief, opts)

    await AuditLogRepository(session, ctx).record(
        ACTION_SEED_COMPLETED,
        entity="firm",
        entity_id=principal.firm_id,
        meta={
            "steps": dict(result.steps),
            "catalogDigest": result.catalog.get("digest"),
            "rulePacks": result.rulepacks,
            "briefSource": result.brief_source,
            "pending": [item["item"] for item in result.pending],
        },
    )
    _log.info("seed.completed", **dict(result.steps.items()))
    return result


async def run(options: SeedOptions | None = None) -> SeedResult:
    """Open a session, seed, commit. What ``python -m garh_api.seed`` calls."""
    from garh_api.db import dispose_async_engine, session_scope

    settings = get_settings()
    configure_logging(settings)
    opts = options or SeedOptions()
    opts.assert_allowed(settings)
    try:
        async with session_scope(settings) as session:
            return await seed(session, opts, settings=settings)
    except SQLAlchemyError as exc:
        # The full traceback goes to the log before the friendly message: a
        # one-line "the database rejected the seed" cost a real debugging
        # session when the cause was a code bug (sync IO on the async
        # session), not a schema problem — the file and line are the fix.
        _log.error("seed.database_error", traceback=traceback.format_exc())
        raise SeedError(
            "The database rejected the seed: %s\nIf this mentions a missing relation, the "
            "schema is not applied yet — run `alembic upgrade head` in apps/api (compose "
            "does it on boot)." % exc
        ) from exc
    finally:
        await dispose_async_engine()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m garh_api.seed",
        description="Seed the demo firm, catalogues, rule packs and demo project (§17).",
    )
    parser.add_argument(
        "--reset-demo",
        action="store_true",
        help="delete the existing demo project and rebuild it (destroys its op log)",
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="permit seeding when APP_ENV is staging/prod (refused by default)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the catalogue, rule packs and demo brief; write nothing",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--quiet", action="store_true", help="print nothing on success")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code; never raises for expected failures."""
    args = build_parser().parse_args(argv)
    options = SeedOptions(
        reset_demo=args.reset_demo,
        allow_production=args.allow_production,
        dry_run=args.dry_run,
    )
    try:
        result = asyncio.run(run(options))
    except (SeedError, SeedDataError) as exc:
        print("seed failed: %s" % exc, file=sys.stderr)
        return 1
    if args.quiet:
        return 0
    if args.json:
        print(json.dumps(result.to_json(), indent=2, sort_keys=True))
    else:
        print(result.render())
    return 0


__all__ = [
    "ACTION_SEED_COMPLETED",
    "ALLOW_PROD_ENV",
    "CREATED",
    "RECREATED",
    "REUSED",
    "SKIPPED",
    "SeedError",
    "SeedOptions",
    "SeedResult",
    "build_parser",
    "main",
    "run",
    "seed",
]
