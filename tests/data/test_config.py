import os
from app import config


def test_load_env_reads_file_but_never_overrides(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text('# c\nMG_TEST_A="from_file"\nMG_TEST_B=file_b\n')
    monkeypatch.delenv("MG_TEST_A", raising=False)
    monkeypatch.setenv("MG_TEST_B", "already_set")
    config.load_env(p)
    assert os.environ["MG_TEST_A"] == "from_file" and os.environ["MG_TEST_B"] == "already_set"
    monkeypatch.delenv("MG_TEST_A")


def test_empty_value_counts_as_unset(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", "")
    assert config.datagov_key() is None
    assert config.price_unit_verified() is False
