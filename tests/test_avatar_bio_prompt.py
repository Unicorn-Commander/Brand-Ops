"""The agent bio is its art direction; it must reach the image prompt.

Until 2026-08-14 _avatar_prompt took only (brand, name, role), so every word an
operator wrote into a bio to describe how an agent should LOOK was discarded
before generation. That is why bios carrying exact hex codes changed nothing.
"""
import inspect
import brand_ops.agent_identity as ai


def test_bio_reaches_the_avatar_prompt():
    src = inspect.getsource(ai.generate_avatar)
    assert "bio" in src, "generate_avatar must pass the bio into the prompt"
    sig = inspect.signature(ai._avatar_prompt)
    assert "bio" in sig.parameters


def test_variant_prompt_carries_the_bio_too():
    sig = inspect.signature(ai._variant_prompt)
    assert "bio" in sig.parameters
    src = inspect.getsource(ai.generate_variants)
    assert "bio = data.get" in src, "bio must be in scope in generate_variants"
    assert "_variant_prompt(brand, name, role, state, bio)" in src


def test_bio_text_appears_in_the_built_prompt(monkeypatch):
    class C: primary = "#7c3aed"; accent = "#22d3ee"
    class V:
        colors = C(); style_keywords = ["dark UI"]; mascot_style = ""
    class B_:
        display_name = "Magic Unicorn"
    class Brand_:
        visual = V(); brand = B_()
    p = ai._avatar_prompt(Brand_(), "Sidekick", "helper", "carries a canvas satchel")
    assert "carries a canvas satchel" in p
    # and an absent bio must not leave a dangling separator
    p2 = ai._avatar_prompt(Brand_(), "Sidekick", "helper", "")
    assert "  " not in p2
