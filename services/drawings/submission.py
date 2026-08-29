"""Per-authority submission templates (D-4) — what one municipal desk wants of a SET.

A rule pack and a submission template answer different questions, and conflating them
is the mistake this module exists to prevent:

* a **rule pack** answers *is this design legal* — setbacks, FAR, coverage, heights;
* a **submission template** answers *is this SET submittable* — which drawings, in
  which order, on what paper, carrying which statutory identifiers.

A design can be perfectly compliant and still come back across the counter because the
khata number is missing from the title block. That rejection costs an architect a
fortnight, and it is invisible to a compliance engine.

Bengaluru is why the two cannot be one file. BBMP and BDA sanction plots in the same
city under the same ``blr`` rule pack, and want different sets — so the template is
keyed on the *authority*, and a city pack may carry several.

## Everything here is a seed, and says so

None of these templates has been checked against an authority's published checklist.
They encode the conventional Indian plan-sanction set and this product's own drawing
vocabulary, marked ``confidence: "seed"`` and ``review: "unreviewed"`` exactly like the
rule packs, and every :class:`SubmissionReadiness` carries that status outward so no
screen can show a green tick without it. :meth:`SubmissionReadiness.ready` means "this
set contains what the template asks for", never "this will be sanctioned" — a product
that blurred those two would be selling an assurance it cannot give.

## The failure this module is built to avoid

A template that requires a sheet kind the vocabulary does not have, or names a city
pack that does not exist, would be *inert*: it would load, it would look right in the
JSON, and the requirement would simply never fire — while the readiness check went on
reporting green. That is bug class 2 in ``CLAUDE.md``, and it shipped here once already
(83 rules went quiet because a default sat outside the packs' own enum). So the loader
validates every template against the real vocabularies at load time and raises, and
:func:`load_templates` is called by a test that breaks each of those on purpose.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

# One-way dependency, deliberately: submission reads the pipeline's sheet vocabulary,
# and the pipeline never reads a template. Ordering a set to a template is the
# handler's job (it already imports both), which keeps this module importable by the
# API without dragging a worker's job machinery in behind it.
from services.drawings.pipeline import SUBMISSION_DB_KINDS
from services.drawings.sheets.frame import LABEL_MAX_CHARS

__all__ = [
    "RequiredSheet",
    "Shortfall",
    "StatutoryField",
    "SubmissionReadiness",
    "SubmissionTemplate",
    "SubmissionTemplateError",
    "check_submission",
    "load_templates",
    "statutory_pairs",
    "submission_template_dir",
    "template_for",
    "templates_for_city_pack",
]

#: services/drawings/submission.py → ../../ is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


class SubmissionTemplateError(RuntimeError):
    """A template on disk is unusable. Raised at load, never mid-job.

    Loudly, and naming the file: a template that fails silently is a requirement that
    never fires, which is worse than no template at all.
    """


def submission_template_dir() -> Path:
    """Where the templates live. ``GARH_SUBMISSION_TEMPLATE_DIR`` overrides, for tests."""
    override = os.environ.get("GARH_SUBMISSION_TEMPLATE_DIR")
    if override:
        return Path(override)
    return _REPO_ROOT / "rulepacks" / "submission"


# ---------------------------------------------------------------------------
# The template
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RequiredSheet:
    """One sheet an authority expects, and whether it is actually mandatory."""

    kind: str
    required: bool = True
    note: str = ""


@dataclass(frozen=True)
class StatutoryField:
    """One identifier the authority wants printed in the title block.

    Not a title-block field in the ordinary sense: ``khataNumber`` means nothing to the
    renderer, so these are carried as ``(label, value)`` pairs into the title block's
    statutory row. :func:`statutory_pairs` builds that row, and a test asserts the row
    reaches paper — a required field the frame silently drops would be a check that
    reports green over a blank line on the drawing.
    """

    key: str
    label: str
    required: bool = True
    note: str = ""


@dataclass(frozen=True)
class SubmissionTemplate:
    """What one sanctioning authority wants. Read from JSON, never constructed by hand."""

    authority: str
    city_pack: str
    title: str
    short_title: str
    citation: str
    confidence: str
    review: str
    verify: str
    paper: str
    scale_denominator: int
    sheets: tuple[RequiredSheet, ...] = ()
    statutory_fields: tuple[StatutoryField, ...] = ()
    declarations: tuple[str, ...] = ()

    def required_kinds(self) -> tuple[str, ...]:
        """The kinds that are actually mandatory, in the authority's own order."""
        return tuple(sheet.kind for sheet in self.sheets if sheet.required)

    def sheet_order(self) -> tuple[str, ...]:
        """Every kind the template mentions, required or not, in its own order.

        The order is the point. A reviewer works down a set in the order they expect it;
        an area statement filed behind the schedules reads as a set that was not checked.
        """
        return tuple(sheet.kind for sheet in self.sheets)

    def required_field_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.statutory_fields if f.required)

    def to_json(self) -> dict[str, Any]:
        return {
            "authority": self.authority,
            "cityPack": self.city_pack,
            "title": self.title,
            "shortTitle": self.short_title,
            "citation": self.citation,
            "confidence": self.confidence,
            "review": self.review,
            "verify": self.verify,
            "paper": self.paper,
            "scaleDenominator": self.scale_denominator,
            "sheets": [
                {"kind": s.kind, "required": s.required, "note": s.note} for s in self.sheets
            ],
            "statutoryFields": [
                {"key": f.key, "label": f.label, "required": f.required, "note": f.note}
                for f in self.statutory_fields
            ],
            "declarations": list(self.declarations),
        }


