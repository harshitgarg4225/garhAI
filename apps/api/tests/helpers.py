"""Small helpers shared by the tests. No fixtures, no state — import freely."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from garh_api.errors import PROBLEM_CONTENT_TYPE


def main_branch(project_id: uuid.UUID) -> uuid.UUID:
    """A project's derived default op branch.

    Delegates to the router layer rather than re-deriving the uuid5, so a test can never
    disagree with the server about which branch "the branch" is.
    """
    from garh_api.routers import main_branch_id

    return main_branch_id(project_id)


def problem(response: httpx.Response) -> dict[str, Any]:
    """Parse a problem+json body, asserting the parts of the contract that always hold.

    Every error the API emits is ``application/problem+json`` with ``code``, ``message`` and
    a non-empty ``action`` (golden rule 9: errors say what to do next). Asserting that here
    means every test that touches an error path also checks the envelope.
    """
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith(PROBLEM_CONTENT_TYPE), (
        "errors must be %s, got %r for %s %s"
        % (PROBLEM_CONTENT_TYPE, content_type, response.request.method, response.request.url)
    )
    body = response.json()
    assert isinstance(body, dict), body
    for field in ("code", "message", "action"):
        assert isinstance(body.get(field), str) and body[field].strip(), (
            "problem+json needs a non-empty %r; got %r" % (field, body)
        )
    return body


def op_payload(op_type: str, **payload: Any) -> dict[str, Any]:
    """A wire-shaped op, camelCase, ready for ``POST /projects/:id/ops``."""
    return {"type": op_type, "payload": payload}


def sorted_codes(body: dict[str, Any]) -> list[str]:
    """Validation-issue codes from a 422 body, sorted — order is not part of the contract."""
    return sorted(str(issue.get("code")) for issue in body.get("errors", body.get("issues", [])))


__all__ = ["main_branch", "op_payload", "problem", "sorted_codes"]
