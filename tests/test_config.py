"""Runtime config validation + the fail-secure AUTH_REQUIRED boot warning.

These guard T1.5: the compose default for AUTH_REQUIRED is now fail-secure
(true), and a deployment that runs with auth off while a DATABASE_URL is set
must emit a loud boot warning (not a raise, so the CLI and the DB-less tests
stay quiet). validate_runtime_config keeps its existing behavior; only the
warning is new.
"""
from __future__ import annotations

import logging

import pytest

from brand_ops import config


def _clear_settings_cache() -> None:
    """get_settings is lru_cached, so a test must reset it after touching env."""
    config.get_settings.cache_clear()


def test_warns_when_auth_off_and_database_url_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Auth off + a real DB url looks like a deploy serving unauthenticated."""
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:REMOVED@db/brand_ops")
    _clear_settings_cache()
    with caplog.at_level(logging.WARNING, logger="brand_ops"):
        settings = config.validate_runtime_config()
    _clear_settings_cache()

    assert settings.database_url
    records = [r for r in caplog.records if r.name == "brand_ops"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "AUTH_REQUIRED" in records[0].message
    assert "UNAUTHENTICATED" in records[0].message


def test_silent_when_auth_off_and_no_database_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The CLI / stdio MCP / DB-less test path must stay quiet (no warning)."""
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _clear_settings_cache()
    with caplog.at_level(logging.WARNING, logger="brand_ops"):
        settings = config.validate_runtime_config()
    _clear_settings_cache()

    assert not settings.database_url
    assert [r for r in caplog.records if r.name == "brand_ops"] == []


def test_silent_when_auth_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Auth on is the production profile: no unauthenticated warning fires."""
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:REMOVED@db/brand_ops")
    monkeypatch.setenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001")
    _clear_settings_cache()
    with caplog.at_level(logging.WARNING, logger="brand_ops"):
        config.validate_runtime_config()
    _clear_settings_cache()

    assert [r for r in caplog.records if r.name == "brand_ops"] == []


def test_warning_does_not_raise_with_db_and_auth_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warning is advisory: auth off must never raise, even with a DB url."""
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:REMOVED@db/brand_ops")
    _clear_settings_cache()
    settings = config.validate_runtime_config()
    _clear_settings_cache()
    assert isinstance(settings, config.Settings)


def test_auth_on_still_raises_on_missing_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing behavior intact: auth on without DATABASE_URL is a hard error."""
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", raising=False)
    _clear_settings_cache()
    with pytest.raises(config.ConfigError) as excinfo:
        config.validate_runtime_config()
    _clear_settings_cache()
    assert "DATABASE_URL" in str(excinfo.value)
    assert "BRAND_OPS_BOOTSTRAP_WORKSPACE_ID" in str(excinfo.value)


def test_boot_check_shares_the_one_auth_required_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request path and the boot check must agree on what "unset" means.

    ``auth_required()``'s default is now fail-closed (true). This module used to
    keep its OWN copy of the parse, defaulting to false -- so an unconfigured node
    would have enforced auth on every request while the boot check believed it was
    a dev box and skipped the prod-profile assertions. One reader, one answer:
    with nothing configured, the profile is prod, and prod without a DATABASE_URL
    refuses to boot rather than coming up serving a broken, empty surface.
    """
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", raising=False)
    _clear_settings_cache()
    try:
        with pytest.raises(config.ConfigError):
            config.validate_runtime_config()
    finally:
        _clear_settings_cache()
