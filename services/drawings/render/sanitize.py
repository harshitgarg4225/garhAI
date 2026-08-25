"""SVG sanitisation (§13). **Fully implemented, pure, no dependency.**

    Input: ... SVG output sanitized (no scripts/foreignObject).

Two functions, and the split between them matters:

* :func:`escape_text` is applied to every string that becomes SVG character data or an
  attribute value. It is the *construction* guard.
* :func:`assert_sanitary` is run over the finished document. It is the *output* guard,
  and it exists because construction guards get bypassed by the next person who adds a
  feature. A sheet is a file we hand to a municipal reviewer and, via a share link, to
  a client's browser; the last thing that touches it should be a check that refuses to
  emit an executable document.

The threat is not hypothetical for this product. Room names, the title block's firm
name, client name and notes, and every user annotation are free text that an architect
types and a client can see — so `<script>` in a room name is a stored XSS delivered by
a drawing. §16 notes the SVG golden diff doubles as a security test for exactly this
reason: the goldens contain a hostile-name fixture, so a regression in escaping shows
up as a byte diff rather than as an incident.

The renderer never emits any of the constructs below, so :func:`assert_sanitary` should
never fire. That is the point: it is a tripwire, not a filter. It raises rather than
scrubbing, because silently stripping a `<script>` teaches nobody anything.
"""

from __future__ import annotations

import re
from typing import Tuple

__all__ = [
    "ALLOWED_ATTRIBUTES",
    "ALLOWED_ELEMENTS",
    "FORBIDDEN_ATTRIBUTE_PREFIXES",
    "FORBIDDEN_ELEMENTS",
    "FORBIDDEN_URI_SCHEMES",
    "SvgSanitizeError",
    "assert_sanitary",
    "escape_text",
    "safe_id",
]


class SvgSanitizeError(ValueError):
    """The SVG we were about to emit contains something executable. Always a bug."""


#: Elements that can execute script, load remote content, or embed HTML.
FORBIDDEN_ELEMENTS: Tuple[str, ...] = (
    "script",
    "foreignobject",
    "iframe",
    "embed",
    "object",
    "animate",
    "animatetransform",
    "set",
    "handler",
    "audio",
    "video",
    "link",
    "meta",
    "use",  # `use` can reference an external document; we never need it
    "image",  # no remote or data-URI raster inside a submission sheet
)

#: Attribute name prefixes that carry script. ``on*`` covers onload/onclick/onmouseover.
FORBIDDEN_ATTRIBUTE_PREFIXES: Tuple[str, ...] = ("on",)

#: Every element :mod:`services.drawings.render.svg` is allowed to emit.
#:
#: An allowlist, not a blocklist, and that is the whole design of :func:`assert_sanitary`.
#: A blocklist over the raw string cannot tell a real ``<body onload=...>`` from the
#: *escaped text* ``&lt;body onload=&quot;...`` that appears when an architect types a
#: hostile string into a room name — the second is completely safe and the first is an
#: incident, and they share a substring. Parsing tags and checking their names against
#: this list distinguishes them exactly: escaped payloads are character data and are
#: never parsed as a tag at all.
ALLOWED_ELEMENTS: Tuple[str, ...] = (
    "svg",
    "title",
    "defs",
    "pattern",
    "g",
    "line",
    "polyline",
    "polygon",
    "path",
    "circle",
    "rect",
    "text",
)

#: Every attribute the renderer is allowed to emit. Presentation and geometry only —
#: no href of any kind, no style (which can carry ``url()``), no event handler.
ALLOWED_ATTRIBUTES: Tuple[str, ...] = (
    "class",
    "cx",
    "cy",
    "d",
    "data-layer",
    "dominant-baseline",
    "fill",
    "fill-rule",
    "font-family",
    "font-size",
    "font-weight",
    "height",
    "id",
    "patternTransform",
    "patternUnits",
    "points",
    "r",
    "stroke",
    "stroke-dasharray",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-width",
    "text-anchor",
    "transform",
    "version",
    "viewBox",
    "width",
    "x",
    "x1",
    "x2",
    "xmlns",
    "y",
    "y1",
    "y2",
)

#: URI schemes that execute or embed. Checked anywhere in the document text.
FORBIDDEN_URI_SCHEMES: Tuple[str, ...] = (
    "javascript:",
    "vbscript:",
    "data:text/html",
    "data:image/svg+xml",
)

#: XML character-data escapes. Ampersand first, or the others get double-escaped.
_ESCAPES: Tuple[Tuple[str, str], ...] = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&apos;"),
)

#: XML 1.0 forbids most control characters outright — a file containing one is not
#: well-formed, so a renderer that passes them through produces an unopenable drawing.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


def escape_text(value: str) -> str:
    """Escape a string for use as SVG character data or an attribute value.

    Control characters are dropped rather than escaped: there is no legal XML
    representation for most of them, and a stray 0x01 in a room name is data corruption
    upstream, not something a drawing should try to render.
    """
    cleaned = _CONTROL_CHARS.sub("", value)
    for raw, escaped in _ESCAPES:
        cleaned = cleaned.replace(raw, escaped)
    return cleaned


