"""Seed data and the demo project (playbook §17).

    python -m garh_api.seed

**Note for whoever owns the Makefile and CI:** the scaffold's ``make seed`` target and the
``e2e-smoke`` job both call ``python -m garh_api.scripts.seed``. This package is
``garh_api.seed`` — that is the path this agent owns (``garh_api/seed/**``), and
``garh_api/scripts/`` does not exist. One line in each place:

    -  python -m garh_api.scripts.seed
    +  python -m garh_api.seed

What it creates, in one sentence: the demo firm **Studio Demo** with admin
``demo@garh.ai``, the global feature flags at their defaults, the firm's default city pack
and the resolved rule-pack version map, a validated furniture/material/facade-kit
catalogue, and the demo project — a 30×40 ft Bengaluru plot with a 9 m road on the south
edge, a G+1 3BHK brief, two storeys, an op log and a named version carrying the folded
snapshot.

What it deliberately does **not** create: a solved plan, a facade, renders or sheets.
Those come from the solver, the 3D layer, the render worker and the drawings worker, none
of which exist yet. :mod:`garh_api.seed.demo` holds one named extension point per missing
piece and says which phase owns it; the runner already calls them all. See that module's
docstring for the full table — it is the honest answer to §17's "one complete demo
project", not a silent omission.

Layout::

    catalog.py   file loading + validation (integer mm, ids, counts, room types)
    demo.py      the demo firm/user/project definition, op log, extension points
    runner.py    the idempotent async runner + CLI
    __main__.py  python -m garh_api.seed

Importing this package pulls in no router and opens no connection, so
``tests/test_catalog_fixtures.py`` can use the validators on their own.
"""

from __future__ import annotations

from garh_api.seed.catalog import (
    FACADE_KIT_IDS,
    MIN_FURNITURE_ITEMS,
    MIN_MATERIALS,
    REQUIRED_RULEPACKS,
    CatalogBundle,
    RulepackRegistry,
    SeedDataError,
    load_catalog_bundle,
    load_rulepack_registry,
)
from garh_api.seed.demo import (
    DEMO_BRIEF_CORPUS_SIBLING,
    DEMO_BRIEF_SOURCE,
    DEMO_CITY_PACK,
    DEMO_FIRM_NAME,
    DEMO_PROJECT_NAME,
    DEMO_USER_EMAIL,
    DEMO_USER_NAME,
    PENDING_PHASES,
    DemoBrief,
    demo_brief_data,
    demo_op_log,
    load_demo_brief,
)
from garh_api.seed.runner import (
    ACTION_SEED_COMPLETED,
    SeedError,
    SeedOptions,
    SeedResult,
    main,
    run,
    seed,
)

__all__ = [
    "ACTION_SEED_COMPLETED",
    "DEMO_BRIEF_CORPUS_SIBLING",
    "DEMO_BRIEF_SOURCE",
    "DEMO_CITY_PACK",
    "DEMO_FIRM_NAME",
    "DEMO_PROJECT_NAME",
    "DEMO_USER_EMAIL",
    "DEMO_USER_NAME",
    "FACADE_KIT_IDS",
    "MIN_FURNITURE_ITEMS",
    "MIN_MATERIALS",
    "PENDING_PHASES",
    "REQUIRED_RULEPACKS",
    "CatalogBundle",
    "DemoBrief",
    "RulepackRegistry",
    "SeedDataError",
    "SeedError",
    "SeedOptions",
    "SeedResult",
    "demo_brief_data",
    "demo_op_log",
    "load_catalog_bundle",
    "load_demo_brief",
    "load_rulepack_registry",
    "main",
    "run",
    "seed",
]
