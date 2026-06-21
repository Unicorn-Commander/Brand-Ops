"""save_result must not let two same-second generations clobber each other.

The generated-asset filename is one-second granular, so two generations of the
same brand/asset/variant within one second resolve to the same name. Before the
reserve_unique_path guard, the second silently overwrote the first's bytes AND
sidecar -- a lost, often paid-for, asset. This pins that the second now lands on
a disambiguated name and both survive with their own sidecars.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from brand_ops.backends.base import GenResult
from brand_ops.output import save_result
from brand_ops.prompt_builder import BuiltPrompt


def _built() -> BuiltPrompt:
    return BuiltPrompt(
        text="a mark",
        asset_type="logo",
        variant=None,
        backend="recraft",
        size=(512, 512),
        transparent=False,
        reference_images=[],
        warnings=[],
    )


def _result(image: bytes) -> GenResult:
    return GenResult(
        images=[image],
        mime="image/png",
        backend="recraft",
        model="recraft-v3",
        cost_cents=4.0,
    )


def test_same_second_generations_do_not_clobber(tmp_path: Path) -> None:
    fixed = datetime(2026, 1, 1, 0, 0, 0)  # identical timestamp forces a name clash
    first = save_result(
        "acme", "logo", None, _built(), _result(b"AAAA"), kits_dir=tmp_path, now=fixed
    )
    second = save_result(
        "acme", "logo", None, _built(), _result(b"BBBB"), kits_dir=tmp_path, now=fixed
    )

    assert len(first) == 1
    assert len(second) == 1
    # Distinct files, neither overwritten, second disambiguated with a -N suffix.
    assert first[0] != second[0]
    assert second[0].name != first[0].name
    assert first[0].read_bytes() == b"AAAA"
    assert second[0].read_bytes() == b"BBBB"
    # Each asset keeps its own sidecar.
    for p in (first[0], second[0]):
        assert p.with_suffix(p.suffix + ".json").exists()
