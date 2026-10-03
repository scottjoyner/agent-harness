"""Broader acceptance for the ``assistx_answers_store_cursor_drops_ties`` fixture.

The store does considerably more than paginate. This tier pins the rest of it, so
that a repair to the cursor cannot quietly break indexing, status transitions,
TTL handling or event publishing.

The trap this catches: the obvious way to stop dropping tied members is to widen
the score -- for example by using a composite score, by adding a counter, or by
sorting on something lossy. Each of those breaks an invariant below, because the
index score is part of the store's observable state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_redis import load_module, purge  # noqa: E402

WORKTREE = Path(__file__).resolve().parents[1]
MODULE_PATH = WORKTREE / "assistx" / "answers_store.py"


@pytest.fixture()
def store():
    module, client = load_module(MODULE_PATH)
    try:
        yield module, client
    finally:
        previous = getattr(module, "_previous_modules", {})
        purge()
        sys.modules.update(previous)


# -- index invariants ----------------------------------------------------

def test_index_score_is_updated_at_in_milliseconds(store):
    """The score is observable state; a repair must not quietly change it."""
    module, client = store
    module.init_answer("a", "q")
    score = client.zsets[module.INDEX_ALL]["a"]
    assert float(score) == float(module._now_ms())


def test_new_answers_enter_the_global_index(store):
    module, client = store
    module.init_answer("a", "q")
    module.init_answer("b", "q")
    assert set(client.zsets[module.INDEX_ALL]) == {"a", "b"}


def test_status_moves_between_status_indexes_only(store):
    module, client = store
    module.init_answer("a", "q")
    assert "a" in client.zsets[module._index_key_for_status("QUEUED")]
    module.set_status("a", "RUNNING")
    assert "a" not in client.zsets[module._index_key_for_status("QUEUED")]
    assert "a" in client.zsets[module._index_key_for_status("RUNNING")]


def test_status_index_key_shape_is_unchanged(store):
    module, _client = store
    assert module._index_key_for_status("DONE") == "assistx:answers:index:status:DONE"
    assert module._index_key_for_status(None) == module.INDEX_ALL


def test_upsert_refreshes_the_score(store):
    module, client = store
    module.init_answer("a", "q")
    before = client.zsets[module.INDEX_ALL]["a"]
    module._index_upsert({"id": "a", "updated_at": before + 5000, "status": "DONE"})
    assert client.zsets[module.INDEX_ALL]["a"] == before + 5000


def test_index_remove_clears_every_status_index(store):
    module, client = store
    module.init_answer("a", "q")
    module._index_remove("a")
    assert "a" not in client.zsets[module.INDEX_ALL]
    for status in module.ALL_STATUSES:
        assert "a" not in client.zsets[module._index_key_for_status(status)]


def test_all_statuses_tuple_is_unchanged(store):
    module, _client = store
    assert module.ALL_STATUSES == ("QUEUED", "RUNNING", "DONE", "FAILED")


# -- CRUD behaviour ------------------------------------------------------

def test_init_answer_shape(store):
    module, _client = store
    module.init_answer("a", "why", {"u": 1})
    obj = module.get_answer("a")
    assert obj["id"] == "a"
    assert obj["question"] == "why"
    assert obj["status"] == "QUEUED"
    assert obj["meta"] == {"u": 1}
    assert obj["created_at"] == obj["updated_at"]


def test_set_result_marks_done_and_clears_error(store):
    module, _client = store
    module.init_answer("a", "q")
    module.set_error("a", "boom")
    assert module.get_answer("a")["status"] == "FAILED"
    module.set_result("a", {"answer": 42})
    obj = module.get_answer("a")
    assert obj["status"] == "DONE"
    assert obj["error"] is None
    assert obj["data"] == {"answer": 42}


def test_set_error_marks_failed(store):
    module, _client = store
    module.init_answer("a", "q")
    module.set_error("a", "boom")
    assert module.get_answer("a")["status"] == "FAILED"


def test_set_status_records_job_and_run(store):
    module, _client = store
    module.init_answer("a", "q")
    module.set_status("a", "RUNNING", job_id="j1", run_id="r1")
    obj = module.get_answer("a")
    assert (obj["job_id"], obj["run_id"]) == ("j1", "r1")


def test_upsert_rewrites_only_the_index_score(store):
    """_index_upsert touches the index, not the stored record.

    Worth pinning: a repair that made the index score diverge from the record
    would be a new defect, not a fix.
    """
    module, client = store
    module.init_answer("a", "q")
    before_score = client.zsets[module.INDEX_ALL]["a"]
    before_record = module.get_answer("a")["updated_at"]
    obj = module.get_answer("a")
    obj["updated_at"] = before_record + 1000
    module._index_upsert(obj)
    assert client.zsets[module.INDEX_ALL]["a"] == before_score + 1000
    assert module.get_answer("a")["updated_at"] == before_record


def test_writes_on_a_missing_answer_are_ignored(store):
    module, client = store
    module.set_status("ghost", "RUNNING")
    module.set_result("ghost", {})
    module.set_error("ghost", "x")
    assert module.INDEX_ALL not in client.zsets or "ghost" not in client.zsets[module.INDEX_ALL]


def test_get_answer_returns_none_for_a_missing_key(store):
    module, _client = store
    assert module.get_answer("nope") is None


def test_get_answer_tolerates_corrupt_json(store):
    module, client = store
    client.strings[module._key("bad")] = "{not json"
    assert module.get_answer("bad") is None


# -- TTL and keyspace ----------------------------------------------------

def test_keys_are_written_with_the_configured_ttl(store):
    module, client = store
    module.init_answer("a", "q")
    assert client.ttls[module._key("a")] == module.ANSWERS_TTL_S


def test_default_ttl_is_unchanged(store):
    module, _client = store
    assert module.ANSWERS_TTL_S == 86400


def test_key_and_channel_namespaces_are_unchanged(store):
    module, _client = store
    assert module._key("a") == "assistx:answers:a"
    assert module._chan("a") == "assistx:answers:a:events"
    assert module._global_chan() == "assistx:answers:events"
    assert module.INDEX_ALL == "assistx:answers:index:updated_at"


# -- events --------------------------------------------------------------

def test_new_answer_publishes_to_both_channels(store):
    module, client = store
    module.init_answer("a", "q")
    channels = [channel for channel, _ in client.published]
    assert module._chan("a") in channels
    assert module.GLOBAL_CHAN in channels


def test_update_publishes_an_update_event(store):
    module, client = store
    module.init_answer("a", "q")
    client.published.clear()
    module.set_status("a", "RUNNING")
    types = [
        json.loads(payload)["type"]
        for _channel, payload in client.published
    ]
    assert "update" in types


def test_publish_failures_do_not_break_the_write_path(store):
    """A broken pubsub must not stop an answer being written."""
    module, client = store

    def boom(*args, **kwargs):
        raise RuntimeError("pubsub down")

    client.publish = boom
    module.init_answer("a", "q")
    assert module.get_answer("a") is not None


def test_publish_event_accepts_a_stale_id(store):
    module, client = store
    module.init_answer("a", "q")
    client.published.clear()
    module.publish_event("a", "note", {"extra": 1})
    assert client.published


def test_publish_event_ignores_unknown_kwargs(store):
    module, client = store
    module.init_answer("a", "q")
    client.published.clear()
    module._publish({"id": "a"}, ev_type="update", legacy_field=1)
    assert client.published


# -- misc ----------------------------------------------------------------

def test_cursor_parser_rejects_garbage(store):
    module, _client = store
    assert module._parse_cursor(None) is None
    assert module._parse_cursor("") is None
    assert module._parse_cursor("no-colon") is None
    assert module._parse_cursor("abc:def") is None


def test_cursor_parser_keeps_an_id_containing_colons(store):
    module, _client = store
    assert module._parse_cursor("100:a:b") == (100.0, "a:b")


def test_redis_url_default_is_unchanged(store):
    module, _client = store
    assert module.REDIS_URL == "redis://localhost:6379/0"


def test_new_answer_ids_are_hex_uuids(store):
    module, _client = store
    first, second = module.new_answer_id(), module.new_answer_id()
    assert first != second
    assert len(first) == 32
    int(first, 16)


def test_unknown_status_filter_reads_the_global_index(store):
    """An unrecognised status falls back to the global index and does not crash.

    The call site coerces an unknown status to None when choosing the index; the
    per-item guard then rejects everything. That is the module's existing
    behaviour and is not what this fixture is about.
    """
    module, client = store
    module.init_answer("a", "q")
    assert module._index_key_for_status(None) == module.INDEX_ALL
    page = module.list_answers_paginated(status="NOT_A_STATUS", limit=10)
    assert page["items"] == []
    assert page["next_cursor"] is None
    assert "a" in client.zsets[module.INDEX_ALL]


def test_limit_larger_than_the_index_returns_everything(store):
    module, client = store
    module.init_answer("a", "q")
    module.init_answer("b", "q")
    page = module.list_answers_paginated(limit=100)
    assert {item["id"] for item in page["items"]} == {"a", "b"}


def test_rebuild_index_is_present(store):
    module, _client = store
    assert callable(module.rebuild_index)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
