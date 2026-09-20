"""Read-only reference data: rule packs, furniture, materials, facade kits (§11).

Four catalogues the client needs before it can render anything useful, all of them
static product data rather than tenant data. They are therefore unauthenticated-free of
tenant scoping but still behind the normal access token, and cached hard: a firm's
browser should fetch the furniture catalogue once a session, not once a tool switch.

Where the data comes from
-------------------------

**Rule packs** are files: ``rulepacks/*.json`` plus ``rulepacks/index.json``, authored
by hand and reviewed by an architect (see ``rulepacks/README.md``). They are served
verbatim — the engine, the UI and the citation shown to a user must all quote the same
bytes, and a transformation here would be a second source of truth for a number someone
submits to a municipal office.

**Furniture, materials and facade kits** load from ``catalog/*.json`` when that
directory exists (``GARH_CATALOG_DIR`` overrides the location), and otherwise fall back
to the built-in tables at the bottom of this file. The fallback is not a placeholder: it
carries the real Indian dimensions §17 specifies, so a fresh checkout can place a bed in
a bedroom and get a plan that means something. Every response says which source it used.

Everything is integer millimetres and whole rupees. A furniture footprint feeds
clearance checks and the solver's furniture-fit gate (§5.4), so a float would put drift
into a "does the bed fit" answer.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import Field, StrictInt, StrictStr

from garh_api.catalog_data import (
    SOURCE_BUILTIN,
    SOURCE_FILES,
    catalog_dir,
    reset_catalog_cache,
)
from garh_api.catalog_data import (
    load_catalog as _load_catalog,
)
from garh_api.catalog_data import (
    read_json as _read_json,
)
from garh_api.logging import get_logger
from garh_api.routers import ApiError, TenantDep, repo_root
from garh_api.schemas import ResponseModel

_log = get_logger(__name__)

router = APIRouter(tags=["catalog"])

#: Reference data changes on deploy, not on request. An hour of browser caching plus an
#: ETag means a tool switch costs a 304 at worst.
CACHE_SECONDS = 3600


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RulePackSummary(ResponseModel):
    """One entry of ``GET /rulepacks``.

    Built by :func:`_normalise_index_entry`, never validated straight from the manifest:
    ``rulepacks/index.json`` uses the pack authors' vocabulary (``pack``, ``title``,
    ``review``) and this is the HTTP vocabulary. Mapping in one function beats teaching
    every consumer both spellings.
    """

    id: StrictStr
    name: StrictStr
    version: StrictStr
    extends: StrictStr | None = None
    kind: StrictStr | None = Field(
        default=None, description="'code' (NBC), 'city' (bye-laws) or 'advisory' (Vastu)."
    )
    selectable: bool = Field(
        default=True,
        description="False for packs that are always loaded via `extends` rather than "
        "chosen by a user — nbc-core is not a city choice.",
    )
    rule_count: StrictInt = 0
    citations_base: StrictStr | None = None
    confidence: StrictStr | None = Field(
        default=None,
        description="'seed' until a reviewing architect signs the pack off (§6).",
    )
    review_status: StrictStr | None = None


class RulePackListOut(ResponseModel):
    packs: list[RulePackSummary] = Field(default_factory=list)
    source: StrictStr = SOURCE_FILES


class CatalogItemOut(ResponseModel):
    """One furniture item. All dimensions integer millimetres (§3)."""

    id: StrictStr
    name: StrictStr
    category: StrictStr
    width_mm: StrictInt
    depth_mm: StrictInt
    height_mm: StrictInt
    room_types: list[StrictStr] = Field(default_factory=list)
    clearance_mm: StrictInt = Field(
        default=0, description="Free space this item needs in front of it to be usable."
    )
    asset_url: StrictStr | None = None
    tags: list[StrictStr] = Field(default_factory=list)


class MaterialOut(ResponseModel):
    """One material assignment target (op 29 ``material.assign``)."""

    id: StrictStr
    name: StrictStr
    category: StrictStr
    finish: StrictStr | None = None
    color_hex: StrictStr | None = None
    #: Texture FAMILY the 3D view draws procedurally (tile, brick, wood, stone,
    #: concrete, plaster, speckle, vein, metal, glass). The catalogue has always
    #: carried it; this response dropped it, so every material rendered as a flat
    #: hex and Kota stone looked like vitrified tile.
    texture: StrictStr | None = None
    #: Optional image map. The SPA's CSP allows same-origin and data: URLs only;
    #: anything else falls back to the procedural family (features/canvas/three).
    texture_url: StrictStr | None = None
    #: Whole rupees per square metre. Indicative, for the cost chip — never a quotation.
    price_inr_per_sqm: StrictInt | None = None
    surface_groups: list[StrictStr] = Field(default_factory=list)


class FacadeKitOut(ResponseModel):
    """A facade kit (§8): data plus the parameters its generator accepts."""

    id: StrictStr
    name: StrictStr
    description: StrictStr = ""
    components: dict[str, Any] = Field(default_factory=dict)
    colorways: list[dict[str, Any]] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)


class CatalogOut(ResponseModel):
    """Wrapper carrying provenance — a client should be able to tell which table it got."""

    source: StrictStr
    count: StrictInt = 0
    items: list[dict[str, Any]] = Field(default_factory=list)


def _validated(model: type[ResponseModel], items: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Validate catalogue rows through their model on the way out.

    This is why :class:`CatalogItemOut` and friends exist: dimensions are ``StrictInt``,
    so a hand-edited ``catalog/furniture.json`` with ``"widthMm": 1524.0`` fails here,
    loudly, at the boundary — instead of putting a float into the solver's furniture-fit
    arithmetic and the clearance checks that depend on it (§3 "never a float for a
    length"). Unknown fields are dropped rather than passed through, so the response
    shape is the documented one.
    """
    return [model.model_validate(item).model_dump(by_alias=True) for item in items]


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


