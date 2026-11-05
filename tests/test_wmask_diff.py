# SPDX-License-Identifier: Apache-2.0
"""packtool wmask-diff: a mask-only terrain re-edition against its
predecessor (AER-4595)."""

import json
import zipfile

import pytest

from packtools import cli, wmask_diff

L5 = 3600 // 32 + 1                     # 113: the side QA's repro read
TILE = "N27W097"
MIP5 = f".mip/5/N27/{TILE}.wmask"


def _pack_mask(rows):
    """Row-packed, MSB first, rows padded to a byte (np.packbits layout)."""
    out = bytearray()
    for row in rows:
        bits = "".join("1" if v else "0" for v in row)
        bits += "0" * (-len(bits) % 8)
        out += int(bits, 2).to_bytes(len(bits) // 8, "big")
    return bytes(out)


def _grid(side, water):
    return [[water(r, c) for c in range(side)] for r in range(side)]


def _seam_band(r, c):
    """The AER-4594 defect: open water with the border posts unset."""
    return 0 < r < L5 - 1 and 0 < c < L5 - 1


def _open_water(r, c):
    return True


def _write(path, edition, mask_fn=_seam_band, *, hgt=b"\x00\x10" * 4, extra=None,
           mask_bytes=None, mosaic_fn=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("pack_meta.json", json.dumps({"id": "terrain-us-central", "kind": "terrain",
                                               "cycle": edition}))
        z.writestr(f"N27/{TILE}.hgt", hgt)
        z.writestr(f".mip/5/N27/{TILE}.hgt", b"\x00\x01" * 8)
        z.writestr(MIP5, mask_bytes if mask_bytes is not None
                   else _pack_mask(_grid(L5, mask_fn)))
        z.writestr(".mip/mosaic/L6.json", json.dumps({"rows": 3, "cols": 10}))
        z.writestr(".mip/mosaic/L6.hgt", b"\x00\x02" * 30)
        z.writestr(".mip/mosaic/L6.wmask",
                   _pack_mask([[mosaic_fn(r, c) if mosaic_fn else c > 4 for c in range(10)]
                               for r in range(3)]))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


def test_border_counts_reads_each_edge_of_an_unaligned_mask():
    # 3 rows x 10 cols (not a multiple of 8): set the east column and two
    # north posts only.
    rows = [[c in (0, 9) for c in range(10)],
            [c == 9 for c in range(10)],
            [c == 9 for c in range(10)]]
    assert wmask_diff.border_counts(_pack_mask(rows), 3, 10) == {
        "N": 2, "S": 1, "W": 1, "E": 3}


def test_union_rebuild_passes_and_counts_gained_posts(tmp_path):
    old = _write(tmp_path / "old.pack", "2024edr1", _seam_band)
    new = _write(tmp_path / "new.pack", "2024edr2", _open_water)
    d = wmask_diff.diff(new, old, borders=[f"{TILE}:5:SE"])
    assert d.failures() == []
    assert d.levels["L5"].changed == 1
    assert d.levels["L5"].gained == 4 * (L5 - 1)          # the whole border ring
    assert d.lost == 0
    assert d.levels["mosaic L6"].changed == 0
    (b,) = d.borders
    assert b.ok and b.published["S"] == 0 and b.staged["S"] == L5 == b.staged["E"]
    assert "OK:" in wmask_diff.render(d)


def test_unfixed_masks_fail_the_border_check(tmp_path):
    old = _write(tmp_path / "old.pack", "2024edr1", _seam_band)
    new = _write(tmp_path / "new.pack", "2024edr2", _seam_band)
    d = wmask_diff.diff(new, old, borders=[f"{TILE}:5:SE"])
    assert d.failures() == [f"border {TILE}:5:SE: N 0/{L5} S 0/{L5} E 0/{L5} W 0/{L5}"]


def test_a_changed_elevation_tile_fails(tmp_path):
    old = _write(tmp_path / "old.pack", "2024edr1")
    new = _write(tmp_path / "new.pack", "2024edr2", hgt=b"\x00\x11" * 4)
    assert wmask_diff.diff(new, old).content_changed == [f"N27/{TILE}.hgt"]


def test_member_sets_must_match(tmp_path):
    old = _write(tmp_path / "old.pack", "2024edr1")
    new = _write(tmp_path / "new.pack", "2024edr2", extra={"N28/N28W097.hgt": b"\x00" * 8})
    d = wmask_diff.diff(new, old)
    assert d.only_staged == ["N28/N28W097.hgt"] and d.failures()
    d = wmask_diff.diff(old, new)
    assert d.only_published == ["N28/N28W097.hgt"] and d.failures()


def test_a_mask_of_the_wrong_length_fails(tmp_path):
    good = _pack_mask(_grid(L5, _seam_band))
    old = _write(tmp_path / "old.pack", "2024edr1", mask_bytes=good[:-15])
    new = _write(tmp_path / "new.pack", "2024edr2", mask_bytes=good[:-15])
    assert wmask_diff.diff(new, old).bad_size == [MIP5]


def test_lost_posts_are_reported_and_gated_only_on_request(tmp_path, capsys):
    old = _write(tmp_path / "old.pack", "2024edr1", _open_water)
    new = _write(tmp_path / "new.pack", "2024edr2", _seam_band,
                 mosaic_fn=lambda r, c: c > 5)
    d = wmask_diff.diff(new, old)
    assert d.lost == 4 * (L5 - 1) + 3 and d.failures() == []
    assert cli.main(["wmask-diff", str(new), "--against", str(old)]) == 0
    assert cli.main(["wmask-diff", str(new), "--against", str(old), "--max-lost", "10"]) == 1
    out = capsys.readouterr().out
    assert "most posts lost" in out and MIP5 in out


def test_border_on_a_tile_the_pack_lacks_fails(tmp_path):
    old = _write(tmp_path / "old.pack", "2024edr1")
    new = _write(tmp_path / "new.pack", "2024edr2")
    d = wmask_diff.diff(new, old, borders=["N30W090:5:S"])
    assert "not in the staged pack" in d.failures()[0]


@pytest.mark.parametrize("spec", ["N27W097:5", "N27W097:x:S", "N27W097:5:Q", "bogus:5:S"])
def test_bad_border_spec_is_a_usage_error(tmp_path, spec):
    old = _write(tmp_path / "old.pack", "2024edr1")
    with pytest.raises(SystemExit):
        cli.main(["wmask-diff", str(old), "--against", str(old), "--border", spec])
