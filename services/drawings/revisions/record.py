"""What a revision *is*: number, date, description, author. **Pure, no geometry.**

F7-A asks for an "auto revision table"; a municipal set needs rather more than a table.
An Indian submission is issued, queried at the counter, corrected and re-issued, and the
sheets that come back have to say — on their face — which issue they are, what changed in
it, when, and who signed it off. That is the difference between a drawing set and a
printout, and it is four fields plus one invariant:

* **the number is the identity** and is never reused. R1 means one thing forever, because
  a reviewer's note quotes it;
* **the date is the issue date**, in §15's DD-MM-YYYY, and dates never run backwards down
  the register;
* **the description says what changed**, in the architect's words — it is what the
  reviewer reads first;
* **the author is who issued it**, because a signed drawing has a person behind it.

The fifth field, :attr:`Revision.state_hash`, is what makes the *clouds* possible rather
than hand-drawn: it pins the model state this revision was issued at, so
``diff_models(state_of(R1), state_of(R2))`` is a computation and not a memory. It is
optional — a set can carry a register without ever clouding — but a revision that has one
can be re-clouded from the op log at any time, which a hand-maintained cloud list cannot.

Everything here is validated at construction. A revision number with a newline in it, a
date of ``31-02-2026`` or a register whose dates run backwards is a defect that must
surface when the record is made, not when a sheet is printed.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as _date
from typing import Any

__all__ = [
    "DATE_PATTERN",
    "MAX_AUTHOR_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NUMBER_LENGTH",
    "Revision",
    "RevisionHistory",
    "parse_date",
]

#: §15: Indian sets are dated DD-MM-YYYY. Exactly two/two/four digits — "1-2-2026" is
#: ambiguous on a sheet that will be read in three offices.
DATE_PATTERN = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")

#: Bounds. A revision number is a label ("R1", "A", "3"), not a sentence; a description
#: is one line of a table cell. Long values are refused rather than silently truncated,
#: because a truncated description on a submission drawing loses the very thing the
#: reviewer asked to see changed.
MAX_NUMBER_LENGTH = 8
MAX_DESCRIPTION_LENGTH = 120
MAX_AUTHOR_LENGTH = 40


def parse_date(text: str) -> _date:
    """``'05-03-2026' -> date(2026, 3, 5)``. Refuses anything that is not a real day.

    Two checks, not one: the shape (so a sheet never prints ``5-3-26``) and the calendar
    (so ``31-02-2026`` cannot be issued). ``datetime.strptime`` alone accepts the first
    kind of mistake, which is how a set ends up with two date formats down one register.
    """
    match = DATE_PATTERN.match(text)
    if match is None:
        raise ValueError(
            "A revision date must be DD-MM-YYYY (§15), got %r. Format it at the "
            "boundary — the sheet prints this string verbatim." % text
        )
    day, month, year = (int(part) for part in match.groups())
    try:
        return _date(year, month, day)
    except ValueError as error:
        raise ValueError("%r is not a real date: %s" % (text, error)) from error


def _clean(name: str, value: str, *, max_length: int) -> str:
    """One line, printable, bounded — or a loud failure naming the field."""
    if not isinstance(value, str):
        raise TypeError("%s must be a string, got %r (%s)" % (name, value, type(value).__name__))
    stripped = value.strip()
    if not stripped:
        raise ValueError(
            "%s cannot be empty. A revision with no %s is a revision nobody can cite."
            % (name, name.split(".")[-1])
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in stripped):
        raise ValueError(
            "%s contains a control character. Sheet text is one line; split it at the "
            "boundary." % name
        )
    if len(stripped) > max_length:
        raise ValueError(
            "%s is %d characters; the limit is %d. It has to fit a title-block cell, and "
            "a silently truncated %s is worse than a rejected one."
            % (name, len(stripped), max_length, name.split(".")[-1])
        )
    return stripped


@dataclass(frozen=True)
class Revision:
    """One issue of the set: ``R1 · 05-03-2026 · "Setbacks revised per query" · SG``."""

    number: str
    date: str
    description: str
    author: str
    #: ``garh_model.state_hash`` of the model as issued, when it is known. This is what
    #: lets the clouds be derived rather than remembered — see :mod:`..diff`.
    state_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "number", _clean("revision.number", self.number, max_length=MAX_NUMBER_LENGTH)
        )
        object.__setattr__(
            self,
            "description",
            _clean("revision.description", self.description, max_length=MAX_DESCRIPTION_LENGTH),
        )
        object.__setattr__(
            self, "author", _clean("revision.author", self.author, max_length=MAX_AUTHOR_LENGTH)
        )
        parse_date(self.date)  # raises with the field named
        if self.state_hash is not None:
            object.__setattr__(
                self, "state_hash", _clean("revision.state_hash", self.state_hash, max_length=128)
            )

    @property
    def issued_on(self) -> _date:
        """The date as a date. Parsed on demand so the stored form stays the printed one."""
        return parse_date(self.date)

    def title_block_row(self) -> tuple[str, str, str]:
        """``(rev, date, description)`` — the shape ``render.frame`` already prints."""
        return (self.number, self.date, self.description)

    def register_row(self) -> tuple[str, str, str, str]:
        """``(rev, date, description, author)`` — the full register line."""
        return (self.number, self.date, self.description, self.author)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "number": self.number,
            "date": self.date,
            "description": self.description,
            "author": self.author,
        }
        if self.state_hash is not None:
            out["stateHash"] = self.state_hash
        return out

    #: The two spellings of one row, and why both are read.
    #:
    #: ``number``/``description`` is this module's own JSON (:meth:`to_json`) and what a
    #: register round-trips through. ``revision``/``note`` is what the **API actually
    #: sends**: ``garh_api.schemas.sheets.RevisionRow`` has fields ``revision``, ``date``,
    #: ``note`` and ``author``, and ``routers/sheets.py`` puts those verbatim into the
    #: ``drawings.generate_sheets`` payload. Reading only ``number`` meant every real job
    #: raised here, ``pipeline._register_from`` swallowed it, and the register silently
    #: never drew — a whole feature inert in production with nothing red anywhere.
    _NUMBER_KEYS = ("number", "revision")
    _DESCRIPTION_KEYS = ("description", "note")

    @classmethod
    def from_json(cls, raw: Any) -> Revision:
        """Accept either object form the product uses, or the title block's three-tuple.

        The object forms are this module's ``{"number", "description", ...}`` and the
        API's ``{"revision", "note", ...}``; see :data:`_NUMBER_KEYS`. Anything else —
        a row with neither spelling of the number — is refused by name, because a
        register row nobody can cite is not a register row.
        """
        if isinstance(raw, Revision):
            return raw
        if isinstance(raw, list | tuple):
            if len(raw) < 3:
                raise ValueError(
                    "A revision tuple is (number, date, description[, author]), got %r" % (raw,)
                )
            author = str(raw[3]) if len(raw) > 3 and raw[3] else "-"
            return cls(str(raw[0]), str(raw[1]), str(raw[2]), author)
        if not hasattr(raw, "get"):
            raise ValueError(
                "A revision needs a number (%s), a date, a description and an author; got %r"
                % (" or ".join(cls._NUMBER_KEYS), raw)
            )
        number = next((raw.get(key) for key in cls._NUMBER_KEYS if raw.get(key)), None)
        if not number:
            raise ValueError(
                "A revision needs a number (%s), a date, a description and an author; got %r"
                % (" or ".join(cls._NUMBER_KEYS), raw)
            )
        description = next((raw.get(key) for key in cls._DESCRIPTION_KEYS if raw.get(key)), "")
        return cls(
            number=str(number),
            date=str(raw.get("date") or ""),
            description=str(description),
            author=str(raw.get("author") or "-"),
            state_hash=(str(raw["stateHash"]) if raw.get("stateHash") else None),
        )


class RevisionHistory(tuple):  # type: ignore[type-arg]
    """The register: revisions in issue order, oldest first.

    A tuple subclass rather than a wrapper so it slices, iterates and compares like the
    sequence it is. The invariants are checked once, here, because every one of them is a
    thing that gets a set queried:

    * **no number is reused** — a second "R1" makes every reference to R1 ambiguous;
    * **dates do not run backwards** — a register that goes 05-03, 12-03, 08-03 says the
      set was issued out of order, which is either a typo or a serious problem, and
      either way the person entering it should hear about it now.

    Equal dates are allowed: two revisions can genuinely be issued the same day.
    """

    __slots__ = ()

    def __new__(cls, revisions: Iterable[Any] = ()) -> RevisionHistory:
        records = tuple(Revision.from_json(item) for item in revisions)
        seen: dict[str, int] = {}
        for index, record in enumerate(records):
            if record.number in seen:
                raise ValueError(
                    "Revision number %r is used twice (rows %d and %d). A number is an "
                    "identity: a reviewer's query quotes it, so it can never be reused."
                    % (record.number, seen[record.number] + 1, index + 1)
                )
            seen[record.number] = index
        for previous, current in itertools.pairwise(records):
            if current.issued_on < previous.issued_on:
                raise ValueError(
                    "Revision %s is dated %s, before %s (%s). The register is in issue "
                    "order; fix the date or the order."
                    % (current.number, current.date, previous.number, previous.date)
                )
        return super().__new__(cls, records)

    @property
    def latest(self) -> Revision | None:
        return self[-1] if self else None

    def by_number(self, number: str) -> Revision:
        for record in self:
            if record.number == number:
                return record
        raise KeyError(
            "no revision %r in this register (have: %s)"
            % (number, ", ".join(r.number for r in self) or "none")
        )

    def previous_to(self, number: str) -> Revision | None:
        """The revision issued immediately before ``number``, or None for the first."""
        for index, record in enumerate(self):
            if record.number == number:
                return self[index - 1] if index else None
        raise KeyError("no revision %r in this register" % number)

    def title_block_rows(self) -> tuple[tuple[str, str, str], ...]:
        """What ``render.frame.title_block_primitives`` takes, newest last."""
        return tuple(record.title_block_row() for record in self)

    def register_rows(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(record.register_row() for record in self)

    def to_json(self) -> list[dict[str, Any]]:
        return [record.to_json() for record in self]
