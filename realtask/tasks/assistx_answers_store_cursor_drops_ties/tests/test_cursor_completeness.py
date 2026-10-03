"""Targeted acceptance for the ``assistx_answers_store_cursor_drops_ties`` fixture.

The index is a sorted set scored by ``updated_at`` in integer milliseconds.
Nothing makes that score unique: bulk enqueues, a ``set_status`` immediately
followed by ``set_result``, and SSE bursts all land several answers on the same
millisecond.

``list_answers_paginated`` documents its cursor as the composite
``'<score>:<id>'`` and then discards the id, advancing an *exclusive score*
bound instead. The moment a page boundary falls inside a tie group, the rest of
that group is skipped -- permanently, because no cursor a client can construct
will re-enter it. Answers exist in the store and never appear in any page.

The invariant: paginating to exhaustion must return every indexed answer, for
any page size and any distribution of tied scores.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_redis import drain_pages, load_module, purge, seed  # noqa: E402

WORKTREE = Path(__file__).resolve().parents[1]
MODULE_PATH = WORKTREE / "assistx" / "answers_store.py"

#: Three answers share the millisecond 2000; 'e' is the casualty the frozen
#: revision loses.
TIED = [("a", 5000), ("b", 4000), ("c", 2000), ("d", 2000), ("e", 2000)]

ALL_TIED = [("a", 3000), ("b", 3000), ("c", 3000), ("d", 3000), ("e", 3000)]

WIDE_TIE = (
    [("a", 9000)]
    + [("t{}".format(i), 5000) for i in range(12)]
    + [("z", 1000)]
)


@pytest.fixture()
def store():
    """A freshly loaded module over an empty in-memory Redis, per test."""
    module, client = load_module(MODULE_PATH)
    try:
        yield module, client
    finally:
        previous = getattr(module, "_previous_modules", {})
        purge()
        sys.modules.update(previous)


def test_module_loads(store):
    module, _client = store
    assert hasattr(module, "list_answers_paginated")
    assert hasattr(module, "_parse_cursor")


def test_cursor_format_is_the_composite_it_documents(store):
    module, _client = store
    assert module._parse_cursor("4000:abc") == (4000.0, "abc")


def test_pagination_returns_everything_when_there_are_no_ties(store):
    module, client = store
    seed(module, client, [("a", 5000), ("b", 4000), ("c", 3000)])
    seen, _pages, _cursors = drain_pages(module, limit=2)
    assert sorted(seen) == ["a", "b", "c"]


@pytest.mark.parametrize("limit", [1, 2, 3, 5, 7, 11, 100])
def test_ties_are_never_dropped_for_any_page_size(store, limit):
    """The defect: a boundary inside a tie group loses the rest of the group.

    The page sizes here are chosen to be useless as a lookup table. 1, 2, 3, 5,
    7 and 11 appear nowhere else in the fixture and 100 is the size the broader
    tier reaches for, so a repair that behaves correctly only for the sizes it
    was written against is caught here rather than in review.
    """
    module, client = store
    seeded = seed(module, client, TIED)
    seen, _pages, _cursors = drain_pages(module, limit=limit)
    missing = sorted(set(seeded) - set(seen))
    assert not missing, (
        "limit={} dropped {} entirely; paginating returned {} of {}".format(
            limit, missing, len(seen), len(seeded)
        )
    )


def test_a_single_tie_group_is_walked_completely(store):
    module, client = store
    seeded = seed(module, client, ALL_TIED)
    seen, _pages, _cursors = drain_pages(module, limit=2)
    assert sorted(seen) == sorted(seeded)
    assert len(seen) == len(set(seen)), "a page repeated a member"


def test_a_wide_tie_group_survives_a_small_page(store):
    module, client = store
    seeded = seed(module, client, WIDE_TIE)
    seen, _pages, _cursors = drain_pages(module, limit=2, max_pages=40)
    missing = sorted(set(seeded) - set(seen))
    assert not missing, "dropped {}".format(missing)


def test_pagination_terminates(store):
    """Terminating matters as much as completeness: no infinite cursor loop."""
    module, client = store
    seed(module, client, TIED)
    seen, pages, _cursors = drain_pages(module, limit=2, max_pages=15)
    assert len(pages) <= 15
    assert seen


def test_newest_first_ordering_is_preserved_within_a_page(store):
    module, client = store
    seed(module, client, [("a", 5000), ("b", 4000), ("c", 3000)])
    page = module.list_answers_paginated(limit=3)
    assert [item["id"] for item in page["items"]] == ["a", "b", "c"]


def test_status_filter_still_paginates_completely(store):
    module, client = store
    seed(module, client, [("a", 5000), ("b", 2000), ("c", 2000), ("d", 2000)])
    seen, _pages, _cursors = drain_pages(module, limit=1, status="DONE")
    assert sorted(seen) == ["a", "b", "c", "d"]


def test_text_filter_still_paginates_completely(store):
    module, client = store
    seed(module, client, [("a", 5000), ("b", 2000), ("c", 2000), ("d", 2000)])
    seen, _pages, _cursors = drain_pages(module, limit=1, q="question")
    assert sorted(seen) == ["a", "b", "c", "d"]


def test_stale_index_entries_do_not_break_completion(store):
    """A missing record is skipped and its index entry cleaned up, not fatal."""
    module, client = store
    seeded = seed(module, client, [("a", 5000), ("b", 2000), ("c", 2000), ("d", 2000)])
    client.strings.pop(module._key("b"))
    seen, _pages, _cursors = drain_pages(module, limit=2)
    assert "b" not in seen
    assert sorted(seen) == sorted(set(seeded) - {"b"})


def test_empty_index_returns_an_empty_page_and_no_cursor(store):
    module, _client = store
    page = module.list_answers_paginated(limit=10)
    assert page["items"] == []
    assert page["next_cursor"] is None
    assert page["count"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
