"""Bounded acceptance tests for the auto-ingest shorts plan driver lifetime.

These tests are the executable definition of the bug this fixture freezes:

    planner.plan_shorts(..., driver=driver) was executed after driver.close()

They import the real ``auto_ingest/shorts/cli.py`` from the evaluation
worktree, with the heavy sibling modules stubbed, and assert the *lifecycle
contract*: graph reads and planning all happen while the driver is open, and
the driver is still closed exactly once on every exit path.

They are deliberately bounded. No Neo4j, no network, no media, no credentials.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auto_ingest.shorts as shorts_pkg  # noqa: E402


EVENTS: list = []


class FakeDriver:
    """Stand-in for a neo4j driver that refuses use after close()."""

    def __init__(self, events):
        self.events = events
        self.closed = False
        self.close_calls = 0

    def session(self):
        if self.closed:
            raise RuntimeError("Neo4j session requested on a closed driver")
        return types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(data=lambda: [])
        )

    def close(self):
        self.close_calls += 1
        self.closed = True
        self.events.append(("close", True))


class FakePlan:
    plan_id = "plan0123456789"
    shorts = []
    iteration = 0
    topic = "fixture_topic"

    def save(self, path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"fixture": true}', encoding="utf-8")


class FakeBrief:
    topic = "fixture_topic"
    title = "Fixture Topic"
    hook = "a hook"
    points = ["point one", "point two"]
    sources = []


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    parent_name, _, leaf = name.rpartition(".")
    if parent_name:
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, leaf, module)
    return module


def curate_brief(driver, topic, **kwargs):
    EVENTS.append(("curate_brief", driver.closed))
    if driver.closed:
        raise RuntimeError("curate_brief received a closed driver")
    return FakeBrief()


def select_highway_pool(driver, **kwargs):
    EVENTS.append(("select_highway_pool", driver.closed))
    if driver.closed:
        raise RuntimeError("select_highway_pool received a closed driver")
    return []


def discusses_topic(driver, topic, **kwargs):
    EVENTS.append(("discusses_topic", driver.closed))
    if driver.closed:
        raise RuntimeError("discusses_topic received a closed driver")
    return []


def plan_shorts(brief, anchors, **kwargs):
    driver = kwargs.get("driver")
    was_closed = bool(driver is not None and driver.closed)
    EVENTS.append(("plan_shorts", was_closed))
    if was_closed:
        raise RuntimeError(
            "plan_shorts received a closed driver; graph mining would silently "
            "fall back to templated text"
        )
    if driver is None:
        raise AssertionError(
            "plan_shorts was called without a driver; the S-G10 graph-mining "
            "contract is not satisfied"
        )
    return FakePlan()


def plan_discussion(driver, clips, **kwargs):
    EVENTS.append(("plan_discussion", driver.closed))
    if driver.closed:
        raise RuntimeError("plan_discussion received a closed driver")
    return FakePlan()


_stub("auto_ingest.shorts.backdrop", select_highway_pool=select_highway_pool)
_stub("auto_ingest.shorts.curator", curate_brief=curate_brief,
      discusses_topic=discusses_topic)
_stub("auto_ingest.shorts.planner", plan_shorts=plan_shorts,
      plan_discussion=plan_discussion)
_stub("auto_ingest.shorts.publish")
_stub("auto_ingest.shorts.render")
_stub("auto_ingest.shorts.models", Plan=FakePlan, Brief=FakeBrief)
_stub("auto_ingest_config", get_neo4j_password=lambda: "unused-in-fixture")

from auto_ingest.shorts import cli as shorts_cli  # noqa: E402

shorts_pkg.cli = shorts_cli


def _plan_args(tmp_path, *extra):
    return shorts_cli.build_parser().parse_args(
        ["plan", "fixture_topic", "--plans-dir", str(tmp_path), *extra]
    )


def _install_driver():
    driver = FakeDriver(EVENTS)
    shorts_cli._driver = lambda: (driver, "neo4j")
    return driver


def _run(tmp_path, *extra):
    EVENTS.clear()
    driver = _install_driver()
    rc = shorts_cli._cmd_plan(_plan_args(tmp_path, *extra))
    return rc, driver


def test_plan_command_succeeds(tmp_path):
    rc, driver = _run(tmp_path)
    assert rc == 0


def test_planning_happens_while_driver_is_open(tmp_path):
    rc, driver = _run(tmp_path)
    assert rc == 0
    planning = [event for event in EVENTS if event[0] == "plan_shorts"]
    assert planning, "plan_shorts was never called"
    for name, closed_at_call in EVENTS:
        if name == "close":
            continue
        assert not closed_at_call, "{} ran against a closed driver".format(name)


def test_plan_shorts_receives_the_live_driver(tmp_path):
    _run(tmp_path)
    planning = [event for event in EVENTS if event[0] == "plan_shorts"]
    assert len(planning) == 1, "expected exactly one plan_shorts call"
    assert planning[0][1] is False


def test_driver_is_closed_after_planning(tmp_path):
    rc, driver = _run(tmp_path)
    assert rc == 0
    assert driver.close_calls == 1
    assert driver.closed is True
    names = [event[0] for event in EVENTS]
    assert names.index("plan_shorts") < names.index("close"), (
        "driver.close() ran before planning completed"
    )


def test_driver_is_closed_when_planning_fails(tmp_path, monkeypatch):
    EVENTS.clear()
    driver = _install_driver()

    def boom(*args, **kwargs):
        EVENTS.append(("plan_shorts", driver.closed))
        raise ValueError("synthetic planning failure")

    monkeypatch.setattr(shorts_cli.planner, "plan_shorts", boom)
    with pytest.raises(ValueError):
        shorts_cli._cmd_plan(_plan_args(tmp_path))
    assert driver.close_calls == 1, "driver leaked when planning raised"
    assert driver.closed is True


def test_driver_is_closed_when_curation_fails(tmp_path, monkeypatch):
    EVENTS.clear()
    driver = _install_driver()

    def boom(*args, **kwargs):
        EVENTS.append(("curate_brief", driver.closed))
        raise RuntimeError("synthetic curation failure")

    monkeypatch.setattr(shorts_cli.curator, "curate_brief", boom)
    with pytest.raises(RuntimeError):
        shorts_cli._cmd_plan(_plan_args(tmp_path))
    assert driver.close_calls == 1, "driver leaked when curation raised"


def test_discussion_mode_still_closes_the_driver(tmp_path, monkeypatch):
    """--discusses cleanup must remain correct after any repair."""
    EVENTS.clear()
    driver = _install_driver()
    monkeypatch.setattr(shorts_cli.curator, "discusses_topic", lambda *a, **k: [])
    rc = shorts_cli._cmd_plan(_plan_args(tmp_path, "--discusses"))
    assert rc == 1, "empty discussion should report failure"
    assert driver.close_calls == 1
    names = [event[0] for event in EVENTS]
    assert "plan_shorts" not in names


def test_discussion_mode_closes_driver_on_success(tmp_path, monkeypatch):
    EVENTS.clear()
    driver = _install_driver()
    monkeypatch.setattr(
        shorts_cli.curator, "discusses_topic", lambda *a, **k: [types.SimpleNamespace(text="x" * 40)]
    )
    rc = shorts_cli._cmd_plan(_plan_args(tmp_path, "--discusses"))
    assert rc == 0
    assert driver.close_calls == 1
    names = [event[0] for event in EVENTS]
    assert names.index("plan_discussion") < names.index("close")
    for name, closed_at_call in EVENTS:
        if name == "close":
            continue
        assert not closed_at_call, "{} ran against a closed driver".format(name)


def test_plan_json_is_written(tmp_path):
    rc, _driver = _run(tmp_path)
    assert rc == 0
    written = list(tmp_path.glob("*.json"))
    assert written, "no plan JSON was written"
    assert "plan0123456789" in written[0].name
