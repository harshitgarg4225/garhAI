"""model → the six municipal sheets, as one deterministic function (§7, F7-A).

This is the module the drawings worker's job handlers call. It exists so that the
whole of sheet generation is testable without a queue, without a database and
without ``ezdxf``:

    bundle  = SheetBundle.from_envelope(payload, model_json, areas_json)
    result  = build_sheets(bundle)          # pure; SVG strings, chains, stats
    result.sheets[0].svg                    # print-true, sanitised, byte-stable

``handler.py`` adds only the things a pure function cannot have: progress events,
blob IO and wall-clock budgets.

What is deliberately *not* here
-------------------------------
* **No area arithmetic.** ``SheetBundle.areas`` is the ``AreaStatement`` the API
  already produced from the same ``garh_rules`` evaluation that produced the
  compliance results (§7: "from rules results — same numbers, one source").
  :class:`TransportStatement` reconstitutes it from JSON as *rows*, and every row
  is carried through verbatim. There is no code path in this package that can
  compute a FAR, a coverage or a setback, which is the only way to guarantee the
  sheet and the compliance tab agree.
* **No ezdxf.** DXF lives behind :mod:`services.drawings.export.dxf`, imported
  lazily by :func:`sheet_dxf_bytes`. Absence of ezdxf costs the DXF format and
  nothing else — SVG, the dimension chains, the schedules and the area statement
  all still generate.
* **No clock.** Nothing here reads ``datetime.now()``. The title block's date is
  an input. That is what makes the §16 goldens stable and what stops a
  regenerate-to-go-green habit from destroying the gate.

Sheet identity
--------------
Every sheet has a **slug** computed from what it draws, never from a name a user
can edit:

    site-plan · floor-plan-<storeyId> · elevation-<n|e|s|w> · section-a
    door-window-schedule · area-statement

``Annotation.sheetId`` in the model document is one of these slugs, so
regenerating the set does not orphan every annotation (§7's regeneration
contract). ``services.drawings.render.reference_sheets`` derives its own
``sheet.id`` from the storey *name* (``sheet-plan-ground-floor``); renaming a
storey would change it, so this module overrides it. Flagged for the integrator.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "DB_KIND_TO_DRAWING_KIND",
    "DRAWING_KIND_TO_DB_KIND",
    "SHEET_FORMATS",
    "GeneratedSheet",
    "PipelineError",
    "SheetBundle",
    "SheetSetResult",
    "TransportStatement",
    "build_sheets",
    "SUBMISSION_DB_KINDS",
    "canonical_sheet_kinds",
    "sheet_dxf_bytes",
    "sheet_glb_bytes",
    "sheet_png_manifest",
    "svg_set_to_pdf_bytes",
]


class PipelineError(ValueError):
    """A bundle the pipeline cannot draw. Always names the missing thing."""

    def __init__(self, message: str, *, action: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.action = action
        self.detail = detail


# ---------------------------------------------------------------------------
# Kind vocabularies
#
# Two exist and both are load-bearing, so the translation lives in one place:
#
#   garh_api.models.SHEET_KINDS         site | floor | elevation | section |
#                                       schedule | area-statement   (the DB CHECK)
#   services.drawings.sheets.SHEET_KINDS site-plan | floor-plan | elevation |
#                                       section | door-window-schedule |
#                                       area-statement               (the drawing)
#
# Before this table existed, `POST /sheets/generate {kinds:["floor"]}` passed the
# API's validator and then failed the worker's, which validated the drawing
# vocabulary. Both spellings are accepted on input; the DB spelling is canonical.
# ---------------------------------------------------------------------------
DB_KIND_TO_DRAWING_KIND: dict[str, str] = {
    "site": "site-plan",
    "floor": "floor-plan",
    # D-2. Sits right after the floor plans in submission order because it is read
    # against them: same storey, same scale, centrelines instead of poché.
    "setting-out": "setting-out",
    "structural-grid": "structural-grid",
    "elevation": "elevation",
    "section": "section",
    "schedule": "door-window-schedule",
    "area-statement": "area-statement",
}
DRAWING_KIND_TO_DB_KIND: dict[str, str] = {v: k for k, v in DB_KIND_TO_DRAWING_KIND.items()}

from services.drawings.sheets.model import (  # noqa: E402
    WORKING_SHEET_KINDS as _WORKING_SHEET_KINDS,
)

#: Submission order. Also the numbering order (§7 title block, "sheet numbering").
DB_KIND_ORDER: tuple[str, ...] = (
    "site",
    "floor",
    "elevation",
    "section",
    "schedule",
    "area-statement",
    # D-2 sits LAST in this tuple and is numbered in its own series (see
    # WORKING_KINDS): a setting-out plan is a WORKING drawing, not a submission one.
    # Inserting it into the A-series would have renumbered every sheet after it —
    # and would also have been wrong, because the municipal set and the GFC set are
    # two different deliverables that an architect issues separately.
    "setting-out",
    "structural-grid",
)

#: Working-drawing kinds, numbered W-01… rather than A-01…. A submission set and a
#: GFC set go to different people at different times; sharing a number series would
#: mean a revision to one silently renumbers the other.
WORKING_KINDS: frozenset[str] = frozenset(
    DRAWING_KIND_TO_DB_KIND[kind] for kind in _WORKING_SHEET_KINDS
)
_WORKING_NUMBER_INDEX: dict[str, int] = {"setting-out": 1, "structural-grid": 2}

#: The submission set, in the DB vocabulary: everything that is NOT a working drawing.
#: Named here rather than re-derived at each call site so a new kind cannot be added to
#: one list and forgotten in the other — which is how a requirement goes quietly inert.
SUBMISSION_DB_KINDS: tuple[str, ...] = tuple(k for k in DB_KIND_ORDER if k not in WORKING_KINDS)

#: Sheet numbers by kind: ``A-01`` … ``A-06``, with a letter suffix when a kind
#: yields more than one sheet (two storeys → ``A-02A``/``A-02B``).
_KIND_NUMBER_INDEX: dict[str, int] = {
    kind: i + 1 for i, kind in enumerate(k for k in DB_KIND_ORDER if k not in WORKING_KINDS)
}

#: Formats a sheet can be published as. ``svg`` always; ``dxf`` needs ezdxf; ``pdf``
#: needs a converter binary. Mirrors ``garh_api.routers.jobs.SHEET_FORMATS``.
SHEET_FORMATS: tuple[str, ...] = ("svg", "dxf", "pdf")

ELEVATION_DIRECTIONS: tuple[str, ...] = ("N", "E", "S", "W")
_DIRECTION_WORD = {"N": "North", "E": "East", "S": "South", "W": "West"}


def canonical_sheet_kinds(raw: Iterable[str] | None) -> tuple[str, ...]:
    """Normalise a requested kind list to the DB vocabulary, in submission order.

    ``None`` means the full F7-A SUBMISSION set — which deliberately excludes the
    working drawings in :data:`WORKING_KINDS`. A GFC drawing is a separate deliverable
    issued at a different time to a different person, so it is opt-in: an existing
    caller asking for "the sheets" must keep getting exactly the sheets it got before,
    not a setting-out plan it never requested and may not want the client to see.
    Unknown kinds raise, naming both spellings — a typo in a payload should not
    silently generate five sheets instead of six.
    """
    if raw is None:
        return tuple(k for k in DB_KIND_ORDER if k not in WORKING_KINDS)
    wanted: list[str] = []
    for item in raw:
        key = str(item).strip()
        if key in DB_KIND_TO_DRAWING_KIND:
            canonical = key
        elif key in DRAWING_KIND_TO_DB_KIND:
            canonical = DRAWING_KIND_TO_DB_KIND[key]
        else:
            raise PipelineError(
                "We don't recognise one of the requested sheets.",
                action="Ask for the full set, or one of: %s." % ", ".join(DB_KIND_ORDER),
                detail="kind=%r; accepted: %s | %s"
                % (
                    item,
                    ", ".join(sorted(DB_KIND_TO_DRAWING_KIND)),
                    ", ".join(sorted(DRAWING_KIND_TO_DB_KIND)),
                ),
            )
        if canonical not in wanted:
            wanted.append(canonical)
    if not wanted:
        raise PipelineError(
            "We couldn't tell which sheets to draw.",
            action="Generate the whole set, or pick at least one sheet.",
            detail="kinds resolved to an empty list",
        )
    return tuple(kind for kind in DB_KIND_ORDER if kind in wanted)


# ---------------------------------------------------------------------------
# The area statement, in transport form
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _TransportRow:
    """One printable area-statement row, carried verbatim from the evaluation.

    Field-for-field the read surface of ``garh_rules.areas.AreaRow`` that
    ``services.drawings.render.tables.area_statement_table`` touches — and nothing
    else, so there is nowhere for a number to be recomputed.
    """

    key: str
    label: str
    value: Any
    unit: str
    allowed: Any = None
    kind: str = "informational"
    note: str | None = None
    rule_ids: tuple[str, ...] = ()

    @property
    def limit_label(self) -> str:
        if self.kind == "allowance":
            return "Permissible"
        if self.kind == "requirement":
            return "Required"
        return ""


@dataclass(frozen=True)
class TransportStatement:
    """``AreaStatement.to_json()`` → the object the sheet renderer consumes.

    A codec, not a model. The renderer asks a statement for ``rows()`` and
    ``warnings``; this hands back exactly what the rules engine put in the JSON.
    Nothing is derived, so a doctored input reaches the sheet unchanged — which is
    the property the API's cross-check test relies on.
    """

    _rows: tuple[_TransportRow, ...]
    warnings: tuple[str, ...] = ()
    #: Kept for the sheet's provenance block and the site plan's coverage/FAR table.
    raw: Mapping[str, Any] = field(default_factory=dict)

    def rows(self) -> tuple[_TransportRow, ...]:
        return self._rows

    # -- scalars the site plan's coverage/FAR note reads ------------------
    # Straight pass-throughs. No default: a statement missing plotAreaMm2 is a broken
    # evaluation, and 0 would print "PLOT AREA: 0.00 m2" on a submission drawing.
    @property
    def plot_area_mm2(self) -> int:
        return int(self.raw["plotAreaMm2"])

    @property
    def footprint_area_mm2(self) -> int:
        return int(self.raw["footprintAreaMm2"])

    @property
    def coverage_allowed_mm2(self) -> int | None:
        value = self.raw.get("coverageAllowedMm2")
        return None if value is None else int(value)

    @property
    def far_countable_area_mm2(self) -> int:
        return int(self.raw["farCountableAreaMm2"])

    @property
    def far_allowed_mm2(self) -> int | None:
        value = self.raw.get("farAllowedMm2")
        return None if value is None else int(value)

    @property
    def total_built_up_area_mm2(self) -> int:
        return int(self.raw["totalBuiltUpAreaMm2"])

    # -- the two exact ratios -------------------------------------------
    # These are Fractions because `format_ratio` rounds on the exact rational, and
    # because the site plan's note is rendered by the same formatter the compliance
    # tab uses. The expressions are copied verbatim from
    # ``garh_rules.areas.AreaStatement.far_achieved`` / ``coverage_achieved`` and are
    # taken over the engine's OWN integers — this is a ratio of two given numbers, not
    # a re-derivation of a regulatory quantity. ``test_ratios_match_the_engines_own_
    # formatted_strings`` compares the printed result against the engine's serialised
    # ``farAchieved``/``coverageAchieved``, so the two cannot drift apart unnoticed.
    @property
    def far_achieved(self) -> Any:
        from fractions import Fraction

        return Fraction(self.far_countable_area_mm2, max(1, self.plot_area_mm2))

    @property
    def far_allowed(self) -> Any:
        from fractions import Fraction

        if self.far_allowed_mm2 is None:
            return None
        return Fraction(self.far_allowed_mm2, max(1, self.plot_area_mm2))

    @property
    def coverage_achieved(self) -> Any:
        from fractions import Fraction

        return Fraction(self.footprint_area_mm2, max(1, self.plot_area_mm2))

    @property
    def coverage_allowed(self) -> Any:
        from fractions import Fraction

        if self.coverage_allowed_mm2 is None:
            return None
        return Fraction(self.coverage_allowed_mm2, max(1, self.plot_area_mm2))

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> TransportStatement:
        rows_raw = payload.get("rows")
        if not isinstance(rows_raw, list) or not rows_raw:
            raise PipelineError(
                "The area statement for this design is empty, so the area-statement "
                "sheet cannot be drawn.",
                action="Run the compliance check for this version, then generate again.",
                detail="areas.rows missing or empty; keys=%s" % sorted(payload),
            )
        rows = tuple(
            _TransportRow(
                key=str(row.get("key") or ""),
                label=str(row.get("label") or ""),
                value=row.get("value"),
                unit=str(row.get("unit") or ""),
                allowed=row.get("allowed"),
                kind=str(row.get("kind") or "informational"),
                note=(str(row["note"]) if row.get("note") else None),
                rule_ids=tuple(str(r) for r in (row.get("ruleIds") or ())),
            )
            for row in rows_raw
            if isinstance(row, Mapping)
        )
        return cls(
            _rows=rows,
            warnings=tuple(str(w) for w in (payload.get("warnings") or ())),
            raw=dict(payload),
        )


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SheetBundle:
    """Everything one generation needs. Constructed at the worker's edge, then frozen.

    ``document`` is the folded ``ProjectDoc`` JSON (``GET /projects/:id/model``'s
    ``doc``), fetched through a presigned asset — never inlined in the envelope, per
    ``JobEnvelope``'s own contract.
    """

    document: Mapping[str, Any]
    areas: TransportStatement | None = None
    kinds: tuple[str, ...] = DB_KIND_ORDER
    scale_denominator: int = 100
    paper: str = "A2"
    dim_to_jamb: bool = False
    title_block_fields: Mapping[str, Any] = field(default_factory=dict)
    revisions: tuple[tuple[str, str, str], ...] = ()
    #: The validated register (D-1), when the payload's revision rows carry a real
    #: DD-MM-YYYY date and an author. ``None`` when they do not: the compact title-block
    #: strip still prints from ``revisions``, because a typo in a note must not fail a
    #: sheet job — it just cannot drive a register or a cloud.
    register: Any | None = None
    #: The folded ``ProjectDoc`` JSON of the state the *previous* revision was issued at.
    #: When present, the floor plans carry revision clouds around what changed since.
    previous_document: Mapping[str, Any] | None = None
    number_prefix: str = "A"
    #: Provenance only — printed nowhere, logged and returned with the result.
    design_version_id: str | None = None
    #: Sanctioning authority id (D-4: ``bbmp``/``bda``/``ncr``/``ghmc``), when the set is
    #: being prepared for a submission. Its template supplies the statutory identifiers
    #: the title block must carry — a khata number in Bengaluru, a block and colony in
    #: Delhi. ``None`` for an ordinary working set, which is most of them.
    authority: str | None = None

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        document: Mapping[str, Any],
        areas: Mapping[str, Any] | None = None,
    ) -> SheetBundle:
        """Build from a ``drawings.generate_sheets`` payload. Every failure names a field."""
        scale = payload.get("scaleDenominator")
        denominator = int(scale) if isinstance(scale, int) and scale > 0 else 100
        revisions: list[tuple[str, str, str]] = []
        for row in payload.get("revisions") or ():
            if isinstance(row, Mapping):
                revisions.append(
                    (
                        str(row.get("revision") or ""),
                        str(row.get("date") or ""),
                        str(row.get("note") or ""),
                    )
                )
            elif isinstance(row, list | tuple) and len(row) >= 3:
                revisions.append((str(row[0]), str(row[1]), str(row[2])))
        return cls(
            register=_register_from(payload.get("revisions") or ()),
            document=document,
            areas=TransportStatement.from_json(areas) if areas else None,
            kinds=canonical_sheet_kinds(payload.get("kinds")),
            scale_denominator=denominator,
            paper=str(payload.get("sheetSize") or "A2"),
            dim_to_jamb=bool(payload.get("dimToJamb")),
            title_block_fields=dict(payload.get("titleBlock") or {}),
            revisions=tuple(revisions),
            number_prefix=str(payload.get("numberPrefix") or "A"),
            design_version_id=(
                str(payload["designVersionId"]) if payload.get("designVersionId") else None
            ),
            authority=(str(payload["authority"]) if payload.get("authority") else None),
        )


def _register_from(rows: Any) -> Any | None:
    """Build a :class:`~services.drawings.revisions.RevisionHistory`, or ``None``.

    Deliberately forgiving in one direction only. The revision rows the API sends today
    are three free-text columns; a register needs a real date and an author, and refuses a
    reused number or a backwards date. When the rows do not meet that bar this returns
    ``None`` and the set falls back to the compact title-block strip — a job must not fail
    because someone typed ``5-3-26``. When they do, the set gets the register table and
    (given a previous document) the clouds.

    What it never does is *invent* the missing parts: an undated revision does not get
    today's date, because a sheet that prints a date nobody chose is a lie on a signed
    drawing.
    """
    from services.drawings.revisions import RevisionHistory

    if not rows:
        return None
    try:
        history = RevisionHistory(rows)
    except (ValueError, TypeError, KeyError):
        return None
    return history or None


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
@dataclass
class GeneratedSheet:
    """One drawn sheet: its identity, its SVG, its chains and its provenance.

    ``drawing`` is the live ``SheetDrawing`` — kept so the export path can re-use the
    same object for DXF/PDF instead of re-deriving geometry, and dropped before the
    result crosses a wire (:meth:`to_json` omits it).
    """

    slug: str
    kind: str  # DB vocabulary
    drawing_kind: str  # drawings vocabulary
    number: str
    title: str
    scale_denominator: int
    paper: str
    viewport: dict[str, Any]
    svg: str
    chains: list[dict[str, Any]]
    element_ids: list[str]
    primitive_count: int
    layers_used: list[str]
    drawing: Any = None

    def to_json(self) -> dict[str, Any]:
        """The metadata the API persists as ``sheets.layout``. No SVG, no drawing.

        The SVG travels as a stored artifact, not through the job event stream: nine
        sheets of inline SVG is a few hundred kilobytes on a Redis stream every time a
        set regenerates, and the stream is not a file store.
        """
        return {
            "sheetId": self.slug,
            "kind": self.kind,
            "drawingKind": self.drawing_kind,
            "number": self.number,
            "title": self.title,
            "scaleDenominator": self.scale_denominator,
            "paper": self.paper,
            "viewport": dict(self.viewport),
            "chains": list(self.chains),
            "elementIds": list(self.element_ids),
            "stats": {
                "primitives": self.primitive_count,
                "chains": len(self.chains),
                "layers": list(self.layers_used),
                "svgBytes": len(self.svg.encode("utf-8")),
            },
        }


@dataclass
class SheetSetResult:
    sheets: list[GeneratedSheet] = field(default_factory=list)
    #: Sheets that could not be drawn, with the reason. Never a silent omission.
    skipped: list[dict[str, str]] = field(default_factory=list)
    #: Non-fatal notes for the UI (e.g. "no facade geometry on the west elevation").
    notes: list[str] = field(default_factory=list)
    state_hash: str | None = None
    chain_count: int = 0
    label_collisions: int = 0

    def by_slug(self, slug: str) -> GeneratedSheet | None:
        for sheet in self.sheets:
            if sheet.slug == slug:
                return sheet
        return None

    def drawings(self) -> list[Any]:
        return [sheet.drawing for sheet in self.sheets if sheet.drawing is not None]

    def to_json(self) -> dict[str, Any]:
        return {
            "sheets": [sheet.to_json() for sheet in self.sheets],
            "skipped": list(self.skipped),
            "notes": list(self.notes),
            "stateHash": self.state_hash,
            "chainCount": self.chain_count,
            "labelCollisions": self.label_collisions,
        }


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------
def build_sheets(bundle: SheetBundle, *, on_sheet: Any = None) -> SheetSetResult:
    """Draw the requested sheets. Deterministic: same bundle → identical bytes.

    ``on_sheet(index, total, sheet_or_none, slug)`` is called after each attempt so the
    caller can emit an honest progress event per sheet. It is the only side channel;
    everything else is a return value.

    A sheet that cannot be drawn (an empty storey, a facade with no walls, an area
    statement with no evaluation) is recorded in ``result.skipped`` with the reason and
    does not fail the set. A set of five real sheets plus one explained gap is more
    useful to an architect than a failed job — and §15's honesty rule means the gap is
    shown, never hidden.
    """
    from services.drawings.dimensions import assert_chains_sum

    doc = _parse_document(bundle.document)
    house = doc.house
    title_block = _title_block(bundle)

    diff, diff_note = _revision_diff(bundle)
    plan = _sheet_plan(doc, bundle, diff=diff)
    result = SheetSetResult(state_hash=_state_hash(doc))
    if diff_note:
        result.notes.append(diff_note)
    total = len(plan)

    for index, entry in enumerate(plan):
        slug, kind, number, title, builder = entry
        try:
            drawing = builder(title_block)
        except Exception as exc:
            result.skipped.append(
                {"sheetId": slug, "kind": kind, "number": number, "reason": _reason(exc)}
            )
            if callable(on_sheet):
                on_sheet(index, total, None, slug)
            continue

        # The §7 step-5 invariant, on every generated sheet, every time. Not a test-only
        # assertion: a chain whose segments do not sum to its overall is a drawing a
        # contractor would build wrong, and it must never leave this function.
        assert_chains_sum(drawing.chains)

        sheet_obj = _restamp(drawing.sheet, slug=slug, number=number, title=title)
        drawing = replace(drawing, sheet=sheet_obj)
        svg = _render_svg(drawing)
        generated = GeneratedSheet(
            slug=slug,
            kind=kind,
            drawing_kind=DB_KIND_TO_DRAWING_KIND[kind],
            number=number,
            title=title,
            scale_denominator=int(
                getattr(getattr(drawing.sheet, "scale", None), "denominator", 100)
            ),
            paper=str(
                getattr(
                    getattr(getattr(drawing.sheet, "frame", None), "paper", None),
                    "name",
                    bundle.paper,
                )
            ),
            viewport=_viewport_json(drawing.sheet),
            svg=svg,
            chains=[_chain_json(chain) for chain in drawing.chains],
            element_ids=_element_ids(drawing),
            primitive_count=drawing.primitive_count(),
            layers_used=list(drawing.layers_used()),
            drawing=drawing,
        )
        result.sheets.append(generated)
        result.chain_count += len(drawing.chains)
        result.label_collisions += _label_collisions(drawing)
        if callable(on_sheet):
            on_sheet(index, total, generated, slug)

    if "area-statement" in bundle.kinds and bundle.areas is None:
        result.notes.append(
            "The area statement needs a compliance evaluation for this version — "
            "run the compliance check and generate again."
        )
    if not result.sheets:
        raise PipelineError(
            "We couldn't draw any sheets from this design.",
            action="Draw at least one storey of walls, then generate again.",
            detail="every sheet was skipped: %s"
            % "; ".join("%s: %s" % (s["sheetId"], s["reason"]) for s in result.skipped),
        )
    _ = house  # kept for readability of the plan builder below
    return result


# ---------------------------------------------------------------------------
# Plan assembly (one closure per sheet, so a failure is scoped to one sheet)
# ---------------------------------------------------------------------------
def _revision_diff(bundle: SheetBundle) -> tuple[Any | None, str]:
    """``(diff, note)`` — the geometric diff against the previous issue, or no diff.

    Both halves are required and neither is guessed: without a register there is no
    revision number to tag a cloud with, and without the previous document there is
    nothing to compare against. A diff that found no change also returns ``None`` — an
    issue that moved nothing gets a register row and no clouds, which is the truth.

    **Failure degrades to no clouds, never to no sheets.** This runs once, before the
    per-sheet ``try``/``except`` in :func:`build_sheets`, so an unreadable previous
    document — a truncated asset, a document from an older schema, geometry that is not
    integer millimetres — used to fail the entire job. That contradicts the handler's own
    promise that a set without a readable previous issue "draws exactly as before", and it
    fails nine good sheets over one optional annotation. The reason is returned as a note
    rather than swallowed: §15's honesty rule means the missing clouds are stated.
    """
    if bundle.register is None or bundle.previous_document is None:
        return (None, "")
    from services.drawings.revisions import diff_models

    try:
        diff = diff_models(bundle.previous_document, bundle.document)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        return (
            None,
            "The previous issue's model could not be read, so this set carries no "
            "revision clouds — every other sheet is unaffected (%s)." % _reason(exc),
        )
    return (diff if diff else None, "")


def _sheet_plan(
    doc: Any, bundle: SheetBundle, *, diff: Any | None = None
) -> list[tuple[str, str, str, str, Any]]:
    from services.drawings.render import reference_sheets as ref

    house = doc.house
    entries: list[tuple[str, str, str, str, Any]] = []

    def number_for(kind: str, ordinal: int, count: int) -> str:
        if kind in WORKING_KINDS:
            # W-01…, a series of its own. See WORKING_KINDS.
            base = "W-%02d" % _WORKING_NUMBER_INDEX[kind]
        else:
            base = "%s-%02d" % (bundle.number_prefix, _KIND_NUMBER_INDEX[kind])
        return base if count <= 1 else base + chr(ord("A") + ordinal)

    if "site" in bundle.kinds:
        entries.append(
            (
                "site-plan",
                "site",
                number_for("site", 0, 1),
                "Site Plan",
                lambda tb: ref.site_plan_sheet(
                    doc,
                    number=number_for("site", 0, 1),
                    title_block=tb,
                    statement=bundle.areas,
                    revisions=bundle.revisions,
                    register=bundle.register,
                ),
            )
        )

    if "floor" in bundle.kinds:
        storeys = [s for s in house.storeys if _has_walls(house, s.id)]
        for ordinal, storey in enumerate(storeys):
            number = number_for("floor", ordinal, len(storeys))
            entries.append(
                (
                    "floor-plan-%s" % storey.id,
                    "floor",
                    number,
                    "%s Plan" % storey.name,
                    (
                        lambda tb, sid=storey.id, num=number: ref.floor_plan_sheet(
                            doc,
                            sid,
                            number=num,
                            title_block=tb,
                            dim_to_jamb=bundle.dim_to_jamb,
                            revisions=bundle.revisions,
                            register=bundle.register,
                            diff=diff,
                        )
                    ),
                )
            )

    if "setting-out" in bundle.kinds:
        # One per storey with walls, same as the floor plans — a setting-out plan is
        # read against its own storey's plan and there is nothing to set out on a
        # storey that has no walls.
        storeys = [s for s in house.storeys if _has_walls(house, s.id)]
        for ordinal, storey in enumerate(storeys):
            number = number_for("setting-out", ordinal, len(storeys))
            entries.append(
                (
                    "setting-out-%s" % storey.id,
                    "setting-out",
                    number,
                    "Setting Out - %s" % storey.name,
                    (
                        lambda tb, sid=storey.id, num=number: ref.setting_out_sheet(
                            doc,
                            sid,
                            number=num,
                            title_block=tb,
                            revisions=bundle.revisions,
                            register=bundle.register,
                        )
                    ),
                )
            )

    if "structural-grid" in bundle.kinds:
        # Only storeys that actually have columns. `_sheet_plan` builds closures, and a
        # closure that raises is reported as a skipped sheet with a reason — but a
        # load-bearing house has no columns on ANY storey, and reporting six skipped
        # sheets for a design decision is noise, not information.
        framed = [s for s in house.storeys if any(c.storey_id == s.id for c in house.columns)]
        for ordinal, storey in enumerate(framed):
            number = number_for("structural-grid", ordinal, len(framed))
            entries.append(
                (
                    "structural-grid-%s" % storey.id,
                    "structural-grid",
                    number,
                    "Structural Grid - %s" % storey.name,
                    (
                        lambda tb, sid=storey.id, num=number: ref.structural_grid_sheet(
                            doc,
                            sid,
                            number=num,
                            title_block=tb,
                            revisions=bundle.revisions,
                            register=bundle.register,
                        )
                    ),
                )
            )

    if "elevation" in bundle.kinds:
        for ordinal, direction in enumerate(ELEVATION_DIRECTIONS):
            number = number_for("elevation", ordinal, len(ELEVATION_DIRECTIONS))
            entries.append(
                (
                    "elevation-%s" % direction.lower(),
                    "elevation",
                    number,
                    "%s Elevation" % _DIRECTION_WORD[direction],
                    (
                        lambda tb, d=direction, num=number: ref.elevation_sheet(
                            doc,
                            d,
                            number=num,
                            title_block=tb,
                            revisions=bundle.revisions,
                            register=bundle.register,
                        )
                    ),
                )
            )

    if "section" in bundle.kinds:
        number = number_for("section", 0, 1)
        entries.append(
            (
                "section-a",
                "section",
                number,
                "Section A-A",
                lambda tb, num=number: ref.section_sheet(
                    doc,
                    number=num,
                    title_block=tb,
                    revisions=bundle.revisions,
                    register=bundle.register,
                ),
            )
        )

    if "schedule" in bundle.kinds:
        number = number_for("schedule", 0, 1)
        entries.append(
            (
                "door-window-schedule",
                "schedule",
                number,
                "Door & Window Schedule",
                lambda tb, num=number: ref.schedule_sheet(
                    doc,
                    number=num,
                    title_block=tb,
                    revisions=bundle.revisions,
                    register=bundle.register,
                ),
            )
        )

    if "area-statement" in bundle.kinds and bundle.areas is not None:
        number = number_for("area-statement", 0, 1)
        entries.append(
            (
                "area-statement",
                "area-statement",
                number,
                "Area Statement",
                lambda tb, num=number: ref.area_statement_sheet(
                    doc,
                    bundle.areas,
                    number=num,
                    title_block=tb,
                    revisions=bundle.revisions,
                    register=bundle.register,
                ),
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Export formats. Each is a thin, lazily-imported boundary.
# ---------------------------------------------------------------------------
def sheet_dxf_bytes(drawings: Sequence[Any]) -> bytes:
    """One DXF holding every sheet as a block (§7 layer convention + DIMSTYLE).

    Import is lazy and the error is re-raised as a :class:`PipelineError` with an
    install hint, because a worker image without ezdxf must fail the *DXF format*,
    not the job.
    """
    from services.drawings.export.dxf import EzdxfMissing, write_dxf_bytes

    try:
        return write_dxf_bytes(list(drawings))
    except EzdxfMissing as exc:
        raise PipelineError(
            "DXF export isn't available on this server.",
            action="Download the PDF instead, or ask your administrator to install ezdxf.",
            detail=str(exc),
        ) from exc


def svg_set_to_pdf_bytes(
    svgs: Sequence[str], *, timeout_seconds: int = 60
) -> tuple[bytes, dict[str, Any]]:
    """The ``pdf-set`` export: one vector page per sheet, print-true.

    Needs a converter binary (rsvg-convert / chromium / inkscape) and, for more than
    one page, a merge tool. There is deliberately no fallback: a rasterised or
    hand-rolled PDF submitted to a municipal office gets rejected, and a rejected
    drawing is worse than an honest error.
    """
    import os
    import tempfile

    from services.drawings.export.pdf import PdfToolMissing, converter_report, svg_set_to_pdf

    directory = tempfile.mkdtemp(prefix="garh-pdfset-")
    target = os.path.join(directory, "set.pdf")
    try:
        try:
            report = svg_set_to_pdf(list(svgs), target, timeout_seconds=timeout_seconds)
        except PdfToolMissing as exc:
            raise PipelineError(
                "PDF export isn't available on this server yet.",
                action="Download the DXF instead, or ask your administrator to install "
                "a PDF converter.",
                detail="%s | %s" % (exc, json.dumps(converter_report(), sort_keys=True)),
            ) from exc
        with open(target, "rb") as handle:
            return (handle.read(), dict(report))
    finally:
        import shutil

        shutil.rmtree(directory, ignore_errors=True)


def sheet_glb_bytes(document: Mapping[str, Any], *, name: str = "garh-model") -> bytes:
    """glTF/GLB of the model itself (F9 "Lumion/D5 bridge"). Pure stdlib."""
    from services.drawings.export.gltf import write_glb_bytes

    return write_glb_bytes(_parse_document(document), name=name)


def sheet_png_manifest(
    drawings: Sequence[Any], *, preset_name: str = "review"
) -> tuple[dict[str, Any], ...]:
    """Filenames and pixel sizes for the ``png-pack``. Sizing only — no rasterisation."""
    from services.drawings.export.png import pack_plan

    return pack_plan([d.sheet for d in drawings], preset_name=preset_name)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _parse_document(document: Mapping[str, Any]) -> Any:
    from garh_model.model import ProjectDoc

    if not isinstance(document, Mapping):
        raise PipelineError(
            "This design couldn't be read.",
            action="Save the design and generate the sheets again.",
            detail="model document is %s, expected an object" % type(document).__name__,
        )
    payload = (
        document.get("doc") if "doc" in document and "schemaVersion" not in document else document
    )
    try:
        return ProjectDoc.from_json(payload)  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError(
            "This design couldn't be read.",
            action="Save the design and generate the sheets again.",
            detail="ProjectDoc.from_json failed: %s: %s" % (type(exc).__name__, exc),
        ) from exc


def _state_hash(doc: Any) -> str | None:
    try:
        from garh_model.fold import state_hash

        return str(state_hash(doc))
    except Exception:
        return None


def _has_walls(house: Any, storey_id: str) -> bool:
    return any(getattr(wall, "storey_id", None) == storey_id for wall in house.walls)


def _statutory(bundle: SheetBundle) -> tuple[tuple[str, str], ...]:
    """Statutory identifiers for this set's authority, or none (D-4).

    An unknown authority id draws nothing rather than failing the job. A sheet job that
    died because a project row named a template this build does not ship would cost an
    architect their whole set; the missing boxes are visible on the drawing, and the
    readiness check — which is where an architect looks before submitting — reports the
    same gap in words.
    """
    if not bundle.authority:
        return ()
    from services.drawings.submission import statutory_pairs, template_for

    try:
        template = template_for(bundle.authority)
    except Exception:  # a bad or unknown template must not kill a whole set
        return ()
    return statutory_pairs(template, bundle.title_block_fields)


def _title_block(bundle: SheetBundle) -> Any:
    """Firm/project fields → a ``TitleBlock``. Unknown keys are ignored, not guessed."""
    from services.drawings.sheets import TitleBlock

    fields = dict(bundle.title_block_fields)
    return TitleBlock(
        statutory=_statutory(bundle),
        firm_name=str(fields.get("firmName") or ""),
        project_name=str(fields.get("projectName") or ""),
        client_name=str(fields.get("clientName") or ""),
        revision=str(fields.get("revision") or "A"),
        date=str(fields.get("date") or ""),
        drawn_by=str(fields.get("drawnBy") or ""),
        checked_by=str(fields.get("checkedBy") or ""),
        notes=str(fields.get("notes") or ""),
        logo_url=(str(fields["logoUrl"]) if fields.get("logoUrl") else None),
    )


def _restamp(sheet: Any, *, slug: str, number: str, title: str) -> Any:
    """Give the drawn sheet its canonical slug and number.

    ``reference_sheets`` names sheets after storeys ("sheet-plan-ground-floor"); this
    replaces that with the stable slug so ``Annotation.sheetId`` survives a rename.
    The title block inside the frame is restamped too, or the printed sheet number and
    the persisted one would disagree.
    """
    frame = getattr(sheet, "frame", None)
    block = getattr(frame, "title_block", None)
    if frame is not None and block is not None:
        frame = replace(frame, title_block=replace(block, sheet_number=number, drawing_title=title))
        return replace(sheet, id=slug, number=number, title=title, frame=frame)
    return replace(sheet, id=slug, number=number, title=title)


def _render_svg(drawing: Any) -> str:
    """Render, sanitise (§13), normalise (§16). All three, in that order, always."""
    from services.drawings.render.sanitize import assert_sanitary
    from services.drawings.render.svg import normalize_svg, render_sheet_svg

    svg = normalize_svg(render_sheet_svg(drawing))
    # render_sheet_svg escapes its text, but the guard runs on the finished document:
    # §13's rule is about what leaves the process, not about trusting the producer.
    assert_sanitary(svg)
    return svg


def _viewport_json(sheet: Any) -> dict[str, Any]:
    viewport = getattr(sheet, "viewport", None)
    if viewport is None:
        return {}
    try:
        return dict(viewport.to_json())
    except Exception:
        return {}


def _chain_json(chain: Any) -> dict[str, Any]:
    """``DimChain.to_json()`` plus its own sum.

    The serialisation itself is the model's — one shape, defined once, so a chain in a
    persisted layout, a chain in a golden and a chain in a log line all read alike.
    ``sumMm`` is added here and is redundant with the segments by construction, which is
    the point: a broken chain then shows up in the stored layout and in a golden diff,
    not only as an exception in a worker log nobody reads.
    """
    payload = dict(chain.to_json())
    payload["sumMm"] = int(chain.sum_of_segments())
    return payload


def _element_ids(drawing: Any) -> list[str]:
    """Model element ids drawn on this sheet, sorted.

    This is what the Review Tray's re-attach picker offers: the elements actually
    visible on the sheet the orphaned annotation lives on. Offering the whole model
    would make the picker useless on a G+2.
    """
    ids = set()
    for group in drawing.groups:
        for primitive in group.primitives:
            element_id = getattr(primitive, "element_id", None)
            if element_id:
                ids.add(str(element_id))
    for chain in getattr(drawing, "chains", ()) or ():
        for segment in chain.segments:
            if segment.anchor_element_id:
                ids.add(str(segment.anchor_element_id))
    return sorted(ids)


def _label_collisions(drawing: Any) -> int:
    """§16: "collision-free assertion (no overlapping text bboxes)", counted on paper.

    Counted, not raised. A collision is a quality defect worth reporting on the job and
    surfacing in the UI; refusing to hand over the whole set because two labels touch
    would be the wrong trade for an architect on a deadline.
    """
    from services.drawings.dimensions import LabelBox, find_label_collisions
    from services.drawings.render.primitives import Text

    boxes: list[LabelBox] = []
    for group in drawing.groups:
        for index, primitive in enumerate(group.primitives):
            if not isinstance(primitive, Text) or not primitive.text.strip():
                continue
            height = int(primitive.height_paper_um)
            width = int(len(primitive.text) * height * 58 // 100)
            x_um, y_um = group.placement.to_paper_um(primitive.at)
            if primitive.anchor == "middle":
                x_um -= width // 2
            elif primitive.anchor == "end":
                x_um -= width
            # Paper space runs Y-down (see Placement), so a baseline sits at the box's
            # BOTTOM edge and the box extends upward — i.e. to smaller y.
            if primitive.baseline == "hanging":
                top = y_um
            elif primitive.baseline == "middle":
                top = y_um - height // 2
            else:
                top = y_um - height
            boxes.append(
                LabelBox(
                    x_mm=x_um,
                    y_mm=top,
                    width_mm=width,
                    height_mm=height,
                    owner_id="%s#%d" % (group.id, index),
                )
            )
    return len(find_label_collisions(boxes))


def _reason(exc: BaseException) -> str:
    message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    return str(message)[:400]


def digest(text: str) -> str:
    """Stable content digest for a rendered artifact (goldens, cache validators)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
