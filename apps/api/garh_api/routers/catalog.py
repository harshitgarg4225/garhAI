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

from garh_api.logging import get_logger
from garh_api.routers import ApiError, TenantDep, repo_root
from garh_api.schemas import ResponseModel

_log = get_logger(__name__)

router = APIRouter(tags=["catalog"])

#: Reference data changes on deploy, not on request. An hour of browser caching plus an
#: ETag means a tool switch costs a 304 at worst.
CACHE_SECONDS = 3600

SOURCE_FILES = "files"
SOURCE_BUILTIN = "builtin"


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
    return (
        os.environ.get("GARH_RULEPACK_DIR")
        or os.environ.get("RULEPACK_DIR")
        or os.path.join(repo_root(), "rulepacks")
    )


#: Where the JSON overrides live, in precedence order after ``GARH_CATALOG_DIR``.
#:
#: ``fixtures/catalog`` is in this list because that is where the files actually
#: are: ``python -m garh_api.seed`` validates them there and
#: ``apps/api/tests/test_catalog_fixtures.py`` asserts against them there. While
#: the only fallback was ``<repo>/catalog`` — a directory that has never
#: existed — a host-run API served the compiled-in table and the authored,
#: validated, tested files were decorative. docker-compose.yml now also sets
#: ``GARH_CATALOG_DIR`` explicitly, so both paths agree.
_CATALOG_DIR_CANDIDATES: tuple[str, ...] = ("catalog", os.path.join("fixtures", "catalog"))


def catalog_dir() -> str:
    override = os.environ.get("GARH_CATALOG_DIR")
    if override:
        return override
    root = repo_root()
    for candidate in _CATALOG_DIR_CANDIDATES:
        path = os.path.join(root, candidate)
        if os.path.isdir(path):
            return path
    # Nothing on disk: return the first candidate so the "not found" log line
    # names one definite path instead of a list of maybes.
    return os.path.join(root, _CATALOG_DIR_CANDIDATES[0])


def _read_json(path: str) -> Any | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        _log.warning("catalog.read_failed", path=os.path.basename(path), error=str(exc))
        return None


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


@lru_cache(maxsize=4)
def _load_catalog(name: str) -> tuple[str, tuple[Any, ...]]:
    """``(source, items)`` for one catalogue — files if present, else the built-in table."""
    path = os.path.join(catalog_dir(), "%s.json" % name)
    data = _read_json(path) if os.path.isfile(path) else None
    items: list[Any] | None = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
    if items:
        return SOURCE_FILES, tuple(items)
    return SOURCE_BUILTIN, tuple(_BUILTIN[name])


def reset_caches() -> None:
    """Drop every cached catalogue. For tests and for a future admin reload hook."""
    _load_rulepack_index.cache_clear()
    _load_rulepack.cache_clear()
    _load_catalog.cache_clear()


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


# ---------------------------------------------------------------------------
# Built-in tables (used when catalog/*.json is absent)
# ---------------------------------------------------------------------------
#
# Real Indian dimensions, from §17's seed list. Integer millimetres throughout.
# `clearanceMm` is the free space in front of an item for it to be usable — the number
# the furniture-fit gate actually tests, and the reason a 3.0m bedroom with a wardrobe
# on the wrong wall fails.

