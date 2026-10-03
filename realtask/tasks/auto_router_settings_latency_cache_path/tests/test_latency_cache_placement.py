"""Targeted acceptance for the ``auto_router_settings_latency_cache_path`` fixture.

``validate_database_placement`` was added as the single canonical resolver for
the SQLite URL grammar, and it documents the deployment contract explicitly:

    ``sqlite:///data/x`` means ``/data/x``;
    ``sqlite:///./data/x`` stays workdir-relative.

``Settings.latency_cache_path`` predates it and never adopted it. For the
bare-relative form the two functions in the same module return different
directories, so the placement guard can pass while the latency EMA cache is
written into the container's writable layer and silently discarded on every
recreation.

The invariant under test is agreement between the two readers of one URL,
asserted relatively so it does not depend on the process working directory.

``pydantic_settings`` is stubbed: it is the module's only third-party import, and
with a plain ``BaseSettings`` the class can be instantiated via ``__new__``
without any pydantic machinery.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
MODULE_PATH = WORKTREE / "auto_router" / "settings.py"


def _load():
    stub = types.ModuleType("pydantic_settings")

    class BaseSettings:
        pass

    stub.BaseSettings = BaseSettings
    stub.SettingsConfigDict = lambda **kwargs: dict(kwargs)
    sys.modules["pydantic_settings"] = stub
    spec = importlib.util.spec_from_file_location("router_settings", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["router_settings"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def settings_module():
    return _load()


def _instance(module, database_url):
    obj = object.__new__(module.Settings)
    obj.database_url = database_url
    obj.database_persistent_root = "/data"
    obj.database_placement_required = False
    obj.database_placement_test_context = False
    return obj


#: The URL forms whose resolution the module claims to implement.
BARE_RELATIVE = "sqlite:///data/router.sqlite3"
DOT_RELATIVE = "sqlite:///./data/router.sqlite3"
ABSOLUTE = "sqlite:////data/router.sqlite3"
MEMORY = "sqlite:///:memory:"

#: The same three forms under filenames and mount points this fixture never
#: names. The defect is a grammar, not a lookup table, so a repair has to hold
#: for a path it has never been shown. Without these a patch that special-cases
#: the literal ``sqlite:///data/router.sqlite3`` passes every other check in
#: this file while leaving the grammar divergent for everything else -- which is
#: memorisation, not a fix.
GENERALITY_URLS = (
    "sqlite:///data/fleet.sqlite3",
    "sqlite:///data/agent-4471.db",
    "sqlite:///var/lib/agent/ledger.db",
    "sqlite:///data/nested/deep.sqlite3",
    "sqlite:///srv/state.db",
)


def test_module_loads(settings_module):
    assert callable(settings_module.validate_database_placement)
    assert isinstance(settings_module.Settings.latency_cache_path, property)


@pytest.mark.parametrize("url", [BARE_RELATIVE, DOT_RELATIVE, ABSOLUTE])
def test_latency_cache_lives_beside_the_resolved_database(settings_module, url):
    """The whole defect: two readers of one URL, two different directories."""
    obj = _instance(settings_module, url)
    placement = settings_module.validate_database_placement(
        url,
        expected_root=obj.database_persistent_root,
        required=False,
    )
    resolved = placement["resolved_path"]
    cache_dir = os.path.dirname(os.path.abspath(obj.latency_cache_path))
    assert cache_dir == os.path.dirname(os.path.abspath(resolved)), (
        "database_url={!r} resolves to {!r} but the latency EMA cache is written "
        "to {!r}".format(url, resolved, cache_dir)
    )


@pytest.mark.parametrize("url", GENERALITY_URLS)
def test_the_same_invariant_holds_for_paths_the_fixture_never_names(
    settings_module, url
):
    """Coherence is a property of the grammar, not of the shipped filename.

    Same assertion as above, over paths that appear nowhere else in this
    fixture. A patch that special-cases one literal URL is caught here.
    """
    obj = _instance(settings_module, url)
    placement = settings_module.validate_database_placement(
        url,
        expected_root=obj.database_persistent_root,
        required=False,
    )
    resolved_dir = os.path.dirname(os.path.abspath(placement["resolved_path"]))
    cache_dir = os.path.dirname(os.path.abspath(obj.latency_cache_path))
    assert cache_dir == resolved_dir, (
        "database_url={!r} resolves into {!r} but the latency EMA cache went to "
        "{!r}; the two readers of this URL grammar still disagree".format(
            url, resolved_dir, cache_dir
        )
    )
    assert os.path.basename(obj.latency_cache_path) == "latency_ema.json"


def test_bare_relative_form_is_treated_as_the_documented_mount(settings_module):
    """sqlite:///data/x is documented as /data/x, and the guard agrees."""
    placement = settings_module.validate_database_placement(
        BARE_RELATIVE, expected_root="/data", required=False
    )
    assert placement["resolved_path"] == "/data/router.sqlite3"
    assert placement["persistent"] is True


def test_latency_cache_follows_the_documented_mount(settings_module):
    """The observable symptom: EMA state lands outside the persistent mount."""
    obj = _instance(settings_module, BARE_RELATIVE)
    cache_path = obj.latency_cache_path
    # Resolved under the persistent root, not under the process workdir.
    assert os.path.dirname(os.path.abspath(cache_path)) == "/data", (
        "latency EMA would be written to {!r}, outside the persistent mount".format(
            cache_path
        )
    )
    assert os.path.basename(cache_path) == "latency_ema.json"


def test_latency_cache_follows_the_absolute_form(settings_module):
    obj = _instance(settings_module, ABSOLUTE)
    assert os.path.dirname(os.path.abspath(obj.latency_cache_path)) == "/data"


def test_latency_cache_follows_the_dot_relative_form(settings_module):
    """Dot-relative stays workdir-relative; both readers must agree on that."""
    obj = _instance(settings_module, DOT_RELATIVE)
    placement = settings_module.validate_database_placement(
        DOT_RELATIVE, expected_root="/data", required=False
    )
    resolved_dir = os.path.dirname(os.path.abspath(placement["resolved_path"]))
    cache_dir = os.path.dirname(os.path.abspath(obj.latency_cache_path))
    assert cache_dir == resolved_dir, (
        "dot-relative resolved to {!r} but the cache went to {!r}".format(
            resolved_dir, cache_dir
        )
    )


def test_cache_filename_is_stable(settings_module):
    for url in (BARE_RELATIVE, ABSOLUTE, "postgres://host/db"):
        obj = _instance(settings_module, url)
        assert os.path.basename(obj.latency_cache_path) == "latency_ema.json", url


@pytest.mark.parametrize("url", ["postgres://host/db", "redis://host:6379/0", "mysql://h/d"])
def test_unsupported_schemes_are_reported_not_guessed(settings_module, url):
    """validate_database_placement must not pretend to understand them."""
    placement = settings_module.validate_database_placement(
        url, expected_root="/data", required=False
    )
    assert placement["resolved_path"] is None
    assert placement["supported"] is False


def test_unsupported_url_does_not_fail_the_cache_lookup(settings_module):
    """A non-sqlite URL falls back; it must not raise."""
    obj = _instance(settings_module, "postgres://host/db")
    assert obj.latency_cache_path.endswith("latency_ema.json")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
