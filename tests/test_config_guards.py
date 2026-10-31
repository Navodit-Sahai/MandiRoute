"""Tests for config.py (assumptions registry) and guards.py (live / stub switch)."""
import os

import pytest
from pydantic import ValidationError

from app import guards
from app.config import Settings, get_settings, load_env_file


# ------------------------------------------------------------------ guards
def test_default_mode_is_live(monkeypatch):
    monkeypatch.delenv("MG_MODE", raising=False)
    assert guards.mode() == "live"
    assert guards.banner() is None


def test_stub_forbidden_in_live_and_replay(monkeypatch):
    for m in ("live", "replay"):
        monkeypatch.setenv("MG_MODE", m)
        with pytest.raises(RuntimeError):
            guards.require_stub_allowed("some_stub")


def test_stub_allowed_in_dev_stub_with_banner(monkeypatch):
    monkeypatch.setenv("MG_MODE", "dev_stub")
    guards.require_stub_allowed("some_stub")                 # must NOT raise
    assert "FIXTURE" in guards.banner()


def test_replay_banner(monkeypatch):
    monkeypatch.setenv("MG_MODE", "replay")
    assert "HISTORICAL REPLAY" in guards.banner()


def test_invalid_mode_is_an_error(monkeypatch):
    monkeypatch.setenv("MG_MODE", "production")
    with pytest.raises(ValueError):
        guards.mode()


# ------------------------------------------------------------------ settings
def test_settings_defaults_are_consistent():
    s = get_settings()
    assert s.delay_mult["none"] <= s.delay_mult["moderate"] <= s.delay_mult["severe"]
    assert s.price_degrade_days <= s.price_abstain_days


def test_person2_flags_default_to_not_verified():
    s = get_settings()
    assert s.price_unit_verified is False
    assert s.telegram_live_location_verified is False


def test_cr5_ripening_disabled_by_default():
    from app.config import RIPENING_ENABLED
    s = get_settings()
    assert s.ripening_enabled is False
    assert RIPENING_ENABLED is False


def test_settings_are_frozen():
    with pytest.raises(ValidationError):
        get_settings().cvar_q = 0.5


def test_bad_delay_ordering_rejected():
    with pytest.raises(ValidationError):
        Settings(delay_mult={"none": 0.0, "moderate": 0.5, "severe": 0.2})


def test_missing_delay_key_rejected():
    with pytest.raises(ValidationError):
        Settings(delay_mult={"none": 0.0, "severe": 0.6})


def test_degrade_after_abstain_rejected():
    with pytest.raises(ValidationError):
        Settings(price_degrade_days=10, price_abstain_days=5)


# ------------------------------------------------------------------ .env loader
def test_env_loader_reads_values_and_keeps_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\nMG_TEST_A=hello # trailing note\nMG_TEST_B='quoted'\nMG_TEST_C=from_file\n\nnonsense line\n")
    monkeypatch.delenv("MG_TEST_A", raising=False)
    monkeypatch.delenv("MG_TEST_B", raising=False)
    monkeypatch.setenv("MG_TEST_C", "already_set")             # real environment must win
    load_env_file(env_file)
    assert os.environ["MG_TEST_A"] == "hello"
    assert os.environ["MG_TEST_B"] == "quoted"
    assert os.environ["MG_TEST_C"] == "already_set"
    for k in ("MG_TEST_A", "MG_TEST_B"):
        monkeypatch.delenv(k, raising=False)


def test_env_loader_ignores_missing_file(tmp_path):
    load_env_file(tmp_path / "does_not_exist.env")           # must not crash
