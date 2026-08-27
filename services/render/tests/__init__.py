"""Tests for the §9 render service.

Unlike ``services/drawings/tests`` these are NOT bare-interpreter runnable: the render
providers legitimately need Pillow, and the stability provider needs httpx — both base
dependencies of ``garh-services``. They stay hermetic in the other sense that matters:
no network. The stability suite runs entirely against ``httpx.MockTransport``.
"""
