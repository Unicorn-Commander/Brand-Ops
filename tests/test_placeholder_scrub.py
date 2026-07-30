"""Legacy template placeholders are scrubbed at the read boundary.

Brands created by the retired copy-the-template path can still carry the
template's literal junk ("REPLACE one line", "Replace with a few sentences
..."). The summary and detail projections scrub exactly those values to None
so an undressed brand renders as an honest empty card -- while real copy, even
copy that merely mentions similar words, passes through untouched. These tests
plant a throwaway junk kit directly in DEFAULT_KITS_DIR (the read path takes no
kits_dir) and remove it on teardown.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app
from brand_ops.brand_loader import DEFAULT_KITS_DIR

JUNK_SLUG = "zz-scrub-junk"
JUNK_YAML = """brand:
  slug: zz-scrub-junk
  display_name: Zz Scrub Junk
  tagline: REPLACE one line
  parent: null
  description: |
    Replace with a few sentences on what this brand is, who it serves, and the
    voice intent. This text is used as context in every prompt.
visual:
  colors:
    primary: "#000000"
    accent: "#000000"
"""

HONEST_SLUG = "zz-scrub-honest"
HONEST_YAML = """brand:
  slug: zz-scrub-honest
  display_name: Zz Scrub Honest
  tagline: One REPLACE is not the placeholder.
  parent: null
  description: |
    A real description. It even says Replace with a few sentences later in the
    text, which must not trigger the scrub because it does not START with it.
visual:
  colors:
    primary: "#112233"
    accent: "#445566"
"""


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    return TestClient(create_app())


def _plant(slug: str, text: str) -> None:
    kit = DEFAULT_KITS_DIR / slug
    kit.mkdir(parents=True, exist_ok=True)
    (kit / "brand.yaml").write_text(text)


@pytest.fixture
def junk_brand() -> Iterator[str]:
    _plant(JUNK_SLUG, JUNK_YAML)
    try:
        yield JUNK_SLUG
    finally:
        shutil.rmtree(DEFAULT_KITS_DIR / JUNK_SLUG, ignore_errors=True)


@pytest.fixture
def honest_brand() -> Iterator[str]:
    _plant(HONEST_SLUG, HONEST_YAML)
    try:
        yield HONEST_SLUG
    finally:
        shutil.rmtree(DEFAULT_KITS_DIR / HONEST_SLUG, ignore_errors=True)


def test_summary_scrubs_placeholder_tagline(client: TestClient, junk_brand: str) -> None:
    brands = client.get("/api/brands").json()["brands"]
    mine = next(b for b in brands if b["slug"] == junk_brand)
    assert mine["tagline"] is None


def test_detail_scrubs_placeholder_tagline_and_description(
    client: TestClient, junk_brand: str
) -> None:
    body = client.get(f"/api/brands/{junk_brand}").json()
    assert body["tagline"] is None
    assert body["description"] is None
    # Only the junk copy is scrubbed; the brand itself still serves normally.
    assert body["display_name"] == "Zz Scrub Junk"


def test_honest_copy_passes_through_unscathed(
    client: TestClient, honest_brand: str
) -> None:
    body = client.get(f"/api/brands/{honest_brand}").json()
    assert body["tagline"] == "One REPLACE is not the placeholder."
    assert body["description"].startswith("A real description.")

    brands = client.get("/api/brands").json()["brands"]
    mine = next(b for b in brands if b["slug"] == honest_brand)
    assert mine["tagline"] == "One REPLACE is not the placeholder."