def safe_id(value: str) -> str:
    """Reduce a string to something safe as an XML ``id``/``class`` token.

    Element ids from the model core are already ULID-ish and safe; this is for the ones
    that are not (sheet numbers with slashes, room names in a class attribute). The
    output is deterministic — no counters, no hashes of anything volatile — because ids
    appear in the goldens.
    """
    cleaned = _UNSAFE_ID_CHARS.sub("-", value.strip())
    cleaned = cleaned.strip("-")
    if not cleaned:
        return "x"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = "x-" + cleaned
    return cleaned


#: A tag opener: name, then the attribute run up to the closing angle bracket.
_TAG = re.compile(r"<\s*(/?)([A-Za-z_][-A-Za-z0-9_:.]*)([^>]*)>", re.DOTALL)
#: One attribute inside a tag. Quoted, single-quoted or bare.
_ATTRIBUTE = re.compile(
    r"""([-A-Za-z0-9_:.]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.DOTALL
)


def assert_sanitary(svg: str) -> None:
    """Raise :class:`SvgSanitizeError` if the document contains anything executable.

    Run on the complete output, immediately before it is returned or written.

    The check **parses tags** (with two small regexes — no XML library, no dependency)
    and holds every element and attribute name against
    :data:`ALLOWED_ELEMENTS` / :data:`ALLOWED_ATTRIBUTES`. Character data between tags is
    not inspected, because :func:`escape_text` has already made it inert: a room name of
    ``<script>alert(1)</script>`` arrives here as ``&lt;script&gt;…`` and is text, not a
    tag.

    This started life as a substring blocklist and had to be rewritten, which is worth
    recording: a blocklist flagged the *escaped* payload ``&lt;body onload=&quot;…`` as an
    event handler, because ``" onload="`` is a substring of safe output as well as of an
    attack. A blocklist that cries wolf on correct output gets switched off, and then the
    real thing walks through. An allowlist over parsed tags has no such ambiguity.
    """
    lowered = svg.lower()
    if "<!entity" in lowered or "<!doctype" in lowered:
        raise SvgSanitizeError(
            "SVG output contains a DOCTYPE or ENTITY declaration. That is the XXE / "
            "billion-laughs surface; a drawing never needs one."
        )

    control = _CONTROL_CHARS.search(svg)
    if control is not None:
        raise SvgSanitizeError(
            "SVG output contains control character %r at offset %d — the file would "
            "not be well-formed XML." % (control.group(0), control.start())
        )

    allowed_elements = frozenset(ALLOWED_ELEMENTS)
    allowed_attributes = frozenset(ALLOWED_ATTRIBUTES)
    forbidden_elements = frozenset(FORBIDDEN_ELEMENTS)

    cursor = 0
    while True:
        index = svg.find("<", cursor)
        if index == -1:
            break
        if svg.startswith("<!--", index):
            end = svg.find("-->", index)
            if end == -1:
                raise SvgSanitizeError("unterminated comment at offset %d" % index)
            # A comment must not smuggle a tag out of a naive consumer's parser.
            if "--" in svg[index + 4 : end]:
                raise SvgSanitizeError(
                    "comment at offset %d contains '--', which is not legal XML" % index
                )
            cursor = end + 3
            continue

        match = _TAG.match(svg, index)
        if match is None:
            raise SvgSanitizeError(
                "SVG output contains a '<' at offset %d that does not open a well-formed "
                "tag. Every literal '<' in text must go through escape_text()." % index
            )
        name = match.group(2).lower()
        if name in forbidden_elements:
            raise SvgSanitizeError(
                "SVG output contains a <%s> element at offset %d. §13 forbids it — this "
                "document is served to a client's browser through a share link."
                % (name, index)
            )
        if name not in allowed_elements:
            raise SvgSanitizeError(
                "SVG output contains an element <%s> at offset %d that is not on the "
                "allowlist (%s). Adding an element to a submission drawing is a "
                "deliberate act — put it in ALLOWED_ELEMENTS with a reason."
                % (name, index, ", ".join(ALLOWED_ELEMENTS))
            )

        for attribute in _ATTRIBUTE.finditer(match.group(3)):
            attribute_name = attribute.group(1)
            value = (
                attribute.group(2)
                if attribute.group(2) is not None
                else attribute.group(3)
                if attribute.group(3) is not None
                else attribute.group(4) or ""
            )
            lowered_name = attribute_name.lower()
            for prefix in FORBIDDEN_ATTRIBUTE_PREFIXES:
                if lowered_name.startswith(prefix) and lowered_name not in allowed_attributes:
                    raise SvgSanitizeError(
                        "SVG output contains the event-handler attribute %r on <%s> at "
                        "offset %d. §13 forbids script in exported drawings."
                        % (attribute_name, name, index)
                    )
            if attribute_name not in allowed_attributes:
                raise SvgSanitizeError(
                    "SVG output contains attribute %r on <%s> at offset %d, which is not "
                    "on the allowlist. href/xlink:href/style are excluded on purpose: "
                    "each can reference or execute remote content."
                    % (attribute_name, name, index)
                )
            lowered_value = value.lower().replace(" ", "")
            for scheme in FORBIDDEN_URI_SCHEMES:
                if scheme.replace(" ", "") in lowered_value:
                    raise SvgSanitizeError(
                        "attribute %r on <%s> at offset %d carries the URI scheme %r. "
                        "§13 forbids executable and embedded-document references."
                        % (attribute_name, name, index, scheme)
                    )
        cursor = match.end()
