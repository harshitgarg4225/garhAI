"""Sheet, annotation and title-block schemas (§7, F7-A, decision D13).

Split from ``schemas/jobs.py`` because these are not job shapes. A sheet set is a
job's *output*; a title block, a revision row and an annotation are project data
that outlive any job, and the Review Tray is a read surface over them.

Three contracts are worth reading before changing anything here.

**1. Annotations are written as ops, never through a route.** Golden rule 1 — "the
op is the atom; UI never mutates state directly". Op 32 (``annotation.set``) is the
only writer, and it goes through ``POST /projects/{id}/ops`` like every other
change, so annotations get undo/redo, versions, provenance and the copilot for free.
The ``annotations`` table is a *projection* of ``ProjectDoc.annotations`` maintained
for two things a folded document cannot give cheaply: a foreign key to
``sheets.id``, and "every orphan in this project" without folding. So there is an
:class:`AnnotationOut` and a :class:`ReviewTrayOut` here, and deliberately no
``AnnotationIn``.

**2. ``orphaned`` is derived, not remembered.** An annotation is orphaned exactly
when its ``anchorElementId`` is absent from the current folded document. That is the
same id-matching rule §7 describes for a solver re-run, applied continuously, and it
cannot go stale the way a stored flag can. It also means a manual delete of the
anchor element routes the note to the tray too — which is more correct than the spec's
minimum, and stated in the UI copy rather than left as a surprise.

**3. No fuzzy re-anchoring. Ever, in MVP.** D13 says so, ``Annotation.reattach``
takes an explicit ``anchorElementId``, and :class:`ReviewTrayOut.policy` carries the
sentence the UI prints so the promise and the copy cannot drift apart.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator

from garh_api.schemas import CamelModel, ResponseModel

# ---------------------------------------------------------------------------
# Title block (§7 "Title block: firm logo/fields template; sheet numbering;
# auto revision table")
# ---------------------------------------------------------------------------

#: Hard cap on stored revision rows. The printed table has room for a handful; an
#: unbounded list would grow the firm settings blob without ever being read.
MAX_REVISION_ROWS = 12


class RevisionRow(CamelModel):
    """One line of the auto revision table.

    ``date`` is a free string in DD-MM-YYYY (§15's Indian format) rather than a
    ``date``: it is printed verbatim on paper, submissions carry things like
    "12-03-2026 (rev. at counter)", and reformatting an architect's own text on a
    municipal drawing is not this API's business.

    These four field names are a **wire contract with the drawings worker**:
    ``routers/sheets.py`` puts ``model_dump(by_alias=True)`` of these rows straight into
    the ``drawings.generate_sheets`` payload, and
    ``services.drawings.revisions.record.Revision.from_json`` reads them back. Renaming
    one here without renaming it there empties the revision register on every sheet set
    and raises nothing — which is exactly what ``author`` being absent used to do to the
    register's BY column. ``test_revisions.py::
    test_the_api_revision_row_shape_is_the_shape_the_register_reads`` pins the pair.
    """

    revision: StrictStr = Field(max_length=8, description="A, B, C… or R1, R2.")
    date: StrictStr = Field(default="", max_length=24, description="DD-MM-YYYY.")
    note: StrictStr = Field(default="", max_length=120, description="What changed.")
    #: Who issued it. The register's BY column — the one column the compact title-block
    #: strip has no room for, and the reason the register exists at all. Optional because
    #: rows stored before this field existed have none; the register prints "-" for them
    #: rather than inventing a name on a signed drawing.
    author: StrictStr = Field(default="", max_length=40, description="Who issued it.")


class TitleBlockFields(CamelModel):
    """The editable title-block template. Every field is printed; none is inferred.

    ``sheetNumber``, ``drawingTitle`` and ``scaleLabel`` are deliberately absent: the
    generator stamps those per sheet, and letting a user set them here would let the
    printed number disagree with the persisted one.
    """

    firm_name: StrictStr = Field(default="", max_length=120)
    project_name: StrictStr = Field(default="", max_length=120)
    client_name: StrictStr = Field(default="", max_length=120)
    revision: StrictStr = Field(default="A", max_length=8)
    date: StrictStr = Field(default="", max_length=24)
    drawn_by: StrictStr = Field(default="", max_length=64)
    checked_by: StrictStr = Field(default="", max_length=64)
    notes: StrictStr = Field(default="", max_length=240)
    logo_url: StrictStr | None = Field(default=None, max_length=512)

    @field_validator("logo_url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        """A logo is fetched by the renderer and printed on a submission drawing.

        Refusing ``javascript:``/``data:`` here is not theatre: the value travels into
        an SVG the browser renders, and §13's sanitiser allowlist is the second line of
        defence, not the first.
        """
        if not value:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("logoUrl must be an http(s) URL.")
        return value


class DrawingPreferencesIn(CamelModel):
    """``PUT /firm/drawing-preferences`` — the firm-wide drafting template.

    Lives in ``firms.settings`` (whose column comment already names "title-block
    fields, dimToJamb, default city pack") plus ``firms.logo_url``. No migration, and
    the natural home: these are drafting-office conventions, not per-project data.
    """

    title_block: TitleBlockFields = Field(default_factory=TitleBlockFields)
    #: §7 step 6: "openings dimensioned to centerline (config flag `dimToJamb` for
    #: firm preference)". A firm-level setting because it is a house style, and one
    #: architect switching it mid-project would make two sheets in the same set
    #: dimension differently.
    dim_to_jamb: StrictBool = False
    sheet_number_prefix: StrictStr = Field(
        default="A", max_length=4, description="A-01, A-02… Some corporations want 'AR'."
    )
    default_scale_denominator: StrictInt = Field(default=100, ge=1, le=2000)
    default_sheet_size: StrictStr = Field(default="A2", max_length=8)
    revisions: list[RevisionRow] = Field(default_factory=list, max_length=MAX_REVISION_ROWS)

    @field_validator("sheet_number_prefix")
    @classmethod
    def _plain_prefix(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned or not cleaned.isalnum():
            raise ValueError("sheetNumberPrefix must be one or more letters or digits.")
        return cleaned


class DrawingPreferencesOut(ResponseModel):
    """What the title-block editor loads. ``source`` is why each value is what it is."""

    title_block: TitleBlockFields
    dim_to_jamb: StrictBool = False
    sheet_number_prefix: StrictStr = "A"
    default_scale_denominator: StrictInt = 100
    default_sheet_size: StrictStr = "A2"
    revisions: list[RevisionRow] = Field(default_factory=list)
    #: ``firm`` when the firm has saved a template, ``defaults`` when it has not.
    #: Shown as a chip, because golden rule 4 wants every default visible.
    source: StrictStr = "defaults"
    firm_logo_url: StrictStr | None = None


# ---------------------------------------------------------------------------
# Submission templates (D-4)
# ---------------------------------------------------------------------------
class SubmissionSheetOut(ResponseModel):
    """One sheet an authority expects, and whether it is actually mandatory."""

    kind: StrictStr
    required: StrictBool = True
    note: StrictStr = ""


class StatutoryFieldOut(ResponseModel):
    """One identifier the authority wants printed in the title block."""

    key: StrictStr
    label: StrictStr
    required: StrictBool = True
    note: StrictStr = ""


class SubmissionTemplateOut(ResponseModel):
    """What one sanctioning authority wants of a set.

    ``confidence`` and ``review`` are not decoration. Not one of these templates has
    been checked against a published municipal checklist, and every screen that shows a
    template must show that alongside it — the same rule the rule packs live under.
    """

    authority: StrictStr
    city_pack: StrictStr
    title: StrictStr
    short_title: StrictStr
    citation: StrictStr
    confidence: StrictStr
    review: StrictStr
    verify: StrictStr = ""
    paper: StrictStr
    scale_denominator: StrictInt
    sheets: list[SubmissionSheetOut] = Field(default_factory=list)
    statutory_fields: list[StatutoryFieldOut] = Field(default_factory=list)
    declarations: list[StrictStr] = Field(default_factory=list)


class SubmissionTemplateListOut(ResponseModel):
    templates: list[SubmissionTemplateOut] = Field(default_factory=list)


class ProjectSubmissionIn(CamelModel):
    """The authority this project is being submitted to, and its statutory values."""

    authority: StrictStr | None = None
    fields: dict[str, StrictStr] = Field(default_factory=dict)


class ProjectSubmissionOut(ResponseModel):
    authority: StrictStr | None = None
    fields: dict[str, StrictStr] = Field(default_factory=dict)
    #: Every authority available for this project's rule pack. Bengaluru returns two.
    available: list[SubmissionTemplateOut] = Field(default_factory=list)


class ShortfallOut(ResponseModel):
    """One thing standing between this set and the counter."""

    kind: StrictStr
    what: StrictStr
    detail: StrictStr


class SubmissionReadinessOut(ResponseModel):
    """What the template asks for, measured against the set that actually exists.

    ``ready`` means every mandatory item is present. It never means "this will be
    sanctioned", which is why ``confidence`` and ``review`` travel with it — a screen
    must not be able to render the tick without rendering what the tick is worth.
    """

    project_id: uuid.UUID
    authority: StrictStr | None = None
    title: StrictStr = ""
    ready: StrictBool = False
    shortfalls: list[ShortfallOut] = Field(default_factory=list)
    advisories: list[StrictStr] = Field(default_factory=list)
    satisfied: StrictInt = 0
    total: StrictInt = 0
    confidence: StrictStr = "seed"
    review: StrictStr = "unreviewed"
    verify: StrictStr = ""
    #: Set when no authority was chosen and the rule pack offers more than one, so the
    #: UI asks instead of guessing. Guessing hands half of Bengaluru the wrong checklist.
    choose_from: list[StrictStr] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Annotations & the Review Tray (§7, D13)
# ---------------------------------------------------------------------------
class AnnotationOut(ResponseModel):
    """One annotation, with enough context for the tray to be actionable.

    ``sheetSlug``/``sheetNumber`` matter: an orphan is useless in a list that only
    says "annotation 4c1f…". The architect needs to know it is the note on A-02A.
    """

    id: uuid.UUID
    #: The op log's own annotation id (``annotation_01J…``). **This** is what the client
    #: passes as op 32's ``id`` to re-attach or delete — the ``id`` above is the
    #: projection row's UUID and is not addressable by an op.
    model_annotation_id: StrictStr | None = None
    sheet_id: uuid.UUID
    sheet_slug: StrictStr | None = None
    sheet_number: StrictStr | None = None
    sheet_kind: StrictStr | None = None
    anchor_element_id: StrictStr | None = None
    anchor_kind: StrictStr = "element"
    #: Annotation content as authored by op 32: ``{text, leader, style}``.
    payload: dict[str, Any] = Field(default_factory=dict)
    orphaned: StrictBool = False
    #: Present only for orphans: the element ids still drawn on that sheet, which is
    #: what the re-attach picker offers. The whole model would be unusable on a G+2.
    reattach_candidates: list[StrictStr] = Field(default_factory=list)
    created_at: datetime | None = None

    @classmethod
    def of(
        cls,
        annotation: Any,
        *,
        sheet: Any = None,
        reattach_candidates: list[str] | None = None,
    ) -> AnnotationOut:
        layout = dict(getattr(sheet, "layout", {}) or {}) if sheet is not None else {}
        payload = dict(annotation.payload or {})
        # The projection's bookkeeping key is lifted into its own field rather than
        # leaked into the annotation's content — a UI that round-trips `payload` into
        # op 32 must not carry it back in.
        model_id = payload.pop("__modelId", None)
        return cls(
            id=annotation.id,
            model_annotation_id=(str(model_id) if model_id else None),
            sheet_id=annotation.sheet_id,
            sheet_slug=layout.get("sheetId"),
            sheet_number=getattr(sheet, "number", None),
            sheet_kind=getattr(sheet, "kind", None),
            anchor_element_id=annotation.anchor_element_id,
            anchor_kind=annotation.anchor_kind,
            payload=payload,
            orphaned=bool(annotation.orphaned),
            reattach_candidates=list(reattach_candidates or []),
            created_at=getattr(annotation, "created_at", None),
        )


#: The sentence the Review Tray prints, kept next to the code that enforces it so a
#: copy edit cannot quietly promise fuzzy matching (D13: "Fuzzy re-matching = later").
NO_FUZZY_REANCHOR_POLICY = (
    "Notes follow their element by id. When a new layout does not keep that id, the "
    "note lands here — we never guess a nearby element. Re-attach it yourself, or "
    "delete it."
)


class ReviewTrayOut(ResponseModel):
    """``GET /projects/{id}/sheets/review-tray`` — the D13 surface, honestly scoped."""

    project_id: uuid.UUID
    design_version_id: uuid.UUID | None = None
    orphaned: list[AnnotationOut] = Field(default_factory=list)
    #: Anchored annotations, so the tray can say "3 of 11 notes need attention".
    attached_count: StrictInt = 0
    policy: StrictStr = NO_FUZZY_REANCHOR_POLICY
    #: True when the tray reconciled against a freshly folded model on this request.
    #: False means the counts are as last written, and the UI says so rather than
    #: implying it just checked.
    reconciled: StrictBool = False


# ---------------------------------------------------------------------------
# Sheet content (the zoomable viewer)
# ---------------------------------------------------------------------------
class SheetContentOut(ResponseModel):
    """The sanitised SVG of one sheet, inline.

    Why the API hands over the bytes here when every *download* goes through a signed
    URL (§11): the viewer needs the markup in the document to pan, zoom and hit-test
    it, a sheet is tens of kilobytes, and an ``<img src>`` pointed at object storage
    would need CORS on the bucket and would give up the §13 re-check. The sanitiser
    runs again on the way out — this endpoint is the last place the SVG passes through
    our code before a browser parses it.
    """

    sheet_id: uuid.UUID
    slug: StrictStr | None = None
    number: StrictStr | None = None
    title: StrictStr | None = None
    kind: StrictStr
    scale_denominator: StrictInt | None = None
    paper: StrictStr | None = None
    #: Paper millimetres, for the viewer's initial fit.
    width_mm: StrictInt | None = None
    height_mm: StrictInt | None = None
    svg: StrictStr
    bytes: StrictInt = 0
    generated_at: datetime | None = None


class SheetSetSummaryOut(ResponseModel):
    """Set-level facts the Sheets tab shows above the thumbnails.

    ``chainSumOk`` is the §7 step-5 invariant, carried all the way to the UI. The
    worker asserts it before a sheet exists, so this is always true in practice —
    which is why it is worth displaying: it is the product's claim about its own
    drawings, and a false there is a bug an architect must see immediately.
    """

    project_id: uuid.UUID
    design_version_id: uuid.UUID | None = None
    sheet_count: StrictInt = 0
    chain_count: StrictInt = 0
    chain_sum_ok: StrictBool = True
    label_collisions: StrictInt = 0
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[StrictStr] = Field(default_factory=list)
    formats_available: list[StrictStr] = Field(default_factory=list)
    generated_at: datetime | None = None


__all__ = [
    "MAX_REVISION_ROWS",
    "NO_FUZZY_REANCHOR_POLICY",
    "AnnotationOut",
    "DrawingPreferencesIn",
    "DrawingPreferencesOut",
    "ReviewTrayOut",
    "RevisionRow",
    "SheetContentOut",
    "SheetSetSummaryOut",
    "TitleBlockFields",
]