_FURNITURE: list[dict[str, Any]] = [
    # -- bedroom -----------------------------------------------------------
    {
        "id": "bed-queen",
        "name": "Queen bed",
        "category": "bed",
        "widthMm": 1525,
        "depthMm": 1900,
        "heightMm": 600,
        "roomTypes": ["bedroom", "bedroom_master", "guest_bedroom"],
        "clearanceMm": 600,
    },
    {
        "id": "bed-king",
        "name": "King bed",
        "category": "bed",
        "widthMm": 1830,
        "depthMm": 2000,
        "heightMm": 600,
        "roomTypes": ["bedroom_master"],
        "clearanceMm": 600,
    },
    {
        "id": "bed-single",
        "name": "Single bed",
        "category": "bed",
        "widthMm": 900,
        "depthMm": 1900,
        "heightMm": 600,
        "roomTypes": ["bedroom", "guest_bedroom", "servant_room"],
        "clearanceMm": 600,
    },
    {
        "id": "bunk-bed",
        "name": "Bunk bed",
        "category": "bed",
        "widthMm": 900,
        "depthMm": 1900,
        "heightMm": 1700,
        "roomTypes": ["bedroom", "guest_bedroom"],
        "clearanceMm": 600,
    },
    {
        "id": "wardrobe-2door",
        "name": "Wardrobe (2 door)",
        "category": "storage",
        "widthMm": 1200,
        "depthMm": 600,
        "heightMm": 2100,
        "roomTypes": ["bedroom", "guest_bedroom", "dress"],
        "clearanceMm": 750,
    },
    {
        "id": "wardrobe-3door",
        "name": "Wardrobe (3 door)",
        "category": "storage",
        "widthMm": 1800,
        "depthMm": 600,
        "heightMm": 2100,
        "roomTypes": ["bedroom_master", "bedroom", "dress"],
        "clearanceMm": 750,
    },
    {
        "id": "bedside-table",
        "name": "Bedside table",
        "category": "table",
        "widthMm": 450,
        "depthMm": 400,
        "heightMm": 600,
        "roomTypes": ["bedroom", "bedroom_master", "guest_bedroom"],
        "clearanceMm": 300,
    },
    {
        "id": "dressing-table",
        "name": "Dressing table",
        "category": "table",
        "widthMm": 900,
        "depthMm": 450,
        "heightMm": 1800,
        "roomTypes": ["bedroom_master", "bedroom", "dress"],
        "clearanceMm": 750,
    },
    {
        "id": "study-table",
        "name": "Study table",
        "category": "table",
        "widthMm": 1200,
        "depthMm": 600,
        "heightMm": 750,
        "roomTypes": ["study", "bedroom", "guest_bedroom"],
        "clearanceMm": 750,
    },
    {
        "id": "bookshelf",
        "name": "Bookshelf",
        "category": "storage",
        "widthMm": 900,
        "depthMm": 350,
        "heightMm": 1800,
        "roomTypes": ["study", "living", "bedroom", "store"],
        "clearanceMm": 600,
    },
    # -- living / dining ---------------------------------------------------
    {
        "id": "sofa-3seat",
        "name": "Sofa (3 seat)",
        "category": "seating",
        "widthMm": 2100,
        "depthMm": 900,
        "heightMm": 800,
        "roomTypes": ["living", "living_dining"],
        "clearanceMm": 750,
    },
    {
        "id": "sofa-2seat",
        "name": "Sofa (2 seat)",
        "category": "seating",
        "widthMm": 1500,
        "depthMm": 900,
        "heightMm": 800,
        "roomTypes": ["living", "living_dining"],
        "clearanceMm": 750,
    },
    {
        "id": "armchair",
        "name": "Armchair",
        "category": "seating",
        "widthMm": 800,
        "depthMm": 850,
        "heightMm": 800,
        "roomTypes": ["living", "study", "balcony", "terrace"],
        "clearanceMm": 600,
    },
    {
        "id": "coffee-table",
        "name": "Coffee table",
        "category": "table",
        "widthMm": 1050,
        "depthMm": 600,
        "heightMm": 400,
        "roomTypes": ["living", "living_dining"],
        "clearanceMm": 450,
    },
    {
        "id": "tv-unit",
        "name": "TV unit",
        "category": "storage",
        "widthMm": 1800,
        "depthMm": 450,
        "heightMm": 500,
        "roomTypes": ["living", "living_dining", "bedroom_master"],
        "clearanceMm": 900,
    },
    {
        "id": "dining-4",
        "name": "Dining table (4 seat)",
        "category": "table",
        "widthMm": 1200,
        "depthMm": 750,
        "heightMm": 750,
        "roomTypes": ["dining", "living_dining"],
        "clearanceMm": 900,
    },
    {
        "id": "dining-6",
        "name": "Dining table (6 seat)",
        "category": "table",
        "widthMm": 1500,
        "depthMm": 900,
        "heightMm": 750,
        "roomTypes": ["dining", "living_dining"],
        "clearanceMm": 900,
    },
    {
        "id": "dining-8",
        "name": "Dining table (8 seat)",
        "category": "table",
        "widthMm": 2100,
        "depthMm": 1000,
        "heightMm": 750,
        "roomTypes": ["dining"],
        "clearanceMm": 900,
    },
    {
        "id": "dining-chair",
        "name": "Dining chair",
        "category": "seating",
        "widthMm": 450,
        "depthMm": 500,
        "heightMm": 900,
        "roomTypes": ["dining", "living_dining"],
        "clearanceMm": 600,
    },
    {
        "id": "shoe-rack",
        "name": "Shoe rack",
        "category": "storage",
        "widthMm": 900,
        "depthMm": 350,
        "heightMm": 900,
        "roomTypes": ["foyer", "living", "lobby", "store"],
        "clearanceMm": 600,
    },
    # -- kitchen / utility -------------------------------------------------
    {
        "id": "kitchen-counter",
        "name": "Kitchen counter (1m module)",
        "category": "kitchen",
        "widthMm": 1000,
        "depthMm": 600,
        "heightMm": 900,
        "roomTypes": ["kitchen"],
        "clearanceMm": 1050,
    },
    {
        "id": "kitchen-sink",
        "name": "Kitchen sink",
        "category": "kitchen",
        "widthMm": 900,
        "depthMm": 550,
        "heightMm": 200,
        "roomTypes": ["kitchen"],
        "clearanceMm": 1050,
    },
    {
        "id": "hob-4burner",
        "name": "Hob (4 burner)",
        "category": "kitchen",
        "widthMm": 600,
        "depthMm": 520,
        "heightMm": 100,
        "roomTypes": ["kitchen"],
        "clearanceMm": 1050,
    },
    {
        "id": "refrigerator",
        "name": "Refrigerator",
        "category": "appliance",
        "widthMm": 700,
        "depthMm": 700,
        "heightMm": 1800,
        "roomTypes": ["kitchen", "utility", "store"],
        "clearanceMm": 900,
    },
    {
        "id": "washing-machine",
        "name": "Washing machine",
        "category": "appliance",
        "widthMm": 600,
        "depthMm": 600,
        "heightMm": 850,
        "roomTypes": ["utility", "bath_wc"],
        "clearanceMm": 750,
    },
    {
        "id": "water-heater",
        "name": "Geyser",
        "category": "appliance",
        "widthMm": 400,
        "depthMm": 400,
        "heightMm": 600,
        "roomTypes": ["bath", "bath_wc", "utility"],
        "clearanceMm": 0,
    },
    # -- bath --------------------------------------------------------------
    {
        "id": "wc-floor",
        "name": "WC (floor mounted)",
        "category": "sanitary",
        "widthMm": 700,
        "depthMm": 400,
        "heightMm": 780,
        "roomTypes": ["bath", "wc", "bath_wc"],
        "clearanceMm": 600,
    },
    {
        "id": "wc-wall-hung",
        "name": "WC (wall hung)",
        "category": "sanitary",
        "widthMm": 550,
        "depthMm": 360,
        "heightMm": 400,
        "roomTypes": ["bath", "wc", "bath_wc"],
        "clearanceMm": 600,
    },
    {
        "id": "washbasin",
        "name": "Washbasin",
        "category": "sanitary",
        "widthMm": 550,
        "depthMm": 450,
        "heightMm": 850,
        "roomTypes": ["bath", "wc", "bath_wc", "dining"],
        "clearanceMm": 600,
    },
    {
        "id": "shower-area",
        "name": "Shower area",
        "category": "sanitary",
        "widthMm": 900,
        "depthMm": 900,
        "heightMm": 2100,
        "roomTypes": ["bath", "bath_wc"],
        "clearanceMm": 0,
    },
    {
        "id": "bathtub",
        "name": "Bathtub",
        "category": "sanitary",
        "widthMm": 1700,
        "depthMm": 750,
        "heightMm": 600,
        "roomTypes": ["bath"],
        "clearanceMm": 750,
    },
    # -- other -------------------------------------------------------------
    {
        "id": "pooja-unit",
        "name": "Pooja unit",
        "category": "storage",
        "widthMm": 900,
        "depthMm": 450,
        "heightMm": 1800,
        "roomTypes": ["pooja", "living"],
        "clearanceMm": 900,
    },
    {
        "id": "car-hatchback",
        "name": "Car (hatchback)",
        "category": "vehicle",
        "widthMm": 1700,
        "depthMm": 3800,
        "heightMm": 1500,
        "roomTypes": ["garage", "stilt", "porch"],
        "clearanceMm": 600,
    },
    {
        "id": "car-sedan",
        "name": "Car (sedan / SUV)",
        "category": "vehicle",
        "widthMm": 1800,
        "depthMm": 4800,
        "heightMm": 1500,
        "roomTypes": ["garage", "stilt", "porch"],
        "clearanceMm": 600,
    },
    {
        "id": "two-wheeler",
        "name": "Scooter / motorcycle",
        "category": "vehicle",
        "widthMm": 700,
        "depthMm": 1800,
        "heightMm": 1100,
        "roomTypes": ["garage", "stilt", "porch"],
        "clearanceMm": 450,
    },
    {
        "id": "water-tank-oht",
        "name": "Overhead water tank (1000 L)",
        "category": "service",
        "widthMm": 1100,
        "depthMm": 1100,
        "heightMm": 1300,
        "roomTypes": ["terrace"],
        "clearanceMm": 450,
    },
]

