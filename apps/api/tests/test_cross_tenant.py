"""THE cross-tenant test (Phase 0 DoD, playbook §13 AuthZ).

*"A cross-tenant access attempt test proves 404/403."*

Firm B holds a valid, unexpired, correctly-signed access token. Every id it presents
belongs to firm A and **exists**. Nothing here is a malformed-input test: the whole point
is that a legitimate caller asking for somebody else's real object is answered exactly as
if that object did not exist.

## Why 404 and not 403

403 says "this exists and you may not have it", which is an existence oracle: a competitor
could enumerate project ids to learn how many projects a studio has and when they were
created. So the tenancy layer returns 404 — see
``garh_api.routers.require_project``: *"a project from another firm is indistinguishable
from a missing one — that is the cross-tenant guarantee, not an accident"*. 403 is reserved
for a caller inside the right firm who lacks the **role**
(:func:`test_member_cannot_delete_a_project_403`), which is the "/403" half of the DoD.

## Why this is table-driven

Coverage that depends on somebody remembering to add a test is not coverage.
:data:`TENANT_SCOPED_CASES` is the table, and
:func:`test_every_tenant_scoped_route_is_covered` walks the live FastAPI route table and
fails if any route carrying a tenant-scoped path parameter is missing from it. A new
endpoint is therefore covered by default: merge it without a row here and this file goes
red, naming the route.

CI runs ``pytest -k "cross_tenant or tenancy"`` as a separate, named step and treats
"nothing matched" (exit code 5) as a failure, so this file cannot be quietly deleted
either.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

import pytest
from garh_api.tenancy import EntityNotFoundError

from tests.helpers import problem

# ---------------------------------------------------------------------------
# The route table
# ---------------------------------------------------------------------------

#: Path parameters that name a tenant-owned object. A route with one of these must be
#: firm-scoped, and must therefore appear in :data:`TENANT_SCOPED_CASES`.
TENANT_SCOPED_PARAMS: frozenset[str] = frozenset(
    {
        "project_id",
        "job_id",
        "version_id",
        "sheet_id",
        "share_link_id",
        "comment_id",
        "annotation_id",
        # G-1/G-4. An invoice and a seat are tenant-owned rows reached by their own id,
        # with no project in the path to carry the ownership check — so the id IS the
        # scoping and these are exactly the routes this guard exists for. They were
        # absent from this set, which meant `_tenant_scoped_routes` skipped the three
        # billing routes carrying them and the guard read as coverage while being
        # structurally unable to fire on them.
        "invoice_id",
        "seat_id",
        # F-3. A refresh family is keyed by USER, not by firm, so the cross-tenant
        # answer comes from a key that does not exist rather than from a WHERE clause.
        # It is listed here anyway: the guarantee a caller cares about is identical
        # ("another tenant's id reads as missing"), and leaving it out would mean the
        # only route that can end another person's session were covered by diligence
        # instead of by default.
        "family_id",
    }
)

#: Routes that take an opaque **capability** rather than a tenant-owned id. They are not
#: firm-scoped by design and are excluded from the coverage guard, each with the reason.
#:
#: Both are still tenancy-relevant and are tested elsewhere:
#: * ``/share/{token}/**`` — the anonymous viewer surface. The token *is* the
#:   authorisation, it is stored hashed, and the router imports no write path
#:   (``test_share_viewer_surface`` in ``test_problem_json.py`` asserts the 404 for an
#:   unknown token).
#: * ``/downloads/{token}`` — an HMAC-signed, ≤10-minute download capability that carries
#:   its own firm id (``routers.verify_download_token``).
CAPABILITY_ROUTES: frozenset[str] = frozenset({"token"})


@dataclass(frozen=True)
class Case:
    """One tenant-scoped route, as firm B will attempt it."""

    method: str
    #: Path template using the same names FastAPI uses, e.g. ``/projects/{project_id}``.
    template: str
    #: JSON body for the methods that need one. ``None`` sends no body.
    body: dict[str, Any] | None = None
    #: Raw bytes for routes that take a binary upload rather than JSON (the
    #: underlay image). Mutually exclusive with ``body``; the payload must be
    #: VALID for the route's own sniffing, so a 404 proves tenancy and not
    #: content validation.
    raw_body: bytes | None = None
    #: Query string appended verbatim (no leading ``?``).
    query: str = ""
    #: Headers merged over the auth header.
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return "%s %s" % (self.method, self.template)


#: A real 1x1 PNG (signature + IHDR + IDAT + IEND), so the underlay upload's
#: magic-byte sniff and dimension parse both succeed and the only thing left to
#: fail is the tenancy check. Built here rather than imported so this file stays
#: readable on its own.
_ONE_PIXEL_PNG: bytes = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

#: Every route that reaches a tenant-owned row. Ordered as the API surface is documented.
#:
#: The bodies are *valid* on purpose. A body that fails Pydantic validation would 422
#: before the tenancy check ever ran, and the test would pass while proving nothing —
#: which is the most likely way for this file to rot into a false green.
TENANT_SCOPED_CASES: tuple[Case, ...] = (
    # -- projects ---------------------------------------------------------
    Case("GET", "/projects/{project_id}"),
    Case("PATCH", "/projects/{project_id}", body={"name": "Renamed by firm B"}),
    Case("DELETE", "/projects/{project_id}"),
    Case("GET", "/projects/{project_id}/branch"),
    # -- plot / brief -----------------------------------------------------
    Case("GET", "/projects/{project_id}/plot"),
    Case(
        "PUT",
        "/projects/{project_id}/plot",
        body={
            "boundary": [
                {"x": 0, "y": 0},
                {"x": 9144, "y": 0},
                {"x": 9144, "y": 12192},
                {"x": 0, "y": 12192},
            ],
            "northDeg": 0,
        },
    ),
    Case("GET", "/projects/{project_id}/brief"),
    Case("PUT", "/projects/{project_id}/brief", body={"data": {"bedrooms": 3}}),
    Case(
        "POST",
        "/projects/{project_id}/brief/parse",
        body={"text": "3 bedrooms, one pooja room", "apply": False},
    ),
    Case("GET", "/projects/{project_id}/compliance"),
    # G-5. Firm B must not learn another studio's plot size, buildable envelope or
    # quoted fee — the last of which is a commercial position, not just tenant data.
    Case("GET", "/projects/{project_id}/estimate"),
    # -- versions ---------------------------------------------------------
    Case("GET", "/projects/{project_id}/versions"),
    Case("POST", "/projects/{project_id}/versions", body={"name": "Firm B checkpoint"}),
    Case("POST", "/projects/{project_id}/versions/{version_id}/restore"),
    # -- the op sequencer -------------------------------------------------
    Case(
        "POST",
        "/projects/{project_id}/ops",
        body={
            "ops": [{"type": "plot.set_north", "payload": {"deg": 90}}],
            "baseIdx": -1,
            "source": "manual",
        },
    ),
    Case("GET", "/projects/{project_id}/ops", query="since=-1"),
    Case("GET", "/projects/{project_id}/model"),
    # SSE, but safe in this sweep: the tenancy check runs (and 404s) inside the
    # handler BEFORE the EventSourceResponse is built, so firm B never streams.
    Case("GET", "/projects/{project_id}/collab/events"),
    # Live cursors: the body passes every bound on purpose, so the 404 proves the
    # tenancy check and not Pydantic (and nothing is published — asserted with a
    # channel subscription in test_collab_cursor.py, which this sweep cannot see).
    Case(
        "POST",
        "/projects/{project_id}/collab/cursor",
        body={"x": 1000, "y": 1000, "storeyIndex": 0},
    ),
    # -- tracing underlay (§: a plan image the architect traces over) ------
    # The upload sends a REAL 1x1 PNG: a body the magic-byte sniff accepts, so a
    # 404 here proves the tenancy check ran and not the image validator.
    Case("GET", "/projects/{project_id}/underlay"),
    Case("PATCH", "/projects/{project_id}/underlay", body={"opacity": 0.5}),
    Case("DELETE", "/projects/{project_id}/underlay"),
    Case(
        "POST",
        "/projects/{project_id}/underlay/image",
        raw_body=_ONE_PIXEL_PNG,
        headers={"content-type": "image/png"},
    ),
    # -- solver -----------------------------------------------------------
    Case("POST", "/projects/{project_id}/solve", body={"optionCount": 3}),
    Case("GET", "/projects/{project_id}/solver-jobs"),
    Case("GET", "/solver-jobs/{job_id}"),
    Case("POST", "/solver-jobs/{job_id}/cancel"),
    Case("GET", "/solver-jobs/{job_id}/events"),
    # -- renders ----------------------------------------------------------
    Case("POST", "/projects/{project_id}/renders", body={"mode": "explore"}),
    Case("GET", "/projects/{project_id}/renders"),
    Case("GET", "/render-jobs/{render_job_id}"),
    Case("POST", "/render-jobs/{render_job_id}/cancel"),
    Case("GET", "/render-jobs/{render_job_id}/events"),
    # -- sheets and exports -----------------------------------------------
    Case("POST", "/projects/{project_id}/sheets/generate", body={"sheetSize": "A2"}),
    Case("GET", "/projects/{project_id}/sheets"),
    Case("GET", "/projects/{project_id}/sheets/{sheet_id}.svg"),
    Case("POST", "/projects/{project_id}/export", body={"kind": "dxf"}),
    Case("GET", "/export-jobs/{export_job_id}"),
    Case("GET", "/export-jobs/{export_job_id}/events"),
    # ``exportJobId`` is a required *query* parameter here, not a body field. Sending it
    # as a body would 422 in request validation before the tenancy check ran, and the
    # case would pass while proving nothing.
    Case(
        "POST",
        "/projects/{project_id}/downloads/audit",
        query="exportJobId={export_job_id}",
    ),
    # -- signed-in devices (F-3) ------------------------------------------
    # Firm B holds a valid token and a real, live family id belonging to firm A's
    # admin. Session records are hash-tagged by user id, so B's lookup misses and the
    # answer is the same 404 an invented id would get. The positive half — that the
    # family really is alive and revocable by ITS OWN user — is asserted in
    # test_sessions.py, so this case cannot pass by revoking nothing.
    Case("DELETE", "/auth/sessions/{family_id}"),
    # -- share links and comments -----------------------------------------
    Case("GET", "/projects/{project_id}/share"),
    Case("POST", "/projects/{project_id}/share", body={"canComment": True}),
    Case("DELETE", "/share/{share_link_id}"),
    Case("GET", "/projects/{project_id}/comments"),
    Case("POST", "/projects/{project_id}/comments", body={"body": "Move the door"}),
    Case("POST", "/comments/{comment_id}/resolve"),
    # -- copilot (§10) ----------------------------------------------------
    Case("POST", "/projects/{project_id}/copilot", body={"text": "add a door to the south wall"}),
    Case(
        "POST",
        "/projects/{project_id}/copilot/decision",
        body={"command": "add a door to the south wall", "outcome": "rejected", "opsCount": 0},
    ),
    # -- DXF import (§13 upload pipeline) ---------------------------------
    # Raw-body upload: the byte ceiling and content sniff run AFTER the
    # project ownership check, so an empty body still proves the 404.
    Case("POST", "/projects/{project_id}/import/dxf", query="filename=plot.dxf"),
    Case("GET", "/import-jobs/{job_id}"),
    # -- render history / uploads / client packs (§9) ---------------------
    Case("GET", "/projects/{project_id}/render-history"),
    Case("POST", "/projects/{project_id}/renders/uploads", body={"count": 1}),
    Case(
        "POST",
        "/projects/{project_id}/renders/client-pack",
        body={
            "shots": [
                # The preset must be REAL: an unknown one 422s at the schema,
                # before tenancy runs, and the case would test validation
                # instead of the cross-tenant 404 it exists for.
                {"slug": "exterior-34-dusk", "preset": "exterior-34-dusk", "mode": "explore"}
            ]
        },
    ),
    Case("GET", "/projects/{project_id}/render-packs/{pack_id}"),
    Case("POST", "/projects/{project_id}/render-packs/{pack_id}/archive"),
    # -- billing (G-1, G-3, G-4) ------------------------------------------
    # Firm B holds a valid ADMIN token for its own firm, so the role check passes and
    # what is left is the tenancy check on a real invoice and a real seat of firm A's.
    # The checkout case is the sharpest: it is a write that would open a gateway order
    # against somebody else's invoice.
    Case("GET", "/billing/invoices/{invoice_id}"),
    Case("POST", "/billing/invoices/{invoice_id}/checkout"),
    Case("DELETE", "/billing/seats/{seat_id}"),
    # -- sheets, the §7 municipal set -------------------------------------
    Case("GET", "/projects/{project_id}/sheets/summary"),
    Case("GET", "/projects/{project_id}/sheets/review-tray"),
    Case("GET", "/projects/{project_id}/sheets/{sheet_id}/annotations"),
    Case("GET", "/projects/{project_id}/sheets/{sheet_id}/content"),
    # -- D-4 submission templates -----------------------------------------
    # The PUT body is deliberately VALID (bbmp is a real authority, the khata number a
    # real shape) so a 404 proves tenancy rather than input validation — which would
    # pass while the route leaked another firm's project.
    Case("GET", "/projects/{project_id}/submission"),
    Case(
        "PUT",
        "/projects/{project_id}/submission",
        body={"authority": "bbmp", "fields": {"khataNumber": "A-1234/56"}},
    ),
    Case("GET", "/projects/{project_id}/sheets/submission-readiness"),
    # -- C-8 version compare ------------------------------------------------
    # The version ids are firm A's own. A version id is not a capability: belonging to
    # the caller's firm is not enough, it must belong to THIS project, or one project's
    # compare reads another's design.
    Case(
        "GET",
        "/projects/{project_id}/versions/compare",
        query="a=00000000-0000-4000-8000-000000000001&b=00000000-0000-4000-8000-000000000002",
    ),
)


# ---------------------------------------------------------------------------
# Firm A's estate: one real row of every kind the table addresses
# ---------------------------------------------------------------------------


@pytest.fixture
def billing_schema(database: Any) -> Any:
    """The five billing tables, present and empty.

    They live on their own ``MetaData`` (``garh_api.billing.models``: a tax invoice must
    outlive the firm it describes, so these tables carry no FK to ``firms``), which means
    ``ALL_TABLES`` does not list them and neither ``conftest.database`` nor
    ``conftest.clean_db`` touches them. The billing routes in ``TENANT_SCOPED_CASES``
    need a real invoice and a real seat of firm A's, so this fixture creates the schema
    if the migration has not been applied and truncates it per test.
    """
    from garh_api.billing.models import BILLING_METADATA, BILLING_TABLES
    from sqlalchemy import text as sql_text

    BILLING_METADATA.create_all(database)
    with database.begin() as connection:
        connection.execute(
            sql_text(
                "TRUNCATE TABLE %s RESTART IDENTITY CASCADE"
                % ", ".join('"%s"' % name for name in BILLING_TABLES)
            )
        )
    return database


async def _billing_estate(session: Any, firm_a: Any) -> dict[str, str]:
    """One real invoice and one real seat owned by firm A.

    The invoice is written through the product's own repository with the fields
    ``billing.invoices`` would put on it, rather than driven through ``POST
    /billing/invoices`` — that route needs the deployment's GST registration in the
    environment, and this fixture is about tenancy, not about GST configuration.
    """
    from datetime import date, datetime, timedelta

    from garh_api.billing.repositories import InvoiceRepository, SeatRepository

    period_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    invoice = await InvoiceRepository(session, firm_a.ctx()).create(
        invoice_number="GA-XT-%s" % uuid.uuid4().hex[:6].upper(),
        status="issued",
        issued_on=date.today(),
        period_start=period_start,
        period_end=period_start + timedelta(days=30),
        supplier_legal_name="Garh Technologies Private Limited",
        supplier_gstin="29AABCG1234H1ZV",
        supplier_state_code="29",
        supplier_address="4th Cross, Indiranagar, Bengaluru 560038",
        customer_legal_name="Studio One LLP",
        customer_gstin=None,
        customer_address="12 MG Road, Bengaluru, 560001",
        place_of_supply_code="29",
        interstate=False,
        currency="INR",
        taxable_inr=4_999,
        cgst_inr=450,
        sgst_inr=450,
        igst_inr=0,
        total_inr=5_899,
        rate_percent_x100=1800,
        lines=[{"description": "Studio plan", "hsnSac": "997331", "amountInr": 4_999}],
    )
    seat = await SeatRepository(session, firm_a.ctx()).assign(
        user_id=firm_a.user_id, seat_type="editor", assigned_by=firm_a.user_id
    )
    return {"invoice_id": str(invoice.id), "seat_id": str(seat.id)}


@pytest.fixture
async def estate_a(
    session: Any, firm_a: Any, project_a: Any, clean_redis: Any, billing_schema: Any
) -> dict[str, str]:
    """Real, committed rows owned by firm A, as a template-substitution map.

    Everything is created through the product's own repositories, so nothing here is a
    shape the application could not produce.
    """
    from garh_api import queue
    from garh_api.auth import SessionStore
    from garh_api.repositories import SheetRepository
    from garh_api.security import new_token_family

    from tests import factories

    version = await factories.create_version(session, firm_a, project_a.id, name="v1")
    solver_job = await factories.create_solver_job(session, firm_a, project_a.id)
    render_job = await factories.create_render_job(session, firm_a, project_a.id)
    share_link, _token = await factories.create_share_link(session, firm_a, project_a.id)
    comment = await factories.create_comment(session, firm_a, project_a.id)
    sheet = await SheetRepository(session, firm_a.ctx()).create(
        project_a.id, kind="floor", number="A-101"
    )
    await session.commit()

    # A REAL, live refresh family for firm A's admin — the F-3 case above needs an id
    # that would succeed for its owner, or its 404 would prove nothing.
    family = new_token_family()
    await SessionStore().start_family(
        user_id=firm_a.user_id,
        firm_id=firm_a.firm_id,
        family=family,
        started_at=int(time.time()),
        ip="203.0.113.10",
        user_agent="Mozilla/5.0 (Macintosh) Chrome/126.0",
    )

    billing_ids = await _billing_estate(session, firm_a)
    await session.commit()

    export_job_id = "exp_%s" % uuid.uuid4().hex[:16]
    await queue.put_export_job(
        queue.ExportJob(
            id=export_job_id,
            firm_id=str(firm_a.firm_id),
            project_id=str(project_a.id),
            kind="dxf",
            status="succeeded",
            progress=100,
            download_url="https://example.invalid/exports/%s.dxf" % export_job_id,
        )
    )

    return {
        **billing_ids,
        "project_id": str(project_a.id),
        "family_id": family,
        "version_id": str(version.id),
        "job_id": str(solver_job.id),
        "render_job_id": str(render_job.id),
        "sheet_id": str(sheet.id),
        "share_link_id": str(share_link.id),
        "comment_id": str(comment.id),
        "export_job_id": export_job_id,
        # Packs are addressed under their project, so the ownership check on
        # project_id is what these cases prove; the pack id itself only needs
        # to be well-formed.
        "pack_id": "renderpack-%s" % uuid.uuid4().hex[:12],
    }


def _url(api: str, case: Case, ids: dict[str, str]) -> str:
    path = case.template.format(**ids)
    query = case.query.format(**ids) if case.query else ""
    return "%s%s%s" % (api, path, ("?" + query) if query else "")


# ---------------------------------------------------------------------------
# (a) The parametrised cross-tenant sweep
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("case", TENANT_SCOPED_CASES, ids=lambda c: c.id)
async def test_cross_tenant_access_is_404(
    case: Case,
    client: Any,
    api: str,
    firm_a: Any,
    firm_b: Any,
    project_a: Any,
    estate_a: dict[str, str],
) -> None:
    """Firm B asks for firm A's object by id and is told it does not exist.

    Both halves of the guarantee are asserted on the same response, because they are the
    same guarantee: the status must be 404, and the body must not name the thing that was
    hidden. A 404 reading "project 'Sharma Residence' belongs to Studio One" would be a
    403 with extra steps.
    """
    payload: dict[str, Any] = (
        {"content": case.raw_body} if case.raw_body is not None else {"json": case.body}
    )
    response = await client.request(
        case.method,
        _url(api, case, estate_a),
        headers={**firm_b.headers, **case.headers},
        **payload,
    )

    assert response.status_code == 404, "%s leaked across tenants: expected 404, got %s\n%s" % (
        case.id,
        response.status_code,
        response.text[:400],
    )
    body = problem(response)
    assert body["code"] == "not_found", body

    text = response.text
    for secret in (project_a.name, firm_a.firm_name, firm_a.email, str(firm_a.firm_id)):
        assert secret not in text, "%s leaked %r in its 404 body" % (case.id, secret)


# ---------------------------------------------------------------------------
# The coverage guard — what makes new routes covered by default
# ---------------------------------------------------------------------------


def _walk_routes(app: Any) -> list[Any]:
    """Flatten the route table across fastapi versions.

    fastapi ≤0.115 flattened ``include_router`` into ``app.routes``; 0.141 keeps
    lazy ``_IncludedRouter`` wrappers there instead, whose children only surface
    via ``effective_candidates()``. Walk both shapes — this guard going quietly
    empty is precisely what ``test_the_route_table_is_not_trivially_small``
    exists to catch (and did, on the 0.141 upgrade).
    """
    out: list[Any] = []
    stack = list(app.routes)
    while stack:
        item = stack.pop()
        children = getattr(item, "routes", None)
        if children:
            stack.extend(children)
            continue
        candidates = getattr(item, "effective_candidates", None)
        if callable(candidates):
            stack.extend(candidates())
            continue
        out.append(item)
    return out


def _tenant_scoped_routes(app: Any) -> set[tuple[str, str]]:
    """Every ``(method, path)`` on the app that carries a tenant-scoped path parameter."""
    found: set[tuple[str, str]] = set()
    for route in _walk_routes(app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        params = set(getattr(route, "param_convertors", {}) or {})
        if not params:
            # Starlette only fills param_convertors for compiled paths; fall back to the
            # template text so a route form we have not seen cannot slip through.
            params = {
                segment.split("}")[0].split(":")[0]
                for segment in path.split("{")[1:]
                if "}" in segment
            }
        if params & CAPABILITY_ROUTES:
            continue
        if not (params & TENANT_SCOPED_PARAMS):
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            found.add((method, path))
    return found


def _covered_routes(api: str) -> set[tuple[str, str]]:
    """The route table above, normalised to the app's own path spellings."""
    covered: set[tuple[str, str]] = set()
    for case in TENANT_SCOPED_CASES:
        path = (
            case.template.replace("{render_job_id}", "{job_id}")
            .replace("{export_job_id}", "{job_id}")
            .replace("{sheet_id}.svg", "{sheet_id}.{fmt}")
        )
        covered.add((case.method, api + path))
    return covered


