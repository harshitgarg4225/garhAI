"""Loading and validating the seed data files (playbook §17).

Catalogue data is **data, not code**: it lives in ``fixtures/catalog/*.json`` so the
seeder, the API's ``/catalog`` routes, the solver's furniture-fit gate (§5.4) and any
future TypeScript consumer all read the same bytes. This module is the only place that
reads those files on the seed path, and it validates them hard before anything touches
the database:

* every dimension is a **non-negative integer millimetre** — a float width would put
  drift into a "does the bed fit" answer, which is exactly what §3 forbids;
* ids are unique and lowercase-kebab, because they end up in op payloads
  (``furniture.set``, ``material.assign``) and in DXF layer names;
* every ``roomTypes`` entry is a real :data:`garh_model.model.ROOM_TYPES` member, so a
  typo cannot silently make an item unreachable from the furniture tool;
* the minimum counts §17 states (≥30 furniture, ≥20 materials, exactly 2 facade kits).

Where the files are looked for, in order — first hit wins:

1. ``$GARH_CATALOG_DIR`` — the same variable ``garh_api.routers.catalog`` honours;
2. ``<root>/catalog`` — the location that module falls back to;
3. ``<root>/fixtures/catalog`` — **where this repository actually keeps the data**.

``<root>`` is ``$GARH_ROOT``, else ``$FIXTURE_DIR``'s parent (compose sets
``FIXTURE_DIR=/app/fixtures``), else the repo root walked up from this file.

The order matters and is deliberately the router's order first: the seeder must validate
*what the API will serve*, not what it wishes the API would serve. If they disagree —
because the files live in ``fixtures/catalog`` and the router is looking in ``catalog`` —
:func:`load_catalog_bundle` says so out loud in :attr:`CatalogBundle.serving_warning`
rather than quietly validating a file nobody will ever send to a client.

Nothing here is fatal when the files are absent. ``routers/catalog.py`` carries a
compiled-in table with the same §17 dimensions, so an image built without ``fixtures/``
still serves a real catalogue; the seeder records ``source: "builtin"`` and moves on.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from garh_api.logging import get_logger

_log = get_logger(__name__)

#: §17's floors. Below these the catalogue cannot furnish a plan, so the seed fails.
MIN_FURNITURE_ITEMS = 30
MIN_MATERIALS = 20
#: The MVP cut line is exactly two kits (playbook §8, SKILL.md "MVP cut lines").
FACADE_KIT_IDS: tuple[str, ...] = ("contemporary", "modern-minimal")

#: Catalogue files, in the order the seed report lists them.
CATALOG_FILES: tuple[str, ...] = ("furniture", "materials", "facade-kits")

SOURCE_FILES = "files"
SOURCE_BUILTIN = "builtin"

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Room types that legitimately hold no furniture. Listed rather than inferred so
#: "nothing fits here" is a decision on the record (see fixtures/catalog/index.json).
ROOM_TYPES_WITHOUT_FURNITURE: frozenset[str] = frozenset({"duct", "shaft", "void", "unassigned"})


class SeedDataError(RuntimeError):
    """Seed input is unusable. Raised before any write, never mid-transaction."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def repo_root() -> str:
    """Directory holding ``rulepacks/`` and ``fixtures/``.

    Mirrors ``garh_api.routers.repo_root`` (``GARH_ROOT`` first) and additionally
    honours ``FIXTURE_DIR``, which is what ``docker-compose.yml`` sets — so the seeder
    finds the bind-mounted ``/app/fixtures`` without any new environment variable.
    """
    override = os.environ.get("GARH_ROOT")
    if override:
        return os.path.abspath(override)
    fixture_dir = os.environ.get("FIXTURE_DIR")
    if fixture_dir and os.path.isdir(fixture_dir):
        return os.path.abspath(os.path.join(fixture_dir, os.pardir))
    here = os.path.abspath(os.path.dirname(__file__))
    # garh_api/seed -> garh_api -> apps/api -> apps -> <repo root>
    return os.path.abspath(os.path.join(here, *([os.pardir] * 4)))


def fixtures_dir() -> str:
    """``fixtures/`` — honours ``FIXTURE_DIR`` (compose sets it)."""
    override = os.environ.get("FIXTURE_DIR")
    if override:
        return os.path.abspath(override)
    return os.path.join(repo_root(), "fixtures")


def rulepack_dir() -> str:
    """``rulepacks/``. ``GARH_RULEPACK_DIR`` is what ``routers/catalog.py`` reads;
    ``RULEPACK_DIR`` is what compose sets. Both are honoured, in that order."""
    return (
        os.environ.get("GARH_RULEPACK_DIR")
        or os.environ.get("RULEPACK_DIR")
        or os.path.join(repo_root(), "rulepacks")
    )