_MATERIALS: list[dict[str, Any]] = [
    {
        "id": "vitrified-tile-600",
        "name": "Vitrified tile 600×600",
        "category": "floor",
        "finish": "glossy",
        "colorHex": "#E8E4DC",
        "priceInrPerSqm": 850,
        "surfaceGroups": ["floor.interior"],
    },
    {
        "id": "vitrified-tile-800",
        "name": "Vitrified tile 800×800",
        "category": "floor",
        "finish": "matte",
        "colorHex": "#DED8CE",
        "priceInrPerSqm": 1150,
        "surfaceGroups": ["floor.interior"],
    },
    {
        "id": "granite-flooring",
        "name": "Granite flooring",
        "category": "floor",
        "finish": "polished",
        "colorHex": "#4A4A4A",
        "priceInrPerSqm": 2400,
        "surfaceGroups": ["floor.interior", "floor.stair"],
    },
    {
        "id": "kota-stone",
        "name": "Kota stone",
        "category": "floor",
        "finish": "honed",
        "colorHex": "#6E7B6B",
        "priceInrPerSqm": 900,
        "surfaceGroups": ["floor.utility", "floor.terrace"],
    },
    {
        "id": "marble-italian",
        "name": "Italian marble",
        "category": "floor",
        "finish": "polished",
        "colorHex": "#F2F0EA",
        "priceInrPerSqm": 4500,
        "surfaceGroups": ["floor.interior"],
    },
    {
        "id": "wooden-laminate",
        "name": "Wooden laminate",
        "category": "floor",
        "finish": "textured",
        "colorHex": "#8B6A45",
        "priceInrPerSqm": 1300,
        "surfaceGroups": ["floor.bedroom"],
    },
    {
        "id": "cement-ips",
        "name": "IPS cement finish",
        "category": "floor",
        "finish": "matte",
        "colorHex": "#9C9C97",
        "priceInrPerSqm": 450,
        "surfaceGroups": ["floor.utility", "floor.parking"],
    },
    {
        "id": "anti-skid-tile",
        "name": "Anti-skid ceramic tile",
        "category": "floor",
        "finish": "matte",
        "colorHex": "#C9C4BA",
        "priceInrPerSqm": 650,
        "surfaceGroups": ["floor.bath", "floor.terrace"],
    },
    {
        "id": "ceramic-wall-tile",
        "name": "Ceramic wall tile",
        "category": "wall",
        "finish": "glossy",
        "colorHex": "#F5F5F0",
        "priceInrPerSqm": 600,
        "surfaceGroups": ["wall.bath", "wall.kitchen"],
    },
    {
        "id": "interior-emulsion",
        "name": "Interior emulsion paint",
        "category": "wall",
        "finish": "matte",
        "colorHex": "#F7F4EF",
        "priceInrPerSqm": 220,
        "surfaceGroups": ["wall.interior", "ceiling.interior"],
    },
    {
        "id": "exterior-texture",
        "name": "Exterior texture paint",
        "category": "wall",
        "finish": "textured",
        "colorHex": "#E3DDD2",
        "priceInrPerSqm": 380,
        "surfaceGroups": ["wall.exterior"],
    },
    {
        "id": "exposed-brick",
        "name": "Exposed brick",
        "category": "wall",
        "finish": "natural",
        "colorHex": "#9C4A2F",
        "priceInrPerSqm": 1400,
        "surfaceGroups": ["wall.exterior", "wall.feature"],
    },
    {
        "id": "exposed-concrete",
        "name": "Exposed concrete",
        "category": "wall",
        "finish": "board-formed",
        "colorHex": "#A8A8A3",
        "priceInrPerSqm": 1600,
        "surfaceGroups": ["wall.exterior", "wall.feature"],
    },
    {
        "id": "stone-cladding",
        "name": "Natural stone cladding",
        "category": "wall",
        "finish": "split-face",
        "colorHex": "#7A6E5D",
        "priceInrPerSqm": 2200,
        "surfaceGroups": ["wall.exterior", "wall.feature"],
    },
    {
        "id": "wpc-cladding",
        "name": "WPC wood-finish cladding",
        "category": "wall",
        "finish": "wood-grain",
        "colorHex": "#7A5230",
        "priceInrPerSqm": 2000,
        "surfaceGroups": ["wall.exterior", "facade.cladding"],
    },
    {
        "id": "acp-panel",
        "name": "ACP panel",
        "category": "wall",
        "finish": "matte",
        "colorHex": "#3C3C3C",
        "priceInrPerSqm": 1800,
        "surfaceGroups": ["facade.cladding"],
    },
    {
        "id": "glass-clear",
        "name": "Clear float glass",
        "category": "glazing",
        "finish": "clear",
        "colorHex": "#CFE3E8",
        "priceInrPerSqm": 1200,
        "surfaceGroups": ["window.glazing"],
    },
    {
        "id": "glass-tinted",
        "name": "Tinted glass",
        "category": "glazing",
        "finish": "tinted",
        "colorHex": "#7E9AA3",
        "priceInrPerSqm": 1600,
        "surfaceGroups": ["window.glazing", "facade.glazing"],
    },
    {
        "id": "upvc-window",
        "name": "uPVC window frame",
        "category": "joinery",
        "finish": "matte",
        "colorHex": "#FFFFFF",
        "priceInrPerSqm": 4200,
        "surfaceGroups": ["window.frame"],
    },
    {
        "id": "aluminium-window",
        "name": "Aluminium window frame",
        "category": "joinery",
        "finish": "anodised",
        "colorHex": "#6B6B6B",
        "priceInrPerSqm": 5200,
        "surfaceGroups": ["window.frame"],
    },
    {
        "id": "teak-door",
        "name": "Teak wood door",
        "category": "joinery",
        "finish": "polished",
        "colorHex": "#6B4423",
        "priceInrPerSqm": 9000,
        "surfaceGroups": ["door.main"],
    },
    {
        "id": "flush-door",
        "name": "Flush door",
        "category": "joinery",
        "finish": "laminated",
        "colorHex": "#B08A5E",
        "priceInrPerSqm": 2600,
        "surfaceGroups": ["door.internal"],
    },
    {
        "id": "ms-railing",
        "name": "MS railing",
        "category": "railing",
        "finish": "powder-coated",
        "colorHex": "#2E2E2E",
        "priceInrPerSqm": 2800,
        "surfaceGroups": ["railing.balcony", "railing.stair"],
    },
    {
        "id": "ss-railing",
        "name": "Stainless steel railing",
        "category": "railing",
        "finish": "brushed",
        "colorHex": "#B8BCC0",
        "priceInrPerSqm": 4500,
        "surfaceGroups": ["railing.balcony", "railing.stair"],
    },
    {
        "id": "glass-railing",
        "name": "Toughened glass railing",
        "category": "railing",
        "finish": "clear",
        "colorHex": "#D6E7EC",
        "priceInrPerSqm": 6500,
        "surfaceGroups": ["railing.balcony"],
    },
    {
        "id": "clay-roof-tile",
        "name": "Mangalore clay roof tile",
        "category": "roof",
        "finish": "natural",
        "colorHex": "#B5502F",
        "priceInrPerSqm": 1100,
        "surfaceGroups": ["roof.pitched"],
    },
    {
        "id": "waterproof-membrane",
        "name": "Waterproofing membrane",
        "category": "roof",
        "finish": "matte",
        "colorHex": "#8E8E8E",
        "priceInrPerSqm": 550,
        "surfaceGroups": ["roof.flat", "floor.terrace"],
    },
]