def test_every_tenant_scoped_route_is_covered(app_routes: Any, api: str) -> None:
    """A new firm-scoped route with no cross-tenant case fails this test.

    This is the mechanism the task calls for: coverage by default rather than by
    diligence. If you are here because a route you just added is listed below, add a
    ``Case`` for it to :data:`TENANT_SCOPED_CASES` with a **valid** body.
    """
    live = _tenant_scoped_routes(app_routes)
    covered = _covered_routes(api)

    uncovered = sorted(live - covered)
    assert not uncovered, (
        "%d tenant-scoped route(s) have no cross-tenant test:\n  %s\n"
        "Add a Case to TENANT_SCOPED_CASES in this file (§13 requires every firm-scoped "
        "route to answer another tenant with 404)."
        % (len(uncovered), "\n  ".join("%s %s" % pair for pair in uncovered))
    )

    stale = sorted(covered - live)
    assert not stale, (
        "%d case(s) name a route that no longer exists:\n  %s\n"
        "Delete the Case, or fix its template."
        % (len(stale), "\n  ".join("%s %s" % p for p in stale))
    )


def test_the_route_table_is_not_trivially_small(app_routes: Any) -> None:
    """Guard against the guard passing because the app registered nothing.

    ``_tenant_scoped_routes`` returning an empty set would make the coverage assertion
    vacuously true, which is exactly the failure CI's "exit code 5" check exists to catch
    one level up.
    """
    assert len(_tenant_scoped_routes(app_routes)) >= 30