def rulepack_dir() -> str:
    # Same precedence as garh_rules.packs.rulepack_dir and garh_api.seed.catalog:
    # GARH_RULEPACK_DIR (code-facing name) then RULEPACK_DIR (what compose sets).
    # Relative overrides resolve against the repo root, never the cwd.
    override = os.environ.get("GARH_RULEPACK_DIR") or os.environ.get("RULEPACK_DIR")
    if override:
        return override if os.path.isabs(override) else os.path.join(repo_root(), override)
    return os.path.join(repo_root(), "rulepacks")


@lru_cache(maxsize=1)
def _load_rulepack_index() -> list[dict[str, Any]]:
    """Read ``rulepacks/index.json``, falling back to scanning the directory.

    The manifest is authoritative when present because it records review status and
    ordering that a directory listing cannot. Scanning is the fallback so a pack dropped
    in during development is visible without editing two files.
    """
    directory = rulepack_dir()
    manifest = _read_json(os.path.join(directory, "index.json"))
    entries: list[Any] | None = None
    if isinstance(manifest, dict) and isinstance(manifest.get("packs"), list):
        entries = manifest["packs"]
    elif isinstance(manifest, list):
        entries = manifest
    if entries is not None:
        return [_normalise_index_entry(item) for item in entries if isinstance(item, dict)]

    scanned: list[dict[str, Any]] = []
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json") or name == "index.json":
                continue
            pack = _read_json(os.path.join(directory, name))
            if isinstance(pack, dict) and pack.get("pack"):
                scanned.append(_normalise_index_entry(pack))
    if not scanned:
        _log.warning("catalog.no_rulepacks", directory=directory)
    return scanned


def _review_status(value: Any) -> str | None:
    """``review`` is a bare string in ``index.json`` and an object inside a pack file."""
    if isinstance(value, dict):
        status = value.get("status")
        return str(status) if status else None
    return str(value) if value else None


