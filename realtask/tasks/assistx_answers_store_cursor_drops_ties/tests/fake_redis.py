"""A minimal in-memory stand-in for Redis, sufficient for answers_store.

Only the surface ``answers_store`` actually uses is implemented:

* ``pipeline(transaction=True)`` with ``zadd`` / ``zrem`` / ``execute``
* ``setex`` / ``get``
* ``publish``
* ``zrevrangebyscore(key, max, min, start=, num=, withscores=)`` including the
  leading-``(`` exclusive-bound syntax Redis uses.

Sorted-set members are ordered by score descending, then by member descending,
matching Redis. No sockets, no files, no network.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

_EXCLUSIVE = re.compile(r"^\((.*)$")


def _parse_bound(raw: str) -> Tuple[float, bool]:
    match = _EXCLUSIVE.match(raw)
    if match:
        return float(match.group(1)), True
    if raw in ("+inf", "inf"):
        return float("inf"), False
    if raw in ("-inf", "-inf"):
        return float("-inf"), False
    return float(raw), False


class FakePipeline:
    def __init__(self, client: "FakeRedis"):
        self._client = client
        self._queued: List[Tuple[str, str, Any]] = []

    def zadd(self, key: str, mapping: Dict[str, float]) -> None:
        for member, score in mapping.items():
            self._queued.append(("zadd", key, (member, float(score))))

    def zrem(self, key: str, member: str) -> None:
        self._queued.append(("zrem", key, member))

    def execute(self) -> None:
        for op, key, payload in self._queued:
            if op == "zadd":
                member, score = payload
                self._client.zadd(key, {member: score})
            else:
                self._client.zrem(key, payload)
        self._queued = []


class FakeRedis:
    """In-memory Redis covering exactly what answers_store needs."""

    def __init__(self):
        self.zsets: Dict[str, Dict[str, float]] = {}
        self.strings: Dict[str, str] = {}
        self.ttls: Dict[str, int] = {}
        self.published: List[Tuple[str, str]] = []

    # -- sorted sets -------------------------------------------------------

    def zadd(self, key: str, mapping: Dict[str, float]) -> None:
        self.zsets.setdefault(key, {}).update(
            {member: float(score) for member, score in mapping.items()}
        )

    def zrem(self, key: str, member: str) -> None:
        self.zsets.setdefault(key, {}).pop(member, None)

    def zrevrangebyscore(
        self,
        key: str,
        max_: str,
        min_: str,
        start: int = 0,
        num: int | None = None,
        withscores: bool = False,
    ):
        """Newest-first slice, honouring Redis' exclusive ``(`` bound syntax."""
        members = self.zsets.get(key, {})
        hi, hi_excl = _parse_bound(max_)
        lo, lo_excl = _parse_bound(min_)
        ordered = sorted(
            members.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
        )
        selected = []
        for member, score in ordered:
            if hi_excl and score >= hi:
                continue
            if not hi_excl and score > hi:
                continue
            if lo_excl and score <= lo:
                continue
            if not lo_excl and score < lo:
                continue
            selected.append((member, score))
        selected = selected[start:] if num is None else selected[start : start + num]
        if withscores:
            return selected
        return [member for member, _ in selected]

    # -- strings -----------------------------------------------------------

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.strings[key] = value
        self.ttls[key] = ttl

    def get(self, key: str):
        return self.strings.get(key)

    # -- pubsub ------------------------------------------------------------

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1

    # -- pipeline ----------------------------------------------------------

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


class FakeRedisModule:
    """Stands in for the ``redis`` module returned by ``load_redis_module``."""

    def __init__(self, client: FakeRedis):
        self.client = client

    def from_url(self, url: str, decode_responses: bool = True) -> FakeRedis:
        self.last_url = url
        self.last_decode_responses = decode_responses
        return self.client


def load_module(answers_store_path):
    """Import ``answers_store`` from the worktree with ``.deps`` stubbed out.

    The module does ``from .deps import load_redis_module`` at import time and
    calls the result immediately, so ``assistx`` and ``assistx.deps`` have to be
    in ``sys.modules`` *before* the module body executes. Callers are expected to
    remove every ``assistx*`` entry afterwards; :func:`purge` does that.
    """
    import importlib.util
    import sys
    import types

    client = FakeRedis()
    package = types.ModuleType("assistx")
    package.__path__ = []
    deps = types.ModuleType("assistx.deps")
    deps.load_redis_module = lambda: FakeRedisModule(client)
    package.deps = deps

    previous = purge()
    sys.modules["assistx"] = package
    sys.modules["assistx.deps"] = deps

    spec = importlib.util.spec_from_file_location(
        "assistx.answers_store", str(answers_store_path)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["assistx.answers_store"] = module
    spec.loader.exec_module(module)
    module._previous_modules = previous
    return module, client


def purge():
    """Drop any previously loaded ``assistx`` modules; returns what was removed."""
    import sys

    removed = {name: sys.modules.pop(name) for name in list(sys.modules)
               if name == "assistx" or name.startswith("assistx.")}
    return removed


def seed(module, client, ids_with_updated_at):
    """Write one answer per id with an explicit ``updated_at`` score.

    ``init_answer`` stamps ``_now_ms()``, which cannot produce deliberate ties,
    so the fixtures are seeded through the same index path the module uses.
    """
    for answer_id, updated_at in ids_with_updated_at:
        obj = {
            "id": answer_id,
            "question": "question for {}".format(answer_id),
            "status": "DONE",
            "created_at": updated_at,
            "updated_at": updated_at,
            "job_id": None,
            "run_id": None,
            "data": None,
            "error": None,
            "meta": {},
        }
        client.setex(module._key(answer_id), module.ANSWERS_TTL_S, json.dumps(obj))
        module._index_upsert(obj)
    return [answer_id for answer_id, _ in ids_with_updated_at]


def drain_pages(module, limit: int = 2, status=None, q=None, max_pages: int = 20):
    """Paginate to exhaustion, returning the ids seen and the cursors used."""
    seen: List[str] = []
    cursors: List[str | None] = [None]
    cursor = None
    pages: List[List[str]] = []
    for _ in range(max_pages):
        page = module.list_answers_paginated(
            status=status, q=q, limit=limit, cursor=cursor
        )
        ids = [item["id"] for item in page["items"]]
        pages.append(ids)
        seen.extend(ids)
        cursor = page["next_cursor"]
        if not cursor:
            break
    return seen, pages, cursors