def catalog_search_paths() -> tuple[str, ...]:
    """Candidate catalogue directories, highest priority first."""
    override = os.environ.get("GARH_CATALOG_DIR")
    root = repo_root()
    candidates = [
        override,
        os.path.join(root, "catalog"),
        os.path.join(fixtures_dir(), "catalog"),
    ]
    return tuple(os.path.abspath(path) for path in candidates if path)


def router_catalog_dir() -> str:
    """The directory ``garh_api.routers.catalog`` will actually read at runtime.

    Delegates rather than re-deriving: this function exists to warn when the seed
    validated one directory and the API serves another, and a second copy of the
    precedence rule would make the warning itself the thing that drifts.
    """
    from garh_api.routers.catalog import catalog_dir

    return os.path.abspath(catalog_dir())


def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise SeedDataError("Could not read %s: %s" % (path, exc)) from exc
    except ValueError as exc:
        raise SeedDataError("%s is not valid JSON: %s" % (path, exc)) from exc


def _items_of(payload: Any, path: str) -> list[Any]:
    """Accept both the bare array and the ``{"items": [...]}`` wrapper the API takes."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return list(payload["items"])
    raise SeedDataError("%s must be a JSON array (or an object with an 'items' array)." % path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _require_int_mm(value: Any, *, where: str, minimum: int = 0) -> int:
    """Integer millimetres only. ``True`` is an ``int`` in Python; it is not a length."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SeedDataError(
            "%s must be an integer millimetre value, got %r. Lengths are never floats "
            "(playbook §3) — scale to whole mm." % (where, value)
        )
    if value < minimum:
        raise SeedDataError("%s must be >= %d, got %d." % (where, minimum, value))
    return value