# ---------------------------------------------------------------------------
# Repository-level tenancy — the layer the routes rely on
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_tenancy_repository_cannot_see_another_firms_row(
    session: Any, firm_a: Any, firm_b: Any, project_a: Any
) -> None:
    """``get`` returns None and ``require`` raises — no query has an unscoped path."""
    from garh_api.repositories import ProjectRepository

    repo_b = ProjectRepository(session, firm_b.ctx())
    assert await repo_b.get(project_a.id) is None
    assert await repo_b.exists(project_a.id) is False
    with pytest.raises(EntityNotFoundError):
        await repo_b.require(project_a.id)

    page = await repo_b.list()
    assert page.items == []
    assert await repo_b.count() == 0

    # And firm A still sees it — proving the filter is a filter, not a global break.
    repo_a = ProjectRepository(session, firm_a.ctx())
    assert (await repo_a.require(project_a.id)).id == project_a.id


@pytest.mark.integration
async def test_tenancy_op_log_is_invisible_across_firms(
    session: Any, firm_a: Any, firm_b: Any, project_a: Any
) -> None:
    """An op log is the design. Firm B must not see its length, let alone its contents."""
    from garh_api.repositories import OpRepository
    from garh_api.repositories.domain import NewOp

    from tests.helpers import main_branch

    branch = main_branch(project_a.id)
    await OpRepository(session, firm_a.ctx()).append(
        project_a.id,
        branch,
        -1,
        [NewOp(type="plot.set_north", payload={"deg": 0})],
        source="manual",
    )
    await session.commit()

    repo_a = OpRepository(session, firm_a.ctx())
    repo_b = OpRepository(session, firm_b.ctx())
    assert await repo_a.head_idx(project_a.id, branch) == 0
    # -1 is "empty branch": the same answer firm B would get for a project id it invented.
    assert await repo_b.head_idx(project_a.id, branch) == -1
    assert await repo_b.list_since(project_a.id, branch, -1) == []
    assert await repo_b.list_branches(project_a.id) == []


