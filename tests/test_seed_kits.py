"""seed_kits: copy-if-missing from the baked seed into the live volume.

Verifies new files are added, existing files are never overwritten, and
generated/user content is preserved (the durability guarantee).
"""
from __future__ import annotations

from pathlib import Path

from brand_ops.seed_kits import seed


def test_copies_missing_files(tmp_path: Path) -> None:
    seed_dir = tmp_path / "seed"
    live = tmp_path / "live"
    (seed_dir / "brandA" / "imported" / "logo").mkdir(parents=True)
    (seed_dir / "brandA" / "brand.yaml").write_text("seed-yaml")
    (seed_dir / "brandA" / "imported" / "logo" / "x.png").write_bytes(b"PNG")

    n = seed(seed_dir, live)

    assert n == 2
    assert (live / "brandA" / "brand.yaml").read_text() == "seed-yaml"
    assert (live / "brandA" / "imported" / "logo" / "x.png").is_file()


def test_never_overwrites_or_deletes(tmp_path: Path) -> None:
    seed_dir = tmp_path / "seed"
    live = tmp_path / "live"
    (seed_dir / "brandA").mkdir(parents=True)
    (seed_dir / "brandA" / "brand.yaml").write_text("seed-yaml")
    # The live dir already holds a user-edited brand.yaml and a generated asset.
    (live / "brandA" / "generated").mkdir(parents=True)
    (live / "brandA" / "brand.yaml").write_text("user-edited")
    (live / "brandA" / "generated" / "og.png").write_bytes(b"GEN")

    n = seed(seed_dir, live)

    assert n == 0  # brand.yaml already present -> not overwritten; nothing new
    assert (live / "brandA" / "brand.yaml").read_text() == "user-edited"
    assert (live / "brandA" / "generated" / "og.png").is_file()


def test_missing_seed_dir_is_noop(tmp_path: Path) -> None:
    assert seed(tmp_path / "does-not-exist", tmp_path / "live") == 0