#: The two MVP kits, exactly as §8 specifies them. ``params`` are what op 28
#: (``facade.edit_component``) may patch; the generator reads nothing else.
_FACADE_KITS: list[dict[str, Any]] = [
    {
        "id": "contemporary",
        "name": "Contemporary",
        "description": "Flat chajjas, a full-height cladding band at the stair bay, "
        "slim MS railings — monochrome with a wood accent.",
        "components": {
            "windowTrim": {"style": "flush-band", "widthMm": 100, "projectionMm": 40},
            "chajja": {
                "style": "flat",
                "projectionMm": 600,
                "thicknessMm": 100,
                "allowedProjectionsMm": [600, 750],
            },
            "parapetProfile": {"style": "banded", "heightMm": 1050, "capThicknessMm": 75},
            "claddingZones": {
                "rule": "stack full-height at entry bay",
                "materialId": "wpc-cladding",
                "widthMm": 1200,
            },
            "porch": {"style": "cantilever", "projectionMm": 1800, "thicknessMm": 200},
            "railing": {"style": "ms-slim", "heightMm": 1050, "materialId": "ms-railing"},
        },
        "colorways": [
            {
                "id": "mono-wood",
                "name": "Monochrome + wood",
                "base": "#F2F0EB",
                "accent": "#7A5230",
                "trim": "#2E2E2E",
            },
            {
                "id": "warm-grey",
                "name": "Warm grey",
                "base": "#DAD5CC",
                "accent": "#8B6A45",
                "trim": "#3C3C3C",
            },
        ],
        "rules": {
            "minFacadeWidthMm": 4500,
            "chajjaOverOpenings": ["window", "door"],
            "claddingBayPickedBy": "stair-adjacent external wall",
        },
    },
    {
        "id": "modern-minimal",
        "name": "Modern Minimal",
        "description": "Recessed windows with a hidden chajja, a plain parapet and a "
        "glass railing — white and grey.",
        "components": {
            "windowTrim": {"style": "recessed", "widthMm": 0, "projectionMm": -75},
            "chajja": {
                "style": "hidden",
                "projectionMm": 600,
                "thicknessMm": 75,
                "allowedProjectionsMm": [600],
            },
            "parapetProfile": {"style": "plain", "heightMm": 1050, "capThicknessMm": 50},
            "claddingZones": {"rule": "none", "materialId": None, "widthMm": 0},
            "porch": {"style": "flush", "projectionMm": 1200, "thicknessMm": 150},
            "railing": {"style": "glass", "heightMm": 1050, "materialId": "glass-railing"},
        },
        "colorways": [
            {
                "id": "white-grey",
                "name": "White + grey",
                "base": "#FFFFFF",
                "accent": "#8E8E8E",
                "trim": "#6B6B6B",
            },
            {
                "id": "off-white",
                "name": "Off white",
                "base": "#F5F3EE",
                "accent": "#A8A8A3",
                "trim": "#3C3C3C",
            },
        ],
        "rules": {
            "minFacadeWidthMm": 4500,
            "chajjaOverOpenings": ["window"],
            "recessDepthMm": 150,
        },
    },
]

_BUILTIN: dict[str, list[dict[str, Any]]] = {
    "furniture": _FURNITURE,
    "materials": _MATERIALS,
    "facade-kits": _FACADE_KITS,
}


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
