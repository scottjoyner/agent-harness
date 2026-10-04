"""Broader acceptance for the ``auto_router_settings_latency_cache_path`` fixture.

Pins the rest of ``settings.py``: the placement guard's own contract, the
settings surface, and the defaults callers rely on. A repair that fixes the path
agreement by, say, inlining a second copy of the URL grammar, or by deleting the
guard, is rejected here.

``pydantic_settings`` is stubbed so the class can be built without pydantic.
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


@pytest.fixture(scope="module")
def settings_module():
    stub = types.ModuleType("pydantic_settings")

    class BaseSettings:
        pass

    stub.BaseSettings = BaseSettings
    stub.SettingsConfigDict = lambda **kwargs: dict(kwargs)
    sys.modules["pydantic_settings"] = stub
    spec = importlib.util.spec_from_file_location("router_settings_broad", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["router_settings_broad"] = module
    spec.loader.exec_module(module)
    return module


def place(module, url, root="/data", required=False, test_context=False):
    return module.validate_database_placement(
        url, expected_root=root, required=required, test_context=test_context
    )


# -- the guard's own contract --------------------------------------------

def test_memory_is_supported_and_not_persistent(settings_module):
    result = place(settings_module, "sqlite:///:memory:")
    assert result["supported"] is True
    assert result["resolved_path"] == ":memory:"
    assert result["persistent"] is False


def test_memory_is_refused_when_persistence_is_required(settings_module):
    """Fail-closed: :memory: is non-persistent, so the guard must reject it."""
    with pytest.raises(ValueError) as ctx:
        place(settings_module, "sqlite:///:memory:", required=True)
    assert "not persistent" in str(ctx.value)


def test_unsupported_scheme_is_reported_not_guessed(settings_module):
    result = place(settings_module, "postgres://host/db")
    assert result["supported"] is False
    assert result["resolved_path"] is None
    assert result["persistent"] is None


def test_absolute_form_is_persistent_under_the_root(settings_module):
    result = place(settings_module, "sqlite:////data/router.sqlite3")
    assert result["resolved_path"] == "/data/router.sqlite3"
    assert result["persistent"] is True


def test_relative_escape_is_detected(settings_module):
    result = place(settings_module, "sqlite:///../elsewhere/router.sqlite3")
    assert result["persistent"] is False


def test_required_guard_raises_for_a_non_persistent_path(settings_module):
    with pytest.raises(ValueError) as ctx:
        place(settings_module, "sqlite:///./data/router.sqlite3", required=True)
    assert "not persistent" in str(ctx.value)


def test_test_context_suppresses_the_guard(settings_module):
    result = place(
        settings_module, "sqlite:///./data/router.sqlite3",
        required=True, test_context=True,
    )
    assert result["persistent"] is False


def test_guard_reports_the_expected_root(settings_module):
    result = place(settings_module, "sqlite:////data/x.sqlite3", root="/srv/db")
    assert result["expected_persistent_root"] == "/srv/db"
    assert result["persistent"] is False


def test_guard_echoes_the_configured_url(settings_module):
    result = place(settings_module, "sqlite:////data/x.sqlite3")
    assert result["configured_url"] == "sqlite:////data/x.sqlite3"


def test_guard_returns_every_documented_key(settings_module):
    result = place(settings_module, "sqlite:////data/x.sqlite3")
    assert set(result) == {
        "configured_url", "resolved_path", "expected_persistent_root",
        "persistent", "supported",
    }


# -- the settings surface -------------------------------------------------

def test_settings_uses_the_documented_env_prefix(settings_module):
    assert settings_module.Settings.model_config["env_prefix"] == "AUTO_ROUTER_"


def test_database_defaults_are_unchanged(settings_module):
    assert settings_module.Settings.database_url == "sqlite:///./data/router.sqlite3"
    assert settings_module.Settings.database_persistent_root == "/data"


def test_placement_flags_default_to_off(settings_module):
    assert settings_module.Settings.database_placement_required is False
    assert settings_module.Settings.database_placement_test_context is False


def test_shipped_docker_url_form_still_validates(settings_module):
    """The four-slash form in docker-compose must keep passing the guard."""
    result = place(
        settings_module, "sqlite:////data/router.sqlite3", required=True
    )
    assert result["persistent"] is True


def test_timing_budgets_are_unchanged(settings_module):
    fields = settings_module.Settings
    assert fields.request_timeout_seconds == 240.0
    assert fields.attempt_timeout_seconds == 240.0
    assert fields.connect_timeout_seconds == 5.0
    assert fields.request_deadline_seconds == 300.0
    assert fields.max_candidate_attempts == 4


def test_latency_knobs_are_unchanged(settings_module):
    fields = settings_module.Settings
    assert fields.latency_persist_interval_seconds == 30
    assert fields.latency_term_weight == 0.0


def test_latency_cache_property_is_still_a_property(settings_module):
    assert isinstance(
        settings_module.Settings.__dict__["latency_cache_path"], property
    )


def test_get_settings_is_cached(settings_module):
    assert hasattr(settings_module.get_settings, "cache_clear")


def test_settings_instance_builds_without_pydantic(settings_module):
    obj = object.__new__(settings_module.Settings)
    obj.database_url = "sqlite:////data/router.sqlite3"
    assert obj.latency_cache_path.endswith("latency_ema.json")


def test_non_sqlite_fallback_is_under_a_data_directory(settings_module, tmp_path, monkeypatch):
    """The fallback branch is documented behaviour, not a defect."""
    monkeypatch.chdir(tmp_path)
    obj = object.__new__(settings_module.Settings)
    obj.database_url = "postgres://host/db"
    assert obj.latency_cache_path == os.path.join(
        str(tmp_path), "data", "latency_ema.json"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
