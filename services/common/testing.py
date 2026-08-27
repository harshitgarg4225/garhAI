"""In-memory doubles so worker tests need no Redis, no network and no GPU.

:class:`FakeRedis` implements exactly the :class:`~services.common.queue.RedisLike`
surface — nothing more, so an accidental new command shows up as an ``AttributeError``
in a test rather than as a surprise in production. Semantics match real Redis where it
matters to this codebase (LTRIM/LRANGE negative indices, ZREM's "did I claim it"
return value, LREM counts, INCR on a missing key).

Deliberate simplifications, all safe for the behaviour under test:

* TTLs are recorded but nothing expires — no test asserts expiry, and a sleeping test
  is a slow test.
* ``blmove`` polls instead of blocking, so a timeout costs wall-clock only up to the
  requested value.
* No cluster, no transactions, no scripting.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

_POLL_SECONDS = 0.005


class FakeRedis:
    """Async in-memory Redis double."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        #: Every ``PUBLISH`` in order: ``(channel, message)``.
        self.published: list[tuple[str, str]] = []
        #: Every ``XADD`` in order, keyed by stream name.
        self.streams: dict[str, list[dict[str, str]]] = {}
        self.closed = False
        #: Set to raise from every command — used to test Redis-blip handling.
        self.fail_with: Exception | None = None

    # -- connection ------------------------------------------------------
    async def ping(self) -> Any:
        self._maybe_fail()
        return True

    async def aclose(self) -> None:
        self.closed = True

    # -- lists -----------------------------------------------------------
    async def lpush(self, name: str, *values: str) -> Any:
        self._maybe_fail()
        target = self.lists.setdefault(name, [])
        for value in values:
            target.insert(0, value)
        return len(target)

    async def rpush(self, name: str, *values: str) -> Any:
        self._maybe_fail()
        target = self.lists.setdefault(name, [])
        target.extend(values)
        return len(target)

    async def llen(self, name: str) -> Any:
        self._maybe_fail()
        return len(self.lists.get(name, []))

    async def lrem(self, name: str, count: int, value: str) -> Any:
        self._maybe_fail()
        target = self.lists.get(name)
        if not target:
            return 0
        removed = 0
        if count >= 0:
            limit = count or len(target)
            index = 0
            while index < len(target) and removed < limit:
                if target[index] == value:
                    del target[index]
                    removed += 1
                else:
                    index += 1
        else:
            index = len(target) - 1
            while index >= 0 and removed < -count:
                if target[index] == value:
                    del target[index]
                    removed += 1
                index -= 1
        return removed

    async def ltrim(self, name: str, start: int, end: int) -> Any:
        self._maybe_fail()
        target = self.lists.get(name)
        if target is None:
            return True
        size = len(target)
        lo = start + size if start < 0 else start
        hi = end + size if end < 0 else end
        lo = max(0, lo)
        hi = min(size - 1, hi)
        self.lists[name] = [] if lo > hi else target[lo : hi + 1]
        return True

    async def lrange(self, name: str, start: int, end: int) -> Any:
        self._maybe_fail()
        target = self.lists.get(name, [])
        size = len(target)
        lo = start + size if start < 0 else start
        hi = end + size if end < 0 else end
        lo = max(0, lo)
        hi = min(size - 1, hi)
        return [] if lo > hi else list(target[lo : hi + 1])

    # `timeout` mirrors redis-py's blmove signature (see RedisLike protocol).
    async def blmove(
        self,
        first_list: str,
        second_list: str,
        timeout: float,  # noqa: ASYNC109
        src: str,
        dest: str,
    ) -> Any:
        self._maybe_fail()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            source = self.lists.get(first_list)
            if source:
                value = source.pop() if src.upper() == "RIGHT" else source.pop(0)
                target = self.lists.setdefault(second_list, [])
                if dest.upper() == "LEFT":
                    target.insert(0, value)
                else:
                    target.append(value)
                return value
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(_POLL_SECONDS)

    # -- hashes ----------------------------------------------------------
    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: str | None = None,
        mapping: dict[str, str] | None = None,
    ) -> Any:
        self._maybe_fail()
        target = self.hashes.setdefault(name, {})
        added = 0
        if key is not None and value is not None:
            added += 0 if key in target else 1
            target[key] = value
        for map_key, map_value in (mapping or {}).items():
            added += 0 if map_key in target else 1
            target[map_key] = map_value
        return added

    async def hget(self, name: str, key: str) -> Any:
        self._maybe_fail()
        return self.hashes.get(name, {}).get(key)

    async def hdel(self, name: str, *keys: str) -> Any:
        self._maybe_fail()
        target = self.hashes.get(name, {})
        return sum(1 for key in keys if target.pop(key, None) is not None)

    async def hlen(self, name: str) -> Any:
        self._maybe_fail()
        return len(self.hashes.get(name, {}))

    # -- sorted sets ------------------------------------------------------
    async def zadd(self, name: str, mapping: dict[str, float]) -> Any:
        self._maybe_fail()
        target = self.zsets.setdefault(name, {})
        added = 0
        for member, score in mapping.items():
            if member not in target:
                added += 1
            target[member] = float(score)
        return added

    async def zrem(self, name: str, *values: str) -> Any:
        self._maybe_fail()
        target = self.zsets.get(name, {})
        return sum(1 for member in values if target.pop(member, None) is not None)

    async def zcard(self, name: str) -> Any:
        self._maybe_fail()
        return len(self.zsets.get(name, {}))

    async def zrangebyscore(
        self, name: str, min: float, max: float, start: int | None = None, num: int | None = None
    ) -> Any:
        self._maybe_fail()
        target = self.zsets.get(name, {})
        members = [member for member, score in target.items() if float(min) <= score <= float(max)]
        members.sort(key=lambda member: (target[member], member))
        offset = start or 0
        if num is None:
            return members[offset:]
        return members[offset : offset + num]

    # -- strings -----------------------------------------------------------
    async def set(self, name: str, value: str, ex: int | None = None, nx: bool = False) -> Any:
        self._maybe_fail()
        if nx and name in self.strings:
            return None
        self.strings[name] = value
        if ex is not None:
            self.ttls[name] = ex
        return True

    async def get(self, name: str) -> Any:
        self._maybe_fail()
        return self.strings.get(name)

    async def delete(self, *names: str) -> Any:
        self._maybe_fail()
        removed = 0
        for name in names:
            if name in self.strings:
                del self.strings[name]
            elif name in self.lists:
                del self.lists[name]
            elif name in self.hashes:
                del self.hashes[name]
            elif name in self.zsets:
                del self.zsets[name]
            else:
                continue
            self.ttls.pop(name, None)
            removed += 1
        return removed

    async def exists(self, *names: str) -> Any:
        self._maybe_fail()
        return sum(
            1
            for name in names
            if name in self.strings
            or name in self.lists
            or name in self.hashes
            or name in self.zsets
        )

    async def expire(self, name: str, time: int) -> Any:
        self._maybe_fail()
        self.ttls[name] = time
        return True

    async def incr(self, name: str) -> Any:
        self._maybe_fail()
        current = int(self.strings.get(name, "0"))
        current += 1
        self.strings[name] = str(current)
        return current

    # -- pub/sub + streams --------------------------------------------------
    async def publish(self, channel: str, message: str) -> Any:
        self._maybe_fail()
        self.published.append((channel, message))
        return 1

    async def xadd(
        self, name: str, fields: dict[str, str], maxlen: int | None = None, approximate: bool = True
    ) -> Any:
        self._maybe_fail()
        stream = self.streams.setdefault(name, [])
        stream.append(dict(fields))
        if maxlen is not None and len(stream) > maxlen:
            del stream[: len(stream) - maxlen]
        return "%d-0" % len(stream)

    # -- helpers for tests ---------------------------------------------------
    def events_on(self, channel: str) -> list[str]:
        return [message for name, message in self.published if name == channel]

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with


__all__ = ["FakeRedis"]
