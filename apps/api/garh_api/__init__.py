"""Garh AI API package.

Layering — each layer may only import downward:

    routers / auth / schemas        (owned elsewhere: HTTP surface, Pydantic models)
        ↓
    repositories/                   the ONLY code that touches tables
        ↓
    tenancy.py                      TenantCtx + firm-scoped Repository base
        ↓
    models.py   db.py   config.py   logging.py

A route handler that imports :mod:`garh_api.models` directly, or builds a ``select()``
of its own, has broken the tenancy guarantee — see :mod:`garh_api.tenancy`.

Deliberately no side effects on import: no engine is created, no logging configured,
no settings validated. ``main.py`` does that in its lifespan hook, so tests and the
Alembic environment can import this package without a database.
"""

from __future__ import annotations

__version__ = "0.1.0"

#: Bumped when the persisted model-document shape changes (``HouseModel.schemaVersion``
#: in playbook §3). Snapshots record it so a fold can migrate old documents.
MODEL_SCHEMA_VERSION = 1

__all__ = ["MODEL_SCHEMA_VERSION", "__version__"]
