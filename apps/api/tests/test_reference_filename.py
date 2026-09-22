"""A pinned picture keeps the name its client gave it.

``ReferenceRepository.add`` has always preferred the uploaded file's name over
"Reference 3" — its own comment says "the filename is a better fallback". But the
route only read a name out of a MULTIPART body, and the web app posts the raw image
bytes. So the name had nowhere to travel: the fallback was unreachable, every board
in the product read "Reference 1 ... Reference 8", and the pre-render review asked
"What should Reference 6 contribute?" about a photo nobody could identify. Found by
uploading three named pictures in a browser and reading the board back.

A name is NOT an inference, and this file is careful about the difference. The
product still reads nothing out of the picture or out of its filename: no scope, no
intent, no "you probably meant the cabinets". The label never reaches a provider
either — ``build_prompt`` is assembled from ``why`` and ``ignore`` alone. It is the
one string the architect already chose, shown back in an editable field.

Which makes the header a piece of untrusted text that gets stored and displayed, so
most of what follows is about what must NOT survive it: a path, a control character,
an undecodable byte sequence, or a name long enough to break the card.
"""

from __future__ import annotations

import struct
import urllib.parse
import zlib

import pytest

pytestmark = pytest.mark.integration


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _make_png(width: int = 64, height: int = 48) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def _url(api: str, project_id, suffix: str = "") -> str:
    return "%s/projects/%s/references%s" % (api, project_id, suffix)


async def _pin_named(client, api, firm, project_id, name: str | None) -> dict:
    """Upload one PNG, optionally carrying the header the web app sends."""
    headers = {**firm.headers, "content-type": "image/png"}
    if name is not None:
        headers["x-garh-filename"] = urllib.parse.quote(name, safe="")
    response = await client.post(_url(api, project_id), headers=headers, content=_make_png())
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_card_is_named_after_the_file(client, api, firm_a, project_a, clean_redis):
    pinned = await _pin_named(client, api, firm_a, project_a.id, "kitchen-tiles.jpg")
    assert pinned["label"] == "kitchen-tiles.jpg", (
        "the board fell back to a counter. Eight photos from one client then read "
        "'Reference 1' through 'Reference 8', and so does every review question."
    )


async def test_a_name_that_is_not_ascii_arrives_intact(client, api, firm_a, project_a, clean_redis):
    """A client in Bengaluru names the file in Kannada; nothing about that is exotic."""
    pinned = await _pin_named(client, api, firm_a, project_a.id, "ಅಡುಗೆಮನೆ.jpg")
    assert pinned["label"] == "ಅಡುಗೆಮನೆ.jpg", pinned["label"]


async def test_NEGATIVE_CONTROL_no_header_still_gets_the_counter(
    client, api, firm_a, project_a, clean_redis
):
    """A pasted image has no filename, and inventing one would be worse than a counter.

    This is the control that keeps the test above honest: without it, a change that
    hard-coded some label would pass the first test and nobody would notice that the
    nameless case had stopped working.
    """
    pinned = await _pin_named(client, api, firm_a, project_a.id, None)
    assert pinned["label"] == "Reference 1", pinned["label"]
    second = await _pin_named(client, api, firm_a, project_a.id, None)
    assert second["label"] == "Reference 2", second["label"]


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("../../etc/passwd", "passwd"),
        ("/var/lib/secrets.png", "secrets.png"),
        ("C:\\Users\\asha\\kitchen.jpg", "kitchen.jpg"),
    ],
)
async def test_a_path_never_survives_only_a_name(
    client, api, firm_a, project_a, clean_redis, sent, stored
):
    """The header is reachable from any script, not only from a file picker.

    Nothing downstream opens this string as a path — it is a label — but a value that
    still LOOKS like a path is one somebody will eventually join onto a directory.
    """
    pinned = await _pin_named(client, api, firm_a, project_a.id, sent)
    assert pinned["label"] == stored, pinned["label"]


async def test_control_characters_are_stripped(client, api, firm_a, project_a, clean_redis):
    pinned = await _pin_named(client, api, firm_a, project_a.id, "kit\x00chen\x07.jpg")
    assert pinned["label"] == "kitchen.jpg", pinned["label"]


async def test_an_undecodable_header_is_dropped_not_guessed_at(
    client, api, firm_a, project_a, clean_redis
):
    """Percent-escapes that are not valid UTF-8 mean the client is not ours.

    Falling back to `errors="replace"` would put U+FFFD on the card; falling back to
    latin-1 would put mojibake there. Neither is a name, so there is no name.
    """
    response = await client.post(
        _url(api, project_a.id),
        headers={**firm_a.headers, "content-type": "image/png", "x-garh-filename": "%ff%fe"},
        content=_make_png(),
    )
    assert response.status_code == 201, response.text
    assert response.json()["label"] == "Reference 1"


async def test_a_very_long_name_is_capped(client, api, firm_a, project_a, clean_redis):
    pinned = await _pin_named(client, api, firm_a, project_a.id, "x" * 400 + ".jpg")
    assert len(pinned["label"]) <= 120, len(pinned["label"])


async def test_the_architect_can_still_rename_it(client, api, firm_a, project_a, clean_redis):
    """The filename is a starting point, not a decision. It stays editable."""
    pinned = await _pin_named(client, api, firm_a, project_a.id, "IMG_20260921_114233.jpg")
    patched = await client.patch(
        _url(api, project_a.id, "/%s" % pinned["id"]),
        headers=firm_a.headers,
        json={"label": "Client's kitchen"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["label"] == "Client's kitchen"