def _normalise_index_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Manifest entry (authors' vocabulary) → :class:`RulePackSummary` fields."""
    rules = entry.get("rules")
    rule_count = entry.get("ruleCount")
    if not isinstance(rule_count, int):
        rule_count = len(rules) if isinstance(rules, list) else 0
    return {
        "id": str(entry.get("pack") or entry.get("id") or ""),
        "name": str(entry.get("title") or entry.get("name") or entry.get("pack") or ""),
        "version": str(entry.get("version") or ""),
        "extends": entry.get("extends"),
        "kind": entry.get("kind"),
        "selectable": bool(entry.get("selectable", True)),
        "ruleCount": rule_count,
        "citationsBase": entry.get("citations_base") or entry.get("citationsBase"),
        "confidence": entry.get("confidence"),
        "reviewStatus": _review_status(entry.get("review")),
    }


@lru_cache(maxsize=8)
def _load_rulepack(pack_id: str) -> dict[str, Any] | None:
    """Load one pack by id. ``lru_cache`` keeps the hot packs in memory between requests."""
    safe = _safe_pack_id(pack_id)
    data = _read_json(os.path.join(rulepack_dir(), "%s.json" % safe))
    return data if isinstance(data, dict) else None


def _safe_pack_id(pack_id: str) -> str:
    """Reject anything that is not a plain pack id.

    This value becomes a filename. ``../../etc/passwd`` must not read a file, and a
    whitelist of characters is the only version of this check that is obviously correct.
    """
    cleaned = (pack_id or "").strip()
    if not cleaned or len(cleaned) > 64:
        raise ApiError(
            "There's no rule pack by that name.",
            status=404,
            code="not_found",
            action="Call GET /rulepacks to see what's available.",
        )
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
    if not set(cleaned.lower()) <= allowed or cleaned != cleaned.lower():
        raise ApiError(
            "There's no rule pack by that name.",
            status=404,
            code="not_found",
            action="Call GET /rulepacks to see what's available.",
        )
    return cleaned


def reset_caches() -> None:
    """Drop every cached rulepack and catalogue. For tests and a future reload hook."""
    _load_rulepack_index.cache_clear()
    _load_rulepack.cache_clear()
    reset_catalog_cache()


def _cached(response: Response, payload: Any) -> None:
    """Attach ``Cache-Control`` and a content-derived ``ETag``.

    The ETag is a hash of the payload, so a redeployed pack invalidates it automatically
    and an unchanged one keeps 304-ing. Starlette compares ``If-None-Match`` itself.
    """
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    response.headers["cache-control"] = "private, max-age=%d" % CACHE_SECONDS
    response.headers["etag"] = '"%s"' % hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/rulepacks", response_model=RulePackListOut, summary="Available rule packs")
async def list_rulepacks(response: Response, ctx: TenantDep) -> RulePackListOut:
    """The packs the compliance engine can load (§6).

    ``confidence`` and ``reviewStatus`` travel with every pack because the UI is required
    to show them: a seeded bye-law value that no architect has checked must never look
    like a verified one (golden rule 4).
    """
    packs = _load_rulepack_index()
    _cached(response, packs)
    return RulePackListOut(
        packs=[RulePackSummary.model_validate(item) for item in packs],
        source=SOURCE_FILES,
    )


@router.get(
    "/rulepacks/{pack_id}",
    summary="One rule pack, verbatim",
    response_model=dict,
)
async def get_rulepack(
    pack_id: str,
    response: Response,
    ctx: TenantDep,
) -> dict[str, Any]:
    """The pack exactly as authored — same bytes the engine and the citations use."""
    pack = _load_rulepack(pack_id)
    if pack is None:
        raise ApiError(
            "There's no rule pack called %r." % pack_id,
            status=404,
            code="not_found",
            action="Call GET /rulepacks to see what's available.",
        )
    _cached(response, pack)
    return pack


@router.get(
    "/catalog/furniture",
    response_model=CatalogOut,
    summary="Furniture catalogue (integer mm, Indian sizes)",
)
async def get_furniture(
    response: Response,
    ctx: TenantDep,
    category: str | None = Query(default=None, max_length=40),
    room_type: str | None = Query(default=None, alias="roomType", max_length=40),
) -> CatalogOut:
    """Filterable by category and by the room type an item belongs in.

    The solver's furniture-fit gate (§5.4) places a standard set per room type from this
    catalogue, so ``roomType`` is the filter the editor's furniture tool uses too — one
    list, one set of dimensions, no second opinion about how big a bed is.
    """
    source, items = _load_catalog("furniture")
    selected = _validated(
        CatalogItemOut,
        tuple(
            item
            for item in items
            if (category is None or item.get("category") == category)
            and (room_type is None or room_type in (item.get("roomTypes") or []))
        ),
    )
    _cached(response, selected)
    return CatalogOut(source=source, count=len(selected), items=selected)


@router.get(
    "/catalog/materials",
    response_model=CatalogOut,
    summary="Material catalogue",
)
async def get_materials(
    response: Response,
    ctx: TenantDep,
    category: str | None = Query(default=None, max_length=40),
) -> CatalogOut:
    """Targets for op 29 ``material.assign``. Prices are indicative whole rupees."""
    source, items = _load_catalog("materials")
    selected = _validated(
        MaterialOut,
        tuple(item for item in items if category is None or item.get("category") == category),
    )
    _cached(response, selected)
    return CatalogOut(source=source, count=len(selected), items=selected)


@router.get(
    "/catalog/facade-kits",
    response_model=CatalogOut,
    summary="Facade kits (§8)",
)
async def get_facade_kits(response: Response, ctx: TenantDep) -> CatalogOut:
    """The two MVP kits. Applying one is op 27 ``facade.apply_kit``.

    A kit never touches walls or rooms — its generator emits separate meshes tagged with
    a ``facadeComponentId``, editable through op 28. That isolation is why a facade can
    be swapped without invalidating a compliance run.
    """
    source, items = _load_catalog("facade-kits")
    kits = _validated(FacadeKitOut, items)
    _cached(response, kits)
    return CatalogOut(source=source, count=len(kits), items=kits)


@router.get("/catalog", response_model=dict, summary="Everything, in one request")
async def get_all_catalogs(response: Response, ctx: TenantDep) -> dict[str, Any]:
    """One round trip for the editor's cold start (§15 micro-speed).

    Three separate fetches on the way into the canvas is three chances to be slow on a
    4G connection; this is the payload the app actually wants.
    """
    furniture_source, raw_furniture = _load_catalog("furniture")
    material_source, raw_materials = _load_catalog("materials")
    kit_source, raw_kits = _load_catalog("facade-kits")
    furniture = _validated(CatalogItemOut, raw_furniture)
    materials = _validated(MaterialOut, raw_materials)
    kits = _validated(FacadeKitOut, raw_kits)
    payload = {
        "furniture": {
            "source": furniture_source,
            "count": len(furniture),
            "items": furniture,
        },
        "materials": {
            "source": material_source,
            "count": len(materials),
            "items": materials,
        },
        "facadeKits": {"source": kit_source, "count": len(kits), "items": kits},
        "rulepacks": _load_rulepack_index(),
    }
    _cached(response, payload)
    return payload


__all__ = [
    "CACHE_SECONDS",
    "SOURCE_BUILTIN",
    "SOURCE_FILES",
    "CatalogItemOut",
    "CatalogOut",
    "FacadeKitOut",
    "MaterialOut",
    "RulePackListOut",
    "RulePackSummary",
    "catalog_dir",
    "reset_caches",
    "router",
    "rulepack_dir",
]
