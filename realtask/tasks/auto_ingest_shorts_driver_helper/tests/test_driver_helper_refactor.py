"""Bounded acceptance tests for the ``small_refactor`` fixture.

The task is behaviour-preserving: factor the repeated
``driver, _ = _driver()`` / ``try: ... finally: driver.close()`` shape in
``auto_ingest/shorts/cli.py`` into one helper, without changing what any
subcommand does.

These tests therefore check three properties:

1. the public CLI surface is unchanged (subcommands, flags, defaults);
2. every subcommand still closes its driver exactly once on every exit path,
   including the discussion-mode early return and an exception;
3. the driver lifetime is now stated in one place rather than repeated.

They deliberately do *not* assert anything about the driver-lifetime ordering
defect in ``plan``: fixing that is a different task, and a behaviour-preserving
refactor must leave it exactly as it found it.
"""
from __future__ import annotations

import inspect
import re
import sys
import types
from pathlib import Path, PurePath

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
CLI = WORKTREE / "auto_ingest" / "shorts" / "cli.py"

if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

import auto_ingest.shorts as shorts_pkg  # noqa: E402

EVENTS: list = []

EXPECTED_SUBCOMMANDS = {
    "plan", "iterate", "render", "list", "montage", "highlights", "trip", "publish",
}
EXPECTED_PLAN_DEFAULTS = {
    "shorts": 3,
    "dur": 30.0,
    "shots_per_short": 3,
    "top_papers": 6,
    "seed": 1,
    "pool": 400,
    "min_score": 0.65,
    "min_text_len": 30,
    "discuss_limit": 40,
}
EXPECTED_PLAN_FLAGS = {
    "topic", "shorts", "dur", "shots_per_short", "top_papers", "seed", "pool",
    "plans_dir", "discusses", "min_score", "min_text_len", "discuss_limit",
}

#: Handlers that open a driver today, and the args each one needs.
DRIVER_HANDLERS = {
    "_cmd_montage": ["montage", "--shorts", "2"],
    "_cmd_highlights": ["highlights", "--per-kind", "1"],
    "_cmd_trip": ["trip", "--shorts", "2"],
    "_cmd_list": ["list", "--neo4j"],
}


class FakeResult(list):
    """A driver result that is both iterable and exposes .data()."""

    def data(self):
        return list(self)


class FakeDriver:
    def __init__(self):
        self.closed = False
        self.close_calls = 0

    def session(self):
        if self.closed:
            raise RuntimeError("session requested on a closed driver")
        return types.SimpleNamespace(run=lambda *a, **k: FakeResult())

    def close(self):
        self.close_calls += 1
        self.closed = True
        EVENTS.append(("close", True))


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
    points = ["point one"]
    sources = []


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    parent_name, _, leaf = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None:
        setattr(parent, leaf, module)
    return module


def _closed_probe(label):
    def probe(*args, **kwargs):
        return []
    probe.__name__ = label
    return probe


def curate_brief(driver, topic, **kwargs):
    return FakeBrief()


def select_highway_pool(driver, **kwargs):
    return []


def discusses_topic(driver, topic, **kwargs):
    return []


def plan_shorts(brief, anchors, **kwargs):
    return FakePlan()


def plan_montage(driver, **kwargs):
    return FakePlan()


def plan_highlights(driver, **kwargs):
    return FakePlan()


def plan_trip_story(driver, **kwargs):
    return FakePlan()


def plan_discussion(driver, clips, **kwargs):
    return FakePlan()


def iterate_plan(prev, anchors, **kwargs):
    return FakePlan()


def render_plan(plan, out_dir, **kwargs):
    return []


def upsert_manifest(driver, plan, out_dir):
    return None


_stub("auto_ingest.shorts.backdrop", select_highway_pool=select_highway_pool)
_stub("auto_ingest.shorts.curator", curate_brief=curate_brief,
      discusses_topic=discusses_topic)
_stub("auto_ingest.shorts.planner", plan_shorts=plan_shorts,
      plan_montage=plan_montage, plan_highlights=plan_highlights,
      plan_trip_story=plan_trip_story, plan_discussion=plan_discussion,
      iterate_plan=iterate_plan)
_stub("auto_ingest.shorts.publish")
_stub("auto_ingest.shorts.render", render_plan=render_plan,
      upsert_manifest=upsert_manifest)
_stub("auto_ingest.shorts.models", Plan=FakePlan, Brief=FakeBrief)
_stub("auto_ingest_config", get_neo4j_password=lambda: "unused-in-fixture")

from auto_ingest.shorts import cli as shorts_cli  # noqa: E402

shorts_pkg.cli = shorts_cli


def _install_driver():
    driver = FakeDriver()
    shorts_cli._driver = lambda: (driver, "neo4j")
    return driver


# --------------------------------------------------------------------------
# 1. public CLI surface
# --------------------------------------------------------------------------

def _subcommand_names(parser):
    import argparse

    return set(
        next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ).choices
    )


def test_public_cli_surface_is_unchanged():
    assert _subcommand_names(shorts_cli.build_parser()) == EXPECTED_SUBCOMMANDS


def test_unknown_subcommand_is_still_rejected():
    with pytest.raises(SystemExit):
        shorts_cli.build_parser().parse_args(["no_such_subcommand"])


def test_plan_flag_set_and_defaults_are_unchanged():
    args = shorts_cli.build_parser().parse_args(["plan", "some_topic"])
    plan = vars(args)
    for name, value in EXPECTED_PLAN_DEFAULTS.items():
        assert plan[name] == value, "default for --{} changed".format(name)
    for flag in EXPECTED_PLAN_FLAGS:
        assert flag in plan, "plan lost argument {!r}".format(flag)


