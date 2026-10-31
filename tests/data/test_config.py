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


def test_promote_coords_selection_rules():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("promote", Path(__file__).resolve().parents[2] / "scripts" / "promote_coords.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    base = {"district": "D", "state": "S", "verified": "0", "source": "x", "note": ""}
    c = [{**base, "mandi_id": "a", "market": "Perundurai(Uzhavar Sandhai )", "lat": "11.2", "lon": "77.6", "precision": "market"},
         {**base, "mandi_id": "b", "market": "Kancheepuram(Uzhavar Sandhai )", "lat": "12.8", "lon": "79.8", "precision": "district_centroid"},
         {**base, "mandi_id": "c", "market": "Anna nagar", "lat": "", "lon": "", "precision": "unknown"},
         {**base, "mandi_id": "d", "market": "Anna nagar 2", "lat": "9", "lon": "78", "precision": "market"}]
    picked, problems = m.select(c, ["perundurai", "kanchee", "nowhere", "anna", "perundurai"], {"d"}, False)
    assert [r["mandi_id"] for r in picked] == ["a", "a"]
    assert len(problems) == 3 and any("district centre" in p for p in problems) and any("no match" in p for p in problems)
