"""Oracle for the ``small_refactor`` fixture.

The deliverable is a *behaviour-preserving* refactor of
``auto_router/request_idempotency.py``: the connection-lifetime shape is
repeated in four methods and should be owned in one place.

This check is written so that neither half can be faked:

1. **structure** -- the connection is acquired and released in exactly one
   place, not four;
2. **behaviour** -- the ledger still behaves as it did, including the part of
   the current code that looks redundant but is load-bearing: the shared
   in-memory connection must survive across calls.

Point 2 is the whole reason this is not a style check. A refactor that
"simplifies" ``finally: if not self.in_memory: conn.close()`` into
``finally: conn.close()`` removes four lines, satisfies every structural
assertion here, and breaks the in-memory ledger outright -- after the first
call, every subsequent call raises ``sqlite3.ProgrammingError``. That version
is rejected, and that is the point.

No network, no credentials: the ledger is SQLite, in memory.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub(name: str, **attrs) -> None:
    """Install a stand-in for a third-party module.

    The ledger itself is pure SQLite, but the two frozen files import ``fastapi``
    and ``pydantic_settings`` at module scope. Those are stubbed rather than
    imported for real so that this oracle's verdict is a property of the
    candidate's patch rather than of whichever packages happen to be installed
    on the machine running it. See ``UndeclaredDependencyTests``, which exists
    because that dependency was once silently host-dependent.
    """
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    parent_name, _, leaf = name.rpartition(".")
    if parent_name:
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, leaf, module)


_stub("fastapi")
_stub("fastapi.responses", JSONResponse=object)
_stub("pydantic_settings", BaseSettings=object, SettingsConfigDict=dict)


from auto_router.request_idempotency import (  # noqa: E402
    RequestIdempotencyLedger,
    RequestIdempotencyMiddleware,
    _database_path,
)

MODULE = ROOT / "auto_router" / "request_idempotency.py"

#: How many times the connection lifetime may be spelled out. The refactor's
#: whole purpose is that this drops to one.
MAX_CONNECTION_SITES = 1

#: The close guard specifically, not merely any ``if not self.in_memory``.
#: ``__init__`` also branches on ``in_memory`` -- to create the parent directory
#: -- and that branch has nothing to do with closing a connection, so counting
#: the bare condition would forbid a perfectly reasonable second use.
GUARDED_CLOSE = re.compile(
    r"if not self\.in_memory:[^\n]*\n\s*conn\.close\(\)"
)


def _occurrences(needle: str) -> int:
    return MODULE.read_text(encoding="utf-8").count(needle)


def _guarded_close_sites() -> int:
    return len(GUARDED_CLOSE.findall(MODULE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------- structure


def test_connection_is_acquired_in_one_place():
    sites = _occurrences("self._connect()")
    assert sites <= MAX_CONNECTION_SITES, (
        "the ledger still acquires a connection in {} places; the refactor's "
        "purpose is to own it in one".format(sites)
    )


def test_connection_is_closed_in_one_place():
    sites = _occurrences("conn.close()")
    assert sites <= MAX_CONNECTION_SITES, (
        "the ledger still closes a connection in {} places".format(sites)
    )


def test_in_memory_close_guard_survives_exactly_once():
    """The guard is the behaviour, not noise. It must exist, exactly once."""
    sites = _guarded_close_sites()
    assert sites == 1, (
        "the in-memory close guard appears {} times; it must appear exactly once, "
        "in the one place that owns the connection".format(sites)
    )


# ---------------------------------------------------------------- behaviour


def test_public_surface_is_unchanged():
    assert callable(RequestIdempotencyMiddleware)
    ledger = RequestIdempotencyLedger("sqlite:///:memory:")
    assert ledger.in_memory is True
    for name in ("reserve", "get", "transition"):
        assert callable(getattr(ledger, name)), name


def test_database_url_parsing_is_preserved_verbatim():
    """Pins the current parsing, quirks included.

    ``sqlite:///ledger.sqlite3`` keeps its leading slash and resolves to the
    *absolute* path ``/ledger.sqlite3``. That looks wrong, and a refactor that
    moved url parsing next to the connection it feeds would be tempted to
    normalise it. Normalising it changes where the ledger is written, so it is
    a behaviour change and not a behaviour-preserving refactor. The separate
    defect it represents is out of scope for this task.
    """
    assert _database_path("sqlite:///:memory:") == Path(":memory:")
    assert _database_path("sqlite:///ledger.sqlite3") == Path("/ledger.sqlite3")
    assert _database_path("sqlite://router.sqlite3") == Path("router.sqlite3")
    with pytest.raises(RuntimeError):
        _database_path("postgres://host/db")


def test_reserve_then_replay_is_rejected():
    ledger = RequestIdempotencyLedger("sqlite:///:memory:")
    accepted, record = ledger.reserve("k1", "fp-a", "req-1")
    assert accepted is True
    assert record["state"] == "in_progress"
    assert record["status_code"] is None

    replayed, stored = ledger.reserve("k1", "fp-a", "req-1")
    assert replayed is False
    assert stored["idempotency_key"] == "k1"


def test_transition_records_terminal_state():
    ledger = RequestIdempotencyLedger("sqlite:///:memory:")
    ledger.reserve("k1", "fp-a", "req-1")
    ledger.transition("k1", "completed", status_code=200, detail="done")
    record = ledger.get("k1")
    assert record is not None
    assert record["state"] == "completed"
    assert record["status_code"] == 200
    assert record["detail"] == "done"


def test_rejected_transitions_change_nothing():
    ledger = RequestIdempotencyLedger("sqlite:///:memory:")
    ledger.reserve("k1", "fp-a", "req-1")
    ledger.transition("k1", "completed", status_code=200, detail="done")

    # completed is terminal: it may not move to failed.
    ledger.transition("k1", "failed", detail="late failure")
    record = ledger.get("k1")
    assert record is not None
    assert record["state"] == "completed"
    assert record["detail"] == "done"

    # a key that was never reserved is a silent no-op
    ledger.transition("absent", "failed")
    assert ledger.get("absent") is None


def test_unsupported_transition_state_is_rejected():
    ledger = RequestIdempotencyLedger("sqlite:///:memory:")
    ledger.reserve("k1", "fp-a", "req-1")
    with pytest.raises(ValueError):
        ledger.transition("k1", "not_a_state")


# ------------------------------------------- the load-bearing part, directly


def test_shared_in_memory_connection_survives_many_operations():
    """The regression this refactor can silently introduce.

    ``_connect`` returns one shared connection for ``:memory:``. If a refactor
    drops the ``if not self.in_memory`` guard, that connection is closed after
    the first operation and every later one raises ProgrammingError -- while
    every structural assertion above still passes.
    """
    ledger = RequestIdempotencyLedger("sqlite:///:memory:")
    for index in range(12):
        accepted, _record = ledger.reserve("k{}".format(index), "fp", "req-{}".format(index))
        assert accepted is True, index
        assert ledger.get("k{}".format(index)) is not None, index
        ledger.transition("k{}".format(index), "completed", status_code=200)
        assert ledger.get("k{}".format(index))["state"] == "completed", index


def test_rejected_transition_does_not_poison_the_ledger():
    """A rollback on an early return must leave the connection usable."""
    ledger = RequestIdempotencyLedger("sqlite:///:memory:")
    ledger.reserve("k1", "fp-a", "req-1")
    ledger.transition("k1", "completed", status_code=200)

    for _ in range(5):
        ledger.transition("k1", "cancelled", detail="ignored")

    accepted, _record = ledger.reserve("k2", "fp-b", "req-2")
    assert accepted is True
    assert ledger.get("k1")["state"] == "completed"


def test_schema_is_created_once_and_is_reusable(tmp_path):
    """A file-backed ledger writes to disk; a second instance must reuse it."""
    url = "sqlite:///" + str(tmp_path / "ledger.sqlite3")
    first = RequestIdempotencyLedger(url)
    assert first.reserve("k1", "fp", "req-1")[0] is True
    first.transition("k1", "completed", status_code=204)

    second = RequestIdempotencyLedger(url)
    assert second.in_memory is False
    assert second.reserve("k1", "fp", "req-1")[0] is False
    assert second.get("k1")["status_code"] == 204