def _require_id(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise SeedDataError(
            "%s must be a lowercase kebab-case id (a-z, 0-9, '-'), got %r." % (where, value)
        )
    return value


def _require_text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeedDataError("%s must be a non-empty string, got %r." % (where, value))
    return value


def validate_furniture(items: Iterable[Any], *, room_types: Iterable[str]) -> list[dict[str, Any]]:
    """Validate the furniture catalogue. Returns the rows, unchanged, on success."""
    known_room_types = set(room_types)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        where = "furniture[%d]" % index
        if not isinstance(raw, dict):
            raise SeedDataError("%s must be an object." % where)
        item_id = _require_id(raw.get("id"), where="%s.id" % where)
        if item_id in seen:
            raise SeedDataError("Duplicate furniture id %r." % item_id)
        seen.add(item_id)
        _require_text(raw.get("name"), where="%s.name" % where)
        _require_id(raw.get("category"), where="%s.category" % where)
        for key in ("widthMm", "depthMm", "heightMm"):
            _require_int_mm(raw.get(key), where="%s.%s" % (where, key), minimum=1)
        _require_int_mm(raw.get("clearanceMm", 0), where="%s.clearanceMm" % where)
        types = raw.get("roomTypes")
        if not isinstance(types, list) or not types:
            raise SeedDataError("%s.roomTypes must be a non-empty array." % where)
        unknown = [t for t in types if t not in known_room_types]
        if unknown:
            raise SeedDataError(
                "%s.roomTypes has unknown room type(s) %s. Allowed values are "
                "garh_model.model.ROOM_TYPES." % (where, ", ".join(map(repr, unknown)))
            )
        rows.append(raw)
    if len(rows) < MIN_FURNITURE_ITEMS:
        raise SeedDataError(
            "The furniture catalogue has %d items; playbook §17 requires at least %d "
            "(the solver's furniture-fit gate places a standard set per room type)."
            % (len(rows), MIN_FURNITURE_ITEMS)
        )
    missing = sorted(
        (known_room_types - ROOM_TYPES_WITHOUT_FURNITURE)
        - {t for row in rows for t in row["roomTypes"]}
    )
    if missing:
        raise SeedDataError(
            "No furniture is declared for room type(s) %s. Either add an item or list "
            "the type in ROOM_TYPES_WITHOUT_FURNITURE with a reason." % ", ".join(missing)
        )
    return rows


def validate_materials(items: Iterable[Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        where = "materials[%d]" % index
        if not isinstance(raw, dict):
            raise SeedDataError("%s must be an object." % where)
        material_id = _require_id(raw.get("id"), where="%s.id" % where)
        if material_id in seen:
            raise SeedDataError("Duplicate material id %r." % material_id)
        seen.add(material_id)
        _require_text(raw.get("name"), where="%s.name" % where)
        _require_id(raw.get("category"), where="%s.category" % where)
        groups = raw.get("surfaceGroups")
        if not isinstance(groups, list) or not groups:
            raise SeedDataError(
                "%s.surfaceGroups must be a non-empty array — a material nothing can be "
                "assigned to is dead data." % where
            )
        price = raw.get("priceInrPerSqm")
        if price is not None and (
            isinstance(price, bool) or not isinstance(price, int) or price <= 0
        ):
            raise SeedDataError(
                "%s.priceInrPerSqm must be whole rupees > 0 or absent, got %r." % (where, price)
            )
        colour = raw.get("colorHex")
        if colour is not None and not re.match(r"^#[0-9A-Fa-f]{6}$", str(colour)):
            raise SeedDataError("%s.colorHex must look like '#RRGGBB', got %r." % (where, colour))
        rows.append(raw)
    if len(rows) < MIN_MATERIALS:
        raise SeedDataError(
            "The material catalogue has %d entries; playbook §17 requires at least %d."
            % (len(rows), MIN_MATERIALS)
        )
    return rows


def validate_facade_kits(items: Iterable[Any]) -> list[dict[str, Any]]:
    rows = list(items)
    ids = []
    for index, raw in enumerate(rows):
        where = "facade-kits[%d]" % index
        if not isinstance(raw, dict):
            raise SeedDataError("%s must be an object." % where)
        ids.append(_require_id(raw.get("id"), where="%s.id" % where))
        _require_text(raw.get("name"), where="%s.name" % where)
        for key in ("components", "rules"):
            if not isinstance(raw.get(key), dict) or not raw[key]:
                raise SeedDataError("%s.%s must be a non-empty object." % (where, key))
        if not isinstance(raw.get("colorways"), list) or not raw["colorways"]:
            raise SeedDataError("%s.colorways must be a non-empty array." % where)
    if tuple(ids) != FACADE_KIT_IDS:
        raise SeedDataError(
            "Facade kits must be exactly %s in that order (MVP cut line, playbook §8); "
            "found %s." % (", ".join(FACADE_KIT_IDS), ", ".join(ids) or "none")
        )
    return rows


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogBundle:
    """Validated catalogue data plus where it came from."""

    source: str
    directory: str | None
    furniture: list[dict[str, Any]] = field(default_factory=list)
    materials: list[dict[str, Any]] = field(default_factory=list)
    facade_kits: list[dict[str, Any]] = field(default_factory=list)
    #: Set when the router will serve a *different* table than the one validated here.
    serving_warning: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        return {
            "furniture": len(self.furniture),
            "materials": len(self.materials),
            "facadeKits": len(self.facade_kits),
        }

    def digest(self) -> str:
        """Content hash of the whole catalogue.

        Recorded in ``firms.settings`` so "the demo firm was seeded against a different
        catalogue than the one on disk" is a diff rather than a mystery.
        """
        payload = json.dumps(
            {
                "furniture": self.furniture,
                "materials": self.materials,
                "facadeKits": self.facade_kits,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "directory": self.directory,
            "counts": self.counts,
            "digest": self.digest(),
            "facadeKitIds": [kit["id"] for kit in self.facade_kits],
        }


def _model_room_types() -> tuple[str, ...]:
    """``ROOM_TYPES`` from the model core — the closed list room types must come from."""
    from garh_model.model import ROOM_TYPES

    return tuple(ROOM_TYPES)


def load_catalog_bundle(*, room_types: Iterable[str] | None = None) -> CatalogBundle:
    """Read and validate the catalogue. Never raises for "no files"; always for bad files."""
    known_room_types = tuple(room_types) if room_types is not None else _model_room_types()

    directory: str | None = None
    for candidate in catalog_search_paths():
        if all(os.path.isfile(os.path.join(candidate, "%s.json" % name)) for name in CATALOG_FILES):
            directory = candidate
            break

    if directory is None:
        _log.warning(
            "seed.catalog_files_absent",
            searched=list(catalog_search_paths()),
            consequence="the API will serve its compiled-in table (routers/catalog.py)",
        )
        return CatalogBundle(source=SOURCE_BUILTIN, directory=None)

    furniture = validate_furniture(
        _items_of(_read_json(os.path.join(directory, "furniture.json")), "furniture.json"),
        room_types=known_room_types,
    )
    materials = validate_materials(
        _items_of(_read_json(os.path.join(directory, "materials.json")), "materials.json")
    )
    kits = validate_facade_kits(
        _items_of(_read_json(os.path.join(directory, "facade-kits.json")), "facade-kits.json")
    )

    warning: str | None = None
    serving = router_catalog_dir()
    if os.path.abspath(directory) != serving:
        warning = (
            "Validated %s, but the API reads %s — so GET /catalog/* will serve the "
            "compiled-in table instead. Set GARH_CATALOG_DIR=%s (compose: add it to the "
            "api service environment) to serve these files." % (directory, serving, directory)
        )
        _log.warning("seed.catalog_not_served", validated=directory, api_reads=serving)

    return CatalogBundle(
        source=SOURCE_FILES,
        directory=directory,
        furniture=furniture,
        materials=materials,
        facade_kits=kits,
        serving_warning=warning,
    )


# ---------------------------------------------------------------------------
# Rule packs
# ---------------------------------------------------------------------------

#: §17: "3 rule packs + nbc-core + vastu". Every one of these must be present.
REQUIRED_RULEPACKS: tuple[str, ...] = ("nbc-core", "blr", "ncr", "hyd", "vastu")


@dataclass(frozen=True)
class RulepackRegistry:
    """The rule packs available to the compliance engine, as registered by the seed."""

    directory: str
    packs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [pack["id"] for pack in self.packs]

    def versions(self) -> dict[str, str]:
        return {pack["id"]: pack["version"] for pack in self.packs}

    def summary(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "packs": self.packs,
            "versions": self.versions(),
        }


def load_rulepack_registry() -> RulepackRegistry:
    """Read ``rulepacks/index.json`` and assert §17's five packs are present.

    "Registering" a rule pack means exactly this: proving the file the engine will load
    exists, is parseable, declares a version, and carries its review status — then
    recording the resolved ``{id: version}`` map on the firm so a compliance report can
    later be traced to the pack revision that produced it. There is no ``rulepacks``
    table in playbook §2 and inventing one would give the packs two homes.
    """
    directory = rulepack_dir()
    manifest_path = os.path.join(directory, "index.json")
    if not os.path.isfile(manifest_path):
        raise SeedDataError(
            "No rule-pack manifest at %s. The compliance engine cannot run without the "
            "packs; check GARH_RULEPACK_DIR / RULEPACK_DIR." % manifest_path
        )
    manifest = _read_json(manifest_path)
    raw_packs = manifest.get("packs") if isinstance(manifest, dict) else manifest
    if not isinstance(raw_packs, list) or not raw_packs:
        raise SeedDataError("%s has no 'packs' array." % manifest_path)

    packs: list[dict[str, Any]] = []
    for entry in raw_packs:
        if not isinstance(entry, dict):
            raise SeedDataError("%s: every pack entry must be an object." % manifest_path)
        pack_id = str(entry.get("pack") or entry.get("id") or "").strip()
        if not pack_id:
            raise SeedDataError("%s: a pack entry has no id." % manifest_path)
        pack_file = os.path.join(directory, "%s.json" % pack_id)
        if not os.path.isfile(pack_file):
            raise SeedDataError(
                "Rule pack %r is listed in index.json but %s does not exist." % (pack_id, pack_file)
            )
        body = _read_json(pack_file)
        if not isinstance(body, dict) or not isinstance(body.get("rules"), list):
            raise SeedDataError("Rule pack %s has no 'rules' array." % pack_file)
        review = entry.get("review")
        review_status = (
            review.get("status") if isinstance(review, dict) else review
        ) or "unreviewed"
        packs.append(
            {
                "id": pack_id,
                "version": str(entry.get("version") or body.get("version") or "0"),
                "title": str(entry.get("title") or entry.get("name") or pack_id),
                "kind": entry.get("kind"),
                "selectable": bool(entry.get("selectable", True)),
                "ruleCount": len(body["rules"]),
                "confidence": entry.get("confidence") or body.get("confidence"),
                "reviewStatus": str(review_status),
            }
        )

    missing = [pack_id for pack_id in REQUIRED_RULEPACKS if pack_id not in {p["id"] for p in packs}]
    if missing:
        raise SeedDataError(
            "Rule pack(s) %s are missing. Playbook §17 seeds nbc-core, the three city "
            "packs and vastu." % ", ".join(missing)
        )
    return RulepackRegistry(directory=directory, packs=packs)


__all__ = [
    "CATALOG_FILES",
    "FACADE_KIT_IDS",
    "MIN_FURNITURE_ITEMS",
    "MIN_MATERIALS",
    "REQUIRED_RULEPACKS",
    "ROOM_TYPES_WITHOUT_FURNITURE",
    "SOURCE_BUILTIN",
    "SOURCE_FILES",
    "CatalogBundle",
    "RulepackRegistry",
    "SeedDataError",
    "catalog_search_paths",
    "fixtures_dir",
    "load_catalog_bundle",
    "load_rulepack_registry",
    "repo_root",
    "router_catalog_dir",
    "rulepack_dir",
    "validate_facade_kits",
    "validate_furniture",
    "validate_materials",
]
