"""Static tenancy lint (§13: "handlers cannot touch tables directly").

    lint rule: no ``session.query`` outside repos

The playbook asks for a lint rule, and ``make tenancy-audit`` is that rule as a grep. This
file is the same rule as a **test**, for three reasons a grep cannot cover:

1. it runs in the ordinary ``pytest`` invocation, so a developer who never runs ``make``
   still cannot merge a handler that opens its own query;
2. it uses the AST rather than a regular expression, so ``system_unscoped_session`` in a
   docstring is correctly ignored while an actual *call* to it is not — the grep cannot tell
   those apart, and a rule that cries wolf gets deleted;
3. it asserts the *positive* invariants too: every tenant-owned table has a ``firm_id``
   column with an index, and every firm-scoped repository is constructed with a
   ``TenantCtx``.

Nothing here needs Postgres or Redis, on purpose. The most important test in the suite
should still run on a laptop with nothing installed.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from garh_api.models import (
    ALL_TABLES,
    NON_TENANT_TABLES,
    TENANT_OWNED_TABLES,
    Base,
)
from garh_api.tenancy import UNSCOPED_ESCAPE_HATCH

# ---------------------------------------------------------------------------
# Where the code is
# ---------------------------------------------------------------------------

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_ROOT = os.path.join(API_ROOT, "garh_api")

#: Modules allowed to build SQL. The tenancy layer, and nothing else.
#:
#: Paths are relative to ``apps/api/``. This is the same allowlist as the Makefile's
#: ``TENANCY_LAYER``, spelled as data instead of as a regex — if the two disagree, the
#: Makefile is the merge gate and this is the developer-facing one, and they should be
#: reconciled in the same commit.
SQL_ALLOWED_PREFIXES: tuple[str, ...] = (
    "garh_api/repositories/",
    "garh_api/tenancy.py",
    "garh_api/db.py",
    "migrations/",
    "tests/",
)

#: Modules allowed to *call* the unscoped escape hatch. A route module may not even
#: mention it (see :func:`test_routers_never_mention_the_escape_hatch`).
ESCAPE_HATCH_CALLERS: frozenset[str] = frozenset(
    {
        "garh_api/tenancy.py",  # defines it
    }
)

#: Modules allowed to *import* it — the re-export plus the definition.
ESCAPE_HATCH_IMPORTERS: frozenset[str] = frozenset(
    {
        "garh_api/tenancy.py",
        "garh_api/repositories/__init__.py",
    }
)

#: Session/engine attributes that mean "this code is building or running SQL".
SQL_METHOD_NAMES: frozenset[str] = frozenset(
    {"query", "execute", "scalars", "scalar", "add", "add_all", "delete", "merge", "get"}
)

#: Names that are a database session at a call site, matching the Makefile's regex.
SESSION_RECEIVERS: frozenset[str] = frozenset({"session", "db", "_session", "conn", "connection"})

#: Repositories that legitimately take no ``TenantCtx``: they run *before* a tenant is
#: known (sign-in), or their table is global. This is the complete allowlist the §13 lint
#: refers to, and every entry is load-bearing:
NON_TENANT_REPOSITORIES: dict[str, str] = {
    "AuthDirectoryRepository": "resolves a principal from an email before any firm is known",
    "OtpCodeRepository": "otp_codes is not tenant-owned (a code is issued pre-auth)",
    "FlagRepository": "flags is global; per-firm overrides live in firms.settings",
    "ShareTokenResolver": "resolves an anonymous token to the firm it belongs to",
}


def _python_files(root: str) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".mypy_cache"}]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _relative(path: str) -> str:
    return os.path.relpath(path, API_ROOT).replace(os.sep, "/")


def _parsed(root: str) -> Iterator[tuple[str, ast.Module, str]]:
    for path in sorted(_python_files(root)):
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        yield _relative(path), ast.parse(source, filename=path), source


def _is_allowed_sql_module(relative: str) -> bool:
    return any(relative.startswith(prefix) for prefix in SQL_ALLOWED_PREFIXES)


# ---------------------------------------------------------------------------
# (h) The lint
# ---------------------------------------------------------------------------


def test_no_module_outside_the_repository_layer_builds_sql() -> None:
    """``session.execute(...)`` in a route handler is a tenancy bug waiting to happen.

    The repository layer's whole value is that ``firm_id`` scoping cannot be forgotten.
    A handler that opens its own query has opted out of that, silently.
    """
    offenders: list[str] = []
    for relative, tree, _source in _parsed(PACKAGE_ROOT):
        if _is_allowed_sql_module(relative):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in SQL_METHOD_NAMES:
                continue
            receiver = func.value
            name = (
                receiver.id
                if isinstance(receiver, ast.Name)
                else receiver.attr
                if isinstance(receiver, ast.Attribute)
                else None
            )
            if name in SESSION_RECEIVERS:
                offenders.append("%s:%d  %s.%s(...)" % (relative, node.lineno, name, func.attr))

    assert not offenders, (
        "%d direct database call(s) outside the repository layer:\n  %s\n\n"
        "Every query must go through a repository that requires a TenantCtx, so firm_id "
        "scoping cannot be forgotten (§13 AuthZ)." % (len(offenders), "\n  ".join(offenders))
    )


def test_escape_hatch_is_only_called_where_it_is_allowed() -> None:
    """``system_unscoped_session`` is the one unscoped path, and it is audited.

    AST-based: a *call* is an offence, a docstring mention is not. That distinction is why
    this exists alongside the Makefile's grep — several repositories reference the name in
    prose to explain why they do not use it.
    """
    callers: dict[str, list[int]] = {}
    importers: set[str] = set()

    for relative, tree, _source in _parsed(PACKAGE_ROOT):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == UNSCOPED_ESCAPE_HATCH for alias in node.names):
                    importers.add(relative)
            elif isinstance(node, ast.Call):
                func = node.func
                target = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if target == UNSCOPED_ESCAPE_HATCH:
                    callers.setdefault(relative, []).append(node.lineno)

    bad_callers = sorted(set(callers) - ESCAPE_HATCH_CALLERS)
    assert not bad_callers, (
        "%s is called from %s. It bypasses tenant scoping entirely; only the modules in "
        "ESCAPE_HATCH_CALLERS may use it, and every use writes an audit row."
        % (UNSCOPED_ESCAPE_HATCH, bad_callers)
    )

    bad_importers = sorted(importers - ESCAPE_HATCH_IMPORTERS)
    assert not bad_importers, (
        "%s is imported by %s. Importing it is how it gets called; add the module to "
        "ESCAPE_HATCH_IMPORTERS only with a written reason."
        % (UNSCOPED_ESCAPE_HATCH, bad_importers)
    )


def test_routers_never_mention_the_escape_hatch() -> None:
    """The §13 check the API's own README documents, run for real.

    Text-based here rather than AST-based, and deliberately stricter: a route module should
    not mention the unscoped path even in a comment, because the next person to read that
    comment is the one who tries it.
    """
    routers = os.path.join(PACKAGE_ROOT, "routers")
    offenders = [
        _relative(path)
        for path in _python_files(routers)
        if UNSCOPED_ESCAPE_HATCH in Path(path).read_text(encoding="utf-8")
    ]
    assert not offenders, "%s appears in router module(s): %s" % (
        UNSCOPED_ESCAPE_HATCH,
        offenders,
    )


def test_routers_do_not_import_the_non_tenant_repositories() -> None:
    """A route must not reach for a repository that skips the tenant check.

    ``AuthDirectoryRepository`` and friends take no ``TenantCtx`` because they run before
    one exists. Called from a route, they are an unscoped query with extra steps —
    ``garh_api.auth`` (the service layer) and ``garh_api.deps`` are the intended callers,
    and ``garh_api.seed`` bootstraps the demo tenant with the same signup path.
    """
    allowed = {
        # The OTP/session service: runs before a tenant exists.
        "garh_api/auth.py",
        # Resolves an anonymous share token to the firm it belongs to.
        "garh_api/deps.py",
        # Re-exports them.
        "garh_api/repositories/__init__.py",
        # Bootstraps the demo tenant through the same signup path a user takes.
        "garh_api/seed/runner.py",
        # §18: "feature flags table read at boot". `flags` is global by design
        # (NON_TENANT_TABLES) and the lifespan hook has no tenant to scope to.
        "garh_api/main.py",
    }
    offenders: list[str] = []
    for relative, tree, _source in _parsed(PACKAGE_ROOT):
        if relative in allowed or relative.startswith("garh_api/repositories/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in NON_TENANT_REPOSITORIES:
                        offenders.append("%s:%d imports %s" % (relative, node.lineno, alias.name))
    assert not offenders, (
        "%d import(s) of a non-tenant-scoped repository outside the allowlist:\n  %s\n"
        "Reasons those repositories exist at all:\n  %s"
        % (
            len(offenders),
            "\n  ".join(offenders),
            "\n  ".join("%s — %s" % item for item in sorted(NON_TENANT_REPOSITORIES.items())),
        )
    )


def test_no_router_imports_the_orm_models() -> None:
    """Routers speak domain dataclasses, not mapped classes.

    Holding a ``models.Project`` in a handler is one attribute access away from lazy-loading
    a relationship across firms. The enum tuples are the exception: they *are* the shared
    vocabulary the API validates against.
    """
    routers = os.path.join(PACKAGE_ROOT, "routers")
    offenders: list[str] = []
    enum_suffixes = ("_ROLES", "_STATUSES", "_UNITS", "_SOURCES", "_MODES", "_KINDS")
    for path in sorted(_python_files(routers)):
        relative = _relative(path)
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("models"):
                for alias in node.names:
                    if not alias.name.endswith(enum_suffixes):
                        offenders.append(
                            "%s:%d imports models.%s" % (relative, node.lineno, alias.name)
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("garh_api.models"):
                        offenders.append("%s:%d imports %s" % (relative, node.lineno, alias.name))
    assert not offenders, "ORM models reached a router:\n  %s" % "\n  ".join(offenders)


# ---------------------------------------------------------------------------
# The positive invariants: the schema itself must be scopeable
# ---------------------------------------------------------------------------


def test_every_tenant_owned_table_has_an_indexed_firm_id() -> None:
    """ "Every tenant-owned table carries ``firm_id`` **plus an index**" (§2).

    Without the column the scoping is impossible; without the index it is a sequential
    scan per request, which is how a correct multi-tenant app becomes an unusable one.
    """
    tables = Base.metadata.tables
    missing_column: list[str] = []
    missing_index: list[str] = []

    for name in TENANT_OWNED_TABLES:
        if name == "firms":
            # The tenant itself: scoped by `id`, not by a self-referential firm_id.
            assert "id" in tables[name].columns
            continue
        table = tables[name]
        if "firm_id" not in table.columns:
            missing_column.append(name)
            continue
        assert not table.columns["firm_id"].nullable, "%s.firm_id must be NOT NULL" % name
        indexed = any(
            "firm_id" in {column.name for column in index.columns} for index in table.indexes
        )
        # A composite primary key or unique constraint leading with firm_id also serves.
        if not indexed:
            indexed = any(
                constraint.columns.keys() and next(iter(constraint.columns.keys())) == "firm_id"
                for constraint in table.constraints
                if hasattr(constraint, "columns")
            )
        if not indexed:
            missing_index.append(name)

    assert not missing_column, "tenant-owned table(s) with no firm_id: %s" % missing_column
    assert not missing_index, "firm_id is not indexed on: %s" % missing_index


def test_non_tenant_tables_are_the_documented_two() -> None:
    """A new global table is a tenancy decision, not an implementation detail."""
    assert set(NON_TENANT_TABLES) == {"flags", "otp_codes"}, NON_TENANT_TABLES
    for name in NON_TENANT_TABLES:
        assert "firm_id" not in Base.metadata.tables[name].columns, (
            "%s is in NON_TENANT_TABLES but carries firm_id — pick one" % name
        )


def test_all_tables_matches_the_metadata() -> None:
    """``ALL_TABLES`` drives the test truncation; a drift silently un-isolates the suite."""
    assert set(ALL_TABLES) == set(Base.metadata.tables), set(ALL_TABLES) ^ set(Base.metadata.tables)


def test_firm_scoped_repositories_require_a_tenant_context() -> None:
    """Every ``Repository`` subclass must take ``(session, ctx)`` — no ctx-less variant.

    Checked by construction rather than by signature text: a repository that can be built
    without a ``TenantCtx`` is a repository whose queries can be unscoped.
    """
    import inspect

    from garh_api import repositories
    from garh_api.tenancy import Repository

    for name in repositories.__all__:
        candidate = getattr(repositories, name)
        if not inspect.isclass(candidate) or not issubclass(candidate, Repository):
            continue
        if name in NON_TENANT_REPOSITORIES:
            continue
        parameters = list(inspect.signature(candidate.__init__).parameters)
        assert parameters[:3] == ["self", "session", "ctx"], (
            "%s.__init__ takes %s; every firm-scoped repository must be (session, ctx)"
            % (name, parameters)
        )


def test_non_tenant_repositories_are_the_documented_allowlist() -> None:
    """Adding a ctx-less repository must be a deliberate, written decision."""
    import inspect

    from garh_api import repositories

    ctxless: set[str] = set()
    for name in repositories.__all__:
        candidate = getattr(repositories, name)
        if not inspect.isclass(candidate):
            continue
        try:
            parameters = list(inspect.signature(candidate.__init__).parameters)
        except (TypeError, ValueError):  # pragma: no cover - builtins
            continue
        if not name.endswith(("Repository", "Resolver")):
            continue
        if "ctx" not in parameters:
            ctxless.add(name)

    undocumented = sorted(ctxless - set(NON_TENANT_REPOSITORIES))
    assert not undocumented, (
        "repository/resolver %s takes no TenantCtx and is not in NON_TENANT_REPOSITORIES. "
        "Every one of those is a query nobody scopes — add it with a reason, or give it a "
        "ctx." % undocumented
    )


@pytest.mark.parametrize("module", ["garh_api.routers.share"])
def test_viewer_surface_imports_no_write_path(module: str) -> None:
    """§13: "the viewer surface is a separate read-only router with no write deps imported".

    Asserted as source text on the public router's module: the write helpers must not be
    imported at module scope, because an import is how they become reachable.
    """
    path = os.path.join(API_ROOT, module.replace(".", os.sep) + ".py")
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)

    module_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                module_level_names.add(alias.asname or alias.name.split(".")[-1])

    forbidden = {"dispatch_ops", "append_ops", "unwrap_snapshot", "load_project_state"}
    leaked = forbidden & module_level_names
    assert not leaked, (
        "the share router imports write-path helper(s) %s at module scope; the viewer "
        "surface must import them inside the one handler that needs them, if at all"
        % sorted(leaked)
    )