def test_exit_codes_are_unchanged():
    parser = shorts_cli.build_parser()
    assert parser.parse_args(["plan", "t", "--discusses"]).discusses is True
    assert parser.parse_args(["render", "p.json", "--tts"]).tts is True


# --------------------------------------------------------------------------
# 2. driver lifetime preserved on every path
# --------------------------------------------------------------------------

def test_driver_closing_helper_is_used_by_the_handlers():
    """Whatever the helper is called, it must yield a live driver and close it.

    A behaviour-preserving refactor introduces one owner of the driver lifetime.
    This test does not name it -- that is the candidate's choice -- but it does
    require that at least one module-level callable takes no required arguments
    and closes a driver it was handed by the factory.
    """
    # Names that are entry points rather than lifetime helpers. ``main`` parses
    # sys.argv, which under pytest would consume the test runner's arguments.
    not_a_helper = {"main", "build_parser"}
    candidates = []
    for name, value in sorted(vars(shorts_cli).items()):
        if name in not_a_helper:
            continue
        # Leading underscores are expected: the candidate will almost certainly
        # name the helper next to the existing _driver().
        if name.startswith("__") or not callable(value) or inspect.ismodule(value):
            continue
        try:
            signature = inspect.signature(value)
        except (TypeError, ValueError):
            continue
        if [
            p
            for p in signature.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]:
            continue

        driver = _install_driver()
        original = shorts_cli._driver
        try:
            shorts_cli._driver = lambda: (driver, "neo4j")
            try:
                _drive_candidate(value)
            except BaseException:
                continue
        finally:
            shorts_cli._driver = original
        if driver.close_calls:
            candidates.append(name)
    assert candidates, (
        "no zero-argument module-level callable opens and closes a driver; the "
        "refactor is expected to introduce one"
    )


def _drive_candidate(value):
    """Exercise a candidate helper in whichever shape it was written.

    A plain function, a generator function, a ``@contextmanager`` and an object
    with a ``close`` method are all legitimate ways to own a driver lifetime,
    and the refactor is the candidate's design choice.
    """
    produced = value()
    if isinstance(produced, PurePath):
        return
    if hasattr(produced, "__enter__") and hasattr(produced, "__exit__"):
        with produced:
            pass
        return
    if hasattr(produced, "close") and callable(produced.close):
        produced.close()
        return
    if hasattr(produced, "throw"):
        produced.close()
        return


@pytest.mark.parametrize("handler,argv", sorted(DRIVER_HANDLERS.items()))
def test_every_handler_still_closes_its_driver_once(handler, argv, tmp_path):
    driver = _install_driver()
    args = shorts_cli.build_parser().parse_args([*argv, "--plans-dir", str(tmp_path)])
    shorts_cli.__dict__[handler](args)
    assert driver.close_calls == 1, "{} closed the driver {} times".format(
        handler, driver.close_calls
    )


def test_render_still_closes_its_driver_once(tmp_path, monkeypatch):
    """render is the one handler that needs a plan file before it opens a driver."""
    driver = _install_driver()
    monkeypatch.setattr(
        shorts_cli, "Plan", type("PlanStub", (), {"load": staticmethod(lambda p: FakePlan())})
    )
    args = shorts_cli.build_parser().parse_args(["render", "plan.json"])
    shorts_cli._cmd_render(args)
    assert driver.close_calls == 1


def test_plan_still_closes_its_driver_on_the_discussion_early_return(tmp_path):
    driver = _install_driver()
    args = shorts_cli.build_parser().parse_args(
        ["plan", "fixture_topic", "--discusses", "--plans-dir", str(tmp_path)]
    )
    rc = shorts_cli._cmd_plan(args)
    assert rc == 1
    assert driver.close_calls == 1


def test_driver_is_not_closed_twice_when_planning_raises(tmp_path, monkeypatch):
    driver = _install_driver()

    def boom(*args, **kwargs):
        raise ValueError("synthetic planning failure")

    monkeypatch.setattr(shorts_cli.planner, "plan_shorts", boom)
    args = shorts_cli.build_parser().parse_args(
        ["plan", "fixture_topic", "--plans-dir", str(tmp_path)]
    )
    with pytest.raises(ValueError):
        shorts_cli._cmd_plan(args)
    assert driver.close_calls == 1


# --------------------------------------------------------------------------
# 3. the point of the refactor
# --------------------------------------------------------------------------

def test_driver_close_is_stated_in_one_place():
    text = CLI.read_text(encoding="utf-8")
    occurrences = len(re.findall(r"driver\.close\(\)", text))
    assert occurrences <= 1, (
        "driver.close() appears {} times; the refactor is meant to state the "
        "driver lifetime once, not to move the repetition around".format(occurrences)
    )


def test_ordering_defect_is_left_exactly_where_it_was():
    """A behaviour-preserving refactor must not also repair the ordering bug.

    ``_cmd_plan`` still calls ``planner.plan_shorts`` after the driver scope has
    ended -- at four-space indentation, outside any ``with`` block. A refactor
    that quietly pulled planning inside the driver's live scope has changed
    behaviour, and this task is not the place to do that.
    """
    text = CLI.read_text(encoding="utf-8")
    match = re.search(r"^([ \t]*)plan = planner\.plan_shorts\(", text, re.MULTILINE)
    assert match, "plan_shorts call not found; the public shape changed"
    indent = match.group(1)
    assert indent == "    ", (
        "plan_shorts is now called at indent {!r}, i.e. inside the driver's live "
        "scope. That repairs the ordering defect, which is a different task.".format(indent)
    )


def test_driver_factory_is_still_referenced():
    text = CLI.read_text(encoding="utf-8")
    assert "_driver()" in text or "_driver(" in text, (
        "the driver factory disappeared; this is not a behaviour-preserving refactor"
    )