@pytest.mark.integration
async def test_cross_tenant_write_does_not_mutate(
    client: Any, api: str, session: Any, firm_a: Any, firm_b: Any, project_a: Any
) -> None:
    """A rejected cross-tenant write must leave zero trace, not merely return 404."""
    from garh_api.repositories import OpRepository, ProjectRepository

    from tests.helpers import main_branch

    rename = await client.patch(
        "%s/projects/%s" % (api, project_a.id),
        json={"name": "Owned by firm B"},
        headers=firm_b.headers,
    )
    assert rename.status_code == 404

    ops = await client.post(
        "%s/projects/%s/ops" % (api, project_a.id),
        json={
            "ops": [{"type": "plot.set_north", "payload": {"deg": 270}}],
            "baseIdx": -1,
            "source": "manual",
        },
        headers=firm_b.headers,
    )
    assert ops.status_code == 404

    session.expire_all()
    project = await ProjectRepository(session, firm_a.ctx()).require(project_a.id)
    assert project.name == "Sharma Residence"
    head = await OpRepository(session, firm_a.ctx()).head_idx(
        project_a.id, main_branch(project_a.id)
    )
    assert head == -1, "a cross-tenant append wrote an op"


# ---------------------------------------------------------------------------
# The 403 half: right firm, wrong role
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_member_cannot_delete_a_project_403(
    client: Any, api: str, member_a: Any, project_a: Any
) -> None:
    """Inside the firm, role is enforced with 403 — existence is not a secret here."""
    response = await client.delete("%s/projects/%s" % (api, project_a.id), headers=member_a.headers)
    assert response.status_code == 403, response.text
    body = problem(response)
    assert body["code"] == "permission_denied", body


