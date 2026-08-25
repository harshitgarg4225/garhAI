"""Pydantic v2 request/response models — the HTTP boundary (§11).

Three conventions hold everywhere in this package, and they are load-bearing:

1. **camelCase on the wire, snake_case in Python.** ``alias_generator=to_camel`` plus
   ``populate_by_name=True`` means a handler writes ``base_idx`` while the client sends
   and receives ``baseIdx``. The TypeScript model core is camelCase (see
   ``packages/model``), and having one casing on the wire keeps the op payloads that
   flow through here byte-identical in both languages.

2. **``extra="forbid"`` on every request model.** §13: "Pydantic strict at every
   boundary". A typo'd field is a 422 with the field named, not a silently ignored
   value — the failure mode that lets a client believe it set ``thicknessMm`` when it
   set ``thikcnessMm``.

3. **Lengths are ``StrictInt`` millimetres.** ``StrictInt`` rejects ``2400.0`` and
   ``"2400"``; a float length must never reach the model core, because dimension chains
   and compliance arithmetic assume exact integers. Use :data:`Mm` for anything that is
   a length, thickness or coordinate.

Response models deliberately omit ``firmId``: the client already knows its firm, and
echoing tenant ids invites guessing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr
from pydantic.alias_generators import to_camel

#: A length/coordinate/thickness in integer millimetres. The whole product's unit.
Mm = StrictInt

ItemT = TypeVar("ItemT")


class CamelModel(BaseModel):
    """Base for every schema in this package: camelCase aliases, strict input."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        from_attributes=True,
        # "model" is domain vocabulary here (the house model), not pydantic's; without
        # this, a field like `model_schema_version` trips pydantic's protected-namespace
        # warning at import time.
        protected_namespaces=(),
    )


class ResponseModel(CamelModel):
    """Base for responses.

    ``extra="ignore"``: a response is built from a domain dataclass we control, and
    forbidding extras on the way *out* only creates a way to 500 in the serialiser.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        from_attributes=True,
        protected_namespaces=(),
    )


# ---------------------------------------------------------------------------
# Errors — problem+json (§11 "problem+json errors {code, message, action}")
# ---------------------------------------------------------------------------


class Problem(ResponseModel):
    """The one error shape the API ever returns.

    Golden rule 9: "errors say what to do next". ``action`` is not optional prose — it
    is the one-click next step the UI renders as a button label.
    """

    code: StrictStr = Field(description="Stable machine code, e.g. 'op_sequence_conflict'.")
    message: StrictStr = Field(description="One human sentence. Never a traceback.")
    action: StrictStr = Field(description="What the user (or client) should do next.")
    request_id: Optional[StrictStr] = Field(
        default=None, description="Correlates with the server logs."
    )


# ---------------------------------------------------------------------------
# Pagination (§11 "cursor pagination")
# ---------------------------------------------------------------------------


class CursorPage(ResponseModel, Generic[ItemT]):
    """Keyset page. ``nextCursor`` is opaque — clients must not parse it."""

    items: list[ItemT]
    next_cursor: Optional[StrictStr] = None
    has_more: StrictBool = False

    @classmethod
    def of(cls, items: list[Any], next_cursor: Optional[str]) -> "CursorPage[Any]":
        return cls(items=items, next_cursor=next_cursor, has_more=next_cursor is not None)


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------


class PointMm(CamelModel):
    """A plot-local point. Origin at the plot's SW corner, +X east, +Y north (§3)."""

    x: Mm
    y: Mm


class RoadEdge(CamelModel):
    """A road abutting one plot edge (op 3 ``plot.set_road``).

    ``widthMm = None`` means "this edge has no road" — the explicit clear form, which
    is why the field is nullable rather than absent.
    """

    edge_index: StrictInt = Field(ge=0)
    width_mm: Optional[Mm] = Field(default=None, gt=0)


class Ack(ResponseModel):
    """Minimal 200 body for actions with nothing useful to return."""

    ok: StrictBool = True


class DeletedOut(ResponseModel):
    id: uuid.UUID
    deleted: StrictBool = True


class HealthOut(ResponseModel):
    """``/healthz`` and ``/readyz`` (§18 "healthz per service")."""

    status: StrictStr
    service: StrictStr
    version: StrictStr
    env: StrictStr
    checks: dict[str, StrictBool] = Field(default_factory=dict)


class MetaOut(ResponseModel):
    """``GET /api/v1/meta`` — what the web app needs before it renders anything."""

    service: StrictStr
    version: StrictStr
    env: StrictStr
    api_prefix: StrictStr
    model_schema_version: StrictInt
    flags: dict[str, StrictBool] = Field(default_factory=dict)
    providers: dict[str, StrictStr] = Field(default_factory=dict)
    limits: dict[str, StrictInt] = Field(default_factory=dict)
    server_time: datetime


__all__ = [
    "Ack",
    "CamelModel",
    "CursorPage",
    "DeletedOut",
    "HealthOut",
    "MetaOut",
    "Mm",
    "PointMm",
    "Problem",
    "ResponseModel",
    "RoadEdge",
]
