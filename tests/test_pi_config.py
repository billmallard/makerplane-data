"""pyefis_data.config — defaults and the construct-never-raises contract."""

from pathlib import Path

from pyefis_data.config import Config, DEFAULT_BASE_URL


def test_defaults_when_no_file(tmp_path):
    cfg = Config.load(tmp_path / "nope.yaml")
    assert cfg.base_url == DEFAULT_BASE_URL
    assert "airports-conus" in cfg.packs
    assert cfg.manifest_url.endswith("/manifest.json")
    assert cfg.sig_url.endswith("/manifest.json.minisig")
    assert cfg.pack_url("airports-conus-2606.pack").endswith(
        "/packs/airports-conus-2606.pack")


def test_malformed_yaml_falls_back_to_defaults(tmp_path):
    p = tmp_path / "data.yaml"
    p.write_text("this: : : not valid yaml ][")
    cfg = Config.load(p)            # must not raise
    assert cfg.base_url == DEFAULT_BASE_URL


def test_overrides_are_applied(tmp_path):
    p = tmp_path / "data.yaml"
    p.write_text(
        "base_url: https://staging.example.com\n"
        "packs: [airports-conus]\n"
        "regions: [conus]\n"
        "auto_update: false\n"
        "stage_next: false\n"
        "storage_budget_gb: 16\n"
    )
    cfg = Config.load(p)
    assert cfg.base_url == "https://staging.example.com"
    assert cfg.packs == ("airports-conus",)
    assert cfg.auto_update is False
    assert cfg.stage_next is False
    assert cfg.storage_budget_gb == 16.0


def test_root_expands_user(tmp_path):
    p = tmp_path / "data.yaml"
    p.write_text("root: ~/somewhere/data\n")
    cfg = Config.load(p)
    assert "~" not in str(cfg.root)
