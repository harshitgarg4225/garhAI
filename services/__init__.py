"""Garh AI worker services.

Three long-running queue consumers and two provider packages:

===================  ==============================  ==============================
package              entrypoint                      playbook
===================  ==============================  ==============================
``services.common``  (library)                       §18 worker runtime, queues, logs
``services.solver``  ``python -m services.solver.worker``    §5  CP-SAT layout engine
``services.render``  ``python -m services.render.worker``    §9  provider iface + mock
``services.drawings`` ``python -m services.drawings.worker`` §7  auto-dim, sheets, DXF
``services.llm``     (library, imported by the API)  §10 brief parse + copilot
===================  ==============================  ==============================

Two properties hold across all of them and must not be broken:

1. **Zero keys, zero GPUs.** ``PROVIDER_LLM=mock`` and ``PROVIDER_RENDER=mock`` are
   the defaults, both mocks are deterministic, and no ML dependency is imported on
   the mock path. The entire product is e2e-testable on a laptop.
2. **Workers are stateless.** They read a self-contained job envelope from Redis,
   fetch inputs through presigned URLs, and publish real progress events. They hold
   no session, no tenant state, and — deliberately — no database connection; see
   ``services/common/jobstore.py`` for who persists job rows and why.
"""

from __future__ import annotations

__all__: list[str] = []
