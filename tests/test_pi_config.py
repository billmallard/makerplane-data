# SPDX-License-Identifier: Apache-2.0
"""pyefis_data.config — defaults and the construct-never-raises contract."""

from pathlib import Path

from pyefis_data.config import Config, DEFAULT_BASE_URL


def test_defaults_when_no_file(tmp_path):
    cfg = Config.load(tmp_path / "nope.yaml")
    assert cfg.base_url == DEFAULT_BASE_URL
    # Selection is kind-driven now: core navdata tracked, no explicit packs,
    # no bulk regions opted in.
    assert set(cfg.track_kinds) == {"navdata", "navaids", "obstacles", "cifp"}
    assert cfg.packs == ()
    assert cfg.regions == ()
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


def test_pyefis_unit_scope_defaults_to_none(tmp_path):
    p = tmp_path / "data.yaml"
    p.write_text("base_url: https://x\n")
    assert Config.load(p).pyefis_unit_scope is None   # None => auto-detect


def test_pyefis_unit_scope_honours_valid_values(tmp_path):
    for val in ("user", "system"):
        p = tmp_path / "data.yaml"
        p.write_text(f"pyefis_unit_scope: {val}\n")
        assert Config.load(p).pyefis_unit_scope == val


def test_pyefis_unit_scope_ignores_bad_values(tmp_path):
    """A typo (or wrong type) must fall back to auto-detect, never obeyed -- a
    bad value can't point the restart at a scope that doesn't exist (#23)."""
    for bad in ("User", "systemd", "both", "true"):
        p = tmp_path / "data.yaml"
        p.write_text(f"pyefis_unit_scope: {bad}\n")
        assert Config.load(p).pyefis_unit_scope is None