# ---------------------------------------------------------------------------
# Loading, with the gates that keep a template from going inert
# ---------------------------------------------------------------------------
def _read(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise SubmissionTemplateError("cannot read %s: %s" % (path.name, exc)) from exc
    except ValueError as exc:
        raise SubmissionTemplateError("%s is not valid JSON: %s" % (path.name, exc)) from exc


def _known_city_packs() -> frozenset[str]:
    """Pack ids from ``rulepacks/index.json``.

    Read rather than hard-coded so a template pointing at a pack nobody ships fails at
    load instead of being quietly unofferable.
    """
    index = _read(_REPO_ROOT / "rulepacks" / "index.json")
    packs = index.get("packs") if isinstance(index, dict) else None
    if not isinstance(packs, list):
        raise SubmissionTemplateError("rulepacks/index.json has no packs list")
    return frozenset(str(p.get("pack")) for p in packs if isinstance(p, dict) and p.get("pack"))


def _template_from(
    payload: Mapping[str, Any], *, name: str, packs: frozenset[str]
) -> SubmissionTemplate:
    """One template, validated against the real vocabularies. Raises rather than guesses."""

    def _need(key: str) -> Any:
        if key not in payload or payload[key] in (None, ""):
            raise SubmissionTemplateError("%s is missing %r" % (name, key))
        return payload[key]

    authority = str(_need("authority"))
    city_pack = str(_need("cityPack"))
    if city_pack not in packs:
        raise SubmissionTemplateError(
            "%s points at rule pack %r, which is not in rulepacks/index.json — the "
            "template would never be offered to anyone" % (name, city_pack)
        )

    sheets: list[RequiredSheet] = []
    seen_kinds: set[str] = set()
    for row in payload.get("sheets") or ():
        if not isinstance(row, Mapping):
            raise SubmissionTemplateError("%s has a sheet entry that is not an object" % name)
        kind = str(row.get("kind") or "")
        if kind not in SUBMISSION_DB_KINDS:
            raise SubmissionTemplateError(
                "%s requires sheet kind %r, which this product does not draw (it draws "
                "%s) — the requirement could never be met, and would sit green forever"
                % (name, kind, ", ".join(SUBMISSION_DB_KINDS))
            )
        if kind in seen_kinds:
            raise SubmissionTemplateError("%s lists sheet kind %r twice" % (name, kind))
        seen_kinds.add(kind)
        sheets.append(
            RequiredSheet(
                kind=kind, required=bool(row.get("required", True)), note=str(row.get("note") or "")
            )
        )
    if not sheets:
        raise SubmissionTemplateError("%s lists no sheets at all" % name)

    fields: list[StatutoryField] = []
    seen_keys: set[str] = set()
    for row in payload.get("statutoryFields") or ():
        if not isinstance(row, Mapping):
            raise SubmissionTemplateError("%s has a statutory field that is not an object" % name)
        key = str(row.get("key") or "")
        label = str(row.get("label") or "")
        if not key or not label:
            raise SubmissionTemplateError(
                "%s has a statutory field without a key or a label; an unlabelled box on "
                "a sanction drawing is worse than no box" % name
            )
        if len(label) > LABEL_MAX_CHARS:
            raise SubmissionTemplateError(
                "%s labels %r with %d characters; the title block prints %d and would "
                "silently truncate it. A statutory label ending in an ellipsis is a "
                "defect nobody sees until the drawing is on a counter — shorten it here."
                % (name, label, len(label), LABEL_MAX_CHARS)
            )
        if key in seen_keys:
            raise SubmissionTemplateError("%s lists statutory field %r twice" % (name, key))
        seen_keys.add(key)
        fields.append(
            StatutoryField(
                key=key,
                label=label,
                required=bool(row.get("required", True)),
                note=str(row.get("note") or ""),
            )
        )

    scale = payload.get("scaleDenominator")
    if not isinstance(scale, int) or scale <= 0:
        raise SubmissionTemplateError("%s has no usable scaleDenominator" % name)

    return SubmissionTemplate(
        authority=authority,
        city_pack=city_pack,
        title=str(_need("title")),
        short_title=str(payload.get("shortTitle") or authority.upper()),
        citation=str(_need("citation")),
        confidence=str(payload.get("confidence") or "seed"),
        review=str(payload.get("review") or "unreviewed"),
        verify=str(payload.get("verify") or ""),
        paper=str(_need("paper")),
        scale_denominator=scale,
        sheets=tuple(sheets),
        statutory_fields=tuple(fields),
        declarations=tuple(str(d) for d in (payload.get("declarations") or ())),
    )


@lru_cache(maxsize=4)
def _load_cached(directory: str) -> tuple[tuple[str, SubmissionTemplate], ...]:
    root = Path(directory)
    index = _read(root / "index.json")
    rows = index.get("templates") if isinstance(index, dict) else None
    if not isinstance(rows, list) or not rows:
        raise SubmissionTemplateError("submission/index.json lists no templates")

    packs = _known_city_packs()
    loaded: list[tuple[str, SubmissionTemplate]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise SubmissionTemplateError("submission/index.json has a non-object entry")
        file_name = str(row.get("file") or "")
        if not file_name:
            raise SubmissionTemplateError("submission/index.json entry has no file")
        payload = _read(root / file_name)
        if not isinstance(payload, Mapping):
            raise SubmissionTemplateError("%s is not an object" % file_name)
        template = _template_from(payload, name=file_name, packs=packs)
        # The manifest and the file must agree. They are edited separately, and a
        # disagreement means one of the two is describing a template nobody serves.
        if row.get("authority") and str(row["authority"]) != template.authority:
            raise SubmissionTemplateError(
                "submission/index.json calls %s %r but the file says %r"
                % (file_name, row["authority"], template.authority)
            )
        if row.get("cityPack") and str(row["cityPack"]) != template.city_pack:
            raise SubmissionTemplateError(
                "submission/index.json puts %s under pack %r but the file says %r"
                % (file_name, row["cityPack"], template.city_pack)
            )
        loaded.append((template.authority, template))

    seen = [a for a, _ in loaded]
    if len(set(seen)) != len(seen):
        raise SubmissionTemplateError("two templates claim the same authority: %r" % (seen,))
    return tuple(loaded)


def load_templates() -> dict[str, SubmissionTemplate]:
    """Every template on disk, keyed by authority. Order follows the manifest."""
    return dict(_load_cached(str(submission_template_dir())))


def reset_cache() -> None:
    """Drop the load cache. For tests that write templates to a temp directory."""
    _load_cached.cache_clear()


def template_for(authority: str) -> SubmissionTemplate:
    """One template by authority id, or :class:`KeyError` naming what does exist."""
    templates = load_templates()
    try:
        return templates[authority]
    except KeyError:
        raise KeyError(
            "no submission template for %r; this build ships %s"
            % (authority, ", ".join(sorted(templates)))
        ) from None


def templates_for_city_pack(city_pack: str) -> tuple[SubmissionTemplate, ...]:
    """Every authority that sanctions under one rule pack.

    Bengaluru returns two. A UI that assumed one would silently pick whichever came
    first, and half of Bengaluru would get the wrong checklist.
    """
    return tuple(t for t in load_templates().values() if t.city_pack == city_pack)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Shortfall:
    """One thing standing between this set and the counter. Never a bare boolean."""

    #: "sheet" | "field" | "paper" | "order"
    kind: str
    #: The sheet kind or field key at fault.
    what: str
    #: A sentence an architect can act on, in their words.
    detail: str


@dataclass(frozen=True)
class SubmissionReadiness:
    """What the template asks for, measured against what the set actually has.

    ``ready`` means every mandatory item is present. It does NOT mean the set will be
    sanctioned, and :attr:`confidence` travels with it so nothing downstream can render
    a green tick without also rendering "seed, unreviewed".
    """

    authority: str
    title: str
    ready: bool
    shortfalls: tuple[Shortfall, ...] = ()
    confidence: str = "seed"
    review: str = "unreviewed"
    verify: str = ""
    #: Present-and-required, for a progress reading that is honest about the denominator.
    satisfied: int = 0
    total: int = 0
    advisories: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "authority": self.authority,
            "title": self.title,
            "ready": self.ready,
            "shortfalls": [
                {"kind": s.kind, "what": s.what, "detail": s.detail} for s in self.shortfalls
            ],
            "confidence": self.confidence,
            "review": self.review,
            "verify": self.verify,
            "satisfied": self.satisfied,
            "total": self.total,
            "advisories": list(self.advisories),
        }


def _article(word: str) -> str:
    """ "a" or "an". Small, but these sentences are read by an architect under pressure."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def check_submission(
    template: SubmissionTemplate,
    *,
    kinds: Sequence[str],
    title_block_fields: Mapping[str, Any] | None = None,
    paper: str | None = None,
) -> SubmissionReadiness:
    """Measure one set against one authority's template.

    ``kinds`` is what the set actually contains; ``title_block_fields`` is the payload
    the sheet job was given. Both come from the set as it stands, never from the
    template — checking a template against itself is the shape of a test that cannot
    fail, and this repository has shipped one of those.

    Optional items become *advisories* rather than shortfalls. A door schedule is worth
    having and is not worth blocking a submission over, and a checklist that cries wolf
    on optional items is a checklist an architect stops reading.
    """
    present = set(kinds)
    fields = dict(title_block_fields or {})
    shortfalls: list[Shortfall] = []
    advisories: list[str] = []
    satisfied = 0
    total = 0

    for sheet in template.sheets:
        if not sheet.required:
            if sheet.kind not in present:
                advisories.append(
                    "%s asks for %s %s sheet but does not require it%s"
                    % (
                        template.short_title,
                        _article(sheet.kind),
                        sheet.kind,
                        (" — %s" % sheet.note) if sheet.note else "",
                    )
                )
            continue
        total += 1
        if sheet.kind in present:
            satisfied += 1
        else:
            shortfalls.append(
                Shortfall(
                    kind="sheet",
                    what=sheet.kind,
                    detail="%s requires %s %s sheet%s"
                    % (
                        template.short_title,
                        _article(sheet.kind),
                        sheet.kind,
                        (": %s" % sheet.note) if sheet.note else "",
                    ),
                )
            )

    for statutory in template.statutory_fields:
        if not statutory.required:
            continue
        total += 1
        value = fields.get(statutory.key)
        if value is not None and str(value).strip():
            satisfied += 1
        else:
            shortfalls.append(
                Shortfall(
                    kind="field",
                    what=statutory.key,
                    detail="%s wants %s in the title block%s"
                    % (
                        template.short_title,
                        statutory.label,
                        (" — %s" % statutory.note) if statutory.note else "",
                    ),
                )
            )

    if paper is not None and paper != template.paper:
        total += 1
        shortfalls.append(
            Shortfall(
                kind="paper",
                what=paper,
                detail="%s expects the set on %s; this one is on %s"
                % (template.short_title, template.paper, paper),
            )
        )
    elif paper is not None:
        total += 1
        satisfied += 1

    return SubmissionReadiness(
        authority=template.authority,
        title=template.title,
        ready=not shortfalls,
        shortfalls=tuple(shortfalls),
        confidence=template.confidence,
        review=template.review,
        verify=template.verify,
        satisfied=satisfied,
        total=total,
        advisories=tuple(advisories),
    )


def statutory_pairs(
    template: SubmissionTemplate, title_block_fields: Mapping[str, Any] | None = None
) -> tuple[tuple[str, str], ...]:
    """``(label, value)`` pairs for the title block's statutory row, in template order.

    Missing values are carried through as an empty string rather than dropped: an
    authority's box printed empty is a box the architect can see is empty, while a box
    that vanishes is a requirement nobody notices until the counter does.
    """
    fields = dict(title_block_fields or {})
    return tuple((f.label, str(fields.get(f.key) or "").strip()) for f in template.statutory_fields)
