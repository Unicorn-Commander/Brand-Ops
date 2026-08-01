"""Reference path hardening + mascot-style avatar prompt discipline.

Pins the product fixes for P-00140:
  * absolute workstation paths are never opened and are flagged invalid
  * missing kit-relative refs surface in the audit
  * character refs under refs/ win over logo reference_images for avatars
  * agent-avatar prompts lead with mascot_style, not graphic style_keywords
"""
from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
import yaml
from PIL import Image

from brand_ops import agent_identity
from brand_ops.backends.base import GenRequest, GenResult
from brand_ops.brand_loader import (
    audit_reference_images,
    load_brand,
    resolve_character_references,
    resolve_reference_images,
)
from brand_ops.models import Brand

_REPO = Path(__file__).resolve().parents[1]


def _png(color=(182, 110, 255, 255), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def kits(tmp_path: Path) -> Path:
    src = _REPO / "brand-kits" / "magic-unicorn" / "brand.yaml"
    d = tmp_path / "magic-unicorn"
    d.mkdir()
    shutil.copy(src, d / "brand.yaml")
    # Materialize the kit-relative refs the real brand.yaml declares, READ FROM
    # that file rather than hardcoded: a hardcoded list silently rots the moment
    # a ref is repointed (it did -- vector moved off .svg because the image
    # gateway rejects image/svg+xml), and the test then fails for a reason that
    # has nothing to do with what it is checking.
    declared = yaml.safe_load((d / "brand.yaml").read_text()).get("reference_images") or {}
    for value in declared.values():
        rel = Path(str(value))
        if rel.is_absolute():
            continue  # the "missing absolute path" cases are the point elsewhere
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == ".svg":
            target.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        else:
            target.write_bytes(_png())
    return tmp_path


def test_audit_flags_absolute_workstation_paths(tmp_path: Path) -> None:
    d = tmp_path / "broken"
    d.mkdir()
    (d / "brand.yaml").write_text(
        yaml.safe_dump(
            {
                "brand": {
                    "slug": "broken",
                    "display_name": "Broken",
                },
                "visual": {
                    "colors": {"primary": "#b66eff", "accent": "#00d4ff"},
                },
                "reference_images": {
                    "canonical": "/Users/someone/Pictures/logo.png",
                    "relative_missing": "imported/logo/nope.png",
                },
            }
        )
    )
    brand = load_brand("broken", kits_dir=tmp_path)
    audit = audit_reference_images("broken", brand, kits_dir=tmp_path)
    assert "canonical" in audit["invalid_absolute"]
    assert "relative_missing" in audit["missing"]
    assert audit["ok"] == []

    found, missing = resolve_reference_images("broken", brand, kits_dir=tmp_path)
    assert found == {}
    assert set(missing) == {"canonical", "relative_missing"}


def test_resolve_ok_kit_relative(kits: Path) -> None:
    brand = load_brand("magic-unicorn", kits_dir=kits)
    found, missing = resolve_reference_images("magic-unicorn", brand, kits_dir=kits)
    assert "canonical" in found
    assert missing == []
    audit = audit_reference_images("magic-unicorn", brand, kits_dir=kits)
    assert set(audit["ok"]) == set(found)
    assert audit["invalid_absolute"] == []


def test_character_refs_prefer_refs_dir(kits: Path) -> None:
    brand = load_brand("magic-unicorn", kits_dir=kits)
    refs = kits / "magic-unicorn" / "refs"
    refs.mkdir()
    (refs / "char-a.png").write_bytes(_png((0, 0, 0, 255)))
    (refs / "char-b.png").write_bytes(_png((255, 255, 255, 255)))
    paths = resolve_character_references("magic-unicorn", brand, kits_dir=kits)
    assert len(paths) == 2
    assert all(p.parent.name == "refs" for p in paths)
    # Logo refs must not mix in when character refs exist.
    assert all("imported" not in str(p) for p in paths)


def test_character_refs_fallback_to_logo(kits: Path) -> None:
    brand = load_brand("magic-unicorn", kits_dir=kits)
    paths = resolve_character_references("magic-unicorn", brand, kits_dir=kits)
    assert paths  # logo refs exist from fixture
    assert all(p.parent.name == "logo" or "imported" in str(p) for p in paths)


def test_avatar_prompt_leads_with_mascot_style() -> None:
    brand = Brand.model_validate(
        {
            "brand": {"slug": "demo", "display_name": "Demo Co"},
            "visual": {
                "colors": {"primary": "#b66eff", "accent": "#00d4ff"},
                "style_keywords": ["dark UI", "neon glow", "glassy panels"],
                "mascot_style": (
                    "white cartoon unicorn bust, thick black outlines, serious "
                    "capable adult attitude, not cutesy"
                ),
            },
        }
    )
    prompt = agent_identity._avatar_prompt(brand, "Iris", "brand director")
    assert "EXACT same illustration style as the reference images" in prompt
    assert "white cartoon unicorn bust" in prompt
    assert "Iris" in prompt and "brand director" in prompt
    # Graphic chrome keywords must not dominate (or even appear) when mascot is set.
    assert "neon glow" not in prompt
    assert "dark UI" not in prompt


def test_avatar_prompt_fallback_without_mascot() -> None:
    brand = Brand.model_validate(
        {
            "brand": {"slug": "demo", "display_name": "Demo Co"},
            "visual": {
                "colors": {"primary": "#b66eff", "accent": "#00d4ff"},
                "style_keywords": ["dark UI", "neon glow"],
            },
        }
    )
    prompt = agent_identity._avatar_prompt(brand, "Scout", "researcher")
    assert "reference images" in prompt.lower()
    assert "not a logo emblem" in prompt or "character" in prompt.lower()
    assert "Scout" in prompt


def test_generate_avatar_passes_character_refs(
    monkeypatch: pytest.MonkeyPatch, kits: Path
) -> None:
    brand_dir = kits / "magic-unicorn"
    refs = brand_dir / "refs"
    refs.mkdir()
    (refs / "mascot.png").write_bytes(_png())
    agent_identity.create_agent(
        "magic-unicorn", "Iris", "brand director", kits_dir=kits
    )
    png = _png()
    captured: dict = {}

    class _Impl:
        def generate(self, req: GenRequest) -> GenResult:
            captured["req"] = req
            return GenResult(
                images=[png], mime="image/png", backend="litellm", model="gpt-image-1"
            )

    monkeypatch.setattr(
        agent_identity,
        "resolve_image_backend",
        lambda d, r, s: ("litellm", _Impl(), None),
    )
    ident = agent_identity.generate_avatar("magic-unicorn", "iris", kits_dir=kits)
    assert ident["avatar_file"] == "avatar.png"
    req: GenRequest = captured["req"]
    assert len(req.reference_images) == 1
    assert req.reference_images[0].name == "mascot.png"
    assert "EXACT same illustration style" in req.prompt or "reference" in req.prompt.lower()
