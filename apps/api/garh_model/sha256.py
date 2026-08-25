"""sha256.py — mirror of ``packages/model/src/sha256.ts``.

The TypeScript side hand-rolls FIPS 180-4 SHA-256 because ``stateHash()`` must be
synchronous and identical in Node and in the browser bundle (``node:crypto``
would break the Vite build, Web Crypto's ``digest`` is async). Python has no such
problem, so this module is a thin, exactly-equivalent wrapper over
:mod:`hashlib` — same function names, same lowercase-hex output, same UTF-8
encoding rules.

The one behaviour worth spelling out is lone-surrogate handling. ``utf8Bytes`` in
TypeScript replaces an unpaired surrogate with U+FFFD (mirroring Python's
``errors='replace'`` intent). Python's ``str.encode('utf-8', 'replace')`` does
NOT do that — it emits ``b'?'`` (0x3F) — so the substitution is done explicitly
here. In practice ``canonical_json`` rejects lone surrogates long before a string
reaches this module; this is belt-and-braces so the two implementations cannot
diverge even on malformed input.
"""

from __future__ import annotations

import hashlib

__all__ = ["sha256_bytes", "sha256_utf8", "utf8_bytes"]

_REPLACEMENT = "�"


def utf8_bytes(s: str) -> bytes:
    """UTF-8 encode ``s``, substituting U+FFFD for any unpaired surrogate.

    Mirrors ``utf8Bytes`` in ``sha256.ts``: a paired surrogate sequence is
    encoded as the astral code point it denotes (Python strings already hold
    astral characters as single code points, so pairing is implicit), and a lone
    surrogate becomes U+FFFD rather than raising.
    """
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in s):
        s = "".join(_REPLACEMENT if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in s)
    return s.encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """SHA-256 of raw bytes as 64 lowercase hex characters."""
    return hashlib.sha256(data).hexdigest()


def sha256_utf8(s: str) -> str:
    """SHA-256 of a UTF-8 string as 64 lowercase hex characters."""
    return sha256_bytes(utf8_bytes(s))