@pytest.mark.integration
async def test_member_can_still_read_and_write_the_design(
    client: Any, api: str, member_a: Any, project_a: Any
) -> None:
    """403 must be about the one destructive route, not a blanket read-only member."""
    read = await client.get("%s/projects/%s" % (api, project_a.id), headers=member_a.headers)
    assert read.status_code == 200, read.text

    append = await client.post(
        "%s/projects/%s/ops" % (api, project_a.id),
        json={
            "ops": [{"type": "plot.set_north", "payload": {"deg": 90}}],
            "baseIdx": -1,
            "source": "manual",
        },
        headers=member_a.headers,
    )
    assert append.status_code == 200, append.text


# ---------------------------------------------------------------------------
# No credentials at all
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    ("header", "expected_code"),
    [
        (None, "unauthenticated"),
        ("Bearer not-a-jwt", "token_invalid"),
        # A credential that EXISTS but is not a bearer token gets the more
        # precise code — still 401, still problem+json, no tenant knowledge.
        ("Basic YWRtaW46YWRtaW4=", "token_invalid"),
    ],
)
async def test_unauthenticated_access_is_401(
    client: Any, api: str, project_a: Any, header: str | None, expected_code: str
) -> None:
    """A tenant-scoped route with no usable token is 401, and says so in problem+json."""
    headers = {} if header is None else {"Authorization": header}
    response = await client.get("%s/projects/%s" % (api, project_a.id), headers=headers)
    assert response.status_code == 401, response.text
    body = problem(response)
    assert body["code"] == expected_code, body
    assert "www-authenticate" in {k.lower() for k in response.headers}


@pytest.mark.integration
async def test_token_from_a_deleted_firm_cannot_read(
    client: Any, api: str, session: Any, firm_b: Any, project_a: Any
) -> None:
    """A signed token for a firm id that owns nothing reads as an empty tenant, not an error.

    The token is cryptographically fine; it simply scopes every query to a firm with no
    rows. That is the failure mode a stolen-and-replayed token has, and it must be dull.
    """
    listing = await client.get("%s/projects" % api, headers=firm_b.headers)
    assert listing.status_code == 200
    assert listing.json()["items"] == []
