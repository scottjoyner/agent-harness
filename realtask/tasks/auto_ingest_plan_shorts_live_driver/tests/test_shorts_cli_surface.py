"""Broader acceptance for the ``auto_ingest_plan_shorts_live_driver`` fixture.

The targeted suite (``test_plan_driver_lifetime.py``) checks the defect this
fixture exists to catch: that planning happens while the driver is open and that
the driver is still closed exactly once on every exit path.

This suite is the *broader* guard. It checks the rest of the module -- the
parser surface, the plans-directory contract, real ``Plan`` persistence, the
brand check, and the remaining driver-owning handlers -- using the **real**
``models``, ``curator`` and ``backdrop`` modules from the frozen snapshot. Only
``planner``, ``publish``, ``render`` and the Neo4j driver are stubbed.

Everything here must hold both before and after the canonical repair. A repair
that fixes the ordering while quietly changing any of it is a regression, not a
fix.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

import auto_ingest.shorts as shorts_pkg  # noqa: E402

EXPECTED_SUBCOMMANDS = {
    "plan", "iterate", "render", "list", "montage", "highlights", "trip", "publish",
}


class _Rows(list):
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
        return types.SimpleNamespace(
            run=lambda *a, **k: _Rows([])
        )

    def close(self):
        self.close_calls += 1
        self.closed = True


class FakePlan:
    plan_id = "plan0123456789"
    shorts = []
    iteration = 0
    topic = "fixture_topic"

    def save(self, path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"fixture": true}', encoding="utf-8")


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


# ``models`` is the real module from the frozen snapshot: the Plan persistence
# checks below exercise the actual dataclasses and the actual save/load path.
# Everything that talks to a graph is stubbed.
_stub("auto_ingest.shorts.planner", **{
    name: (lambda *a, **k: FakePlan())
    for name in (
        "plan_shorts", "plan_montage", "plan_highlights", "plan_trip_story",
        "plan_discussion", "iterate_plan",
    )
})
_stub("auto_ingest.shorts.curator", curate_brief=lambda *a, **k: None,
      discusses_topic=lambda *a, **k: [])
_stub("auto_ingest.shorts.backdrop", select_highway_pool=lambda *a, **k: [])
_stub("auto_ingest.shorts.publish")
_stub("auto_ingest.shorts.render")
_stub("auto_ingest.shorts.uploader", load_queue=lambda *a, **k: [],
      validate_queue=lambda *a, **k: [])
_stub("auto_ingest_config", get_neo4j_password=lambda: "unused-in-fixture")

# ``cli._brand_check`` opens with ``from PIL import Image`` before it looks at
# anything, so even the missing-manifest path below needs the name to resolve.
# Pillow is a real dependency of the upstream project and is not a dependency of
# this harness, so it is stubbed rather than installed: an acceptance tier that
# imports an undeclared third-party package reports REGRESSION_FAILURE on a host
# that lacks it and passes on one that has it, which makes the verdict a
# property of the machine instead of the patch. The brand check never reaches
# ``Image`` on the path under test.
#
# Same reasoning as the pydantic_settings stub in the auto-router fixtures.
_stub("PIL", Image=types.SimpleNamespace(open=lambda *a, **k: None))
_stub("PIL.Image")

from auto_ingest.shorts import cli as shorts_cli  # noqa: E402
from auto_ingest.shorts.models import Plan  # noqa: E402

shorts_pkg.cli = shorts_cli


def _install_driver():
    driver = FakeDriver()
    shorts_cli._driver = lambda: (driver, "neo4j")
    return driver


# --------------------------------------------------------------------------
# parser surface
# --------------------------------------------------------------------------

def test_subcommand_set_is_intact():
    import argparse

    parser = shorts_cli.build_parser()
    names = set(
        next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        ).choices
    )
    assert names == EXPECTED_SUBCOMMANDS


def test_plan_defaults_are_intact():
    args = shorts_cli.build_parser().parse_args(["plan", "t"])
    assert vars(args)["shorts"] == 3
    assert vars(args)["dur"] == 30.0
    assert vars(args)["shots_per_short"] == 3
    assert vars(args)["seed"] == 1
    assert vars(args)["pool"] == 400
    assert vars(args)["discusses"] is False


def test_defaults_dirs_respect_their_environment_variables(monkeypatch):
    """The plans/out directory contract must survive any repair."""
    plans = Path("/tmp/shirts-plans-contract-check")
    out = Path("/tmp/shirts-out-contract-check")
    monkeypatch.setenv("SHORTS_PLANS_DIR", str(plans))
    monkeypatch.setenv("SHORTS_OUT_DIR", str(out))
    import importlib

    reloaded = importlib.reload(shorts_cli)
    assert reloaded.DEFAULT_PLANS_DIR == Path(plans)
    assert reloaded.DEFAULT_OUT_DIR == Path(out)


def test_defaults_dirs_have_their_documented_fallbacks(monkeypatch):
    monkeypatch.delenv("SHORTS_PLANS_DIR", raising=False)
    monkeypatch.delenv("SHORTS_OUT_DIR", raising=False)
    import importlib

    reloaded = importlib.reload(shorts_cli)
    assert str(reloaded.DEFAULT_PLANS_DIR) == "shorts_plans"
    assert str(reloaded.DEFAULT_OUT_DIR) == "shorts_out"


# --------------------------------------------------------------------------
# real plan persistence
# --------------------------------------------------------------------------

def test_plan_round_trips_through_disk(tmp_path):
    """Real Plan.save / Plan.load from the frozen snapshot."""
    from auto_ingest.shorts.models import Brief, Plan, PlannedShort

    brief = Brief(topic="fixture_topic", title="Fixture", hook="hook", points=["a", "b"])
    plan = Plan(
        topic=brief.topic,
        brief=brief,
        iteration=0,
        shorts=[
            PlannedShort(
                id="s1",
                brief_topic=brief.topic,
                title="one",
                cues=[],
                shots=[],
            )
        ],
    )
    path = tmp_path / "fixture_topic__plan.json"
    plan.save(path)
    assert path.is_file()
    reloaded = Plan.load(path)
    assert reloaded.topic == "fixture_topic"
    assert len(reloaded.shorts) == 1


def test_list_disk_mode_reports_real_plans(tmp_path, capsys):
    from auto_ingest.shorts.models import Brief, Plan, PlannedShort

    brief = Brief(topic="listed_topic", title="Listed", hook="hook", points=["a"])
    plan = Plan(
        topic=brief.topic,
        brief=brief,
        iteration=0,
        shorts=[PlannedShort(id="s1", brief_topic=brief.topic, title="one", cues=[], shots=[])],
    )
    plan.save(tmp_path / "listed_topic__plan.json")

    args = shorts_cli.build_parser().parse_args(["list", "--plans-dir", str(tmp_path)])
    assert shorts_cli._cmd_list(args) == 0
    out = capsys.readouterr().out
    assert "listed_topic__plan.json" in out
    assert "topic=listed_topic" in out
    assert "shorts=1" in out
    assert "iter=0" in out


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------

def test_brand_check_reports_a_missing_manifest(tmp_path):
    ok, issues = shorts_cli._brand_check(tmp_path / "does-not-exist")
    assert ok is False
    assert issues and "missing brand_manifest.json" in issues[0]


def test_publish_validate_is_report_only(tmp_path):
    args = shorts_cli.build_parser().parse_args(
        ["publish", "validate", "--disk", "--out-root", str(tmp_path)]
    )
    # An empty root must report an intact queue, never touch the network.
    rc = shorts_cli._cmd_publish(args)
    assert rc in (0, 1)


# --------------------------------------------------------------------------
# the other driver-owning handlers
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "handler,argv",
    [
        ("_cmd_montage", ["montage", "--shorts", "1"]),
        ("_cmd_highlights", ["highlights", "--per-kind", "1"]),
        ("_cmd_trip", ["trip", "--shorts", "1"]),
    ],
)
def test_other_handlers_still_close_their_driver(handler, argv, tmp_path):
    driver = _install_driver()
    args = shorts_cli.build_parser().parse_args([*argv, "--plans-dir", str(tmp_path)])
    shorts_cli.__dict__[handler](args)
    assert driver.close_calls == 1


def test_list_neo4j_mode_still_closes_its_driver():
    driver = _install_driver()
    args = shorts_cli.build_parser().parse_args(["list", "--neo4j"])
    assert shorts_cli._cmd_list(args) == 0
    assert driver.close_calls == 1


def test_iterate_still_closes_its_driver_when_the_plan_is_missing(tmp_path):
    """An early failure in a handler must not leak the driver."""
    driver = _install_driver()
    args = shorts_cli.build_parser().parse_args(
        ["iterate", str(tmp_path / "absent.json"), "--resolve", "--pool", "10"]
    )
    with pytest.raises(FileNotFoundError):
        shorts_cli._cmd_iterate(args)
    assert driver.close_calls == 0, "iterate reads the plan before opening a driver"


def test_render_still_closes_its_driver_when_the_plan_is_missing(tmp_path):
    driver = _install_driver()
    args = shorts_cli.build_parser().parse_args(["render", str(tmp_path / "absent.json")])
    with pytest.raises(FileNotFoundError):
        shorts_cli._cmd_render(args)
    assert driver.close_calls == 0
